/**
 * API client.
 *
 * The access token lives in memory only. The refresh token is an HttpOnly
 * cookie the browser sends automatically, so nothing long-lived is reachable
 * from JavaScript. A 401 triggers exactly one refresh attempt, and concurrent
 * 401s share that attempt rather than stampeding the endpoint.
 */

import type {
  ArtifactSpec, ChartRedraw, Connection, ConversationSummary, Dashboard, DashboardSummary,
  DashboardTile, LlmConfig, MessageWithRun, ProblemDetail, Report, ReportBlock,
  ReportBlockCheck, ReportChart, ReportRun, ReportRunDetail, ReportSection,
  ReportSectionResult,
  ReportSummary, RunDetail, RunEvent, SchemaSnapshot, SemanticDocument, SemanticJob,
  SemanticLayer, SqlDraft, TilePosition, TileResult, TestResult, User,
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

  if (response.status === 401 && retry && !path.startsWith('/auth/')) {
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

// ── llm configs ───────────────────────────────────────────────────────────
export const llmConfigs = {
  list: () => get<LlmConfig[]>('/llm-configs'),
  create: (payload: Record<string, unknown>) =>
    post<LlmConfig>('/llm-configs', payload),
  update: (id: string, payload: Record<string, unknown>) =>
    patch<LlmConfig>(`/llm-configs/${id}`, payload),
  remove: (id: string) => del(`/llm-configs/${id}`),
  test: (id: string) => post<TestResult>(`/llm-configs/${id}/test`),
  testDraft: (payload: Record<string, unknown>) =>
    post<TestResult>('/llm-configs/test', payload),
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
  send: (id: string, payload: { content: string; connection_id?: string; llm_config_id?: string }) =>
    post<{ run_id: string; message_id: string }>(`/conversations/${id}/messages`, payload),
  suggestions: (id: string) =>
    get<{ suggestions: string[] }>(`/conversations/${id}/suggestions`),
}

// ── SQL drafts ────────────────────────────────────────────────────────────
// Two ways to reach one shape. `draft` asks a model; `validate` never does, so
// a user with no provider configured can still build a whole dashboard.
export const sqlDrafts = {
  draft: (payload: { connection_id: string; llm_config_id: string; question: string }) =>
    post<SqlDraft>('/sql/drafts', payload),
  validate: (payload: { connection_id: string; sql: string }) =>
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
    language?: 'fa' | 'en'
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
