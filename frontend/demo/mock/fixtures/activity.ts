/**
 * What the installation has been doing: the token usage the Usage section
 * charts, and the audit log the Administration section reads.
 *
 * Usage is a deterministic list of operations — one per model call path the
 * product counts — over the four months before `DEMO_TODAY`, aggregated into
 * buckets exactly the way `usage_service.clamp_window` aligns them, so every
 * period the page offers (an hour to ninety days, or custom) has the bucket
 * width the real server would choose. Runs asked in this session are added on
 * top, so asking a question moves your own usage.
 */
import type { AuditEntry, UsageBucket, UsageModel, UsageSeries, UsageTotal } from '../../../src/api/types'
import { daysAgo, demoNow } from '../clock'
import { IDS, LLM_CONFIGS } from './world'

// ── a small deterministic generator ──────────────────────────────────────────
function mulberry32(seed: number) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export interface Operation {
  at: number
  actorId: string
  actor: string
  model: string
  prompt: number
  completion: number
}

/** Who asks, how often on a working day, and which model they reach for. */
const ASKERS: { id: string; name: string; perDay: number; sonnet: number; until?: number }[] = [
  { id: IDS.users.mazbar, name: 'Mazbar Azami', perDay: 7.5, sonnet: 0.8 },
  { id: IDS.users.priya, name: 'Priya Nair', perDay: 5.2, sonnet: 0.55 },
  { id: IDS.users.tomas, name: 'Tomás Álvarez', perDay: 4.1, sonnet: 0.7 },
  { id: IDS.users.leila, name: 'Leila Karimi', perDay: 2.6, sonnet: 0.9 },
  { id: IDS.users.chen, name: 'Chen Wei', perDay: 3.3, sonnet: 0.35 },
  // Disabled five weeks ago; her history stays.
  { id: IDS.users.hannah, name: 'Hannah Schmidt', perDay: 1.8, sonnet: 0.5, until: 36 },
]

const HISTORY_DAYS = 120

function generate(): Operation[] {
  const random = mulberry32(20260925)
  const ops: Operation[] = []
  const sonnet = LLM_CONFIGS[0].model
  const gpt = LLM_CONFIGS[1].model
  for (let day = HISTORY_DAYS; day >= 0; day -= 1) {
    const date = new Date(daysAgo(day, 0))
    const weekday = date.getUTCDay()
    const weekend = weekday === 0 || weekday === 6
    // Month-end close: finance asks more in the last days of a month.
    const monthEnd = date.getUTCDate() >= 26
    for (const asker of ASKERS) {
      if (asker.until !== undefined && day < asker.until) continue
      const rate = asker.perDay * (weekend ? 0.12 : 1) * (monthEnd && asker.id === IDS.users.chen ? 2.2 : 1)
      let n = Math.floor(rate)
      if (random() < rate - n) n += 1
      for (let i = 0; i < n; i += 1) {
        const hour = 8 + random() * 10
        const at = Date.parse(daysAgo(day, hour))
        if (at > demoNow()) continue
        const useSonnet = random() < asker.sonnet
        // A chat run is route + clarify + generate + present + chart: the
        // schema block is sent twice, which is most of the prompt side.
        const prompt = Math.round((useSonnet ? 15800 : 14900) + random() * 5200)
        const completion = Math.round(260 + random() * 520)
        ops.push({ at, actorId: asker.id, actor: asker.name, model: useSonnet ? sonnet : gpt, prompt, completion })
      }
    }
  }
  return ops
}

const OPERATIONS = generate()
const session: Operation[] = []

/** A run asked in this session, counted in everybody's usage from now on. */
export function recordOperation(op: Operation): void {
  session.push(op)
}

// ── clamp_window, ported ─────────────────────────────────────────────────────
const GRANULARITY: [number, number][] = [
  [2 * 3600, 5 * 60],
  [12 * 3600, 15 * 60],
  [2 * 86400, 3600],
  [14 * 86400, 6 * 3600],
]
const DAY = 86400

interface Window { since: number; until: number; bucket: number; offset: number }

function clampWindow(since: number | null, until: number | null, offsetMinutes: number, now: number): Window {
  const end = until ?? now
  let start = since ?? end - 30 * DAY * 1000
  const widest = end - 366 * DAY * 1000
  if (start < widest) start = widest
  const offset = Math.max(-840, Math.min(840, offsetMinutes)) * 60
  if (start >= end) return { since: end, until: end, bucket: 300, offset }
  const span = (end - start) / 1000
  const bucket = GRANULARITY.find(([longest]) => span <= longest)?.[1] ?? DAY
  const count = Math.ceil(span / bucket)
  const last = floorTo(end - 1, bucket, offset)
  const first = last - bucket * (count - 1) * 1000
  return { since: first, until: end, bucket, offset }
}

function floorTo(ms: number, seconds: number, offset: number): number {
  const epoch = Math.floor(ms / 1000)
  return (Math.floor((epoch + offset) / seconds) * seconds - offset) * 1000
}

/**
 * The window the page asked for, moved onto the demo's clock.
 *
 * The page computes "the last 30 days" from the visitor's own clock; the demo
 * lives on `DEMO_TODAY`. Shifting the request by the difference answers the
 * question the page meant — the last 30 days of *this* world — and the series
 * it returns names its own `since`/`until`, which is what the chart draws.
 */
function demoWindow(params: { since?: string; until?: string; tz_offset?: number }): Window {
  const shift = Date.now() - demoNow()
  const since = params.since ? Date.parse(params.since) - shift : null
  const until = params.until ? Math.min(Date.parse(params.until) - shift, demoNow()) : null
  return clampWindow(since, until, params.tz_offset ?? 0, demoNow())
}

function bucketsOf(ops: Operation[], w: Window): UsageBucket[] {
  const map = new Map<number, UsageBucket>()
  for (const op of ops) {
    const start = floorTo(op.at, w.bucket, w.offset)
    const b = map.get(start) ?? {
      start: new Date(start).toISOString(), prompt_tokens: 0, completion_tokens: 0, runs: 0,
      cache_read_tokens: null, cache_write_tokens: null,
    }
    b.prompt_tokens += op.prompt
    b.completion_tokens += op.completion
    b.runs += 1
    map.set(start, b)
  }
  return [...map.entries()].sort((a, b) => a[0] - b[0]).map(([, b]) => b)
}

function seriesOf(ops: Operation[], w: Window, actorId: string | null, actor: string): UsageSeries {
  const inside = ops.filter((op) => op.at >= w.since && op.at < w.until)
  const byModel = new Map<string, Operation[]>()
  for (const op of inside) byModel.set(op.model, [...(byModel.get(op.model) ?? []), op])
  const models: UsageModel[] = [...byModel.entries()]
    .map(([model, list]) => ({
      model,
      prompt_tokens: list.reduce((s, o) => s + o.prompt, 0),
      completion_tokens: list.reduce((s, o) => s + o.completion, 0),
      runs: list.length,
      unmeasured: 0,
      // Neither provider row reports cache figures here: null is "not
      // reported", which the screen shows as nothing rather than as zero.
      cache_read_tokens: null,
      cache_write_tokens: null,
      cache_measured: 0,
      buckets: bucketsOf(list, w),
    }))
    .sort((a, b) => b.prompt_tokens + b.completion_tokens - (a.prompt_tokens + a.completion_tokens))
  return {
    actor_id: actorId,
    actor,
    prompt_tokens: inside.reduce((s, o) => s + o.prompt, 0),
    completion_tokens: inside.reduce((s, o) => s + o.completion, 0),
    runs: inside.length,
    unmeasured: 0,
    cache_read_tokens: null,
    cache_write_tokens: null,
    cache_measured: 0,
    since: new Date(w.since).toISOString(),
    until: new Date(w.until).toISOString(),
    bucket_seconds: w.bucket,
    buckets: bucketsOf(inside, w),
    models,
  }
}

function all(): Operation[] {
  return [...OPERATIONS, ...session]
}

export function usageMine(userId: string, name: string, params: { since?: string; until?: string; tz_offset?: number }): UsageSeries {
  const w = demoWindow(params)
  return seriesOf(all().filter((op) => op.actorId === userId), w, userId, name)
}

export function usageByPerson(params: { since?: string; until?: string; tz_offset?: number }): UsageSeries[] {
  const w = demoWindow(params)
  const people = new Map<string, string>()
  for (const op of all()) people.set(op.actorId, op.actor)
  return [...people.entries()]
    .map(([id, name]) => seriesOf(all().filter((op) => op.actorId === id), w, id, name))
    .filter((s) => s.runs > 0)
    .sort((a, b) => b.prompt_tokens + b.completion_tokens - (a.prompt_tokens + a.completion_tokens))
}

export function usageTotal(params: { since?: string; until?: string; tz_offset?: number }): UsageTotal {
  const w = demoWindow(params)
  return { ...seriesOf(all(), w, null, 'All users'), unattributed: 0, unattributed_tokens: 0 }
}

// ── the audit log ────────────────────────────────────────────────────────────
/**
 * Identifiers and counts, never content — the writer's own rule. Newest first,
 * the way `GET /audit` serves it.
 */
export const AUDIT: AuditEntry[] = ([
  [0.2, 'Mazbar Azami', 'grant.created', 'connection', IDS.connections.sales, 'SUCCESS', { privilege: 'select', team_id: IDS.teams.finance }],
  [0.9, 'Olivia Bennett', 'access.denied', 'conversation', null, 'DENIED', { needed: 'conversation.create' }],
  [1.3, 'Leila Karimi', 'knowledge.feedback.recorded', 'run', null, 'SUCCESS', { verdict: 'NEEDS_REVIEW' }],
  [2.1, 'Priya Nair', 'disclosure.changed', 'connection', IDS.connections.sakila, 'SUCCESS', { from: 'SAMPLE', to: 'AGGREGATE' }],
  [2.1, 'Priya Nair', 'grant.created', 'connection', IDS.connections.sakila, 'SUCCESS', { privilege: 'select', team_id: IDS.teams.analytics }],
  [3.4, 'Mazbar Azami', 'role.assigned', 'user', IDS.users.leila, 'SUCCESS', { role: 'Knowledge Manager' }],
  [4.0, 'Marcus Johnson', 'access.denied', 'connection', IDS.connections.sales, 'DENIED', { needed: 'select' }],
  [5.6, 'Mazbar Azami', 'team.member.added', 'team', IDS.teams.revops, 'SUCCESS', { user_id: IDS.users.tomas }],
  [6.2, 'Priya Nair', 'llm_config.endpoint.changed', 'llm_config', IDS.llm.gpt, 'SUCCESS', { field: 'base_url' }],
  [8.8, 'Mazbar Azami', 'grant.revoked', 'connection', IDS.connections.sales, 'SUCCESS', { privilege: 'select', user_id: IDS.users.hannah }],
  [9.0, 'Mazbar Azami', 'user.manage', 'user', IDS.users.hannah, 'SUCCESS', { status: 'DISABLED' }],
  [12.5, 'Mazbar Azami', 'service_user.created', 'user', IDS.service.digest, 'SUCCESS', { roles: 1 }],
  [14.1, 'Tomás Álvarez', 'ownership.transferred', 'dashboard', null, 'SUCCESS', { to: IDS.users.chen }],
  [19.7, 'Mazbar Azami', 'team.created', 'team', IDS.teams.revops, 'SUCCESS', {}],
] as [number, string, string, string, string | null, string, Record<string, unknown>][])
  .map(([days, actor, action, resource_type, resource_id, outcome, detail], i) => ({
    at: daysAgo(days, 17 - (i % 7)),
    actor,
    actor_ip: `10.40.${12 + (i % 5)}.${30 + i * 7}`,
    action,
    resource_type,
    resource_id,
    outcome,
    detail,
  }))
