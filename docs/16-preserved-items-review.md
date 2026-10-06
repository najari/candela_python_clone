# 보존 항목 검토 — ABS_ESP 예제 42개 (2026-09-26)

"보존 항목"은 가져오기에서 해석하지 않고 원문으로 남긴 CDD 조각이다. ABS 예제에는 42개가 있고, 그중 27개가 출력을 막는다(VAL-012). 이 문서는 항목마다 원인, 근거 자료, 해결 방법을 정리한다.

> 이 문서는 16차 시점의 분석이다. **17차 후속 구현**: 선택 변형의 객체 참조 범위 검사, 원본 전체에서 미사용임을 확인한 타입 제외, PROTOCOLSTANDARD·DOCTMPL·비활성 VCKMGR 속성화를 완료했다. 새 가져오기의 보존 항목은 42개, 의미 차단 표시는 24개다. 이 중 선택 변형 검사에서 ABS의 미사용 타입 13개와 다른 변형의 항목이 범위 밖으로 표시된다. MUXDT는 템플릿 참조 때문에 차단을 유지한다. EVENTDATA는 도움말에 언급이 없다는 이유만으로 비차단으로 바꾸지 않았다. 상세 동작·검증은 [17차 빌드 기록](../BUILD-NOTES.md)을 따른다. **19차**: #9 BCD와 #10 고정 길이 정수 배열(바이트 필드)을 가져온다. **20차**: #13 STRUCTDT(1바이트 또는 bo=21)를 packet 타입으로 가져온다. **21차(테스트 미실행)**: #14 MUX를 가져온다(미검증, ODX 출력 차단). ABS의 미사용 타입은 13 → 8이다.

> **28차(2026-09-27)**: CANoe 13 예제의 같은 문서 CDD(`UDS-ExampleEcu-5.0.4/5.1.0.cdd`, Door)와 CANdelaStudio가 출력한 `UDS-ExampleEcu-5.0.2.pdx`를 대조해 근거를 얻었다. #15 변형 식별(→ ECU-VARIANT-PATTERN, ASCII 값만), #7 UNSUPPSRVNEG(→ GLOBAL-NEG-RESPONSE, 이 문서의 "도움말 없음"을 Vector 출력으로 대체), #8 RECORDDTPOOL(24비트 DTC-DOP·풀 DTC 모두 가져옴일 때 비차단), #6 DTCSTATUSMASK(Door는 모두 optno인데 Vector 출력 상태 비트는 인스턴스 구조를 따름 → 출력 영향 없음, 비차단)를 해석했다(스키마 11). #1·#2는 보존 목록 `구분` 열과 기본 필터(출력 차단만 보임)로 정리했다. #16 EVENTDATA는 대조할 Vector 출력이 없어 차단을 유지한다. #17 Job은 Door CDD에 있지만 Vector 출력에 SINGLE-ECU-JOB이 없다(내보내지 않음) — 출력 방식은 결정이 필요해 차단 유지. ABS 새 가져오기의 차단 보존 항목은 19 → 12(복합·가변 타입 7, packet bo=12 2, EVENTDATA 1, Job 2). 28차 이전에 가져온 프로젝트는 다시 가져와야 새 분류가 적용된다. 상세는 [28차 빌드 기록](../BUILD-NOTES.md).

> **29차(2026-09-27)**: 남은 12개를 모두 해석했다(스키마 12). 같은 문서 쌍(Door CDD 5.0.4/5.1.0 ↔ Vector PDX 5.0.2)과 KWP 템플릿 ↔ CANdela Import PDX로 대조했다.
> - #11 COMPTBL: 표 29의 "특성 곡선"은 VALTBL(→ TAB-INTP)이고 COMPTBL은 구간별 선형(Piecewise linear)이다. 구간 하나 → LINEAR(OdometerValue: 0..65534, V1=16, NOT-AVAILABLE 65535), 여러 구간 → SCALE-LINEAR(Door TestDatatypePiecewiseLinear, 구간마다 COMPU-SCALE). ABS 3개는 모두 구간 하나라 선형으로 가져온다. 여러 구간은 구간 저장이 없어 보존.
> - #12 FORMULADT: Vector 출력 = IDENTICAL, DESC "Generated from formula data type …", 물리 타입 = 부호화 타입, 단위 없음. 변환 `formula`(수식 원문, 계산 안 함)로 가져오고 같은 모양으로 낸다. WRN-001(비차단)로 물리값·단위가 ODX에 없음을 알린다. 역수식(phys2Coded)이 있는 문서는 근거가 없어 보존.
> - #13 STRUCTDT: packet 수준 bo는 ODX에 나가지 않는다(Door TestDatatypeStructureLevel1 bo=12 6바이트 → STRUCTURE BYTE-SIZE 6, 멤버 DOP 자기 순서). bo=12 여러 바이트 packet도 가져온다.
> - #14 MUXDT: Door에서 인스턴스가 쓰는 RD의 같은 MUXDT는 TABLE·TABLE-KEY·TABLE-STRUCT로 나가고, 인스턴스가 없는 프로토콜 템플릿(ResponseOnEvent 등)은 DIAG-SERVICE가 없다. 출력 범위 규칙을 "프로토콜 서비스·클래스 템플릿(DCLTMPL) 안의 참조는 그 템플릿이 쓰일 때만 사용"으로 바꿨다. ABS의 MUXDT는 인스턴스가 없는 RD·RMBA·WMBA·RequestUpload에서만 참조되어 범위 밖(보존 항목으로는 남음). MUX 자체의 출력(TABLE 모양)은 아직 없다.
> - #16 EVENTDATA: 도움말 1_ODXExport_Introduction [ODXE-D115] "ODX export does not include Events" → 출력 영향 없음(비차단).
> - #17 JOBCNR: ABS·Door 모두 Job이 없는 빈 컨테이너(NAME·QUAL만)이고 Door의 Vector 출력에 SINGLE-ECU-JOB이 없다 → 빈 컨테이너는 출력 영향 없음. Job이 든 컨테이너는 계속 차단. 위 표의 "Job 내용은 외부 job 파일"은 ABS에 맞지 않았다.
>
> ABS 새 가져오기의 차단 보존 항목 12 → 1(MUXDT, Base Variant 출력 범위 밖). 29차 이전에 가져온 프로젝트는 다시 가져와야 한다. 상세는 [29차 빌드 기록](../BUILD-NOTES.md).

> **30차(2026-09-27)**: 범위를 `examples\vector_example`의 19개 파일로 넓혔다. 출력 차단 보존 항목 203 → 102. 열거 테이블 예외값, 중첩 packet, 부동소수, 앞 길이 바이트, 구간별 선형(SCALE-LINEAR), 특성 곡선(TAB-INTP), DID MUX(ODX MUX)·서비스 MUX(ODX TABLE), 쓰지 않는 Job 파일, KWP 프로토콜 표준을 Door ↔ Vector 출력으로 대조해 해석했다(스키마 13). 해결된 항목은 보존 목록의 `해결된 항목 보이기`로 본다. 남은 것과 근거는 [30차 빌드 기록](../BUILD-NOTES.md).

## 핵심 문제: 출력 차단 범위

`docs/11`의 VAL-012 정의는 "출력 폐쇄 그래프에 의미 있는 미지원 노드"다. 현재 구현은 **문서 어디에 있든** 의미 있는 보존 항목이 하나라도 있으면 모든 변형의 ODX 출력을 막는다. 예를 들어 ABS의 보존 데이터 타입 14개는 가져온 DID·서비스 어디에서도 쓰지 않는다.

- COMPTBL 3개는 쓰이지 않는 STRUCTDT에서만 참조된다.
- MUXDT는 인스턴스로 쓰이지 않은 프로토콜 서비스에서만 참조된다.
- 나머지 10개는 참조가 없다.

**해결 1 (권장, 우선):** 보존 항목마다 출력 영향 범위를 계산하고, 출력하는 변형의 폐쇄 그래프에 걸릴 때만 막는다. 문서 전체에 걸리는 항목은 따로 판정한다.

| 소유 | 차단 범위 |
|---|---|
| 데이터 타입 | 그 타입을 쓰는 DID·서비스를 출력할 때만 |
| 변형 | 그 변형을 출력할 때만 |
| 인스턴스 | 그 인스턴스를 가진 변형을 출력할 때만 |
| 문서 전체 | 항목별 판정(아래 표) |

의미 매핑을 새로 추측하는 것이 아니라 명세(VAL-012 정의)에 맞추는 수정이다. ABS에서는 이것만으로 차단 27개 중 14개(타입)가 풀린다.

## 항목별 검토

| # | 항목 (개수) | 현재 | 확인한 사실 | 해결 방법 | 근거 | 난이도 |
|---|---|---|---|---|---|---|
| 1 | 메타데이터: EXTSTORAGEITEMS·DESC·SPECOWNER·DTID·QUALGENOPTIONS·AUTHORS·HISTITEMS·TARGETGROUPS (8) | 보존, 비차단 | 출력 의미 없음 | 그대로 둔다. 보존 목록의 기본 필터에서 숨기고, DESC·AUTHORS·HISTITEMS는 ECU 속성 창의 "문서 정보"로 보여 준다 | — | 낮음 |
| 2 | 템플릿 정의: ATTRCATS·DEFATTS·NEGRESCODES·STATEGROUPS·RECORDTMPLS·PROTOCOLSERVICES·DCLTMPLS (7) | 보존, 비차단 | 인스턴스 단위로 이미 해석함(NRC·상태·프로토콜 서비스·클래스·속성) | "해석됨(원문 참조용)"으로 표시를 바꾸고 기본 목록에서 숨긴다 | — | 낮음 |
| 3 | PROTOCOLSTANDARD (1) | 차단 | 내용은 `UDS` 한 단어 | ECU 속성 `cdd.protocol_standard`로 가져오고 비차단으로 바꾼다 | 원문 자체 | 낮음 |
| 4 | DOCTMPL (1) | 차단 | 문서를 만든 템플릿의 이름·Qualifier·saveno | ECU 속성으로 가져온다(15차의 template_oid와 같은 출처 정보). 비차단 | 원문 자체 | 낮음 |
| 5 | VCKMGR (1) | 차단 | `vckmode="none"`(변형 코딩 키 관리 꺼짐) | `vckmode="none"`이면 의미가 없으므로 비차단. 다른 값이면 계속 차단 | 원문 자체 | 낮음 |
| 6 | DTCSTATUSMASK (1) | 차단 | 상태 비트 8개의 optyes/optno 목록(템플릿 제약). 실제 활성 비트는 인스턴스 STRUCT(DATAOBJ/GAP)에 있고 이미 해석함 | 비트별 속성으로 보존하고 비차단으로 바꾼다. ODX 출력은 인스턴스 STRUCT만으로 결정되기 때문이다(도움말 100 "In case the Status-bit is not activated, a RESERVED-PARAM"). 확인 필요: optyes/optno의 정확한 의미. Door CDD에는 이 요소가 없어 대조하지 못했다 | 도움말 100 | 중간 |
| 7 | UNSUPPSRVNEG (1) | 차단 | 지원하지 않는 서비스에 대한 부정 응답 전문 템플릿(7F SID NRC, NRC 목록) | NRC 목록을 ECU 속성으로 가져온다. ODX 대응이 도움말에 없어 출력 차단은 유지한다. 해결 1을 적용하면 문서 전체 항목이 된다 | 도움말 없음 | 중간 |
| 8 | RECORDDTPOOL (1) | 차단 | DTC 레코드 풀(DTC 정의 모음, OBDRecordDataType 등). 인스턴스가 쓰는 DTC는 이미 가져옴 | 풀 안의 DTC 중 인스턴스에서 참조하지 않는 것만 남는다. 도움말 100 "DTC-pool → ECU-SHARED-DATA"에 대응이 있다. 풀 DTC를 가져오고 ECU-SHARED-DATA로 출력한다 | 도움말 100 | 중간 |
| 9 | 데이터 타입 BCD(IDENT enc=bcd) 4개 | 차단 | 쓰는 곳 없음 | 가져오기 추가: 도움말 표 31 numBCD → A_UINT32, ENCODING BCD-P. 인코딩 `bcd`와 변환 규칙이 필요하다 | 도움말 110 표 31 | 중간 |
| 10 | 정수 배열(IDENT qty=field) 1개 | 차단 | HexDump_5_Byte, 쓰는 곳 없음 | 고정 길이 필드(minsz=maxsz)는 bytes 배열로 가져온다. 도움말 표 35–36(고정 필드 → STANDARD-LENGTH-TYPE) | 도움말 110 | 낮음 |
| 11 | COMPTBL 3개 | 차단 | 특성 곡선(characteristic curve). OdometerValue·Counter는 STRUCTDT에서만 쓰임 | 도움말 표 29: Characteristic curve → TAB-INTP. 새 변환 종류 `table_interpolation`(점 목록) | 도움말 110 표 29 | 높음 |
| 12 | FORMULADT 3개 | 차단 | 수식 타입, 쓰는 곳 없음 | 도움말: Formula → IDENTICAL + DESC에 수식, 경고(Vector 자체도 손실 출력). 원시값만 다루는 "수식(해석 안 함)" 타입으로 가져오고, 출력 때 경고를 요구한다 | 도움말 110 | 중간 |
| 13 | STRUCTDT 2개 | 차단 | packet(구조) 타입, 쓰는 곳 없음. 멤버가 COMPTBL을 참조 | 도움말 표 30: Packet → STRUCTURE. 앱에 이미 packet 타입이 있어 멤버 해석(#11 선행)만 추가하면 된다 | 도움말 110 | 중간 |
| 14 | MUXDT 1개 | 차단 | Memory_Address_and_Size. 프로토콜 서비스(0x23·0x3D·0x34 등)의 CONTENTCOMP에서만 참조 | 도움말 110 "MUX": 응답 안 MUX → ODX MUX. 선택자·케이스 모델이 필요하고, 0x23·0x34 서비스 지원과 같이 진행해야 의미가 있다 | 도움말 110 | 높음 |
| 15 | IDENTIFICATIONSET (변형 3) + IDENTIFICATIONFEATURESET (1) | 차단 | 변형 식별: Software_Version 데이터 객체 값 "0.0.1"~"0.0.3" 등. feature = 데이터 객체 경로 + 서비스 경로 | 모델 추가(식별 특징: 서비스·데이터 객체 참조, 변형별 기대값). 도움말 50: IdentificationPattern → ECU-VARIANT-PATTERN, 사용한 서비스는 DIAGNOSTIC-CLASS=VARIANTIDENTIFICATION | 도움말 50·80 | 중간 |
| 16 | EVENTDATA (1) | 차단 | 표시등·운전 주기·디바운스·조건(DEM 설정). 추출한 도움말 범위의 ODX 출력 장(30 Features/Export)에 대응이 없다(Navigation Tree에만 표시 노드로 나옴) | ODX 출력에 영향이 없는 ECU 설정으로 보고 비차단(INF-001)으로 바꾼다. 원문과 표시는 유지한다. 필요하면 트리에 읽기 전용 목록(도움말 Navigation Tree의 Event 노드)을 추가한다 | 도움말 Export 무언급(약한 근거) | 낮음 |
| 17 | JOBCNR 2개 | 차단 | Generic_Jobs·Flash_Jobs 컨테이너. Job 내용은 외부 job 파일 | 도움말: SINGLE-ECU-JOB으로 출력. job 코드(PROG-CODE)가 외부 파일이라 출력 불가. 해결 1을 적용하면 Job을 포함한 변형만 막는다. 추가로 "Job 제외 출력" 선택 옵션(사용자 결정)을 둘 수 있다 | 도움말 80 | 중간 |

## 권장 순서

1. **해결 1: 출력 범위 기반 차단.** 명세에 맞추는 수정이다. ABS 차단 27개 중 14개가 풀린다.
2. **낮은 난이도 정리(#1–5, #10, #16).** 문서 전체 차단 항목 4개가 비차단이나 속성이 된다. 메타데이터·템플릿 15개는 기본 목록에서 숨겨 사용자가 볼 목록을 줄인다.
3. **#15 변형 식별.** 변형 3개의 차단이 풀리고 ODX ECU-VARIANT-PATTERN 출력 근거가 있다.
4. **#8 DTC 풀, #6·#7 템플릿 제약** 속성화.
5. **#9 BCD, #12 수식, #13 packet, #11 특성 곡선, #14 MUX.** 타입 모델 확장으로, 실제로 쓰는 파일이 있을 때 진행한다.

1–3을 마치면 ABS에서 남는 차단은 UNSUPPSRVNEG, Job 2개, 사용하지 않는 타입뿐이다. 사용하지 않는 타입은 해결 1로 비차단이 된다. 그 밖에 ODX 출력을 막는 것은 보존 항목이 아닌 기존 사유(실행 가능 상태 제한, 스냅샷·확장 데이터 레코드)다.
