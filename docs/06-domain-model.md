# 도메인 모델

[목차](README.md) · [DB](07-sqlite-schema.md) · [검증](11-validation-and-testing.md)

## 식별·텍스트·소유 관계

ObjectId는 내부 UUID 문자열이다. qualifier는 사용자와 내보내기에 쓰는 기술 명칭이며 전역 UUID를 대체하지 않는다. CDD ID는 source file hash와 함께 매핑한다. 복사 시 새 ObjectId를 부여하되 의미가 같은 DID 번호를 다른 변형에서 유지할 수 있다.

이름·설명은 object_id+field+locale로 저장한다. 화면 fallback 순서는 선택 언어→en-US→존재하는 언어의 사전순 첫 값이다. fallback은 표시 정책이며 원문 저장값을 변경하지 않는다. qualifier는 MVP 신규 입력에서 `[A-Za-z_][A-Za-z0-9_]*`를 허용한다. 가져온 불일치는 보존하고 출력 전 해결하도록 표시한다.

| 객체 | 필수 필드와 타입 | 관계·소유·삭제 |
|---|---|---|
| Project | UUID, schemaVersion int, revision int, defaultLocale | ECU 1개; 명령 이력·출처 소유 |
| Ecu | ObjectId, qualifier, 이름, manufacturer text | 프로젝트에 1개; variants 소유; 직접 삭제 금지 |
| Variant | ObjectId, ecuId, isBase bool, ordinal int, locked bool | 객체 membership 보유; 기본 변형은 1개 |
| DiagnosticClass | ObjectId, role enum | 인스턴스 분류; 사용 중 삭제 제한 |
| DiagnosticInstance | ObjectId, classId | services 소유, DIDs 참조; 여러 변형에서 공유 |
| Service | ObjectId, instanceId, sid u8, addressing, enabled | request/positive/negative telegram과 조건 소유 |
| Did | ObjectId, scopeId, identifier u16, structureId | 변형에 공유; 인스턴스·구조에서 참조 |
| Dtc | ObjectId, scopeId, code u24 | 변형별 포함; MVP는 목록 정의 |
| DataType | ObjectId, encoding, bitLength, byteOrder, conversion | 문서 범위 재사용; packet은 structure 참조 |
| DataObject | ObjectId, dataTypeId, default/constant typed literal | 구조의 데이터 항목; 기본값·상수값 소유 |
| Structure | ObjectId, kind payload/packet/bitfield | 순서가 있는 멤버; 순환 불가 |
| StateGroup | ObjectId, kind session/security, initialStateId | states 소유; 문서 범위 정의 |
| State | ObjectId, groupId, code u8 | 서비스 허용·전이에서 참조 |

공통 Object에는 supported/readOnly와 이름 검색 키를 둔다. DB에서 보장하지 못하는 타입별 object.kind 일치와 정확히 한 ECU·기본 변형의 존재는 명령 커밋 전 VAL-014/VAL-016으로 검사한다.

## 변형과 공유

Variant는 다른 Variant를 실시간 상속하지 않는다. created_from은 생성 출처만 나타낸다. 기본 변형에 객체를 추가해도 모든 파생 변형을 자동 변경하지 않는다. 추가할 변형을 사용자가 명시적으로 선택한다.

공유 DID를 한 변형에서만 변경할 때:

1. 해당 DID와 그 데이터를 소유한 구조·DataObject를 복제한다.
2. 선택 변형에서 그 DID를 사용하는 공유 인스턴스를 복제한다.
3. 복제 인스턴스의 소유 서비스·전문·조건을 복제하고 새 DID로 연결한다.
4. 구조 변경 없이 재사용 가능한 타입은 공유를 유지한다. 타입의 의미도 바꾸면 타입을 추가 복사한다.
5. 선택 변형의 membership만 교체하고 전체 참조 포함 조건을 검사한다.

인스턴스가 사용되는 모든 변형에 그 인스턴스가 참조하는 DID가 동일한 객체로 포함되어야 한다. 같은 숫자의 다른 DID 객체가 있다는 이유만으로 충족하지 않는다. 이는 원본의 공유 일관성 해석을 반영한 규칙이다.

## 수치·타입

MVP encoding은 unsigned, signed, ascii, bytes, packet이다. 정수 atom은 1..64비트, ascii/bytes는 8비트 원소의 고정 배열이다. packet은 고정 구조를 참조한다. IEEE 부동소수점 wire 타입·가변 배열·종료문자 기반 필드는 P1이다.

unsigned 범위는 0..2^n−1, signed 범위는 −2^(n−1)..2^(n−1)−1이다. signed wire 인코딩은 2의 보수다. byte order는 byte-aligned 다바이트 정수에 적용한다.

정수 리터럴은 부호와 10진수의 정규 문자열로, 계수·오프셋은 지수 표기 없는 Decimal 문자열로 저장한다. MVP의 계수·오프셋은 유효숫자 최대 28자리, 정수부 최대 18자리, 소수부 최대 18자리로 제한한다. 계산에는 명시 precision 80자리를 사용하고 NaN·Infinity·범위를 넘는 입력은 거절한다. 이 입력 제한을 넘는 CDD 변환식은 정밀도를 낮추지 않고 미지원으로 보존한다. float를 경유하지 않으며 UI 표시 자릿수와 실제 저장값은 분리한다.

선형 변환은 physical = raw × factor + offset이다. factor 0은 MVP에서 거절한다. 역변환 결과가 정수가 아니면 오류이며 반올림하지 않는다. 텍스트 테이블은 겹치지 않는 닫힌 raw 범위→언어별 label을 사용하고 역변환은 단일 raw에 대응하는 항목만 허용한다.

## 전문 배치

멤버는 data, reserved, structure, did_data, did_with_identifier, literal, dtc, iteration, fault_record 중 하나다. 위치는 ordinal과 선행 멤버 길이에서 계산하며 사용자 입력 offset을 저장하지 않는다.

dtc는 변형 DTC 표(DTC-DOP)의 24비트 DTC 값이다. iteration은 전용 항목 구조를 PDU 끝까지 반복하는 멤버로(ODX END-OF-PDU-FIELD), 요청·응답 전문 구조의 마지막 멤버로만 둘 수 있다. 항목은 바이트 단위 길이여야 하고 반복을 다시 담을 수 없다. 반복 수는 전문 길이에서 정해지며 min_items는 최소 반복 수다. 근거: 예제 UDS-ExampleEcu.pdx(CANdelaStudio 5.0 ODX 출력)의 0x19 02 ListOfDTC, 도움말 100_ODXExport "Data objects for Fault Memory". fault_record(schema 8)는 고장 메모리 레코드 자리다. sr/edr은 8비트 레코드 번호, sd/ed는 번호·DTC에 따라 내용이 정해지는 가변 데이터이며 구조의 마지막 멤버여야 한다. 내용은 고장 메모리 인스턴스의 snapshot_records·snapshot_default_dids·snapshot_dtc_dids·extended_records가 정한다. sd/ed의 ODX 대응은 근거가 없어 출력을 막는다. reserved는 원래 이름(label)을 가질 수 있다.

선형 계수는 10진수 또는 기약분수("359/255")로 저장하며 변환은 정확한 유리수로 계산한다. 비유한 소수 물리값은 표시만 근삿값이다.

최상위 데이터는 byte-aligned여야 한다. 작은 비트 항목은 bitfield 구조에 둔다. 자체 프로파일의 비트 필드 순서는 첫 멤버가 첫 바이트의 MSB 쪽을 차지하도록 정의한다. CDD 비트 배치가 이 규칙과 다르면 변환 증거 없이는 지원하지 않는다. 이를 모든 프로토콜의 일반 규칙으로 주장하지 않는다.

reserved는 명시 길이를 가진 0 채움, literal은 고정 바이트다. SID·subfunction·DID·routine ID 헤더는 프로파일로 생성하고 UI에서 임의 변경하지 않는다. 데이터 배열 순서를 UI 표 정렬 순서와 혼동하지 않는다.

## 서비스·상태

0x22/0x2E는 단일 DID 연결만 MVP로 지원한다. 0x31은 routine ID와 subfunction, 고정 payload를 가진다. 0x10/0x11/0x3E는 프로파일에 허용된 subfunction과 고정 응답을 사용한다. 임의 SID 구성, service 0x27 실행, seed/key 알고리즘은 제외한다.

허용 조건은 그룹 간 AND, 그룹 안에서는 현재 상태에 대응하는 allowed 값으로 평가한다. 서비스에 관련 없는 그룹은 모든 상태에서 허용되며 전이가 없다. 관련 그룹은 모든 상태의 행을 명시한다. 각 출발 상태에서 전이는 최대 하나이고 allowed=false이면 전이할 수 없다.

## 삭제

업무 객체 간 참조는 기본 RESTRICT다. membership·출처 매핑처럼 대상에 종속된 연결만 cascade를 허용한다. 소유 집합 삭제는 Repository의 명시적 역순 삭제 명령으로 수행하며 objects와 subtype 테이블이 서로 고아가 되지 않도록 한다. 사용 중인 타입·기본 변형·유일 ECU는 일반 삭제할 수 없다.
