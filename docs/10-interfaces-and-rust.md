# 내부 인터페이스와 Rust 확장

[목차](README.md) · [아키텍처](05-system-architecture.md) · [저장](08-editing-save-recovery.md)

## 공통 DTO

인터페이스는 프로세스 내부 Python 서비스 계약이다. HTTP 서버·REST API를 만들지 않는다. Qt 객체·sqlite3.Connection·lxml Element를 계층 경계의 DTO로 전달하지 않는다.

| 타입 | 필드 |
|---|---|
| ProjectRef | project_id: UUID, session_id: UUID |
| SnapshotRef | project_id, revision: int, schema_version: int, profile_version: str, local_path: str |
| Command | command_id: UUID, type: enum, payload: typed DTO |
| ChangeSet | revision, state_token, affected_ids, invalidated_scopes, dirty |
| ValidationIssue | rule_id, severity, blocks_export, object_id?, field?, source_path?, message, remedy |
| JobResult | job_id, status: succeeded/partial/cancelled/failed, input_revision?, output?, issues |
| Error | code, message, retryable, object_id?, field?, details: safe metadata |
| Page | rows: immutable DTO list, next_cursor?, total_count?, revision |

UUID·revision·ID는 정규화 값이다. 수치 리터럴은 문자열+명시 타입으로 전달한다. 선택적 필드는 없음을 null로 표현하며 0·빈 문자열과 혼동하지 않는다.

## 명령 계약

### execute_command(command, expected_revision) → ChangeSet

CreateVariant, DetachDidForVariant, UpdateDid, UpdateDataType, UpdateService, SetStateConditions, InsertDtcs, BulkEdit, DeleteObjects, Undo, Redo를 command.type으로 정의한다. 각 payload는 대상 ID와 수정 필드를 명시하며 임의 SQL/임의 Python 함수를 받지 않는다.

expected_revision이 다르면 REVISION_CONFLICT를 반환하고 자동 재실행하지 않는다. 같은 command_id가 성공 로그에 있으면 이미 실행된 결과를 반환하여 UI 재시도로 중복 적용하지 않는다. command_id는 논리적 시도마다 새로 발급한다.

검증 실패·읽기 전용·참조 사용 중·범위 중복은 의미 있는 오류로 반환한다. 트랜잭션의 부분 성공은 없다. 정방향·역방향 데이터는 같은 코드 경로의 제약조건을 통과해야 한다.

### query_objects(scope, filter, sort, page) → Page

scope는 object kind와 variant_id를 포함한다. 필터는 number exact/range, name contains, qualifier contains, supported 상태다. sort는 허용 column key와 asc/desc이며 마지막 동률 정렬은 ObjectId다. 기본 200행, 최대 1,000행이다.

page cursor는 revision을 포함한다. 문서 revision이 바뀌면 CURSOR_STALE을 반환하고 첫 페이지부터 조회한다. 미지의 정렬 열이나 SQL 조각은 거절한다. total_count는 별도 비동기 집계할 수 있다.

## 파일·검증·미리보기 계약

| 인터페이스 | 입력 | 출력·취소·실패 |
|---|---|---|
| import_cdd(path) | 로컬 입력 경로 | ImportPreview(job_id, source hash, DTO staging ref, coverage, issues); 취소 시 작업 프로젝트 없음 |
| apply_import(preview_id, expected_revision) | 검사된 임시 결과 | 새 프로젝트의 ChangeSet; 원본 변경/해시 차이는 재분석 |
| validate_project(snapshot, scope) | 고정 revision, 전체/선택 범위 | ValidationReport, checks_version, coverage; stale 결과는 현재 확정 결과로 쓰지 않음 |
| build_telegram_preview(service_id, values) | 서비스, typed raw/physical 값 | offsets, lengths, bytes/hex, converted values, issues, revision; ECU 통신 없음 |
| export_odx(snapshot, variants, destination) | 변형 IDs, 언어, 로컬 출력 경로 | ExportReport(files, hashes, schema/profile/rules version, input revision, checks); 실패 시 최종 파일 유지 |
| save_project(destination) | 목적지, 저장/다른 이름 모드 | SaveReceipt(project_id, revision, state_token, file_hash, path); 성공 후에만 clean |
| recover_project(recovery_id) | 스캔 결과의 세션 ID | 새 ProjectRef와 복구 보고; 임의 경로 입력으로 세션 밖 파일을 처리하지 않음 |

import_cdd의 파싱과 실제 적용은 두 단계다. 미리보기 결과를 UI 조작만으로 바꾸지 않고 적용 DTO와 해시로 고정한다. 가져오기는 새 프로젝트의 초기 revision에만 적용하며 기존 문서 병합은 P1이다.

### 이벤트

Progress(job_id, phase, completed, total?), JobFinished(job_id, result), DocumentChanged(ChangeSet), SaveCompleted(receipt)를 Qt queued signal로 전달한다. 작업자 프로세스는 DTO를 큐로 보내고 GUI가 Qt 객체 변경을 수행한다. UI가 닫힌 작업의 신호는 세션 ID로 무시한다.

## 오류 코드

| 코드 | 의미·재시도 |
|---|---|
| VALIDATION_FAILED / DUPLICATE_IDENTIFIER / REFERENCE_IN_USE | 입력 수정 후 새 명령 |
| REVISION_CONFLICT / CURSOR_STALE | 현재 상태 재조회 |
| READ_ONLY / UNSUPPORTED_FEATURE / UNSUPPORTED_VERSION | 지원 범위 또는 모드 변경 필요 |
| INVALID_XML / INVALID_PROJECT / BROKEN_REFERENCE | 입력 검사·보고서 확인 |
| VALIDATION_UNAVAILABLE / EXPORT_BLOCKED | XSD/규칙 확보 또는 차단 사유 해소 |
| IO_ERROR / DISK_FULL / FILE_CHANGED / BUSY | 경로·공간·충돌 해결 후 재시도 |
| CANCELLED | 오류 알림창 없이 취소 상태 |
| INTERNAL_ERROR | job_id를 표시하고 진단 로그 참조 |

## Rust 도입 기준과 계약

MVP에서는 Rust 설치·빌드를 필수로 만들지 않는다. NFR-004 미달을 재현하고 CPU 프로파일에서 해당 알고리즘이 주요 비용임을 확인한 후 최적화 후보로 지정한다. UI의 과도한 리렌더링이나 DB 인덱스 문제는 해당 계층에서 먼저 수정한다.

후보는 encode/decode_fields, convert_values, validate_reference_graph, compare_snapshots다. 모두 불변 입력→결과의 배치 함수로 설계한다. 도메인 상태·DB 연결·Qt 객체를 넘기지 않는다. 전체 프로젝트 JSON의 반복 직렬화나 셀별 네이티브 호출을 사용하지 않는다.

PyO3 확장 모듈은 maturin wheel로 빌드한다. 긴 Rust 작업은 작업 스레드에서 실행하고 Python interpreter lock을 해제한다. 취소는 atomic flag 등 네이티브 토큰을 주기적으로 검사한다. Python 숫자와 Rust 정수의 범위·byte order·오류 종류를 명시적으로 맞춘다.

채택 조건: 동일 corpus의 Python/Rust 결과 동등성, 호출/복사 비용 포함 성능 개선, 증가한 패키지 크기·메모리 보고, Rust 없는 개발 fallback과 배포 wheel 로드 검증. 미입증이면 Python 구현을 유지한다.

참고: [maturin](https://www.maturin.rs/tutorial), [PyO3 parallelism](https://pyo3.rs/main/parallelism).
