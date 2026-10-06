# cdd_utils CDD 파서 검토 (2026-09-26)

대상: `C:\work\setting\cdd_utils` (패키지 `cdd` 2.0.0, xsdata 바인딩 + 변환기 + 코덱). 비교 대상: DiagAuthor `interchange.cdd_preview` / `cdd_model`.

실행 방법: PC에 설치된 xsdata 26.2(Python313 site-packages)를 세션으로 복사해 cdd_utils를 그대로 실행했다(`load_cdd(path, strict=False)`). DiagAuthor는 `cdd_preview`/`apply_cdd`.

## 실행 결과

| 파일 | cdd_utils (사용 서비스) | DiagAuthor (서비스 / 변형) |
|---|---|---|
| CANSystem 5.0.100 ×2 | 실패: Unknown property DIAGCLASSATTS:UNSDEF | 3 / 1 |
| CANSystemDoor 6.0.120 | 실패: 같은 사유 | 3 / 1 |
| kwp2000-seat 5.0.111 | 실패: Unknown property VARATTS:UNSDEF | 14 / 1 |
| UDS-ExampleEcu 5.0.4 / 5.1.0 | 실패: Unknown property VALTBL:EXCL | 61 / 2, 64 / 2 |
| SampleUDS 12.0.104 | 29 | 24 / 1 |
| ABS_ESP 13.0.101 | 52 (기본 변형만) | 59 / 4 |
| Multilingual | 4 | 2 / 1 |
| Vector_KWPonCAN_1.3.cddt | 25 | 18 / 1 |
| Vector_UDS_13.0.cddt | 86 | 57 / 1 |
| Vector_UDSonLIN_1.1.cddt | 13 | 9 / 1 |
| CddTools 12.0.104 | 96 | 54 / 2 |

양쪽이 모두 가져온 서비스(ABS 52, CddTools 54, SampleUDS 24, UDS cddt 57)의 요청·긍정 응답 전문은 기본값 기준으로 바이트가 같다. 차이는 설계 차이뿐이다: 0x14 기본값(cdd_utils = 텍스트표 첫 항목 FFFFFF, 비교 스크립트 = 0), 0x19 반복 목록(cdd_utils = 항목 1회 포함, DiagAuthor = 0회).

cdd_utils만 만드는 서비스의 품질(UDS 13 템플릿):
- 맞음: 0x11 Hard/Soft Reset, 0x19 01/14/18, 0x2C 01 Define by Identifier, 0x2A 주기 응답(SID 없는 응답), DevelopmentData_Read.
- 틀리거나 불완전: 0x23 ReadMemoryByAddress·0x34 RequestDownload·0x2C 02(주소·크기 MUX 미인코딩, 요청이 2311·340011로 끝남), 0x36 가변 레코드(1비트×8로 처리).
- 비트필드 순서 오류: 0x28 요청 `28 01 01`(normal messages = bit0)을 communicationTypeNetworks=1로 디코딩한다. CDD STRUCT의 첫 멤버는 최하위 비트다(DiagAuthor 10차 근거).
- 물리값 없음: ASCII "0.0.1"을 정수 206933339697로, 선형 변환 없이 원시값으로 디코딩한다.

## 성격 차이

| 항목 | cdd_utils | DiagAuthor |
|---|---|---|
| 목적 | 읽기 전용 조회·전문 인코딩/디코딩 라이브러리 | 저작 도구(가져오기 → 편집 → 저장 → ODX 출력) |
| 파싱 | xsdata 바인딩(스키마 고정) | lxml 직접 해석, 모르는 요소는 원문 보존 |
| 변형 | 기본 변형(VAR base=1)만 | 모든 변형, DIAGINSTREF 공유, 변형별 배치 |
| 모르는 내용 | 요소: 파일 전체 실패 / 속성: 조용히 버림 | 노드 단위 보존 + 출력 영향 판정(VAL-012/INF-001) |
| 디코딩 | 원시 정수만(선형 변환·텍스트표 미적용), NUMITER는 빈 목록, EOSITER는 1회, MUX 기본 분기 무시 | 해당 없음(요청 전문 미리보기만) |
| 상태 조건 | mayBeExec/trans 인덱스 튜플 그대로 | 상태 그룹·전이 모델 |
| 출처 | 바인딩 생성 스키마 출처 문서화 없음 | 도움말·예제 근거 주석 |

## 버전 호환

- 실행 결과 CANoe 예제 6개가 Phase 0(xsdata)에서 ParserError로 실패한다(위 표). strict=False로도 막을 수 없다.
- 조용히 버려지는 의미 있는 속성: `SERVICE@act`(비활성 서비스), `STATICCOMP@bl`(5.x), `STATUSDTCPROXYCOMP@dtref`.

## 참고할 만한 것

1. **CddTools.cdd** (12.0.104, 변형 2, 프로토콜 서비스 105) — 새 회귀 코퍼스. 저장소에 복사하지 않고 환경 변수 경로로 읽는다(출처 불명).
   DiagAuthor 현재 결과: 서비스 54개 가져옴, 보존 124(차단 105), VAL-012 72.
   주요 공백: CONTENTCOMP 15, MUXCOMPCONT 11, 비선형 타입 예외값 7, 해결 안 된 DID 참조 10, 길이·바이트 순서 IDENT 5, 0x2A 주기 응답(SID 없는 응답) 3, 비트필드 초과 3, MUXCOMP 3, ECU 수준 FAULTMEMORY 1, 32비트 TEXTTBL bm=255.
2. **SAE J2012 DTC 표시(P/C/B/U + 4자리)** — 표준 규칙이라 표시 기능으로 채택 가능.
3. **요소 어휘 목록** — DATATYPES 13종(EOSITERDT, VALTBL, COMPTBL, FORMULADT, PROCDT, NUMITERDT, ENDMKITERDT, STATICITERRDT 등) 점검 목록으로만 사용.

## 채택하지 않을 것

- 파서 교체·병행: 변형·보존·쓰기 모델이 없고 버전 내성이 더 낮다.
- `candela.py` 바인딩 복사: 생성 스키마 출처가 불명이다. Vector 스키마 파생일 수 있어 저장소 규칙(Vector 파일·추출 스키마 금지)과 충돌할 수 있다.
- DTCSTATUSMASK `conv`(req/optyes/optno) 해석(`supported = conv != optno`): 근거 없는 추정이라 docs/16 #6의 "확인 필요"를 해소하지 못한다.
- MUX/NUMITER/CONTENTCOMP 처리: 디코딩이 불완전해 의미 근거로 쓸 수 없다. 구조 참고만 한다.

## DiagAuthor 쪽 발견 사항

비교로 드러난 DiagAuthor 공백(cdd_utils는 맞게 만드는 것):
- 상태 그룹 종류 `authorization`(UDS 13 템플릿) 미지원 → 그 상태를 참조하는 0x11 Hard/Soft Reset까지 가져오지 못한다.
- CONTENTCOMP(구성요소 묶음) 미지원 → 0x19 01/14/18, 0x2C 01, 0x23/0x34 등.
- 슬롯의 `DATAOBJ spec=no` 비트필드(0x28 CommunicationType) 미지원.
- 0x2A 주기 응답(SID 없는 긍정 응답)을 "SID 불일치"로 거부.
- 복합 멤버 DID(DevelopmentData 등) 미지원 → 그 DID를 읽는 서비스도 빠진다.


- `cdd_model.resolve`: `dest="any"`인 구성요소에 `maxbl`이 없으면 `integer()`의 입력 안내 문구("정수 또는 0x로 시작하는 16진수…")가 보존 사유로 새어 나온다(CddTools 6건). 사유를 "가변 길이 any 구성요소"로 바꿔야 한다.
- Synthetic.cdd(`ECUDOC@doctype` 없음, cdd_utils 작성자의 수제 파일)는 거부된다. 실제 Vector 출력이 아니므로 정책 유지.

## 재검토(20차) — 반영 결과

- cdd_utils 바인딩이 갱신되어 이제 CANoe 예제·cantools CDD를 모두 읽는다. 새 시험 문서: cantools 테스트 CDD 4개(2.0.5 ×3, 10.0.108).
- 반영:
  - CANdela 2.x 형식 허용. 2.0.5 어휘가 5.x~13.x와 같다.
  - STRUCTDT를 packet 타입으로 가져온다(도움말 110 표 30). 0x28 CommunicationType이 여기에 해당한다.
  - BCD 비트 마스크는 타입 길이를 덮으면 전체 마스크로 본다.
  - cdd_utils 예제 폴더를 추가 시험 문서로 쓴다(`DIAGAUTHOR_EXTRA_CDD_DIRS`).
- 결과: Vector 공용 문서 서비스 합계 656 → 670이다. 2.0.5 파일은 서비스 20~22개로 열린다.
- 보류:
  - J2012 표시: DTC 형식을 CDD만으로 알 수 없다.
  - DTCSTATUSMASK conv 해석: high 검토가 필요하다.
  - MUX, ECU 수준 FAULTMEMORY/DOMAINDATAPROXYCOMP: 모델 설계 참고로만 기록했다.
