/* Mirrors backend/app/models/connection.py. */

export type SslMode =
  | 'disable'
  | 'allow'
  | 'prefer'
  | 'require'
  | 'verify-ca'
  | 'verify-full';

export type WarningLevel = 'info' | 'warning' | 'error';

/** Either the fields, or a whole connection string in `dsn`. */
export interface ConnectionInput {
  name?: string;
  host?: string;
  port?: number;
  database?: string;
  user?: string;
  password?: string;
  sslmode?: SslMode;
  dsn?: string;
}

export interface Diagnostic {
  level: WarningLevel;
  code: string;
  message: string;
}

export interface ServerInfo {
  database: string;
  role_name: string;
  server_version: string;
  server_version_num: number;
  is_replica: boolean;
  is_superuser: boolean;
  bypasses_rls: boolean;
  read_only_enforced: boolean;
  has_pg_stat_statements: boolean;
  has_read_all_stats: boolean;
}

export interface SchemaSummary {
  name: string;
  tables: number;
  partitioned_tables: number;
  views: number;
  materialized_views: number;
  rls_tables: number;
  never_analyzed: number;
  unreadable: number;
}

export interface ProbeResult {
  server: ServerInfo;
  schemas: SchemaSummary[];
  warnings: Diagnostic[];
}

export interface TestConnectionResponse {
  ok: boolean;
  probe: ProbeResult | null;
  error: string | null;
  error_kind:
    | 'auth'
    | 'network'
    | 'timeout'
    | 'permission'
    | 'unsupported'
    | 'unknown'
    | null;
}

export interface StoredConnection {
  id: string;
  name: string;
  host: string;
  port: number;
  database: string;
  user: string;
  sslmode: SslMode;
  project_id: string;
  selected_schemas: string[];
  created_at: string;
  last_connected_at: string | null;
  last_probe: ProbeResult | null;
}

export interface Project {
  id: string;
  name: string;
  created_at: string;
  business_context: string;
  business_context_updated_at: string | null;
  last_scan_at: string | null;
  last_generated_at: string | null;
  selected_tables: string[];
  table_notes: Record<string, { purpose: string }>;
  connection: StoredConnection | null;
}

export interface Bootstrap {
  projects: Project[];
  active_project_id: string | null;
  openai_configured: boolean;
  openai_model: string;
}

export interface ScanCounts {
  tables: number;
  views: number;
  materialized_views: number;
  columns: number;
  declared_foreign_keys: number;
  enums: number;
  unreadable: number;
}

export interface ScanProfile {
  columns_profiled: number;
  columns_without_stats: number;
  columns_redacted: number;
}

export interface ColumnStats {
  null_fraction: number | null;
  distinct: number | null;
  unique: boolean;
  pii_suspected: boolean;
  values: { value: string; frequency: number | null }[] | null;
  range: { min: string; max: string } | null;
}

export interface ScanColumn {
  name: string;
  data_type: string;
  not_null: boolean;
  indexed: boolean;
  comment: string | null;
  enum_values?: string[];
  stats: ColumnStats | null;
}

export interface ScanTable {
  schema: string;
  name: string;
  qualified_name: string;
  kind: 'table' | 'partitioned_table' | 'view' | 'materialized_view';
  rows: number | null;
  row_basis: string;
  rls: boolean;
  readable: boolean;
  comment: string | null;
  columns: ScanColumn[];
  primary_key: string[];
  foreign_keys: {
    columns: string[];
    target: string | null;
    target_columns: string[];
  }[];
}

export interface Scan {
  id: string;
  created_at: string;
  schemas: string[];
  counts: ScanCounts;
  profile: ScanProfile;
  catalog: { tables: ScanTable[] };
}

export interface BundleFact<T = unknown> {
  value: T;
  provenance: 'declared' | 'sampled' | 'inferred' | 'user';
  confidence: number;
  basis?: string;
  note?: string;
  action?: string;
}

export interface Bundle {
  bundle_version: number;
  generated_at: string;
  scope: {
    selected: string[];
    excluded: { table: string; reason: string }[];
    narrowed_for_this_run: boolean;
    total_available: number;
  };
  tables: {
    name: string;
    kind: string;
    model_as: 'cube' | 'evidence';
    purpose?: BundleFact<string>;
    empty?: BundleFact<boolean>;
    default_time_dimension?: BundleFact<string>;
  }[];
  relationships: {
    child: string;
    child_columns: string[];
    parent: string;
    cardinality: BundleFact<string>;
  }[];
  dangling_foreign_keys: {
    child: string;
    columns: string[];
    target: string;
    reason: string;
  }[];
  vocabularies: { column: string; values: string[]; provenance: string; confidence: number }[];
  heuristics: {
    kind: string;
    column: string;
    tables: string[];
    confidence: number;
    action: string;
    basis: string;
  }[];
  counts: Record<string, number>;
}

export type JobState = 'running' | 'done' | 'failed';

/** One step of a generation job. `key` is stable, so a step that starts and
 *  later finishes updates its row rather than adding a second one. */
export interface JobStep {
  key: string;
  label: string;
  state: JobState;
  detail: string | null;
  at: number;
}

export interface JobSnapshot {
  job_id: string;
  state: JobState;
  events: JobStep[];
  result: GeneratedModel | null;
  error: string | null;
}

export interface GeneratedModel {
  id: string;
  created_at: string;
  scope: {
    selected: string[];
    excluded: string[];
    total_available: number;
    narrowed_for_this_run: boolean;
  };
  counts: Record<string, number>;
  questions: { subject: string; question: string; why_it_matters: string }[];
  files_written?: string[];
  /** Published path -> YAML body. Returned by both generate and getModel. */
  files?: Record<string, string>;
}

export interface CubeMember {
  name: string;
  title: string;
  type: string;
}

export interface AvailableCube {
  name: string;
  title: string;
  table: string;
  description: string;
  /** Dot path from the root cube, or null when nothing connects them. */
  join_path: string | null;
  reachable: boolean;
  dimensions: CubeMember[];
  measures: CubeMember[];
}

export interface CubeCatalogue {
  root_cube: string;
  suggested_root: string;
  cubes: AvailableCube[];
  /** Member names offered by more than one cube; they collide in a view. */
  ambiguous_members: string[];
}

export interface SavedView {
  id: string;
  name: string;
  title: string;
  description: string;
  root_cube: string;
  members: { cube: string; includes: string[] }[];
  updated_at: string;
}

export interface ViewIndexStatus {
  configured: boolean;
  reachable: boolean;
  /** Points currently in Qdrant for this project. */
  count: number;
  /** Points the views would produce right now. */
  expected?: number;
  /** True when the index no longer matches the views. */
  stale?: boolean;
  added?: number;
  removed?: string[];
  changed?: string[];
  indexed_at?: string | null;
  collection?: string;
}

export interface ViewIndexReport {
  indexed: number;
  collection: string;
  views: number;
  members: number;
  /** "view.member" for anything embedded without a description. */
  undescribed: string[];
}

export interface ViewSearchHit {
  name: string;
  cube: string;
  type: 'measure' | 'dimension' | 'segment';
  title: string;
  description: string;
  score: number;
}

export interface ViewSearchResult {
  question: string;
  total: number;
  views: { view: string; score: number; members: ViewSearchHit[] }[];
}

export interface ProjectPrompt {
  key: string;
  label: string;
  purpose: string;
  /** What the project uses now: the override if there is one, else the default. */
  body: string;
  default: string;
  customised: boolean;
  updated_at: string | null;
  allowed: string[];
  required: string[];
  /** False for the refusal text, which is returned verbatim. */
  is_llm: boolean;
  tags: string[];
}

export interface AgentStep {
  step: string;
  [key: string]: unknown;
}

export interface AgentAnswer {
  kind: 'db_query' | 'normal' | 'not_permitted';
  answer: string;
  ok?: boolean;
  view?: string;
  sql?: string;
  rows?: Record<string, unknown>[];
  error?: string;
  steps: AgentStep[];
}
