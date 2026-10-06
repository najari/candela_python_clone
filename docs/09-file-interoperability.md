# 파일 교환·호환성 명세

[목차](README.md) · [분석 근거](01-reference-analysis.md) · [테스트](11-validation-and-testing.md)

## 지원 선언과 입력 한계

| 형식 | MVP 계약 | 아직 보장하지 않는 것 |
|---|---|---|
| CDD | CANdela 형식 2.x~13.x 읽기(확인: 2.0.5·5.0.100·5.0.111·6.0.120·12.0.104·13.0.100·13.0.101·13.0.103), 확인된 UDS 구조 부분집합 해석 | 다른 버전·모든 OEM·모든 서비스·완전한 왕복 |
| CDDT | 문서 템플릿을 CDD와 같은 해석 경로로 읽어 새 프로젝트의 시작점으로 가져오기(15차) | CDDT 쓰기·템플릿 편집·템플릿 갱신(문서 업그레이드)·OEM 제약 완전 재현 |
| ODX | 2.2.0 `.odx-d` 진단 데이터 부분집합 쓰기 | 전체 범주·ODX 2.0.1 출력·PDX·실차 통신 설정 완비 |
| CSV | DTC의 UTF-8 목록 읽기/쓰기 | XLS/XLSX·Excel COM·임의 OEM 열 자동 해석 |
| `.diagproj` | 자체 schema 1 저장·재열기 | CDD 파일처럼 원본 도구에서 직접 열기 |

위 표는 구현 목표다. 현재 변환기 실행 결과가 아니다. 정식 ODX 2.2.0 XSD와 관련 규칙의 확보, 로컬 외부 도구의 실제 사용 가능 여부는 TASK-02에서 확인한다.

초기 입력 제한: CDD 100MiB, XML 깊이 128, 단일 텍스트 8MiB, CSV 50MiB/100,000행, 한 프로젝트 객체 500,000개. 초과는 이유를 표시하고 중단한다. 이는 제품의 초기 자원 한계이며 표준의 제한이 아니다. 대규모 고객 자료로 조정할 때 성능·메모리 테스트를 함께 갱신한다.

## CDD 가져오기 파이프라인

1. 원본을 바이트로 읽고 크기·해시·XML 루트·dtdvers를 확인한다.
2. 외부 DTD 로딩, 엔터티 확장, 네트워크 접근을 비활성화한다. 외부 SYSTEM 선언 자체는 허용하지만 자원을 조회하지 않는다. 미해결 사용자 엔터티는 명시 오류다.
3. 첫 패스에서 원본 id·oid·노드 경로와 프로토콜/템플릿 연결을 수집한다.
4. 두 번째 패스에서 dtref, tmplref, didRef, DIAGINSTREF 등 참조를 해석한다.
5. 지원 객체를 자체 DTO로 변환하고 변형 membership을 구성한다.
6. 원본 의미를 지원/부분 지원/미지원/오류로 분류한다.
7. 미리보기와 영향 보고를 제공한 뒤 새 작업 프로젝트에 한 번에 적용한다.

| CDD 개념/요소 | 내부 표현 | 처리 규칙 |
|---|---|---|
| CANDELA/ECUDOC | project_meta + source_files | 원본 버전·언어·해시 보존 |
| ECU / VAR | ecu / variants | base 속성과 참조 포함 관계 해석 |
| DID / DIDREF | dids / variant_dids | DID 정의 수와 변형별 사용 수를 구분 |
| DIAGCLASS / DIAGINST / DIAGINSTREF | classes / instances / memberships | 클래스 템플릿 참조로 역할 결정; 이름으로만 추측하지 않음 |
| SERVICE / PROTOCOLSERVICE / tmplref | services + telegrams | 고정 상수·proxy·인스턴스 값을 합쳐 실질 구조를 해석 |
| IDENT / LINCOMP / TEXTTBL | data_types / conversion_entries | encoding·bl·bo·COMP·TEXTMAP의 증명된 조합만 매핑 |
| DATAOBJ / STRUCTURE | data_objects / structures | dtref·상수·기본값·순서 보존 |
| NAME/DESC/TUV | localized_texts | 모든 입력 언어 보존, 서식은 원본 XML도 별도 보존 |
| DTC 관련 RECORD/정의·참조 | dtcs / variant_dtcs / attributes | record template의 필드 의미로 코드 열을 식별; 태그명 DTC의 존재를 가정하지 않음 |
| STATEGROUP/STATE/mayBeExec/trans | groups/states/conditions/transitions | 그룹 범위와 출발·도착 의미를 함께 해석 |
| Snapshot/Extended Data/Events/Job 등 | unsupported_nodes | 본문 보존, 영향을 받는 객체의 편집·출력 제한 |

`bo` 등의 숫자 속성은 설치 예제와 실제 출력 비교로 해석을 증명해야 한다. 21 같은 값의 의미를 명칭만 보고 결정하지 않는다. 지원할 수 없는 속성 조합은 미지원으로 분류한다.

CDDT의 실행 지침·외부 action·첨부물은 실행하지 않는다. 소유자 인증·권한·잠금의 의미를 해석하지 못하면 관련 객체를 읽기 전용으로 가져온다.

현재 구현 상태(15차): CDDT는 CDD와 같은 CANDELA 형식이며 `ECUDOC@doctype`만 다르다(inst = CDD, tmpl = CDDT; CANdelaStudio 13 설치 예제 템플릿 3종으로 확인). 템플릿은 프로토콜 서비스·클래스 템플릿·타입·상태·NRC와 기본 변형의 기본 인스턴스를 담는다. 가져오면 템플릿을 바탕으로 한 새 프로젝트가 되며, 원본 바이트는 CDD와 같이 보존하고 ECU 속성 `cdd.document_type=CDDT`, `cdd.template_oid`로 출처를 남긴다. 확장자와 문서 종류가 다르거나 다른 DTD 버전·문서 종류는 `UNSUPPORTED_VERSION`으로 거부한다. CDDT 쓰기·템플릿 편집·기존 프로젝트에 템플릿 갱신 적용은 P2 범위다.

스키마가 표현할 수 없는 SID·클래스·가변 타입은 허용된 SID나 타입으로 바꾸지 않는다. 그런 노드는 unsupported_nodes에 저장하고 가장 가까운 유효한 상위 객체를 owner로 연결한다. Qt 트리의 미지원 노드 ID는 unsupported_nodes의 별도 식별자이며 objects/subtype 객체로 가장하지 않는다. 상위 객체도 모델링할 수 없으면 owner를 NULL로 두고 의미 영향 범위 미확정에 따른 전체 출력 차단을 적용한다.

## 미지원·손실 정책

원본 바이트는 source_files, 객체별 출처는 source_mappings, 미지원 본문은 unsupported_nodes에 저장한다. 보존했다고 해서 편집·출력할 수 있는 것은 아니다.

- 구조 손상·필수 원본 ID 중복·핵심 참조 해석 불가: 가져오기 실패 또는 관련 하위 그래프 격리. 실패한 객체를 임의 생성하지 않는다.
- 고급 기능·미지원 서비스: 관련 객체를 읽기 전용으로 보존한다.
- 선택한 출력의 의존 그래프에 의미 있는 미지원 노드가 있음: 출력 차단.
- 의미 영향 범위를 산정할 수 없는 문서 전체 확장: 전체 출력 차단.
- 진단 의미와 무관하다고 확인된 화면 배치·서식 차이: 비차단 경고. 확인 근거를 지원표에 적는다.

보고서 예시는 다음과 같다. 숫자는 형식 예시이며 실제 파일 분석 결과가 아니다.

```json
{
  "status": "partial",
  "source_version": "13.0.101",
  "coverage": {"mapped": 10, "unsupported": 1, "failed": 0},
  "issues": [{"code": "UNSUPPORTED_SNAPSHOT", "severity": "warning",
    "blocks_export": true, "source_path": "/CANDELA/ECUDOC/ECU/...",
    "object_id": null, "message": "Snapshot 구조는 현재 출력 범위에 포함할 수 없습니다."}]
}
```

객체 수는 정의 수, 참조 수, 변환 산출 객체 수를 따로 기록한다. CDD의 서비스 59개가 있다는 사실을 자체 지원 서비스 59개로 보고하지 않는다.

## ODX 2.2.0 출력

첫 출력은 진단 데이터 설명 파일이다. 통신 파라미터·버스 연결·ISO-TP 설정을 완비한 실행 가능 테스트 데이터베이스로 주장하지 않는다. 외부 재열기는 기술적 파싱과 의미 대조 단계이며 실제 통신 검증을 포함하지 않는다.

| 내부 의미 | ODX 방향 | 검증 |
|---|---|---|
| 프로젝트·변형 | DIAG-LAYER-CONTAINER의 BASE-VARIANT/ECU-VARIANT | 선택 변형의 유효 내용·식별자 보존 |
| Service | DIAG-SERVICE, REQUEST, POS-RESPONSE, NEG-RESPONSE | SID·subfunction·DID/routine ID·NRC |
| 단순 타입 | DATA-OBJECT-PROP, DIAG-CODED-TYPE, PHYSICAL-TYPE | 인코딩·비트 길이·byte order |
| 선형·열거 변환 | COMPU-METHOD 및 관련 scale | factor·offset·범위·label |
| 구조 | STRUCTURE 및 PARAM 참조 | 계산된 BYTE/BIT-POSITION·길이 |
| DTC 정의 | DTC-DOP와 DTC | 코드·설명·변형 범위 |
| 선형 계수 | 분수 계수는 COMPU-DENOMINATOR(도움말 110) | — |
| 상태 모델 | STATE-CHART·STATE·STATE-TRANSITION·START-STATE(도움말 180) | 실행 가능 상태 제한(PRE-CONDITION)은 근거가 없어 차단 |
| 0x19·0x14 전문 | DTC 값 → DTC-DOP 참조(빈 표는 RESERVED), 상태 바이트 → 비트 PARAM(비활성 비트 RESERVED), DTC 그룹 → TEXTTABLE DOP, 반복 → END-OF-PDU-FIELD | 스냅샷·확장 데이터 레코드는 가져오지만 ODX(ENV-DATA-DESC·MUX) 출력은 근거가 없어 차단 |
| 조건·전이 | 2.2.0 상태·전이 관련 모델 | 정식 규칙·외부 도구로 증명한 매핑만 허용 |

MVP에서는 각 선택 변형의 유효 진단 내용을 명시적으로 출력한다. 최적화된 ODX 상속을 도입하지 않으며, 원래 기본 변형이 선택되지 않아도 자기완결적인 기준 layer를 생성한다. 내부의 공유 identity는 `.diagproj`에서 보존하고 ODX에서는 의미가 같은 구조로 출력한다. 동일 참조를 중복 없이 해석할 수 있도록 export 전용 ID map을 만든다.

ID는 객체 UUID와 layer UUID에서 결정적으로 생성한다. SHORT-NAME은 qualifier를 바탕으로 하되 범위 내 충돌·문자 규칙을 검사하고 임의 조용한 이름 덮어쓰기를 하지 않는다. 출력 순서는 객체 종류·업무 식별자·UUID로 안정화하며 구조 멤버의 의미 순서는 보존한다.

실행 순서: 범위 선택 → 참조 폐쇄 계산 → 최신 규칙 검사 → 고정 revision 스냅샷 → 임시 XML → 공식 로컬 XSD → 자체 의미 대조 → 최종 파일 원자적 교체 → 보고서. 스키마가 없으면 VALIDATION_UNAVAILABLE로 실패하고 검증 완료 파일을 제공하지 않는다.

조건·전이 매핑을 증명하지 못한 객체는 출력 차단한다. 조건을 빼서 출력 성공으로 만들지 않는다. ODX 2.0.1 PDX 예제의 통과를 2.2.0의 통과로 대체하지 않는다.

## CSV 계약

UTF-8/UTF-8 BOM을 허용하고 기본 구분자는 쉼표다. 탭·세미콜론은 사용자 선택 후 미리보기에 반영한다. 줄바꿈은 CRLF/LF 모두 허용한다. 첫 행은 헤더다. 필수 열은 code, qualifier, name이며 description은 선택이다. 대상 변형·언어는 가져오기 설정에서 지정한다.

code는 `0x` 접두사가 있는 hex 또는 10진수다. 출력은 항상 `0x`와 6자리 대문자 hex를 쓴다. 빈 값, 부호, 범위 초과, 중복 qualifier/code는 오류다. 소스 내 중복과 대상 내 충돌을 구분한다. MVP는 기존 행 자동 갱신을 하지 않고 새 행만 삽입한다.

```csv
code,qualifier,name,description
0x123456,SupplyVoltageLow,전원 전압 낮음,자체 테스트 데이터
0x123457,TemperatureHigh,온도 높음,자체 테스트 데이터
```

출력은 표준 quoting과 줄바꿈 escaping을 사용한다. 텍스트 첫 문자가 스프레드시트 수식으로 해석될 수 있으면 원문 보존 CSV와 별개의 '스프레드시트 표시용' 옵션을 제시한다. 기본 교환 모드는 원문을 보존하며 자동 apostrophe 삽입은 하지 않는다.

참고: [ASAM ODX](https://www.asam.net/standards/detail/mcd-2-d/), [lxml XSD 검사](https://lxml.de/validation.html).
