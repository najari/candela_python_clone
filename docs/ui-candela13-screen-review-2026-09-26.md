# UI 변경 검토 — CANdelaStudio 13 실제 화면 대비 (2026-09-26)
## 근거

- **CANdelaStudio 화면**: 13.0 SP1 View Edition에서 `examples\candela_example\ABS_ESP-Example.cdd`를 열어 노드별 페이지, 대화상자, 리본, 출력 창을 직접 확인했다. View Edition은 저장할 수 없고, 문서도 바꾸지 않았다.
- **DiagAuthor 화면**: 2026-09-26 18:35 run_checks GUI 스모크 스크린샷 02–11과 `ui.py`(트리 `build_tree`, 페이지 `show_page`), `presentation.py`(`details_text`).
- **앞선 문서와의 관계**: `ui-tree-review-2026-09-25.md`는 도움말을 근거로 썼다. 이 문서는 실제 화면으로 그 내용을 확인하고 우측 페이지와 대화상자 수준까지 넓힌다. T0–T2는 이미 구현됐고, 경로·Shortcut 개요·참조 영향 등 T3 일부도 스모크에서 확인된다.

## 1. 결론

1. **가장 큰 차이는 우측 영역이다.** CANdela는 노드 종류마다 전용 페이지(폼 + 탭 + 표 + 바이트 격자)를 보여 준다. DiagAuthor는 어떤 노드든 "페이징 표 + 텍스트 속성창 + 모달 편집"이다. 특히 **진단 인스턴스 페이지**가 없다. 서비스별 요청·응답 바이트, 파라미터 탭, 바이트 격자는 CANdela 작업의 중심인데, DiagAuthor에서는 텍스트 속성창의 `layout_lines`로만 볼 수 있다.
2. **트리 뼈대는 대체로 맞다.** 다만 CANdela의 중간 노드가 빠져 있다: Supported Diagnostic Classes, Identifying Features/Patterns, States 하위 3개, Negative Responses 하위 2개, ECU Information 하위의 인터페이스·통신 파라미터. 인스턴스 표기도 다르다.
3. **경로는 사람이 읽을 수 있어야 한다.** CANdela는 `npath:/Base Variant/Identification/VIN`을 쓴다. DiagAuthor는 `diag://<UUID>/<UUID>/object/<UUID>`라서 경로 창을 사람이 읽거나 입력할 수 없다.
4. **공유 객체 편집 방식이 다르다.** CANdela는 공유 DID·인스턴스를 편집할 때 "참조하는 모든 변형을 수정"과 "이 변형에서 사본으로 바꾼 뒤 수정" 중 하나를 고르게 한다. DiagAuthor는 영향만 보여 준다(09 참조 영향). 공유 모델을 바꾸는 의미 변경이므로 high 검토가 필요하다.
5. **개요 표는 변형을 열로 두는 행렬이어야 한다.** CANdela의 DTC·DID·Shortcut Qualifier 개요는 모두 행 = 객체, 열 = 변형(yes/no)이다. DiagAuthor의 Shortcut 개요는 변형마다 행이 따로 있어 209행이 된다.
6. **바로 고칠 결함이 2건 있다**(U1).
   - 트리에서 다른 노드로 옮겨도 속성창에 이전 객체가 남는다(05: Identification을 골랐는데 Acceleration_Sensor가 보인다).
   - 검증을 실행하기 전에는 검증 결과 표 머리가 DID 열(DID·이름·Qualifier·공유 변형·지원)로 나온다(02, 03).

## 2. 노드별 비교

"CANdela"는 이번에 직접 확인한 화면이다. "변경"의 읽기 전용 항목은 보존 원문에서 표시만 하고 출력 의미에는 쓰지 않는다. 기존 원칙대로 출력 차단은 유지한다.

### 2.1 문서 수준

| 노드 | CANdela | DiagAuthor | 변경 |
|---|---|---|---|
| ECU Information | 이름, 서식 있는 설명. 하위 `Supported Interfaces`는 인터페이스 체크 목록(Diagnostic CAN, Comfort CAN, Powertrain CAN, CAN FD)이다. 그 아래 `Diagnostic CAN`은 통신 파라미터 표(Name, Category, Overwritten Value, Description; P2/P2*, S3, CAN ID, STmin, Baudrate 등) | ECU 이름 노드뿐이고 페이지가 없다 | ECU 페이지(이름, 설명, 원본 파일, revision, 개수 요약). 인터페이스와 통신 파라미터는 보존 원문을 **읽기 전용 표**로 보여 준다 |
| Diagnostic Trouble Codes | 장 소개 문장. `Available DTCs Fault Memory (24 Bit)`: DTC(C002011처럼 SAE 문자 형식), Error text, Set/Reset condition, Corrective action, Special instruction, Vehicle System Groups 열과 전체 열 필터, Show column info, DTC Import/Export. `Overview Fault Memory`: DTC × 변형 yes/no, Show details, Show libraries | `고장 메모리 > DTC 개요`, `Base Variant > DTC (31)` | DTC 표에 속성 열(조건·조치)을 추가한다. DTC 코드 **표시 형식 옵션**(0x 16진 / SAE 문자형)을 두고, SAE 형식은 표시 전용으로 원시값을 툴팁에 함께 보인다 |
| DID Overview | Identifier, Name, 변형별 yes/no, Used, DID Data(데이터 객체 이름 목록). Show DID Data, Show Libraries, 검색 | `DID 개요 (19)` | Used와 DID Data 열을 맞춘다 |
| Protocol Services | 읽기 전용 표: Name `(0x10) DiagnosticSessionControl`, Request `10 LL`, Pos `50 LL zz`, Neg `7F 10 rc`, Used, Required | `프로토콜 서비스 (31)` | 요청·응답 **패턴 열**(자리표시 LL/zz/rc)과 Used/Required 열을 추가한다 |
| Negative Responses | 장 노드 아래 `Available Negative Response Codes`(코드, 이름, 설명)와 `Negative Response Codes for unsupported services`(0x11, 0x12) | `부정 응답 코드 (40)` 하나 | 두 번째 하위 노드를 읽기 전용으로 추가한다 |
| Data Types | 변환 종류 아이콘, Name, Bit Length(`1:0` = 바이트:비트, `[17]` 배열, `[2..7]` 범위), Unit, Byte Order, Description, Conversion, Used(Yes/No). 템플릿 타입은 회색. New / Properties | 이름, Qualifier, 인코딩, 비트, 순서, 변환, 단위, 사용(“16개”) | 길이를 `바이트:비트`와 배열·범위 표기로 보이고, 변환 아이콘을 넣고, 템플릿 소유 타입을 회색으로 표시한다. 사용 수는 유지한다 |
| States | 장 소개와 하위 `State Groups`, `Dependencies`, `Defaults` | `상태 > 그룹 노드들`, `의존성` | 2.3절 |
| Libraries, Vehicle System Groups, Import Pool, Requirements | 있음 | 없음 | 범위 밖. 노드도 만들지 않는다 |

### 2.2 변형과 진단 클래스

| 노드 | CANdela | DiagAuthor | 변경 |
|---|---|---|---|
| Base Variant | 이름, 서식 있는 설명. 하위 `Identifying Features`, `DIDs`, `Supported Diagnostic Classes` | `◆ Base Variant` 아래 DID·DTC·클래스가 바로 온다 | 중간 노드 `지원 진단 클래스`를 두고 클래스를 그 아래로 옮긴다. `DTC (31)`은 변형 소속이므로 지금처럼 남긴다(앞선 결정 1) |
| Identifying Features | 표: Diagnostic instance, Data Object, Mandatory, Status, Path. New Feature, Change path | 원문 보존만 | 읽기 전용 표 |
| DIDs | **마스터·디테일**. 상단 DID 표: Identifier, Name, Diagnostic Class, Used in Fault Memory, DID Data. 하단 `Data of <DID>` 멤버 표: Byte No., Bit Pos., Name, Data Type, Default, Constant, Description. 오른쪽에 **바이트 × Bit7..Bit0 격자**(필드별 색). 도구줄: New DID, Delete DID, Select from Repository, 레이아웃 전환 3종 | DID 표(DID, 이름, Qualifier, 공유 변형 “4개”, 지원 “예”) + 텍스트 `데이터 구조` | 마스터·디테일로 바꾼다. 멤버 표와 바이트 격자는 인스턴스 페이지와 **같은 위젯**을 쓴다(2.4절). DID 표에 Diagnostic Class, Used in Fault Memory, DID Data 열을 추가한다 |
| Supported Diagnostic Classes | 템플릿 클래스 **체크 목록**. 비활성 클래스(Download, Memory)도 체크 해제 상태로 보인다 | 없음 | 읽기 전용 체크 목록(DCLTMPL 보존 원문 17개에서 유도). 편집은 하지 않는다 |
| 진단 클래스 | 설명, 인스턴스 표(Type/Identifier, Name, Services), Filter(Name), New / Delete / Repository | 식별자, 이름, Qualifier, 서비스, 공유 변형 | 거의 같다. 설명 행만 추가한다 |
| 인스턴스 트리 표기 | `01 Default Session`, `F190 VIN`, `0100 Braking`: 0x 없는 16진, 괄호 없음. 필수 인스턴스는 굵게. 식별자 없는 싱글턴(`Tester Present`)은 이름만 | `[01] Default Session`, `● [00] Tester Present`, 공유 기호 접두 | CANdela 표기로 바꾼다. 식별자가 서비스에서 유도된 값일 뿐인 싱글턴에는 `[00]`을 붙이지 않는다. 공유 구분은 텍스트 기호 대신 아이콘 5종으로 한다 |
| Variants | 변형 목록 표: Name, Identifying Pattern(`0.0.1, 0.0.2, 0.0.3`), Description. Variant Wizard | `변형 (3)` 노드 | 목록 페이지에 식별 패턴 열을 읽기 전용으로 넣는다 |
| 확장 변형 | 이름·설명, Delete Variant, Lock Variant. 하위 `Identifying Patterns`(패턴 표, New/New Range/Delete, 패턴별 상세: Instance·Data Object·Value·Data Type·Status·Path), `DIDs`, `Supported Diagnostic Classes` | `◇ A Sample 🔒` 아래가 Base와 같은 구조 | 식별 패턴 노드를 읽기 전용으로 추가하고, 중간 노드는 Base와 맞춘다 |
| Job 컨테이너 | Jobs 표(Name, File, Description)와 Job parameters(Input/Output 탭) | `🔒 Generic Jobs ⚠` → 보존 목록 | 표 모양만 맞춰 읽기 전용으로 보인다 |

### 2.3 상태

| 페이지 | CANdela | DiagAuthor | 변경 |
|---|---|---|---|
| State Groups | 마스터 표(Name, Semantic, Negative Response `0x7E Subfunction…`, Description)와 선택한 그룹의 상태 표("topmost state will be used as initial state") | 그룹마다 트리 노드 하나 | 마스터·디테일 한 페이지로 합친다. 트리의 그룹별 노드는 없애도 된다 |
| Dependencies | 모드 3개와 하단의 Variant·State group 콤보. ① 개요: `Sessions/*/Start`처럼 클래스별 서비스로 묶고 펼치면 인스턴스별 행이 나온다. 열은 Protocol Service, Prefix(`10 LL`), 상태마다 하나. 셀은 `(yes)`/`(no)`/`(mixed)`/도착 상태 이름. ② 편집: 인스턴스 서비스를 평면 목록으로 두고, 열에서 여러 행을 골라 F2로 일괄 변경. ③ 그래프(GraphViz, 미설치 시 안내문) | `의존성` 표(✓ ✗ →)와 별도 상태 그래프 대화상자(07, 자체 그림) | 개요 모드의 **클래스별 묶음과 펼침**, 편집 모드의 **다중 선택 일괄 변경**을 추가한다. 그래프는 지금 자체 그림을 유지한다(GraphViz 번들 안 함). 괄호 `(yes)`의 의미(템플릿 기본값 상속으로 추정)는 도움말로 확인한 뒤에 따른다 |
| Defaults | 새 서비스의 기본 전이: 템플릿 서비스별 Prefix와 상태 열, State group 콤보 | 없음 | 읽기 전용 표(보존 원문). 새 서비스 추가 시 적용은 후순위 |

### 2.4 진단 인스턴스 페이지 (신규, 핵심)

CANdela 화면 구성:

- **머리**: Name, Description, Identifier(`0xF190`)와 편집 버튼.
- **서비스 행**(템플릿이 정한 서비스 전부):
  - 체크박스: 회색 체크 = 필수, 체크 = 사용, 해제 = 미사용이고 행 전체가 회색. 예) IO Control의 Freeze·Return Control.
  - 역할 이름(Read/Write/Start/Set…).
  - Protocol Service 링크(`(0x2E) WriteDataByIdentifier`).
  - Request `2E F1 90 zz`, Pos `6E F1 90`, Neg `7F 2E rc`. 상수 서브함수는 기울임, 자리표시(zz/rc/yy/xx/sr/er/ww)는 해당 탭으로 가는 링크, `>>`는 NR 탭을 연다.
- **파라미터 탭**: 자리표시마다 하나씩. 예) `Data (zz)`(DID 공유 데이터면 링크 아이콘), `Start NR (rc)`, `Control Options (Start) (zz)`, `Status (Start) (yy)`, `Results (w)`. 탭 안에는 멤버 표(Byte No., Bit Pos., Name, Data Type, Default, Constant, Description)가 있다. 중첩 구조는 펼침 행으로 보인다(Wheel Speed의 `Direction of rotation` 아래 2비트 필드 4개). 오른쪽에는 바이트 격자가 있고, 한 바이트 안 비트 필드를 칸으로 나눈다.
- **NR 탭**: Response Code, Error text, Description.
- **하단 전환 버튼**: 전체 / 파라미터만 / 바이트 격자 표시.
- **Fault Memory 인스턴스**: 서비스 6개(0x19 하위 기능, 0x14)와 탭 `DtcStatusMask (zz)`(비트 0–7 사용 여부 체크, Request default), `Format Identifier (yy)`, `DTC Table (xx)`, `GroupOfDtc (ww)`. 여기에 버튼이 더 있어 **Snapshot 편집기**(레코드 표 Number/Name/Protocol/Storage Trigger/Update, 공통 DID, 오른쪽 속성 패널)와 **Extended Data 편집기**(레코드 표, 데이터 배치 표 + 격자, 속성 패널: Storage Trigger fdcThreshold/testFailed/pending/confirmed/custom)를 연다. AUTOSAR Events 편집기도 있지만 예제는 비어 있다.

DiagAuthor는 지금 인스턴스 노드를 누르면 서비스 표(SID, 이름, Qualifier, Subfunction, 공유 변형)가 나오고, 서비스를 고르면 텍스트 속성창에 요청·긍정 응답 배치가 줄글로 나온다.

변경:

- `instance_page` 위젯을 새로 만든다: 머리 폼, 서비스 행, 탭, 멤버 표, 바이트 격자, 전환 버튼.
  - 데이터는 이미 있다: `telegram_layouts`, `nrcs`, 상태 조건, 서비스 속성.
  - 자리표시 이름은 CDD 원문의 파라미터 그룹 이름을 쓴다. 없으면 `요청 데이터`/`응답 데이터`로 표시한다.
- 미사용 템플릿 서비스 행은 DCLTMPL 보존 원문에서 유도해 **회색·읽기 전용**으로 보여 준다. 체크로 켜는 편집은 템플릿 의미 매핑이 증명되기 전까지 막는다.
- Fault Memory 탭은 우선 읽기 전용이다: DtcStatusMask 비트 표, DTC Table(변형 DTC 목록), Snapshot·Extended 레코드 표. 지금 `snapshot_text`와 `extended_text`로 텍스트만 보이는 데이터다. 편집은 DTCSTATUSMASK 의미 검토(high) 뒤로 미룬다.
- 멤버 편집은 지금의 구조 대화상자를 연결한다. 표 안에서 바로 편집하는 것은 U6.

## 3. 공통 UI

| 기능 | CANdela | DiagAuthor | 변경 | 우선 |
|---|---|---|---|---|
| 뒤로/앞으로 | 리본 Navigation Back/Forward | 없음 | 노드·객체 선택 이력 스택. Alt+←/→ | P1 |
| 경로 | `npath:/C Sample/Dynamic Data/Wheel Speed/Data`. 콤보 입력으로 이동 | `diag://UUID…` | 경로 창에는 **이름 경로**를 보이고, 입력도 이름 경로로 받는다. 복사는 둘 다 제공한다(이름 경로 / 안정 UUID 경로). 이름이 겹치면 후보 목록을 보인다 | P1 |
| 찾기 | Find 대화상자. 범위: Names / Qualifier / 이름·Qualifier·설명·식별자·DTC·Service shortcut·Response code / **Service Request("22 10 03")**. 대소문자, 단어 단위, 전체 일치. Find all 결과는 출력 창에 npath 링크로 남는다 | 표 검색창(현재 목록만) | 문서 전체 찾기. 요청 바이트 검색을 넣고, 결과 목록을 두 번 누르면 이동한다 | P1 |
| 공유 객체 편집 | "DID referenced in multiple variants" 대화상자: 모든 변형 수정 / 이 변형에서 사본으로 교체 후 수정 | 참조 영향 표시(09)만 | 편집 확정 전에 같은 두 선택지를 준다. 사본 선택 시 변형 소속을 옮기는 API가 필요하다 | P1 · **high 설계** |
| 참조 | References 대화상자(참조 변형 목록 + 링크 + Count) | 참조 영향 대화상자(직접·간접, 이유, 영향 변형) | 지금이 더 자세하다. 리본에 해당하는 도구 버튼과 컨텍스트 메뉴만 추가한다 | P2 |
| 컨텍스트 메뉴 | 변형: Copy/Paste/Delete, Lock/Unlock, Export ODX, Attribute Overview, Target Group Overview, Compact view, Revision History, Properties. DTC 표: New/Cut/Copy/Paste/Delete/References/Properties | 일부 | 트리·표 모두 같은 명령 집합으로 맞춘다(기존 명령 재사용) | P2 |
| Shortcut Qualifier 개요 | 먼저 변형 부분집합을 고른다. 행 = shortcut, 열 = 변형 yes/no, Path 링크. "모든 변형에 보이는 행 숨기기", 표 복사 | 변형마다 행(209행) | 행렬로 바꾸고 변형 선택·공통 숨기기·복사를 추가한다 | P2 |
| Compact View | 변형 하나를 골라 Base와 **공유되지 않는** 인스턴스만 트리로 보인다 | 없음 | `share` 데이터로 바로 만들 수 있다 | P2 |
| 출력 창 | Type·Timestamp·Origin·Message 열, 종류 토글(Error/Warning/Info), 텍스트 필터, 오른쪽 상세 창. Check Consistency는 "Checking service prefixes in … OK / Document is consistent" | 검증·작업 결과 패널(심각도, 규칙, 경로, 내용, 수정 안내, 출력 차단, revision) | 검증 패널은 지금 쪽이 더 낫다. 작업 기록 탭에 종류·시각 열과 필터만 추가한다. **U1 결함**: 검증 전 표 머리가 DID 열로 나옴 | P1(결함) / P3 |
| Rework 목록 | Done 체크, Link, Message(할 일) | 없음 | 후순위 | P3 |
| Revision History | Authors / History 표(Version, Author, Date, Status, Modification, Reason). 예제에 14건 | revision 번호만 | 보존 원문을 읽기 전용 표로 보인다 | P3 |
| 속성 대화상자 | 탭 구성(General / 변환 종류 / Invalid Values / Authorization / Attributes). Linear 탭: 길이 Byte:Bit, Bitmask, Byte order, Encoding raw/phys, 하한·상한 raw/phys, `phys = Factor / Divisor × data + Offset`, Unit, Inverse, Display format, Precision. Edit DTCs는 **Navigation 콤보 + Up/Down**으로 다음 항목으로 넘어간다 | 모달 대화상자, 필드 나열 | 탭 구성과 앞뒤 이동 버튼을 넣는다. Divisor는 원본에 있을 때만 그 형식으로 표시한다 | P2 |
| 사용 중 타입 보호 | "Data type is already in use. Properties affecting the length cannot be modified." | 확인 필요 | 사용 수 > 0이면 길이 필드를 잠그고 이유를 보인다(없으면 추가) | P2 |
| 새 항목 | 모든 표 끝에 "Please click here to create a new element" 행 | 추가 버튼 → 대화상자 | 표 끝 새 항목 행(대화상자를 여는 방식으로 시작) | P3 |
| 장 소개·Previous/Next | 장 노드마다 설명 문장과 도움말 링크. 모든 페이지 하단에 트리 순서 이동 | 없음 | 장 노드 소개 문장만 넣는다. Previous/Next는 넣지 않는다 | P3 |
| 성능 | 페이지 전환마다 5–15초 걸리고 흰 화면으로 기다린다 | 작업 스레드로 비동기 | 유지. 인스턴스 페이지도 작업 스레드 한 번 호출로 받는다 | — |

## 4. 구현 단계

| 단계 | 내용 | 스키마 | 작업 강도 |
|---|---|---|---|
| U1 | 결함 2건(속성창 갱신, 검증 표 머리). 인스턴스 표기(`01 Default Session`, 싱글턴 `[00]` 제거). 이름 경로 표시·입력. 뒤로/앞으로. 문서 전체 찾기(요청 바이트 포함). DID 표 열 추가 | 없음 | low |
| U2 | **인스턴스 페이지**와 공용 멤버 표·바이트 격자 위젯. DID 마스터·디테일. Fault Memory 읽기 전용 탭 | 없음(기존 layout 사용) | 설계 high(자리표시·탭 이름 규칙) → 구현 low |
| U3 | 공유 편집 선택 대화상자(모든 변형 / 이 변형 사본) | 소유 변형 이동 API. 스키마 변경은 설계에서 판단 | **설계 high** → 구현 low |
| U4 | Shortcut 행렬, Compact View, 의존성 개요 묶음·일괄 편집, State Groups 마스터·디테일, 트리 중간 노드(지원 진단 클래스, States 하위, NRC 하위) | 없음 | low |
| U5 | 읽기 전용 표시: ECU 인터페이스·통신 파라미터, Identifying Features/Patterns, 템플릿 클래스·미사용 서비스, Job 표, Defaults, 프로토콜 서비스 패턴 열, Revision History | 없음(보존 원문 파싱, 출력 의미 없음) | low. 원문 해석 규칙은 예제로 확인 |
| U6 | 속성 대화상자 탭·앞뒤 이동, 사용 중 타입 보호, 표 끝 새 항목 행, 장 소개, 작업 기록 필터, Rework | — | 필요할 때 |

**U1과 U2를 먼저 하기를 권한다.** U2가 CANdela와 가장 크게 달라 보이는 부분이고, 새 데이터 없이 할 수 있다. U3은 공유 의미를 바꾸므로 설계 검토를 먼저 한다.

## 5. 범위 밖

AUTOSAR Events·DEXT·SWC Sync, Libraries, Vehicle System Groups, Import Pool, Requirements, Expert View(템플릿 편집), Translation View, Target Group, Custom Actions, GraphViz 번들.

## 6. 결정할 것

1. **싱글턴 인스턴스 식별자**: `Tester Present`에 `[00]`을 붙이지 않는다(CANdela와 같게). 서비스에서 유도한 값은 툴팁에만 둔다.
2. **경로 기본 표시**: 이름 경로를 기본으로 보이고, UUID 경로는 "안정 경로 복사"로 따로 둔다.
3. **DTC 표시 형식**: 기본은 0x 16진이고 SAE 문자형은 옵션이다. 또는 CANdela처럼 SAE 문자형을 기본으로 한다.
4. **트리 상태 그룹 노드**: 없애고 State Groups 한 페이지로 합칠지 정한다.

## 7. 진행 현황 (2026-09-26, 22차)

U2~U6과 2.1절 표 변경을 구현했다. Windows 테스트 1차에서 236개 중 2건이 실패해 고쳤고, 재실행과 빌드·GUI 스모크는 아직이다. 세부 내용과 한계는 [BUILD-NOTES 22차](../BUILD-NOTES.md)에 있다.

| 단계 | 상태 | 반영한 것 | 남은 것 |
|---|---|---|---|
| U1 | 이전 작업에서 반영 | — | — |
| U2 | 구현, 스모크 전 | 공용 데이터 배치 위젯(멤버 표 + 바이트 격자 + 전환 버튼), 인스턴스 페이지(머리, 서비스 행, 미사용·보존 원문 템플릿 행), DID 마스터·디테일, 고장 메모리 읽기 전용 탭 | 자리표시 링크, 표 안 멤버 편집, 고장 메모리 편집 |
| U3 | 구현, 스모크 전 | 공유 편집 선택 대화상자(기본 = 이 변형 사본), 인스턴스 사본 명령 `detach_instance`(스키마 변경 없음) | 설계 검토 |
| U4 | 구현, 스모크 전 | 트리 중간 노드, 상태 그룹 마스터·디테일, Shortcut 행렬, Compact View, 의존성 클래스별 묶음·일괄 변경(`bulk_conditions`) | 그래프 모드는 기존 상태 그래프 유지 |
| U5 | 구현, 스모크 전 | 인터페이스·통신 파라미터, 식별 특징·패턴, 지원 진단 클래스, Job, Defaults, 미지원 서비스 NRC, 프로토콜 서비스 패턴 열, Revision History | Defaults를 새 서비스에 적용, ABS_ESP 외 예제로 해석 확인 |
| U6 | 구현, 스모크 전 | 속성 대화상자 탭·이전/다음, 사용 중 타입 잠금, 표 끝 새 항목 행, 장 소개, 작업 기록 필터, Rework | 명령 수준 타입 보호 |
| 2.1 | 구현, 스모크 전 | ECU 페이지, DTC 속성 열·SAE 표시 옵션, DID 개요 열, 데이터 타입 길이·아이콘·템플릿 회색 | — |

6절 결정은 이렇게 정했다.

- 1(싱글턴 `[00]` 없음)·2(이름 경로 기본): U1에서 이미 반영.
- 3(DTC 표시): 0x 16진이 기본이고 SAE 문자형은 옵션이다(표시 전용, 원시값은 툴팁).
- 4(트리 상태 그룹 노드): 유지하고 `상태 그룹` 중간 노드 아래에 둔다. 한 페이지 마스터·디테일도 함께 둔다.

의존성의 괄호 표기 `(yes)`/`(no)`는 의미를 확인하지 못해 따르지 않았다. 묶음 행은 ✓/✗/(혼합)만 보인다.
