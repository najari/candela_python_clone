# 배포·유지보수 명세

[목차](README.md) · [저장·복구](08-editing-save-recovery.md) · [개발 계획](12-development-roadmap.md)

## 개발·빌드 기준

초기 환경 조사에서 Python 3.13.15, PySide6 6.11.2, SQLite 3.50.4를 확인했다. 이는 현재 컴퓨터의 설치 정보이며 제품 최소 버전·최신 보안 버전의 보증이 아니다. 제품 개발은 프로젝트 전용 가상환경과 검증된 패치 버전을 고정해서 진행한다.

Python 표준 sqlite3, PySide6, lxml을 기본 런타임으로 사용한다. 개발 도구는 pytest·pytest-qt다. 첫 MVP는 Rust 도구체인을 요구하지 않는다. Python·Qt·lxml·내장 SQLite의 버전을 빌드 manifest와 정보 화면에 기록한다.

설치된 전역 Python을 수정하지 않는다. 의존성 목록·해시·빌드 명령·환경 정보를 버전 관리한다. `.diagproj` 스키마 버전, 프로파일 버전, 앱 버전을 각각 분리한다.

## 패키징

pyside6-deploy를 사용하고 초기에는 one-folder Windows x64 배포를 기준으로 한다. Qt plugins, sqlite3 런타임, lxml 네이티브 라이브러리, worker 프로세스, 공식적으로 확보한 로컬 XSD의 포함 여부를 확인한다. 첫 검증은 ZIP 배포본, 정식 설치기는 후속 배포 작업에서 동일 폴더 산출물을 패키징한다.

Python이 없는 Windows에서 새 프로젝트·DB 저장·복구·CSV·XML 검사·worker 실행을 확인한다. 개발 폴더의 DLL/PATH에 의존하는 실행은 실패로 판정한다. 설치 후 네트워크가 없는 상태에서도 정상 동작해야 한다.

Rust를 도입하면 고정 Python ABI와 Windows 아키텍처에 맞춘 wheel을 포함하고 네이티브 로드 실패를 검사한다. 설치 프로그램이 사용자 프로젝트를 자동 migration하거나 삭제하지 않는다.

참고: [pyside6-deploy](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html).

## 사용자 파일 배치

앱 데이터 폴더 이름은 초기 기술 이름 `DiagAuthor`로 통일한다. 향후 제품 이름이 바뀌면 명시 migration을 제공한다.

| 위치 | 내용·정책 |
|---|---|
| 사용자 선택 폴더 | `.diagproj`, 직전 `.bak`, 내보내기 파일 |
| `%LOCALAPPDATA%/DiagAuthor/settings.json` | 창 배치·최근 경로·표 표시 설정; 진단 모델 없음 |
| `%LOCALAPPDATA%/DiagAuthor/workspaces/<session_uuid>/` | working.sqlite, WAL/SHM, manifest, 임시 분석 결과 |
| `%LOCALAPPDATA%/DiagAuthor/logs/` | 회전 로그; 파일당 10MiB, 최대 5개 |
| `%LOCALAPPDATA%/DiagAuthor/quarantine/` | 손상 복구 후보; 자동 삭제하지 않음 |

정상 닫기·명시 폐기 세션만 정리한다. 경로를 지우기 전 앱 전용 루트 아래인지 검사한다. 미해결 복구·격리 데이터는 사용자의 명시 폐기 전 보존한다. 원본 `.diagproj`와 CDD는 자동 정리 대상이 아니다.

네트워크 공유 폴더·동기화 중인 원본에 대한 동시 쓰기를 MVP에서 지원하지 않는다. 작업 DB는 항상 로컬에 두며 파일을 전달할 때는 명시 저장본을 사용한다.

## 버전 호환·업그레이드

| 입력 상태 | 처리 |
|---|---|
| 현재 자체 스키마 | 정상 열기·작업본 생성 |
| 지원하는 과거 스키마 | 원본 백업 후 작업본에서 migration |
| 미래 자체 스키마 | 편집 거부; 식별 가능한 기본 정보만 표시 |
| 손상 파일 | 정상본/복구본 안내, 원본 변경 금지 |
| 알 수 없는 CDD 버전 | unsupported version 보고, 자동 강제 변환 없음 |

업그레이드 후 최초 저장 전까지 원본은 유지한다. 다운그레이드 쓰기는 MVP에서 제공하지 않는다. `.bak`은 해당 파일을 쓴 앱/스키마 버전과 함께 관리한다. migration 결과를 임의 SQL로 수리하고 성공으로 표시하지 않는다.

## 로그·오류 지원

기본 로그 필드는 UTC 시각, level, event code, job/session ID, revision, rule ID, 처리량, 소요 시간이다. DID/DTC 설명 원문, 원본 XML, 바이트 payload, 전체 파일 경로는 기본 로그에 넣지 않는다. 파일 식별은 basename·해시 일부로 제한한다.

사용자가 선택한 진단 보고서에는 입력 해시·버전·검증 오류·미지원 coverage를 담는다. 원본 데이터 포함은 별도 선택으로 분리한다. 보고서는 로컬에 저장하고 자동 업로드하지 않는다.

로그 성공, XML 파싱 성공, XSD 성공, 외부 도구 재열기 성공을 서로 다른 event로 기록한다. 하나의 단계를 통과했다는 이유로 전체 호환 완료 이벤트를 만들지 않는다.

## 유지보수 검수

앱 업데이트마다 TC-003/005/027/031/033/037/038을 회귀 검사한다. DB 스키마 변경은 관련 DDL·도메인·파일 변환·추적표를 함께 수정한다. Qt 버전 변경은 고해상도·IME·클립보드·worker·패키징을 확인한다. 설치 패키지에 포함한 구성요소 목록과 고지 파일을 유지한다.
