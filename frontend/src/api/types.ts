export interface ProblemDetail {
  type?: string
  title?: string
  status?: number
  detail?: string
  code?: string
  correlation_id?: string
  errors?: { field: string; message: string }[]
  /** A refused dashboard import: every tile it would not store, by title. */
  tiles?: string[]
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
  clarify_enabled: boolean
  include_db_comments: boolean
  /** Whether the scheduled conflict checker may run this connection's
   *  templates against each other. The only thing in the product that queries
   *  a customer's database without being asked. */
  conflict_checks_enabled: boolean
  /** Whether taught questions reach the generate prompt as few-shot examples.
   *  **Off by default**, and off is byte-identical to `PROMPT_VERSION` v8 —
   *  the prompt every recorded accuracy measurement was taken on. */
  knowledge_examples_enabled: boolean
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
  /** Extra request parameters, in the **selected provider's own** vocabulary —
   *  `stop_sequences` and `thinking` for Anthropic, `stop` and `seed` for
   *  anything OpenAI-compatible. Nothing here is typed in TypeScript on
   *  purpose: the legal set is the provider's, it is served by
   *  `llmConfigs.parameters()` and validated on save against the same catalog,
   *  and a second copy of that list in the SPA is a second thing to keep true. */
  params: Record<string, unknown>
  /** The model this endpoint is asked for vectors. Empty means this provider
   *  is for completions only, which is every configuration until somebody
   *  says otherwise. */
  embedding_model: string
  embedding_params: Record<string, unknown>
  status: string
  has_api_key: boolean
  last_tested_at: string | null
}

/** One documented request parameter, in enough detail to render a field.
 *
 *  Mirrors `ParamSpec.as_dict()` in `app/domain/value_objects/llm_params.py`.
 *  The form is **generated** from these, so adding a parameter to the catalog
 *  puts it on the screen with no change here. */
export interface ParamSpec {
  name: string
  kind: 'number' | 'integer' | 'boolean' | 'string' | 'string_list' | 'object'
  /** One line, written for the person configuring the model. */
  summary: string
  example: string
  minimum?: number
  maximum?: number
  /** The values the provider documents. Present makes the field a picker. */
  choices?: string[]
  /** For an object parameter, the keys the provider documents. Empty means
   *  the endpoint defines the body itself (`extra_body`, `logit_bias`). */
  object_keys?: string[]
}

export interface ParameterCatalog {
  provider: string
  completion: ParamSpec[]
  embedding: ParamSpec[]
  /** Whether the provider has an embedding endpoint at all. Stated rather than
   *  inferred from an empty list, so the form can say *why* — Anthropic does
   *  not offer one, which is a fact and not a gap in the catalog. */
  embedding_supported: boolean
}

export interface TestResult {
  ok: boolean
  latency_ms: number
  message?: string | null
  server_version?: string | null
  readonly_confirmed?: boolean
  detected_capabilities?: Record<string, unknown>
  /** Which configured parameters this model actually accepts, and which it
   *  does not. An unsupported parameter is dropped silently at request time;
   *  a test that stayed silent too would let a configuration claim a behaviour
   *  it never has. */
  applied_params?: Record<string, unknown>
  dropped_params?: string[]
  /** The embedding half, when one is configured. Absent otherwise. */
  embedding?: {
    ok: boolean
    model: string
    dimension: number
    message: string
  } | null
}

export interface SchemaColumn {
  name: string
  data_type: string
  nullable: boolean
  is_primary_key: boolean
  is_foreign_key: boolean
  references: string | null
  // What the database's own catalog says this column is — `COMMENT ON`, MySQL's
  // COLUMN_COMMENT, an MS_Description property, Oracle's ALL_COL_COMMENTS.
  // Optional because a snapshot taken before comments were captured has no such
  // key at all, which is not the same as a column nobody documented.
  comment?: string | null
}

export interface SchemaTable {
  schema: string
  name: string
  columns: SchemaColumn[]
  approx_row_count: number | null
  comment?: string | null
}

export interface SchemaRelationship {
  from_table: string
  from_column: string
  to_table: string
  to_column: string
}

/** Catalog description above the table level, and what the last sync found. */
export interface SchemaCatalogMeta {
  // Only PostgreSQL and SQL Server carry either of these; MySQL has none
  // outside MariaDB and Oracle has neither.
  database_comment?: string | null
  schema_comments?: Record<string, string>
  counts?: { tables: number; columns: number }
}

export interface SchemaSnapshot {
  dialect: string
  version: number
  synced_at: string | null
  tables: SchemaTable[]
  relationships: SchemaRelationship[]
  catalog_meta?: SchemaCatalogMeta
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
  /** Rows that should not count unless the question asks for them. */
  default_exclusions: string
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
    /** The glossary pass ran and its answer was unusable — as opposed to the
     *  model deciding nothing needed defining, which is also an empty list. */
    glossary_failed?: boolean
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

/** One declared slot in a template. */
export interface TemplateParam {
  name: string
  type: 'string' | 'number' | 'date' | 'datetime' | 'boolean'
  comment: string
}

/**
 * One literal the AST walk found, ticked or refused.
 *
 * `eligible: false` is not an omission — the editor renders the refusal with
 * its reason beside it, because showing the rejected candidate teaches the
 * rule better than hiding it, and the curator occasionally knows better.
 */
export interface ParamProposal {
  name: string
  type: TemplateParam['type']
  /** The literal as the statement renders it — `'EMEA'`, `10000`. */
  literal: string
  /** Which occurrence of that exact text this is, so the highlight lands on
   *  the right one when a statement filters on `'EMEA'` twice. */
  occurrence: number
  comment: string
  suggested: boolean
  eligible: boolean
  reason: string
}

export type TemplateRole = 'RETRIEVABLE' | 'BENCHMARK_ONLY' | 'HELD_OUT'
export type TemplateStatus = 'ACTIVE' | 'STALE' | 'CONFLICTED' | 'ARCHIVED'

export interface KnowledgeTemplate {
  id: string
  connection_id: string
  question: string
  question_normalized: string
  sql: string
  params: TemplateParam[]
  note: string
  source: string
  literal_provenance: 'HUMAN_AUTHORED' | 'MODEL_DERIVED'
  role: TemplateRole
  status: TemplateStatus
  status_reason: string
  schema_version: number
  referenced_tables: string[]
  /** The other templates this one disagrees with. Populated only by the
   *  conflict checker — never by a form. */
  conflicts_with: string[]
  /** **The rows that prove a conflict.** Fabric reasons over SQL text and
   *  reports a confidence of 1–5; this ran both statements and compared the
   *  answers, so the pane shows the disagreement rather than a warning. Empty
   *  on every healthy template; every cell is already a string. */
  conflict_evidence: ConflictEvidence
  hit_count: number
  last_hit_at: string | null
  verified_at: string | null
  last_validated_at: string | null
  created_at: string
  updated_at: string
}

/** What `knowledge_templates.conflict_evidence` holds. Written from this
 *  template's own point of view, so `left_*` is always *this* one's answer. */
export interface ConflictEvidence {
  summary?: string
  left_columns?: string[]
  right_columns?: string[]
  left_rows?: string[][]
  right_rows?: string[][]
}

/** The store's health — §4.7's three rows of the curator's queue.
 *  Ids rather than counts, because the queue links to the templates. */
export interface KnowledgeHealth {
  total: number
  stale: string[]
  conflicted: string[]
  /** No matches, and old enough for that to mean something. Surfaced, never
   *  enforced: a template written for a question asked once a year is not
   *  waste, so this list carries no action button. */
  unused: string[]
  /** False means *was not allowed to look*, which must never be printed as
   *  *found nothing*. */
  conflict_checks_enabled: boolean
  unused_after_days: number
}

/** What one on-demand sweep did, for the button that asked for it. */
export interface MaintenanceResult {
  checked: number
  staled: string[]
  revived: string[]
  conflicted: string[]
  cleared: string[]
  pairs_considered: number
  pairs_executed: number
  /** Pairs the checker declined to run, each naming the slot that had no probe
   *  value — how a curator learns that a parameter needs a value list. */
  skipped: string[]
  conflicts_checked: boolean
  /** The embedding index, when the connection has one pinned. Zeroes on a
   *  lexical connection, which is the default and most of them. */
  indexed: number
  index_current: number
  index_truncated: boolean
  index_error: string
}

/** Whether this store is searched by meaning, and how much of it is indexed.
 *
 *  `enabled` and `indexed` are separate on purpose: a connection can have a
 *  model pinned and no vectors yet, and that is a normal state rather than a
 *  failure — the UI says *indexing* for it, because *on* would promise a
 *  behaviour the next question will not show. */
export interface EmbeddingStatus {
  enabled: boolean
  model: string
  dimension: number
  templates: number
  indexed: number
  /** The provider's own sentence when the probe or the last pass refused.
   *  Empty on success. */
  message: string
  /** The provider that made this index — or, while the store is still matched
   *  on words, the one that *would* make it. One embedder serves the whole
   *  deployment and is set up in LLM providers, so this is reported and never
   *  posted. Null is the state where the control has nothing to switch to and
   *  says so instead of offering a button that fails. */
  embedder: EmbeddingProvider | null
}

export interface EmbeddingProvider {
  id: string
  name: string
  provider: string
  model: string
}

export interface KnowledgeTemplateList {
  templates: KnowledgeTemplate[]
  schema_version: number
  schema_synced: boolean
  /** Whether this reader may write. The UI **hides** rather than disables. */
  can_curate: boolean
  /** Templates whose SQL no longer resolves against the current snapshot but
   *  the sweep has not yet withdrawn — read-time drift, reported the moment a
   *  re-sync creates it. A row the sweep already withdrew carries
   *  `status: 'STALE'` and is counted in `health.stale` instead. */
  stale_ids: string[]
  health: KnowledgeHealth
}

/** What `POST .../templates/check` answers, in one round trip. */
export interface TemplateCheckResult {
  valid: boolean
  issue: string
  issues: { rule_id: string; message: string; hint: string | null }[]
  referenced_tables: string[]
  proposals: ParamProposal[]
  /** The SQL as it would be stored, with the accepted literals replaced. */
  sql: string
  params: TemplateParam[]
  /** The `{names}` the question declares. */
  question_slots: string[]
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

/** The CLARIFICATION artifact: what the run asked, and the readings offered. */
export interface ClarificationSpec {
  question: string
  options: string[]
}

export type ArtifactSpec = TableArtifactSpec & Record<string, unknown>

export interface Artifact {
  id: string
  kind: 'TABLE' | 'CHART' | 'KPI' | 'CLARIFICATION' | 'ERROR' | 'SQL_SUMMARY'
  spec: ArtifactSpec
}

/**
 * One number drawn big — what a single-row result becomes instead of nothing.
 *
 * `value` and `delta.text` arrive already written out. The backend formats
 * them so that a tile and a chat turn showing the same number cannot disagree
 * about it, and so the figure is written the same way as the axis beside it.
 */
export interface KpiSpec {
  value: string
  raw: number | null
  label: string
  caption: string | null
  delta: { text: string; direction: 'up' | 'down' | 'flat'; caption: string } | null
  sparkline: number[]
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

/**
 * What the answer's badge says, and the evidence behind it.
 *
 * `question` and `bound_params` are not decoration. The matched question is
 * the reader's only defence against a confident wrong match, and the bindings
 * answer the next thing a suspicious reader wants to know — *did it think July
 * or June?*
 */
export interface RunKnowledge {
  tier: 'VERIFIED' | 'GROUNDED' | 'GENERATED'
  template_id: string | null
  /** The matched template's question, shown verbatim. */
  question: string
  /** `{ region: 'EMEA', from_date: '2026-01-01' }`. */
  bound_params: Record<string, string>
  score: number
  matcher: string
  /** True once somebody asked for a fresh answer instead of this one. */
  overridden: boolean
  /** This reader's own verdict on this answer, and what became of it. */
  feedback: AnswerFeedback | null
}

/** One verdict on an answer, and what became of it. */
export interface AnswerFeedback {
  id: string
  run_id: string
  verdict: 'CORRECT' | 'WRONG' | 'NEEDS_REVIEW'
  comment: string
  state: 'OPEN' | 'RESOLVED' | 'DISMISSED'
  /** Why a curator dismissed it — shown back to the person who flagged it,
   *  because a dismissal with no reason is indistinguishable from silence. */
  resolution_note: string
  /** **The loop closing.** The flag that became knowledge says so. */
  became_template: string | null
  resolved_at: string | null
  created_at: string
  /** Whose queue this landed in — the connection's owner, named by the server
   *  so the acknowledgement stays true when ownership moves. Empty falls back
   *  to the generic sentence rather than to a blank. */
  routed_to: string
}

/** One flag in the curator's queue, with the evidence beside it. */
export interface Review {
  id: string
  run_id: string
  verdict: 'WRONG' | 'NEEDS_REVIEW'
  comment: string
  state: string
  created_at: string
  question: string
  sql: string
  /** A name, never an address. */
  flagged_by: string
}

// ── benchmarks and the score (Phase 6) ────────────────────────────────────
/** One run of a set, with **both** numbers — never one.
 *
 *  Accuracy on questions answered *from* a template and accuracy on questions
 *  answered *without* one are different numbers, and only the second moves for
 *  a reason. `held_out_*` is the one the strip puts first and larger. */
export interface BenchmarkRun {
  id: string
  set_id: string
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'
  prompt_version: string
  model_snapshot: Record<string, unknown>
  total: number
  /** Members that produced a comparable answer. Below `total` when a member
   *  could not be probed — an accuracy over a shrinking denominator is the
   *  classic silent lie, so the difference is shown rather than hidden. */
  scored: number
  matched: number
  held_out_total: number
  held_out_matched: number
  taught_total: number
  taught_matched: number
  error_message: string
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export interface BenchmarkSet {
  id: string
  connection_id: string
  name: string
  description: string
  template_ids: string[]
  held_out_fraction: number
  created_at: string
  updated_at: string
  /** Newest first, capped — the sparkline's points. */
  runs: BenchmarkRun[]
  held_out_count: number
}

/** One question's verdict, labelled by the comparator and by no model. */
export interface BenchmarkResult {
  id: string
  template_id: string | null
  question: string
  gold_sql: string
  candidate_sql: string
  role: 'HELD_OUT' | 'BENCHMARK_ONLY'
  outcome:
    | 'MATCH' | 'MISMATCH' | 'EXEC_FAILED' | 'VALIDATION_FAILED'
    | 'NO_SQL' | 'NOT_PROBED' | 'ERROR'
  from_template: boolean
  gold_row_count: number | null
  candidate_row_count: number | null
  duration_ms: number
  failure_reason: string
}

export interface BenchmarkCandidate {
  id: string
  question: string
  hit_count: number
  referenced_tables: string[]
}

/** What the score strip needs, in one round trip. An empty `sets` means the
 *  strip is **absent** rather than showing zeros — §4.8: never an empty chart. */
export interface BenchmarkOverview {
  sets: BenchmarkSet[]
  can_curate: boolean
  candidates: number
  min_set_size: number
}

/** One row in the backlog: what to teach, and why it is worth teaching. */
export interface Suggestion {
  kind: 'FLAGGED' | 'BACKFILL' | 'TRAFFIC' | 'FAILED' | 'UNKNOWN_WORDS'
  question: string
  count: number
  reason: string
  sql: string
  source: string
  /** Whether the literals in `sql` were a model's choice. */
  model_derived: boolean
  origin_id: string
  words: string[]
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
  knowledge: RunKnowledge
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

// ── dashboards ────────────────────────────────────────────────────────────
// One shape for a tile's data whether it was just computed or served from the
// cache — `computed_at` is how the reader tells which, and how old it is.
export interface TileResult {
  status: 'OK' | 'ERROR'
  columns: { name: string; db_type: string; semantic_type: string }[]
  rows: unknown[][]
  row_count: number
  truncated: boolean
  duration_ms: number
  computed_at: string
  vega_spec: Record<string, unknown> | null
  /** model | model_adjusted | heuristic | none — who chose the chart. */
  chart_source: string
  /** Set when the pick was overruled: "a pie does not fit; showing a bar". */
  chart_note: string | null
  /** A METRIC tile's big number, planned by the backend. Null for every other type. */
  kpi: KpiSpec | null
  error: { code: string; message: string } | null
}

export type TileType = 'CHART' | 'TABLE' | 'METRIC' | 'TEXT'
export type SqlOrigin = 'GENERATED' | 'GENERATED_EDITED' | 'HANDWRITTEN'

/**
 * How a TABLE tile is drawn. Mirrors `TableConfig` in `api/schemas.py`.
 *
 * Presentation only, applied in the browser to rows it already has — which is
 * why it is not part of the tile's cache fingerprint, and why hiding a column
 * hides it rather than withholding it.
 */
export interface TableColumnConfig {
  name: string
  hidden?: boolean
  /** null keeps the column's own name; '' is a deliberate blank heading. */
  label?: string | null
  align?: 'auto' | 'left' | 'right' | 'center'
  format?: 'auto' | 'integer' | 'decimal' | 'percent' | 'text'
}

export interface TableConfig {
  /** Position in this list is the display order. */
  columns: TableColumnConfig[]
  sort_column?: string | null
  sort_direction?: 'asc' | 'desc'
}

export interface DashboardTile {
  id: string
  dashboard_id: string
  connection_id: string | null
  connection_name: string | null
  llm_config_id: string | null
  llm_config_name: string | null
  title: string
  tile_type: TileType
  question: string | null
  sql: string
  sql_origin: SqlOrigin
  /** null means Auto: the chart is re-planned from every result. */
  chart_config: Record<string, unknown> | null
  /** null means "as the query returned it": every column, in query order. */
  table_config: TableConfig | null
  max_rows: number | null
  /** null means "inherit the dashboard's default"; 0 means manual only. */
  refresh_interval_seconds: number | null
  /** The resolved rate, so the scheduler needs no copy of the inherit rule. */
  effective_refresh_interval_seconds: number
  grid_x: number
  grid_y: number
  grid_w: number
  grid_h: number
  position: number
  created_at: string
  updated_at: string
}

export interface Dashboard {
  id: string
  name: string
  description: string | null
  status: 'ACTIVE' | 'ARCHIVED'
  grid_columns: number
  row_height_px: number
  gap_px: number
  compact_mode: 'VERTICAL' | 'NONE'
  palette: string
  theme_override: 'INHERIT' | 'DARK' | 'LIGHT'
  default_refresh_interval_seconds: number
  created_at: string
  updated_at: string
  tiles: DashboardTile[]
}

export interface DashboardSummary {
  id: string
  name: string
  description: string | null
  status: 'ACTIVE' | 'ARCHIVED'
  default_refresh_interval_seconds: number
  tile_count: number
  last_refreshed_at: string | null
  created_at: string
  updated_at: string
}

// ── a dashboard as a file ─────────────────────────────────────────────────
/**
 * What an export holds, and the only shape import accepts.
 *
 * Two absences are the point of the format. There are **no ids**: a
 * `connection_id` means nothing in the account that reads the file, so each
 * database a tile needs is a `ref` with a display name, and the importing user
 * says which of *their* connections it is. And there are **no results**: an
 * export is the SQL, never the rows it returned — a file that carried the
 * numbers would turn "share this dashboard" into "send this person an extract
 * of the customer's database".
 */
export interface DashboardDocumentConnection {
  ref: string
  name: string
  /** The engine, so a dialect change can be pointed out before the guard does. */
  database_type: string
}

export interface DashboardDocumentTile {
  /** null for a TEXT tile, and for one whose connection was deleted. */
  connection_ref: string | null
  title: string
  tile_type: TileType
  question: string | null
  sql: string
  sql_origin: SqlOrigin
  chart_config: Record<string, unknown> | null
  table_config: TableConfig | null
  max_rows: number | null
  refresh_interval_seconds: number | null
  grid_x: number
  grid_y: number
  grid_w: number
  grid_h: number
  position: number
}

export interface DashboardDocument {
  format: string
  version: number
  exported_at: string
  dashboard: {
    name: string
    description: string | null
    grid_columns: number
    row_height_px: number
    gap_px: number
    compact_mode: 'VERTICAL' | 'NONE'
    palette: string
    theme_override: 'INHERIT' | 'DARK' | 'LIGHT'
    default_refresh_interval_seconds: number
  }
  connections: DashboardDocumentConnection[]
  tiles: DashboardDocumentTile[]
}

/** A tile the import dropped, named the way the user will look for it. */
export interface ImportSkip {
  title: string
  code: string
  reason: string
}

export interface DashboardImportResult {
  dashboard: Dashboard
  imported_tiles: number
  skipped: ImportSkip[]
}

export interface TilePosition {
  tile_id: string
  grid_x?: number
  grid_y?: number
  grid_w?: number
  grid_h?: number
  position?: number
}

/** Whether one chart type fits a result, and if not, why not. */
export interface ChartOption {
  chart_type: string
  supported: boolean
  reason: string | null
  /**
   * Channel → column, for the columns the backend fitted to reach `supported`.
   * Present only when supported. A picker that keeps its own column selection
   * across a type change is not asking the question this verdict answered.
   */
  columns: Record<string, string> | null
}

/** A redrawn chart for a finished run, plus fresh verdicts. */
export interface ChartRedraw {
  spec: Record<string, unknown> | null
  chart_type: string
  reason: string | null
  options: ChartOption[]
}

/** A statement, the guard's verdict on it, and what it returns. */
export interface SqlDraft {
  sql: string
  validation_status: 'VALID' | 'REJECTED'
  validation_report: {
    status?: string
    issues?: { rule_id: string; severity: string; message: string; hint?: string | null }[]
    referenced_tables?: string[]
    limit_applied?: number | null
  }
  referenced_tables: string[]
  chart_suggestion: Record<string, unknown> | null
  /**
   * Who chose `chart_suggestion`. `model` / `model_adjusted` mean a model read
   * the question; `heuristic` means only the result's column shape was
   * consulted. Only the first two are worth moving the type picker off Auto.
   */
  chart_source: string | null
  /** Per-type verdicts for the picker; empty means "no opinion yet". */
  chart_options: ChartOption[]
  preview: TileResult | null
  question: string | null
  llm_config_id: string | null
}

// ── reports ───────────────────────────────────────────────────────────────
// A report is a *document*: a structure the user approved, prose written over
// real results, and a run kept as a snapshot of a moment. It shares no table
// and no code path with dashboards — see docs/reports-plan.md §1.

/**
 * Derived from the request text server-side, never picked in the UI.
 *
 * It is still read here — the document is laid out right-to-left in Persian
 * and its own furniture is written in it — but there is no control that sets
 * it. See `app/reports/language.py`.
 */
export type ReportLanguage = 'fa' | 'en'

/**
 * What `section_target` may be, mirroring `app/reports/outline.py`.
 *
 * Kept beside the types rather than in a component because it is part of the
 * contract: outside this range the API answers 422, and a stepper that cannot
 * leave it is how the user never sees that.
 */
export const MIN_SECTIONS = 2
export const MAX_SECTIONS = 8
export const DEFAULT_SECTIONS = 5

/** One question, one query, one rendering. No TEXT: prose belongs to sections. */
export type ReportBlockType = 'CHART' | 'TABLE' | 'METRIC'

/**
 * A *label*, and only a label. It drives the prompt when a block is checked and
 * gives the picker something to show; the window itself lives in the SQL as
 * relative date arithmetic the database resolves on every run.
 */
export type ReportTimeWindow =
  | 'none' | 'last_7_days' | 'last_30_days' | 'last_month' | 'last_3_months'
  | 'last_12_months' | 'previous_quarter' | 'ytd' | 'custom'

/** Whether a block can be produced, answered mechanically by the guard. */
export type ReportFeasibility = 'UNCHECKED' | 'FEASIBLE' | 'EMPTY' | 'INFEASIBLE'

/**
 * `PARTIAL` exists because nothing else here generates independently-failable
 * parts: some sections succeeded and some did not, and the status is *derived*
 * from them rather than set.
 */
export type ReportRunStatus =
  | 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'PARTIAL' | 'FAILED' | 'CANCELLED'

export type ReportSectionKind = 'NORMAL' | 'EXECUTIVE_SUMMARY'

export interface ReportBlock {
  id: string
  section_id: string
  position: number
  /** What the user edits in v1 — not the SQL. */
  question: string
  /**
   * What the figure is called in the document: a statement, where `question`
   * is a question. **Empty means the question is used**, so an unset title is
   * a caption, not a blank.
   */
  title: string
  sql: string
  sql_hash: string
  sql_origin: SqlOrigin
  block_type: ReportBlockType
  /** null means Auto — right for a re-run against differently-shaped data. */
  chart_config: Record<string, unknown> | null
  time_window: ReportTimeWindow
  feasibility_status: ReportFeasibility
  /** The guard's own message. Shown verbatim, never re-worded. */
  feasibility_reason: string | null
  feasibility_checked_at: string | null
  max_rows: number | null
  created_at: string
  updated_at: string
}

export interface ReportSection {
  id: string
  report_id: string
  position: number
  heading: string
  /** One line on what this section's paragraph covers. Prompt input, not display. */
  intent: string
  kind: ReportSectionKind
  created_at: string
  updated_at: string
  blocks: ReportBlock[]
}

export interface Report {
  id: string
  name: string
  description: string | null
  /** The user's request, kept verbatim — what the outline was proposed from. */
  prompt: string
  /** Pinned forever: a report keyed to one connection cannot cross policies. */
  connection_id: string | null
  connection_name: string | null
  llm_config_id: string | null
  llm_config_name: string | null
  /** Read off the request, never chosen: it sets the document's direction. */
  language: ReportLanguage
  /** How many sections to ask the model for. The summary is added on top. */
  section_target: number
  status: 'ACTIVE' | 'ARCHIVED'
  created_at: string
  updated_at: string
  sections: ReportSection[]
}

export interface ReportSummary {
  id: string
  name: string
  description: string | null
  connection_id: string | null
  connection_name: string | null
  llm_config_id: string | null
  llm_config_name: string | null
  language: ReportLanguage
  section_target: number
  status: 'ACTIVE' | 'ARCHIVED'
  section_count: number
  created_at: string
  updated_at: string
}

/** What a feasibility check answers: the block, and what the verdict came from. */
export interface ReportBlockCheck {
  block: ReportBlock
  preview: TileResult | null
  chart_suggestion: Record<string, unknown> | null
  chart_options: ChartOption[]
}

export interface ReportRun {
  id: string
  report_id: string
  status: ReportRunStatus
  /** Free text the progress header renders with the two counters below. */
  phase: string
  progress_current: number
  progress_total: number
  llm_config_id: string | null
  /** Which model wrote this document, kept beside it. */
  model_snapshot: Record<string, string>
  prompt_version: string
  language: ReportLanguage
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

/** One block's numbers, snapshotted at the moment they were computed. */
export interface ReportBlockResult {
  id: string
  block_id: string | null
  section_id: string | null
  position: number
  heading_snapshot: string
  /** The caption this figure was published with. Empty falls back to the question. */
  title_snapshot: string
  question_snapshot: string
  sql_text: string
  sql_hash: string
  columns: { name: string; db_type: string; semantic_type: string }[]
  rows: unknown[][]
  row_count: number
  truncated: boolean
  vega_spec: Record<string, unknown> | null
  chart_source: string | null
  chart_note: string | null
  kpi: KpiSpec | null
  computed_at: string
  duration_ms: number
  status: 'OK' | 'FAILED'
  error_code: string | null
  error_message: string | null
  /**
   * Whether the query behind this figure differs from the one the *previous*
   * generation ran. **null means there was nothing to compare with** — a first
   * run, or a block that did not exist last time — which is a different answer
   * from `false` and stays distinguishable.
   */
  sql_changed: boolean | null
}

/** One figure in a paragraph that no result supports. A suspicion, not a verdict. */
export interface NumericFinding {
  text: string
  value: number
  /** `percentage` findings are the expected false positives — marked softly. */
  kind: 'figure' | 'percentage'
}

export interface ReportSectionResult {
  id: string
  section_id: string | null
  position: number
  heading_snapshot: string
  /** What the model wrote. */
  prose: string
  /** What the user wrote over it. **null = not edited**, and reverting sends null. */
  edited_prose: string | null
  /** null means the check did not run; `findings: []` means it ran and found nothing. */
  numeric_check: { checked: number; findings: NumericFinding[] } | null
  status: 'OK' | 'FAILED' | 'SKIPPED_NO_DATA'
  error_message: string | null
  created_at: string
}

/** The poll target: the run, and everything written so far. */
export interface ReportRunDetail extends ReportRun {
  blocks: ReportBlockResult[]
  sections: ReportSectionResult[]
}

/**
 * What a redraw changed, and the verdicts the picker needs to stay honest.
 *
 * Not the whole block result: the row's bulk is its `rows`, a redraw does not
 * touch them, and the caller already has them. `spec` is null when the pick was
 * refused, and `reason` then says why.
 */
export interface ReportChart {
  spec: Record<string, unknown> | null
  /** model | model_adjusted | heuristic | user | none — who chose this picture. */
  chart_source: string
  chart_note: string | null
  reason: string | null
  options: ChartOption[]
}
