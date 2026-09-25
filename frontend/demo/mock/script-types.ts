/**
 * The shapes `scripts/build.py` writes into `fixtures/answers.generated.ts`
 * and `fixtures/deep.generated.ts`.
 *
 * Everything that reaches the UI from here is typed against the real
 * `src/api/types` shapes, so a fixture that drifts from what the API serves is
 * a compile error in `demo/tsconfig.json`, not a blank panel in a browser.
 */
import type {
  ChartOption, GeneratedQuery, KpiSpec, TableArtifactSpec, TemplateCheckResult,
} from '../../src/api/types'
import type { DeepClaim, PlanView, Revision, StepFound } from '../../src/components/deep-plan'

export type DemoConnectionKey = 'sales' | 'sakila'

/**
 * The guard's report as the API serves it: `GeneratedQuery['validation_report']`
 * plus what the SPA does not read (`referenced_columns`, an issue's `node_sql`).
 * Declared open rather than stripped, because the fixture is the backend's own
 * output and the demo should not quietly edit it.
 */
type Issue = NonNullable<GeneratedQuery['validation_report']['issues']>[number]
export type WireReport = Omit<GeneratedQuery['validation_report'], 'issues'> & {
  // `hint` is `str | None` on the wire: a warning with nothing to suggest sends null.
  issues?: (Omit<Issue, 'hint'> & { hint?: string | null } & Record<string, unknown>)[]
} & Record<string, unknown>

export interface ScriptedAnswer {
  id: string
  connection: DemoConnectionKey
  /** The question as the chip and the sidebar show it. */
  question: string
  /** Other phrasings that count as this question, compared normalised. */
  aliases: string[]
  intent: 'ANALYTICAL' | 'METADATA'
  /** Question ids offered as follow-up chips once this answer lands. */
  followups: string[]
  /** What this answer demonstrates, for the guide. Empty for the minor ones. */
  shows: string
  /** Already asked in one of the sidebar's conversations. */
  history: boolean
  /** The narrative, written from the rows below by the build. */
  answer: string
  /** Every draft, in order: rejected ones first, the one that ran last. */
  attempts: (Omit<GeneratedQuery, 'validation_report'> & {
    rewritten_sql: string | null
    validation_status: 'VALID' | 'REJECTED'
    validation_report: WireReport
  })[]
  result: (TableArtifactSpec & {
    /** What the database reported for the statement itself. */
    duration_ms: number
    rows_scanned_estimate: number | null
  }) | null
  /** The compiled Vega-Lite spec, or null when no chart was drawn. */
  chart: Record<string, unknown> | null
  kpi: KpiSpec | null
  /** The `chart` step's verdict, as the real node words it. */
  chart_step?: { status: 'DONE' | 'SKIPPED'; detail: string }
  /** The `inspect` step's verdict. */
  inspect_step?: { status: 'DONE' | 'SKIPPED'; detail: string }
  findings?: { code: string; message: string; hint?: string | null; retry?: boolean }[]
  /** Every chart type's verdict for the picker. */
  options: ChartOption[]
  /** What *Change chart* answers for each type, precomputed by the real compiler. */
  redraws: Record<string, { spec: Record<string, unknown> | null; chart_type: string; reason: string | null }>
  /**
   * `POST …/templates/check` for this answer's statement, for every set of
   * literals the editor can tick — keyed by the sorted names, `''` for none.
   */
  template_check?: { sql: string; question: string; answers: Record<string, TemplateCheckResult> }
}

/** What the `chart` node makes of one result, and every *Change chart* answer. */
export type ScriptedChart = Pick<ScriptedAnswer, 'chart' | 'kpi' | 'chart_step' | 'options' | 'redraws'>

/**
 * One step of a scripted deep analysis, as `run_deep` recorded it.
 *
 * `evidence` is `pipeline/evidence.step_payload` verbatim — the backend's own
 * computed summary included — so the `STEP_EVIDENCE` event and the `ANALYSIS`
 * artifact carry exactly what a real run would. The chart fields are what the
 * `chart` node makes of this step's result if it is the last to run: the whole
 * plan's last step, or the one in flight when *Answer now* was pressed.
 */
export interface ScriptedDeepStep extends ScriptedChart {
  evidence: StepFound & { intent: string; tool: string; why: string }
  attempt: ScriptedAnswer['attempts'][number]
  result: NonNullable<ScriptedAnswer['result']>
  inspect_step: { status: 'DONE' | 'SKIPPED'; detail: string }
}

/** The answer written from the first `steps` steps, checked by `check_claims`. */
export interface ScriptedDeepAnswer {
  steps: number
  /** The writer's prose as it streamed, citation markers and all. */
  streamed: string
  /** The prose as stored: the markers lifted out into `claims`. */
  answer: string
  /** `check_claims`' own record: each sentence, the step it cites, its figures. */
  claims: (DeepClaim & { figures: number[] })[]
  traceable: number | null
}

/** What `scripts/build.py` writes into `fixtures/deep.generated.ts`. */
export interface ScriptedDeep {
  id: string
  connection: DemoConnectionKey
  question: string
  aliases: string[]
  followups: string[]
  shows: string
  /** The plan as `PLAN_PROPOSED` carries it: every step as first written. */
  plan: PlanView
  /** Steps sharpened once their dependencies answered, in order. */
  revisions: Revision[]
  steps: ScriptedDeepStep[]
  /** One per prefix of the plan: `answers[k - 1]` is written from `k` steps. */
  answers: ScriptedDeepAnswer[]
}
