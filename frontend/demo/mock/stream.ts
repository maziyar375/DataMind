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
import type { Revision } from '../../src/components/deep-plan'
import type { ScriptedAnswer, ScriptedChart, ScriptedDeep } from './script-types'
import { SNAPSHOTS } from './fixtures/schema.generated'
import { DEEP_LIMITS } from './fixtures/world'
import {
  DEEP_MS, FALLBACK_TICK_MS, RUN_START_MS, STEP_MS, TEXT_CHUNK_CHARS, TEXT_TICK_MS,
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

/**
 * The pen a timeline is written with. `step` appends one node's
 * `STEP_STARTED`/`STEP_FINISHED` pair at the running clock and records the
 * step as the finished run keeps it; `stream` spreads prose over a node as
 * `TEXT_DELTA`s; `finish` closes the run. A chat run and a deep run are both
 * written with it, so their trails cannot drift apart in how a step is timed.
 */
function recorder(runId: string, model: LlmConfig, connection: Connection) {
  const events: TimedEvent[] = [{
    at: 0,
    type: 'RUN_STARTED',
    data: { run_id: runId, model: model.model, connection: connection.name },
  }]
  const steps: RunStep[] = []
  const spent = { prompt: 0, completion: 0 }
  let t = RUN_START_MS
  let seq = 0

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
      spent.prompt += usage.prompt
      spent.completion += usage.completion
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

  function finish(repairs: number): Timeline {
    const total = t + 60
    events.push({
      at: total,
      type: 'RUN_FINISHED',
      data: { status: 'SUCCEEDED', error_code: null, repair_count: repairs, total_latency_ms: total },
    })
    events.sort((a, b) => a.at - b.at)
    return { events, steps, total, repairs, promptTokens: spent.prompt, completionTokens: spent.completion }
  }

  return { events, spent, step, stream, finish, now: () => t }
}

/** The `chart` node over the result that was charted: its verdict, and the artifact it made. */
function chartNode(step: Pen['step'], events: TimedEvent[], charted: ScriptedChart | null) {
  const verdict = charted?.chart_step ?? { status: 'SKIPPED' as const, detail: 'Nothing chartable' }
  const chartType = /^(\w+) chart/.exec(verdict.detail)?.[1]
  step('chart', verdict.status, verdict.detail, STEP_MS.chart,
    verdict.status === 'DONE' && verdict.detail !== 'big number' ? { prompt: 520, completion: 58 } : undefined,
    (_start, end) => {
      if (verdict.status === 'DONE') {
        events.push({
          at: end - 1, type: 'ARTIFACT_CREATED',
          data: charted?.kpi ? { kind: 'KPI' } : { kind: 'CHART', chart_type: chartType, source: /\((\w+)\)/.exec(verdict.detail)?.[1] },
        })
      }
    })
}

type Pen = ReturnType<typeof recorder>

export function scriptRun(
  answer: ScriptedAnswer,
  connection: Connection,
  model: LlmConfig,
  runId: string,
): Timeline {
  const key = answer.connection
  const { events, step, stream, finish } = recorder(runId, model, connection)
  const schema = tokens(schemaChars(key))
  const tableCount = SNAPSHOTS[key].tables.length

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

    chartNode(step, events, answer)
  }

  return finish(Math.max(0, answer.attempts.length - 1))
}

/** Why a deep run stopped short of its plan. '' is "it did not". */
export type DeepStop = '' | 'answer_now'

/** `nodes/deep.py`'s `_STOPPED`, for the one ending the demo can reach. */
const STOPPED: Record<Exclude<DeepStop, ''>, string> = {
  answer_now: 'you asked for an answer now',
}

export interface DeepTimeline extends Timeline {
  /** When each step that ran started, ms into the run — what *Answer now* is measured against. */
  stepStarts: number[]
  /** How many of the planned steps ran. */
  ran: number
  stop: DeepStop
  /** The answer as stored: the product's preface, if any, then the writer's clean prose. */
  answer: string
  /** The `ANALYSIS` artifact `nodes/deep.analysis_record` writes when the run ends. */
  analysis: Record<string, unknown>
}

/** `BUDGET_SPENT`, as `nodes/deep._spent` words it. One statement per step: no step here repairs. */
function spentOf(steps: number, rows: number, prompt: number) {
  return {
    steps, max_steps: DEEP_LIMITS.max_steps,
    queries: steps, max_queries: DEEP_LIMITS.max_queries,
    rows, max_rows: DEEP_LIMITS.max_rows_total,
    prompt_tokens: prompt, max_prompt_tokens: DEEP_LIMITS.max_prompt_tokens,
  }
}

/**
 * A deep analysis, played the way the deep graph runs one:
 *
 *   route → plan → [ step → scope → retrieve → generate → validate → execute
 *                    → inspect → compute ] × steps → synthesize → chart
 *
 * with `PLAN_PROPOSED` at the end of `plan`, `PLAN_REVISED` inside the `step`
 * that sharpens a step, and `STEP_EVIDENCE` + `BUDGET_SPENT` at the end of
 * each `compute` — the payloads recorded by `build.py`'s `run_deep`.
 *
 * `ran` is how many steps run: all of them, or fewer after *Answer now*. The
 * step in flight when it was pressed still finishes, then `synthesize`
 * writes from what was found, opening with the product's own sentence saying
 * how much of the plan the answer stands on (`nodes/deep._preface`).
 */
export function scriptDeep(
  deep: ScriptedDeep,
  connection: Connection,
  model: LlmConfig,
  runId: string,
  ran: number = deep.steps.length,
): DeepTimeline {
  const key = deep.connection
  const { events, spent, step, stream, finish, now } = recorder(runId, model, connection)
  const schema = tokens(schemaChars(key))
  const tableCount = SNAPSHOTS[key].tables.length
  const planned = deep.plan.steps.length
  const stop: DeepStop = ran < planned ? 'answer_now' : ''

  step('route', 'DONE', `Classified ANALYTICAL in ${STEP_MS.route}ms`, STEP_MS.route,
    { prompt: 104, completion: 2 })
  step('plan', 'DONE', `${planned} steps planned`, DEEP_MS.plan,
    { prompt: schema + 1150, completion: tokens(JSON.stringify(deep.plan).length) },
    (_start, end) => events.push({
      at: end - 2, type: 'PLAN_PROPOSED', data: { ...deep.plan, max_steps: DEEP_LIMITS.max_steps },
    }))

  const stepStarts: number[] = []
  const current = [...deep.plan.steps]
  const revisions: Revision[] = []
  let rows = 0
  for (let i = 0; i < ran; i++) {
    const scripted = deep.steps[i]
    const revision = deep.revisions.find((r) => r.index === i) ?? null
    const depends = deep.plan.steps[i].depends_on
    const question = (revision?.by ?? deep.plan.steps[i]).question
    // The reviser reads what the dependencies found, disclosed.
    const findings = depends.reduce(
      (n, d) => n + tokens(JSON.stringify(deep.steps[d].result.rows.slice(0, 50)).length), 0)
    stepStarts.push(now())
    step('step', 'DONE',
      `Step ${i + 1} of ${planned}${revision ? ' (revised)' : ''}: ${question.slice(0, 160)}`,
      depends.length ? DEEP_MS.revise : DEEP_MS.step,
      depends.length ? { prompt: 620 + findings, completion: revision ? 74 : 14 } : undefined,
      (_start, end) => {
        if (!revision) return
        events.push({ at: end - 2, type: 'PLAN_REVISED', data: { ...revision } })
        revisions.push(revision)
        current[i] = revision.by
      })
    step('scope', 'SKIPPED', null, STEP_MS.scope)
    step('retrieve', 'DONE', `${tableCount} tables via FULL_SNAPSHOT`, STEP_MS.retrieve)

    const attempt = scripted.attempt
    step('generate', 'DONE', `Attempt ${attempt.attempt_no} drafted`, STEP_MS.generate,
      { prompt: schema + 540 + tokens(question.length), completion: tokens(attempt.raw_sql.length) + 24 },
      (_start, end) => events.push({
        at: end - 2, type: 'SQL_GENERATED', data: { attempt_no: attempt.attempt_no, sql: attempt.raw_sql },
      }))
    step('validate', 'DONE', `Valid · ${attempt.referenced_tables.length} tables`, STEP_MS.validate,
      undefined, (_start, end) => events.push({
        at: end - 1, type: 'SQL_VALIDATED',
        data: {
          attempt_no: attempt.attempt_no, sql: attempt.rewritten_sql,
          referenced_tables: attempt.referenced_tables,
          limit_applied: attempt.validation_report.limit_applied ?? null,
        },
      }))

    const result = scripted.result
    step('execute', 'DONE', `${result.row_count} rows in ${result.duration_ms}ms`,
      result.duration_ms + STEP_MS.executeOverhead, undefined, (_start, end) => {
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
    step('inspect', scripted.inspect_step.status, scripted.inspect_step.detail, STEP_MS.inspect)

    rows += result.row_count
    const found = scripted.evidence
    const computed = found.computed
    step('compute', 'DONE',
      `Step ${i + 1}: ${found.row_count} rows`
        + (computed
          ? ` · ${computed.tool.toLowerCase().replace(/_/g, ' ')} ${computed.ok ? 'computed' : `refused (${computed.refusal})`}`
          : ''),
      DEEP_MS.compute, undefined, (_start, end) => {
        events.push({ at: end - 2, type: 'STEP_EVIDENCE', data: { ...found } })
        events.push({ at: end - 1, type: 'BUDGET_SPENT', data: spentOf(i + 1, rows, spent.prompt) })
      })
  }

  const preface = stop
    ? `This answer is built from ${ran} of ${planned} planned steps: the analysis stopped early because ${STOPPED[stop]}.`
    : ''
  const written = ran > 0 ? deep.answers[ran - 1] : null
  // `_plain`, for the one road here with nothing to write from.
  const body = written ? written.answer : 'No step of the analysis produced a result.'
  const answer = `${preface}\n\n${body}`.trim()
  const narrated = deep.steps.slice(0, ran)
    .reduce((n, s) => n + 120 + tokens(JSON.stringify(s.result.rows.slice(0, 50)).length), 0)
  step('synthesize', 'DONE',
    written
      ? `${ran} of ${planned} steps · ${written.claims.length} claims`
        + (written.traceable === null ? '' : ` · ${Math.round(written.traceable * 100)}% traceable`)
      : `0 of ${planned} steps · written without a model (nothing to write from)`,
    written ? DEEP_MS.synthesize : DEEP_MS.compute,
    written ? { prompt: 900 + narrated, completion: tokens(written.streamed.length) } : undefined,
    (start, end) => {
      if (preface) events.push({ at: start + 40, type: 'TEXT_DELTA', data: { text: `${preface}\n\n` } })
      if (!written) {
        events.push({ at: start + 60, type: 'TEXT_DELTA', data: { text: body } })
        return
      }
      // The markers stream as the writer wrote them; the stored answer has
      // them lifted out, so the live text is replaced with it at the end.
      stream(written.streamed, start + (preface ? 60 : 0), end - 20)
      events.push({ at: end - 3, type: 'TEXT_RESET', data: { reason: 'citations' } })
      events.push({ at: end - 2, type: 'TEXT_DELTA', data: { text: answer } })
    })

  const charted = ran > 0 ? deep.steps[ran - 1] : null
  chartNode(step, events, charted)

  const timeline = finish(0)
  return {
    ...timeline,
    stepStarts,
    ran,
    stop,
    answer,
    analysis: {
      plan: { ...deep.plan, steps: current },
      revisions,
      steps: deep.steps.slice(0, ran).map((s) => s.evidence),
      stop_reason: stop,
      claims: written?.claims ?? [],
      traceable: written?.traceable ?? null,
      budget: spentOf(ran, rows, timeline.promptTokens ?? 0),
    },
  }
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
