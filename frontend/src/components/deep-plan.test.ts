/**
 * `npm run test:deep` — the plan panel's arithmetic, with no DOM.
 *
 * Plain assertions under `node --experimental-strip-types`, like every other
 * suite here. What it pins is what a reader could not check by looking: which
 * step is running, that a revision is shown as one, that a stopped analysis
 * says why, and that a footnote is never attached to the wrong sentence.
 */
import {
  answerSpans,
  applyDeepEvent,
  budgetLine,
  canAnswerNow,
  emptyDeep,
  fromAnalysis,
  stepRows,
  stopSentence,
  type DeepView,
} from './deep-plan.ts'

let failures = 0
let counter = 0
function check(actual: unknown, expected: unknown, name = ''): void {
  counter += 1
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures += 1
  const label = name || `check ${counter}`
  console.log(
    ok
      ? `ok    ${label}`
      : `FAIL  ${label}\n        got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`,
  )
}
// `node:assert` needs `@types/node`, which is not a dependency here; the
// suites share this shape instead.
const assert = { equal: check, deepEqual: check }

const step = (question: string, depends_on: number[] = []) => ({
  question, intent: 'CONFIRM', why: '', tool: 'SQL', depends_on,
})
const PLAN = {
  restatement: 'Why revenue fell in March.',
  stop_when: 'the driver is named',
  steps: [step('Feb vs Mar?'), step('By region?', [0]), step('Drill in?', [1])],
}
const found = (index: number, status = 'DONE') => ({
  index, question: PLAN.steps[index].question, status, row_count: 3, sql: 'SELECT 1',
})

function run(events: Array<[string, Record<string, unknown>]>): DeepView {
  return events.reduce((view, [type, data]) => applyDeepEvent(view, type, data), emptyDeep())
}

// ── the fold ──────────────────────────────────────────────────────────────
{
  const view = run([
    ['PLAN_PROPOSED', PLAN],
    ['STEP_EVIDENCE', found(0)],
    ['STEP_EVIDENCE', found(0)], // a replay is one step, not two
  ])
  assert.equal(view.plan?.steps.length, 3)
  assert.equal(view.steps.length, 1)
}

{
  const unrelated = emptyDeep()
  assert.equal(applyDeepEvent(unrelated, 'TEXT_DELTA', { text: 'x' }) === unrelated, true,
    'a non-deep event returns the same view')
}

// ── which step is running ─────────────────────────────────────────────────
{
  const view = run([['PLAN_PROPOSED', PLAN], ['STEP_EVIDENCE', found(0)]])
  assert.deepEqual(stepRows(view, true).map((r) => r.state), ['done', 'running', 'pending'])
  // Finished (Answer now after step one): nothing is pending, the rest never ran.
  assert.deepEqual(stepRows(view, false).map((r) => r.state), ['done', 'not-run', 'not-run'])
}

{
  const view = run([
    ['PLAN_PROPOSED', PLAN],
    ['STEP_EVIDENCE', found(0, 'FAILED')],
    ['STEP_EVIDENCE', found(1, 'SKIPPED')],
  ])
  assert.deepEqual(stepRows(view, true).map((r) => r.state), ['failed', 'skipped', 'running'])
}

// ── a revision is shown as a revision ─────────────────────────────────────
{
  const view = run([
    ['PLAN_PROPOSED', PLAN],
    ['STEP_EVIDENCE', found(0)],
    ['STEP_EVIDENCE', found(1)],
    ['PLAN_REVISED', { index: 2, replaced: PLAN.steps[2], by: step('Products in EMEA?', [1]) }],
  ])
  const rows = stepRows(view, true)
  assert.equal(rows[2].step.question, 'Products in EMEA?')
  assert.deepEqual(rows[2].replaced.map((s) => s.question), ['Drill in?'])
  assert.equal(view.plan?.steps.length, 3, 'a revision never adds a step')
}

// ── answer now ────────────────────────────────────────────────────────────
{
  const view = run([['PLAN_PROPOSED', PLAN]])
  assert.equal(canAnswerNow(emptyDeep(), true), false, 'nothing to answer from before a plan')
  assert.equal(canAnswerNow(view, true), true)
  assert.equal(canAnswerNow(view, false), false)
  assert.equal(canAnswerNow({ ...view, answerNowRequested: true }, true), false)
}

// ── why it stopped ────────────────────────────────────────────────────────
{
  const partial = fromAnalysis({
    plan: PLAN, steps: [found(0), found(1), found(2)].slice(0, 2), stop_reason: 'answer_now',
  })
  assert.equal(stopSentence(partial), 'Stopped after 2 of 3 steps: you asked for an answer now.')
  assert.equal(partial.answerNowRequested, true)

  const complete = fromAnalysis({ plan: PLAN, steps: [found(0), found(1), found(2)] })
  assert.equal(stopSentence(complete), '')
}

{
  assert.equal(budgetLine(null), '')
  assert.equal(
    budgetLine({ steps: 2, max_steps: 5, queries: 3, max_queries: 12, rows: 12500,
                 max_rows: 20000, prompt_tokens: 1, max_prompt_tokens: 2 }),
    '2 of 5 steps · 3 of 12 queries · 12,500 rows',
  )
}

// ── footnotes ─────────────────────────────────────────────────────────────
{
  const claims = [
    { text: 'Revenue fell 12%.', cites: 1 },
    { text: 'EMEA drove 90% of it.', cites: 2, unsupported: [{ value: 90 }] },
  ]
  const preface = 'This answer is built from 2 of 3 planned steps: you asked for an answer now.'
  const spans = answerSpans(`${preface}\n\nRevenue fell 12%. EMEA drove 90% of it.`, claims)
  assert.deepEqual(spans.map((s) => s.cites), [null, 1, 2])
  assert.equal(spans[0].text, preface)
  assert.equal(spans[0].lead, true)
  assert.equal(spans[2].unsupported, true)

  // No preface: exactly the claims.
  assert.deepEqual(
    answerSpans('Revenue fell 12%. EMEA drove 90% of it.', claims).map((s) => s.cites), [1, 2],
  )

  // Edited, or not the text the claims came from: whole and uncited, never
  // a footnote on the wrong sentence.
  const edited = answerSpans('Revenue fell sharply. EMEA drove most of it.', claims)
  assert.deepEqual(edited, [{
    text: 'Revenue fell sharply. EMEA drove most of it.', cites: null, unsupported: false,
  }])
}

console.log(failures === 0 ? '\nall passed' : `\n${failures} failed`)
// `throw`, not `process.exit`, for the reason every other suite gives.
if (failures > 0) throw new Error(`${failures} test(s) failed`)
