"""프로젝트 파일 스키마 마이그레이션. 작업 DB 사본에만 적용하며 원본 .diagproj는 저장 전까지 바꾸지 않는다."""
import hashlib
import json
import uuid
from datetime import datetime, timezone

from .domain import DomainError

CURRENT_VERSION = 13

# from_version → 다음 버전으로 올리는 SQL. schema.sql의 새 정의와 같은 결과가 되도록 유지한다.
SCRIPTS = {
    1: """
ALTER TABLE data_types ADD COLUMN raw_lower TEXT CHECK(raw_lower IS NULL OR conversion='linear');
ALTER TABLE data_types ADD COLUMN raw_upper TEXT CHECK(raw_upper IS NULL OR conversion='linear');
CREATE TABLE raw_exclusions (
  data_type_id TEXT NOT NULL REFERENCES data_types(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  locale TEXT NOT NULL,
  lower_raw TEXT NOT NULL,
  upper_raw TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('undef','noSignal','invalidSignal')),
  label TEXT NOT NULL,
  PRIMARY KEY(data_type_id,ordinal,locale)
) STRICT;
""",
    2: """
-- schema 3: 테이블 CHECK는 ALTER로 바꿀 수 없어 새 정의로 다시 만든다(foreign_keys=OFF 상태에서 실행).
CREATE TABLE services_v3 (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  instance_id TEXT NOT NULL REFERENCES diagnostic_instances(id) ON DELETE RESTRICT,
  sid INTEGER NOT NULL CHECK(sid BETWEEN 0 AND 255),
  subfunction INTEGER CHECK(subfunction BETWEEN 0 AND 127),
  routine_identifier INTEGER CHECK(routine_identifier BETWEEN 0 AND 65535),
  physical INTEGER NOT NULL DEFAULT 1 CHECK(physical IN (0,1)),
  functional INTEGER NOT NULL DEFAULT 0 CHECK(functional IN (0,1)),
  suppress_positive INTEGER NOT NULL DEFAULT 0 CHECK(suppress_positive IN (0,1)),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  -- schema 3: uds_fixed = 자체 고정 프로파일(헤더 자동 생성·편집 가능), raw = 가져온 원문 배치(읽기 전용)
  profile TEXT NOT NULL DEFAULT 'uds_fixed' CHECK(profile IN ('uds_fixed','raw')),
  CHECK(physical=1 OR functional=1),
  CHECK(profile='raw' OR sid IN (16,17,34,46,49,62)),
  CHECK(profile='raw' OR (sid=49 AND routine_identifier IS NOT NULL) OR (sid!=49 AND routine_identifier IS NULL)),
  CHECK(profile='raw' OR (sid IN (16,17,49,62) AND subfunction IS NOT NULL)
     OR (sid IN (34,46) AND subfunction IS NULL AND suppress_positive=0)),
  CHECK(profile='uds_fixed' OR (subfunction IS NULL AND routine_identifier IS NULL AND suppress_positive=0))
) STRICT;

INSERT INTO services_v3(id,instance_id,sid,subfunction,routine_identifier,physical,functional,suppress_positive,enabled,profile) SELECT id,instance_id,sid,subfunction,routine_identifier,physical,functional,suppress_positive,enabled,'uds_fixed' FROM services;
DROP TABLE services;
ALTER TABLE services_v3 RENAME TO services;
CREATE INDEX ix_services_instance ON services(instance_id);
CREATE TABLE diagnostic_classes_v3 (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  role TEXT NOT NULL CHECK(role IN ('session','reset','read','write','routine','tester_present','fault_memory','other'))
) STRICT;
INSERT INTO diagnostic_classes_v3(id,role) SELECT id,role FROM diagnostic_classes;
DROP TABLE diagnostic_classes;
ALTER TABLE diagnostic_classes_v3 RENAME TO diagnostic_classes;
CREATE TABLE raw_exclusions_v3 (
  data_type_id TEXT NOT NULL REFERENCES data_types(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  locale TEXT NOT NULL,
  lower_raw TEXT NOT NULL,
  upper_raw TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('undef','noSignal','invalidSignal','notImplemented','unexpectedResult')),
  label TEXT NOT NULL,
  PRIMARY KEY(data_type_id,ordinal,locale)
) STRICT;
INSERT INTO raw_exclusions_v3(data_type_id,ordinal,locale,lower_raw,upper_raw,kind,label) SELECT data_type_id,ordinal,locale,lower_raw,upper_raw,kind,label FROM raw_exclusions;
DROP TABLE raw_exclusions;
ALTER TABLE raw_exclusions_v3 RENAME TO raw_exclusions;
""",
    3: """
-- schema 4: 탐색 트리용 변형별 클래스 소속·순서, 인스턴스 순서, 싱글턴 클래스.
-- 이전 파일에는 문서 순서가 없으므로 기존 행 순서(rowid)를 순서로 삼는다.
ALTER TABLE diagnostic_classes ADD COLUMN singleton INTEGER NOT NULL DEFAULT 0 CHECK(singleton IN (0,1));
ALTER TABLE variant_instances ADD COLUMN ordinal INTEGER NOT NULL DEFAULT 0 CHECK(ordinal>=0);
CREATE TABLE variant_classes (
  variant_id TEXT NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
  class_id TEXT NOT NULL REFERENCES diagnostic_classes(id) ON DELETE RESTRICT,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  PRIMARY KEY(variant_id,class_id),
  UNIQUE(variant_id,ordinal)
) STRICT;
CREATE INDEX ix_variant_classes_class ON variant_classes(class_id);
UPDATE variant_instances SET ordinal=(
  SELECT count(*) FROM variant_instances o
  JOIN diagnostic_instances oi ON oi.id=o.instance_id
  JOIN diagnostic_instances me ON me.id=variant_instances.instance_id
  WHERE o.variant_id=variant_instances.variant_id AND oi.class_id=me.class_id AND o.rowid<variant_instances.rowid);
INSERT INTO variant_classes(variant_id,class_id,ordinal)
  SELECT variant_id,class_id,row_number() OVER (PARTITION BY variant_id ORDER BY first)-1
  FROM (SELECT vi.variant_id,i.class_id,min(vi.rowid) first FROM variant_instances vi
        JOIN diagnostic_instances i ON i.id=vi.instance_id GROUP BY vi.variant_id,i.class_id);
""",
    4: """
-- schema 5: objects.kind에 nrc·protocol_service·job_container 추가(CHECK 변경은 테이블 재생성, foreign_keys=OFF 상태).
CREATE TABLE objects_v5 (
  id TEXT PRIMARY KEY CHECK(length(id)=36),
  kind TEXT NOT NULL CHECK(kind IN (
    'ecu','variant','did','dtc','diagnostic_class','diagnostic_instance',
    'service','data_type','data_object','structure','state_group','state',
    'nrc','protocol_service','job_container')),
  qualifier TEXT NOT NULL,
  qualifier_key TEXT NOT NULL,
  supported INTEGER NOT NULL DEFAULT 1 CHECK(supported IN (0,1)),
  read_only INTEGER NOT NULL DEFAULT 0 CHECK(read_only IN (0,1))
) STRICT;
INSERT INTO objects_v5(id,kind,qualifier,qualifier_key,supported,read_only) SELECT id,kind,qualifier,qualifier_key,supported,read_only FROM objects;
DROP TABLE objects;
ALTER TABLE objects_v5 RENAME TO objects;
CREATE INDEX ix_objects_qualifier ON objects(kind,qualifier_key,id);
-- 원본 변형: 이전 파일에는 원본·참조 구분이 없으므로 기본 변형을, 없으면 순서가 가장 앞선 사용 변형을 원본으로 삼는다.
ALTER TABLE diagnostic_instances ADD COLUMN owner_variant_id TEXT REFERENCES variants(id) ON DELETE SET NULL;
UPDATE diagnostic_instances SET owner_variant_id=(
  SELECT vi.variant_id FROM variant_instances vi JOIN variants v ON v.id=vi.variant_id
  WHERE vi.instance_id=diagnostic_instances.id ORDER BY v.is_base DESC, v.ordinal LIMIT 1);
-- schema 5: 템플릿의 부정 응답 코드 목록(CDD NEGRESCODES). 서비스의 NRC 이름 표시에 쓴다.
CREATE TABLE nrcs (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  code INTEGER NOT NULL UNIQUE CHECK(code BETWEEN 0 AND 255)
) STRICT;

-- schema 5: 템플릿 프로토콜 서비스의 읽기 전용 요약(CDD PROTOCOLSERVICES). 출력 의미에는 쓰지 않는다.
CREATE TABLE protocol_services (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  request_text TEXT NOT NULL,
  positive_text TEXT NOT NULL,
  negative_text TEXT NOT NULL,
  template_count INTEGER NOT NULL CHECK(template_count>=0),
  service_count INTEGER NOT NULL CHECK(service_count>=0)
) STRICT;

-- schema 5: Job 컨테이너(CDD JOBCNR). 내용은 보존 원문(unsupported_nodes, 소유자 = 컨테이너)으로만 둔다.
CREATE TABLE job_containers (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  owner_variant_id TEXT REFERENCES variants(id) ON DELETE SET NULL
) STRICT;

CREATE TABLE variant_jobs (
  variant_id TEXT NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
  job_id TEXT NOT NULL REFERENCES job_containers(id) ON DELETE RESTRICT,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  PRIMARY KEY(variant_id,job_id),
  UNIQUE(variant_id,ordinal)
) STRICT;
CREATE INDEX ix_variant_jobs_job ON variant_jobs(job_id);

""",
    5: """
CREATE TABLE command_receipts (
  command_id TEXT PRIMARY KEY CHECK(length(command_id)=36),
  request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
  action TEXT NOT NULL,
  applied_revision INTEGER NOT NULL CHECK(applied_revision>=0),
  selected_id TEXT,
  completed_at TEXT NOT NULL
) STRICT;
""",
    6: """
-- schema 7: 구조 멤버에 dtc·iteration 종류와 min_items 추가(CHECK 변경은 테이블 재생성, foreign_keys=OFF 상태).
CREATE TABLE structure_members_v7 (
  structure_id TEXT NOT NULL REFERENCES structures(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  kind TEXT NOT NULL CHECK(kind IN ('data','reserved','structure','did_data','did_with_identifier','literal','dtc','iteration')),
  data_object_id TEXT UNIQUE REFERENCES data_objects(id) ON DELETE RESTRICT,
  nested_structure_id TEXT REFERENCES structures(id) ON DELETE RESTRICT,
  did_id TEXT REFERENCES dids(id) ON DELETE RESTRICT,
  reserved_bits INTEGER CHECK(reserved_bits>0),
  literal_bytes BLOB,
  -- schema 7: iteration = 중첩 구조(항목)를 PDU 끝까지 반복(ODX END-OF-PDU-FIELD). min_items는 최소 반복 수.
  --           dtc = 변형 DTC 표의 24비트 DTC 값(ODX DTC-DOP 참조).
  min_items INTEGER CHECK(min_items IS NULL OR min_items>=0),
  PRIMARY KEY(structure_id,ordinal),
  CHECK((kind='data' AND data_object_id IS NOT NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NULL)
     OR (kind='structure' AND data_object_id IS NULL AND nested_structure_id IS NOT NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NULL)
     OR (kind IN ('did_data','did_with_identifier') AND data_object_id IS NULL
         AND nested_structure_id IS NULL AND did_id IS NOT NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NULL)
     OR (kind='reserved' AND data_object_id IS NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NOT NULL AND literal_bytes IS NULL AND min_items IS NULL)
     OR (kind='literal' AND data_object_id IS NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NOT NULL AND length(literal_bytes)>0 AND min_items IS NULL)
     OR (kind='dtc' AND data_object_id IS NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NULL)
     OR (kind='iteration' AND data_object_id IS NULL AND nested_structure_id IS NOT NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NOT NULL))
) STRICT;
INSERT INTO structure_members_v7(structure_id,ordinal,kind,data_object_id,nested_structure_id,did_id,reserved_bits,literal_bytes)
  SELECT structure_id,ordinal,kind,data_object_id,nested_structure_id,did_id,reserved_bits,literal_bytes FROM structure_members;
DROP TABLE structure_members;
ALTER TABLE structure_members_v7 RENAME TO structure_members;
CREATE INDEX ix_members_did ON structure_members(did_id);
CREATE INDEX ix_members_nested ON structure_members(nested_structure_id);
""",
    7: """
-- schema 8: objects.kind에 snapshot_record·extended_record, 구조 멤버에 fault_record·record_role·label(재생성, foreign_keys=OFF 상태).
CREATE TABLE objects_v8 (
  id TEXT PRIMARY KEY CHECK(length(id)=36),
  kind TEXT NOT NULL CHECK(kind IN (
    'ecu','variant','did','dtc','diagnostic_class','diagnostic_instance',
    'service','data_type','data_object','structure','state_group','state',
    'nrc','protocol_service','job_container','snapshot_record','extended_record')),
  qualifier TEXT NOT NULL,
  qualifier_key TEXT NOT NULL,
  supported INTEGER NOT NULL DEFAULT 1 CHECK(supported IN (0,1)),
  read_only INTEGER NOT NULL DEFAULT 0 CHECK(read_only IN (0,1))
) STRICT;
INSERT INTO objects_v8(id,kind,qualifier,qualifier_key,supported,read_only) SELECT id,kind,qualifier,qualifier_key,supported,read_only FROM objects;
DROP TABLE objects;
ALTER TABLE objects_v8 RENAME TO objects;
CREATE INDEX ix_objects_qualifier ON objects(kind,qualifier_key,id);
CREATE TABLE structure_members_v8 (
  structure_id TEXT NOT NULL REFERENCES structures(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  kind TEXT NOT NULL CHECK(kind IN ('data','reserved','structure','did_data','did_with_identifier','literal','dtc','iteration','fault_record')),
  data_object_id TEXT UNIQUE REFERENCES data_objects(id) ON DELETE RESTRICT,
  nested_structure_id TEXT REFERENCES structures(id) ON DELETE RESTRICT,
  did_id TEXT REFERENCES dids(id) ON DELETE RESTRICT,
  reserved_bits INTEGER CHECK(reserved_bits>0),
  literal_bytes BLOB,
  -- schema 7: iteration = 중첩 구조(항목)를 PDU 끝까지 반복(ODX END-OF-PDU-FIELD). min_items는 최소 반복 수.
  --           dtc = 변형 DTC 표의 24비트 DTC 값(ODX DTC-DOP 참조).
  min_items INTEGER CHECK(min_items IS NULL OR min_items>=0),
  -- schema 8: fault_record = 고장 메모리 레코드 자리. sr/edr = 스냅샷·확장 데이터 레코드 번호(8비트),
  --           sd/ed = 번호·DTC에 따라 내용이 정해지는 레코드 데이터(가변 길이, snapshot_*·extended_records 참조).
  record_role TEXT CHECK(record_role IS NULL OR record_role IN ('sr','sd','edr','ed')),
  -- schema 8: 예약 비트의 원래 이름(CDD GAPDATAOBJ 등). 표시·ODX SHORT-NAME용.
  label TEXT CHECK(label IS NULL OR kind='reserved'),
  PRIMARY KEY(structure_id,ordinal),
  CHECK((kind='data' AND data_object_id IS NOT NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NULL)
     OR (kind='structure' AND data_object_id IS NULL AND nested_structure_id IS NOT NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NULL)
     OR (kind IN ('did_data','did_with_identifier') AND data_object_id IS NULL
         AND nested_structure_id IS NULL AND did_id IS NOT NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NULL)
     OR (kind='reserved' AND data_object_id IS NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NOT NULL AND literal_bytes IS NULL AND min_items IS NULL)
     OR (kind='literal' AND data_object_id IS NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NOT NULL AND length(literal_bytes)>0 AND min_items IS NULL)
     OR (kind='dtc' AND data_object_id IS NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NULL)
     OR (kind='iteration' AND data_object_id IS NULL AND nested_structure_id IS NOT NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NOT NULL)
     OR (kind='fault_record' AND data_object_id IS NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NULL)),
  CHECK((kind='fault_record') = (record_role IS NOT NULL))
) STRICT;
INSERT INTO structure_members_v8(structure_id,ordinal,kind,data_object_id,nested_structure_id,did_id,reserved_bits,literal_bytes,min_items)
  SELECT structure_id,ordinal,kind,data_object_id,nested_structure_id,did_id,reserved_bits,literal_bytes,min_items FROM structure_members;
DROP TABLE structure_members;
ALTER TABLE structure_members_v8 RENAME TO structure_members;
CREATE INDEX ix_members_did ON structure_members(did_id);
CREATE INDEX ix_members_nested ON structure_members(nested_structure_id);
-- schema 8: 고장 메모리 인스턴스의 스냅샷·확장 데이터 레코드(CDD DIAGINST/FAULTMEMORY).
-- 스냅샷: 레코드 번호 목록과, 레코드에 담기는 DID 목록(기본 + DTC별 대체).
CREATE TABLE snapshot_records (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  instance_id TEXT NOT NULL REFERENCES diagnostic_instances(id) ON DELETE RESTRICT,
  number INTEGER NOT NULL CHECK(number BETWEEN 0 AND 255),
  UNIQUE(instance_id,number)
) STRICT;
CREATE INDEX ix_snapshot_records_instance ON snapshot_records(instance_id);

CREATE TABLE snapshot_default_dids (
  instance_id TEXT NOT NULL REFERENCES diagnostic_instances(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  did_id TEXT NOT NULL REFERENCES dids(id) ON DELETE RESTRICT,
  PRIMARY KEY(instance_id,ordinal)
) STRICT;
CREATE INDEX ix_snapshot_default_did ON snapshot_default_dids(did_id);

CREATE TABLE snapshot_dtc_dids (
  instance_id TEXT NOT NULL REFERENCES diagnostic_instances(id) ON DELETE CASCADE,
  dtc_id TEXT NOT NULL REFERENCES dtcs(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  did_id TEXT NOT NULL REFERENCES dids(id) ON DELETE RESTRICT,
  PRIMARY KEY(instance_id,dtc_id,ordinal)
) STRICT;
CREATE INDEX ix_snapshot_dtc_did ON snapshot_dtc_dids(did_id);
CREATE INDEX ix_snapshot_dtc_dtc ON snapshot_dtc_dids(dtc_id);

-- 확장 데이터: 레코드 번호별 데이터 구조.
CREATE TABLE extended_records (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  instance_id TEXT NOT NULL REFERENCES diagnostic_instances(id) ON DELETE RESTRICT,
  number INTEGER NOT NULL CHECK(number BETWEEN 0 AND 255),
  structure_id TEXT NOT NULL REFERENCES structures(id) ON DELETE RESTRICT,
  UNIQUE(instance_id,number)
) STRICT;
CREATE INDEX ix_extended_records_instance ON extended_records(instance_id);
CREATE INDEX ix_extended_records_structure ON extended_records(structure_id);
""",
    8: """
-- schema 9: 가변 바이트 필드(PDU 끝까지, 최소·최대 길이)와 packed BCD 인코딩. data_types의 CHECK를 바꾸려면 표를 다시 만든다.
CREATE TABLE data_types_v9 (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  -- schema 9: bcd = packed BCD(한 자리 4비트). 근거: 도움말 110_ODXExport 표 31 numBCD → A_UINT32, BASE-TYPE-ENCODING BCD-P.
  encoding TEXT NOT NULL CHECK(encoding IN ('unsigned','signed','ascii','bytes','packet','bcd')),
  bit_length INTEGER,
  byte_order TEXT NOT NULL DEFAULT 'big' CHECK(byte_order IN ('big','little')),
  array_length INTEGER,
  conversion TEXT NOT NULL CHECK(conversion IN ('identity','linear','text_table')),
  factor_text TEXT,
  offset_text TEXT,
  unit TEXT NOT NULL DEFAULT '',
  structure_id TEXT REFERENCES structures(id) ON DELETE RESTRICT,
  -- schema 2: 선형 변환의 원시값 유효 범위(NULL=제한 없음). 마이그레이션의 ALTER TABLE과 같은 정의를 쓴다.
  raw_lower TEXT CHECK(raw_lower IS NULL OR conversion='linear'),
  raw_upper TEXT CHECK(raw_upper IS NULL OR conversion='linear'),
  -- schema 9: PDU 끝까지 이어지는 가변 바이트 필드(array_length NULL)의 최소 바이트 수.
  -- 근거: CDD SIMPLEPROXYCOMP dest=any(maxbl 없음) = UDS-ExampleEcu.pdx DtcSnapshotRecordNumber
  -- (A_BYTEFIELD, MIN-MAX-LENGTH-TYPE, TERMINATION=END-OF-PDU, MIN-LENGTH).
  min_length INTEGER CHECK(min_length IS NULL OR min_length BETWEEN 0 AND 1048576),
  -- 가변 바이트 필드의 최대 바이트 수(NULL = 제한 없음). CDD IDENT qty=field의 maxsz. ODX MIN-MAX-LENGTH-TYPE/MAX-LENGTH.
  max_length INTEGER CHECK(max_length IS NULL OR (min_length IS NOT NULL AND max_length BETWEEN min_length AND 1048576)),
  CHECK((encoding='packet' AND structure_id IS NOT NULL AND bit_length IS NULL
         AND array_length IS NULL AND conversion='identity' AND min_length IS NULL)
     OR (encoding IN ('unsigned','signed') AND structure_id IS NULL
         AND bit_length IS NOT NULL AND array_length IS NOT NULL
         AND bit_length BETWEEN 1 AND 64 AND array_length=1 AND min_length IS NULL)
     OR (encoding='bcd' AND structure_id IS NULL AND bit_length BETWEEN 4 AND 32 AND bit_length%4=0
         AND array_length=1 AND conversion='identity' AND min_length IS NULL)
     OR (encoding IN ('ascii','bytes') AND structure_id IS NULL
         AND bit_length IS NOT NULL AND array_length IS NOT NULL
         AND bit_length=8 AND array_length BETWEEN 1 AND 1048576 AND conversion='identity' AND min_length IS NULL)
     OR (encoding='bytes' AND structure_id IS NULL AND bit_length=8 AND array_length IS NULL
         AND min_length IS NOT NULL AND conversion='identity')),
  CHECK((conversion='linear' AND factor_text IS NOT NULL AND offset_text IS NOT NULL)
     OR (conversion!='linear' AND factor_text IS NULL AND offset_text IS NULL))
) STRICT;
INSERT INTO data_types_v9(id,encoding,bit_length,byte_order,array_length,conversion,factor_text,offset_text,unit,structure_id,raw_lower,raw_upper)
  SELECT id,encoding,bit_length,byte_order,array_length,conversion,factor_text,offset_text,unit,structure_id,raw_lower,raw_upper FROM data_types;
DROP TABLE data_types;
ALTER TABLE data_types_v9 RENAME TO data_types;
CREATE INDEX ix_types_structure ON data_types(structure_id);
""",
    9: """
-- schema 10: structure_members.kind에 mux 추가, state_groups.kind에 authorization·other:* 추가(CHECK 변경이라 표를 다시 만든다),
-- MUX 선택자·케이스 표. 기존 행은 그대로 옮긴다.
CREATE TABLE structure_members_v10 (
  structure_id TEXT NOT NULL REFERENCES structures(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  -- schema 10: mux = 선택자 값에 따라 케이스 구조가 정해지는 자리(CDD MUXDT·MUXCOMP). 선택자·케이스는 muxes·mux_cases.
  kind TEXT NOT NULL CHECK(kind IN ('data','reserved','structure','did_data','did_with_identifier','literal','dtc','iteration','fault_record','mux')),
  data_object_id TEXT UNIQUE REFERENCES data_objects(id) ON DELETE RESTRICT,
  nested_structure_id TEXT REFERENCES structures(id) ON DELETE RESTRICT,
  did_id TEXT REFERENCES dids(id) ON DELETE RESTRICT,
  reserved_bits INTEGER CHECK(reserved_bits>0),
  literal_bytes BLOB,
  -- schema 7: iteration = 중첩 구조(항목)를 PDU 끝까지 반복(ODX END-OF-PDU-FIELD). min_items는 최소 반복 수.
  --           dtc = 변형 DTC 표의 24비트 DTC 값(ODX DTC-DOP 참조).
  min_items INTEGER CHECK(min_items IS NULL OR min_items>=0),
  -- schema 8: fault_record = 고장 메모리 레코드 자리. sr/edr = 스냅샷·확장 데이터 레코드 번호(8비트),
  --           sd/ed = 번호·DTC에 따라 내용이 정해지는 레코드 데이터(가변 길이, snapshot_*·extended_records 참조).
  record_role TEXT CHECK(record_role IS NULL OR record_role IN ('sr','sd','edr','ed')),
  -- schema 8: 예약 비트의 원래 이름(CDD GAPDATAOBJ 등). 표시·ODX SHORT-NAME용.
  label TEXT CHECK(label IS NULL OR kind='reserved'),
  PRIMARY KEY(structure_id,ordinal),
  CHECK((kind='data' AND data_object_id IS NOT NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NULL)
     OR (kind='structure' AND data_object_id IS NULL AND nested_structure_id IS NOT NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NULL)
     OR (kind IN ('did_data','did_with_identifier') AND data_object_id IS NULL
         AND nested_structure_id IS NULL AND did_id IS NOT NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NULL)
     OR (kind='reserved' AND data_object_id IS NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NOT NULL AND literal_bytes IS NULL AND min_items IS NULL)
     OR (kind='literal' AND data_object_id IS NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NOT NULL AND length(literal_bytes)>0 AND min_items IS NULL)
     OR (kind='dtc' AND data_object_id IS NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NULL)
     OR (kind='iteration' AND data_object_id IS NULL AND nested_structure_id IS NOT NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NOT NULL)
     OR (kind IN ('fault_record','mux') AND data_object_id IS NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL AND min_items IS NULL)),
  CHECK((kind='fault_record') = (record_role IS NOT NULL))
) STRICT;
INSERT INTO structure_members_v10(structure_id,ordinal,kind,data_object_id,nested_structure_id,did_id,reserved_bits,literal_bytes,min_items,record_role,label)
  SELECT structure_id,ordinal,kind,data_object_id,nested_structure_id,did_id,reserved_bits,literal_bytes,min_items,record_role,label FROM structure_members;
DROP TABLE structure_members;
ALTER TABLE structure_members_v10 RENAME TO structure_members;
CREATE INDEX ix_members_did ON structure_members(did_id);
CREATE INDEX ix_members_nested ON structure_members(nested_structure_id);
CREATE TABLE state_groups_v10 (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  -- schema 10: authorization(UDS 13 템플릿)과 'other:<qualifier>'(spec 없는 그룹). 두 종류는 ODX 대응 미확인(가져오기에서 supported=0).
  kind TEXT NOT NULL UNIQUE CHECK(kind IN ('session','security','authorization') OR kind GLOB 'other:*'),
  initial_state_id TEXT NOT NULL,
  FOREIGN KEY(initial_state_id,id) REFERENCES states(id,group_id)
    DEFERRABLE INITIALLY DEFERRED
) STRICT;
INSERT INTO state_groups_v10(id,kind,initial_state_id) SELECT id,kind,initial_state_id FROM state_groups;
DROP TABLE state_groups;
ALTER TABLE state_groups_v10 RENAME TO state_groups;
-- schema 10: MUX 자리의 선택자(같은 전문의 앞쪽 데이터 객체)와 선택자 값 범위별 케이스 구조, 기본 구조(없으면 NULL).
CREATE TABLE muxes (
  structure_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  selector_object_id TEXT NOT NULL REFERENCES data_objects(id) ON DELETE RESTRICT,
  default_structure_id TEXT REFERENCES structures(id) ON DELETE RESTRICT,
  PRIMARY KEY(structure_id,ordinal),
  FOREIGN KEY(structure_id,ordinal) REFERENCES structure_members(structure_id,ordinal) ON DELETE CASCADE
) STRICT;
CREATE INDEX ix_muxes_selector ON muxes(selector_object_id);
CREATE INDEX ix_muxes_default ON muxes(default_structure_id);

CREATE TABLE mux_cases (
  structure_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  case_ordinal INTEGER NOT NULL CHECK(case_ordinal>=0),
  lower_raw TEXT NOT NULL,
  upper_raw TEXT NOT NULL,
  case_structure_id TEXT NOT NULL REFERENCES structures(id) ON DELETE RESTRICT,
  PRIMARY KEY(structure_id,ordinal,case_ordinal),
  FOREIGN KEY(structure_id,ordinal) REFERENCES muxes(structure_id,ordinal) ON DELETE CASCADE
) STRICT;
CREATE INDEX ix_mux_cases_structure ON mux_cases(case_structure_id);
""",
    10: """
-- schema 11: 변형 식별 패턴(CDD VAR/IDENTIFICATIONSET → ODX ECU-VARIANT-PATTERN). 패턴 하나 = 모두 맞아야 하는 조건 여러 개.
-- 조건 = 식별 서비스(DIAG-COMM)의 긍정 응답 데이터 객체(parameter = Qualifier)의 기대값(expected_value, 물리값 글자).
-- 근거: CANoe 예제 UDS-ExampleEcu-5.0.4.cdd ↔ 같은 문서의 Vector 출력 UDS-ExampleEcu-5.0.2.pdx(Part_Number = "Developer ECU").
CREATE TABLE variant_patterns (
  variant_id TEXT NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
  pattern INTEGER NOT NULL CHECK(pattern>=1),
  ordinal INTEGER NOT NULL CHECK(ordinal>=1),
  service_id TEXT NOT NULL REFERENCES services(id) ON DELETE RESTRICT,
  parameter TEXT NOT NULL,
  expected_value TEXT NOT NULL,
  PRIMARY KEY(variant_id,pattern,ordinal)
) STRICT;
CREATE INDEX ix_variant_patterns_service ON variant_patterns(service_id);

-- schema 11: 지원하지 않는 서비스 요청의 부정 응답 NRC(CDD UNSUPPSRVNEG → ODX GLOBAL-NEG-RESPONSE, 7F SID NRC).
CREATE TABLE unsupported_service_nrcs (
  nrc_id TEXT PRIMARY KEY REFERENCES nrcs(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0)
) STRICT;
""",
    11: """
-- schema 12: data_types.conversion에 formula(CDD FORMULADT) 추가와 수식 원문 열 formula_expression. CHECK 변경이라 표를 다시 만든다.
-- 근거: KWP 템플릿 FORMULADT EngineTemperature ↔ Vector 출력 UDS-ExampleEcu.pdx DOP(IDENTICAL, DESC 수식 문구). 기존 행은 그대로 옮긴다.
CREATE TABLE data_types_v12 (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  -- schema 9: bcd = packed BCD(한 자리 4비트). 근거: 도움말 110_ODXExport 표 31 numBCD → A_UINT32, BASE-TYPE-ENCODING BCD-P.
  encoding TEXT NOT NULL CHECK(encoding IN ('unsigned','signed','ascii','bytes','packet','bcd')),
  bit_length INTEGER,
  byte_order TEXT NOT NULL DEFAULT 'big' CHECK(byte_order IN ('big','little')),
  array_length INTEGER,
  -- schema 12: formula = CDD FORMULADT(수식 원문 formula_expression, 계산하지 않음). ODX는 IDENTICAL + DESC 수식 문구(Vector 출력과 같음).
  conversion TEXT NOT NULL CHECK(conversion IN ('identity','linear','text_table','formula')),
  factor_text TEXT,
  offset_text TEXT,
  unit TEXT NOT NULL DEFAULT '',
  structure_id TEXT REFERENCES structures(id) ON DELETE RESTRICT,
  -- schema 2: 선형 변환의 원시값 유효 범위(NULL=제한 없음). 마이그레이션의 ALTER TABLE과 같은 정의를 쓴다.
  raw_lower TEXT CHECK(raw_lower IS NULL OR conversion='linear'),
  raw_upper TEXT CHECK(raw_upper IS NULL OR conversion='linear'),
  -- schema 9: PDU 끝까지 이어지는 가변 바이트 필드(array_length NULL)의 최소 바이트 수.
  -- 근거: CDD SIMPLEPROXYCOMP dest=any(maxbl 없음) = UDS-ExampleEcu.pdx DtcSnapshotRecordNumber
  -- (A_BYTEFIELD, MIN-MAX-LENGTH-TYPE, TERMINATION=END-OF-PDU, MIN-LENGTH).
  min_length INTEGER CHECK(min_length IS NULL OR min_length BETWEEN 0 AND 1048576),
  -- 가변 바이트 필드의 최대 바이트 수(NULL = 제한 없음). CDD IDENT qty=field의 maxsz. ODX MIN-MAX-LENGTH-TYPE/MAX-LENGTH.
  max_length INTEGER CHECK(max_length IS NULL OR (min_length IS NOT NULL AND max_length BETWEEN min_length AND 1048576)),
  -- schema 12: coded→phys 수식 원문(CDD FORMULA usage=coded2Phys). 정수 타입에만.
  formula_expression TEXT,
  CHECK((encoding='packet' AND structure_id IS NOT NULL AND bit_length IS NULL
         AND array_length IS NULL AND conversion='identity' AND min_length IS NULL)
     OR (encoding IN ('unsigned','signed') AND structure_id IS NULL
         AND bit_length IS NOT NULL AND array_length IS NOT NULL
         AND bit_length BETWEEN 1 AND 64 AND array_length=1 AND min_length IS NULL)
     OR (encoding='bcd' AND structure_id IS NULL AND bit_length BETWEEN 4 AND 32 AND bit_length%4=0
         AND array_length=1 AND conversion='identity' AND min_length IS NULL)
     OR (encoding IN ('ascii','bytes') AND structure_id IS NULL
         AND bit_length IS NOT NULL AND array_length IS NOT NULL
         AND bit_length=8 AND array_length BETWEEN 1 AND 1048576 AND conversion='identity' AND min_length IS NULL)
     OR (encoding='bytes' AND structure_id IS NULL AND bit_length=8 AND array_length IS NULL
         AND min_length IS NOT NULL AND conversion='identity')),
  CHECK((conversion='linear' AND factor_text IS NOT NULL AND offset_text IS NOT NULL)
     OR (conversion!='linear' AND factor_text IS NULL AND offset_text IS NULL)),
  CHECK((conversion='formula' AND formula_expression IS NOT NULL AND length(trim(formula_expression))>0 AND encoding IN ('unsigned','signed'))
     OR (conversion!='formula' AND formula_expression IS NULL))
) STRICT;
INSERT INTO data_types_v12(id,encoding,bit_length,byte_order,array_length,conversion,factor_text,offset_text,unit,structure_id,raw_lower,raw_upper,min_length,max_length)
  SELECT id,encoding,bit_length,byte_order,array_length,conversion,factor_text,offset_text,unit,structure_id,raw_lower,raw_upper,min_length,max_length FROM data_types;
DROP TABLE data_types;
ALTER TABLE data_types_v12 RENAME TO data_types;
CREATE INDEX ix_types_structure ON data_types(structure_id);
""",
    12: """
-- schema 13: float 인코딩, 구간별 선형(scale_linear)·특성 곡선(tab_intp) 변환과 conversion_segments 표, 앞 길이 바이트(leading_length_bits).
-- CHECK 변경이라 data_types를 다시 만든다. 기존 행은 그대로 옮긴다.
CREATE TABLE data_types_v13 (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  -- schema 9: bcd = packed BCD(한 자리 4비트). 근거: 도움말 110_ODXExport 표 31 numBCD → A_UINT32, BASE-TYPE-ENCODING BCD-P.
  -- schema 13: float = IEEE 754 부동소수(32·64비트). 근거: Door CDD VehicleSpeed(enc=flt 32) ↔ UDS-ExampleEcu-5.0.2.pdx A_FLOAT32.
  encoding TEXT NOT NULL CHECK(encoding IN ('unsigned','signed','ascii','bytes','packet','bcd','float')),
  bit_length INTEGER,
  byte_order TEXT NOT NULL DEFAULT 'big' CHECK(byte_order IN ('big','little')),
  array_length INTEGER,
  -- schema 12: formula = CDD FORMULADT(수식 원문 formula_expression, 계산하지 않음). ODX는 IDENTICAL + DESC 수식 문구(Vector 출력과 같음).
  -- schema 13: scale_linear = 구간별 선형(CDD COMPTBL 여러 구간 → ODX SCALE-LINEAR), tab_intp = 특성 곡선(CDD VALTBL → ODX TAB-INTP).
  -- 구간·점은 conversion_segments. 근거: Door TestDatatypePiecewiseLinear·TestDatatypeCharacteristicCurve ↔ 5.0.2 PDX.
  conversion TEXT NOT NULL CHECK(conversion IN ('identity','linear','text_table','formula','scale_linear','tab_intp')),
  factor_text TEXT,
  offset_text TEXT,
  unit TEXT NOT NULL DEFAULT '',
  structure_id TEXT REFERENCES structures(id) ON DELETE RESTRICT,
  -- schema 2: 선형 변환의 원시값 유효 범위(NULL=제한 없음). 마이그레이션의 ALTER TABLE과 같은 정의를 쓴다.
  raw_lower TEXT CHECK(raw_lower IS NULL OR conversion='linear'),
  raw_upper TEXT CHECK(raw_upper IS NULL OR conversion='linear'),
  -- schema 9: PDU 끝까지 이어지는 가변 바이트 필드(array_length NULL)의 최소 바이트 수.
  -- 근거: CDD SIMPLEPROXYCOMP dest=any(maxbl 없음) = UDS-ExampleEcu.pdx DtcSnapshotRecordNumber
  -- (A_BYTEFIELD, MIN-MAX-LENGTH-TYPE, TERMINATION=END-OF-PDU, MIN-LENGTH).
  min_length INTEGER CHECK(min_length IS NULL OR min_length BETWEEN 0 AND 1048576),
  -- 가변 바이트 필드의 최대 바이트 수(NULL = 제한 없음). CDD IDENT qty=field의 maxsz. ODX MIN-MAX-LENGTH-TYPE/MAX-LENGTH.
  max_length INTEGER CHECK(max_length IS NULL OR (min_length IS NOT NULL AND max_length BETWEEN min_length AND 1048576)),
  -- schema 12: coded→phys 수식 원문(CDD FORMULA usage=coded2Phys). 정수 타입에만.
  formula_expression TEXT,
  -- schema 13: 앞에 붙는 길이 바이트(CDD sz=lszbyte·lsz2bytes·lsz3bytes·lsz4bytes)의 비트 수. 가변 바이트 필드에만.
  -- 근거: Door Memory_2_Byte_Size_Info ↔ 5.0.2 PDX LEADING-LENGTH-INFO-TYPE A_BYTEFIELD BIT-LENGTH 16.
  leading_length_bits INTEGER CHECK(leading_length_bits IS NULL OR leading_length_bits IN (8,16,24,32)),
  CHECK((encoding='packet' AND structure_id IS NOT NULL AND bit_length IS NULL
         AND array_length IS NULL AND conversion='identity' AND min_length IS NULL)
     OR (encoding IN ('unsigned','signed') AND structure_id IS NULL
         AND bit_length IS NOT NULL AND array_length IS NOT NULL
         AND bit_length BETWEEN 1 AND 64 AND array_length=1 AND min_length IS NULL)
     OR (encoding='bcd' AND structure_id IS NULL AND bit_length BETWEEN 4 AND 32 AND bit_length%4=0
         AND array_length=1 AND conversion='identity' AND min_length IS NULL)
     OR (encoding IN ('ascii','bytes') AND structure_id IS NULL
         AND bit_length IS NOT NULL AND array_length IS NOT NULL
         AND bit_length=8 AND array_length BETWEEN 1 AND 1048576 AND conversion='identity' AND min_length IS NULL)
     OR (encoding='bytes' AND structure_id IS NULL AND bit_length=8 AND array_length IS NULL
         AND min_length IS NOT NULL AND conversion='identity')
     OR (encoding='float' AND structure_id IS NULL AND bit_length IN (32,64) AND array_length=1
         AND conversion IN ('identity','linear') AND min_length IS NULL)),
  CHECK(leading_length_bits IS NULL OR (encoding='bytes' AND array_length IS NULL)),
  CHECK((conversion='linear' AND factor_text IS NOT NULL AND offset_text IS NOT NULL)
     OR (conversion!='linear' AND factor_text IS NULL AND offset_text IS NULL)),
  CHECK((conversion='formula' AND formula_expression IS NOT NULL AND length(trim(formula_expression))>0 AND encoding IN ('unsigned','signed'))
     OR (conversion!='formula' AND formula_expression IS NULL))
) STRICT;
INSERT INTO data_types_v13(id,encoding,bit_length,byte_order,array_length,conversion,factor_text,offset_text,unit,structure_id,raw_lower,raw_upper,min_length,max_length,formula_expression)
  SELECT id,encoding,bit_length,byte_order,array_length,conversion,factor_text,offset_text,unit,structure_id,raw_lower,raw_upper,min_length,max_length,formula_expression FROM data_types;
DROP TABLE data_types;
ALTER TABLE data_types_v13 RENAME TO data_types;
CREATE INDEX ix_types_structure ON data_types(structure_id);

-- schema 13: 구간별 선형(scale_linear)의 구간과 특성 곡선(tab_intp)의 점. 원시값은 정수 글자.
-- scale_linear: lower_raw..upper_raw 구간마다 phys = factor·raw + offset. tab_intp: 점(lower_raw → value_text), 점 사이는 선형 보간.
CREATE TABLE conversion_segments (
  data_type_id TEXT NOT NULL REFERENCES data_types(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  lower_raw TEXT NOT NULL,
  upper_raw TEXT,
  factor_text TEXT,
  offset_text TEXT,
  value_text TEXT,
  PRIMARY KEY(data_type_id,ordinal),
  CHECK((upper_raw IS NOT NULL AND factor_text IS NOT NULL AND offset_text IS NOT NULL AND value_text IS NULL)
     OR (upper_raw IS NULL AND factor_text IS NULL AND offset_text IS NULL AND value_text IS NOT NULL))
) STRICT;
""",
}


def version(db):
    return db.execute("PRAGMA user_version").fetchone()[0]


def migrate(db):
    """작업 DB를 CURRENT_VERSION까지 올린다. 단계마다 한 트랜잭션이며 실패하면 그 단계 전체를 되돌린다."""
    applied = []
    while version(db) < CURRENT_VERSION:
        start = version(db)
        script = SCRIPTS.get(start)
        if script is None: raise DomainError(f"스키마 {start}에서 올리는 마이그레이션이 없습니다.", "UNSUPPORTED_VERSION")
        now = datetime.now(timezone.utc).isoformat()
        revision = db.execute("SELECT revision FROM project_meta").fetchone()[0] + 1
        digest = hashlib.sha256(script.encode()).hexdigest()
        record = json.dumps({"from": start, "to": start + 1, "script_sha256": digest})
        # 테이블 재생성 중 DROP이 ON DELETE CASCADE를 일으키지 않도록 외래 키 검사를 끈다(트랜잭션 밖에서만 바꿀 수 있음).
        db.execute("PRAGMA foreign_keys=OFF")
        try:
            db.executescript("BEGIN IMMEDIATE;" + script + f"""
INSERT INTO schema_migrations(to_version,from_version,script_sha256,applied_at) VALUES({start + 1},{start},'{digest}','{now}');
UPDATE project_meta SET schema_version={start + 1}, revision={revision}, updated_at='{now}';
INSERT INTO change_log(revision,event_id,command_id,action,affected_ids_json,before_json,after_json,occurred_at)
  VALUES({revision},'{uuid.uuid4()}','{uuid.uuid4()}','migrate','[]','{{"schema_version":{start}}}','{record}','{now}');
PRAGMA user_version={start + 1};
COMMIT;""")
        except Exception:
            if db.in_transaction: db.execute("ROLLBACK")
            raise
        finally:
            db.execute("PRAGMA foreign_keys=ON")
        if db.execute("PRAGMA foreign_key_check").fetchone() or db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise DomainError("마이그레이션 후 무결성 검사에 실패했습니다.", "INVALID_PROJECT")
        applied.append(start + 1)
    return applied
