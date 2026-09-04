/**
 * API client.
 *
 * The access token lives in memory only. The refresh token is an HttpOnly
 * cookie the browser sends automatically, so nothing long-lived is reachable
 * from JavaScript. A 401 triggers exactly one refresh attempt, and concurrent
 * 401s share that attempt rather than stampeding the endpoint.
 */

import type {
  AnswerFeedback,
  ArtifactSpec, BenchmarkCandidate, BenchmarkOverview, BenchmarkResult,
  BenchmarkRun, BenchmarkSet, ChartRedraw, Connection, ConversationSummary, Dashboard, DashboardDocument,
  DashboardImportResult, DashboardSummary,
  DashboardTile, EmbeddingStatus, KnowledgeHealth, KnowledgeTemplate,
  KnowledgeTemplateList,
  LlmConfig, MaintenanceResult, MessageWithRun, ParameterCatalog, ProblemDetail,
  Report, ReportBlock,
  ReportBlockCheck, ReportChart, ReportRun, ReportRunDetail, ReportSection,
  ReportSectionResult,
  ReportSummary, Review, RunDetail, RunEvent, RunKnowledge, SchemaSnapshot,
  SemanticDocument, SemanticJob, Suggestion,
  SemanticLayer, SqlDraft, TemplateCheckResult, TemplateParam,
  TilePosition, TileResult, TileType, TestResult, User,
} from './types'

const BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

let accessToken: string | null = null
let refreshInFlight: Promise<boolean> | null = null
const listeners = new Set<() => void>()

export function setAccessToken(token: string | null): void {
  accessToken = token
  listeners.forEach((fn) => fn())
}

export function getAccessToken(): string | null {
  return accessToken
}

export function onAuthChange(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export class ApiError extends Error {
  code: string
  status: number
  detail: ProblemDetail | null

  constructor(message: string, code: string, status: number, detail: ProblemDetail | null) {
    super(message)
    this.code = code
    this.status = status
    this.detail = detail
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let body: ProblemDetail | null = null
  try {
    body = (await response.json()) as ProblemDetail
  } catch {
    /* a non-JSON error body is still an error */
  }
  return new ApiError(
    body?.detail ?? body?.title ?? `Request failed (${response.status})`,
    body?.code ?? 'E_UNKNOWN',
    response.status,
    body,
  )
}

async function attemptRefresh(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const response = await fetch(`${BASE}/auth/refresh`, {
          method: 'POST',
          credentials: 'include',
        })
        if (!response.ok) return false
        const data = (await response.json()) as { access_token: string }
        setAccessToken(data.access_token)
        return true
      } catch {
        return false
      } finally {
        // Cleared on the next tick so simultaneous callers share this result.
        setTimeout(() => {
          refreshInFlight = null
        }, 0)
      }
    })()
  }
  return refreshInFlight
}

/**
 * The routes that mint or destroy the session itself, and so must never be
 * retried behind a refresh: refreshing to retry a refresh is circular, and
 * "your credentials are wrong" is not a stale token.
 *
 * This was `/auth/*` until the account screen arrived. `/auth/me` and
 * `/auth/me/password` are ordinary authenticated calls made by a page that
 * has been open for a while — excluding them meant an expired access token
 * turned "save my new display name" into a hard 401 that the refresh cookie
 * sitting right there could have rescued.
 */
const SESSION_ROUTES = ['/auth/login', '/auth/refresh', '/auth/logout']

async function request<T>(
  path: string,
  init: RequestInit = {},
  retry = true,
): Promise<T> {
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type') && init.body) {
    headers.set('Content-Type', 'application/json')
  }
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)

  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
    credentials: 'include',
  })

  if (response.status === 401 && retry && !SESSION_ROUTES.includes(path)) {
    if (await attemptRefresh()) return request<T>(path, init, false)
    setAccessToken(null)
  }

  if (!response.ok) throw await parseError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const get = <T>(path: string) => request<T>(path)
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined })
const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
// PUT for the routes that replace a thing whole rather than amend it — a
// password, a semantic layer, a block's statement. Sending one twice is
// sending it once.
const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PUT', body: JSON.stringify(body) })
const del = (path: string) => request<void>(path, { method: 'DELETE' })

// ── auth ──────────────────────────────────────────────────────────────────
export const auth = {
  async login(email: string, password: string): Promise<User> {
    const tokens = await post<{ access_token: string }>('/auth/login', {
      email,
      password,
    })
    setAccessToken(tokens.access_token)
    return get<User>('/auth/me')
  },
  async restore(): Promise<User | null> {
    if (!(await attemptRefresh())) return null
    try {
      return await get<User>('/auth/me')
    } catch {
      return null
    }
  },
  async logout(): Promise<void> {
    try {
      await post<void>('/auth/logout')
    } finally {
      setAccessToken(null)
    }
  },
  me: () => get<User>('/auth/me'),
  /** Your own display name. Email, role and status stay with an admin. */
  updateProfile: (display_name: string) =>
    patch<User>('/auth/me', { display_name }),
  /**
   * Your own password, proving you know the current one.
   *
   * The server revokes every session and issues a fresh one, so the response
   * carries the access token that replaces the one this call was made with.
   * Forgetting to swap it in would sign the caller out one request later —
   * which is the bug the endpoint was written to avoid.
   */
  async changePassword(current: string, next: string): Promise<void> {
    const tokens = await put<{ access_token: string }>('/auth/me/password', {
      current_password: current,
      new_password: next,
    })
    setAccessToken(tokens.access_token)
  },
}

// ── users ─────────────────────────────────────────────────────────────────
export const users = {
  list: () => get<User[]>('/users'),
  create: (payload: { email: string; display_name: string; role: string }) =>
    post<{ user: User; temporary_password: string }>('/users', payload),
  update: (
    id: string,
    payload: {
      display_name?: string
      email?: string
      role?: string
      status?: string
    },
  ) => patch<User>(`/users/${id}`, payload),
  setPassword: (id: string, password: string) =>
    request<void>(`/users/${id}/password`, {
      method: 'PUT',
      body: JSON.stringify({ password }),
    }),
  remove: (id: string) => del(`/users/${id}`),
}

// ── connections ───────────────────────────────────────────────────────────
export const connections = {
  list: () => get<Connection[]>('/connections'),
  create: (payload: Record<string, unknown>) =>
    post<Connection>('/connections', payload),
  update: (id: string, payload: Record<string, unknown>) =>
    patch<Connection>(`/connections/${id}`, payload),
  remove: (id: string) => del(`/connections/${id}`),
  test: (id: string) => post<TestResult>(`/connections/${id}/test`),
  // Probe credentials that have no row yet, so a connection can be checked
  // before it is created. Records nothing server-side.
  testDraft: (payload: Record<string, unknown>) =>
    post<TestResult>('/connections/test', payload),
  syncSchema: (id: string) => post<SchemaSnapshot>(`/connections/${id}/schema/sync`),
  schema: (id: string) => get<SchemaSnapshot>(`/connections/${id}/schema`),
}

// ── semantic layer ────────────────────────────────────────────────────────
// Generation is a job, not a request: describing forty tables takes minutes,
// so `generate` returns immediately with a job the UI polls through `job()`.
export const semantic = {
  get: (connectionId: string) =>
    get<SemanticLayer>(`/connections/${connectionId}/semantic`),
  save: (connectionId: string, document: SemanticDocument) =>
    request<SemanticLayer>(`/connections/${connectionId}/semantic`, {
      method: 'PUT',
      body: JSON.stringify({ document }),
    }),
  remove: (connectionId: string) => del(`/connections/${connectionId}/semantic`),
  generate: (
    connectionId: string,
    payload: { llm_config_id: string; mode: 'MERGE' | 'REPLACE'; only_tables?: string[] },
  ) => post<SemanticJob>(`/connections/${connectionId}/semantic/generate`, payload),
  job: (connectionId: string, jobId: string) =>
    get<SemanticJob>(`/connections/${connectionId}/semantic/jobs/${jobId}`),
  cancelJob: (connectionId: string, jobId: string) =>
    post<SemanticJob>(`/connections/${connectionId}/semantic/jobs/${jobId}/cancel`),
  // Same parser the save path uses, so the metric editor cannot promise
  // something the backend will later reject.
  check: (
    connectionId: string,
    payload: {
      table: string
      expression: string
      required_joins?: string[]
      is_filter?: boolean
    },
  ) =>
    post<{ valid: boolean; issue: string }>(
      `/connections/${connectionId}/semantic/check`,
      payload,
    ),
}

// ── knowledge templates ───────────────────────────────────────────────────
// A peer of `semantic`, scoped to a connection the same way. `check` is what
// makes the editor honest: the same backend parser that will reject the
// statement at save time answers while it is still being typed, and it
// proposes the parameters in the same round trip because both come from one
// parse.
export const knowledge = {
  // The curator's queue and the ranked backlog. Both read-only and both open
  // to anyone who can read the connection: seeing what people reported, and
  // what nothing here answers, is not a privilege.
  reviews: (connectionId: string, state = 'OPEN') =>
    get<Review[]>(
      `/connections/${connectionId}/knowledge/reviews?state=${state}`,
    ),
  resolve: (
    connectionId: string,
    feedbackId: string,
    payload: { template_id?: string; note?: string; dismiss?: boolean },
  ) =>
    post<AnswerFeedback>(
      `/connections/${connectionId}/knowledge/reviews/${feedbackId}/resolve`,
      payload,
    ),
  suggestions: (connectionId: string) =>
    get<Suggestion[]>(`/connections/${connectionId}/knowledge/suggestions`),
  list: (connectionId: string, includeArchived = false) =>
    get<KnowledgeTemplateList>(
      `/connections/${connectionId}/knowledge/templates` +
        (includeArchived ? '?include_archived=true' : ''),
    ),
  capabilities: (connectionId: string) =>
    get<{ can_curate: boolean }>(`/connections/${connectionId}/knowledge/capabilities`),
  // Store health, and the sweep that produces it. `revalidate` is synchronous
  // rather than a job: the staleness half is a parse per template and the
  // conflict half is two row-capped read-only queries per near-duplicate pair,
  // so the list the curator is looking at refreshes to what the sweep found
  // rather than to a job id.
  health: (connectionId: string) =>
    get<KnowledgeHealth>(`/connections/${connectionId}/knowledge/health`),
  revalidate: (connectionId: string) =>
    post<MaintenanceResult>(
      `/connections/${connectionId}/knowledge/templates/revalidate`,
      {},
    ),
  // Embedding search. The dimension is never sent — it is measured from the
  // provider's own reply, because two endpoints serving one model name at
  // different widths is a thing that happens and a store half indexed at each
  // is a store where similarity means nothing.
  embeddings: (connectionId: string) =>
    get<EmbeddingStatus>(`/connections/${connectionId}/knowledge/embeddings`),
  // `llmConfigId` names which provider embeds. Optional: absent keeps the one
  // already pinned on the connection, and failing that takes the account's
  // first provider that declares an embedding model.
  setEmbeddings: (
    connectionId: string,
    enabled: boolean,
    model = '',
    llmConfigId?: string,
  ) =>
    put<EmbeddingStatus>(`/connections/${connectionId}/knowledge/embeddings`, {
      enabled,
      model,
      llm_config_id: llmConfigId ?? null,
    }),
  check: (
    connectionId: string,
    payload: {
      sql: string
      question?: string
      params?: TemplateParam[]
      // The names the curator has ticked. When present the server does the
      // substitution — on the tree, not by string replacement — and returns
      // the parameterized SQL it would store.
      accept?: string[]
    },
  ) =>
    post<TemplateCheckResult>(
      `/connections/${connectionId}/knowledge/templates/check`,
      payload,
    ),
  create: (
    connectionId: string,
    payload: {
      question: string
      sql: string
      params: TemplateParam[]
      note?: string
      source?: string
      role?: 'RETRIEVABLE' | 'BENCHMARK_ONLY'
    },
  ) =>
    post<KnowledgeTemplate>(`/connections/${connectionId}/knowledge/templates`, payload),
  update: (connectionId: string, id: string, payload: Record<string, unknown>) =>
    patch<KnowledgeTemplate>(
      `/connections/${connectionId}/knowledge/templates/${id}`,
      payload,
    ),
  // The score (Phase 6). Sets, their history, and one run's per-question
  // verdicts — because a score nobody can drill into is a score nobody should
  // trust, and the failure reason on a mismatch is usually the next fix.
  benchmarks: (connectionId: string) =>
    get<BenchmarkOverview>(`/connections/${connectionId}/knowledge/benchmarks`),
  benchmarkCandidates: (connectionId: string) =>
    get<BenchmarkCandidate[]>(
      `/connections/${connectionId}/knowledge/benchmarks/candidates`,
    ),
  createBenchmark: (
    connectionId: string,
    payload: {
      name: string
      description?: string
      template_ids: string[]
      held_out_fraction?: number
    },
  ) =>
    post<BenchmarkSet>(
      `/connections/${connectionId}/knowledge/benchmarks`,
      payload,
    ),
  // Really deletes — a set is an instrument, not somebody's knowledge, and the
  // questions it was built from come back RETRIEVABLE.
  deleteBenchmark: (connectionId: string, setId: string) =>
    del(`/connections/${connectionId}/knowledge/benchmarks/${setId}`),
  runBenchmark: (connectionId: string, setId: string) =>
    post<BenchmarkRun>(
      `/connections/${connectionId}/knowledge/benchmarks/${setId}/run`,
      {},
    ),
  benchmarkResults: (connectionId: string, runId: string) =>
    get<BenchmarkResult[]>(
      `/connections/${connectionId}/knowledge/benchmarks/runs/${runId}/results`,
    ),
  // Archives. The row is never destroyed — the system does not delete a
  // person's work, so this returns the archived template rather than nothing.
  archive: (connectionId: string, id: string) =>
    request<KnowledgeTemplate>(
      `/connections/${connectionId}/knowledge/templates/${id}`,
      { method: 'DELETE' },
    ),
}

// ── llm configs ───────────────────────────────────────────────────────────
export const llmConfigs = {
  // `purpose` narrows the list to rows that can do one job. A configuration
  // declares a chat model, an embedding model, or both, so **every picker that
  // chooses a model to answer with must pass `'chat'`** — otherwise an
  // embeddings-only endpoint is offered as something to ask a question of. The
  // providers page is the one caller that wants all of them.
  list: (purpose?: 'chat' | 'embedding') =>
    get<LlmConfig[]>(`/llm-configs${purpose ? `?purpose=${purpose}` : ''}`),
  create: (payload: Record<string, unknown>) =>
    post<LlmConfig>('/llm-configs', payload),
  update: (id: string, payload: Record<string, unknown>) =>
    patch<LlmConfig>(`/llm-configs/${id}`, payload),
  remove: (id: string) => del(`/llm-configs/${id}`),
  test: (id: string) => post<TestResult>(`/llm-configs/${id}/test`),
  testDraft: (payload: Record<string, unknown>) =>
    post<TestResult>('/llm-configs/test', payload),
  // What each provider documents, so the advanced-parameter form can be
  // generated rather than written out. Fetched once per session: it is a
  // description of two public APIs and holds nothing about anybody.
  parameters: () => get<ParameterCatalog[]>('/llm-configs/parameters'),
}

// ── conversations ─────────────────────────────────────────────────────────
export const conversations = {
  list: () => get<ConversationSummary[]>('/conversations'),
  create: (payload: { connection_id?: string; llm_config_id?: string; title?: string }) =>
    post<ConversationSummary>('/conversations', payload),
  update: (id: string, payload: Record<string, unknown>) =>
    patch<ConversationSummary>(`/conversations/${id}`, payload),
  remove: (id: string) => del(`/conversations/${id}`),
  messages: (id: string) => get<MessageWithRun[]>(`/conversations/${id}/messages`),
  send: (
    id: string,
    payload: {
      content: string
      connection_id?: string
      llm_config_id?: string
      // "Answer this without consulting the knowledge store." Sent by
      // *Generate a fresh answer instead*, after the override is recorded.
      skip_templates?: boolean
    },
  ) => post<{ run_id: string; message_id: string }>(`/conversations/${id}/messages`, payload),
  suggestions: (id: string) =>
    get<{ suggestions: string[] }>(`/conversations/${id}/suggestions`),
}

// ── SQL drafts ────────────────────────────────────────────────────────────
// Two ways to reach one shape. `draft` asks a model; `validate` never does, so
// a user with no provider configured can still build a whole dashboard.
export const sqlDrafts = {
  // `tile_type` is a hint about where the statement is headed, not a promise:
  // METRIC earns SQL rules that ask for a series rather than a lone figure, and
  // a KPI on the preview. Optional, and omitting it behaves as before.
  draft: (payload: {
    connection_id: string
    llm_config_id: string
    question: string
    tile_type?: TileType
  }) => post<SqlDraft>('/sql/drafts', payload),
  validate: (payload: { connection_id: string; sql: string; tile_type?: TileType }) =>
    post<SqlDraft>('/sql/drafts/validate', payload),
}

// ── dashboards ────────────────────────────────────────────────────────────
// `data` is the only call that returns numbers: reading a dashboard returns
// its layout, and the tiles that are *due* are asked for separately, because
// each one is on its own clock.
export const dashboards = {
  list: () => get<DashboardSummary[]>('/dashboards'),
  create: (payload: Record<string, unknown>) => post<Dashboard>('/dashboards', payload),
  get: (id: string) => get<Dashboard>(`/dashboards/${id}`),
  update: (id: string, payload: Record<string, unknown>) =>
    patch<Dashboard>(`/dashboards/${id}`, payload),
  remove: (id: string) => del(`/dashboards/${id}`),

  addTile: (id: string, payload: Record<string, unknown>) =>
    post<DashboardTile>(`/dashboards/${id}/tiles`, payload),
  updateTile: (id: string, tileId: string, payload: Record<string, unknown>) =>
    patch<DashboardTile>(`/dashboards/${id}/tiles/${tileId}`, payload),
  removeTile: (id: string, tileId: string) => del(`/dashboards/${id}/tiles/${tileId}`),
  duplicateTile: (id: string, tileId: string) =>
    post<DashboardTile>(`/dashboards/${id}/tiles/${tileId}/duplicate`),
  // One call per drag-end, carrying every tile the drag moved.
  setLayout: (id: string, positions: TilePosition[]) =>
    patch<DashboardTile[]>(`/dashboards/${id}/layout`, { positions }),

  /**
   * The dashboard as a portable document — the layout and the SQL, no ids and
   * no results. Fetched rather than linked: a `<a download>` carries no bearer
   * token, so the browser saves the body this returns (see
   * `components/dashboard-transfer.tsx`).
   */
  exportDocument: (id: string) => get<DashboardDocument>(`/dashboards/${id}/export`),
  /**
   * Create a dashboard from a document. `connection_map` answers the one
   * question a file cannot: which of *this* user's connections each of its
   * databases is. Every statement in it runs the guard on the way in, so a
   * rejected tile is a 422 — or, with `skip_invalid`, a reported loss.
   */
  importDocument: (payload: {
    document: unknown
    name?: string | null
    connection_map: Record<string, string>
    skip_invalid?: boolean
  }) => post<DashboardImportResult>('/dashboards/import', payload),

  data: (id: string, tileIds: string[] = [], force = false) =>
    post<{ results: Record<string, TileResult> }>(
      `/dashboards/${id}/data${force ? '?force=true' : ''}`,
      { tile_ids: tileIds },
    ),
  tileData: (id: string, tileId: string, force = false) =>
    post<TileResult>(
      `/dashboards/${id}/tiles/${tileId}/data${force ? '?force=true' : ''}`,
    ),
}

// ── reports ───────────────────────────────────────────────────────────────
/**
 * A report is a template plus its runs, and the two are addressed apart:
 * everything up to `startRun` edits the document's *structure*, and everything
 * after it reads or repairs one generation of it.
 *
 * Two calls are deliberately synchronous and slow — `proposeOutline` is one
 * model call and `checkBlock` is five to ten seconds of guard work on one
 * heading, both with the user watching a single thing. Generation is the one
 * that is not: it is minutes, so it returns 202 and `run()` is polled.
 */
export const reports = {
  list: () => get<ReportSummary[]>('/reports'),
  create: (payload: {
    name: string
    description?: string | null
    prompt?: string
    connection_id: string
    llm_config_id?: string | null
    /** How many sections the model is asked for. No language: it is derived
     *  from `prompt` server-side, so there is nothing here to disagree with. */
    section_target?: number
  }) => post<Report>('/reports', payload),
  get: (id: string) => get<Report>(`/reports/${id}`),
  /** Name, description, prompt, model, status. A *different* connection is 422. */
  update: (id: string, payload: Record<string, unknown>) =>
    patch<Report>(`/reports/${id}`, payload),
  remove: (id: string) => del(`/reports/${id}`),

  /** One model call, and it **replaces** the outline. Returns the whole report. */
  proposeOutline: (id: string) => post<Report>(`/reports/${id}/outline`),

  addSection: (id: string, payload: Record<string, unknown>) =>
    post<ReportSection>(`/reports/${id}/sections`, payload),
  updateSection: (id: string, sectionId: string, payload: Record<string, unknown>) =>
    patch<ReportSection>(`/reports/${id}/sections/${sectionId}`, payload),
  removeSection: (id: string, sectionId: string) =>
    del(`/reports/${id}/sections/${sectionId}`),

  addBlock: (id: string, sectionId: string, payload: Record<string, unknown>) =>
    post<ReportBlock>(`/reports/${id}/sections/${sectionId}/blocks`, payload),
  /** Editing the question resets the block to UNCHECKED and drops its SQL. */
  updateBlock: (id: string, blockId: string, payload: Record<string, unknown>) =>
    patch<ReportBlock>(`/reports/${id}/blocks/${blockId}`, payload),
  removeBlock: (id: string, blockId: string) =>
    del(`/reports/${id}/blocks/${blockId}`),
  /** *Can this be produced, and if not, why.* Answers with a verdict, never a 502. */
  checkBlock: (id: string, blockId: string) =>
    post<ReportBlockCheck>(`/reports/${id}/blocks/${blockId}/check`),
  /**
   * Write the block's statement by hand. The same answer as `checkBlock`, by
   * the other road: guarded and previewed, with no model involved at all.
   *
   * `sql_origin` comes back derived from what the block already held — a
   * client neither sends it nor could gain anything by sending it.
   */
  editBlockSql: (id: string, blockId: string, sql: string) =>
    put<ReportBlockCheck>(`/reports/${id}/blocks/${blockId}/sql`, { sql }),

  startRun: (id: string) => post<ReportRun>(`/reports/${id}/runs`),
  runs: (id: string) => get<ReportRun[]>(`/reports/${id}/runs`),
  /** The poll target: the run and every result written so far. */
  run: (id: string, runId: string) =>
    get<ReportRunDetail>(`/reports/${id}/runs/${runId}`),
  cancelRun: (id: string, runId: string) =>
    post<ReportRun>(`/reports/${id}/runs/${runId}/cancel`),
  /** Rebuild one section of a finished run. 202, onto the same poll. */
  retrySection: (id: string, runId: string, sectionId: string) =>
    post<ReportRun>(`/reports/${id}/runs/${runId}/sections/${sectionId}/retry`),
  /** Write over a paragraph; `null` reverts to what the model wrote. */
  editProse: (id: string, runId: string, sectionId: string, editedProse: string | null) =>
    patch<ReportSectionResult>(
      `/reports/${id}/runs/${runId}/sections/${sectionId}`,
      { edited_prose: editedProse },
    ),
  /**
   * Draw one saved block a different way, from the rows the run kept.
   *
   * Unlike the chat redraw this one **persists**, onto the run: a report is
   * printed from its saved run, so a chart living only in the browser would not
   * survive the export. `auto` hands the planner no suggestion at all.
   */
  redrawBlockChart: (id: string, runId: string, resultId: string, chartType: string) =>
    post<ReportChart>(
      `/reports/${id}/runs/${runId}/blocks/${resultId}/chart`,
      { chart_type: chartType },
    ),
}

/** Whether a report run may still write more rows — the poll's stop condition. */
export function isReportRunInFlight(status: string): boolean {
  return status === 'QUEUED' || status === 'RUNNING'
}

// ── runs ──────────────────────────────────────────────────────────────────
/**
 * Whether the executor may still emit events for this run — the mirror of
 * `RunStatus.is_in_flight` on the backend, and deliberately *not* the inverse
 * of "terminal". `NEEDS_CLARIFICATION` is neither: the run wrote its question
 * and closed, while the exchange is unfinished. Anything deciding whether to
 * wait for more events wants this one.
 */
export function isRunInFlight(status: string): boolean {
  return status === 'QUEUED' || status === 'RUNNING'
}

export const runs = {
  get: (id: string) => get<RunDetail>(`/runs/${id}`),
  cancel: (id: string) => post<{ cancelled: boolean }>(`/runs/${id}/cancel`),
  /**
   * Run the same question again, against the same user message — so the
   * transcript keeps one question where the reader asked one. Refused for a
   * run that is still going, or one that already produced an answer.
   */
  retry: (id: string) =>
    post<{ run_id: string; message_id: string }>(`/runs/${id}/retry`),
  // Records that a reader did not believe a verified answer. Split from
  // re-asking on purpose: the *measurement* has to survive a reader who closes
  // the tab instead of re-asking, and that measurement — the override rate —
  // is what the short-circuit threshold is tuned from.
  override: (id: string) => post<RunKnowledge>(`/runs/${id}/override`),
  // *Was this right?* — open to any signed-in user, on purpose. The person
  // best placed to notice a wrong answer is the person who asked, and they are
  // usually not the person allowed to fix it.
  feedback: (id: string, payload: { verdict: string; comment?: string }) =>
    post<AnswerFeedback>(`/runs/${id}/feedback`, payload),
  artifact: (id: string) => get<{ id: string; kind: string; spec: ArtifactSpec }>(`/artifacts/${id}`),
  poll: (id: string, after: number) =>
    get<RunEvent[]>(`/runs/${id}/events/poll?after=${after}`),
  /**
   * Redraw a finished run's result as another chart type. The response also
   * carries fresh per-type verdicts, so opening the picker and changing the
   * chart are one round trip rather than two.
   */
  redrawChart: (id: string, chartType: string) =>
    post<ChartRedraw>(`/runs/${id}/chart`, { chart_type: chartType }),
}

/**
 * Stream a run's events.
 *
 * EventSource cannot send an Authorization header, so this uses fetch with a
 * ReadableStream and parses the SSE frames directly. On failure it falls back
 * to polling, which is why the backend exposes both.
 */
export function streamRun(
  runId: string,
  handlers: {
    onEvent: (event: RunEvent) => void
    onDone: () => void
    onError?: (error: Error) => void
  },
): () => void {
  const controller = new AbortController()
  let lastSeq = 0
  let stopped = false

  const stop = () => {
    stopped = true
    controller.abort()
  }

  ;(async () => {
    try {
      const response = await fetch(`${BASE}/runs/${runId}/events?after=${lastSeq}`, {
        headers: {
          Authorization: `Bearer ${accessToken ?? ''}`,
          Accept: 'text/event-stream',
        },
        credentials: 'include',
        signal: controller.signal,
      })

      if (!response.ok || !response.body) throw new Error('stream unavailable')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (!stopped) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const frames = buffer.split('\n\n')
        buffer = frames.pop() ?? ''

        for (const frame of frames) {
          const dataLine = frame.split('\n').find((l) => l.startsWith('data: '))
          if (!dataLine) continue
          try {
            const event = JSON.parse(dataLine.slice(6)) as RunEvent
            lastSeq = event.seq
            handlers.onEvent(event)
            if (event.type === 'RUN_FINISHED') {
              stop()
              handlers.onDone()
              return
            }
          } catch {
            /* a malformed frame should not kill the stream */
          }
        }
      }
      // The body ended and `RUN_FINISHED` never arrived — the `return` above
      // is the only exit for a run that actually completed. So the server went
      // away mid-run: an API restart, a proxy idle timeout, a dropped
      // connection. That is not "done", and reporting it as done is what makes
      // the step trail lurch: the caller clears the live view, reloads the
      // thread, finds the run still in flight, re-attaches from seq 0, and the
      // whole pipeline replays in one burst with the chart on the end of it.
      //
      // A clean EOF and a thrown read are the same event with different
      // plumbing, so they take the same recovery.
      if (!stopped) await pollUntilDone(runId, lastSeq, handlers, () => stopped)
    } catch (error) {
      if (stopped) return
      handlers.onError?.(error as Error)
      // Fall back to polling rather than leaving the UI stuck mid-run.
      await pollUntilDone(runId, lastSeq, handlers, () => stopped)
    }
  })()

  return stop
}

async function pollUntilDone(
  runId: string,
  fromSeq: number,
  handlers: { onEvent: (event: RunEvent) => void; onDone: () => void },
  isStopped: () => boolean,
): Promise<void> {
  let seq = fromSeq
  // Polling is the recovery path, so it is reached exactly when the server is
  // unreachable — a restart, a redeploy, a blip. Giving up on the first failed
  // poll hands the reader a run frozen mid-pipeline at the one moment the
  // fallback exists for. A few attempts cover a restart; a run that stays
  // unreachable past that is reported rather than polled forever.
  let failures = 0
  while (!isStopped()) {
    try {
      const events = await runs.poll(runId, seq)
      failures = 0
      for (const event of events) {
        seq = event.seq
        handlers.onEvent(event)
        if (event.type === 'RUN_FINISHED') {
          handlers.onDone()
          return
        }
      }
      if (events.length === 0) {
        // `RUN_FINISHED` is not guaranteed to arrive. A run whose executor died
        // with the process is swept by the reconciler, which marks the row
        // FAILED with a plain UPDATE and emits nothing — there is no event left
        // to wait for, and the loop above would wait for it every 1.2s forever.
        // Quiet polls are the only moment worth the extra request, and they are
        // exactly the moment this happens.
        const { status } = await runs.get(runId)
        if (!isRunInFlight(status)) {
          handlers.onDone()
          return
        }
      }
    } catch {
      if ((failures += 1) >= POLL_MAX_FAILURES) {
        handlers.onDone()
        return
      }
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
  }
}

const POLL_INTERVAL_MS = 1200
const POLL_MAX_FAILURES = 5   // ~6s, comfortably longer than an API restart
