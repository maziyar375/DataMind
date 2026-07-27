export interface ProblemDetail {
  type?: string
  title?: string
  status?: number
  detail?: string
  code?: string
  correlation_id?: string
  errors?: { field: string; message: string }[]
}

export interface User {
  id: string
  email: string
  display_name: string
  role: 'ADMIN' | 'MEMBER'
  status?: string
  created_at?: string
}

export interface Connection {
  id: string
  name: string
  database_type: string
  host: string
  port: number
  database_name: string
  username: string
  ssl_mode: string | null
  schema_allowlist: string[]
  max_rows: number
  statement_timeout_ms: number
  disclosure_policy: 'NONE' | 'AGGREGATE' | 'SAMPLE' | 'FULL'
  semantic_layer_enabled: boolean
  status: string
  readonly_confirmed: boolean
  server_version: string | null
  last_tested_at: string | null
  last_synced_at: string | null
}

export interface LlmConfig {
  id: string
  name: string
  provider: string
  base_url: string | null
  model: string
  temperature: number
  max_tokens: number
  status: string
  has_api_key: boolean
  last_tested_at: string | null
}

export interface TestResult {
  ok: boolean
  latency_ms: number
  message?: string | null
  server_version?: string | null
  readonly_confirmed?: boolean
  detected_capabilities?: Record<string, unknown>
}

export interface SchemaColumn {
  name: string
  data_type: string
  nullable: boolean
  is_primary_key: boolean
  is_foreign_key: boolean
  references: string | null
}

export interface SchemaTable {
  schema: string
  name: string
  columns: SchemaColumn[]
  approx_row_count: number | null
}

export interface SchemaRelationship {
  from_table: string
  from_column: string
  to_table: string
  to_column: string
}

export interface SchemaSnapshot {
  dialect: string
  version: number
  synced_at: string | null
  tables: SchemaTable[]
  relationships: SchemaRelationship[]
}

// ── semantic layer ─────────────────────────────────────────────────────────
// Mirrors `app/semantic/models.py`. `valid` and `issue` are written by the
// backend validator on every read, never by this UI: the editor shows drift,
// it does not decide what counts as drift.
export interface Provenance {
  source: 'llm' | 'human' | 'derived'
  edited: boolean
  reviewed: boolean
}

export type ColumnRole = 'key' | 'time' | 'dimension' | 'measure' | 'attribute'
export type EntityRole = 'fact' | 'dimension' | 'bridge' | 'lookup' | 'unknown'
export type Additivity = 'additive' | 'semi_additive' | 'non_additive'

export interface SemanticColumn {
  name: string
  label: string
  description: string
  synonyms: string[]
  role: ColumnRole
  unit: string
  value_meanings: Record<string, string>
  provenance: Provenance
  valid: boolean
  issue: string
}

export interface SemanticMetric {
  name: string
  label: string
  description: string
  synonyms: string[]
  expression: string
  filters: string[]
  required_joins: string[]
  additive: Additivity
  unit: string
  format: string
  provenance: Provenance
  valid: boolean
  issue: string
}

export interface SemanticEntity {
  table: string
  label: string
  description: string
  synonyms: string[]
  grain: string
  role: EntityRole
  default_time_column: string
  columns: SemanticColumn[]
  metrics: SemanticMetric[]
  exclude: boolean
  provenance: Provenance
  valid: boolean
  issue: string
}

export interface SemanticJoin {
  left: string
  right: string
  on: string
  cardinality: 'one_to_one' | 'one_to_many' | 'many_to_one' | 'many_to_many'
  fan_out_warning: string
  provenance: Provenance
}

export interface GlossaryTerm {
  term: string
  meaning: string
  maps_to: string[]
  provenance: Provenance
}

export interface TimeSemantics {
  fiscal_year_start_month: number
  week_starts_on: 'monday' | 'sunday'
  timezone: string
  relative_windows: 'calendar' | 'rolling'
  notes: string
  provenance: Provenance
}

export interface SemanticDocument {
  document_version: number
  business_context: string
  time: TimeSemantics
  entities: SemanticEntity[]
  joins: SemanticJoin[]
  glossary: GlossaryTerm[]
}

export interface SemanticJob {
  id: string
  connection_id: string
  llm_config_id: string | null
  model_snapshot: Record<string, unknown>
  mode: 'MERGE' | 'REPLACE'
  only_tables: string[]
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED'
  phase: string
  progress_current: number
  progress_total: number
  stats: {
    tables_described?: number
    tables_failed?: string[]
    metrics_kept?: number
    metrics_dropped?: number
  }
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export interface SemanticTableFact {
  table: string
  column_count: number
  approx_row_count: number | null
  described: boolean
}

export interface SemanticLayer {
  document: SemanticDocument
  exists: boolean
  enabled: boolean
  entity_count: number
  metric_count: number
  reviewed_count: number
  issue_count: number
  schema_version: number
  schema_dialect: string
  stale: boolean
  tables: SemanticTableFact[]
  model_snapshot: Record<string, unknown>
  prompt_version: string
  generated_at: string | null
  edited_at: string | null
  job: SemanticJob | null
}

export interface ConversationSummary {
  id: string
  title: string
  status: string
  default_connection_id: string | null
  default_llm_config_id: string | null
  created_at: string
  updated_at: string
  message_count: number
  preview: string | null
}

export interface RunStep {
  seq: number
  name: string
  status: 'PENDING' | 'RUNNING' | 'DONE' | 'SKIPPED' | 'FAILED'
  detail: string | null
  duration_ms: number | null
}

export interface TableArtifactSpec {
  columns: { name: string; db_type: string; semantic_type: string }[]
  rows: unknown[][]
  row_count: number
  truncated: boolean
}

export type ArtifactSpec = TableArtifactSpec & Record<string, unknown>

export interface Artifact {
  id: string
  kind: 'TABLE' | 'CHART' | 'CLARIFICATION' | 'ERROR' | 'SQL_SUMMARY'
  spec: ArtifactSpec
}

export interface GeneratedQuery {
  attempt_no: number
  raw_sql: string
  rewritten_sql: string | null
  validation_status: string
  validation_report: {
    status?: string
    issues?: { rule_id: string; severity: string; message: string; hint?: string }[]
    referenced_tables?: string[]
    limit_applied?: number | null
  }
  referenced_tables: string[]
}

export interface RunDetail {
  id: string
  conversation_id: string
  status: string
  error_code: string | null
  error_message: string | null
  repair_count: number
  total_latency_ms: number | null
  db_latency_ms: number | null
  model_snapshot: Record<string, unknown>
  steps: RunStep[]
  artifacts: Artifact[]
  queries: GeneratedQuery[]
}

export interface MessageWithRun {
  id: string
  seq: number
  role: 'USER' | 'ASSISTANT' | 'SYSTEM'
  content: string | null
  created_at: string
  run: RunDetail | null
}

export interface RunEvent {
  seq: number
  type: string
  data: Record<string, any>
}
