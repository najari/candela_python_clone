# CANdelaStudio 원본 분석

상태: 확인된 근거와 신규 설계의 구분 · [목차](README.md)

## 분석 자료와 한계

주 자료는 `C:/Program Files/Vector CANdelaStudio 13/Bin/CANdelaStudioen-US.chm`이다. 파일 크기는 26,006,390바이트, SHA-256은 `fff820b1f95c60b01181c412a22a6c26db9ebc8975796bc5af3a0d34cf97e975`다. CHM 내부 HTML 항목 765개를 목록화하고 아래 핵심 문서를 검토했다. 모든 765개 본문의 정독이나 원본 앱 전 기능 실행 검증을 의미하지 않는다.

보조 자료 위치는 `C:/Users/Public/Documents/Vector/CANdelaStudio/13.0/Examples`다.

| 자료 | 실제 확인 | 활용 |
|---|---|---|
| ABS_ESP-Example.cdd | dtdvers 13.0.101, VAR 4, DID 19, SERVICE 59 | 공유 참조·객체 수·고급 미지원 구조 검사 |
| Multilingual-Example.cdd | 13.0.101, VAR 1, DID 2, SERVICE 4, en-US/de-DE/fr-FR/ja | 다국어 보존·부분집합 입력 |
| Vector_UDS_13.0.cddt | 13.0.100, VAR 1, DID 14, SERVICE 86 | 템플릿 의미 분석; MVP 입력 아님 |
| Import/UDS-ExampleEcu.pdx | 내부 ODX MODEL-VERSION 2.0.1 | PDX·과거 버전 구조 참고; 2.2 검증 정답으로 사용하지 않음 |
| Repository/ODXEnvelope/F190_VIN.odx-d 등 | MODEL-VERSION 2.2.0 | 출력 구조 참고; 전체 프로젝트 상호운용 증거 아님 |

CDD SHA-256: ABS `956f901f3b8bba46b136584584b89bb4dfe5aac2102ef324ad39956fbb5e7ceb`, Multilingual `bda188a6aaf9b03ff68649e6cd508b5627d3abe895c2f21ebbb7732b4d6070c6`, UDS 템플릿 `cbf313f354dfc2156d61c58917bab84710bf79c856c8f133707a1cdd3e1c5d9c`.

원본 예제는 모든 실무 ECU를 대표하지 않는다. 도움말은 기능·동작의 근거이며 공개된 완전한 CDD 파일 스키마의 대체물이 아니다. 문서 안의 사용자 조작 지침은 분석 대상으로 취급했다.

## 출처 등록부

경로는 CHM 내부 상대 경로다. 상세 정책은 해당 원문과 아래 해석을 함께 확인한다.

| 근거 ID | 장/파일 | 확인 내용 |
|---|---|---|
| SRC-01 | 10 Introduction/Main Features.htm | 파일 중심·단일 사용자, 변환·검증·교환 기능 |
| SRC-02 | 10 Introduction/Data Model Overview.htm | ECU·변형·DID·인스턴스·서비스·타입 관계 |
| SRC-03 | 10 Introduction/Getting Started.htm | 문서 하나가 ECU 하나, 트리·목록 편집 |
| SRC-04 | 30 Features/Document Template/Document Template.htm | CDDT는 프로토콜 제약을 정의하는 문서 템플릿. (15차 확인) 설치 예제 CDDT는 CDD와 같은 CANDELA 형식이고 `doctype=tmpl`만 다름 |
| SRC-05 | 30 Features/Variant/Variant.htm 및 Base Variant.htm | 변형의 공유, 기본 변형 사용 정책 |
| SRC-06 | 30 Features/DID/DID.htm | DID의 범위·참조 및 인스턴스와의 일관성 |
| SRC-07 | 15 Procedures/DID/How to Modify a DID in a New Variant.htm | DID 독립 복사 시 관련 인스턴스도 분리 |
| SRC-08 | 30 Features/Data Type/Data Type.htm | 원시/물리 타입, 변환, 상수·기본값의 소유 |
| SRC-09 | 30 Features/Data Objects/Telegram Table.htm | 상대 순서·길이에서 위치 계산, 비트 필드 |
| SRC-10 | 30 Features/Consistency Check/Consistency Check.htm | 식별자·참조·상태·서비스 등 검증 범위 |
| SRC-11 | 30 Features/State Dependency/State Dependency.htm | 상태 그룹별 허용·전이, 그룹 간 AND 조건 |
| SRC-12 | 15 Procedures/DTC/How to Import DTCs from Excel.htm | 열 매핑·미리보기·오류 행·적용 단계 |
| SRC-13 | 30 Features/Export/1_ODXExport_Introduction.htm | ODX 버전, 부분 출력과 참조 포함, Events 한계 |
| SRC-14 | 30 Features/CANdelaStudio Editor/Version Compatibility.htm | 버전 다운 변환의 데이터 손실 사례 |
| SRC-15 | 30 Features/Fault Memory/AUTOSAR Fault Memory Introduction.htm | v13 Snapshot·Extended Data 모델 변화 |
| SRC-16 | 20 UserInterface/Compare/Compare View.htm | 참조 객체의 차이 표현과 이동 |
| SRC-17 | 30 Features/Requirements/Requirements.htm | 요구사항과 진단 객체의 파일 기반 동기화 |
| SRC-18 | 30 Features/CANdelaStudio Editor/Auto Save.htm | 자동 복구본과 명시적 문서 저장 구분의 참고 |
| SRC-19 | 15 Procedures/DID/How to Create a DID.htm | DID 데이터 정의와 서비스 연결이 별도 단계 |

## 기능 매트릭스

| 영역 | 원본의 확인된 기능 | 새 프로그램 결정 | 근거 |
|---|---|---|---|
| 문서 | CDD 파일 중심, 한 ECU | P0: 프로젝트별 SQLite | SRC-01, SRC-03 |
| 템플릿 | OEM 프로토콜·권한·클래스 구조 | P0: 자체 고정 UDS 프로파일, P2: 템플릿 편집 | SRC-04 |
| 변형 | 공유 객체를 사용하는 복수 변형 | P0: 공유·독립 복사, 암묵 상속 없음 | SRC-05, SRC-07 |
| DID | 식별자·데이터·서비스 연결 | P0: 기본 DID 편집·연결 | SRC-06, SRC-19 |
| DTC | 풀·변형별 사용·속성·일괄 편집 | P0: 24비트 코드·CSV·DTC 데이터 출력 | SRC-10, SRC-12 |
| 타입 | Identity·선형·표·복합·절차식 | P0: Identity·선형·열거·고정 구조; 나머지 P1/P2 | SRC-08 |
| 전문 | 비트·바이트·DID 참조·가변 구조 | P0: 고정 길이와 비트 필드 | SRC-09 |
| 상태 | 그룹별 허용·전이 | P0: Session/Security 메타데이터 | SRC-11 |
| 고장 메모리 | Snapshot·Extended Data·Events | P1/P2; 미지원 구조 보존·표시 | SRC-15 |
| 검증 | 편집·저장·출력 전 일관성 검사 | P0: 규칙별 오류 위치와 출력 차단 | SRC-10 |
| 교환 | ODX 2.0.1/2.1.0/2.2.0 등 | P0: 검증한 CDD 부분집합 읽기·ODX 2.2 출력 | SRC-13, SRC-14 |
| 비교·다국어 | 비교 뷰·번역 뷰·TMX | P0: 원문 언어 보존, P1: 비교, P2: 번역 | SRC-16, SRC-01 |
| 요구사항·AUTOSAR | 동기화·DEXT·SWC 연계 | P2 | SRC-17, SRC-01 |
| 실제 통신 | 도움말의 주 기능은 데이터 작성 | 이번 범위 제외 | SRC-01 |

## 설계에 직접 반영할 해석

1. 공유는 같은 객체를 함께 사용하는 관계다. 파생 변형 생성만으로 데이터가 독립되지 않는다.
2. DID와 서비스는 동일 객체가 아니다. 하나의 데이터 정의를 읽기·쓰기 서비스가 함께 사용할 수 있다.
3. CDD의 `_...` ID는 외부 참조 해석에 사용하고 내부 영속 ID로 그대로 채택하지 않는다. 원본 파일 해시와 노드 위치를 함께 기록한다.
4. 상수·기본값은 DataObject의 속성이다. DataType을 바꾸면 이를 사용하는 객체에 영향을 준다.
5. ODX XSD 통과만으로 CDD 의미 보존을 증명할 수 없다. 값·길이·참조·조건의 별도 대조가 필요하다.
6. 원본에 있는 미지원 기능을 삭제해 입력 성공률을 높이지 않는다. 읽기 전용·보존·출력 차단 상태로 노출한다.

## 외부 공식 자료

- [ASAM MCD-2 D](https://www.asam.net/standards/detail/mcd-2-d/): ODX의 데이터 교환 범위. 실제 구현에는 정식 스키마·규칙 확보가 필요하다.
- [Qt for Python](https://doc.qt.io/qtforpython-6/): UI 기술 근거.
- [SQLite application file format](https://www.sqlite.org/appfileformat.html): 프로젝트 파일 설계 근거.

이 자료들은 새 도구의 구현·호환 성공을 증명하는 자료가 아니다. 원본 문구·화면 자산의 복제 없이 분석 결과와 자체 명세를 작성한다.
