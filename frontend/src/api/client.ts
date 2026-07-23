/**
 * API client.
 *
 * The access token lives in memory only. The refresh token is an HttpOnly
 * cookie the browser sends automatically, so nothing long-lived is reachable
 * from JavaScript. A 401 triggers exactly one refresh attempt, and concurrent
 * 401s share that attempt rather than stampeding the endpoint.
 */

import type {
  ArtifactSpec, Connection, ConversationSummary, LlmConfig, MessageWithRun,
  ProblemDetail, RunDetail, RunEvent, SchemaSnapshot, TestResult, User,
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
  update: (id: string, payload: { role?: string; status?: string }) =>
    patch<User>(`/users/${id}`, payload),
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
}

// ── runs ──────────────────────────────────────────────────────────────────
export const runs = {
  get: (id: string) => get<RunDetail>(`/runs/${id}`),
  cancel: (id: string) => post<{ cancelled: boolean }>(`/runs/${id}/cancel`),
  artifact: (id: string) => get<{ id: string; kind: string; spec: ArtifactSpec }>(`/artifacts/${id}`),
  poll: (id: string, after: number) =>
    get<RunEvent[]>(`/runs/${id}/events/poll?after=${after}`),
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
      if (!stopped) handlers.onDone()
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
  while (!isStopped()) {
    try {
      const events = await runs.poll(runId, seq)
      for (const event of events) {
        seq = event.seq
        handlers.onEvent(event)
        if (event.type === 'RUN_FINISHED') {
          handlers.onDone()
          return
        }
      }
    } catch {
      handlers.onDone()
      return
    }
    await new Promise((resolve) => setTimeout(resolve, 1200))
  }
}
