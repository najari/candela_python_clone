# 0.1.0 개발 빌드 안내

이 빌드는 전체 24주 계획의 출시 완료본이 아닙니다. 작성·저장·편집 기능을 연결한 개발 빌드입니다. 초기 빌드 이후 자동 테스트와 GUI 스모크를 실행했습니다. 실행 결과는 `build/checks/`와 `build/build-result.json`에 기록합니다.

빌드 방식: `pyside6-deploy` / Nuitka는 Python 단계 컴파일을 완료했으나 현재 설치된 Visual Studio에 Windows SDK가 없어 C 컴파일 단계에서 중단되었습니다. 이번 배포에는 PyInstaller 6.22.0을 사용합니다. 두 빌드 설정을 소스에 남겼고, 기본 `build.ps1`은 PyInstaller를 사용합니다. 앱 코드의 기술 스택은 변경하지 않았습니다.

## 30차(Claude): Vector 예제 19개의 보존 항목 해석 — 열거 예외값·중첩 packet·부동소수·앞 길이 바이트·구간별 선형·특성 곡선·MUX 출력, 해결된 항목 목록, 스키마 13 (2026-09-27) — **클라우드 테스트 통과(294개, 56개 건너뜀: PySide6 없음·예제 경로), Windows 확인 전**

- **요청**: `examples\vector_example`의 19개 파일(CDD 16·CDDT 3)에서 해결할 수 있는 보존 항목을 모두 해결하고, UI에 해결된 보존 항목을 보이게 한다.
- **결과**: 19개 파일의 출력 차단 보존 항목 **203 → 102**. 파일별: ABS 1, Multilingual 1, SampleUDS 3, 4.0.2 3, CANSystemDoor 4, CD-Changer 4, UDSonLIN 4, CANSystem 5, 4.0.4 5, 7.0.1 5, Test 6, KWPonCAN 6, kwp2000-bed 6, kwp2000-seat 6, Door 5.1.0 7, UDSonCAN 8, Door 5.0.4 9, J1939 9, UDS 13.0 템플릿 10. 모든 파일이 가져오기·검증·외래 키 검사를 통과한다.
- **근거**(같은 문서 쌍 Door CDD 5.0.4 ↔ Vector 출력 UDS-ExampleEcu-5.0.2.pdx, 파일은 저장소에 복사하지 않음):
  - 열거 테이블 예외값: DTC_Significance(EXCL 2..255 noSignal "Reserved") → TEXTTABLE + SCALE-CONSTR NOT-AVAILABLE, TestDatatypeMuxValues → NOT-AVAILABLE 5..6·NOT-DEFINED 16..255·빈 구간 10..15.
  - 중첩 packet: TestDatatypeStructureLevel2·3 → STRUCTURE 안의 STRUCTURE.
  - 부동소수: VehicleSpeed(enc=flt 32, f=1 div=10, s=-300 e=3000) → A_FLOAT32(BASE-TYPE-ENCODING 없음), LINEAR, NOT-DEFINED -3.402823e+038..-301·3001..3.402823e+038. Vector의 물리 타입은 A_INT32(PVALUETYPE sgn)이지만 DiagAuthor는 기존 선형 규칙대로 A_FLOAT64를 낸다(기존 차이, 아래 남은 것).
  - 앞 길이 바이트: Memory_1~4_Byte_Size_Info(sz=lszbyte~lsz4bytes) → A_BYTEFIELD LEADING-LENGTH-INFO-TYPE BIT-LENGTH 8/16/24/32. ASCII 문자열(UDS 13.0 템플릿)·0 종료 문자열(J1939)은 Vector 출력이 없어 보존.
  - 구간별 선형: TestDatatypePiecewiseLinear(COMP 8개) → SCALE-LINEAR, 구간마다 COMPU-SCALE. 특성 곡선: TestDatatypeCharacteristicCurve(VALMAP 11점) → TAB-INTP, 점마다 LOWER-LIMIT와 COMPU-CONST/V, PRECISION 1.
  - DID MUX: DID_MuxedTestData(Selector + MUXDT TestDatatype) → DID STRUCTURE에 Selector VALUE(0)와 MUX 참조 VALUE(0), MUX BYTE-POSITION 1·SWITCH-KEY BYTE-POSITION 0·선택자 DOP, 빈 기본 구조 → DEFAULT-CASE(SHORT-NAME만), CASE Case_0x.. LOWER/UPPER-LIMIT = 선택자 물리값.
  - 서비스 MUX: Download_Request(MUXDT Memory_Address_and_Size, 선택자 ALFID) → TABLE(KEY-DOP = 선택자 DOP, 선택자 값마다 TABLE-ROW TR_0x.. KEY = 물리값, STRUCTURE-REF), 요청 PARAM TABLE-KEY + TABLE-STRUCT. MUXDT의 기본 STRUCTURE는 Vector TABLE에 없다 → 내지 않고 **WRN-002**(비차단)로 알린다.
  - JOBFILES: Door의 Job 코드 파일은 Job이 하나도 참조하지 않고 Vector 출력에 없다 → 참조하는 Job이 없으면 출력 영향 없음.
  - PROTOCOLSTANDARD "KWP": UDS와 같은 출처 정보(한 단어 원문) → ECU 속성 `cdd.protocol_standard`.
- **스키마 13**(`migrations.py` 12 → 13, 픽스처 `schema_v12.sql`): `data_types.encoding`에 `float`(32·64비트, 동일값·선형), `conversion`에 `scale_linear`·`tab_intp`, 열 `leading_length_bits`(8/16/24/32, 가변 bytes만), 표 `conversion_segments`(구간: 하한·상한·계수·오프셋, 점: 원시값·물리값). CHECK 변경이라 data_types를 다시 만든다.
- **모델**(`domain`): `check_type`·`check_segments`(구간 겹침·점 오름차순), `convert`(부동소수 10진 원시값, 구간별 선형 정·역변환, 특성 곡선 선형 보간·역변환 거부), 전문 인코딩(IEEE 754, 앞 길이 바이트는 전문 가운데에도 둘 수 있음). 가져오기(`interchange`)는 위 요소를 새 모델로 읽고, 사용처(DID·서비스)가 있는 MUXDT는 "해석함"으로 바꾼다.
- **해결된 보존 항목 UI**: 보존 항목 목록에 `해결된 항목 보이기`(기본 켬). 켜면 출력 차단 항목과 함께 ① 예전에 보존하던 요소를 가져온 객체(구분 **해결됨(가져옴)**, 사유 = 해결 차수·근거, 행을 열면 그 객체) ② 근거를 찾아 해석했거나 출력 영향이 없다고 판정한 원문(구분 해석됨·출력 영향 없음)이 보인다. 가져온 객체에는 속성 `import.resolved`(해결 내역, ODX에는 나가지 않음)가 붙어 속성 창에도 보인다. 트리 글자 `보존 항목 (전체 · 차단 n · 해결 m)`.
- **UI**: 인코딩 `float`, 변환 `구간별 선형`·`특성 곡선`(아이콘 ⟋·∿, 구간·점은 CDD에서 온 것을 유지), `앞 길이 바이트` 선택(사용 중 타입은 잠금).
- **새 테스트** `test/test_preserved_types_30.py`(12개): 부동소수·구간별 선형·특성 곡선·앞 길이 바이트 규칙과 변환, 새 프로젝트 전문(앞 길이 바이트 + float + 뒤 필드) 인코딩과 ODX, 12 → 13 마이그레이션, Door ↔ Vector 대조(타입, SCALE-LINEAR·TAB-INTP·TEXTTABLE 예외값, DID MUX, 서비스 TABLE, 보존 분류, 해결된 항목 목록), KWP 프로토콜 표준. 기존 기대값(마이그레이션 목록, 규칙 집합에 WRN-002, Door 여러 구간 COMPTBL)을 고쳤다.
- **남은 것(근거 없음·큰 기능)**: 고장 메모리 레코드 ODX(ENV-DATA-DESC: Door DOMAINDATAPROXYCOMP 서비스 2·ECU FAULTMEMORY 1, 그리고 모든 프로젝트의 스냅샷·확장 데이터 서비스 출력 차단 — Door PDX에 근거가 있어 다음 대상), 반복 항목 안의 MUX(DDDI by address 18, Vector 출력 없음), MUX 케이스 끝의 가변 데이터(WMBA 3, 5.0.2 PDX에 없음), ASCII가 아닌 변형 식별 값 10, 예전 KWP 문서의 DTC 레코드·그룹·NUMITERCOMP·UNSUPPSRVNEG·POS 없는 서비스(27), 쓰지 않는 MUXDT 16(출력 범위 밖), 부분 비트 마스크 1, Job이 든 컨테이너·Job 파일 2, ASCII·0 종료 길이 문자열 2. 선형 타입 물리 타입(PVALUETYPE에 따른 A_INT32/A_UINT32 등)은 Vector와 다르다(기존 차이).
- **검증 범위**: 클라우드 Linux(PySide6 없음) 294개, 오류는 PySide6가 없어 `test_ui_editing`을 읽지 못한 것 하나. UI 변경(보존 목록 체크 상자·트리 글자·대화상자 인코딩/변환/앞 길이)은 실행해 보지 못했다. Windows `build\run_checks.cmd`가 필요하다.

## 29차(Claude): 남은 차단 보존 항목 12개 해석 — 구간 하나 COMPTBL, 수식 타입(스키마 12), bo=12 packet, Event 데이터, 빈 Job 컨테이너, 템플릿 참조 범위 (2026-09-27) — **Windows 통과(20:38~20:57, 테스트 305·빌드·GUI 스모크)**

- **요청**: 남은 보존 항목 12개의 해결책을 찾고(근거 조사), 모두 구현한다.
- **근거 조사**(파일은 저장소에 복사하지 않음): CANoe 13 예제 `CAN/Diagnostics`의 Door CDD(`UDSBasic/Cdd/UDS-ExampleEcu-5.0.4.cdd`, `UDSSystem/CDD/UDS-ExampleEcu-5.1.0.cdd`) ↔ Vector 출력 `UDSSystem/PDX/UDS-ExampleEcu-5.0.2.pdx`, CANdela 13 예제 `Vector_KWPonCAN_1.3.cddt` ↔ `Import/UDS-ExampleEcu.pdx`, 도움말. 판 차이(5.0.2 ↔ 5.0.4: 5.0.4에 RMBA·WMBA·Authentication 서비스가 더 있음)는 아래 대조 요소에 영향이 없었다.
  - COMPTBL(구간별 선형): 구간 하나 KWP OdometerValue(s=0 e=65534 f=16, EXCL 65535 noSignal) → DOP LINEAR, COMPU-SCALE 0..65534, V0=0 V1=16, A_FLOAT64 PRECISION 0, SCALE-CONSTR NOT-AVAILABLE 65535. 구간 8개 Door TestDatatypePiecewiseLinear → SCALE-LINEAR(구간마다 COMPU-SCALE·계수), EXCL undef → NOT-DEFINED.
  - FORMULADT: KWP EngineTemperature(uns 8비트, coded2Phys `270+510*(1-pow(e,-0.0255*x))`, 단위 K) → DOP IDENTICAL, DESC `Generated from formula data type 'EngineTemperature'.` / `CodedToPhys formula: f(x)=…; ` / `PhysToCodedFormula: f(x)=`, 부호화·물리 A_UINT32, UNIT-REF 없음.
  - STRUCTDT: Door TestDatatypeStructureLevel1(bo=12, 6바이트, 멤버 HexDump_2Byte bo=21) → STRUCTURE BYTE-SIZE 6, BYTE-POSITION 0/2/4, 멤버 DOP는 기본(high-low) 순서. KWP EnvironmentData(bo=12, 4바이트) → STRUCTURE BYTE-SIZE 4.
  - MUXDT: Door Memory_Address_and_Size는 인스턴스가 쓰는 Download_Request(RD)에서 TABLE + TABLE-KEY(ALFID) + TABLE-STRUCT(`<?GeneratedFromMux yes?>`)로 나간다. 인스턴스가 없는 ResponseOnEvent·LinkControl 프로토콜 템플릿은 DIAG-SERVICE가 없고 그 타입은 참조 없는 DOP로만 남는다(Vector는 쓰지 않는 단순 타입도 DOP로 낸다 — 참조 없는 DOP 75개. DiagAuthor는 여전히 쓰지 않는 타입을 내지 않는다).
  - Job: Door의 Flash_Jobs·Generic_Jobs는 ABS와 같이 Job이 없는 빈 컨테이너이고 Vector 출력에 SINGLE-ECU-JOB이 없다. 도움말 90_ODXExport_Jobs는 Job마다 SINGLE-ECU-JOB을 만든다.
  - EVENTDATA: 도움말 1_ODXExport_Introduction [ODXE-D115] "ODX export does not include Events". ABS EVENTDATA는 Event 기준 데이터(표시등 3·운행 주기 4·디바운스 4·조건 2)뿐이고 다른 곳에서 참조하지 않는다.
- **가져오기**(`interchange.parse_type`·`cdd_formula`, `cdd_model`): 구간 하나 COMPTBL → 선형(LINCOMP와 같은 규칙, 여러 구간은 "SCALE-LINEAR 미지원"으로 보존). FORMULADT → 변환 `formula`(coded2Phys 하나만, 정수 타입만, 역수식이 있으면 보존). STRUCTDT의 bo=12 제한을 없앴다. 빈 JOBCNR과 EVENTDATA는 "출력 영향 없음"(비차단)으로 원문 보존. Job이 든 컨테이너는 전처럼 차단.
- **출력 범위**(`export_scope.unused_source_types`): PROTOCOLSERVICE와 DCLTMPL(서비스 템플릿·공유 구성요소 포함) 안의 참조는 그 템플릿이 다른 곳(인스턴스·클래스)에서 쓰일 때만 사용으로 본다. 그 밖의 원문 참조는 전처럼 사용.
- **스키마 12**(`migrations.py` 11 → 12, 픽스처 `schema_v11.sql`): `data_types.conversion`에 `formula`, 열 `formula_expression`(formula일 때만, 정수 타입만). CHECK 변경이라 표를 다시 만들고 기존 행을 옮긴다.
- **모델·출력**: `check_type`이 수식 규칙을 검사하고, `convert`는 수식 타입의 물리값 입력을 거부한다(원시값 = 표시값, ODX IDENTICAL과 같음). `build_odx`는 수식 타입을 IDENTICAL + DESC(Vector 문구) + 부호화 타입과 같은 물리 타입(uns A_UINT32, sgn A_INT32 — sgn은 도움말 표 32 근거)으로 내고 단위를 내지 않는다. 검증 규칙 **WRN-001**(warning, 비차단, `docs/11`)이 수식 타입마다 물리값·단위가 ODX에 없음을 알린다.
- **UI**: 변환 이름 `수식(CDD)`·아이콘 `ƒ`, 속성 대화상자 변환 목록에 `수식 (CDD, ODX 동일값)`과 수식 입력 칸(비우고 저장하면 기존 수식 유지), 미리보기는 "계산하지 않음" 안내, 구조 표·배치 요약에 `수식(ODX 동일값)`. 비교 보기가 수식을 비교한다. 원본 변환식 표시는 COMPTBL도 보인다.
- **결과(ABS 새로 가져오기)**: 출력 차단 보존 항목 12 → **1**(MUXDT, Base Variant 출력 범위 밖). ABS의 COMPTBL·FORMULADT·packet 8개는 데이터 타입으로 들어온다(쓰는 DID·서비스는 없음). Base Variant 출력을 막는 것은 보존 항목이 아닌 기존 사유(실행 가능 상태 제한 서비스, 스냅샷·확장 데이터 서비스)뿐이다. 29차 이전에 가져온 프로젝트는 다시 가져와야 새 분류가 적용된다.
- **새 테스트** `test/test_preserved_types.py`(10개): 수식 규칙·물리값 거부, 수식 DOP의 요소 순서·DESC 문구·IDENTICAL·A_UINT32/A_INT32·단위 없음·WRN-001 비차단, 편집 때 수식 유지, 11 → 12 마이그레이션(기존 선형 타입 보존, 새 열·CHECK), 템플릿 참조 범위(최소 문서), ABS 가져오기 값(OdometerValue 계수·범위·예외, 수식 3개, packet 2개, 남은 차단 MUXDT), Door(여러 바이트 bo=12 packet, 여러 구간 COMPTBL 보존, 빈 Job, 쓰이는 MUX는 범위 안). 기존 테스트의 기대값(마이그레이션 목록, EVENTDATA 분류, 차단 12 → 1, 범위 밖 타입 8 → MUXDT, 검증 규칙에 WRN-001)을 고쳤다.
- **검증 범위**: 클라우드 Linux(PySide6 없음)에서 282개 실행, 오류는 PySide6가 없어 `test_ui_editing`을 읽지 못한 것 하나뿐. UI 변경(대화상자 수식 칸·아이콘)은 실행해 보지 못했다. Windows `build\run_checks.cmd`가 필요하다.
- **Windows 1차**(20:31, `run_checks.cmd`): 테스트 305개 중 16개 실패 — 모두 기대값이 29차 변경을 반영하지 않은 것. `test_cdd_import`(Vector 공용 문서 전체 가져오기) 15개는 검증 규칙 집합에 WRN-001이 없었고(수식 타입이 이제 들어옴), `test_ui_tree` 1개는 Fault Memory·빈 Job 컨테이너의 ⚠를 기대했다(이제 비차단). 두 테스트의 기대값을 고쳤다. **2차**(20:38~20:57): 테스트 305개 통과, 빌드 성공, GUI 스모크 통과(`gui_exit=0`, 사전 검사 요약 `출력 차단 15개 · 범위 밖 7개`, 저장은 전처럼 대체 경로).
- **남은 것**: 여러 구간 COMPTBL(SCALE-LINEAR, 구간 저장 필요), VALTBL(TAB-INTP), MUX 출력(서비스 안 TABLE-KEY/TABLE-STRUCT, DID 안 MUX).

## 28차(Claude): 보존 항목 해석 — 변형 식별 패턴·미지원 서비스 부정 응답·DTC 레코드 풀·상태 마스크, 보존 목록 정리, 스키마 11 (2026-09-27) — **클라우드 테스트 통과(295개, 6개 건너뜀), Windows 확인 전**

- **요청**: 보존 항목 해결 방법(권장 순서 1~3)을 진행한다.
- **근거 조사**(파일은 저장소에 복사하지 않음): CANdela 13 예제의 `Repository/ODXEnvelope`는 DID·DTC 단위 파일뿐이라 문서 수준 정의의 근거가 없었다. 대신 CANoe 13 예제 `CAN/Diagnostics`에 같은 문서(Door ECU)의 CDD(`UDSBasic/Cdd/UDS-ExampleEcu-5.0.4.cdd`, `UDSSystem/CDD/UDS-ExampleEcu-5.1.0.cdd`)와 그것을 CANdelaStudio(ODXExport201 8.5.3104)가 출력한 `UDSSystem/PDX/UDS-ExampleEcu-5.0.2.pdx`(ODX 2.0.1)가 있어 CDD → Vector ODX 쌍으로 대조했다. 문서 판(5.0.2 ↔ 5.0.4)이 조금 다르지만 아래 대조 항목은 같았다.
  - 변형 식별: CDD `IDENTIFICATIONSET`(Part_Number 바이트 목록 = "Developer ECU", 특징 경로 `…/EcuIdentification/[SERVICE]Read`) → ODX `ECU-VARIANT-PATTERN/MATCHING-PARAMETER`(EXPECTED-VALUE `Developer ECU`, DIAG-COMM-SNREF `EcuIdentification_Read`, OUT-PARAM-IF-SNREF `Part_Number`).
  - 미지원 서비스 부정 응답: CDD `UNSUPPSRVNEG`(CONSTCOMP 7F, STATICCOMP spec=id, NRC 목록) → ODX `GLOBAL-NEG-RESPONSE UNSUPPORTED_SERVICE_NR`(SID_NR CODED-CONST 127 SEMANTIC=SERVICE-ID, SID_RQ_NR MATCHING-REQUEST-PARAM, NRC VALUE + TEXTTABLE DOP, 빈 구간 NOT-DEFINED).
  - DTC 상태 마스크: Door CDD의 `DTCSTATUSMASK`는 8비트 모두 `optno`인데 Vector 출력의 DtcStatusbyte는 인스턴스 구조(DATAOBJ = VALUE, GAPDATAOBJ = RESERVED: 0·3·4·5·6 사용)를 그대로 따른다 → 마스크는 ODX에 나가지 않는다.
  - DTC 레코드 풀: ABS의 Vector 출력(ODXEnvelope DTC 파일)은 DTC-DOP를 24비트·HEX·IDENTICAL로 낸다 = 풀의 faultMemory 레코드 타입(24비트 부호 없는 16진)과 같고, 풀의 RECORD는 모두 DTC로 가져온다.
  - EVENTDATA: Door CDD에 없어 근거가 없다 → **차단 유지**. Job 컨테이너: Door CDD에 2개 있지만 Vector 출력에 SINGLE-ECU-JOB이 없다(Vector가 내지 않음). 출력 여부는 결정이 필요해 차단 유지.
- **스키마 11**(`migrations.py` 10 → 11, 픽스처 `schema_v10.sql`): 표 `variant_patterns`(변형·패턴·순번·서비스·응답 데이터 객체 Qualifier·기대값, 서비스 삭제는 RESTRICT)와 `unsupported_service_nrcs`(NRC·순서). 이전 파일은 빈 표로 올라가며 **기존 프로젝트의 보존 항목 분류는 바뀌지 않는다**(원문에서 다시 해석하지 않음). 새 분류를 쓰려면 CDD를 다시 가져온다.
- **가져오기**(`interchange.cdd_preview`, `application.apply_cdd`): 위 다섯 요소(IDENTIFICATIONSET·IDENTIFICATIONFEATURESET·UNSUPPSRVNEG·DTCSTATUSMASK·RECORDDTPOOL)를 Vector 출력과 같은 모양일 때만 해석하고 비차단으로 보존한다(원문 그대로 남김). 모양이 다르면 이유를 남기고 차단한다: 식별 값이 ASCII가 아님·식별 서비스를 못 찾음·가져오지 못함, 부정 응답 구성요소가 7F·SID·NRC 순서가 아님, 레코드 풀이 24비트가 아니거나 가져오지 않은 DTC·OBD 레코드가 있음, 상태 마스크·특징에 확장 속성.
- **ODX 출력**(`interchange.build_odx`, `export_scope.variant_pattern_params`): 변형 층 끝에 `ECU-VARIANT-PATTERNS`(DIAG-COMM-SNREF = 이 변형의 같은 Qualifier 활성 서비스, OUT-PARAM-IF-SNREF = 긍정 응답의 해당 PARAM `P_n`), `NEG-RESPONSES` 뒤 `GLOBAL-NEG-RESPONSES`와 사전의 `NRC_DOP`. 서비스·파라미터를 풀 수 없거나 ASCII 타입이 아니면 출력 사전 검사에 VAL-012(`patterns`)로 나오고 출력이 막힌다.
- **보존 항목 목록**: `구분` 열(출력 차단 · 해석됨(원문 참조용) · 출력 영향 없음 · 메타데이터·출처 정보), 처음에는 출력 차단 항목만 보이고 `출력 영향 없는 원문도 보이기`로 전부 본다(숨긴 수는 목록 머리에). 트리 글자 `보존 항목 (전체 · 차단 n)`. 인스턴스 단위로 이미 해석한 템플릿 정의는 "해석됨: 템플릿·정의"로 보인다.
- **결과(ABS 새로 가져오기)**: 출력 차단 보존 항목 19 → **12**(남은 것: 복합·가변 데이터 타입 7, packet 바이트 순서 2, EVENTDATA 1, Job 컨테이너 2). 변형 A·B·C Sample에 식별 패턴 7개, 미지원 서비스 NRC 0x11·0x12. Base Variant 출력을 막는 것은 여전히 실행 가능 상태 제한 서비스 11개·스냅샷/확장 데이터 서비스 2개·Job·범위 안 타입 1개·EVENTDATA다.
- **새 테스트** `test/test_document_definitions.py`(9개): 해석 규칙(ASCII·서비스 해석·모양 검사·거부 사례), ODX 출력(패턴·전역 부정 응답의 요소·순서·NRC DOP 빈 구간), 풀 수 없는 패턴의 출력 차단, 패턴이 쓰는 서비스는 삭제 사전 검사에서 막힘, ABS 가져오기 결과·보존 목록 필터, Door CDD 가져오기 결과가 Vector 출력 값과 같음(CANoe 예제가 있을 때). 마이그레이션 테스트 기대 버전 목록에 11을 더하고 10 → 11 테스트를 추가했다.
- **발견(고치지 않음)**: 기존 출력의 CODED-CONST는 `DIAG-CODED-TYPE` 다음에 `CODED-VALUE`를 쓴다. Vector 출력(2.0.1)은 `CODED-VALUE`가 먼저다. 새 GLOBAL-NEG-RESPONSE는 Vector 순서를 따랐다. 정식 ODX 2.2.0 XSD로 검사해 기존 순서가 맞는지 확인해야 한다.
- **남은 것**: Windows `build\run_checks.cmd`. 스냅샷 레코드(ENV-DATA-DESC)는 Door 쌍에 Vector 출력이 있어(`SnapshotRecord`, ENV-DATA ALL-VALUE) 다음 대조 대상이다. 복합 타입·EVENTDATA·Job은 근거 확보 또는 결정이 필요하다.

## 27차(Claude): P3 새 기능 자동 테스트와 M6-3·M6-4·M6-6 검토 (2026-09-27) — **Windows 통과(18:12~18:18, 테스트 285·빌드·GUI 스모크)**

- **요청**: 26차 뒤 남은 일 중 ① P3 새 기능 자동 테스트, ② 데이터 타입 바꾸기·변형 마법사·비교 보기 검토를 함께 진행한다.
- **새 테스트** `test/test_p3_features.py`(18개): 값 형식(형식별 목록, 16진·2진·BCD 니블, 2의 보수, 접두어 규칙, 거부), 데이터 타입 바꾸기(사용처·길이 영향·뒤 필드 이동, 범위 밖·값 종류·글자 길이·비트필드 충돌, 한 명령 적용·실행 취소·충돌 행 포함 시 전체 거부), ABS 예제로 변형 마법사(자료·참조로·사본으로·빈 변형 개수, 필수 인스턴스·사본 불가 규칙, 인스턴스별 동작, [VW-R510] 설명 복사, 실행 취소), 비교 보기(두 변형, 저장된 파일과 비교해 바뀐 이름·텍스트 내보내기), 상태 그래프(변형 범위·요청 바이트), 의존성 주소 방식, 인스턴스 머리 이름 바꾸기, 위젯(구조 표 값 형식, DTC 코드 16진·10진, 타입 바꾸기 표의 모두 선택이 충돌 행을 건너뜀, 마법사 방식별 동작 값).
- **검토에서 고친 것(M6-3)**: 글자(ascii)·바이트(bytes) 타입 사이를 바꿀 때 기본값·상수의 길이를 보지 않아, 4글자 값이 2바이트 타입으로 바뀌면 전문을 만들 수 없게 됐다(`build_telegram`이 "정확히 n바이트" 오류). 이제 미리보기가 충돌로 막고(가변 길이 타입은 최소·최대 길이 범위), 명령도 같은 검사를 거친다.
- **검토 결과 그대로 둔 것**: M6-3에서 1바이트 → 1비트처럼 전체 길이가 바이트 경계를 벗어나는 변경은 막지 않고 영향 열에 ⚠(검증 VAL-005)로 알린다(설계대로, 검증이 출력을 막음). M6-4에서 참조로·사본으로 방식은 일부 인스턴스를 빼도 기준 변형의 DID·DTC·Job을 모두 참조한다(빼는 것은 인스턴스뿐). 빈 변형은 넣은 인스턴스가 쓰는 DID만 넣는데, 서비스 구조가 `instance_dids` 밖에서 DID를 가리키는 경우는 예제에 없어 확인하지 못했다. M6-6은 테스트로 본 범위(변형 비교·파일 비교·내보내기)에서 문제를 찾지 못했다.
- **검증 범위**: 클라우드 Linux(PySide6 6.11.2 offscreen, ABS_ESP 예제)에서 전체 285개 통과(5개 건너뜀). Windows 18:12~18:18 `run_checks.cmd`: 테스트 285개 통과(새 `test_p3_features` 18개 모두 실행·통과), 빌드 성공, GUI 스모크 통과(`gui_exit=0`, 저장은 전처럼 대체 경로). `QFontDatabase: Cannot find font directory` 줄은 offscreen Qt의 경고로 결과와 무관하다.

## 26차(Claude): UI 개선 P1 9개 이식 — 칸 편집·여러 행 입력·필터·노드 진입·새 번호·Qualifier 자동·찾기 창·키보드·바이트 격자 (2026-09-27) — **Windows 통과(17:57~18:02, 테스트 267·빌드·GUI 스모크)**

- **요청**: 1단계(P1)는 구현되지 않았었다. 23차 기준으로 따로 써 두었던 P1 코드를 24차(P2)·25차(P3) 위에 이식한다: M1-1, M1-2, M1-3, M1-4, M1-5, M2-1, M3-1, M3-3, M5-1. P2가 이미 만든 부분(묶음 명령 `set_dtc_attributes`, `make_qualifier`, 출력 창 링크, 경로 입력, 멤버 칸 편집, 속성 시트)은 그대로 쓰고 겹치는 P1 코드는 버렸다.
- **검증 범위**: `py_compile`과 pyflakes(정의되지 않은 이름)만 했다. 새 테스트 `test/test_ui_editing.py`(23차 로컬판을 이 코드에 맞춘 것, 24개)와 기존 테스트는 돌리지 않았다. Windows `build\run_checks.cmd`가 필요하다.
- **Windows 1차 실행**(17:48~17:51, `run_checks.cmd` 테스트 단계): 267개 중 266개 통과. 실패 `LayoutPaneLinkTest.test_grid_and_member_selection_are_linked` — 테스트가 `members.selectRow(1)`로 행을 골랐는데, 멤버 표는 칸 단위 단일 선택(SelectItems·SingleSelection)이라 Qt가 아무것도 고르지 않았다. 앱 코드는 그대로이고 테스트가 사용자처럼 칸을 고르게(`setCurrentCell`) 고쳤다. `test_rejected_edit…`의 IntegrityError 로그는 중복 코드 거부를 일부러 일으킨 것이다(테스트 통과). 테스트가 실패해서 빌드·GUI 스모크는 돌지 않았다.
- **Windows 2차 실행**(17:57~18:02, `run_checks.cmd`): 테스트 267개 통과(`unittest_exit=0`, 예제가 필요한 일부는 건너뜀), 빌드 성공, GUI 스모크 통과(`gui_exit=0`: 가져오기·DID 배치·경로 이동·오른쪽 버튼 메뉴·참조 영향·트리·프로젝트 메뉴·상태 그래프·ODX 사전 검사·타입 독립 복사·실행 취소·다시 실행·저장). 걱정했던 데이터 타입 목록 칸 누르기 뒤 `편집` 메뉴 단계도 통과했다. 저장 창은 전처럼 대체 경로(`save_dialog_fallback`)로 저장했다. 스모크 끝의 `app_log` 오류(2026-09-25 apply_cdd FileNotFoundError)는 이전 날짜 기록이다. 스모크는 칸 편집·찾기 창 같은 P1 새 동작을 누르지 않는다(자동 테스트 `test_ui_editing`이 맡음).
- **결정 D1 적용 — F2가 바뀜**: F2 = 목록 칸 편집(`칸 편집`, [LIST-R410]). 속성 대화상자는 Alt+Enter·두 번 클릭·`속성…  Alt+Enter` 단추(24차까지는 F2도 속성 대화상자였다). 데이터 배치 멤버 표·속성 시트의 F2는 전처럼 그 표가 먼저 받는다. 의존성 목록의 F2는 여러 행 상태 조건 일괄 변경 그대로.
- **M1-1 칸 편집**(`diagstudio/cell_edit.py` 새 파일, `ObjectModel.flags·setData`, `CellDelegate`): 이름·Qualifier·DTC 코드(0x·10진·SAE 문자형)·DID 번호·상태 코드·변형 설명·ECU 제조사·DTC 레코드 항목(열거 = 콤보, 필수 열거는 비울 수 없음). F2·글자 입력·선택된 칸 다시 누르기로 시작, Enter 확정, Esc 취소, Tab·Shift+Tab 다음·이전 편집 칸, 마지막 행 마지막 편집 칸의 Tab은 새 DTC·DID를 [UI-R335] 번호로 만들고 첫 칸을 편집(그 밖의 목록은 추가 대화상자). 확정 = 명령 하나(실행 취소 한 번). 결과 전에 칸에 새 값을 보이고, 거부되면 다시 읽어 되돌린 뒤 입력을 칸에 되살려 다시 편집한다(모달 창 없이 상태 줄·출력 창). 편집 중에는 목록 다시 싣기를 보류한다. 고칠 수 없는 칸의 F2는 이유를 상태 줄·출력 창에 쓴다. 이름을 바꾸면 Qualifier가 아직 이름에서 만든 값이고 가져온 객체가 아닐 때 따라간다([QUAL-R020], 목록 행 `imported`).
- **M1-2 여러 행 입력·값 복사·오른쪽 버튼**: 여러 행을 고르고 한 칸을 확정하면 선택한 모든 행에 넣는다(이름·설명 = `bulk_edit`, 레코드 항목 = `set_dtc_attributes`, 번호·Qualifier는 한 행만). DTC 레코드 항목 열 오른쪽 버튼 `선택한 DTC에 이 값 복사`(둘 이상 골랐을 때만, [DTCUI-R035]). 오른쪽 버튼은 이미 선택된 행이면 여러 행 선택을 유지하고 새 행이면 그 행을 고른다, 빈 곳은 새로 만들기·붙여넣기·표 복사 메뉴([UI-R350]). 메뉴에 `'열' 칸 편집  F2`.
- **M1-3 필터**(`application.filter_terms`): 띄어 쓴 단어는 하나라도 맞으면(OR), 큰따옴표 안은 구절, 번호(10진·0x)는 단어마다. 필터가 걸리면 `n / 전체 표시` 칩, 필터 칸 지우기 단추·오른쪽 버튼 `필터 지우기`. 노드에 들어가면 필터를 비운다.
- **M1-4 노드 진입·정렬·열 너비**: 결정 D4 — 페이지를 나누지 않고 한 목록(`page_size=0`, `‹ 이전`·`다음 ›`와 설정 `목록 페이지 크기` 제거). 결정 D5 — 들어갈 때 번호가 있는 목록은 번호순(같으면 이름순), 문서 순서 목록은 문서 순서(`Application.default_sort`). 머리 누르기 = 그 열(다시 누르면 반대), Ctrl+누르기 = 여러 열, 머리 글자에 ▲▼(여러 열이면 순서 번호). 정렬과 열 너비는 목록 종류별로 세션 동안 기억한다(처음 여는 목록만 내용에 맞춤). 노드를 누르면 목록에 포커스와 첫 행 선택, 트리에서 Enter도 목록으로. 다시 실어도 선택·현재 칸을 유지한다([UI-R340-SelMod], [LIST-R600]).
- **M1-5 새 번호**(`suggest_number`·`add_defaults`, 작업 스레드 허용 목록에 추가): [UI-R335] 기준 번호 R(새 항목 행 = 마지막 보이는 행, 첫 행 선택 = 그 행, 그 밖 = 첫 선택 행 바로 위, 필터로 보이는 행이 없으면 가장 큰 번호)에서 R+1부터 위로 가장 작은 빈 번호, 없으면 R−1부터 아래로. 빈 번호는 그 변형 전체에서 찾는다. 추가 대화상자의 DID·DTC 번호 기본값이 된다(찾지 못하면 이유를 알림).
- **M2-1 Qualifier 자동**(`dialogs.ObjectDialog._setup_qualifier`, `domain.qualifier_follows`): Qualifier 줄은 접어 두고 `값 · 이름에서 자동/직접 입력 ▸`만 보인다. 새 객체는 이름(한글만이면 결정 D3 종류 + 번호, 번호 칸을 바꾸면 따라감)에서 만들고, 편집할 때는 아직 이름에서 만든 값이고 가져온 객체가 아니면 이름을 따라간다. 펼쳐 직접 고치면 따라가기를 멈추고 `이름에서 다시 만들기`로 되돌린다. Qualifier 128자 제한은 새 객체(`_new_name`)와 바뀐 값에만 — 가져온 긴 Qualifier를 그대로 두면 다른 속성을 고칠 수 있다(`_common`).
- **M3-1 찾기 창**(`diagstudio/find_dialog.py` 새 파일, `find_document` 확장): 비모달 `찾기…`(Ctrl+Shift+F). 최근 검색어(사용자 설정 `find_history`), 범위(모두·이름·Qualifier·요청 바이트 — 한 바이트도 됨), 대소문자 구분·단어 단위·전체 일치. 찾기 = 처음부터, `다음 찾기` F3 = 마지막 찾은 곳 다음부터(끝이면 처음부터, 창을 닫아도 됨), 모두 찾기 = 출력 창에 ↗ 링크. 결과는 문서 트리 순서다. 예전 결과 목록 대화상자는 없앴다.
- **M3-3 키보드**: F2 칸 편집, F3 다음 찾기, F9 검증(F5도 유지), F6·Shift+F6 트리 → 목록 → 선택 객체 창, Ctrl+C = 보이는 그대로(화면 열 이름 머리 줄, 숨긴 열 제외, 선택이 없으면 모든 행). 목록 크게 보기의 Esc는 칸 편집 중이면 편집만 취소한다.
- **M5-1 바이트 격자**(`presentation.byte_grid`, `layout_view.LayoutPane`): 24바이트보다 긴 데이터·예약 필드는 앞 23바이트와 마지막 바이트만(바이트 칸 `n…`·`…m`, [DOUIG-R210]). 예전 64바이트 자르기를 없앴다. 격자 칸을 누르면 그 멤버 행, 멤버 행을 고르면 그 필드의 격자 칸을 모두 고른다([DOUIG-R120]).
- **작업 줄**: 칸 확정·Ctrl+Z·찾기·머리 정렬처럼 작업 중에 들어온 일은 버리지 않고 줄에 세워 끝난 뒤 순서대로 보낸다(`queue_job`, 25차 `when_idle` 대신 `command()`가 씀).
- **새 파일**: `diagstudio/cell_edit.py`, `diagstudio/find_dialog.py`(pyproject 목록에 추가), `test/test_ui_editing.py`. 스키마 변경 없음(10). ODX 출력 의미는 바꾸지 않았다.
- **GUI 스모크 영향(확인 전)**: 스모크는 F2를 쓰지 않는다. 데이터 타입 목록에서 이미 선택된 칸을 한 번 누르면 칸 편집기가 열릴 수 있다(선택된 칸 다시 누르기 = 편집). 바로 `편집` 메뉴로 가므로 값은 바뀌지 않지만, 실패하면 이 단계를 먼저 본다.
- **남은 것**: Windows `build\run_checks.cmd`, P3 새 기능 자동 테스트, M6-3·M6-4·M6-6 high 검토.

## 25차(Claude): UI 개선 P3 7개 — 값 형식·상태 줄·인스턴스 이름·타입 바꾸기·변형 마법사·비교 보기·의존성/그래프 옵션 (2026-09-27) — **Windows 통과(13:39~13:44, 테스트 243·빌드·GUI 스모크), P3 새 기능 자동 테스트 없음**

- **요청**: 24차(P2) 위에 `docs/ui-manual-review-2026-09-26.md` 3단계(P3) 7개(M2-5, M4-2, M5-4, M6-3, M6-4, M6-6, M6-7)를 구현한다. 구현이 끝나면 테스트하지 말고 알린다(사용자 지시). P1은 여전히 없다.
- **검증 범위**: `py_compile`만 했다. 구현 도중(클라우드 Linux, PySide6 6.11.2 offscreen) 기존 테스트 243개가 통과하는 것을 한 번 확인했으나, 그 뒤의 변경(설정 격리, 비교 창 연결 등)과 P3 새 기능의 자동 테스트는 쓰지도 돌리지도 않았다. Windows `build\run_checks.cmd`가 필요하다.
- **Windows 실행**: 13:20 `run_checks.cmd` = 테스트 243 통과·빌드 성공·GUI 스모크가 데이터 타입 목록 단계에서 실패(결과 창이 늘어 목록이 3행만 보여 UIA에 `_Byte` 칸이 없음 → 스모크가 목록 머리 글 `데이터 타입 (n)`·`n개 객체`로 판단하게 고침). 13:35 `run_gui_smoke.cmd` = DID 오른쪽 버튼 메뉴가 열리지 않음(선택 조회 중 `table_menu`가 버림 → 짧은 조회 중에는 트리·목록 메뉴를 열게 고침, 스모크는 오른쪽 버튼 전에 창을 앞으로). 13:39 `run_checks.cmd` = 테스트 243 통과, 빌드 성공, GUI 스모크 통과(`gui_exit=0`: 타입 독립 복사·실행 취소·다시 실행·저장까지). 저장 창은 기존 대체 경로(`save_dialog_fallback`)로 저장했다. 스모크는 P3 새 화면을 누르지 않는다.
- **GUI 스모크 실패 수정(24차 Windows 10:21)**: `타입 복사 실행 취소 응답 시간 초과`. 복사 뒤 선택 조회(`selection_details`)가 도는 동안 실행 취소·다시 실행 동작이 꺼져 Ctrl+Z가 버려졌다. `command()`는 작업 중이면 버리지 않고 끝난 뒤 실행하고(`when_idle`), 실행 취소·다시 실행은 조용한 조회(QUIET_METHODS) 중에도 켜 둔다(저장·검증 같은 긴 작업 중에는 끈다).
- **M4-2 상태 줄 설명**: 모든 QAction에 `setStatusTip`(`ui.STATUS_TIPS`), 결과 창 보이기·최근 파일 항목 포함.
- **M2-5 값 형식**(`presentation.value_formats·format_value·parse_value`, `dialogs.MembersEditor`): 구조 표 위 `값 형식 10진·16진·2진`. 정수 타입(unsigned·signed·bcd) 멤버의 기본값·상수만 보이는 글자가 바뀌고 저장 값(10진 원시값)은 같다. 접두어 `0x`·`0b`는 형식과 상관없이 받고, 없으면 지금 형식으로 읽는다. 16진·2진은 부호화 값(signed 음수 = 2의 보수). BCD는 10진·16진만([VALUI-R050])이고 16진은 자리마다 한 니블(`0x1234` = 1234, 각 자리 0~9). **바뀐 점**: 전에는 대화상자가 BCD 칸의 `0x12`를 그대로 보내 작업 스레드가 18로 읽었다. 이제 대화상자는 늘 10진으로 바꿔 보낸다. 읽을 수 없는 값은 칸을 붉게 두고 확인·적용을 막는다. DTC 대화상자의 코드 칸에 16진·10진 단추와 SAE 표기. 설정 표시 탭 `16진 값을 앞자리 0으로 채우기`(기본 켜짐).
- **M5-4 인스턴스 이름**: 인스턴스 페이지 머리에 이름 칸(Enter·포커스 이동 = 확정, Esc = 되돌림)과 `속성…`(이름·Qualifier·설명 대화상자, `update_common`). 이름은 같은 클래스·변형 안에서 유일(트리 자료로 검사), 여러 변형이 쓰면 설정에 따라 묻는다. Qualifier가 옛 이름에서 만든 값과 같으면 새 이름을 따라간다(M2-1 규칙, `make_qualifier`). 원문·미지원 인스턴스는 읽기 전용. 머리 글에는 이름 대신 식별자·Qualifier를 쓴다(`test_ui_widgets` 기대값 수정).
- **M6-3 데이터 타입 바꾸기**(새 조회 `replace_type_preview`, 새 명령 `replace_data_type`, `tools.ReplaceTypeDialog`, 편집 메뉴·타입 목록 오른쪽 버튼): 사용처(데이터 객체)를 골라 한 명령으로 새 타입에 연결한다. 충돌(고를 수 없음): 원문 배치·읽기 전용·미지원·보존 원문(출력 차단)이 걸린 소유자, 확장 데이터 레코드·MUX 케이스 구조, MUX 선택자, packet ↔ 단순 타입, 순환, 비트필드 안의 길이 변경, 사용 중인 packet 타입의 길이 변경, 가변 길이를 마지막이 아닌 자리에, 기본값·상수 종류가 바뀜(정수 ↔ 글자·바이트), 새 타입 범위 밖, 1 MiB 초과. 영향 열: 길이 변화·뒤 필드 위치 이동·바이트 경계 경고(VAL-005)·기본값 물리값 변화(원시값 유지)·공유 변형 수. 모두 선택(충돌 제외)·모두 해제. 명령은 같은 검사를 다시 하고 구조 검사 목록에 넣었다. 스키마 변경 없음.
- **M6-4 변형 마법사**(`create_variant` 확장, 새 조회 `variant_wizard_items`, `tools.VariantWizardDialog`, `변형 추가…`·변형 목록 추가가 마법사를 연다): 참조로(기본)·사본으로(필수·사본 불가 인스턴스는 참조)·빈 변형(기본 변형의 필수 인스턴스만, 기준 변형 비활성). 왼쪽 클래스(묶음 동작·요약 아이콘), 오른쪽 인스턴스(필수·동작 참조/사본/사용 안 함, (혼합)). 필수는 사용 안 함 불가, 사본은 `_detach_instance` 규칙(DID 공유). DID·DTC·Job: 참조로·사본으로는 기준 변형 것 참조, 빈 변형은 넣은 인스턴스가 쓰는 DID(스냅샷 DID 포함)만. [VW-R510] 모든 인스턴스가 참조·사본이면 기준 변형의 설명(모든 언어)·`user.*` 속성 복사(`cdd.*`·식별 패턴은 복사하지 않음). 기존 `create_variant` 호출(방식 없음)은 참조로와 같고 설명·사용자 속성 복사가 더해졌다. 설정 편집 탭 `변형 마법사 사본 허용`(기본 켜짐).
- **M6-6 비교 보기**(`comparison.compare_tree`·`export_text/xml/xhtml`, 새 조회 `compare_view`, `compare_view.CompareDialog` 비모달): 한 창에서 왼쪽(작업본 변형)·오른쪽(작업본의 다른 변형 또는 저장된 파일의 변형) 선택. 트리 = 변형 → 클래스 → 인스턴스 → 서비스 → 차이 행, DID·DTC 묶음, 파일 비교의 데이터 타입 장(문서 순서). 필터 왼쪽만·바뀜·오른쪽만·🔗참조 객체·레코드 항목·속성 값·상태 전이(조건 포함)·다른 언어(처음 꺼짐), 모두 보이기, 자식이 모두 숨으면 부모 숨김·안내 문장, 펼침(변형·클래스·인스턴스(기본)·모두), 상세 창, 양쪽 파일·revision·경로, 색 범례, 왼쪽·오른쪽 객체로 이동(창 유지, 파일 쪽은 이동 불가), 내보내기(텍스트·XML·XHTML, 화면 필터 미적용). 엔진 확장: 설명·다른 언어 이름·설명, 속성 값(`app.*` 제외), DTC 레코드 항목(`#raw` 합침), 참조한 데이터 타입·DID 정의 차이([CMP-R005] 한 줄), 멤버 이름 다른 언어. 저장 파일 비교는 `object_attributes`·`muxes`·`mux_cases`도 읽고 화면 언어를 작업본에 맞춘다. 메뉴 `변형 Compare…`·`저장된 프로젝트와 비교…`가 이 창을 연다. 기존 `compare_variants`·`compare_project_file` API는 그대로 둔다. 옮기기 모드는 범위 밖.
- **M6-7 의존성 표·상태 그래프**: 의존성 목록 아래 `모두 펼치기`·`모두 접기`(서비스별 보기면 묶음 보기로 바꿈), `주소 방식 표시`(첫 열 뒤 P·F·P/F, 묶음은 (혼합)), 상태 열 머리 오른쪽 버튼 `왼쪽 상태 열 접기`·`모든 열 펼치기`. 상태 그래프(`state_graph(variant)`)는 변형·상태 그룹 선택, 전이 글자 서비스 이름 ↔ 요청 바이트(`10 01`), 전이 색 구분, 상태 노드 툴팁(그 상태에서 실행할 수 있는 서비스, 조건 없는 서비스 수). 인자 없는 `state_graph()`는 전처럼 모든 서비스.
- **설정 격리**: `panels.app_settings()` — 환경 변수 `DIAGAUTHOR_SETTINGS`(ini)가 있으면 그 파일을 쓴다. 주 창을 만드는 테스트 4개(`test_ui_gaps·tree·validation·widgets`)가 임시 ini를 쓰게 했다(24차 남은 것: 테스트가 Windows 사용자 설정을 읽음).
- **새 파일**: `diagstudio/tools.py`, `diagstudio/compare_view.py`(pyproject 목록에 추가). 스키마 변경 없음(10). ODX 출력 의미는 바꾸지 않았다.
- **남은 것**: P3 새 기능의 자동 테스트, Windows `build\run_checks.cmd`(테스트 → 빌드 → GUI 스모크), M6-3·M6-4·M6-6의 high 검토. P1은 없다.

## 24차(Claude): UI 개선 P2 13개 — 출력 창·경로·속성 시트·시작 화면·설정·Qualifier 개요 (2026-09-27) — **Windows 1차: 테스트 243 통과·빌드 성공·GUI 스모크 실패 → 수정, 재실행 전**

- **요청**: `docs/ui-manual-review-2026-09-26.md` 2단계(P2) 13개(M4-1, M4-3, M1-6, M1-7, M2-2, M2-3, M2-4, M3-2, M5-2, M5-3, M6-1, M6-2, M6-5)를 구현한다. 결정 D1~D7은 문서 권장안을 따른다. 1단계(P1)는 아직이므로 P2가 기대는 부분(묶음 명령, 칸 편집기, Qualifier 생성 함수)만 최소로 넣었다.
- **Windows 1차 실행**(2026-09-27 10:07~10:12, `run_checks.cmd`): 테스트 243개 통과, 빌드 성공, GUI 스모크 실패(`gui_exit=1`, ODX 사전 검사 단계 "출력 범위 밖의 보존 항목 표시 누락"). 그 앞 단계(가져오기, DID 배치, 경로 복사·이동, 오른쪽 버튼 메뉴 `참조 영향…`, 트리, 프로젝트 메뉴, 상태 그래프, Shortcut Qualifier 개요)는 통과했다.
  - 원인: Qt 표 칸은 화면에 보이는 행만 UIA로 노출된다(같은 실행에서 DID 19개 중 9칸만 읽혔다). 23차부터 결과 창이 최소 높이로 열려 검증 결과가 2~3행만 보였고, `범위 밖` 행은 5번째 행부터다. 데이터는 맞았다(52개 중 범위 밖 11개).
  - 수정: 검증 결과가 나오면 결과 창의 검증 결과 탭을 앞에 두고, 최소 높이로 접혀 있으면 창 높이의 35%(최대 360)로 늘린다(`ui.reveal_results`, 늘리기만 함). 요약 줄에 `범위 밖 N개`를 더했다. 스모크는 결과 창이 늘어난 뒤의 칸을 최대 10초 다시 읽는다. 경로 ▾ 단추에 겹쳐 보이던 메뉴 화살표를 없앴다. 이 수정 뒤 Windows 재실행은 아직이다.
- **검증 범위**: 클라우드에 PySide6를 설치할 수 없어(패키지 저장소 403) Qt 화면 코드는 실행하지 않았다. `py_compile`, Qt API 정적 검사(Windows 빌드 `.pyd`의 Shiboken 서명), 정의되지 않은 이름·`self` 속성 검사, Qt 없는 테스트(243개, 57 건너뜀 — 모두 통과)만 했다. Windows에서 `build\run_checks.cmd`가 필요하다.
- **M4-1 출력 창**(`pages.WorkLog`): 열 = 종류·시각(1/100초)·출처·↗·내용. 종류 단추(오류·경고·정보·상태, '상태'는 처음에 숨김)·출처·내용 필터(AND), 필터 초기화, 오른쪽 상세 영역(기본 꺼짐), 자동 스크롤(기본 켜짐), 복사, 지우기. 필터·단추·열 너비는 QSettings(`output/*`)에 남는다. ↗ 칸이나 행 두 번 클릭 = 대상 객체로 이동. 명령 완료(대상 객체)·저장·열기·가져오기·출력·검증·경로 오류가 출처를 넘긴다. 일상 조회는 기록하지 않는다. 이전 형식 변환 경고는 파일마다 한 번만 남긴다.
- **M4-3·M1-6·M1-7**(`ui.py` 보기 메뉴): `보기 초기화`(분할 위치·결과 창·열 너비·출력 창 필터), `목록 크게 보기`(목록 위 `⤢ 목록 크게`, Esc로 되돌림), 결과 창 보이기, `열 정보…`와 헤더 툴팁(헤더 오른쪽 버튼). DTC 레코드 항목 열은 원본 템플릿의 Qualifier·종류·필수·설명·값 목록을 보인다(`query_objects`의 `column_info`, 표시 전용).
- **M2-2 속성 시트**(`panels.PropertySheet`): 선택 객체 창 `속성` 탭 = 편집 가능한 시트(위) + 상세 글(아래). 여러 행을 고르면 공통 속성만, 값이 다르면 `(여러 값)`, 값을 넣으면 모두에 한 명령으로 적용한다. 이름·설명 = `bulk_edit`, Qualifier = `update_common`(하나 골랐을 때), DTC 코드 = `update_dtc`, 레코드 항목 = 새 `set_dtc_attributes`(여러 DTC), `user.*` = `set_attributes`. 선택은 작업 스레드 `selection_details`(상세 + 시트) 한 번으로 조회하고, 바꾼 뒤 같은 행들을 다시 고른다. 여러 변형이 쓰는 객체는 설정에 따라 묻는다.
- **M2-3 DTC 대화상자**: 위쪽 DTC 콤보 + ▲▼(바꾼 값이 있으면 적용 뒤 옮김), `상세 보기`(여러 줄 편집), `빈 항목 숨기기`, 항목 ▲▼(Alt+↑/↓), 키 열 → 항목 툴팁. DTC 추가 대화상자에도 속성 탭(`catalog.dtc_record_items`, `create_dtc`의 `attributes`).
- **M2-4 대화상자 공통**(`dialogs.BaseDialog`): 단추 `확인`·`취소`·`적용`(편집 대화상자만, 닫지 않음), 크기 조절 표시, 첫 칸 포커스, 포커스 없는 콤보·스핀 상자의 휠 막기(`panels.WheelGuard`, 휠은 부모로). 다른 창을 여는 메뉴·단추 글자는 "…"로 끝난다(`추가…`, `속성…`, `선택 객체의 참조 영향…`, `Qualifier 개요…`, `상태 그래프…` 등). 속성 대화상자 단축키는 F2와 Alt+Enter.
- **M3-2 경로 입력**: ▾ 이번 세션 기록, `이동` 단추. `qpath:`(Qualifier 경로), `[DID]`·`[DTC]` 같은 종류 선택자, 큰따옴표·작은따옴표 조각, `\` 이스케이프(`application.parse_object_path`·`resolve_qualifier_path`). 문법 없는 입력은 이름 경로(D7), ECU 조각·목록 노드 조각은 있어도 없어도 된다. 틀린 경로는 선택을 유지하고 대화상자 없이 상태 줄·출력 창에 남긴다. 설정 `경로 복사 형식 = Qualifier`면 목록 오른쪽 버튼 `경로 복사`가 qpath를 만든다(`object_qualifier_path`). `이름 경로 복사` 단추는 그대로 이름 경로다.
- **M5-2 멤버 칸 편집**(`layout_view.LayoutPane`): 이름·데이터 타입(콤보)·기본값·상수·설명을 F2·두 번 클릭으로 고친다(설명은 Enter 줄바꿈·Ctrl+Enter 확정, Esc 취소, Tab 다음 칸). 새 명령 `update_member`는 데이터 객체를 제자리에서 고치고 기존 구조 검사를 그대로 거친다. 여러 변형이 쓰는 DID·인스턴스는 U3 선택을 묻고, 사본을 고르면 `detach_*` 뒤 `member_counterpart`로 사본의 같은 위치 멤버에 다시 적용한다. 바이트·비트·고정값·원문 배치 멤버에서 F2는 이유를 상태 줄·출력 창에 쓴다. 설명 칸은 설명이 없으면 타입 요약을 회색으로 보인다.
- **M5-3 서비스 행**: 요청·긍정·부정 칸 링크(누르면 그 전문 탭, `rc`는 NRC 목록 열기·닫기, `NRC ≫` 단추), 행 툴팁(phys·func 줄, SPRMIB), 비활성 = 체크 + 빨간 ✕. NR 탭에 상태 그룹 NRC(아이콘, 원본 상태 그룹의 '상태 불일치 부정 응답', 표시 전용 — 출력 의미에 쓰지 않음).
- **M6-1 시작 화면**(`panels.StartPage`): 최근 파일(✕ = 목록에서 빼기)·최근 폴더·빠른 접근(예제 폴더·바탕 화면·문서)·새 프로젝트·열기·CDD 가져오기·설정, `파일 → 최근 파일`. 주 창 어디에 끌어 놓아도 `.diagproj`는 열고 `.cdd`·`.cddt`는 가져오기 미리보기로 보낸다.
- **M6-2 설정**(`panels.SettingsDialog`, `파일 → 설정…`): 일반(최근 파일 수 0~16·격자 선·줄 색·예제 폴더), 표시(DTC 형식·트리 인스턴스 식별자·경로 복사 형식), 편집(서비스를 열 때 NRC 목록·새 데이터 타입 바이트 순서), 확인(공유 객체 수정 묻기·이전 형식 알림), 목록(페이지 크기·새 항목 행·열 너비 맞춤). QSettings `options/*`, 프로젝트 파일에는 넣지 않는다. 개정 기록 묻기(D6)는 넣지 않았다.
- **M6-5 Qualifier 개요 편집**(`panels.QualifierOverviewDialog`): 범위(문서 전체 / 현재 변형), 이름에서 만든 Qualifier 숨기기, 하위 객체 보이기, 검색, Qualifier 칸 편집(형식·128자 검사), `다시 만들기…`(최대 길이 8~128, 이름대로·대문자·소문자, 대상 선택한 행·보이는 행·문서 전체, 한글만 있는 이름은 종류+번호 또는 종류+순번 — D3). 바꾼 값은 확인·적용 때 새 명령 `set_qualifiers` 하나로 반영한다. Shortcut Qualifier 개요는 그대로다.
- **데이터·명령**: 스키마 변경 없음(10). 새 명령 `set_qualifiers`·`set_dtc_attributes`·`update_member`, 새 조회 `property_sheet`·`selection_details`·`object_qualifier_path`·`member_counterpart`, `domain.make_qualifier`. ODX 출력 의미는 바꾸지 않았다.
- **테스트·스모크 변경**: `test_ui_widgets` 출력 창 필터를 종류 단추로. GUI 스모크의 메뉴 글자를 "…" 규칙에 맞췄다(`참조 영향…`, `Qualifier 개요…`, `Attribute 개요…`, `상태 그래프…`, `Shortcut Qualifier 개요…`). 새 기능의 자동 테스트는 쓰지 않았다.
- **남은 것**: 수정 뒤 Windows `build\run_checks.cmd` 재실행(스모크의 ODX 사전 검사와 그 뒤 단계 — 타입 독립 복사·Undo/Redo·저장 — 는 22차 뒤로 통과 기록이 없다. `build/checks`의 10·11·04 캡처가 22차 것이다). P1(셀 편집 M1-1 등)이 없어 목록의 F2는 아직 속성 대화상자다. 사용자 설정은 Windows 사용자 QSettings에 있어 테스트도 같은 값을 읽는다(예: 트리 식별자를 끄면 `test_ui_tree` 기대값이 달라진다).

## 23차(Claude): 화면 피드백 반영 — 선택 객체 창 위치, Qualifier 열, DTC 속성 편집 (2026-09-26) — **자동 테스트 통과(클라우드), Windows 빌드·GUI 스모크 전**

- **요청**(사용자 화면 확인 피드백 4건): ① 선택 객체 창을 목록 옆이 아니라 목록 아래에 둔다. ② 이름 경로 ‹ › 버튼 방향이 반대 같다. ③ Qualifier 열은 어느 표든 맨 뒤로(평소 보지 않는 열). ④ DTC 편집 대화상자의 속성(4) 탭에서 값을 고칠 수 없다.
- **① 선택 객체 창**(`ui.py`, `layout_view.py`):
  - 주 영역을 `트리 | (목록 / 선택 객체)`로 바꿨다. 목록과 선택 객체 창은 세로 분할(`work_splitter`, 처음 55:45)이다.
  - 창 높이를 아끼려고 `선택 객체` 제목과 `속성 편집 F2` 버튼을 탭 줄 왼쪽·오른쪽에 두었다(제목·버튼 줄 없음).
  - 데이터 배치: 멤버 표와 바이트 격자를 위아래에서 좌우로 바꿨다(CANdela처럼 격자가 오른쪽). 전체 / 멤버 표만 / 격자만 버튼은 탭 줄 오른쪽으로 옮겼다.
  - 결과 창(검증 · 작업 결과)은 처음에 최소 높이로 연다. 분할 위치는 종료 때 설정(`work_splitter`, `main_splitter`)에 남기고 다음 실행에서 되살린다.
  - 안내 문장의 "오른쪽"을 "아래"로 고쳤다(DID·서비스·상태 그룹·인스턴스 페이지 머리, 트리 툴팁).
- **② 뒤로/앞으로**: 실제 작업 스레드로 ABS_ESP를 열어 재현한 결과 ‹는 직전 위치, ›는 다음 위치로 정상 이동했다. 사용자도 정상으로 확인했다. 재현 중 찾은 결함만 고쳤다: 프로젝트를 연 직후의 첫 위치가 이력에 없어서, 첫 이동 뒤 ‹가 꺼져 처음 위치로 돌아갈 수 없었다. `apply_state`가 이력이 비어 있으면 현재 노드를 첫 항목으로 넣는다.
- **③ Qualifier 열 맨 뒤**(`presentation.qualifier_last`):
  - 적용한 표: 주 목록(`ObjectModel`), 세부 표·읽기 전용 표(`pages.fill_table`: 세부 표, 고장 메모리 레코드, 개정 이력 등), 개요 대화상자(`OverviewDialog`: Qualifier 개요, 참조·비교 등), 구조 편집 표(`MembersEditor`, 열 번호를 이름 상수로 바꿈).
  - 머리가 정확히 `Qualifier`인 열만 옮긴다. `Shortcut Qualifier` 행렬(그 표의 주제 열)과 보존 항목의 `원본 경로`(qualifier 필드를 다른 뜻으로 씀)는 그대로다. 작업 스레드 API의 열 순서는 바꾸지 않았다(표시 규칙).
  - 주 목록 열 너비를 내용에 맞춘다(`fit_columns`: 열마다 최대 320, 이름 열 250~360, 첫 식별자 열 90~160). 맨 끝 Qualifier가 남는 폭 전체를 차지하던 문제를 막고, 폭이 모자라면 Qualifier가 가로 스크롤 끝으로 밀린다.
- **④ DTC 속성 편집**(`dialogs.DtcAttributeEditor`, `application._dtc_record_items`·`_set_dtc_attributes`):
  - 속성 탭은 원본 CDD의 DTC 레코드 항목 템플릿마다 한 행(항목 · 값 · 키)이다. ABS_ESP: TR `SetCondition`·`ResetCondition`·`SpecialInstruction`(conv=opt), ENUM `CorrectiveAction`(conv=req, ETAG 0~6). 이 DTC에 없는 항목도 빈 행으로 보여 채울 수 있다.
  - 텍스트는 값 칸을 두 번 눌러 고친다. 열거는 템플릿 ETAG 목록에서 고르고, 표시명과 `#raw`를 함께 저장한다(가져오기와 같은 규칙: 표시명 = ETAG en-US, 없으면 첫 값). UNS는 0 이상 정수(df=hex면 `0x..`로 저장). 다국어 복제(`@언어`)와 `user.*`는 텍스트다.
  - 값을 비우면 항목을 지운다. 필수 항목(conv=req)은 비울 수 없다. 템플릿에 없는 키, `#raw` 직접 수정, 템플릿에 없는 열거 값은 작업 스레드가 거부한다. 원본 CDD가 없는 프로젝트는 `cdd.*`를 바꿀 수 없고 `user.*`만 바꾼다. 그 밖의 속성(가져오기 메타데이터 등)은 회색·읽기 전용이다.
  - 명령은 기존 `update_dtc`에 `attributes`(바뀐 행만)를 더했다. 한 명령이므로 Undo 한 번에 되돌아간다. 스키마 변경 없음.
  - ODX 출력: 기존 대응(DTC의 `cdd.*` 속성 → SDG, `#`·`@` 키 제외)을 그대로 쓰므로 고친 값이 SDG에 나간다. 새 의미 매핑은 추가하지 않았다. DTC 밖 객체의 `cdd.*` 속성은 출력 의미를 확인하지 않았으므로 계속 읽기 전용이다.
- **테스트**(클라우드 Linux, Python 3.13.13, PySide6 6.11.2 offscreen, `examples\candela_example` 예제): 243개 중 238 통과, 5 건너뜀(CANoe 예제·추가 CDD 폴더가 없어서 건너뛰는 코퍼스 시험). 새 테스트 7개: Qualifier 열 순서(`test_ui_widgets`), DTC 속성 편집기, 선택 객체 창 위치, 첫 위치 이력(`test_ui_gaps`), DTC 속성 편집·Undo·거부·원본 없는 프로젝트(`test_ui_pages`). 화면은 offscreen 캡처로 확인했다(1440×930, 1920×1100).
- **남은 것**: Windows에서 `build\run_checks.cmd`(테스트 → 빌드 → GUI 스모크)를 돌려야 한다. DTC 추가 대화상자에는 속성 탭이 없다(추가한 뒤 편집에서 채운다).

## 22차(Claude): UI 개선 U2~U6 — CANdelaStudio 13 화면 대비 (2026-09-26) — **Windows 재실행 통과(22:16, 테스트 236·빌드·GUI 스모크)**

- **요청**: `docs/ui-candela13-screen-review-2026-09-26.md`(이하 화면 검토)의 U1~U6을 반영한다. 구현만 하고 테스트는 실행하지 말 것. 따라서 이 항목의 동작은 **검증되지 않았다**. 문법 검사(py_compile)와 정의되지 않은 이름·`self` 속성 정적 검사만 했다.
- **작성 때는 GUI 코드를 실행하지 않았다**: 작업 환경(클라우드)에 PySide6를 설치할 수 없었다(패키지 저장소 접근 차단). 그래서 `ui.py`·`dialogs.py`·`pages.py`·`layout_view.py`는 Windows 테스트 전까지 import도 해 보지 않았다.
- **Windows 테스트 1차**(2026-09-26 21:34, 사용자 실행, `run_checks.cmd` 1단계): 236개 중 실패 1·오류 1. 22차 새 테스트(`test_ui_pages`, `test_ui_widgets`)와 나머지는 통과했다. 두 건을 고쳤고, 수정 뒤 Windows 재실행은 아직이다(클라우드에서는 Qt 없는 테스트만 통과 확인). 빌드·GUI 스모크는 테스트 실패로 실행되지 않았다.
  - 오류 `test_ui_gaps.test_object_dialog_focus_field`: 속성 대화상자가 이전·다음 이동용 비교 값을 만들려고 열 때 `values()`를 부른다. id 없는 record에서 KeyError가 났다. 비교 값을 만들 수 없으면 None으로 두고 "바뀐 것"으로 보게 고쳤다(`ObjectDialog._snapshot`).
  - 실패 `test_layout_presentation.test_pattern_groups_non_literal_fields`: 테스트 기대값이 틀렸다. 자리표시 묶음의 필드 수는 반복 항목 Items까지 센 3이 맞다(반복 안의 필드는 세지 않는다). 기대값을 고쳤다.
  - `QFontDatabase: Cannot find font directory …` 줄은 offscreen 테스트에서 PySide6가 글꼴을 싣지 않아 나오는 경고다. 22차 이전부터 있었고 결과와 무관하다.
- **Windows 재실행**(2026-09-26 22:16~22:22, `run_checks.cmd`): 테스트 236개 통과, 빌드 성공, GUI 스모크 통과(`build/checks/status.txt`: unittest_exit=0, build_exit=0, gui_exit=0).
- **U1**: 이 작업 전에 이미 코드에 있었다(속성창 갱신, 검증 표 머리, 인스턴스 표기, 이름 경로, 뒤로/앞으로, 문서 찾기, DID 열). 손대지 않았다.
- **원칙**: 새 화면은 기본이 표시 전용이다. 보존 원문(원본 CDD)에서 읽은 표는 읽기 전용이고 출력 의미에 쓰지 않는다. 출력 차단 규칙은 바꾸지 않았다. **스키마 변경 없음**(10 그대로).
- **오른쪽 영역**: 텍스트 속성창 하나를 탭 3개로 바꿨다. `데이터 배치`(DID·서비스), `세부 표`(마스터·디테일의 디테일), `속성`(기존 텍스트)이다. 선택한 객체에 맞는 탭이 앞에 온다. 목록 위에 페이지 머리를 두었다(장 소개 문장 또는 인스턴스 요약).
- **U2 데이터 배치·인스턴스 페이지**(`layout_view.py`, `presentation.py`):
  - 공용 위젯: 멤버 표(바이트, 비트, 이름, 데이터 타입, 기본값, 상수, 설명)와 바이트 × Bit7..Bit0 격자. 격자는 필드별로 색을 칠하고, 한 바이트 안 비트 필드는 칸을 나눈다. 반복 항목·가변 길이는 격자 끝의 `…` 행 하나로 줄인다. 하단 버튼으로 전체 / 멤버 표만 / 격자만을 고른다. DID와 서비스가 같은 위젯을 쓴다.
  - 서비스: 요청·긍정 응답 탭과 `NR (rc) n` 탭. 머리에 역할, 프로토콜 서비스, 패턴(`2E F1 90 zz` / `6E F1 90` / `7F 2E rc`)을 보인다. 자리표시는 데이터 필드 묶음마다 zz, yy, xx, ww… 순서로 붙인다. 탭 이름은 묶음이 필드 하나면 그 필드 이름, 여럿이면 `요청 데이터`/`응답 데이터`이고 자리표시를 함께 적는다.
  - 진단 인스턴스 노드 페이지: 머리(이름, 식별자, 클래스, 공유, 원본 변형, 경고)와 서비스 행(템플릿 순서, 역할, 사용 체크, 프로토콜 서비스, 요청·긍정·부정 패턴). DCLTMPL에 있지만 인스턴스가 쓰지 않는 서비스는 회색 `미사용` 행이다. CDD에 있지만 가져오지 못한 서비스는 `보존 원문` 행이다. 둘 다 읽기 전용이다.
  - 고장 메모리 인스턴스: 페이지 머리의 `고장 메모리 레코드` 버튼으로 읽기 전용 탭을 연다. `DtcStatusMask (zz)`(비트, 이름, 사용, 변환), `DTC 표 (xx)`, 스냅샷 레코드(공통 DID 포함), 확장 데이터 레코드(배치 표 + 격자). 편집은 화면 검토대로 DTCSTATUSMASK 의미 검토 뒤로 미룬다.
- **U3 공유 편집 선택**: 여러 변형이 쓰는 DID·서비스를 편집하면 `이 변형에서 사본으로 바꾼 뒤 수정`(기본) / `참조하는 모든 변형 수정`을 묻는다.
  - DID는 기존 `detach_did`를 쓴다.
  - 서비스는 새 명령 `detach_instance`로 인스턴스 전체를 현재 변형의 사본으로 바꾼다. 인스턴스·서비스·NRC·상태 조건·전이·전문 구조를 복사하고, DID 참조는 공유로 둔다. 사본의 원본 변형은 현재 변형이고, 클래스 안 순서는 원래 자리를 이어받는다. 한 명령이므로 Undo 한 번에 되돌아간다.
  - 보존 원문·원문 배치(raw)·미지원 서비스·고장 메모리 레코드가 걸린 인스턴스는 거부한다. 사본에는 원문의 출력 차단 정보가 따라가지 않기 때문이다.
  - 트리·표 메뉴에도 `현재 변형에서 인스턴스를 사본으로 바꾸기`를 넣었다. 스키마는 바꾸지 않고 기존 `owner_variant_id`·`variant_instances`로 처리했다. 화면 검토는 U3을 설계 검토 대상으로 두었으나, 별도 검토 없이 구현했다.
- **U4 트리·개요**:
  - 트리 중간 노드: `지원 인터페이스`(인터페이스별 통신 파라미터), 변형 아래 `식별 특징`/`식별 패턴`, `지원 진단 클래스`(클래스·싱글턴·Job 컨테이너를 그 아래로 옮김), `상태` 아래 `상태 그룹`/`의존성`/`기본 상태 전이`, `부정 응답 코드` 아래 `미지원 서비스 NRC`. 이름 경로에는 중간 노드를 넣지 않는다(CANdela npath와 같다). 트리 최상위 순서가 바뀌어 `test_ui_tree`의 기대값을 고쳤다.
  - 상태 그룹 마스터·디테일: 목록(종류, 부정 응답, 상태, 설명)과 `세부 표`의 상태(초기 상태 먼저). 트리의 그룹 노드는 유지하고 `상태 그룹` 아래로 옮겼다.
  - Shortcut Qualifier 행렬: 행 = shortcut, 열 = 변형. 변형 선택, 모든 변형에 있는 행 숨기기, 검색, 표 복사를 넣었고 두 번 누르면 이동한다.
  - Compact View: 고른 변형에서 Base와 공유하지 않는 인스턴스만 트리로 보인다.
  - 의존성: 검색 줄의 콤보로 `서비스별`(기본, 편집) / `클래스별 묶음`(개요)을 고른다. 묶음은 클래스·역할(`Sessions / * / Start`)로 묶는다. 구성원 값이 다르면 `(혼합)`이고, 두 번 누르면 펼친다. 여러 행을 골라(묶음 행은 구성원 전부) F2를 누르면 한 상태 칸을 허용/차단/전이/전이 없음으로 일괄 변경한다(새 명령 `bulk_conditions`, Undo 한 번). 조건이 없던 서비스(제한 없음)에 차단·전이를 정하면, 그룹의 모든 상태를 허용으로 채운 뒤 그 칸만 바꾼다.
- **U5 보존 원문 읽기 전용 표시**(`source_views.py`): 저장된 원본 CDD를 안전 파서로 다시 읽는다. 결과는 원본 해시·언어별로 캐시한다.
  - 표: 인터페이스·통신 파라미터(이름, 분류, 기본값, 덮어쓴 값, 적용값, 설명), 식별 특징, 변형별 식별 패턴(변형 목록에도 패턴 열), 지원 진단 클래스 체크 목록(템플릿 클래스 전부, 쓰지 않는 클래스는 해제), Job 표, 기본 상태 전이(Defaults: 템플릿 서비스 × 상태, ✓/✗/—), 미지원 서비스 NRC, 프로토콜 서비스 패턴 열(`10 LL` / `50 LL zz` / `7F 10 rc`)과 사용·필수 열, Revision History(작성자·이력 탭).
  - 원본 CDD가 없는 프로젝트(직접 만든 프로젝트)는 해당 노드에 안내 행만 보인다.
  - 해석 규칙과 테스트 기대값(클래스 17개 중 Download·Memory 해제, 미지원 서비스 NRC 0x11·0x12, 이력 14건 등)은 ABS_ESP 예제 원문으로만 맞췄다.
- **2.1 문서 수준 표**:
  - ECU 페이지: ECU 행을 자동 선택해 원본 파일·형식 버전·가져온 시각, revision, 객체 개수 요약을 보인다.
  - DTC: 원본 DTC 속성(`cdd.*`)을 열로 추가했다. 열 머리는 레코드 항목 템플릿 이름이다(예: Set Condition). 검색 줄의 DTC 표시 콤보로 0x 16진(기본) / SAE 문자형(`C002011`)을 고른다. SAE는 표시 전용이고 원시값은 툴팁에 함께 보인다. 선택은 사용자 설정(QSettings)에 남는다.
  - DID 개요: `사용`, `DID 데이터` 열.
  - 데이터 타입: 길이를 `바이트:비트`로 보인다(`2:0`, 배열 `1:0 [17]`, 범위 `1:0 [2..7]`). 변환 종류 아이콘을 넣고, 템플릿에서 온 타입은 회색으로 표시한다. 속성 창에 사용처 수, 길이, 템플릿 여부, 원본 변환식(`phys = 1 / 16 × raw + 0`)을 보인다.
- **U6**:
  - 속성 대화상자: 데이터 타입은 일반 / 변환 / 예외값 / 열거값 / Packet 구조 / 속성(n) 탭이다. 변환 탭에 원본 CDD 수식과 미리보기를 둔다. 객체 대화상자에 `◀ 이전` / `다음 ▶`을 넣었다(바뀐 값이 있으면 적용한 뒤 목록의 이웃 객체를 연다).
  - 사용 중 타입 보호: 사용 수가 0보다 크면 인코딩·비트 길이·배열 길이·packet 멤버를 잠그고 이유를 보인다. 대화상자 수준의 보호이고, 명령 쪽 검사는 기존 그대로다.
  - 표 끝 새 항목 행: 누르면 기존 추가 대화상자를 연다. 읽기 전용 목록과 읽기 전용 프로젝트에는 없다.
  - 장 노드 소개 문장을 페이지 머리에 보인다. Previous/Next 페이지 이동은 화면 검토대로 넣지 않았다.
  - 작업 기록: 시각·종류(오류/경고/정보)·내용 표, 종류·내용 필터, 지우기.
  - Rework 목록(프로젝트 메뉴): 완료, 내용, 연결 경로. ECU의 앱 속성 `app.rework`(JSON)에 저장하므로 저장·Undo를 따른다. ODX로 내보내지 않고, 속성 표시와 Attribute 개요에서도 숨긴다.
- **결정**(화면 검토 6절):
  - 1·2(싱글턴 `[00]` 없음, 이름 경로 기본): U1에서 이미 반영돼 있다.
  - 3(DTC 표시): 0x 16진이 기본이고 SAE 문자형은 옵션이다.
  - 4(트리 상태 그룹 노드): 노드를 유지하고 `상태 그룹` 중간 노드 아래에 둔다. 한 페이지 마스터·디테일도 함께 제공한다.
  - 의존성의 `(yes)`/`(no)` 괄호 표기는 의미(템플릿 기본값 상속으로 추정)를 도움말로 확인하지 못해 따르지 않았다. 묶음 행은 ✓/✗/(혼합)만 보인다.
- **작업 스레드 API**: 조회 `compact_tree`, `shortcut_matrix`, `revision_history`, `fault_memory_view`, `rework_items`를 추가하고 `workers.py` 허용 목록에 넣었다. `query_objects`에 새 목록 종류(인터페이스 등 보존 원문 표, 상태 그룹, 템플릿 서비스 행)와 페이지 머리(`page_info`)를 더했다. 새 명령은 `detach_instance`, `bulk_conditions`, `set_rework`이다.
- **GUI 스모크**(`build/smoke_cdd_gui.py`, 실행하지 않음):
  - DID를 고른 뒤 텍스트 속성창 대신 `데이터 배치` 멤버 표에서 `Acceleration`과 `×0.1 -13`을 확인한다. 속성 텍스트는 이제 뒤쪽 탭에 있다.
  - 18:35 실행의 `경로 입력창 응답 시간 초과` 실패에 대응했다. 포커스로 찾지 못하면 automation id가 `objectPath`로 끝나는 편집 상자를 찾는다.
- **테스트(작성만 하고 실행하지 않음)**: 새 파일 `test_layout_presentation.py`(자리표시·비트 위치·격자), `test_ui_pages.py`(보존 원문 표, 페이지 데이터, 명령과 Undo), `test_ui_widgets.py`(Qt 위젯). 고친 파일 `test_ui_tree.py`(트리 순서·중간 노드), `test_ui_navigation.py`(패턴 열, DID 배치). 기대값 자체가 틀렸을 수 있다.
- **파일**: 새 `diagstudio/layout_view.py`, `pages.py`, `source_views.py`. 수정 `application.py`, `ui.py`, `dialogs.py`, `presentation.py`, `workers.py`, `pyproject.toml`(pyside6-project 파일 목록), `build/smoke_cdd_gui.py`.
- **남은 것·한계**:
  - 먼저 `build\run_checks.cmd`로 테스트 → 빌드 → GUI 스모크를 돌린다. 실패하면 이 항목의 코드부터 의심한다.
  - 인스턴스 페이지에는 자리표시 링크(누르면 해당 탭), 표 안 직접 멤버 편집, 미사용 템플릿 서비스를 체크로 켜는 편집이 없다. 켜는 편집은 템플릿 의미 매핑이 증명될 때까지 막는다.
  - 기본 상태 전이(Defaults)는 표시만 하고 새 서비스에 적용하지 않는다.
  - 보존 원문 해석은 ABS_ESP 예제 기준이다. 다른 템플릿이나 이전 형식(2.x 등)에서는 표가 비거나 일부 열이 빈칸일 수 있다.
  - 원본 CDD 전체를 처음 한 번 파싱하므로, 큰 CDD에서 해당 노드를 처음 열 때 느릴 수 있다. 측정하지 않았다.
  - U3(인스턴스 사본)은 공유 의미를 바꾸는 명령이다. 설계 검토를 받지 않았다.

## 21차(Claude): cdd_utils 대비 공백 서비스 가져오기 — 미검증 표시, MUX, 스키마 10 (2026-09-26) — **테스트 미실행**

- **요청**: cdd_utils가 만들지만 우리가 원문 보존만 하던 서비스를 가져온다. 코드만 수정하고 테스트는 실행하지 말 것. 따라서 이 항목의 동작은 **검증되지 않았다**. 문법 검사(py_compile)만 했다. 다음 작업에서 자동 테스트·코퍼스 시험을 먼저 돌려야 한다.
- **원칙**: 바이트 배치는 CDD 그대로 가져온다. ODX 대응을 예제·도움말로 확인하지 못한 의미는 객체를 **미검증**(`objects.supported=0`, 속성 `cdd.unverified`에 사유)으로 둔다. 그러면 VAL-012로 해당 변형의 출력이 막힌다. 조용히 추측해 출력하지 않는다.
- **추가한 것**:
  - **0x2A 주기 응답**(PROTOCOLSERVICE `periodicresp=1`): 긍정 응답이 SID 없이 주기 식별자부터 오는 배치를 원문 배치로 가져온다. 미검증이고 속성 `cdd.periodic_response`를 남긴다.
  - **8비트보다 큰 비트필드 컨테이너**(16·24·32비트, big endian만): 8비트와 같은 규칙(첫 멤버 = 최하위 비트)으로 가져온다. 미검증. little endian 컨테이너, packet·MUX 케이스 안의 큰 비트필드는 계속 보존한다.
  - **KWP2000 16비트 DTC 그룹·DTC 자리**: 16비트 값 필드로 가져온다. 24비트 DTC 표(DTC-DOP)와 연결하지 않는다. 미검증.
  - **authorization·spec 없는 상태 그룹**: `state_groups.kind`에 `authorization`, `other:<qualifier>`를 추가했다. 그룹을 미검증으로 두고 상태·조건·전이를 가져온다(UDS 13 템플릿 0x11 Hard/Soft Reset 등). ODX 출력은 세션·보안 외 그룹의 STATE-CHART를 차단한다.
  - **템플릿 부정 응답 SID 불일치**(Vector 파일 자체의 불일치, 예: CANSystem REI): 가져오고 속성 `cdd.negative_response_sid`를 남긴다. 미리보기·출력은 요청 SID를 쓴다. 미검증.
  - **respOnPhys/respOnFunc=0**(응답 없는 주소 방식): 속성으로 남기고 가져온다. 미검증.
  - **MUX**(MUXDT를 가리키는 슬롯·CONTENTCOMP 데이터 객체 + `dataObjectRef` 선택자, 템플릿 MUXCOMP + 인스턴스 MUXCOMPCONT 지역 MUXDT + `selref` 선택자): 새 구조 멤버 종류 `mux`와 표 `muxes`(선택자 데이터 객체, 기본 구조)·`mux_cases`(값 범위별 케이스 구조)를 만들었다.
    - 규칙: MUX는 전문의 마지막 필드이고 반복 항목 안에 둘 수 없다. 선택자는 같은 전문의 앞쪽 데이터 필드다(VAL-005, 가져오기에서도 같은 규칙으로 보존).
    - 미리보기: 선택자 값에 맞는 케이스(없으면 기본 구조)를 이어서 인코딩한다.
    - ODX 출력: 차단한다(도움말 110의 MUX 대응은 응답 MUX만 언급하고, 요청·선택자 참조 형태를 확인하지 못함).
    - 대상: 0x23·0x3D·0x34·0x35의 주소·크기. 0x2C 02(반복 항목 안 MUX)는 계속 보존한다.
- **스키마 10**: `structure_members.kind` = `mux`, `state_groups.kind` CHECK 확장(두 표를 다시 만든다), 표 `muxes`·`mux_cases`. 9→10 마이그레이션과 픽스처 `schema_v9.sql`(PC의 9판)을 추가했다. 기존 마이그레이션 테스트의 기대 목록에 10을 더했다(실행하지 않음).
- **연결 코드**: 출력 범위 검사(`export_scope`)·참조 영향(`references`)·파일 비교(`comparison`)가 MUX 선택자·케이스 구조를 따라간다. 속성 창 배치 표시에 MUX 케이스 목록이 보이고, 미리보기 입력에 모든 케이스의 데이터 객체가 나온다.
- **아직 보존**: 0x2C 02 Define by Address(반복 안 MUX), 12.x ECU 수준 FAULTMEMORY + DOMAINDATAPROXYCOMP, 길이 접두 필드(lsz*bytes), 수식·특성 곡선 타입.

## 20차(Claude): cdd_utils 재검토 반영 — CANdela 2.x 형식, packet 타입(STRUCTDT) (2026-09-26)

- **재검토 대상**: `C:\work\setting\cdd_utils`(xsdata 바인딩 기반 읽기 전용 파서). 19차 이후 바인딩(`models/candela.py`)이 갱신되어 이제 우리 시험 문서를 모두 읽는다. 또 cantools 테스트 CDD 4개(`examples/web_samples`)가 추가됐다. 코드는 복사하지 않고, 동작과 시험 문서만 참고했다.
- **참조해 반영한 것**:
  - **CANdela 2.x 형식**: cantools 테스트 CDD는 형식 2.0.5다. 요소·속성 어휘를 5.x~13.x와 비교하니 같았다(추가된 것: UNION, RECORDITEMTMPL, 일부 id 속성). 그래서 형식 판정을 주 버전 2~13으로 넓혔다. 3.x·4.x는 파일로 확인하지 못했다. 2.0.5 파일 3개가 열린다(서비스 20~22개). BCD 타입의 비트 마스크는 2.0.5 파일이 길이와 무관하게 0xFFFFFFFF를 쓴다. 그래서 타입 길이를 덮으면 전체 마스크로 본다(정수 타입과 같은 판정).
  - **packet 타입(STRUCTDT)**: 멤버(DATAOBJ·8비트 STRUCT 비트필드)를 해석해 packet 타입으로 가져온다. 근거: 도움말 110 표 30 Packet → STRUCTURE. 멤버 비트 합이 CVALUETYPE 길이와 같아야 한다. 여러 바이트 packet의 STRUCTDT 수준 `bo=12` 의미는 근거가 없어, 1바이트이거나 `bo=21`인 것만 가져온다. 이로써 0x28 CommunicationControl(CommunicationType 비트필드)이 들어온다. `28 01 01` = normalCommunicationMessages(bit 0)이다. cdd_utils는 이 비트 순서를 반대로 디코딩한다. ISO 14229-1과 ABS 예제 근거는 이쪽이다.
  - **시험 문서**: `VectorCorpusTest.test_extra_documents_open`이 `DIAGAUTHOR_EXTRA_CDD_DIRS`(기본 `C:\work\setting\cdd_utils\examples`)의 CDD를 모두 열어 본다. CANdela 문서가 아닌 수제 파일(Synthetic.cdd, doctype 없음)은 거부가 정상이다.
- **결과**: Vector 공용 문서 18개의 서비스 합계는 656 → 670이다(Vector_UDS_13.0.cddt 68 → 72, UDS-ExampleEcu-5.1.0 100 → 104 등). CddTools 69, cantools 10.0.108 8, 2.0.5 20~22. 모든 파일의 검증은 VAL-012·INF-001뿐이다.
- **참조하지 않은 것(사유)**:
  - SAE J2012 DTC 표시(P0123 형식): 표준이지만 ECU가 어떤 DTC 형식을 쓰는지 CDD만으로 알 수 없다. Vector ODX 출력(ODXEnvelope)은 `DISPLAY-TROUBLE-CODE`를 16진으로 낸다.
  - DTCSTATUSMASK `conv`(req/optyes/optno) → 지원 여부 해석: cdd_utils의 추정이다. 근거 대조가 필요한 의미라 high 검토로 넘긴다.
  - 응답 디코딩(`decode_values`): 원시값만 읽고 비트필드 순서가 틀려 참고할 수 없다.
  - MUXDT·MUXCOMP(0x23·0x34·0x2C 02, 39개 서비스)와 12.x ECU 수준 FAULTMEMORY + DOMAINDATAPROXYCOMP(srn/sd/edrn/ed): cdd_utils의 구조 해석(선택자 dtref, 기본 STRUCTURE, CASE s/e / dest별 레코드 연결)을 향후 모델 설계 참고로 기록한다. 모델·ODX 근거가 필요해 이번에는 구현하지 않았다.
- **남은 cdd_utils 대비 공백**(cdd_utils가 만들지만 우리는 보존하는 서비스): 0x2A 주기 응답 30, 16비트 비트필드 22, MUX 39, KWP 16비트 DTC 그룹 11, 인증 상태 그룹 2, 템플릿의 부정 응답 SID 불일치(Vector 파일 자체의 불일치) 6.
- **테스트**: 183개 중 UI 29개를 건너뛰고 나머지는 통과했다(Linux, Qt 없음). Windows 확인은 아직 하지 않았다.

## 19차(Claude): Vector 공용 문서의 모든 CDD/CDDT 가져오기, 가변·BCD 타입, 스키마 9 (2026-09-26)

- **이어받은 상태**: 17차(출력 범위 검사·출처 정보 분류)와 18차(Tier-1 UI 보완)를 검토하고 그 위에 작업했다. 두 작업이 17차에서 다룬 출력 범위·PROTOCOLSTANDARD/DOCTMPL/VCKMGR는 건드리지 않았고, EVENTDATA도 17차 판단대로 차단을 유지한다. UI 테스트는 요청에 따라 실행하지 않았다.
- **대상**: `C:\Users\Public\Documents\Vector` 아래 CDD/CDDT 22개(중복 제외 18개, 형식 5.0.100~13.0.103)를 미리보기 → 가져오기 → 검증 → 저장 → 다시 열기로 돌렸다. 이번에 새로 찾은 파일은 Ethernet·FlexRay·LIN·MOST·J1939·IO_HIL·Medical 예제의 UDS-ExampleEcu 4.0.2/4.0.4/7.0.1, UDS_On_J1939_Ecu, Test.cdd, kwp2000-bed, CD-Changer다. 관련 파일(.cdi·.pdx·job)은 CDD 옆에 없었다.
- **열기 실패 1건 수정**: IO_HIL `Test.cdd`(6.5.120)는 ECU 이름의 en-US TUV가 비어 있고(`uptodate="0"`) de-DE에만 값이 있다. 그래서 빈 이름으로 프로젝트 생성이 거부됐다. 이제 빈 TUV는 번역 없음으로 보고 모든 객체의 이름 후보에서 뺀다.
- **가져오기 확장**(근거가 있는 것만):
  - CONTENTCOMP: 템플릿에 내용이 고정된 구성요소. 슬롯과 같은 데이터 객체 나열이다.
  - 고정 길이 바이트 필드: IDENT uns 8비트, qty=field, minsz=maxsz → bytes 배열.
  - 가변 바이트 필드: 최대 길이 없는 `dest=any` 자리와 IDENT uns field(minsz≠maxsz) → PDU 끝까지, 최소·최대 길이. 근거는 UDS-ExampleEcu.pdx의 DtcSnapshotRecordNumber·ASCII_17(MIN-MAX-LENGTH-TYPE, END-OF-PDU)이다. 전문의 마지막 필드일 때만 쓰며, 아니면 서비스를 원문 보존한다.
  - packed BCD: IDENT enc=bcd, 32비트 이하. 근거는 도움말 110 표 31(numBCD → A_UINT32, BCD-P)이다.
- **결과**: 18개 모두 열린다. 가져온 서비스 합계는 550(17개, Test.cdd 실패) → 656(18개)이다. 예: UDS-ExampleEcu-5.1.0 64 → 100, 5.0.4 61 → 93, Vector_UDS_13.0.cddt 57 → 68, UDS-ExampleEcu-4.0.2 28 → 32. 모든 파일의 검증 결과는 VAL-012·INF-001뿐이다. 새로 가져온 서비스(0x19 01, 0x2C 01, 0x36, 0x22 BCD 등)의 요청·응답 전문은 cdd_utils(`C:\work\setting\cdd_utils`)의 기본값 인코딩과 바이트 단위로 같다.
- **남은 주요 미지원**:
  - 16비트 비트필드 컨테이너(근거 없음)와 16비트 DTC 그룹(KWP).
  - 0x2A 주기 응답(SID 없는 응답)과 MUXCOMP(0x23·0x34 주소·크기).
  - 인증 상태 그룹(authorization)과 길이 접두 필드(lsz*bytes).
  - 수식·특성 곡선·STRUCTDT·MUXDT 타입.
- **스키마 9**: `data_types`에 `min_length`·`max_length`(가변 바이트 필드)와 `bcd` 인코딩을 추가했다. CHECK 변경이라 표를 다시 만든다(8→9 마이그레이션, 픽스처 `schema_v8.sql` = PC의 8판 `schema.sql`). 가변 필드가 전문 마지막이 아니거나 반복 항목 안에 있으면 VAL-005다. ODX는 가변 필드 → `MIN-MAX-LENGTH-TYPE TERMINATION=END-OF-PDU`, BCD → `BASE-TYPE-ENCODING=BCD-P`로 낸다. 타입 편집 창은 bytes의 배열 길이를 비우면 가변 필드로 만든다.
- **기타**: 미리보기 입력에 반복 항목의 데이터 객체를 포함한다(0x2C 01 SourceDID 등). 가변 필드는 배치 표시에 "가변 길이 · 최소 N바이트"로 보인다. ABS 미사용 타입은 13 → 8이다(HexDump_5_Byte·BCD 4개를 가져옴).
- **테스트**: 새 테스트는 다음과 같다.
  - `VectorCorpusTest`: 환경 변수 `DIAGAUTHOR_VECTOR_PUBLIC`(기본 `C:\Users\Public\Documents\Vector`)의 모든 CDD/CDDT. 저장소에 복사하지 않는다.
  - `TemplateComponentTest`, `VariableFieldRuleTest`, `EmptyTranslationTest`.
  - 스키마 8→9 마이그레이션.
  - Linux(Qt 없음)에서 182개 중 UI 29개를 건너뛰고 나머지는 통과했다. Windows 확인은 아직 하지 않았다.

## 18차: 데이터 타입·프로토콜 서비스·NRC Tier-1 UI 보완 (2026-09-26)

- **근거**: [docs/04](docs/04-ui-specification.md) UI-06·UI-10·UI-12와 CANdela 13.0 도움말의 화면 배치·용어·읽기 전용 규칙을 기준으로 삼았다. 근거를 확인하지 못한 항목은 제외했고 스키마 마이그레이션은 없다.
- **NRC → 사용 서비스 역방향 이동**: `export_scope.py`와 같은 `service_nrcs`↔`nrcs` 코드 조인으로 NRC를 쓰는 서비스를 찾아 참조 영향 대화상자와 관련 대상 메뉴에 연결했다. `protocol_service`→서비스 연결은 신뢰할 수 있는 영속 링크가 없어(가져오기 시 임시 카운트만 존재) 구현하지 않고 보고-제외로 기록했다.
- **읽기 전용 표시 정직성**: 공유하던 안내 문구를 나눴다. NRC는 VAL-011·ODX 역할을 명시하고, `protocol_service`는 출력 중립 안내를 유지한다. 추가/편집/삭제는 종류별로 구분해 nrc·protocol_service에 실제 사유를 표시한다.
- **표**: 죽어 있던 정렬을 고쳤다(byte_order, conversion, unit, request/positive/negative text, usage). enum 셀은 대화상자 용어로 지역화했고, 검색은 telegram/type 텍스트까지 포함한다. NRC 사용 횟수 열과 데이터 타입 사용 열을 추가했다.
- **편집기**: Packet 탭은 packet 종류에만 표시한다. 서비스 NRC 선택기는 NRC 카탈로그를 사용하며 `values()` 형태는 그대로다. 타입 → DID 직접 이동 메뉴를 추가했다.
- **명시적으로 제외**: CANdela 전용 열·아이콘, pool 편집, 상태 그룹 NRC 아이콘, 템플릿 편집, P1/P2 타입, ODX 의미론.
- **변경 파일**: `diagstudio/ui.py`, `application.py`, `references.py`, `presentation.py`, `dialogs.py`; 테스트 `test_ui_gaps.py`, `test_ui_navigation.py`, `test_cdd_behaviour.py`.
- **검증**: 172개 테스트 통과(신규 9개: test_cdd_behaviour 3, test_ui_navigation 1, test_ui_gaps 5). `run_checks.cmd nopause` 2026-09-26 10:29:16~10:33:31 실행, unittest_exit=0, build_exit=0, gui_exit=0(GUI 스모크는 export_preflight·types_node·type_copy_undo_redo 단계 포함 통과). py_compile 이상 없음.

## 17차: Claude 후속 — ODX 출력 범위 검사·보존 정보 분류 (2026-09-26)

- **이어받은 상태**: 16차의 이전 CDD 형식·CDDT 지원을 유지했다. 마지막 기록은 153개 테스트·빌드 성공, GUI 실패였으므로 화면 검사를 별도로 완료해야 하는 상태였다.
- **출력 범위 검사**: 선택 변형의 DID·DTC·활성 서비스·중첩 구조·타입·상태·고장 메모리·Job 의존 관계를 따라 검사한다. 다른 변형과 비활성 서비스의 독립 항목은 출력 차단에서 제외하되 보고서에 사유를 남긴다. 전체 프로젝트 검증은 종전처럼 모든 문제를 표시한다. `가져오기 / 내보내기 → 현재 변형의 ODX 사전 검사`에서 스키마 파일 없이 이 범위를 확인한다. 범위와 revision, `범위 밖` 여부가 검증 표에 표시된다.
- **소유 UUID가 없는 원문 타입**: 저장된 원본 CDD의 ID/참조 관계를 분석한다. 원본 전체에서 사용하지 않는 타입과 그 타입끼리의 참조만 제외한다. 미사용 프로토콜 템플릿의 참조도 사용으로 취급하는 보수적 방식이다. ABS의 미지원 타입 14개 중 **13개**가 제외되고, 템플릿에서 참조하는 MUXDT는 남는다. 원본이 없거나 XML 분석·ID 식별이 불가능하면 차단을 유지한다. 출처 BLOB은 출력 검사 스냅샷에서만 읽는다.
- **비차단 정보 오류 수정**: 이전 `export_odx`는 정보 메시지를 포함해 검증 항목이 하나라도 있으면 차단했다. 이제 `blocks_export`로 판단하고, 출력 보고서에 전체 사전 검사와 범위 제외 사유를 저장한다. 선택 범위에 사용하는 미지원 의미는 계속 차단한다. 제한된 상태 조건·32비트 초과 정수·데이터 상수·32비트 초과 고정값도 사전 검사에 표시한다.
- **출처·비활성 정보**: 설치 ABS 예제에서 확인한 `PROTOCOLSTANDARD=UDS`, `DOCTMPL` 출처 정보, `VCKMGR(vckmode=none, 숫자 설정=0)`를 ECU 속성으로 가져오고 원문을 비차단으로 보존한다. 확장 속성·자식 또는 활성 코딩 설정은 차단을 유지한다. EVENTDATA·DTCSTATUSMASK·UNSUPPSRVNEG·DTC 풀의 의미는 이번에 재분류하지 않았다. 새 분류는 CDD를 다시 가져올 때 적용된다. 기존 `.diagproj` 원문은 자동 변경하지 않는다. 스키마 8 유지.
- **한계**: 공식 ODX 2.2.0 스키마와 외부 도구 대조는 여전히 필요하다. 테스트용 완화 스키마는 출력 흐름·보고서·차단 검사를 위한 것이며 ODX 적합성을 증명하지 않는다. ABS 전체 출력은 상태 제한·고장 메모리 레코드·문서 전역 미지원 항목 등으로 계속 차단된다.
- **실행 결과**: 최종 Windows 검사 후 이 항목에 기록한다.

## 16차(Claude): 이전 CANdela 형식 CDD 열기, 보존 항목 검토 (2026-09-26)

- **이전 형식 지원**: CANoe 13 예제(`Sample Configurations 13.0.172\CAN`)의 CDD 6개가 "버전이 달라" 열리지 않았다. 형식 버전은 5.0.100(CANSystem), 5.0.111(kwp2000-seat), 6.0.120(CANSystemDoor), 12.0.104(UDS-ExampleEcu-5.0.4, SampleUDS), 13.0.103(UDS-ExampleEcu-5.1.0)이다. 모든 파일의 요소·속성 이름을 CANdela 13 예제(13.0.101)·템플릿(13.0.100)과 비교했다. 이전 형식도 같은 어휘를 쓰고 추가된 것만 있었다: STATICCOMP@bl(5.x), STATUSDTCPROXYCOMP@dtref, SERVICE@act, FLTDEF·VALTBL·IMAGEPROXY 등. 6.0.120 Door CDD의 타입을 CANdela 5.0 ODX 출력(UDS-ExampleEcu.pdx, ECU "Door")의 DOP와 대조하니 19개 중 18개가 길이·변환·계수까지 일치했다. 나머지 1개는 문서 개정 차이로 보이는 상태 비트 라벨(on/off ↔ true/false)이다. 그래서 형식 판정을 "CANdela 주 버전 5~13"으로 넓혔다. 14 이상(새 형식, 도움말상 Converter 필요)과 5 미만은 거부한다. 모르는 요소는 이전처럼 원문 보존·출력 차단한다.
- **형식 차이 해석**: 5.x의 `STATICCOMP@bl`(타입 참조 대신 비트 길이, CONSTCOMP@bl과 같은 의미)을 쓴다 — kwp2000-seat 서비스 6 → 14. `SERVICE@act="0"`(인스턴스에서 비활성화한 서비스)은 `enabled=0`으로 가져온다. 도움말 80_ODXExport "Deactivated Services ... are not exported to ODX"에 따라 ODX 출력에서 빠진다. ECU 속성 `cdd.dtd_version`에 형식 버전을 남기고, 미리보기 창에 이전 형식임을 알린다.
- **결과**: 6개 모두 열린다. 서비스 수: CANSystem 3, CANSystemDoor 3, UDS-ExampleEcu-5.0.4 61, 5.1.0 64, SampleUDS 24, kwp2000-seat 14. 모두 외래 키 검사를 통과했고 검증 결과는 VAL-012·INF-001뿐이다. 테스트 `OlderFormatTest`(설치 위치에서 읽음, 저장소에 복사하지 않음, `DIAGAUTHOR_CANOE_SAMPLES`로 경로 변경 가능)를 추가했다.
- **보존 항목 검토**: [docs/16](docs/16-preserved-items-review.md). 핵심은 VAL-012가 명세("출력 폐쇄 그래프에 의미 있는 미지원 노드")와 달리 문서 어디에 있든 전체 출력을 막는다는 점이다. ABS의 차단 27개 중 사용하지 않는 데이터 타입이 14개다. 권장 순서: ① 출력 범위 기반 차단 ② 메타데이터·단순 항목 정리(PROTOCOLSTANDARD·DOCTMPL·VCKMGR·EVENTDATA 등) ③ 변형 식별(ECU-VARIANT-PATTERN) ④ DTC 풀·템플릿 제약 ⑤ BCD·수식·packet·특성 곡선·MUX 타입.

## 15차(Claude): 14차·DeepSeek 작업 검토, CDDT 템플릿 가져오기 (2026-09-26)

- **검토 — 14차(참조 영향·타입 독립 복사·삭제 사전 검사)**: 역참조 그래프, SAVEPOINT 삭제 검사(항상 되돌림, 변경 기록이 꺼진 상태라 실행 취소 이력에 남지 않음), 타입 복사의 순환·원자성 검사를 확인했다. 한 곳 고침: `test_reference_ui.py`가 PySide6를 무조건 가져와 Qt가 없는 환경에서 모듈 전체가 오류였다. 다른 Qt 테스트처럼 건너뛰게 했다. 직전 Windows 실행(09-26 03:54)은 unittest 0·빌드 0, GUI 스모크는 새 타입 복사 단계에서 실패(StopIteration)한 뒤 스크립트만 고쳐진 상태였다.
- **검토 — DeepSeek(CDDT)**: `.cddt`를 열기·가져오기 경로로 보내는 UI(`CDD_SUFFIXES`, 대화상자 필터)와, 비SQLite 파일을 `INVALID_PROJECT`로 돌려주는 `persistence` 수정은 유지했다. 다만 문서는 "선택할 수 없음"이라 쓰고 대화상자는 `.cddt`를 보여 주며, 가져오면 확장자만 보고 거부하는 중간 상태였다.
- **CDDT 구현**: 설치 예제 템플릿 3종(Vector_UDS_13.0, Vector_KWPonCAN_1.3, Vector_UDSonLIN_1.1)을 분석했다. CDD와 같은 CANDELA 형식이고 `ECUDOC@doctype="tmpl"`만 다르며, 프로토콜 서비스·DCLTMPL·타입·상태 그룹·NRC·DID와 기본 변형(기본 인스턴스·서비스)을 담는다. 도움말(Data Model Overview "Template and Document", Getting Started "File|New ... specify the CANdela template")대로 **템플릿을 바탕으로 새 프로젝트를 만드는 가져오기**로 구현했다. 해석 경로는 CDD와 같고, 원본 바이트를 보존하며, ECU 속성 `cdd.document_type`·`cdd.template_oid`와 원본 형식 버전("13.0.100 CDDT")으로 출처를 남긴다. 미리보기 창 제목·설명이 템플릿임을 알린다. 문서 판정: dtdvers 13.0.100(Vector_UDS_13.0 템플릿)·13.0.101, doctype inst/tmpl만 받고 확장자와 문서 종류가 다르면 거부한다.
- **함께 넓힌 해석**(템플릿에서 드러났고 CDD에도 적용): 데이터 타입의 사용자 속성(ENUM 등 — UDS 템플릿 23개, UDSonLIN 17개 타입이 이것 때문에 보존만 되었다), DTC 레코드의 부호 없는 정수 항목(UNSRECORDITEM, 예: DTC_PRIORITY_VALUE), 서비스 Semantic 속성(도움말 80_ODXExport: ODX DIAG-SERVICE SEMANTIC을 직접 지정 — 클래스 유형 대응보다 우선).
- **결과**: UDS 13.0 템플릿 → 타입 84·DID 11·서비스 57·인스턴스 33·프로토콜 서비스 106, 검증은 VAL-012(보존 원문)·INF-001만. KWPonCAN·UDSonLIN도 가져오기·외래 키 검사 통과. ABS 예제 결과는 그대로.
- **남은 것**: CDDT 쓰기, 템플릿 편집, 기존 프로젝트에 새 템플릿 적용(문서 업그레이드). 템플릿에서 드러난 미지원 구성(authorization 상태 그룹, CONTENTCOMP·MUXCOMPCONT, STRUCTDT·MUXDT, 32비트 부분 비트 마스크 등)은 CDD와 같이 원문 보존·출력 차단.

## 14차: 참조 영향·타입 독립 복사·삭제 사전 검사 (2026-09-25)

- **참조 영향**: 프로젝트 메뉴 및 객체 우클릭에서 직접 사용처와 간접 영향을 조회한다. 데이터 타입 → 데이터 객체 → 중첩 구조/packet → DID·고장 메모리·인스턴스·서비스 → 변형을 추적한다. 상태 조건·전이도 포함한다. 행에서 객체로 이동하고 변형별 영향을 확인할 수 있다. 미지원 원문 내부의 참조 의미는 해석하지 않는다.
- **타입 독립 복사**: 타입 편집 전 영향 목록에서 공유 타입 수정 또는 독립 복사를 선택한다. 별도 메뉴로 사용처 없는 타입도 복사할 수 있다. 하위 데이터 타입·구조·데이터 객체, 기본값·상수·열거값·예외값·번역·출처·속성을 새 UUID로 복사한다. 내부에서 반복 참조한 타입은 사본 안에서도 하나를 공유한다. 한 명령이므로 Undo/Redo·재시도와 저장/재열기가 적용된다. 기존 DID·서비스의 연결은 자동 변경하지 않고, 사용자가 데이터 구조에서 새 타입을 선택한다.
- **복사 제한**: 읽기 전용·미지원 객체, 해석하지 못한 의미가 연결된 객체, DID/DTC·고장 메모리를 외부 참조하는 packet은 복사하지 않는다. 실패 시 사본 일부도 남기지 않는다. 새 스키마는 필요하지 않아 버전 8을 유지한다.
- **삭제 전 검사**: 실제 삭제 경로를 SAVEPOINT 안에서 실행한 후 항상 되돌려, FK·읽기 전용·초기 상태·공유 객체 제약을 확인한다. 삭제 가능한 경우에만 적용 버튼을 표시하고 제거할 객체 수와 참조 영향을 안내한다. 검사 자체는 객체·revision·실행 취소 이력을 바꾸지 않는다. DID/DTC 삭제는 현재 변형의 소속과 잠금도 검사한다.
- **검토 중 수정**: 13차의 분수 지원 이후 온도 예제 표시가 `25.0`에서 `25`로 바뀐 회귀를 복구했다. 물리값 표시가 계수 입력의 자리수 제한을 잘못 적용하던 문제와 큰 비유한 소수 근삿값의 정수부 끝 0을 지우던 문제도 수정했다. 유한 소수의 물리값과 u64 최대값은 정확히 표시한다.
- **빌드 검사**: 실행 중인 기본 배포본이 있으면 배포 폴더를 손대기 전에 중단한다. 전체 확인 스크립트는 테스트 또는 빌드 실패 시 다음 단계로 넘어가지 않아 이전 EXE를 새 결과처럼 검사하지 않는다.
- 검증 결과는 이 절에 실제 실행 후 기록한다. 남은 항목의 재분류와 추적은 [구현 현황](docs/15-implementation-status.md)을 따른다.

## 13차(Claude): 분수 계수, 상태 차트 출력, 스냅샷·확장 데이터 레코드, 예약 비트 이름, CSV 미리보기 가상화, 스키마 8 (2026-09-25)

남은 항목 중 근거가 있는 것을 구현했다. 근거가 없는 부분은 이전처럼 출력을 막고 사유를 표시한다.

- **분수 계수**: LINCOMP f/div가 유한 소수가 아니면(ABS Position_1_Byte 359/255) 기약분수 "359/255"로 저장한다. 변환은 `fractions.Fraction`으로 정확히 계산하고, 표시만 유효숫자 12자리 근삿값에 "…"를 붙인다(1 → 1.40784313725…). 물리값 입력은 10진수·분수 모두 받으며 정수 원시값으로 정확히 떨어질 때만 받는다(반올림하지 않음). ODX는 `COMPU-NUMERATOR (offset·q, p) / COMPU-DENOMINATOR q`. 근거: 도움말 110_ODXExport "Linear: Export of Denominator ... only if it differs from 1". 유한 소수 계수는 기존대로 분모 1(Vector ODXEnvelope 출력과 같음). 타입 창에서 분수로 입력할 수 있다. ABS 보존 항목 −1.
- **상태 차트 ODX 출력**: 도움말 180_ODXExport대로 상태 그룹 → STATE-CHART(SEMANTIC = 그룹 종류), 상태 → STATE, 서비스의 전이 → STATE-TRANSITION(같은 전이는 한 번, 여러 서비스가 STATE-TRANSITION-REFS로 참조), 초기 상태 → START-STATE-SNREF. **실행 가능 상태가 일부로 제한된 서비스**는 ODX 대응 설명이 없어 계속 차단하고, 차단 메시지에 해당 서비스를 적는다. 모든 상태에서 허용(= 제한 없음)인 서비스의 전이는 출력된다. ABS는 제한된 서비스 17개가 있어 여전히 차단된다.
- **스냅샷·확장 데이터 레코드(스키마 8)**: DIAGINST/FAULTMEMORY를 해석한다. 스냅샷 레코드 번호(0x10 First_Occurrence, 0x20 Last_Occurrence), 기본 DID 목록(0x010A, 0x010D), DTC별 대체 DID 목록, 확장 데이터 레코드(0x00·0x0A·0x0B·0x0C와 각 구조). 0x19 04·0x19 06 서비스도 가져온다(ABS 서비스 57 → **59**, 전부). 전문의 sr/edr(레코드 번호, 8비트)·sd/ed(번호·DTC에 따라 정해지는 가변 데이터)는 멤버 `fault_record`로 둔다. 고장 메모리 인스턴스 속성 창에 레코드·DID 목록을 표시한다. **ODX 출력은 차단**: CANdela 13 형식(DID 목록 스냅샷)의 ENV-DATA-DESC·MUX 구성은 도움말·예제 근거가 없다(PDX는 CANdela 5.0의 다른 모델). 검증 창에 VAL-012 경고로 나온다. EVENTDATA(표시등·운전 주기·디바운스 등 ECU 설정)는 계속 보존.
- **예약 비트 이름**: GAPDATAOBJ 이름(WarningIndicatorRequested 등)을 `structure_members.label`로 보존해 배치·편집 창·ODX LONG-NAME에 쓴다.
- **스키마 8**: objects.kind에 snapshot_record·extended_record, structure_members에 fault_record·record_role·label, 표 snapshot_records·snapshot_default_dids·snapshot_dtc_dids·extended_records. 7 → 8 마이그레이션이 새 스키마와 같은 표·색인을 만드는지 비교했다. 픽스처 `schema_v7.sql` = PC의 7판 `schema.sql`.
- **그 밖**: 저장 파일 비교가 인스턴스의 고장 메모리 정의를 비교한다. 스냅샷에서 쓰는 DID는 삭제를 막는다. 레코드·레코드 구조에서 경로 이동은 고장 메모리 인스턴스로 간다.
- **성능**: CSV 적용 미리보기를 위젯 항목 대신 모델(QTableView)로 바꿨다. 10만 행 테스트 추가. DTC 2만 개 측정값은 아래 남은 작업에 적었다.
- **남은 것**: 초안 ODX 출력(검증 안 된 출력 허용 여부)은 결정이 필요하다. 배포 서명·설치 프로그램은 인증서·설치 도구가 필요하다. 실행 가능 상태·스냅샷/확장 데이터의 ODX 대응은 근거가 확보되면 구현한다.

## 12차(Claude): 0x19·0x14 고장 메모리 전문 — DTC 값·상태 바이트·DTC 그룹·PDU 끝까지 반복, 스키마 7 (2026-09-25)

설계 근거는 두 가지다. `Import/UDS-ExampleEcu.pdx`는 **CANdelaStudio 5.0이 출력한 ODX**(파일 주석 `ASAMOdxExport201.dll 5.0.104`)라 CDD 구성요소가 ODX로 어떻게 나가는지 직접 보여 주고, 도움말 100_ODXExport "Data objects for Fault Memory"가 규칙을 설명한다.

| CDD 구성요소 | 모델(스키마 7) | ODX |
|---|---|---|
| `EOSITERCOMP`(minNumOfItems) | 멤버 `iteration` + 전용 항목 구조, `min_items` | END-OF-PDU-FIELD → BASIC-STRUCTURE (PDX ListOfDTC) |
| `SIMPLEPROXYCOMP dest=dtc`(슬롯 = 고장 메모리 레코드) | 멤버 `dtc`(24비트) | VALUE → 변형 DTC-DOP. DTC 표가 비면 RESERVED(도움말) |
| `STATUSDTCPROXYCOMP`(슬롯 = STRUCT) | 8비트 비트필드(10차 모델). `GAPDATAOBJ`는 예약 비트 | 활성 비트 VALUE, 비활성 비트 RESERVED(PDX DtcStatusbyte: WarningIndicatorRequested 비트 7) |
| `GROUPOFDTCPROXYCOMP`(슬롯 = GODTCDATAOBJ + 지역 TEXTTBL) | 데이터 객체, 지역 표는 타입(LocalTable), `def` → 기본값 | TEXTTABLE DOP 24비트(PDX LocalTable "All groups") |
| `SIMPLEPROXYCOMP dest=any`, 슬롯 없음 | 구성요소 이름의 바이트 타입(예: DtcCount 2바이트) | A_BYTEFIELD IDENTICAL(PDX DtcCount 16비트) |

- **가져오기**: ABS 예제 서비스 53 → **57**(FaultMemory_ReadNumber 19 01, ReadAllIdentified 19 02, ReadAllSupported 19 0A, Clear 14). 원문 배치(읽기 전용) 서비스로 들어간다. 보존 항목 50 → 46, 출력 차단 35 → 31.
- **남은 것**: 0x19 04(스냅샷)·0x19 06(확장 데이터)은 레코드 번호별로 내용이 바뀌는 구조(sr/sd/edr/ed, 인스턴스의 FAULTMEMORY/EVENTDATA → ODX ENV-DATA-DESC·MUX)라 모델이 없어 계속 보존한다. 사유가 "스냅샷·확장 데이터 레코드 구성요소 미지원: sr/edr"로 구체화됐다. GroupOfDtc의 individualDtcs=1(개별 DTC 지정, 도움말상 DTC-DOP 서비스 추가 생성)도 근거 예제가 없어 보존한다. 예약 비트의 원래 이름(WarningIndicatorRequested)은 모델에 이름 칸이 없어 "Reserved"로 보인다.
- **스키마 7**: `structure_members.kind`에 `dtc`·`iteration`, 열 `min_items`. CHECK 변경이라 테이블을 재생성한다(6 → 7). 테스트 픽스처 `schema_v6.sql`은 PC에 있는 6판 `schema.sql`과 같은 내용이다.
- **검증**: VAL-005에 반복 규칙 추가 — 요청·응답 전문 구조에만, 마지막 멤버로만, 항목은 바이트 단위 길이·전용 구조.
- **표시**: 속성 창 배치에 "반복 ListOfDTC — 아래 항목(4바이트)을 PDU 끝까지 반복, 최소 0개" 줄과 항목 기준 상대 위치(↻) 행. 전문 미리보기는 최소 반복 수만큼 항목을 넣는다.
- **ODX 사전 요소 순서 수정**: 기존 출력은 DTC-DOPS를 STRUCTURES 뒤, TABLES를 UNIT-SPEC 앞에 두었다. Vector 출력(PDX, ODXEnvelope 0112) 순서 DTC-DOPS → DATA-OBJECT-PROPS → STRUCTURES → END-OF-PDU-FIELDS → UNIT-SPEC → TABLES로 맞췄다. 예약 비트도 CODED-CONST 0 대신 RESERVED(BIT-LENGTH)로 낸다.
- **경로 이동**: 데이터 객체·구조(비트필드 멤버, 반복 항목)도 담고 있는 DID·서비스·packet 타입으로 경로를 만든다(검증 결과의 VAL-005 항목에서 이동 가능). Qualifier 개요에 데이터 객체·구조가 나온다.

## 11차(Claude): DIAG-SERVICE SEMANTIC, VAL-011 프로파일 검사 (2026-09-25)

- **DIAG-SERVICE SEMANTIC**(3차부터 남은 항목): CDD의 DCLTMPL `cls` 코드(ses·idn·ftm·act …)를 클래스 속성 `cdd.class_type`으로 보존하고, ODX 출력 때 도움말 60_ODXExport 표 9(클래스 유형 → SEMANTIC)로 정한다. ABS 예제: IDENTIFICATION 14, ACTUAL-DATA 9, CONTROL 5, COMMUNICATION 4, SESSION 3 등. 근거가 엇갈리는 한 곳: Vector PDX 예제(UDS-ExampleEcu, ODX 2.0.1)는 DynamicData에 CURRENTDATA를 쓴다. (12차 정정: 그 파일은 CANdelaStudio 5.0 ODX 2.0.1 출력이다. 현재 13.0 도움말 표 9는 ACTUAL-DATA이고 이 앱은 ODX 2.2.0을 출력하므로 표 9를 따른다.) 유형 코드를 확인하지 못한 클래스(calibration·upload)는 SEMANTIC을 생략하고, 직접 만든 클래스(유형 없음)는 기존 SID 기준 값을 쓴다. 이 속성은 기존 프로젝트에 없으므로 CDD를 다시 가져와야 적용된다.
- **VAL-011 판정**(10차에서 정책만 넣은 항목): 고정 프로파일(uds_fixed) 서비스의 subfunction과 모든 서비스의 NRC를 검사한다. 0x3E는 00, 0x31은 01·02·03만 인정한다(CDD 템플릿 프로토콜 서비스 TP "3E 00", RC "31 01/02/03"에 고정된 값). 0x10·0x11은 템플릿이 매개변수로만 두므로 ISO 14229-1 정의 값(0x10: 01–04, 0x11: 01–05)만 인정하고, 제조사·공급사 정의 범위는 의미를 증명할 수 없어 경고한다. NRC는 프로젝트의 NRC 목록(CDD 템플릿 NEGRESCODES)에 있어야 하며, 목록이 없는 프로젝트는 ABS 템플릿과 같은 40개 목록을 쓴다. 원문(raw) 서비스의 subfunction은 원문 배치 그대로 보존이므로 검사하지 않는다. warning/출력 차단. ABS 예제는 해당 없음(회귀 테스트).
- Windows 확인: 17:39 실행에서 unittest 0. 빌드는 실행 중인 DiagAuthor.exe가 `dist\DiagAuthor.dist`의 파일을 잠가 실패(PermissionError)했다. 앱을 닫고 다시 실행해야 한다.

## 10차(Claude): 8·9차 검토, 검증 심각도 정책, CDD 비트필드, 복구 작업본 격리 (2026-09-25)

8·9차(DeepSeek 세션) 변경을 검토하고 남은 항목을 이어서 구현했다.

- **검토 결과**: 검증 창·삭제 후 이동·필터 복원·타입 미리보기·복구 메타데이터 모두 동작과 테스트를 확인했다. 한 곳을 고쳤다: 복구 후보 무결성 검사가 작업본을 **쓰기 모드**로 열었다. 쓰기 연결을 닫으면 WAL 체크포인트로 복구 대상 파일이 바뀐다. 읽기 전용으로 열고, 열 수 없으면 "확인 불가"로 표시한다. 검사 전후 작업 폴더 바이트가 같은지 테스트 추가.
- **검증 심각도·출력 차단 정책**(8차에서 남긴 항목): `docs/11` 표대로 규칙별 심각도를 붙인다. VAL-011·VAL-012는 warning/차단, 나머지 VAL은 error/차단, 새 INF-001(의미 없는 보존 메타데이터)은 info/비차단. 코드의 규칙 번호를 명세에 맞췄다: 미지원 의미 VAL-018 → **VAL-012**, 변형에 없는 DID VAL-003 → **VAL-008**, 타입 검사 VAL-005 → **VAL-007**, 구조 길이 VAL-006 → **VAL-005**, 상태 행렬 누락 VAL-010 → **VAL-020**. 이제 검증 창의 경고·정보 필터와 "출력 차단" 열이 실제로 구분된다. ABS 예제: VAL-012(경고) + INF-001(정보)만 남는다.
- **CDD STRUCT 비트필드**(3차부터 남은 항목): 비트 순서를 Vector 출력과 대조했다. `Repository/ODXEnvelope/010A_Wheel_Speed.odx-d`에서 Direction_of_rotation(컨테이너 HexDump_1Byte, 2비트 멤버 4개)은 BYTE-POSITION 8, BIT-POSITION 0·2·4·6이다. 즉 첫 멤버가 최하위 비트다. 같은 예제의 CommunicationControl CommType(Normal·Network·Diagnostic 1비트 + reserved 5비트)도 UDS 규격의 비트 0 = 정상 메시지와 맞는다. 구현: 8비트 컨테이너 STRUCT를 전용 중첩 구조(`structures.kind='bitfield'`, 남는 상위 비트는 예약 비트)로 가져오고, 평탄화할 때 비트필드 멤버 순서를 뒤집어 전문·ODX가 같은 위치를 쓴다. 속성 창의 배치 표기도 ODX와 같은 "바이트.비트(최하위=0)"로 바꿨다. ABS 예제: DID 18 → **19**(0x010A Wheel Speed), 서비스 48 → **53**(Wheel_Speed_Read, Communication Control 4개), 보존 항목 60 → 50, 출력 차단 45 → 35. 8비트가 아닌 컨테이너는 근거가 없어 계속 보존한다. DID 편집 창은 비트필드 행을 "비트필드: 이름"으로 보여 주고 그대로 유지한다.
- **복구 작업본 격리**: 작업 복구 창에 "선택 작업본 격리(목록에서 제외)…" 버튼. 확인 후 작업 폴더를 `%LOCALAPPDATA%\DiagAuthor\quarantine\<ID>-<시각>`으로 옮긴다. 파일은 지우지 않으며, 다른 창에서 쓰는 작업본과 잘못된 ID는 거부한다.

## 9차: 검증 오류 필드 힌트, 타입 미리보기, 복구 메타데이터 (2026-09-25)

근거: `docs/04-ui-specification.md` UI-06(타입: 원시→물리 미리보기), UI-10(행 선택으로 필드 이동), UI-12(복구: 원본 경로·작업 시각·저장 시각·무결성·원본 변경 여부).

- **검증 오류 필드 힌트**: 검증 결과에서 객체로 이동하면 상태 표시줄과 작업 기록에 "수정 안내"와 대상 필드를 남긴다. 이어서 편집 창(F2)을 열면 해당 필드에 포커스를 준다(현재 `field`를 내보내는 규칙은 VAL-013 qualifier).
- **타입 raw→물리 미리보기**: 데이터 타입 편집 창에 원시값 입력 미리보기를 추가했다. unsigned/signed의 동일값·선형 변환만 계산하고(예: 650 → 25.0), 열거 테이블·비수치 인코딩은 계산하지 않는다고 표시한다. 미리보기 값은 저장하지 않는다.
- **복구 메타데이터**: 저장 시 `saved_at`을 manifest에 기록하고(이후 저장부터, 기존 작업본은 —), 복구 후보 목록에 저장 시각·무결성(`PRAGMA quick_check` 정상/손상/확인 불가 + 스키마 버전)·원본 변경 여부(원본 그대로/변경됨/원본 없음/저장 전)를 표로 보여 준다. 복구는 기존과 같이 원본을 자동으로 덮어쓰지 않는다.
- 파일: `diagstudio/persistence.py`, `diagstudio/dialogs.py`, `diagstudio/ui.py`, `test/test_recovery_metadata.py`(신규), `test/test_ui_gaps.py`(신규).
- 회귀 테스트 **97개 통과**(이전 91개). Windows 확인(2026-09-25 17:02:19–17:06:03, `build\run_checks.cmd nopause`): `build\checks\status.txt` unittest·build·gui 모두 0, `unittest.log` 97개 OK, 기본 배포본 재빌드 후 GUI 스모크 통과.
- 남은 것: 복구 작업본의 명시 폐기(별도 격리) UI는 아직 없다. → 10차에서 구현.

## 8차: 검증 결과 UI, 삭제 후 선택 이동, 필터 복원 (2026-09-25)

근거: `docs/04-ui-specification.md` UI-10(검증 표시: severity·rule·객체 경로·차단 여부·revision, 오래된 결과 배지, 오류/경고 필터)과 53행(삭제 후 부모·다음 객체 이동, 검증 이동 후 원래 필터 복원).

- **검증 결과 표시**: `Application.validate_project`가 각 항목에 안정 객체 경로(`diag://`)를 붙인다(`object_path`, 실패 시 빈 값). 검증 창은 심각도·규칙·객체 경로·내용·수정 안내·출력 차단·revision 열과 전체/오류/경고/정보 필터, 이전 revision 배지, 항목·차단 개수 요약을 표시한다. 행 더블 클릭 이동은 그대로다.
- **삭제 후 선택 이동**: 삭제 확인 시 현재 목록에서 다음 행(없으면 이전 행)을 미리 구해, 명령 결과의 `selected_id`(삭제된 ID)를 그 값으로 덮어쓴다. 이로써 삭제 직후 `query_objects`가 삭제된 ID를 찾지 못해 나오던 `DomainError("선택한 객체가 이 목록에 없습니다.")` 경고가 사라지고, 상태 표시줄과 작업 기록에 이동 사유를 남긴다.
- **필터 복원**: 검증 결과 이동이 검색 필터를 지우기 전에 노드·검색어·정렬·페이지를 저장하고 "← 이전 필터" 버튼을 띄운다. 버튼은 원래 목록 위치로 되돌린다. 검색어를 다시 입력하면 저장은 버려진다.
- 파일: `diagstudio/ui.py`, `diagstudio/application.py`, `test/test_ui_validation.py`(신규), `test/test_ui_navigation.py`.
- 회귀 테스트 **91개 통과**(이전 84개). Windows 확인(2026-09-25 16:27:05–16:30:20, `build\run_checks.cmd nopause`): `build\checks\status.txt` unittest·build·gui 모두 0, `build\checks\unittest.log` 91개 OK, 기본 `dist\DiagAuthor.dist` 재빌드 후 ABS 예제 가져오기·DID 0x010B·저장 GUI 스모크 통과. 7차 기록의 73개/filecompare 우회 결과는 이 실행으로 대체된다.
- 검토: 변경의 공유 모델·명령 멱등성·읽기 전용 처리에 회귀가 없음을 확인했다. 남긴 항목은 아래 "계획 대비 남은 작업"에 적었다.
- 이번 수정에서 함께 고친 것: 열 순서를 docs/04대로 맞추고 revision 열 추가, 프로젝트를 닫을 때 검증 표와 필터 초기화, 닫힌 상태에서 stale 배지 숨김, DID 분리 후 stale 배지 갱신, 필터 저장 조건 확대(정렬·페이지 포함), 목록이 빈 경우 삭제 메시지 문구 정정, UI 테스트 격리(LOCALAPPDATA 복원·테스트별 상태 초기화)와 삭제 배선 회귀 테스트 추가.

## 7차(Claude): GPT 작업분 검토·보완, 명령 재시도, 파일 비교 확장 (2026-09-25)

GPT 세션이 T3·T4, 저장 파일 DID·DTC 비교, 명령 ID 재시도(스키마 6)를 구현한 뒤 이어받아 검토했다.

Windows 확인: 이 변경은 이후 8·9차의 `build\run_checks.cmd` 실행(17:02–17:06, 테스트 97개·재빌드·GUI 스모크 통과)에 포함되어 확인됐다.

검토에서 고친 것
- **Qualifier 개요가 가져온 CDD 프로젝트에서 중단**: `locate_object`가 확장 변형에만 있는 인스턴스(ABS C Sample의 Yaw Rate Sensor 등)를 다른 변형 기준으로 찾을 때 `UnboundLocalError`가 났다. 개요는 `DomainError`만 건너뛰므로 목록 전체가 실패했다. 기존 테스트는 CDD 없는 작은 프로젝트만 썼다. 수정 후 ABS에서 740행 계산, 모든 경로가 트리 노드로 되돌아가는지 테스트 추가.
- **개요 계산량**: `object_path`가 호출마다 이름 표 전체를 다시 읽어 Qualifier·Attribute·번역 개요가 객체 수의 제곱으로 느려졌다. 이름 표를 한 번 읽어 넘기고, 번역 표는 객체별 경로를 캐시한다(ABS 기준 각 0.05초 이하).
- **변형 Compare의 사본 표시**: 코드가 같은 별개 객체를 모두 "서로 다른 객체"로 표시했다. 파일 비교와 같은 정의 서명으로 "사본, 정의 같음" / "사본, 정의 다름: 필드"를 구분한다.
- **작업 지침**: `CLAUDE.md`의 모델 표가 GPT 모델로 바뀌고 `run_tests.cmd`·sqlite `closing` 규칙이 빠져 있었다. Claude 세션(Opus 5.5 low/high)과 GPT 세션(`AGENTS.md`) 규칙을 한 표에 나눠 적고 빠진 규칙을 되살렸다. 실행 중인 앱이 배포 폴더를 잠가 빌드가 실패하는 문제도 적었다.

이어서 완료한 것
- **명령 ID 재시도(스키마 6)**: GPT가 구현했으나 기록·Windows 확인 전이었다. `execute_command(..., command_id)`는 같은 ID·같은 요청을 다시 받으면 적용하지 않고 첫 결과를 돌려준다(`already_applied`). 같은 ID에 다른 요청은 `COMMAND_ID_REUSED`로 거부, 실패한 명령은 기록하지 않는다. 영수증(`command_receipts`)은 저장 파일에 남고 실행 취소 이력에 포함되지 않는다. 5 → 6 마이그레이션 테스트 추가.
- **저장 파일 비교 확장**: DID·DTC에 더해 진단 인스턴스·서비스를 비교한다. 인스턴스는 (클래스 Qualifier, 인스턴스 Qualifier), 서비스는 Qualifier로 대응시켜 UUID와 무관하다. 서비스 헤더·주소·NRC·전문 구조·상태 조건/전이(그룹·상태 Qualifier 기준), 인스턴스의 원본 변형을 비교한다. 같은 CDD를 두 번 가져온 파일은 차이 0건, ABS 기본 변형 대 C Sample은 Yaw Rate Sensor 추가와 Control DTC Setting 사본(원본 변형 다름)을 표시한다. 설명·번역·사용자 속성은 아직 비교하지 않는다. 메뉴 이름은 "저장된 프로젝트와 비교…".

## 저장된 프로젝트 파일과 비교 (2026-09-25)

- 프로젝트 메뉴의 **저장된 프로젝트와 비교**(당시 이름: 저장된 프로젝트와 DID·DTC 비교)에서 현재 작업본의 변형과 `.diagproj` 파일의 변형을 각각 선택한다. 같은 파일을 선택하면 저장 이후의 미저장 변경도 확인할 수 있다.
- 대상 파일은 읽기 전용으로 열고 스키마·무결성을 검사한다. 변형 선택 중 파일이 바뀌면 비교를 취소한다. 비교는 프로젝트의 DB·실행 취소 이력을 변경하지 않는다.
- DID는 범위·16비트 식별자, DTC는 범위·24비트 코드로 대응시킨다. 다른 파일의 UUID가 달라도 동일한 정의는 차이로 표시하지 않는다. 이름·Qualifier·지원 상태, DID의 데이터 객체·타입·변환식·예외값·구조가 다르면 해당 항목을 표시한다. 현재 프로젝트에만 있는 항목은 더블 클릭해 이동할 수 있다.
- 이 기능은 **DID·DTC 정의 비교**다. 서비스·인스턴스·상태 조건·원본 참조 관계, 설명·번역·사용자 속성의 차이는 아직 비교하지 않는다. 이전 스키마 파일은 먼저 앱에서 열어 현재 스키마로 저장해야 한다.
- 검증(2026-09-25): 회귀 테스트 **73개 통과**(`build/checks/unittest-file-compare.log`). 기존 실행 파일이 열려 있어 기본 배포 폴더의 재생성이 Windows 파일 잠금으로 실패했다. 실행 중인 앱은 종료하지 않고 `dist/filecompare/`에 PyInstaller 배포본과 ZIP을 별도로 만들었다. 새 EXE의 GUI 스모크는 CDD 가져오기·DID 탐색·상태 그래프·저장과 새 비교 메뉴 표시까지 통과했다(`build/checks/filecompare/gui-events.json`). 실제 파일 간 비교 계산·파일 불변·미저장 변경·변경 중단은 회귀 테스트에서 확인했다.

## UI T3·T4 구현 (2026-09-25)

- T3: 프로젝트·변형·객체 UUID가 포함된 `diag://` 경로 복사·입력 이동, 트리·표 컨텍스트 메뉴, 관련 DID·데이터 타입 이동, Qualifier·Shortcut Qualifier 개요. CDD `SHORTCUTQUAL`을 별도 속성으로 보존해 실제 원본 값만 개요에 표시한다.
- T4: Attribute 개요는 프로젝트 전체·현재 변형 범위를 고를 수 있다. `user.*` 텍스트 속성을 선택 객체에 한 명령으로 일괄 설정하고 Undo할 수 있다. 같은 프로젝트 두 변형의 Compact View와 Compare, 서비스 전이 기반 상태 그래프, 저장된 번역만 보여 주는 번역 표도 추가했다. 번역 추가·수정은 작업 DB 명령으로 처리하며 Undo할 수 있다.
- 이 시점의 변형 Compare는 같은 프로젝트의 두 변형에 한정했다. 인스턴스는 객체 ID·원본 변형 기준, DID·DTC는 코드와 객체 ID 기준으로 비교했다. 저장 파일의 DID·DTC 비교는 위 후속 구현을 따른다.
- 상태 그래프는 저장된 상태·전이를 표시한다. 실행 가능 조건은 기존 의존성 표에서 확인한다.
- 검증(2026-09-25): 회귀 테스트 **69개 통과**(`build/checks/unittest-t34-release.log`), PyInstaller 재빌드 성공(`build/build.log`), 빌드 EXE GUI 스모크 통과(`build/checks/t34release/gui-events.json`). ABS 예제 가져오기 → DID 0x010B 선택 → 경로 복사·재이동 → DID 우클릭 메뉴 → 상태 그래프·Shortcut Qualifier 개요 → `.diagproj` 저장을 확인했다. 같은 EXE로 다시 열어 DID 18개·DTC 31개 표시를 확인했고(`reopen.json`), 저장 DB의 `PRAGMA integrity_check` 결과는 `ok`다. T4 Attribute 일괄 설정·번역 Undo, 변형 비교의 데이터 규칙은 단위 테스트로 확인했다.

## 6차 구현: 탐색 트리 3단계 — 원본·참조 구분, NRC·프로토콜 서비스·Job 목록, 스키마 5 (2026-09-25)

근거: `docs/ui-tree-review.md` 2절 D5·7절 T2, 도움말 Icons [TREE-R400], ABS 예제의 DIAGINST/DIAGINSTREF·JOBCNR/JOBCNRREF·NEGRESCODES·PROTOCOLSERVICES.

- **원본·참조(D5)**: `diagnostic_instances.owner_variant_id` = CDD에서 DIAGINST가 있는 변형. 다른 변형의 소속은 DIAGINSTREF 참조다. 인스턴스 표시 5종 완성: ● 기본 변형에만 / ◉ 기본 변형 원본, 확장 변형에서 참조 / ○ 확장 변형에만 / ◈ 확장 변형 원본, 기본 변형에서 참조 / ◎ 확장 변형 원본, 다른 확장 변형에서만 참조. 속성 창에 원본 변형 표시.
- 편집 시 원본 유지: 새 서비스의 인스턴스는 그 변형이 원본, DID 분리로 만든 사본은 분리한 변형이 원본, 원본 변형이 인스턴스를 놓거나 삭제되면 남은 사용 변형(기본 변형 우선)으로 넘긴다. VAL-021에 "원본 변형이 인스턴스를 포함하지 않음" 추가.
- **NRC 목록**: NEGRESCODES 40개를 코드·이름으로 가져와 루트 "부정 응답 코드" 노드에 표시. 서비스 속성의 NRC에 이름을 붙인다(예: `0x22 Conditions not correct`).
- **프로토콜 서비스 목록(읽기 전용)**: 31개. 요청·긍정·부정 응답 구성 요약(상수는 16진, ‹고정 값›, {데이터 자리}, [기타 구성요소]), 사용 템플릿 수, 문서 서비스 수. 예: DSC `10 ‹DiagSessionType›` / `7F 10 {RC}`, 서비스 3.
- **Job 컨테이너**: JOBCNR를 이름·원본 변형·변형별 순서로 가져와 변형 아래 🔒 노드로 표시(참조는 ↪). 내용은 해석하지 않고 컨테이너 소유의 보존 원문(출력 차단)으로 둔다. JOBCNRREF는 참조로 표현되므로 보존 항목에서 빠진다(ABS 보존 항목 66 → 60, 출력 차단 51 → 45).
- **스키마 5** (`migrations.py` 4 → 5): objects.kind에 nrc·protocol_service·job_container 추가(테이블 재생성), owner_variant_id 열, nrcs·protocol_services·job_containers·variant_jobs 테이블. 이전 파일의 원본 변형은 기본 변형, 없으면 순서가 가장 앞선 사용 변형으로 정한다(파일에 근거가 없음).
- 테스트: 템플릿 목록·원본 4개, 분리 후 원본 이전, 4 → 5 마이그레이션 추가. 구 스키마 테스트 파일은 현재 버전 기준으로 기대값 계산.

## 5차 구현: 탐색 트리 2단계 — 트리 재구성과 노드별 목록 (2026-09-25)

Windows 확인 완료(2026-09-25 13:56, `build\run_checks.cmd`): 단위 테스트 54개 통과(Qt 트리 테스트 8개 포함), 재빌드 성공, GUI 스모크 통과 — 가져오기 후 트리에서 Identification 클래스를 고르면 F190~F189 인스턴스 7개가 표시됨(`build\checks\05-tree-class.png`). Qt 트리 항목은 UIA Select로는 선택이 바뀌지 않아 스모크는 실제 클릭으로 확인한다.

근거: `docs/ui-tree-review.md` 4–5절(T1), CANdelaStudio 도움말 Navigation Tree [TREE-R010]·Icons [TREE-R400]·State Dependencies·DID/DTC Overview.

- **트리 순서** [TREE-R010]: ECU → 고장 메모리(DTC 개요) → DID 개요 → ◆ 기본 변형 → 변형(확장 변형들) → 상태(그룹·의존성) → 데이터 타입 → 보존 항목.
- **변형 하위**: DID, DTC, 진단 클래스(문서 순서, 인스턴스 수) → 인스턴스, 싱글턴 인스턴스는 변형 바로 아래, 보조 노드 "모든 서비스". CANdela처럼 서비스는 트리에 넣지 않고 인스턴스 노드의 목록에서 연다.
- **인스턴스 표시**: `[식별자] 이름`(DID·subfunction·루틴 ID에서 유도), 공유 표시 ● 기본 변형에만 / ◉ 기본 변형 것을 확장 변형이 참조 / ○ 확장 변형 로컬 / ◎ 확장 변형 사이 공유, 필수(req) 굵게, 보존 원문이 연결되면 ⚠(툴팁에 건수). 확장 변형끼리의 원본·참조 구분은 스키마 5(D5)에서 한다.
- **노드별 목록**: 변형 → 진단 클래스 목록, 클래스 → 인스턴스(식별자·서비스·공유), 인스턴스 → 서비스, 상태 그룹 → 상태, 의존성 → 서비스 × 상태 표(✓ 허용, ✗ 차단, ✓ → 전이 대상, — 제한 없음, 현재 변형 기준), DID·DTC 개요 → 객체 × 변형 포함 여부. 속성 창은 인스턴스·클래스·상태 그룹 상세도 보여 준다.
- **트리 모델**: `QTreeWidget` → `QTreeView` + `QStandardItemModel`(명세 04의 모델 기반). 상태가 바뀌어 다시 그려도 노드 키로 펼침·선택을 되살린다(편집 후 트리가 접히던 문제 해결). 검증 결과 더블 클릭은 트리 노드를 펼쳐 선택한 뒤 행을 고른다.
- **API**: `Application.tree()`(overview에 포함), `query_objects(scope=…)`와 kind `diagnostic_class`·`diagnostic_instance`·`dependency`·`did_overview`·`dtc_overview`. 동적 열은 결과의 `columns`로 전달.
- 테스트: `test/test_ui_tree.py`(Qt offscreen 8개), 트리 데이터 5개 추가. GUI 스모크에 Identification 클래스 노드 선택 → F190 목록 확인 단계 추가(`05-tree-class.png`).

## 4차 구현: 탐색 트리 1단계 — 클래스·인스턴스 배치, 스키마 4 (2026-09-25)

Windows 확인 완료(2026-09-25 13:25, `build\run_checks.cmd`): 단위 테스트 41개 통과, 재빌드 성공, GUI 스모크 통과(가져오기 미리보기 "진단 클래스 13 · 인스턴스 42 · 서비스 48 · DTC 31 · 상태 5", 저장).

근거와 전체 계획: `docs/ui-tree-review.md`(검토 2절 결함 D1–D4, 7절 T0). 이번 단계는 가져오기·데이터만 고친다. 트리 화면 재구성(T1)은 다음 단계다.

- **D1 클래스 중복 해소**: 진단 클래스를 변형별 `DIAGCLASS`가 아니라 템플릿 `DCLTMPL`(tmplref) 기준으로 하나만 만든다. ABS 예제 클래스 15개(중복 포함) → 13개.
- **D2 인스턴스 누락 해소**: 가져올 수 있는 서비스가 없는 인스턴스도 만든다. 그 서비스 원문은 인스턴스 소유의 보존 항목으로 연결된다. ABS 인스턴스 36 → 42(Fault Memory, Wheel Speed, Communication Control 4개). 소유자가 있는 보존 항목 15 → 28.
- **D3 문서 순서**: `variant_classes(variant_id, class_id, ordinal)` 신설, `variant_instances.ordinal` 추가. CDD의 VAR 자식 순서를 그대로 기록한다. C Sample의 로컬 인스턴스 Yaw Rate Sensor도 문서 위치(Dynamic Data 두 번째)를 지킨다.
- **D4 싱글턴 클래스**: `DCLTMPL single="1"`을 `diagnostic_classes.singleton`으로 가져온다(Fault Memory, Tester Present). 가짜 역할 클래스를 만들지 않는다.
- 수동 편집도 배치를 유지한다: 서비스 생성 시 클래스를 변형 끝에 추가, DID 분리로 만든 사본은 원래 순서를 이어받음, 변형 복사 시 클래스 배치 복사. 가져온 인스턴스는 마지막 서비스를 지워도 남긴다.
- **검증 VAL-021**: 인스턴스의 클래스가 변형에 없거나, 싱글턴 클래스에 인스턴스가 둘 이상이면 오류.
- **스키마 4** (`migrations.py` 3 → 4): 열 추가와 테이블 신설만 한다. 기존 파일은 행 순서를 문서 순서로 삼는다. 3 이하에서 CDD를 가져온 파일의 중복 클래스는 합치지 않는다(식별 근거가 파일에 없음). 정확한 배치가 필요하면 CDD를 다시 가져온다.
- 테스트 41개(배치 6개, 3 → 4 마이그레이션 1개 추가). 구 스키마 파일은 `LegacyApplication`으로 만든다(테스트 전용).

## 3차 구현: 원문 배치 서비스, 예외값 ODX 대응, 스키마 3 (2026-09-25)

Windows 확인 완료(2026-09-25 12:54): 단위 테스트 34개 통과, 재빌드 성공, GUI 스모크 통과(가져오기 미리보기 "서비스 48 · DTC 31 · 상태 5", 저장). 버전 2 파일의 3 변환은 단위 테스트로 확인.

- **근거 확보**: 로컬 CANdelaStudio 도움말(CHM)을 `build\extract_help.cmd`(7-Zip)로 `build\reference\`에 풀어 대조했다. build\는 git 제외이며 도움말은 저장소에 넣지 않는다. Vector PDX 예제(`Import/UDS-ExampleEcu.pdx`)도 대조했다.
- **상태 조건 해석 확정**: "상태가 적히지 않은 그룹 = 제한 없음"은 도움말의 병렬 상태 그룹 규칙과 일관성 조건 [STTR-R200](한 그룹 전체 차단은 출력 거부)으로 뒷받침된다. 초기 상태 = 그룹의 첫 상태(도움말 정의).
- **예외값 ODX 대응**: 도움말 110_ODXExport Table 40대로 undef→NOT-DEFINED, noSignal·notImplemented·unexpectedResult→NOT-AVAILABLE, invalidSignal→NOT-VALID. noSignal은 PDX 예제 Frequency_Counter(NOT-AVAILABLE + SHORT-LABEL)로도 확인. 예외 종류 notImplemented·unexpectedResult 추가.
- **원문 배치(raw) 서비스**: 모든 구성요소를 해석했지만 고정 프로파일과 다른 서비스(0x27 SecurityAccess, 0x85 ControlDTCSetting, 0x2F IOControl, DID 참조 없는 0x22)를 바이트 배치 그대로 가져와 **읽기 전용**으로 둔다. 상태 조건·전이도 함께 가져온다(예: SendKey Locked → Unlocked L1). ABS 예제 서비스 35 → 48. ODX 출력 시 SEMANTIC은 근거가 없어 생략.
- **스키마 3** (`migrations.py` 2 → 3): services.profile(uds_fixed/raw)과 SID 범위 확장, diagnostic_classes.role에 other, raw_exclusions 종류 확장. CHECK 변경은 테이블 재생성이 필요해 외래 키 검사를 끈 상태에서 옮긴다. 버전 1·2 파일은 열 때 3으로 변환된다.

아직 남은 것(근거 부족·검토 필요):
- (12차 구현) 0x19 01/02/0A·0x14: 반복·DTC·상태·DTC 그룹 모델. 0x19 04/06 스냅샷·확장 데이터는 남음.
- (10차 구현) STRUCT 슬롯: 8비트 컨테이너 비트필드는 Vector 출력과 대조해 구현. 8비트가 아닌 컨테이너는 근거가 없어 보존.
- (13차 일부 구현) 상태 조건의 ODX 출력: 상태 차트·전이는 출력. 실행 가능 상태가 제한된 서비스(precondition)는 도움말에 대응이 없어 계속 출력 차단.
- ODX 외부 대조: 설치된 Vector ODXStudio 7에 ODX 2.2.0 검사기(EffectiveODXChecker)가 있다. 출력 파일을 ODXStudio에서 열어 검사하는 것이 계획서의 외부 도구 대조에 해당한다. 현재는 XSD 지정 없이는 출력 자체가 막혀 있어, 검증되지 않은 초안 출력 허용 여부를 결정해야 한다.
- (13차 구현) 비유한 소수 계수(359/255): 기약분수로 저장, COMPU-DENOMINATOR로 출력.
- (11차 구현) DIAG-SERVICE SEMANTIC: CDD 클래스 유형 기준으로 출력.

## 2차 구현: CDD 서비스·상태·DTC, 스키마 2 (2026-09-25)

Windows 확인 완료(2026-09-25): 단위 테스트 29개 통과(`build\checks\unittest.log`), PyInstaller 재빌드 성공, 빌드된 EXE GUI 스모크 통과 — ABS 가져오기 미리보기 "서비스 35 · DTC 31 · 상태 5", DID 18개 표시, DID 0x010B 속성, 저장. 타입 편집 창의 예외값 탭은 자동 확인 범위 밖이다.
확인 스크립트는 콘솔 창에 진행을 보여 주고 끝에서 키 입력을 기다린다: `build\run_tests.cmd`(테스트만), `build\run_checks.cmd`(테스트 → 빌드 → GUI), `build\run_gui_smoke.cmd`(GUI만).

- **서비스 가져오기** (`diagstudio/cdd_model.py`): DIAGINST → DCLTMPL(SHSTATIC·SHPROXY) → DCLSRVTMPL → PROTOCOLSERVICE 참조를 따라 요청·응답 배치를 바이트 단위로 해석하고, 자체 고정 프로파일(0x10/0x11/0x22/0x2E/0x31/0x3E)과 정확히 같을 때만 가져온다. ABS 예제: 서비스 59개 중 35개. 0x27·0x28·0x85·0x14·0x19·0x2F, STRUCT 슬롯, DTC 슬롯 등은 서비스 단위로 원문 보존.
- **인스턴스·변형 소속**: 공유 인스턴스(DIAGINSTREF)는 같은 객체를 참조하고, 변형 전용 인스턴스(C_Sample의 Yaw_Rate_Sensor)는 그 변형에만 속한다. NRC는 NEG 슬롯의 NEGRESCODEPROXY → NEGRESCODE v.
- **상태 그룹·조건·전이**: STATEGROUPS(session/security)로 기본 그룹을 대체. mayBeExec/trans는 문서 순서의 전역 상태 번호. 세션 상태 코드는 해당 상태로 전이하는 0x10 subfunction에서 도출(Default=1, Programming=2, Extended=3), 근거 없는 상태는 순서 번호(`cdd.state_code_basis` 속성).
- **DTC**: faultMemory RECORDDATAOBJ의 RECORD → 코드(v)·이름(TEXT), 레코드 항목(설정·해제 조건, 조치)은 객체 속성. ABS 예제 31개, 4개 변형 공유.
- **사용자 정의 속성**: ECU·인스턴스·서비스의 ENUM/UNS/CSTR 속성(예: Diagnostic_CAN.ReqCanId=0xA1)을 `object_attributes`에 보존. 이전에는 ECU 속성을 조용히 버렸다.
- **스키마 2 + 마이그레이션** (`diagstudio/migrations.py`): `data_types.raw_lower/raw_upper`(선형 유효 범위), `raw_exclusions`(EXCL 예외값). 버전 1 파일은 작업 사본에서 변환해 열고, 저장하면 버전 2로 기록되며 원본은 `.bak`. 새 버전 파일은 열기를 거부.
- **변환 규칙**: 예외 원시값은 라벨로 표시, 유효 범위 밖 원시값은 오류. 타입 편집 창에 유효 범위·예외값 탭 추가(Qt 화면은 미확인).
- **ODX 출력** (예제 ODXEnvelope 대조): 유효 범위 밖 원시값과 EXCL undef → `INTERNAL-CONSTR/SCALE-CONSTR VALIDITY=NOT-DEFINED`(EXCL은 SHORT-LABEL 포함), 텍스트 표의 미대응 원시값도 NOT-DEFINED. DTC-DOP 자식 순서를 Vector 출력과 맞추고 DISPLAY-TROUBLE-CODE `0x%08X`, 레코드 항목은 SDGS로 출력. noSignal·invalidSignal은 대응 근거가 없어 출력 차단. 공식 XSD 검사는 여전히 사용자가 스키마를 지정해야 가능.
- **편집 안정성**: 한 인스턴스가 읽기·쓰기 서비스를 함께 가질 수 있어, 서비스 수정·삭제 시 자기 DID 역할만 제거하도록 수정. 검증 결과에서 같은 미지원 노드의 중복 보고 제거.

해석 근거: mayBeExec의 빈 그룹 해석과 초기 상태는 3차에서 도움말로 확인했다(위 3차 항목).

## 0.1.0 빌드 이후 수정 (2026-09-25)

첫 실행 로그와 설치 예제 CDD로 확인한 문제를 수정했다. 2026-09-25 Windows에서 확인한 결과:

- 단위 테스트 15개 통과 (`build\checks\unittest.log`)
- PyInstaller 재빌드 성공 (`build\build-result.json`)
- 빌드된 EXE GUI 확인 (`build\run_gui_smoke.cmd` → `build\checks\run2\`): `ABS_ESP-Example.cdd` 인자 실행 → 미리보기(타입 84·DID 15·변형 4·보존 126) → 가져오기 → DID 0x010B 선택 시 속성 창에 `×0.0625 0 V`, `×0.1 -13 m/s²` 배치 표시 → Ctrl+S 저장(7.5 MB) 후 제목의 `*` 해제

GUI 확인은 자동 조작 스모크 수준이며 인수 테스트를 대체하지 않는다.

- **Windows 저장 실패 수정**: `Session.save`가 읽기 전용 핸들에 `os.fsync`를 호출해 Windows에서 항상 `OSError`(EBADF)가 났다. 쓰기 핸들(`r+b`)로 변경.
- **CDD 선형 변환 `div` 누락 수정**: `COMP div`를 무시해 전압 등이 1/16 대신 1배로 가져와졌다. 예제 ODXEnvelope 출력 대조로 `phys = raw·(f/div) + o`(오프셋은 나누지 않음)를 확인해 반영. 유한 소수가 아닌 계수(359/255)는 반올림하지 않고 미지원으로 보존.
- **조용한 의미 손실 차단**: 원시값 유효 범위를 제한하는 `s`/`e`, 예외값 `EXCL`, 비트 길이보다 좁은 `bm`, 알 수 없는 자식·속성을 가진 타입은 미지원으로 보존한다. 이런 타입을 쓰는 DID도 함께 보존된다(ABS 예제 DID 18 → 15).
- **가져오기 직후 실행 취소로 빈 프로젝트가 되던 문제 수정**: 가져오기 후 실행 취소 이력을 비움.
- **CDD 진입점**: 도구 모음에 "CDD 가져오기", 열기 창에서 `.cdd` 선택 가능, 실행 인자로 `.cdd` 전달 시 가져오기 미리보기.
- **속성 창**: DID·서비스 전문의 바이트/비트 배치, 타입·변환식·단위, 텍스트 표 항목, NRC를 표시(`diagstudio/presentation.py`).
- **표 정렬 표시**: 초기 헤더 정렬 화살표와 실제 정렬(이름 오름차순) 불일치 제거.
- **빌드 스크립트**: Windows PowerShell 5.1에서 PyInstaller의 stderr INFO 로그를 종료 오류로 처리해 빌드가 멈추던 문제 수정(실행 중 `ErrorActionPreference=Continue`, 종료 코드로 판정, UTF-8 BOM).
- **검증 스크립트**: `build\run_checks.cmd`(테스트→빌드→GUI), `build\run_gui_smoke.cmd`(GUI만), `build\smoke_cdd_gui.py`.
- **테스트 추가**: `test/test_cdd_import.py` 15개. 실행 `.venv\Scripts\python.exe -m unittest discover -s test -v`. 예제 CDD가 없으면 해당 테스트는 건너뛴다.

## 작성된 구현

- 한국어 Qt Widgets 주 화면: ECU·변형 탐색, 페이지 표, 검색·정렬, 언어 선택, 상세 속성, 결과 패널.
- 프로젝트 생성·열기·저장·다른 이름 저장, 기본 변형, 같은 파일의 중복 열기 제한.
- 별도 WAL 작업 DB, SQLite 백업 API를 통한 단독 파일 저장, 원본 변경 감지, 저장 전 파일 `.bak`, 비정상 종료 작업본 선택.
- 정규화 STRICT 스키마 35개 테이블, 트랜잭션, 임시 SQLite 변경 감지 트리거, 지속 undo/redo 프레임, revision·저장 token.
- ECU·변형·DID·DTC·타입·고정 서비스 편집, 공유 DID와 관련 인스턴스·서비스·payload 복제, 사용 중인 참조 삭제 차단.
- signed/unsigned 1~64비트, big/little endian, 고정 ASCII·bytes·packet, Decimal 선형 변환, 열거 범위, 요청·정응답·NRC 바이트 미리보기.
- UDS 0x10/0x11/0x22/0x2E/0x31/0x3E, 세션·보안 상태 허용 및 전이 편집.
- DTC UTF-8 CSV 읽기·열 매핑·중복 검사·선택 행 적용·내보내기, TSV 복사·DTC 붙여넣기, 이름·설명 일괄 편집.
- 앱 내 도메인 검증과 오류 객체 정보 이동, 긴 작업의 진행 표시·협력적 취소.

## 파일 호환성의 현재 범위

| 형식 | 현재 구현 | 제한 |
|---|---|---|
| `.diagproj` | 스키마 10 읽기·쓰기, 스키마 1~9 자동 변환 | 더 새 스키마는 열기 거부 |
| CDD 형식 2.x~13.x | 고정 프로파일 서비스(0x10/0x11/0x22/0x2E/0x31/0x3E)와 그 밖 서비스의 원문 배치(읽기 전용, 0x19·0x14의 DTC·상태 바이트·DTC 그룹·PDU 끝 반복·스냅샷/확장 데이터 레코드 포함)·상태 조건·전이·DTC·사용자 속성, 단순 고정 IDENT/LINCOMP(`div`·유효 범위·EXCL 포함, 비유한 소수 계수는 기약분수)/TEXTTBL 타입, 고정·가변 바이트 필드, packed BCD, CONTENTCOMP, packet(STRUCTDT), 단순 DATAOBJ·8비트 STRUCT 비트필드 DID, 기본·파생 변형과 DID 참조, 다국어 이름·설명, 원본 BLOB·ID 대응 | 원문 배치로도 해석하지 못한 서비스·Event(EVENTDATA)·Job·8비트가 아닌 비트필드·복합 구조·부분 비트 마스크는 미지원 원문으로 보존. 0x19 04/06 스냅샷·확장 데이터는 가져오지만 ODX 출력은 차단. 모든 OEM의 파일 지원을 보장하지 않음 |
| ODX 2.2.0 | 선택 변형의 타입·DID 테이블·고정 서비스·NRC·DTC 정의 XML 생성, 사용자가 지정한 XSD에 대해 검사 후 출력 | 공식 XSD는 포함하지 않음. XSD 부재·오류·미지원 의존성은 출력 차단. 32비트 초과 wire 정수, 상태 조건/전이, 데이터 객체 상수 매핑은 아직 차단. 외부 ODX 도구 호환 미확인 |
| CSV | UTF-8/BOM, 쉼표·세미콜론·탭, 10진수/16진수 DTC 코드 | 기존 행 자동 덮어쓰기 없음. 오류·중복 행은 적용 불가 |
| CDDT | 13.0.100·13.0.101 문서 템플릿(`doctype=tmpl`)을 CDD와 같은 경로로 읽어 새 프로젝트 생성(15차). 설치 예제 3종 확인 | 쓰기·템플릿 편집·템플릿 갱신 미지원. 해석하지 못한 부분은 CDD와 같이 원문 보존·출력 차단 |

미지원 항목에 의미가 있으면 원문을 보존하고 출력을 차단합니다. 호환되지 않는 데이터를 조용히 버리지 않습니다. CDD에서 가져온 프로젝트에는 프로토콜·템플릿 미지원 항목이 남기 때문에 ODX 출력이 차단될 수 있습니다.

ODX는 [ASAM MCD-2 D](https://www.asam.net/standards/detail/mcd-2-d/) 표준입니다. 사용자가 확보한 정식 2.2.0 스키마와 그 include/import 파일들을 동일한 폴더 구조로 두고 출력 창에서 `odx.xsd`를 지정해야 합니다. 스키마 통과만으로 ECU 통신 가능성 또는 OEM 의미 호환을 보장하지 않습니다.

## 계획 대비 남은 작업

- 0x19 04/06 스냅샷·확장 데이터의 ODX 출력(ENV-DATA-DESC·MUX 구성 근거 필요). 모델·가져오기는 13차. (0x27·0x28·0x85·0x2F는 원문 배치 읽기 전용으로 가져옴, STRUCT 8비트 비트필드는 10차)
- ODX 64비트·상태·상수 등 추가 의미 매핑과 공식 XSD/외부 도구 대조.
- 모든 VAL 규칙의 개별 보고, 세밀한 오류 필드 포커스, 중첩 구조 편집의 고급 UX. 저장된 객체 참조 영향과 삭제 사전 검사는 14차에 구현했다.
- (완료) 손상 복구본 격리 UI — 10차, 명령 ID 재시도 — 스키마 6.
- 무거운 CPU 작업의 프로세스 분리. 현재 UI와 DB/파일 작업은 분리된 QThread를 사용하고 CPU 작업도 그 작업 큐에서 수행.
- 현재 페이지 조회는 DB 작업 스레드에서 필터·정렬 후 페이지를 반환. (13차) CSV 적용 미리보기는 모델 기반(10만 행). DTC 2만 개 측정: 목록 0.18초, 검증 0.21초, 속성 0.34초, 가져오기 명령 5초, 실행 취소 2.7초 — 실행 취소·대량 등록은 개선 여지.
- (14차 구현) 재귀 packet 타입 사용처·영향 안내, 독립 타입 복사 UI. 특정 사용처만 자동으로 사본에 연결하는 기능과 일부 고급 공유 객체 관리는 남아 있다.
- 배포 서명·설치 프로그램·Python 없는 PC 실행 확인·성능 목표 측정.
- (13차 구현) 비유한 소수 계수 → COMPU-DENOMINATOR. noSignal·invalidSignal ODX 대응은 3차에 확인(도움말 표 40).
- 자동 테스트는 CDD·도메인·마이그레이션·저장·명령 재시도·복구·비교·Qt 화면 동작을 포함한다. `docs/11-validation-and-testing.md`의 전체 인수 테스트와 개발 환경 외 PC 검증은 아직 완료되지 않았다.
- (완료, 10차) 검증 규칙별 severity·blocks_export: VAL-011·VAL-012 warning/차단, INF-001 info/비차단. VAL-011 판정은 11차.
- (24~27차 완료) UI 상호작용 29개(`docs/ui-manual-review-2026-09-26.md` 1~3단계): 구현·자동 테스트·Windows 확인 끝. 결정 D6(개정 기록 묻기)만 남음. 남은 작업 전체 목록은 `docs/15-implementation-status.md`(27차 갱신).

## 파일·복구 위치

작업 DB와 복구 manifest는 `%LOCALAPPDATA%/DiagAuthor/workspaces/<session_uuid>`에, 파일 잠금은 `locks`에, 원문을 기록하지 않는 기본 로그는 `logs`에 저장합니다. Qt 설정은 Windows 사용자별 QSettings 위치에 저장합니다. 앱 종료 시 명시적으로 폐기한 세션만 정리합니다. 비정상 종료한 작업본은 다음 실행 때 복구 후보로 표시됩니다.

배포 폴더에 포함한 `licenses`는 설치된 의존성의 라이선스 원문입니다. 이 프로그램은 Vector 제품의 실행 코드·이미지·도움말·예제 파일을 배포 패키지에 포함하지 않습니다.
