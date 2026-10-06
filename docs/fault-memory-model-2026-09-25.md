# 0x19·0x14 고장 메모리 전문 모델 (12차 스키마 7, 13차 스키마 8)

## 근거
- `Import/UDS-ExampleEcu.pdx`: CANdelaStudio 5.0이 출력한 ODX 2.0.1(`ASAMOdxExport201.dll 5.0.104`). CDD 구성요소가 ODX로 어떻게 나가는지 직접 보여 준다.
- 도움말 100_ODXExport "Data objects for Fault Memory"(DTC table, GroupOfDtc, StatusOfDtc), 190(ODX 2.2.0 RESERVED에는 코드 타입이 없음).

## 대응
| CDD | 모델 | ODX |
|---|---|---|
| EOSITERCOMP (minNumOfItems) | `structure_members.kind='iteration'` + 전용 항목 구조, `min_items` | END-OF-PDU-FIELD → BASIC-STRUCTURE |
| SIMPLEPROXYCOMP dest=dtc (슬롯 = RECORDDATAOBJ rtSpec=faultMemory) | `kind='dtc'` (24비트) | VALUE → 변형 DTC-DOP. DTC 표가 비면 RESERVED |
| STATUSDTCPROXYCOMP (슬롯 = STRUCT) | 8비트 비트필드. GAPDATAOBJ → 예약 비트(이름은 `label`, 13차) | 활성 비트는 VALUE, 비활성 비트는 RESERVED |
| GROUPOFDTCPROXYCOMP (GODTCDATAOBJ + 지역 TEXTTBL, def) | 데이터 객체 + 지역 표 타입, 기본값 | 24비트 TEXTTABLE DOP |
| SIMPLEPROXYCOMP dest=any, 슬롯 없음 | 구성요소 이름의 바이트 타입 | A_BYTEFIELD IDENTICAL |
| SIMPLEPROXYCOMP dest=sr/sd/edr/ed (13차) | `kind='fault_record'`, `record_role`. 정의는 DIAGINST/FAULTMEMORY → `snapshot_records`, `snapshot_default_dids`, `snapshot_dtc_dids`, `extended_records` | **차단**. CANdela 13 형식(DID 목록 스냅샷)의 ENV-DATA-DESC·MUX 구성은 근거가 없다(PDX는 CANdela 5.0의 다른 모델) |

## 규칙
- 반복은 요청·응답 전문 구조의 마지막 멤버로만 둔다. 항목은 바이트 단위 길이이며 반복을 다시 담지 않는다(VAL-005).
- 가변 레코드 데이터(sd/ed)는 구조의 마지막 멤버여야 한다(VAL-005). 서비스 인스턴스에 레코드 정의가 있어야 한다(VAL-021). ODX 근거가 없다는 경고는 VAL-012로 표시한다.
- ABS 예제: 0x19 01/02/04/06/0A와 0x14를 원문 배치(읽기 전용) 서비스로 가져온다. 서비스 53 → 57(12차) → 59(13차, 전부).
- 스냅샷 레코드: 0x10 First_Occurrence, 0x20 Last_Occurrence. 기본 DID는 0x010A, 0x010D이고 DTC 0x402011과 0x402012는 0x0107을 더한다. 확장 데이터 레코드: 0x00, 0x0A, 0x0B, 0x0C.

## 남은 것
- 0x19 04/06의 ODX 출력: 근거가 확보되면 구현한다.
- EVENTDATA(표시등, 운전 주기, 디바운스, 조건): ECU 설정이라 보존한다.
- GroupOfDtc individualDtcs=1: 근거 예제가 없어 보존한다.

## 함께 고친 것
- ODX 사전 요소 순서를 Vector 출력에 맞췄다: DTC-DOPS → DATA-OBJECT-PROPS → STRUCTURES → END-OF-PDU-FIELDS → UNIT-SPEC → TABLES.
- 예약 비트를 RESERVED PARAM으로 출력한다.
- 데이터 객체와 구조의 경로는 그것을 담은 DID나 서비스로 연결한다. 레코드는 고장 메모리 인스턴스로 연결한다.
