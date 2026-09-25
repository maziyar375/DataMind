/**
 * Scripted playback of a run: the step trail, the streamed answer and the
 * result preview, in the order and with the payloads the real pipeline emits.
 *
 * The event contract is `backend/app/pipeline/graph.py` (`STEP_STARTED`
 * `{seq, name}`, `STEP_FINISHED` `{seq, name, status, detail, duration_ms}`)
 * and `services/run_service.py` (`RUN_STARTED`, `RUN_FINISHED`), plus what the
 * nodes emit along the way — `SQL_GENERATED`, `SQL_REJECTED`,
 * `SQL_VALIDATED`, `QUERY_COMPLETED`, `RESULT_PREVIEW`, `TEXT_DELTA`. The
 * details are the nodes' own sentences (`pipeline/nodes/__init__.py`), filled
 * in from the recorded run.
 *
 * A timeline is a pure function of the answer and `timing.ts`, so the live
 * trail and the persisted one are the same object read at two moments: a
 * reload mid-run replays what has "happened" and carries on from there, which
 * is what the real SSE endpoint does from `Last-Event-ID`.
 */
import type { Connection, LlmConfig, RunDetail, RunEvent, RunStep } from '../../src/api/types'
import type { ScriptedAnswer } from './script-types'
import { SNAPSHOTS } from './fixtures/schema.generated'
import {
  FALLBACK_TICK_MS, RUN_START_MS, STEP_MS, TEXT_CHUNK_CHARS, TEXT_TICK_MS,
} from './timing'

export interface TimedEvent {
  /** Milliseconds after the run started. */
  at: number
  type: string
  data: Record<string, unknown>
}

export interface Timeline {
  events: TimedEvent[]
  /** Every step as the finished run records it. */
  steps: RunStep[]
  /** When `RUN_FINISHED` fires. */
  total: number
  repairs: number
  promptTokens: number | null
  completionTokens: number | null
}

/** Roughly what a prompt costs: four characters a token, the usual rule. */
const tokens = (chars: number) => Math.max(1, Math.round(chars / 4))

/** The schema block every generating call is sent, sized like `retrieve` sizes it. */
function schemaChars(key: 'sales' | 'sakila'): number {
  return SNAPSHOTS[key].tables.reduce((sum, t) => sum + 60 + 40 * t.columns.length, 0)
}

function chunks(text: string, size: number): string[] {
  // Split on characters, not UTF-16 units, so a Persian letter or an emoji is
  // never cut in half on its way to the screen.
  const chars = [...text]
  const out: string[] = []
  for (let i = 0; i < chars.length; i += size) out.push(chars.slice(i, i + size).join(''))
  return out
}

export function scriptRun(
  answer: ScriptedAnswer,
  connection: Connection,
  model: LlmConfig,
  runId: string,
): Timeline {
  const key = answer.connection
  const events: TimedEvent[] = []
  const steps: RunStep[] = []
  let t = RUN_START_MS
  let seq = 0
  let prompt = 0
  let completion = 0
  const schema = tokens(schemaChars(key))
  const tableCount = SNAPSHOTS[key].tables.length

  events.push({
    at: 0,
    type: 'RUN_STARTED',
    data: { run_id: runId, model: model.model, connection: connection.name },
  })

  function step(
    name: string,
    status: RunStep['status'],
    detail: string | null,
    duration: number,
    usage?: { prompt: number; completion: number },
    during?: (start: number, end: number) => void,
  ) {
    seq += 1
    const start = t
    const end = t + duration
    events.push({ at: start, type: 'STEP_STARTED', data: { seq, name } })
    during?.(start, end)
    events.push({
      at: end,
      type: 'STEP_FINISHED',
      data: { seq, name, status, detail, duration_ms: duration },
    })
    if (usage) {
      prompt += usage.prompt
      completion += usage.completion
    }
    steps.push({
      seq, name, status, detail, duration_ms: duration,
      ...(usage
        ? { prompt_tokens: usage.prompt, completion_tokens: usage.completion, llm_calls: 1 }
        : {}),
    })
    t = end
  }

  function stream(text: string, start: number, end: number) {
    const parts = chunks(text, TEXT_CHUNK_CHARS)
    const gap = Math.max(TEXT_TICK_MS, (end - start - 120) / Math.max(1, parts.length))
    parts.forEach((part, i) => {
      events.push({ at: Math.min(end - 5, start + 90 + i * gap), type: 'TEXT_DELTA', data: { text: part } })
    })
  }

  const metadata = answer.intent === 'METADATA'
  step('route', 'DONE', `Classified ${answer.intent} in ${STEP_MS.route}ms`, STEP_MS.route,
    { prompt: 104, completion: 2 })
  if (metadata) {
    step('match', 'SKIPPED', 'Not analytical (METADATA)', STEP_MS.skipped)
  } else {
    step('match', 'DONE', 'No template matched (best 0.00)', STEP_MS.match)
  }
  // No sections on either connection: the node says nothing and the trail
  // hides it, exactly as it does for a real connection without sections.
  step('scope', 'SKIPPED', null, STEP_MS.scope)
  step('retrieve', 'DONE', `${tableCount} tables via FULL_SNAPSHOT`, STEP_MS.retrieve)

  if (metadata) {
    // A schema question halts here, with its answer streamed by `describe`.
    const duration = STEP_MS.describe
    step('describe', 'DONE', `Described ${tableCount} of ${tableCount} tables in ${duration - 6}ms`,
      duration, { prompt: schema + 310, completion: tokens(answer.answer.length) },
      (start, end) => stream(answer.answer, start, end))
  } else {
    step('describe', 'SKIPPED', 'Not a schema question', STEP_MS.skipped)
    step('clarify', 'DONE', `Answerable as asked, in ${STEP_MS.clarify - 4}ms`, STEP_MS.clarify,
      { prompt: schema + 270, completion: 36 })

    answer.attempts.forEach((attempt, i) => {
      const first = i === 0
      const duration = first ? STEP_MS.generate : STEP_MS.regenerate
      const previous = first ? 0 : tokens(answer.attempts[i - 1].raw_sql.length) + 180
      step('generate', 'DONE', `Attempt ${attempt.attempt_no} drafted`, duration,
        { prompt: schema + 540 + previous, completion: tokens(attempt.raw_sql.length) + 24 },
        (_start, end) => events.push({
          at: end - 2, type: 'SQL_GENERATED', data: { attempt_no: attempt.attempt_no, sql: attempt.raw_sql },
        }))
      const report = attempt.validation_report
      if (attempt.validation_status === 'VALID') {
        step('validate', 'DONE', `Valid · ${attempt.referenced_tables.length} tables`, STEP_MS.validate,
          undefined, (_start, end) => events.push({
            at: end - 1, type: 'SQL_VALIDATED',
            data: {
              attempt_no: attempt.attempt_no, sql: attempt.rewritten_sql,
              referenced_tables: attempt.referenced_tables, limit_applied: report.limit_applied ?? null,
            },
          }))
      } else {
        const codes = (report.issues ?? []).map((issue) => issue.rule_id)
        step('validate', 'DONE', `Rejected: ${codes.join(', ')}`, STEP_MS.validate,
          undefined, (_start, end) => events.push({
            at: end - 1, type: 'SQL_REJECTED', data: { attempt_no: attempt.attempt_no, issues: report.issues ?? [] },
          }))
      }
    })

    const result = answer.result!
    const executeMs = result.duration_ms + STEP_MS.executeOverhead
    step('execute', 'DONE', `${result.row_count} rows in ${result.duration_ms}ms`, executeMs, undefined,
      (_start, end) => {
        events.push({
          at: end - 2, type: 'QUERY_COMPLETED',
          data: {
            row_count: result.row_count, duration_ms: result.duration_ms,
            truncated: result.truncated, rows_scanned_estimate: result.rows_scanned_estimate,
          },
        })
        events.push({
          at: end - 1, type: 'RESULT_PREVIEW',
          data: { columns: result.columns, rows: result.rows, row_count: result.row_count, truncated: result.truncated },
        })
      })

    const inspect = answer.inspect_step ?? { status: 'DONE' as const, detail: 'No issues found' }
    step('inspect', inspect.status, inspect.detail, STEP_MS.inspect)

    // What `present` was sent depends on the policy: rows under SAMPLE/FULL,
    // only their shape under AGGREGATE — the same rule the narrative follows.
    const shared = ['SAMPLE', 'FULL'].includes(connection.disclosure_policy)
      ? tokens(JSON.stringify(result.rows.slice(0, 50)).length)
      : 40
    step('present', 'DONE', 'Answer written', STEP_MS.present,
      { prompt: 380 + tokens(answer.attempts.at(-1)!.raw_sql.length) + shared, completion: tokens(answer.answer.length) },
      (start, end) => stream(answer.answer, start, end))

    const verdict = answer.chart_step ?? { status: 'SKIPPED' as const, detail: 'Nothing chartable' }
    const chartType = /^(\w+) chart/.exec(verdict.detail)?.[1]
    step('chart', verdict.status, verdict.detail, STEP_MS.chart,
      verdict.status === 'DONE' && verdict.detail !== 'big number' ? { prompt: 520, completion: 58 } : undefined,
      (_start, end) => {
        if (verdict.status === 'DONE') {
          events.push({
            at: end - 1, type: 'ARTIFACT_CREATED',
            data: answer.kpi ? { kind: 'KPI' } : { kind: 'CHART', chart_type: chartType, source: /\((\w+)\)/.exec(verdict.detail)?.[1] },
          })
        }
      })
  }

  const repairs = Math.max(0, answer.attempts.length - 1)
  const total = t + 60
  events.push({
    at: total,
    type: 'RUN_FINISHED',
    data: { status: 'SUCCEEDED', error_code: null, repair_count: repairs, total_latency_ms: total },
  })
  events.sort((a, b) => a.at - b.at)
  return { events, steps, total, repairs, promptTokens: prompt, completionTokens: completion }
}

/**
 * The demo speaking, not the product: no steps, because nothing ran, and no
 * run on the answer, so no badge claims it was generated against anything.
 */
export function scriptFallback(text: string): Timeline {
  const events: TimedEvent[] = []
  const parts = chunks(text, TEXT_CHUNK_CHARS * 2)
  parts.forEach((part, i) => {
    events.push({ at: RUN_START_MS + i * FALLBACK_TICK_MS, type: 'TEXT_DELTA', data: { text: part } })
  })
  const total = RUN_START_MS + parts.length * FALLBACK_TICK_MS + 40
  events.push({ at: total, type: 'RUN_FINISHED', data: { status: 'SUCCEEDED', error_code: null, repair_count: 0, total_latency_ms: total } })
  return { events, steps: [], total, repairs: 0, promptTokens: null, completionTokens: null }
}

/** The steps as they stood `elapsed` ms in — what a stopped run keeps. */
export function stepsAt(timeline: Timeline, elapsed: number): RunStep[] {
  const out: RunStep[] = []
  for (const event of timeline.events) {
    if (event.at > elapsed) break
    if (event.type === 'STEP_STARTED') {
      const seq = event.data.seq as number
      const recorded = timeline.steps.find((s) => s.seq === seq)!
      out.push({ seq, name: recorded.name, status: 'RUNNING', detail: null, duration_ms: null })
    } else if (event.type === 'STEP_FINISHED') {
      const seq = event.data.seq as number
      const index = out.findIndex((s) => s.seq === seq)
      out[index] = timeline.steps.find((s) => s.seq === seq)!
    }
  }
  return out
}

/**
 * Play a timeline from `startedAt` onwards.
 *
 * Events already due are delivered at once — the replay a late reader gets —
 * and the rest on their own schedule. `stopAt` is a cancellation: the stream
 * ends there with `RUN_FINISHED { status: CANCELLED }`, which is what the
 * real run writes when *Stop* is pressed.
 */
export function play(
  timeline: Timeline,
  startedAt: number,
  now: () => number,
  handlers: { onEvent: (event: RunEvent) => void; onDone: () => void },
  cancelledAt: () => number | null,
): () => void {
  let stopped = false
  let index = 0
  let timer: number | undefined
  let seq = 0

  const emit = (type: string, data: Record<string, unknown>) => {
    seq += 1
    handlers.onEvent({ seq, type, data })
  }

  const tick = () => {
    if (stopped) return
    const elapsed = now() - startedAt
    const cancel = cancelledAt()
    while (index < timeline.events.length) {
      const event = timeline.events[index]
      if (cancel !== null && event.at > cancel - startedAt) {
        emit('RUN_FINISHED', { status: 'CANCELLED' })
        stopped = true
        handlers.onDone()
        return
      }
      if (event.at > elapsed) break
      index += 1
      emit(event.type, event.data)
      if (event.type === 'RUN_FINISHED') {
        stopped = true
        handlers.onDone()
        return
      }
    }
    const next = timeline.events[index]
    const wait = next ? Math.max(0, next.at - (now() - startedAt)) : 0
    // Wake at the next event or soon enough to notice a Stop, whichever first.
    timer = window.setTimeout(tick, Math.min(wait, 120))
  }

  timer = window.setTimeout(tick, 0)
  return () => {
    stopped = true
    if (timer !== undefined) window.clearTimeout(timer)
  }
}

export type { RunDetail }
