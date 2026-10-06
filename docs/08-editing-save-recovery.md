# 편집·저장·복구 프로토콜

[목차](README.md) · [스키마](07-sqlite-schema.md) · [인터페이스](10-interfaces-and-rust.md)

## 파일의 역할

| 파일 | 역할 | 수명 |
|---|---|---|
| 사용자 지정 `.diagproj` | 마지막 명시 저장 상태 | 사용자가 보관·전달 |
| 작업 폴더 `working.sqlite` 및 WAL/SHM | 현재 편집 상태 | 정상 닫기 시 정리, 중단 시 복구 |
| 작업 폴더 manifest.json | 원본 경로·파일 해시·세션·저장 시각·종료 상태 | 프로젝트 바깥의 세션 메타데이터 |
| 목적지와 같은 디렉터리의 임시 DB | 저장 중 생성한 일관된 사본 | 검증 후 최종 파일로 교체 |
| 목적지 `.bak` | 직전 정상 명시 저장본 | 최근 1개 유지 |

작업 폴더는 LOCALAPPDATA 하위 앱 전용 디렉터리다. 원본 파일을 직접 WAL 모드로 편집하지 않는다. 프로젝트 UUID와 원본 파일 식별을 구분하여 같은 UUID의 '다른 이름 저장' 사본도 다른 파일로 취급한다.

## 열기와 동시 사용

1. 파일 존재·SQLite header·application_id·schema version을 읽기 전용으로 검사한다.
2. 정규화 경로와 Windows 파일 identity를 기준으로 앱 간 잠금을 획득한다. 별칭 경로가 같은 파일을 가리키는지도 확인한다.
3. 다른 작성 세션이 있으면 두 번째 창은 마지막 저장본의 읽기 전용 사본으로 연다. 미저장 작업의 실시간 공유는 하지 않는다.
4. 읽기 전용 원본 연결에서 backup API로 로컬 작업 DB를 만든다.
5. 필요 migration과 foreign_key_check·기본 객체 검사를 수행한다.
6. 일반 재열기에서는 undo_stack을 비우고 현재 상태를 새 saved checkpoint로 설정한다. change_log는 유지한다.
7. 작업 DB 연결을 WAL/FULL 모드로 열고 화면에 첫 페이지를 로드한다.

잘못된 SQLite 스키마나 임의 SQL 객체가 포함된 파일을 그대로 신뢰하지 않는다. 앱이 기대하는 테이블·컬럼·인덱스와 비교하고 확장 로딩은 허용하지 않는다. local 작업본 생성 전 원본은 절대 수정하지 않는다.

## 명령과 트랜잭션

편집 명령은 입력, 영향 객체, 정방향 변경, 역방향 변경을 가진다. 필수 구조를 유지하는 최소 명령 단위로 실행한다. CDD/CSV 분석은 트랜잭션 밖에서 수행하고 적용만 안에서 수행한다.

DB 쓰기 큐는 expected_revision을 검사하고 BEGIN IMMEDIATE를 연다. 도메인 사전 검사 → SQL 적용 → 도메인 후조건 → undo 프레임·change_log·history_state·revision 갱신 → COMMIT을 수행한다. 성공 signal은 COMMIT 이후에만 보낸다. 오류·커밋 전 취소는 ROLLBACK한다.

Undo는 저장 시점을 되돌리는 SQLite ROLLBACK이 아니다. 역명령을 새 트랜잭션으로 적용하며 revision은 증가한다. Redo도 새 revision을 만든다. 새 편집을 하면 현재 cursor 뒤의 redo 프레임을 제거하고 새 state token을 만든다. 삭제 Undo는 원래 UUID·순서·공유 연결을 복원한다.

## dirty와 체크포인트

dirty = current_token != saved_token이다. 새 프로젝트는 saved_token=NULL이다. revision은 단조 증가하여 비동기 결과에 사용하고 dirty 판정에는 사용하지 않는다. Undo/Redo가 저장 당시 token에 도달하면 dirty가 해제된다.

저장 후 Undo한 다음 다른 분기로 편집하면 저장 token은 남아 있어도 해당 이력 경로로 자동 복귀하지 않는다. 새 저장 시 현재 token을 saved_token으로 갱신한다. 복구 세션은 기존 undo cursor와 token을 검증 후 유지한다.

## Ctrl+S / 다른 이름 저장

1. 진행 중인 편집 명령을 마치고 쓰기 큐에 저장 barrier를 둔다. 탐색은 허용하되 새 변경은 대기시킨다.
2. revision R과 current_token T를 고정한다. 목적지가 외부에서 변경되었는지 이전 저장 해시/파일 identity와 대조한다.
3. 기존 목적지의 일관된 backup을 먼저 확보한다. 다른 이름 저장에서 파일이 이미 있으면 덮어쓰기 선택을 받는다.
4. backup API로 같은 디렉터리의 임시 DB를 만든다. 작업용 writer connection의 열린 트랜잭션 안에서 자기 자신을 backup하지 않는다.
5. 임시 DB의 saved_token을 T로 설정하고 journal_mode=DELETE로 만든 뒤 integrity_check·foreign_key_check·기본 도메인 검사를 수행한다.
6. 연결을 닫고 파일을 flush한 뒤 같은 볼륨의 원자적 교체 연산으로 목적지를 바꾼다. 이 짧은 교체 단계에 진입하면 취소하지 않는다.
7. 작업 DB의 saved_token=T와 manifest 저장 정보를 갱신한다. 이 메타데이터 갱신은 내용 revision을 증가시키지 않는다.
8. barrier를 해제하고 저장 결과를 표시한다. 대기 변경이 이후 적용되면 다시 dirty가 된다.

2~5단계 실패/취소는 목적지를 변경하지 않고 임시 파일만 정리한다. 6단계 성공 후 7단계 이전 중단은 다음 실행에서 목적지의 project UUID·revision·token과 manifest를 대조하여 저장 성공 상태로 복구한다. 모든 오류에서 마지막 정상본 또는 `.bak`을 확인할 수 있어야 한다.

파일 교체의 원자성과 전원 장애의 모든 하드웨어 동작을 동일하게 가정하지 않는다. 파일/DB flush와 backup을 사용하고 강제 종료·디스크 오류 주입으로 검증한다. [SQLite Backup API](https://www.sqlite.org/backup.html)

## 닫기·중단·복구

dirty일 때 닫기는 저장/저장하지 않음/취소다. 저장 실패는 닫기를 취소한다. 저장하지 않음은 원본을 유지하고 폐기 마커를 원자적으로 기록한 뒤 작업 DB를 닫고 세션 디렉터리를 정리한다. 정리 실패로 잔여 파일이 있어도 폐기 마커가 있으면 복구 후보에 다시 표시하지 않는다.

작업 DB에는 명령마다 커밋하므로 별도 전체 파일 자동 저장 타이머가 필요 없다. manifest 시각은 커밋 후 갱신하되 crash 복구는 manifest만 믿지 않고 DB revision도 읽는다. UI에 아직 확정하지 않은 셀 내용은 복구 보장 대상이 아니다.

복구는 원래 working.sqlite와 WAL을 유지한 채 SQLite로 읽어 무결성을 확인한다. SHM/WAL을 임의 삭제하지 않는다. 검증된 backup으로 새 세션을 만든 후 원본 세션은 보존한다. 원본 프로젝트가 외부에서 바뀌었거나 없어졌으면 다른 이름 저장만 허용한다. 손상본은 격리하고 자동 덮어쓰지 않는다.

복구 후보는 사용자에게 종료/마지막 작업/저장 시각과 파일명을 보여준다. 미해결 복구 후보는 자동 만료시키지 않는다. 정상·폐기 세션만 자동 정리한다.

## 관련 검증

TC-003/004/005는 저장·폐기·복구, TC-019는 Undo, TC-027은 migration, TC-029는 revision·취소, TC-037은 live WAL backup, TC-038은 저장 교체 후 중단과 dirty 체크포인트를 검증한다.
