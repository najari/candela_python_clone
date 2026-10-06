# 요구사항 추적·설계 결정·위험

[목차](README.md) · [요구사항](02-product-requirements.md) · [테스트](11-validation-and-testing.md) · [작업](12-development-roadmap.md)

## 기능 추적표

설계 열은 문서 번호다. UI 범위 밖 시스템 요구사항은 이를 명시한다. SRC는 [원본 근거](01-reference-analysis.md)에 정의한다. 신규 설계는 원본 제품의 사실로 인용하지 않는다.

| 요구사항 | 근거 | 시나리오·화면 | 설계 | 테스트 | 작업 |
|---|---|---|---|---|---|
| FR-001 | SRC-03 | UC-01 / UI-02 | 06,07,08 | TC-001 | TASK-04, TASK-08 |
| FR-002 | 신규 저장 설계 | UC-01 / UI-02 | 07,08 | TC-002 | TASK-07 |
| FR-003 | 신규 SQLite 결정 | UC-11 / UI-01 | 07,08 | TC-003, TC-037 | TASK-07 |
| FR-004 | SRC-18·신규 복구 | UC-12 / UI-12 | 08,13 | TC-005, TC-038 | TASK-07 |
| FR-005 | SRC-02 | UC-01 / UI-03 | 06,07 | TC-006 | TASK-08 |
| FR-006 | SRC-05 | UC-07 / UI-04 | 06,07 | TC-007 | TASK-06, TASK-08 |
| FR-007 | SRC-07 | UC-07 / UI-04, UI-05 | 06,08 | TC-008 | TASK-06, TASK-09 |
| FR-008 | SRC-06, SRC-19 | UC-03 / UI-05 | 06,07 | TC-010 | TASK-09 |
| FR-009 | SRC-08 | UC-04 / UI-06 | 06,07 | TC-011, TC-012 | TASK-05, TASK-09 |
| FR-010 | SRC-09 | UC-03 / UI-07 | 06 | TC-013 | TASK-05, TASK-09 |
| FR-011 | SRC-02 | UC-03, UC-05 / UI-07 | 06,09 | TC-014 | TASK-10 |
| FR-012 | SRC-11 | UC-05 / UI-07 | 06,07 | TC-015 | TASK-11 |
| FR-013 | SRC-10, SRC-12 | UC-06 / UI-08 | 06,07 | TC-016 | TASK-12 |
| FR-014 | SRC-12·신규 CSV 계약 | UC-06 / UI-09 | 09 | TC-017, TC-018 | TASK-12 |
| FR-015 | 신규 명령 결정 | UC-07, UC-11 / UI-01 | 08,10 | TC-019, TC-038 | TASK-06 |
| FR-016 | SRC-03 | UC-09 / UI-01, UI-05 | 04,05,10 | TC-020 | TASK-08 |
| FR-017 | SRC-10 | UC-09 / UI-10 | 05,11 | TC-021, TC-029 | TASK-14 |
| FR-018 | SRC-02, SRC-14 | UC-02 / UI-09 | 09,10 | TC-022 | TASK-02, TASK-13 |
| FR-019 | SRC-13 | UC-10 / UI-11 | 09,10 | TC-024, TC-025, TC-034 | TASK-15 |
| FR-020 | SRC-14, SRC-15 | UC-02, UC-10 / UI-09, UI-11 | 07,09 | TC-023 | TASK-13, TASK-15 |
| FR-021 | SRC-06, SRC-10 | UC-08 / UI-01 | 06,08 | TC-009 | TASK-06 |
| FR-022 | SRC-01 | UC-02 / UI-01 | 06,07 | TC-026 | TASK-08, TASK-13 |
| FR-023 | 신규 자체 형식 | UC-01 / UI-02 | 07,08,13 | TC-027 | TASK-04, TASK-17 |
| FR-024 | SRC-12 | UC-06 / UI-08 | 08,10 | TC-028 | TASK-06, TASK-12 |

## 품질 추적표

| 요구사항 | 설계·관련 흐름 | 테스트 | 작업 |
|---|---|---|---|
| NFR-001 | 07,08 / UC-11, UC-12 | TC-003, TC-004, TC-005, TC-030, TC-037, TC-038 | TASK-04, TASK-07, TASK-16 |
| NFR-002 | 06,07 / UC-04 | TC-011, TC-012, TC-013 | TASK-05, TASK-09 |
| NFR-003 | 04,05,10 / UC-09 | TC-029 | TASK-08, TASK-14 |
| NFR-004 | 05,07,11 / UC-09, UC-11 | TC-032 | TASK-16 |
| NFR-005 | 09,13 / UC-02 | TC-033 | TASK-13, TASK-17 |
| NFR-006 | 13 / 배포 | TC-031 | TASK-03, TASK-17 |
| NFR-007 | 01,09,10 / UC-02, UC-10 | TC-022, TC-024, TC-025, TC-034 | TASK-02, TASK-15 |
| NFR-008 | 13 / 운영 | TC-035 | TASK-17 |

## 설계 결정 기록

| ID | 결정 | 대안과 채택 이유 | 변경 조건 |
|---|---|---|---|
| ADR-01 | Python+PySide6 Qt Widgets | WPF 대신 Python 생태계와 표·트리 중심 개발 속도 | 플랫폼·팀 역량·UI 요건 변화 |
| ADR-02 | 프로젝트별 SQLite | ZIP+JSON보다 참조·부분 갱신·트랜잭션·쿼리 관리에 적합 | 서버 공동 편집 요구가 생기면 별도 제품 설계 |
| ADR-03 | 로컬 작업 DB와 명시 저장본 분리 | 원본 직접 자동 갱신 대신 Ctrl+S/저장하지 않음 유지 | 사용자가 자동 저장 중심 UX를 요구할 때 |
| ADR-04 | 공유 membership와 명시 독립 복사 | 단순 변형 상속은 원본 의미와 다름 | 다른 의미 모델이 입증될 때 |
| ADR-05 | 정규화 관계 + typed TEXT 수치 | 전체 JSON blob·REAL 저장은 관계/정밀도 위험 | 동등성 증명과 migration 필요 |
| ADR-06 | Rust는 측정 후 배치 함수 | 처음부터 이중 언어로 구현하는 비용을 줄임 | 반복되는 CPU 병목이 확인될 때 |
| ADR-07 | 부분 호환을 명시 | 범용 호환 선언은 원본 corpus·규칙 증거 부족 | 형식별 테스트 추가 후 지원표 확대 |
| ADR-08 | QSqlTableModel 직접 쓰기 사용 안 함 | 모든 편집을 도메인 명령과 undo 경로로 통일 | 대체 모델도 명령 계약을 유지해야 함 |

## 위험·보류 사항

| ID | 위험·현재 상태 | 대응과 해결 증거 | 담당 작업 |
|---|---|---|---|
| RISK-01 | CDD 전체 스키마·OEM 차이 미검증 | 원본 corpus 버전별 매핑·손실 보고, 지원 범위 제한 | TASK-02, TASK-13 |
| RISK-02 | 정식 ODX 2.2 XSD·규칙 확보 미완료 | 정식 자료 경로·버전·해시를 기록한 뒤 XSD 테스트 | TASK-02 |
| RISK-03 | 설치된 ODX 도구의 실제 실행·라이선스·검증 기능 미확인 | 외부 재열기·필드 대조 기록; 설치 존재만으로 통과 금지 | TASK-02, TASK-15 |
| RISK-04 | 공유 DID/인스턴스 분리 오류 | Base/High 그래프 비교와 Undo 회귀 | TASK-06 |
| RISK-05 | 조건·전이의 ODX 의미 손실 | 정식 매핑 검증 전 출력 차단 | TASK-11, TASK-15 |
| RISK-06 | Qt model 호출·Python 처리로 UI 지연 | 페이지 캐시·worker·profile·실측 후 최적화 | TASK-08, TASK-16 |
| RISK-07 | WAL·저장 교체·manifest 중단 시점 | live backup·강제 종료·토큰 대조 | TASK-07, TASK-16 |
| RISK-08 | SQLite unsigned64·Decimal 정밀도 | typed TEXT와 경계 테스트 | TASK-04, TASK-05 |
| RISK-09 | migration 실패·미래 파일 쓰기 | 작업본 migration·원본 backup·미래 버전 쓰기 금지 | TASK-04, TASK-17 |
| RISK-10 | 원본 fixture만 통과하는 과적합 | 자체 경계·변형·비정상·실제 사용자 corpus 확대 | TASK-13, TASK-18 |

## 변경 관리

요구사항을 바꾸면 연결 UC/UI/VAL/TC/TASK를 함께 갱신한다. 스키마 변경에는 migration과 이전 데이터 읽기 테스트를 추가한다. 원본 분석의 사실은 근거 없이 설계 편의에 맞춰 수정하지 않는다.

프로젝트 파일 형식·MVP 범위·저장 UX·숫자 표현의 변경은 새 ADR로 기록한다. 이전 결정을 삭제하지 않고 대체 관계를 적는다. 문서 완료 후에도 RISK의 외부 증거는 해당 개발 작업에서 해결하며, 보류가 있다는 사실을 숨기지 않는다.

## 이번 문서 산출물 검수 기록

검수일: 2026-09-25. 실제 제품 구현·배포 테스트와 구분한 문서 검수다.

- Markdown 15개 생성, 상대 링크 대상 존재, 코드 블록 닫힘, UTF-8 읽기 확인.
- 기능 FR-001~024, 품질 NFR-001~008, 시나리오 UC-01~12, 화면 UI-01~12, 규칙 VAL-001~020, 테스트 TC-001~038, 작업 TASK-01~19의 ID 범위 확인.
- 모든 기능·품질 요구사항의 테스트·작업 추적표 연결 확인.
- 문서의 전체 DDL을 Python sqlite3 / SQLite 3.50.4 메모리 DB에서 실행: 테이블 35개, 명시 인덱스 20개, application_id 0x44494147, integrity_check=ok, foreign_key_check 오류 없음.
- DDL 설계 확인 24건 통과: 변형별 DID 중복, definition/membership 번호 일치·갱신 전파, 기본 변형 중복, 참조 삭제 제한, 범위·NULL·STRICT 제약, 원자적 rollback, u64 TEXT 보존, 구조 멤버 조합·소유, 지연 상태 참조, 상태 그룹 참조, 서비스 범위, 백업 스냅샷 독립성 등을 확인했다.

위 결과는 메모리 DB에서의 스키마 설계 확인이다. 파일 기반 WAL 중단 복구, Windows 잠금·원자적 교체, Qt UI, CDD 가져오기, ODX 출력·외부 상호운용, 성능 및 배포 테스트는 아직 실행하지 않았다. 해당 테스트를 통과한 것으로 표시하지 않는다.
