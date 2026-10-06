PRAGMA foreign_keys = ON;
PRAGMA application_id = 0x44494147;
PRAGMA user_version = 2;

CREATE TABLE project_meta (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  project_uuid TEXT NOT NULL UNIQUE CHECK(length(project_uuid)=36),
  schema_version INTEGER NOT NULL CHECK(schema_version>=1),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
  default_locale TEXT NOT NULL,
  profile_id TEXT NOT NULL,
  profile_version TEXT NOT NULL,
  saved_app_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE schema_migrations (
  to_version INTEGER PRIMARY KEY CHECK(to_version>=1),
  from_version INTEGER NOT NULL CHECK(from_version>=0),
  script_sha256 TEXT NOT NULL CHECK(length(script_sha256)=64),
  applied_at TEXT NOT NULL,
  CHECK(to_version=from_version+1)
) STRICT;

CREATE TABLE source_files (
  id TEXT PRIMARY KEY CHECK(length(id)=36),
  kind TEXT NOT NULL CHECK(kind IN ('cdd','csv','other')),
  basename TEXT NOT NULL,
  format_version TEXT,
  sha256 TEXT NOT NULL UNIQUE CHECK(length(sha256)=64),
  content BLOB NOT NULL,
  imported_at TEXT NOT NULL
) STRICT;

CREATE TABLE objects (
  id TEXT PRIMARY KEY CHECK(length(id)=36),
  kind TEXT NOT NULL CHECK(kind IN (
    'ecu','variant','did','dtc','diagnostic_class','diagnostic_instance',
    'service','data_type','data_object','structure','state_group','state')),
  qualifier TEXT NOT NULL,
  qualifier_key TEXT NOT NULL,
  supported INTEGER NOT NULL DEFAULT 1 CHECK(supported IN (0,1)),
  read_only INTEGER NOT NULL DEFAULT 0 CHECK(read_only IN (0,1))
) STRICT;
CREATE INDEX ix_objects_qualifier ON objects(kind,qualifier_key,id);

CREATE TABLE localized_texts (
  object_id TEXT NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
  field TEXT NOT NULL CHECK(field IN ('name','description')),
  locale TEXT NOT NULL,
  value TEXT NOT NULL,
  search_key TEXT NOT NULL,
  PRIMARY KEY(object_id,field,locale)
) STRICT;
CREATE INDEX ix_text_search ON localized_texts(field,locale,search_key,object_id);

CREATE TABLE source_mappings (
  id INTEGER PRIMARY KEY,
  object_id TEXT NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
  source_file_id TEXT NOT NULL REFERENCES source_files(id) ON DELETE RESTRICT,
  node_path TEXT NOT NULL,
  external_id TEXT,
  external_oid TEXT,
  UNIQUE(object_id,source_file_id,node_path)
) STRICT;
CREATE INDEX ix_source_external ON source_mappings(source_file_id,external_id);

CREATE TABLE object_attributes (
  object_id TEXT NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  value_type TEXT NOT NULL CHECK(value_type IN ('text','integer','decimal','boolean','hex')),
  value_text TEXT NOT NULL,
  PRIMARY KEY(object_id,key)
) STRICT;

CREATE TABLE ecu (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  project_singleton INTEGER NOT NULL UNIQUE REFERENCES project_meta(singleton) ON DELETE RESTRICT,
  manufacturer TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE variants (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  ecu_id TEXT NOT NULL REFERENCES ecu(id) ON DELETE RESTRICT,
  is_base INTEGER NOT NULL DEFAULT 0 CHECK(is_base IN (0,1)),
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  locked INTEGER NOT NULL DEFAULT 0 CHECK(locked IN (0,1)),
  created_from_variant_id TEXT REFERENCES variants(id) ON DELETE SET NULL,
  UNIQUE(ecu_id,ordinal)
) STRICT;
CREATE UNIQUE INDEX ux_base_variant ON variants(ecu_id) WHERE is_base=1;

CREATE TABLE identifier_scopes (
  id TEXT PRIMARY KEY,
  protocol TEXT NOT NULL CHECK(protocol='UDS'),
  kind TEXT NOT NULL CHECK(kind IN ('did','dtc','routine')),
  bit_length INTEGER NOT NULL,
  CHECK((kind IN ('did','routine') AND bit_length=16) OR (kind='dtc' AND bit_length=24))
) STRICT;

CREATE TABLE structures (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  kind TEXT NOT NULL CHECK(kind IN ('payload','packet','bitfield'))
) STRICT;

CREATE TABLE data_types (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  encoding TEXT NOT NULL CHECK(encoding IN ('unsigned','signed','ascii','bytes','packet')),
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
  CHECK((encoding='packet' AND structure_id IS NOT NULL AND bit_length IS NULL
         AND array_length IS NULL AND conversion='identity')
     OR (encoding IN ('unsigned','signed') AND structure_id IS NULL
         AND bit_length IS NOT NULL AND array_length IS NOT NULL
         AND bit_length BETWEEN 1 AND 64 AND array_length=1)
     OR (encoding IN ('ascii','bytes') AND structure_id IS NULL
         AND bit_length IS NOT NULL AND array_length IS NOT NULL
         AND bit_length=8 AND array_length BETWEEN 1 AND 1048576 AND conversion='identity')),
  CHECK((conversion='linear' AND factor_text IS NOT NULL AND offset_text IS NOT NULL)
     OR (conversion!='linear' AND factor_text IS NULL AND offset_text IS NULL))
) STRICT;
CREATE INDEX ix_types_structure ON data_types(structure_id);

CREATE TABLE conversion_entries (
  data_type_id TEXT NOT NULL REFERENCES data_types(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  locale TEXT NOT NULL,
  lower_raw TEXT NOT NULL,
  upper_raw TEXT NOT NULL,
  label TEXT NOT NULL,
  PRIMARY KEY(data_type_id,ordinal,locale)
) STRICT;

-- schema 2: 원시값 예외(CDD EXCL). 한 예외가 언어별 라벨 행을 가진다. 라벨이 없으면 locale='' 한 행.
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

CREATE TABLE data_objects (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  data_type_id TEXT NOT NULL REFERENCES data_types(id) ON DELETE RESTRICT,
  default_kind TEXT CHECK(default_kind IN ('integer','decimal','text','hex')),
  default_text TEXT,
  constant_kind TEXT CHECK(constant_kind IN ('integer','decimal','text','hex')),
  constant_text TEXT,
  CHECK((default_kind IS NULL)=(default_text IS NULL)),
  CHECK((constant_kind IS NULL)=(constant_text IS NULL))
) STRICT;
CREATE INDEX ix_data_objects_type ON data_objects(data_type_id);

CREATE TABLE dids (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  scope_id TEXT NOT NULL REFERENCES identifier_scopes(id) ON DELETE RESTRICT,
  identifier INTEGER NOT NULL CHECK(identifier BETWEEN 0 AND 65535),
  structure_id TEXT NOT NULL REFERENCES structures(id) ON DELETE RESTRICT,
  UNIQUE(id,scope_id,identifier)
) STRICT;
CREATE INDEX ix_dids_structure ON dids(structure_id);

CREATE TABLE dtcs (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  scope_id TEXT NOT NULL REFERENCES identifier_scopes(id) ON DELETE RESTRICT,
  code INTEGER NOT NULL CHECK(code BETWEEN 0 AND 16777215),
  UNIQUE(id,scope_id,code)
) STRICT;

CREATE TABLE variant_dids (
  variant_id TEXT NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
  did_id TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  identifier INTEGER NOT NULL,
  PRIMARY KEY(variant_id,did_id),
  UNIQUE(variant_id,scope_id,identifier),
  FOREIGN KEY(did_id,scope_id,identifier) REFERENCES dids(id,scope_id,identifier)
    ON UPDATE CASCADE ON DELETE RESTRICT
) STRICT;
CREATE INDEX ix_variant_dids_target ON variant_dids(did_id);

CREATE TABLE variant_dtcs (
  variant_id TEXT NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
  dtc_id TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  code INTEGER NOT NULL,
  PRIMARY KEY(variant_id,dtc_id),
  UNIQUE(variant_id,scope_id,code),
  FOREIGN KEY(dtc_id,scope_id,code) REFERENCES dtcs(id,scope_id,code)
    ON UPDATE CASCADE ON DELETE RESTRICT
) STRICT;
CREATE INDEX ix_variant_dtcs_target ON variant_dtcs(dtc_id);

CREATE TABLE diagnostic_classes (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  role TEXT NOT NULL CHECK(role IN ('session','reset','read','write','routine','tester_present','fault_memory'))
) STRICT;

CREATE TABLE diagnostic_instances (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  class_id TEXT NOT NULL REFERENCES diagnostic_classes(id) ON DELETE RESTRICT
) STRICT;
CREATE INDEX ix_instances_class ON diagnostic_instances(class_id);

CREATE TABLE instance_dids (
  instance_id TEXT NOT NULL REFERENCES diagnostic_instances(id) ON DELETE CASCADE,
  did_id TEXT NOT NULL REFERENCES dids(id) ON DELETE RESTRICT,
  role TEXT NOT NULL CHECK(role IN ('read','write','data')),
  PRIMARY KEY(instance_id,did_id,role)
) STRICT;
CREATE INDEX ix_instance_dids_target ON instance_dids(did_id);

CREATE TABLE variant_instances (
  variant_id TEXT NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
  instance_id TEXT NOT NULL REFERENCES diagnostic_instances(id) ON DELETE RESTRICT,
  PRIMARY KEY(variant_id,instance_id)
) STRICT;
CREATE INDEX ix_variant_instances_target ON variant_instances(instance_id);

CREATE TABLE services (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  instance_id TEXT NOT NULL REFERENCES diagnostic_instances(id) ON DELETE RESTRICT,
  sid INTEGER NOT NULL CHECK(sid IN (16,17,34,46,49,62)),
  subfunction INTEGER CHECK(subfunction BETWEEN 0 AND 127),
  routine_identifier INTEGER CHECK(routine_identifier BETWEEN 0 AND 65535),
  physical INTEGER NOT NULL DEFAULT 1 CHECK(physical IN (0,1)),
  functional INTEGER NOT NULL DEFAULT 0 CHECK(functional IN (0,1)),
  suppress_positive INTEGER NOT NULL DEFAULT 0 CHECK(suppress_positive IN (0,1)),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  CHECK(physical=1 OR functional=1),
  CHECK((sid=49 AND routine_identifier IS NOT NULL) OR (sid!=49 AND routine_identifier IS NULL)),
  CHECK((sid IN (16,17,49,62) AND subfunction IS NOT NULL)
     OR (sid IN (34,46) AND subfunction IS NULL AND suppress_positive=0))
) STRICT;
CREATE INDEX ix_services_instance ON services(instance_id);

CREATE TABLE service_nrcs (
  service_id TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  code INTEGER NOT NULL CHECK(code BETWEEN 1 AND 255),
  PRIMARY KEY(service_id,code)
) STRICT;

CREATE TABLE telegrams (
  id TEXT PRIMARY KEY CHECK(length(id)=36),
  service_id TEXT NOT NULL REFERENCES services(id) ON DELETE RESTRICT,
  kind TEXT NOT NULL CHECK(kind IN ('request','positive','negative')),
  structure_id TEXT NOT NULL REFERENCES structures(id) ON DELETE RESTRICT,
  UNIQUE(service_id,kind)
) STRICT;
CREATE INDEX ix_telegrams_structure ON telegrams(structure_id);

CREATE TABLE structure_members (
  structure_id TEXT NOT NULL REFERENCES structures(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  kind TEXT NOT NULL CHECK(kind IN ('data','reserved','structure','did_data','did_with_identifier','literal')),
  data_object_id TEXT UNIQUE REFERENCES data_objects(id) ON DELETE RESTRICT,
  nested_structure_id TEXT REFERENCES structures(id) ON DELETE RESTRICT,
  did_id TEXT REFERENCES dids(id) ON DELETE RESTRICT,
  reserved_bits INTEGER CHECK(reserved_bits>0),
  literal_bytes BLOB,
  PRIMARY KEY(structure_id,ordinal),
  CHECK((kind='data' AND data_object_id IS NOT NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL)
     OR (kind='structure' AND data_object_id IS NULL AND nested_structure_id IS NOT NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NULL)
     OR (kind IN ('did_data','did_with_identifier') AND data_object_id IS NULL
         AND nested_structure_id IS NULL AND did_id IS NOT NULL AND reserved_bits IS NULL AND literal_bytes IS NULL)
     OR (kind='reserved' AND data_object_id IS NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NOT NULL AND literal_bytes IS NULL)
     OR (kind='literal' AND data_object_id IS NULL AND nested_structure_id IS NULL
         AND did_id IS NULL AND reserved_bits IS NULL AND literal_bytes IS NOT NULL AND length(literal_bytes)>0))
) STRICT;
CREATE INDEX ix_members_did ON structure_members(did_id);
CREATE INDEX ix_members_nested ON structure_members(nested_structure_id);

CREATE TABLE state_groups (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  kind TEXT NOT NULL UNIQUE CHECK(kind IN ('session','security')),
  initial_state_id TEXT NOT NULL,
  FOREIGN KEY(initial_state_id,id) REFERENCES states(id,group_id)
    DEFERRABLE INITIALLY DEFERRED
) STRICT;

CREATE TABLE states (
  id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE RESTRICT,
  group_id TEXT NOT NULL REFERENCES state_groups(id) ON DELETE RESTRICT,
  code INTEGER NOT NULL CHECK(code BETWEEN 0 AND 255),
  UNIQUE(group_id,code),
  UNIQUE(id,group_id)
) STRICT;

CREATE TABLE service_conditions (
  service_id TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  group_id TEXT NOT NULL REFERENCES state_groups(id) ON DELETE RESTRICT,
  state_id TEXT NOT NULL,
  allowed INTEGER NOT NULL CHECK(allowed IN (0,1)),
  PRIMARY KEY(service_id,group_id,state_id),
  FOREIGN KEY(state_id,group_id) REFERENCES states(id,group_id) ON DELETE RESTRICT
) STRICT;
CREATE INDEX ix_conditions_state ON service_conditions(state_id);

CREATE TABLE service_transitions (
  service_id TEXT NOT NULL,
  group_id TEXT NOT NULL,
  from_state_id TEXT NOT NULL,
  to_state_id TEXT NOT NULL,
  PRIMARY KEY(service_id,group_id,from_state_id),
  FOREIGN KEY(service_id,group_id,from_state_id)
    REFERENCES service_conditions(service_id,group_id,state_id) ON DELETE CASCADE,
  FOREIGN KEY(to_state_id,group_id) REFERENCES states(id,group_id) ON DELETE RESTRICT
) STRICT;
CREATE INDEX ix_transitions_target ON service_transitions(to_state_id);

CREATE TABLE unsupported_nodes (
  id INTEGER PRIMARY KEY,
  source_file_id TEXT NOT NULL REFERENCES source_files(id) ON DELETE RESTRICT,
  owner_object_id TEXT REFERENCES objects(id) ON DELETE RESTRICT,
  node_path TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  semantic INTEGER NOT NULL CHECK(semantic IN (0,1)),
  raw_xml TEXT NOT NULL,
  UNIQUE(source_file_id,node_path,reason_code)
) STRICT;
CREATE INDEX ix_unsupported_owner ON unsupported_nodes(owner_object_id);

CREATE TABLE import_issues (
  id INTEGER PRIMARY KEY,
  source_file_id TEXT NOT NULL REFERENCES source_files(id) ON DELETE RESTRICT,
  object_id TEXT REFERENCES objects(id) ON DELETE SET NULL,
  node_path TEXT,
  code TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN ('info','warning','error')),
  blocks_export INTEGER NOT NULL CHECK(blocks_export IN (0,1)),
  message TEXT NOT NULL
) STRICT;
CREATE INDEX ix_import_issues_source ON import_issues(source_file_id,severity);

CREATE TABLE change_log (
  revision INTEGER PRIMARY KEY CHECK(revision>=1),
  event_id TEXT NOT NULL UNIQUE CHECK(length(event_id)=36),
  command_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('execute','undo','redo','migrate')),
  affected_ids_json TEXT NOT NULL CHECK(json_valid(affected_ids_json)),
  before_json TEXT NOT NULL CHECK(json_valid(before_json)),
  after_json TEXT NOT NULL CHECK(json_valid(after_json)),
  occurred_at TEXT NOT NULL
) STRICT;

CREATE TABLE undo_stack (
  position INTEGER PRIMARY KEY CHECK(position>=1),
  command_id TEXT NOT NULL UNIQUE,
  forward_json TEXT NOT NULL CHECK(json_valid(forward_json)),
  inverse_json TEXT NOT NULL CHECK(json_valid(inverse_json)),
  before_token TEXT NOT NULL,
  after_token TEXT NOT NULL
) STRICT;

CREATE TABLE history_state (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  cursor_position INTEGER NOT NULL CHECK(cursor_position>=0),
  current_token TEXT NOT NULL,
  saved_token TEXT
) STRICT;
