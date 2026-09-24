/**
 * A deep analysis, as the plan panel reads it — from the live events or from
 * the finished run's `ANALYSIS` artifact, into one shape.
 *
 * DOM-free and React-free, like the other modules whose failures are quiet
 * (`npm run test:deep`). What lives here is exactly the part a reader cannot
 * check by looking: which step is running, which one was replaced, whether the
 * answer's footnotes still line up with its sentences, and what sentence says
 * why the analysis stopped short.
 *
 * `applyDeepEvent` is the same fold `backend/app/services/deep_plan.py` runs
 * over the durable events for a reader arriving late — a step's evidence is
 * keyed by its index, a revision replaces the step at its index and is kept —
 * so a reader who watched and a reader who reloaded see one plan.
 */

export interface PlanStepView {
  question: string
  intent: string
  why: string
  tool: string
  depends_on: number[]
}

export interface PlanView {
  restatement: string
  steps: PlanStepView[]
  stop_when: string
}

export interface StepComputed {
  tool: string
  ok: boolean
  summary: string
  refusal: string | null
}

/** What one step found, as `STEP_EVIDENCE` and the artifact both carry it. */
export interface StepFound {
  index: number
  question: string
  intent?: string
  tool?: string
  status: 'DONE' | 'FAILED' | 'SKIPPED'
  note?: string
  row_count?: number
  truncated?: boolean
  sql?: string | null
  attempts?: number
  computed?: StepComputed | null
}

export interface Revision {
  index: number
  replaced: PlanStepView
  by: PlanStepView
}

export interface Budget {
  steps: number
  max_steps: number
  queries: number
  max_queries: number
  rows: number
  max_rows: number
  prompt_tokens: number
  max_prompt_tokens: number
}

export interface DeepClaim {
  text: string
  cites: number | null
  unsupported?: unknown[]
}

export interface DeepView {
  plan: PlanView | null
  revisions: Revision[]
  steps: StepFound[]
  budget: Budget | null
  stopReason: string
  claims: DeepClaim[]
  traceable: number | null
  /** The reader pressed *Answer now*; the button says so until the run ends. */
  answerNowRequested: boolean
}

export function emptyDeep(): DeepView {
  return {
    plan: null, revisions: [], steps: [], budget: null, stopReason: '',
    claims: [], traceable: null, answerNowRequested: false,
  }
}

type Data = Record<string, unknown>

function planOf(data: Data): PlanView {
  return {
    restatement: String(data.restatement ?? ''),
    steps: Array.isArray(data.steps) ? (data.steps as PlanStepView[]) : [],
    stop_when: String(data.stop_when ?? ''),
  }
}

function withStep(steps: readonly StepFound[], found: StepFound): StepFound[] {
  return [...steps.filter((s) => s.index !== found.index), found]
    .sort((a, b) => a.index - b.index)
}

/**
 * One event into the view. Anything that is not one of the four deep events
 * returns the view unchanged — the same object — so a caller can hand it every
 * event of a run without asking which kind it is.
 */
export function applyDeepEvent(view: DeepView, type: string, data: Data): DeepView {
  switch (type) {
    case 'PLAN_PROPOSED':
      return { ...view, plan: planOf(data) }
    case 'PLAN_REVISED': {
      const revision = data as unknown as Revision
      if (!view.plan) return view
      const steps = view.plan.steps.map((s, i) => (i === revision.index ? revision.by : s))
      return {
        ...view,
        plan: { ...view.plan, steps },
        revisions: [...view.revisions, revision],
      }
    }
    case 'STEP_EVIDENCE':
      return { ...view, steps: withStep(view.steps, data as unknown as StepFound) }
    case 'BUDGET_SPENT':
      return { ...view, budget: data as unknown as Budget }
    default:
      return view
  }
}

/** A finished run's `ANALYSIS` artifact, into the same view. */
export function fromAnalysis(spec: Data): DeepView {
  return {
    plan: spec.plan ? planOf(spec.plan as Data) : null,
    revisions: (spec.revisions as Revision[] | undefined) ?? [],
    steps: [...((spec.steps as StepFound[] | undefined) ?? [])].sort((a, b) => a.index - b.index),
    budget: (spec.budget as Budget | undefined) ?? null,
    stopReason: String(spec.stop_reason ?? ''),
    claims: (spec.claims as DeepClaim[] | undefined) ?? [],
    traceable: typeof spec.traceable === 'number' ? spec.traceable : null,
    answerNowRequested: spec.stop_reason === 'answer_now',
  }
}

// ── the rows the panel draws ───────────────────────────────────────────────
export type RowState = 'done' | 'failed' | 'skipped' | 'running' | 'pending' | 'not-run'

export interface StepRow {
  index: number
  step: PlanStepView
  state: RowState
  found: StepFound | null
  /** Earlier wordings of this step, oldest first — drawn struck through. */
  replaced: PlanStepView[]
}

/**
 * One row per planned step, in order, with the state the reader should see.
 *
 * **Running** is the first step with no evidence while the run is in flight —
 * the loop is strictly in order, so there is exactly one. **Not run** is a
 * step the analysis never reached because it stopped early (a budget, or
 * *Answer now*); it is distinct from **pending**, which only exists while the
 * run is still going, because a finished analysis has nothing pending.
 */
export function stepRows(view: DeepView, inFlight: boolean): StepRow[] {
  if (!view.plan) return []
  const found = new Map(view.steps.map((s) => [s.index, s]))
  let running = inFlight
  return view.plan.steps.map((step, index) => {
    const evidence = found.get(index) ?? null
    let state: RowState
    if (evidence) {
      state = evidence.status === 'DONE' ? 'done'
        : evidence.status === 'SKIPPED' ? 'skipped' : 'failed'
    } else if (running) {
      state = 'running'
      running = false
    } else {
      state = inFlight ? 'pending' : 'not-run'
    }
    return {
      index,
      step,
      state,
      found: evidence,
      replaced: view.revisions.filter((r) => r.index === index).map((r) => r.replaced),
    }
  })
}

/** Whether *Answer now* can still do anything. */
export function canAnswerNow(view: DeepView, inFlight: boolean): boolean {
  return inFlight && !view.answerNowRequested && view.plan !== null
}

const STOPPED: Record<string, string> = {
  steps: 'it reached the step limit',
  queries: 'it reached the query limit',
  rows: 'it reached the row limit',
  tokens: 'it reached the token limit',
  time: 'it ran out of time',
  answer_now: 'you asked for an answer now',
}

/**
 * Why the answer stands on part of the plan, or `''` when it does not. The
 * backend writes the same sentence at the top of the answer; this is the
 * panel's copy, for a reader looking at the plan rather than the prose.
 */
export function stopSentence(view: DeepView): string {
  const planned = view.plan?.steps.length ?? 0
  if (!view.stopReason && view.steps.length >= planned) return ''
  const why = STOPPED[view.stopReason] ?? 'it could not continue'
  return `Stopped after ${view.steps.length} of ${planned} steps: ${why}.`
}

function thousands(n: number): string {
  return n.toLocaleString('en-US')
}

/** The budget as one quiet line: what is spent, against what is allowed. */
export function budgetLine(budget: Budget | null): string {
  if (!budget) return ''
  return [
    `${budget.steps} of ${budget.max_steps} steps`,
    `${budget.queries} of ${budget.max_queries} queries`,
    `${thousands(budget.rows)} rows`,
  ].join(' · ')
}

export const INTENT_LABEL: Record<string, string> = {
  CONFIRM: 'Confirm',
  DECOMPOSE: 'Break down',
  COMPARE: 'Compare',
  DRILL: 'Drill in',
  CHECK: 'Rule out',
}

export const TOOL_LABEL: Record<string, string> = {
  SQL: 'Query',
  CONTRIBUTION: 'What drove it',
  COMPARE_PERIODS: 'Period over period',
  OUTLIERS: 'What stands out',
}

// ── the answer's footnotes ─────────────────────────────────────────────────
export interface AnswerSpan {
  text: string
  /** The 1-based step this sentence was drawn from; null for none. */
  cites: number | null
  unsupported: boolean
  /** The product's own opening sentence — a paragraph of its own, no claim. */
  lead?: boolean
}

/**
 * The answer as sentences, each with the step it cites.
 *
 * The answer may open with a sentence the *product* wrote — "This answer is
 * built from 3 of 5 planned steps…" — which no claim covers, so the claims
 * are matched against the **end** of the answer and whatever precedes them is
 * one uncited span. Whitespace-insensitive, as `claimSpans` in
 * `report-document.ts` is and for its reason; and where the claims do not
 * reassemble into the answer at all, the answer comes back whole and uncited
 * rather than with footnotes attached to the wrong sentences.
 */
export function answerSpans(answer: string, claims: readonly DeepClaim[]): AnswerSpan[] {
  const whole: AnswerSpan[] = [{ text: answer, cites: null, unsupported: false }]
  if (claims.length === 0) return whole
  const flat = (text: string) => text.replace(/\s+/g, ' ').trim()
  const joined = flat(claims.map((c) => c.text).join(' '))
  const all = flat(answer)
  if (!all.endsWith(joined)) return whole
  const lead = all.slice(0, all.length - joined.length).trim()
  const spans: AnswerSpan[] = claims.map((c) => ({
    text: c.text,
    cites: c.cites,
    unsupported: (c.unsupported ?? []).length > 0,
  }))
  return lead ? [{ text: lead, cites: null, unsupported: false, lead: true }, ...spans] : spans
}
