"""스레드 소유 SQLite 저장소, 행 변경 이력, 명시적 저장과 복구."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import uuid

from . import __version__
from .domain import DomainError, Snapshot
from . import migrations

# 새 프로젝트의 스키마 정의. 테스트는 이전 버전 파일을 만들기 위해 이 경로를 바꿀 수 있다.
SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def uid():
    return str(uuid.uuid4())


def now():
    return datetime.now(timezone.utc).isoformat()


def app_home():
    p = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DiagAuthor"
    p.mkdir(parents=True, exist_ok=True)
    return p


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def json_write(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


class FileLock:
    def __init__(self, identity):
        folder = app_home() / "locks"
        folder.mkdir(exist_ok=True)
        name = hashlib.sha256(identity.encode()).hexdigest() + ".lock"
        self.file = (folder / name).open("a+b")
        self.acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0, 2)
                if self.file.tell() == 0:
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
        except OSError:
            self.file.close()

    def close(self):
        if self.acquired:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.file.seek(0)
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            finally:
                self.file.close()
                self.acquired = False


def connect(path, readonly=False):
    if readonly:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)
    else:
        db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=5000")
    return db


def _recovery_probe(folder):
    """복구 후보의 무결성과 스키마 버전을 읽는다. 세션 잠금은 이미 확인된 상태다.
    읽기 전용으로 연다. 쓰기 연결을 닫으면 WAL 체크포인트로 복구 대상 파일이 바뀌기 때문이다."""
    path = folder / "working.sqlite"
    if not path.is_file():
        return "확인 불가", None
    try:
        db = connect(path, True)
    except (OSError, sqlite3.OperationalError):
        return "확인 불가", None
    except sqlite3.Error:
        return "손상", None
    try:
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.DatabaseError:
            return "손상", None
        try:
            integrity = "정상" if db.execute("PRAGMA quick_check").fetchone()[0] == "ok" else "손상"
        except sqlite3.DatabaseError:
            integrity = "손상"
        return integrity, version
    finally:
        db.close()


class Repository:
    OMIT = {"change_log", "undo_stack", "history_state", "command_receipts", "schema_migrations", "project_meta"}

    def __init__(self, db):
        self.db = db
        self.tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        self.columns = {t: [dict(r) for r in db.execute(f'PRAGMA table_info("{t}")')] for t in self.tables}
        self.pks = {t: [r["name"] for r in sorted(cols, key=lambda r: r["pk"]) if r["pk"]] for t, cols in self.columns.items()}
        self._recording = False
        self.db.create_function("record_changes", 0, lambda: int(self._recording))
        self.db.execute("CREATE TEMP TABLE row_changes(seq INTEGER PRIMARY KEY, table_name TEXT, old_row TEXT, new_row TEXT)")
        for table in self.tables:
            if table in self.OMIT: continue
            def json_expr(alias):
                parts = []
                for col in self.columns[table]:
                    name = col["name"]
                    expr = f'{alias}."{name}"'
                    if col["type"] == "BLOB":
                        expr = f"CASE WHEN {expr} IS NULL THEN NULL ELSE json_object('$blob',hex({expr})) END"
                    parts.extend([f"'{name}'", expr])
                return "json_object(" + ",".join(parts) + ")"
            for action in ("INSERT", "UPDATE", "DELETE"):
                old = "NULL" if action == "INSERT" else json_expr("OLD")
                new = "NULL" if action == "DELETE" else json_expr("NEW")
                self.db.execute(f"CREATE TEMP TRIGGER record_{table}_{action} AFTER {action} ON main.\"{table}\" WHEN record_changes()=1 BEGIN INSERT INTO row_changes(table_name,old_row,new_row) VALUES('{table}',{old},{new}); END")

    def all(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    def one(self, sql, args=()):
        r = self.db.execute(sql, args).fetchone()
        return dict(r) if r else None

    def insert(self, table, **values):
        if table not in self.tables: raise DomainError("잘못된 테이블")
        keys = list(values)
        self.db.execute(f'INSERT INTO "{table}" ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})', [values[k] for k in keys])

    def update(self, table, where, values):
        if table not in self.tables: raise DomainError("잘못된 테이블")
        if not values: return
        self.db.execute(f'UPDATE "{table}" SET {",".join(k+"=?" for k in values)} WHERE {" AND ".join(k+"=?" for k in where)}', [*values.values(), *where.values()])

    def delete(self, table, **where):
        if table not in self.tables or not where: raise DomainError("잘못된 삭제")
        self.db.execute(f'DELETE FROM "{table}" WHERE {" AND ".join(k+"=?" for k in where)}', list(where.values()))

    @property
    def revision(self):
        return self.db.execute("SELECT revision FROM project_meta").fetchone()[0]

    def history(self):
        return self.one("SELECT * FROM history_state")

    def snapshot(self, locale=None, include_sources=False):
        data = {}
        for table in self.tables:
            if table in self.OMIT or (table == "source_files" and not include_sources): continue
            data[table] = self.all(f'SELECT * FROM "{table}"')
        meta = self.one("SELECT * FROM project_meta")
        data["project_meta"] = [meta]
        return Snapshot(data, meta["revision"], locale or meta["default_locale"])

    def receipt(self, command_id):
        if "command_receipts" not in self.tables:
            return None
        return self.one("SELECT * FROM command_receipts WHERE command_id=?", (command_id,))

    def record_receipt(self, command_id, request_sha256, action, selected_id=None):
        if command_id is not None and "command_receipts" in self.tables:
            self.insert("command_receipts", command_id=command_id, request_sha256=request_sha256,
                        action=action, applied_revision=self.revision, selected_id=selected_id, completed_at=now())

    @contextmanager
    def command(self, name, expected_revision=None, command_id=None, request_sha256=None, outcome=None):
        if expected_revision is not None and expected_revision != self.revision:
            raise DomainError("문서가 변경되었습니다. 다시 조회하세요.", "REVISION_CONFLICT")
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("PRAGMA defer_foreign_keys=ON")
        self.db.execute("DELETE FROM row_changes")
        self._recording = True
        try:
            yield
            self._recording = False
            if self.db.execute("PRAGMA foreign_key_check").fetchone():
                raise DomainError("필수 참조가 해결되지 않았습니다.", "BROKEN_REFERENCE")
            deltas = self.all("SELECT table_name,old_row,new_row FROM row_changes ORDER BY seq")
            if deltas:
                state = self.history()
                audit_id = command_id or uid()
                token = uid()
                self.db.execute("DELETE FROM undo_stack WHERE position>?", (state["cursor_position"],))
                forward = json.dumps(deltas, ensure_ascii=False)
                inverse = json.dumps([{**d, "old_row": d["new_row"], "new_row": d["old_row"]} for d in reversed(deltas)], ensure_ascii=False)
                self.insert("undo_stack", position=state["cursor_position"]+1, command_id=audit_id, forward_json=forward, inverse_json=inverse, before_token=state["current_token"], after_token=token)
                self.update("history_state", {"singleton": 1}, {"cursor_position": state["cursor_position"]+1, "current_token": token})
                self._audit(audit_id, "execute", forward, name)
            self.record_receipt(command_id, request_sha256, name, (outcome or {}).get("selected_id"))
            self.db.execute("COMMIT")
        except Exception:
            self._recording = False
            self.db.execute("ROLLBACK")
            raise

    def _audit(self, command_id, action, change_json, label):
        rev = self.revision + 1
        self.insert("change_log", revision=rev, event_id=uid(), command_id=command_id, action=action, affected_ids_json="[]", before_json="{}", after_json=json.dumps({"label": label, "changes": json.loads(change_json)}, ensure_ascii=False), occurred_at=now())
        self.update("project_meta", {"singleton": 1}, {"revision": rev, "updated_at": now()})

    def replay(self, redo=False, command_id=None, request_sha256=None):
        state = self.history()
        pos = state["cursor_position"] + (1 if redo else 0)
        frame = self.one("SELECT * FROM undo_stack WHERE position=?", (pos,))
        delta = frame["forward_json" if redo else "inverse_json"] if frame else None
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("PRAGMA defer_foreign_keys=ON")
        try:
            for d in json.loads(delta) if delta else ():
                table = d["table_name"]
                def decode(s):
                    return {k: bytes.fromhex(v["$blob"]) if isinstance(v, dict) and "$blob" in v else v for k, v in json.loads(s).items()} if s else None
                old, new = decode(d["old_row"]), decode(d["new_row"])
                row = new or old
                where = {k: row[k] for k in self.pks[table]}
                if new is None:
                    self.delete(table, **where)
                else:
                    columns = list(new)
                    updates = [k for k in columns if k not in self.pks[table]]
                    clause = "DO UPDATE SET " + ",".join(f'"{k}"=excluded."{k}"' for k in updates) if updates else "DO NOTHING"
                    self.db.execute(f'INSERT INTO "{table}" ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)}) ON CONFLICT ({",".join(self.pks[table])}) {clause}', list(new.values()))
            if frame:
                self.update("history_state", {"singleton": 1}, {"cursor_position": pos if redo else pos-1, "current_token": frame["after_token" if redo else "before_token"]})
                self._audit(frame["command_id"], "redo" if redo else "undo", delta, "다시 실행" if redo else "실행 취소")
            self.record_receipt(command_id, request_sha256, "redo" if redo else "undo")
            if self.db.execute("PRAGMA foreign_key_check").fetchone(): raise DomainError("이력 참조 복원에 실패했습니다.")
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise


class Session:
    def __init__(self, recovery=None):
        self.session_id = str(uuid.UUID(recovery)) if recovery else uid()
        self.folder = app_home() / "workspaces" / self.session_id
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock("session:" + self.session_id)
        if not self.lock.acquired: raise DomainError("이미 사용 중인 복구 작업입니다.", "BUSY")
        self.source_locks = []
        self.path = self.folder / "working.sqlite"
        self.manifest_path = self.folder / "manifest.json"
        self.meta = {"session_id": self.session_id, "source": None, "source_hash": None, "status": "active", "updated_at": now()}
        self.readonly = False
        self.db = None
        self.repo = None

    def lock_source(self, path):
        identities = ["path:" + os.path.normcase(str(path.resolve()))]
        if path.exists():
            st = path.stat()
            identities.append(f"inode:{st.st_dev}:{st.st_ino}")
        acquired = []
        for identity in identities:
            if any(i == identity for i, _ in self.source_locks): continue
            lock = FileLock(identity)
            if not lock.acquired:
                for _, l in acquired: l.close()
                return False
            acquired.append((identity, lock))
        self.source_locks.extend(acquired)
        return True

    def initialize(self, source=None, recover=False):
        try:
            if recover:
                self.meta = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                if self.meta.get("status") != "active": raise DomainError("종료된 작업본입니다.")
                if self.meta.get("source"):
                    self.readonly = not self.lock_source(Path(self.meta["source"]))
            elif source:
                source = Path(source).resolve()
                self.readonly = not self.lock_source(source)
                src = connect(source, True)
                try:
                    self.check_file(src, allow_older=True)
                    dest = connect(self.path)
                    try: src.backup(dest)
                    finally: dest.close()
                finally: src.close()
                self.meta.update(source=str(source), source_hash=digest(source))
            self.db = connect(self.path)
            if not source and not recover:
                self.db.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
            else:
                self.check_file(self.db, allow_older=True)
                # 이전 스키마 파일은 작업 사본에서 올린다. 원본은 저장 전까지 그대로 두며 저장 시 .bak이 남는다.
                self.meta["migrated_to"] = migrations.migrate(self.db)
                self.check_file(self.db)
                if not recover:
                    self.db.execute("DELETE FROM undo_stack")
                    self.db.execute("UPDATE history_state SET cursor_position=0,saved_token=current_token")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.repo = Repository(self.db)
            self.touch()
        except Exception:
            self.close(discard=not recover)
            raise

    @staticmethod
    def check_file(db, allow_older=False):
        try:
            if db.execute("PRAGMA application_id").fetchone()[0] != 0x44494147:
                raise DomainError("DiagAuthor 프로젝트 파일이 아닙니다.", "INVALID_PROJECT")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            current = migrations.CURRENT_VERSION
            if version > current or version < 1 or (version < current and not allow_older):
                raise DomainError(f"지원하지 않는 스키마 버전 {version}. 이 빌드는 버전 1~{current}을 열 수 있습니다.", "UNSUPPORTED_VERSION")
            meta = db.execute("SELECT schema_version FROM project_meta WHERE singleton=1").fetchone()
            if not meta or meta[0] != version: raise DomainError("프로젝트 스키마 정보가 일치하지 않습니다.")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
                raise DomainError("프로젝트 파일의 무결성이 올바르지 않습니다.", "INVALID_PROJECT")
        except sqlite3.DatabaseError as e:
            # sqlite3.OperationalError는 DatabaseError의 하위형이라 비SQLite 파일·필수 표 누락도 여기로 온다.
            # 위에서 올린 DomainError는 DatabaseError가 아니므로 그대로 통과한다.
            raise DomainError("DiagAuthor 프로젝트 파일이 아닙니다.", "INVALID_PROJECT") from e

    def touch(self):
        self.meta["updated_at"] = now()
        json_write(self.manifest_path, self.meta)

    def save(self, destination, cancel=lambda: None):
        if self.readonly: raise DomainError("읽기 전용 프로젝트입니다.", "READ_ONLY")
        target = Path(destination).resolve()
        if target.suffix.lower() != ".diagproj": target = target.with_suffix(".diagproj")
        if target == self.path.resolve() or self.folder.resolve() in target.parents:
            raise DomainError("작업 폴더 밖에 프로젝트를 저장하세요.")
        if not self.lock_source(target): raise DomainError("다른 창에서 사용 중인 파일입니다.", "BUSY")
        same = self.meta.get("source") and os.path.normcase(str(target)) == os.path.normcase(self.meta["source"])
        if same and target.exists() and self.meta.get("source_hash") != digest(target):
            raise DomainError("원본 파일이 외부에서 변경되었습니다. 다른 이름으로 저장하세요.", "FILE_CHANGED")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{uid()}.tmp")
        output = None
        try:
            cancel()
            output = connect(temp)
            self.db.backup(output, pages=256, progress=lambda *_: cancel())
            output.execute("PRAGMA journal_mode=DELETE")
            output.execute("UPDATE history_state SET saved_token=current_token")
            output.execute("UPDATE project_meta SET saved_app_version=?", (__version__,))
            self.check_file(output)
            output.close()
            output = None
            # Windows의 fsync(_commit)는 쓰기 권한 핸들이 필요하다. "rb"로 열면 EBADF(OSError).
            with temp.open("r+b") as f: os.fsync(f.fileno())
            cancel()
            if target.exists():
                backup_temp = target.with_name(target.name + ".bak.tmp")
                old = connect(target, True)
                back = connect(backup_temp)
                try:
                    old.backup(back)
                    back.execute("PRAGMA journal_mode=DELETE")
                finally:
                    old.close()
                    back.close()
                os.replace(backup_temp, target.with_name(target.name + ".bak"))
            os.replace(temp, target)
            self.lock_source(target)
            self.db.execute("UPDATE history_state SET saved_token=current_token")
            self.meta.update(source=str(target), source_hash=digest(target))
            self.meta["saved_at"] = now()
            self.touch()
            return str(target)
        finally:
            if output: output.close()
            if temp.exists(): temp.unlink()

    def close(self, discard=False):
        try:
            if discard:
                self.meta["status"] = "discarded"
                self.touch()
            if self.db:
                self.db.close()
                self.db = None
        finally:
            for _, lock in self.source_locks: lock.close()
            self.source_locks.clear()
            self.lock.close()
        if discard and self.folder.parent == app_home() / "workspaces":
            shutil.rmtree(self.folder, ignore_errors=True)

    @staticmethod
    def quarantine_recovery(session_id):
        """복구 후보를 목록에서 빼서 격리 폴더로 옮긴다. 파일은 지우지 않는다(사용자가 직접 확인·삭제)."""
        try: session_id = str(uuid.UUID(str(session_id)))
        except ValueError: raise DomainError("복구 작업 ID가 올바르지 않습니다.", "INVALID_RECOVERY")
        folder = app_home() / "workspaces" / session_id
        if not (folder / "manifest.json").is_file(): raise DomainError("복구 작업본이 없습니다.", "INVALID_RECOVERY")
        lock = FileLock("session:" + session_id)
        if not lock.acquired: raise DomainError("다른 창에서 사용 중인 작업본은 격리할 수 없습니다.", "BUSY")
        try:
            target_root = app_home() / "quarantine"
            target_root.mkdir(parents=True, exist_ok=True)
            target = target_root / f"{session_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            folder.rename(target)
        finally:
            lock.close()
        return {"session_id": session_id, "path": str(target)}

    @staticmethod
    def recoveries():
        root = app_home() / "workspaces"
        result = []
        if not root.exists(): return result
        for manifest in root.glob("*/manifest.json"):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if data.get("status") != "active" or not (manifest.parent / "working.sqlite").exists(): continue
                lock = FileLock("session:" + manifest.parent.name)
                if lock.acquired:
                    lock.close()
                    integrity, version = _recovery_probe(manifest.parent)
                    data["saved_at"] = data.get("saved_at")
                    data["schema_version"] = version
                    data["integrity"] = integrity
                    source = data.get("source")
                    if not source:
                        data["source_changed"] = None
                    else:
                        try: data["source_changed"] = digest(Path(source)) != data.get("source_hash")
                        except OSError: data["source_changed"] = None
                    result.append(data)
            except (OSError, ValueError): continue
        return sorted(result, key=lambda x: x.get("updated_at", ""), reverse=True)
