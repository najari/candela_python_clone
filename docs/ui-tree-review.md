# UI·탐색 트리 검토 — CANdelaStudio 13 대비 (2026-09-25)

근거
- CANdelaStudio 13 도움말(`build\reference\candela_help`): `20 UserInterface/Tree/Navigation Tree` [TREE-R010], `Tree/Icons` [TREE-R400], `10 Introduction/Data Model Overview`, `Diagnostic Class`·`Diagnostic Instance(s)`, `DID Overview` [DIDOV-R610..], `DTC Overview`, `State Dependency/State Groups·Dependencies`, `Service Property Page State Transitions`, `Variant/Variants·Compact View`, `Qualifier Overview`, `Attribute Overview`, `Shortcut Qualifier Overview`, `30 Features/CANdelaStudio Editor/Views`.
- 설치 예제 `ABS_ESP-Example.cdd`의 실제 구조.
- DiagAuthor 현재 코드: `ui.py` `apply_state`, schema v3, `apply_cdd`. 예제를 실제로 가져온 DB를 조회해 확인했다.
- CANdelaStudio 실행 화면은 캡처하지 않았다. 트리 순서와 노드 구성은 도움말 [TREE-R010]을 따랐다.

> 진행 상황: T0–T4 구현(T3·T4는 GPT 세션 구현 후 Claude 검토·보완). 저장된 `.diagproj`와의 비교는 DID·DTC·인스턴스·서비스까지 확장 — 2026-09-25. 설명·번역·사용자 속성 비교는 후속 범위. 회귀·Windows 빌드 결과는 [BUILD-NOTES.md](../BUILD-NOTES.md)를 따른다.

## 1. 결론

1. 지금 트리는 변형마다 "DID / DTC / 서비스" 목록을 고르는 필터다. CANdela 트리의 중심인 **진단 클래스 → 진단 인스턴스** 계층이 없다. 그래서 가져온 서비스 48개가 인스턴스·클래스 구분 없이 평면 목록으로 보인다.
2. CANdela는 서비스를 트리에 넣지 않는다. 트리는 인스턴스에서 끝나고, 서비스·전문 표(telegram table)·데이터 객체는 인스턴스의 우측 페이지에 나온다(Data Model Overview). DiagAuthor도 이 구조를 따르는 것이 맞다.
3. 트리 보강의 절반은 DB에 이미 있는 데이터로 할 수 있다: 클래스, 인스턴스, 변형 소속, 상태 그룹과 상태, 조건·전이, DID·DTC의 변형 소속, 속성, 소유자가 있는 보존 항목. 나머지 절반은 가져오기와 스키마부터 고쳐야 한다(2절 결함 D1–D5).
4. 우측 영역은 지금 "종류별 표 + 텍스트 속성" 한 가지뿐이다. CANdela는 노드 종류마다 페이지가 다르다: 클래스는 인스턴스 목록, 인스턴스는 서비스 목록과 전문 탭, 상태는 의존성 표를 보여 준다.

## 2. 트리 전에 고칠 가져오기·데이터 결함

ABS_ESP 예제를 가져온 DB에서 확인한 결과다.

| # | 현상 | 원인 | 트리에서 보이는 영향 |
|---|---|---|---|
| D1 | 클래스 객체 15개 중 `ControlDTCSetting` 4개, `DynamicData` 2개가 중복된다 | 클래스 키가 변형별 `DIAGCLASS` id다. 같은 템플릿(`tmplref` → DCLTMPL)이면 같은 클래스여야 한다 | 변형마다 같은 이름의 클래스가 따로 생긴다. 비교와 공유 판단이 틀어진다 |
| D2 | 인스턴스 42개 중 36개만 들어온다. `Fault Memory`, `Wheel Speed`, `Communication Control` 4개가 빠진다 | `apply_cdd`가 가져올 수 있는 서비스가 없는 인스턴스를 건너뛴다 | CANdela 트리에 있는 노드가 사라진다. `Communication Control` 클래스는 통째로 없어진다 |
| D3 | 클래스와 인스턴스의 문서 순서가 없다 | `diagnostic_classes`와 `variant_instances`에 ordinal 열이 없다 | CANdela 순서(Sessions, Identification, …)로 그릴 수 없다 |
| D4 | 싱글턴 인스턴스(`Tester Present`)가 가짜 클래스 `Tester_Present`에 들어간다 | `class_id`가 NOT NULL이라 role 이름으로 클래스를 만든다 | 변형 바로 아래 있어야 할 인스턴스가 클래스처럼 보인다 |
| D5 | 공유가 참조인지 사본인지, 원본이 어느 변형인지 모른다 | `variant_instances`는 소속만 기록한다. CDD의 `DIAGINST`(원본)와 `DIAGINSTREF`(참조) 구분이 없어진다 | [TREE-R400] 아이콘 5종 가운데 "확장 변형 원본을 다른 확장 변형이 참조"를 구분할 수 없다 |

그 밖에 원문으로만 보존된 채 트리에 나오지 않는 것이 있다: NRC 40개(`NEGRESCODES`), 프로토콜 서비스 31개, DCL 템플릿 17개, Job 컨테이너 2개(+ 참조 6개), 변형 식별 패턴(`IDENTIFICATIONSET`).

## 3. CANdela 트리와 현재 상태

Standard View 기준이며, 순서는 [TREE-R010]을 따른다.

| CANdela 노드 | 내용 | ABS_ESP | DiagAuthor 현재 | 판정 |
|---|---|---|---|---|
| ECU Information | 문서 정보. 메뉴: Qualifier·Attribute·Target Group 개요, 비교 | — | ECU 이름 노드, 전용 페이지 없음 | 보강 |
| Fault Memory | DTC 목록, DTC 개요(변형별 yes/no, 같은 속성끼리 한 줄) | DTC 31 | 변형 아래 "DTC" 목록 | 보강 |
| DID Overview | DID × 변형 yes/no, 같은 속성끼리 한 줄, "Go To" | DID 19 | 없음 | 신규(데이터 있음) |
| Libraries | 라이브러리 | 0 | 없음 | 범위 밖 |
| Base Variant | DIDs, 진단 클래스 → 인스턴스, 싱글턴 인스턴스, Job 컨테이너 | 클래스 11, 싱글턴 2, Job 2 | ◆ 변형 → DID/DTC/서비스 | **재구성** |
| Variants | 확장 변형 목록 페이지(이름, 식별 패턴, 설명) + 각 변형 | A/B/C Sample | ◇ 변형이 ECU 바로 아래 | 재구성 |
| States | 상태 그룹·상태, 의존성(개요·편집·그래프), 기본값 | 그룹 2, 상태 5 | 루트 "상태" 평면 목록 | **재구성**(데이터 있음) |
| Vehicle System Groups | | — | 없음 | 범위 밖 |
| Data Types | 데이터 타입 | 105(가져옴 90) | 루트 "데이터 타입" | 유지 |
| Protocol Services | 읽기 전용 목록: Request/Pos/Neg 개요, Used, Required | 31 | 원문 보존만 | 읽기 전용 신규 |
| Diagnostic Class Templates | Expert View 전용 | 17 | 원문 보존만 | 후순위 |
| Negative Responses | NRC 목록 | 40 | 서비스 상세의 코드 숫자만 | 신규 |
| Default Attributes / Import Pool / Requirements | Expert 전용 또는 부가 기능 | — | 없음 | 범위 밖 |
| (DiagAuthor 고유) 미지원 항목 | 보존 원문, 출력 차단 여부 | 66 | 루트 한 곳에 모음 | 유지하고, 소유자가 있는 15개는 해당 노드에도 ⚠로 표시 |

## 4. 제안 트리

ABS_ESP 예제로 그린 모습이다. 괄호 안은 자식 수이고, `[..]`는 인스턴스 식별자(서비스에서 유도)다.

```
ABS_ESP                              ECU 정보 페이지
├─ 고장 메모리
│  └─ DTC 개요                       DTC × 변형 yes/no
├─ DID 개요                          DID × 변형 yes/no
├─ ◆ Base Variant                    변형 페이지(이름·설명·식별 패턴)
│  ├─ DID (19)
│  ├─ DTC (31)
│  ├─ Sessions (3)                   클래스 페이지 = 인스턴스 표
│  │  ├─ Default Session [01]        인스턴스 페이지 = 서비스 표 + 전문 탭
│  │  ├─ Programming Session [02]
│  │  └─ Extended Diagnostic Session [03]
│  ├─ Identification (7)
│  │  └─ VIN [F190] …
│  ├─ Security Access · Control DTC Setting · Dynamic Data (9) · Stored Data
│  ├─ Variant Coding · IO Control · Routine
│  ├─ Communication Control (4)  ⚠   D2 수정 후 표시(서비스는 보존 원문)
│  ├─ Ecu Reset (2)
│  ├─ Fault Memory  ⚠                싱글턴 인스턴스(D2·D4)
│  ├─ Tester Present                 싱글턴 인스턴스(D4)
│  └─ 🔒 Generic Jobs · Flash Jobs    Job 컨테이너, 보존·읽기 전용
├─ 변형                              확장 변형 목록 페이지
│  ├─ ◇ A Sample                     같은 하위 구조. 공유 인스턴스는 참조 아이콘
│  ├─ ◇ B Sample
│  └─ ◇ C Sample                     Dynamic Data에 로컬 인스턴스 Yaw Rate Sensor
├─ 상태
│  ├─ Session (Default · Programming · Extended)
│  ├─ SecurityAccess (Locked · Unlocked L1)
│  └─ 의존성                          서비스 × 상태 표(허용/차단/전이)
├─ 데이터 타입 (90)
├─ 프로토콜 서비스 (31)  읽기 전용
├─ 부정 응답 코드 (40)   읽기 전용
└─ 가져오기 보존 항목 (66)
```

노드 표시 규칙
- **공유 아이콘**([TREE-R400]): 기본 변형에만 있음 / 기본 변형 것을 확장 변형이 참조 / 확장 변형 로컬 / 확장 변형 것을 다른 변형이 참조. 앞의 셋은 지금 데이터로 유도할 수 있고, 마지막은 D5를 고쳐야 한다. 비활성 인스턴스는 흐리게 표시한다.
- **필수 인스턴스는 굵게**: CANdela는 `req="req"`를 굵게 표시한다. 이 값은 이미 속성으로 가져오고 있다.
- **⚠ 부분 지원**: 보존 원문이 연결된 노드(`unsupported_nodes.owner_object_id`)에 붙인다. 툴팁에 사유를 보여 준다.
- **개수**: 목록형 노드에는 자식 수를 붙인다.
- 공유 인스턴스는 변형마다 같은 객체다. 한 변형에서 고르면 다른 변형의 같은 노드도 같은 속성을 보여 준다(편집 경고는 지금처럼 공유 영향으로 표시한다).

## 5. 우측 페이지(노드별)

| 노드 | CANdela 우측 내용 | 제안 |
|---|---|---|
| ECU | 문서 속성 | 이름, 제조사, 원본 파일, revision, 개수 요약 |
| 변형 목록 / 변형 | 이름·식별 패턴·설명 표 / 이름·설명·속성 | 같게. 식별 패턴은 보존 원문을 읽기 전용으로 보여 준다 |
| 진단 클래스 | 인스턴스 표: 아이콘, Identifier(16진), 이름, 서비스 이름들, 표시 지정 속성. 필터. "Go To DID" | 같게. "Go To DID"는 `instance_dids`로 만든다 |
| 진단 인스턴스 | 이름·설명·Identifier, 서비스 목록, 전문 탭(zz 등), DID면 탭 머리에 "DID" | 서비스 표 + 선택 서비스의 요청/긍정/부정 전문 탭. 지금 `details_text`의 layout을 탭 표로 옮긴다 |
| 상태 그룹 | 상태 목록(첫 상태 = 초기 상태), semantic, 기본 NRC | 같게(읽기 전용부터) |
| 의존성 | 개요 표 / 편집 표 / 그래프(GraphViz) | 개요 표: 행 = 서비스, 열 = 상태, 셀 = 허용·차단·전이 대상. 편집은 기존 조건 대화상자로 연결. 그래프는 후순위(GraphViz는 번들하지 않는다) |
| DID 개요 / DTC 개요 | 행 = 코드, 열 = 변형 yes/no, 속성이 같은 것끼리 한 줄, Go To | 같게. 데이터가 있어 바로 할 수 있다 |
| 프로토콜 서비스 / NRC | 읽기 전용 목록 | 보존 원문을 파싱해 표로만 보여 준다. 출력 의미에는 쓰지 않는다 |

## 6. 트리 밖의 UI 기능

| 기능 | CANdela | 우선순위 | 비고 |
|---|---|---|---|
| 트리 상태 유지 | 편집해도 펼침·선택 유지 | **P1** | 지금은 `apply_state`마다 `tree.clear()`를 해서 편집할 때마다 트리가 접힌다 |
| 트리 모델 | — | **P1** | 명세(04-ui-specification)는 `QAbstractItemModel`이다. 지금은 `QTreeWidget`. 노드 수가 늘어나므로 모델로 바꾸고 작업 스레드의 `tree()` API 한 번으로 받는다 |
| 검증 결과에서 트리로 이동 | Go To | P1 | 지금은 표 종류만 바꾼다. 트리 노드를 펼쳐 선택하게 한다 |
| 객체 경로 | Path Entry [TB-R310], Copy Path | P2 | `Base Variant/Sessions/Default Session/Start` 형식. 트리 필터와 함께 둔다 |
| 컨텍스트 메뉴 | Go To DID / 데이터 타입, 속성, 경로 복사 | P2 | 기존 명령 재사용 |
| Qualifier 개요 | 문서 또는 하위 범위의 qualifier 표 | P2 | 경로 열 포함. ODX SHORT-NAME 충돌 확인에 쓸모가 크다 |
| Shortcut Qualifier 개요 | 서비스 × 변형, ODX SHORT-NAME | P2 | ODX 출력 이름 확인용 |
| Attribute 개요 | 하위 범위 속성 표, 일괄 설정 | P3 | `object_attributes`가 있다 |
| 변형 Compact View | 두 변형 비교: 참조가 아닌 인스턴스 | P3 | D5 수정 후 |
| Compare View | 문서·객체 비교 | P3 | |
| 상태 그래프 | GraphViz | P3 | |
| Translation View | 언어별 용어 표 | P3 | 지금은 로캘 전환만 있다 |
| Expert View, Libraries, Vehicle System Groups, Requirements, Import Pool | | 범위 밖 | 템플릿 편집은 하지 않는다 |

## 7. 구현 순서 제안

| 단계 | 내용 | 스키마 | 작업 강도 |
|---|---|---|---|
| T0 | 가져오기 결함 D1–D4: DCLTMPL 기준 클래스, 서비스 없는 인스턴스도 가져오기(서비스는 보존), 클래스·인스턴스 순서, 싱글턴 인스턴스 | v4: `variant_classes(variant_id, class_id, ordinal)`, `variant_instances.ordinal`, 싱글턴 표현 | 설계 high → 구현 low |
| T1 | 트리 재구성(4절), `Application.tree()`, `QAbstractItemModel`, 펼침·선택 유지, 아이콘 3종·굵게·⚠·개수, 클래스·인스턴스·상태 그룹 페이지, 의존성 개요 표, DID·DTC 개요 | 없음 | low |
| T2 | D5 참조·사본 구분(아이콘 5종 완성), NRC 목록, 프로토콜 서비스·Job 읽기 전용 목록 | v5: `variant_instances.is_reference` 또는 owner, `nrcs` | 설계 high → 구현 low |
| T3 | 경로·Go To, 컨텍스트 메뉴, Qualifier·Shortcut Qualifier 개요 | 없음 | low |
| T4 | Attribute 개요, Compact View, Compare, 상태 그래프, 번역 표 | — | 필요할 때 |

T1은 T0 없이도 시작할 수 있다. 다만 그러면 중복 클래스와 누락 인스턴스가 트리에 그대로 드러난다. T0을 먼저 하기를 권한다.

## 8. 결정할 것

1. DTC 위치: CANdela처럼 "고장 메모리"에 모을지, 지금처럼 변형 아래 둘지. 제안: 두 곳 모두 두는 것이다. DTC가 변형 소속(`variant_dtcs`)이라 변형 아래 목록이 필요하고, 개요는 고장 메모리에 둔다.
2. 서비스를 트리 노드로 둘지. 제안: CANdela처럼 두지 않는다. 대신 인스턴스 페이지에 둔다. 변형별 "전체 서비스" 평면 목록은 보조 노드로 남긴다.
