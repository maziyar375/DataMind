/**
 * What the Generate button is allowed to say about an outline.
 *
 * `npm run test:readiness` — Node runs this file directly (type stripping, no
 * bundler, no test framework, no new dependency), the same arrangement
 * `report-document.test.ts` uses, and browser globals only for the same
 * reason: `@types/node` is not a dependency of this project.
 *
 * Every case below is a state a user reaches by doing something ordinary —
 * rewording a question after the sweep, deleting the last question out of a
 * section, accepting a window that has no rows in it yet — and each asserts the
 * thing the run really does with it. The one that matters most is the silent
 * one: a hand-written statement kept through a reword, which no chip in the
 * interface used to call anything, and which produces the previous question's
 * numbers under the new heading.
 */
import { preflightOf, readinessOf } from './report-readiness.ts'
import type { ReportBlock, ReportSection } from '../api/types.ts'

let failures = 0
function check(name: string, actual: unknown, expected: unknown): void {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures += 1
  console.log(
    ok
      ? `ok    ${name}`
      : `FAIL  ${name}\n        got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`,
  )
}

const AT = '2026-08-01T12:00:00Z'

/** A block as the guard left it: checked, and carrying the statement it validated. */
function block(id: string, extra: Partial<ReportBlock> = {}): ReportBlock {
  return {
    id,
    section_id: 's1',
    position: 0,
    question: 'revenue by month',
    title: '',
    sql: 'SELECT 1',
    sql_hash: 'abc',
    sql_origin: 'GENERATED',
    block_type: 'CHART',
    chart_config: null,
    time_window: 'none',
    feasibility_status: 'FEASIBLE',
    feasibility_reason: null,
    feasibility_checked_at: AT,
    max_rows: null,
    created_at: AT,
    updated_at: AT,
    ...extra,
  }
}

/** A block as it exists before anyone has checked it: a question and nothing else. */
function unchecked(id: string, extra: Partial<ReportBlock> = {}): ReportBlock {
  return block(id, {
    sql: '',
    sql_hash: '',
    feasibility_status: 'UNCHECKED',
    feasibility_checked_at: null,
    ...extra,
  })
}

function section(
  id: string, blocks: ReportBlock[], extra: Partial<ReportSection> = {},
): ReportSection {
  return {
    id,
    report_id: 'r1',
    position: 0,
    heading: 'Sales',
    intent: 'how sales moved',
    kind: 'NORMAL',
    created_at: AT,
    updated_at: AT,
    blocks: blocks.map((b) => ({ ...b, section_id: id })),
    ...extra,
  }
}

const kinds = (sections: ReportSection[]) => preflightOf(sections).problems.map((p) => p.kind)

// ── counting the outline ─────────────────────────────────────────────────
const mixed = [
  section('s1', [
    block('b1'),
    block('b2', { feasibility_status: 'EMPTY' }),
    block('b3', {
      feasibility_status: 'INFEASIBLE',
      sql: '',
      feasibility_reason: 'orders has no column named margin.',
    }),
    unchecked('b4'),
  ]),
]
const counted = readinessOf(mixed)
check('every verdict is counted', [counted.blocks, counted.ready, counted.empty], [4, 1, 1])
check('…including the two that stop a run being useful', [counted.infeasible, counted.unchecked], [1, 1])

// `create_run` refuses only when *no* block carries SQL. Claiming otherwise
// here is how a disabled button ends up disagreeing with the server.
check('one statement is enough for the API to accept a run', readinessOf([
  section('s1', [block('b1'), unchecked('b2')]),
]).runnable, true)
check('and none at all is the one thing it refuses', readinessOf([
  section('s1', [unchecked('b1'), unchecked('b2')]),
]).runnable, false)
check(
  'which it says in the words the panel will use',
  /has a query yet/.test(readinessOf([section('s1', [unchecked('b1')])]).blocked ?? ''),
  true,
)
// The other eleven still produce a document, and the preflight is where the
// hole in it gets described. A greyed-out button cannot say any of that.
check('a refused question does not block the run any more', readinessOf([
  section('s1', [block('b1'), block('b2', { feasibility_status: 'INFEASIBLE', sql: '' })]),
]).runnable, true)

// ── what generating now would produce ────────────────────────────────────
const clean = preflightOf([section('s1', [block('b1'), block('b2')])])
check('a checked outline has nothing to say', clean.problems, [])
check('…so Generate generates on the first click', [clean.clean, clean.canGenerate], [true, true])
check('and there is nothing for a sweep to do', clean.sweepable.length, 0)

const oneMissed = preflightOf([section('s1', [block('b1'), unchecked('b2')])])
check('an unchecked question is a problem of its own', kinds([
  section('s1', [block('b1'), unchecked('b2')]),
]), ['no-query'])
check('…named as the hole it becomes', /error message/.test(oneMissed.problems[0].detail), true)
check('…in red, and counted', [oneMissed.problems[0].tone, oneMissed.problems[0].count], ['red', 1])
check('…and it is exactly what the sweep should walk', oneMissed.sweepable.map((b) => b.id), ['b2'])
check('…while the API would still accept the run, which is why saying so matters',
  oneMissed.canGenerate, true)

// `update_block` keeps hand-written SQL through a reword and resets only the
// verdict — so this executes, and answers the previous question. Sweeping it
// would "fix" that by throwing the user's SQL away, which is not a decision a
// bulk button gets to make.
const stale = preflightOf([
  section('s1', [
    block('b1'),
    unchecked('b2', { sql: 'SELECT revenue FROM sales', sql_hash: 'def', sql_origin: 'HANDWRITTEN' }),
  ]),
])
check('a reworded hand-written statement is its own problem', stale.problems.map((p) => p.kind), ['stale-sql'])
check('…described as the wrong numbers it produces', /previous wording/.test(stale.problems[0].detail), true)
check('…and never swept in bulk', stale.sweepable.length, 0)

const refused = preflightOf([
  section('s1', [
    block('b1'),
    block('b2', {
      feasibility_status: 'INFEASIBLE',
      sql: '',
      sql_hash: '',
      feasibility_reason: 'orders has no column named margin.',
    }),
  ]),
])
check('a refusal is reported', refused.problems.map((p) => p.kind), ['infeasible'])
check('…carrying the guard’s own sentence', refused.problems[0].hint, 'orders has no column named margin.')
check('…and is not swept, because the same wording gets the same answer', refused.sweepable.length, 0)
check(
  'the categories are disjoint — a refusal is not also “never checked”',
  refused.problems.reduce((sum, p) => sum + p.count, 0),
  1,
)

const emptySection = preflightOf([
  section('s1', [block('b1')]),
  section('s2', []),
  section('s3', [], { kind: 'EXECUTIVE_SUMMARY', heading: 'In short' }),
])
check('a section with no questions is amber', emptySection.problems.map((p) => p.kind), ['sectionless'])
check('…counted once, with the summary exempt', emptySection.problems[0].count, 1)

const noRows = preflightOf([
  section('s1', [block('b1'), block('b2', { feasibility_status: 'EMPTY' })]),
])
check('no rows is amber, not red — the section says so and that is correct',
  [noRows.problems[0].kind, noRows.problems[0].tone], ['empty', 'amber'])
check('…and the run is still worth starting', noRows.canGenerate, true)

const bare = preflightOf([section('s1', []), section('s2', [])])
check('an outline with no questions at all', bare.problems[0].kind, 'no-questions')
check('…cannot be generated', [bare.canGenerate, bare.clean], [false, false])

const fresh = preflightOf([section('s1', [unchecked('b1'), unchecked('b2')])])
check('nothing checked yet: refusable and sweepable at once',
  [fresh.canGenerate, fresh.sweepable.length], [false, 2])

// ── the order the dialog reads in ────────────────────────────────────────
const everything = preflightOf([
  section('s1', [
    unchecked('b1'),
    block('b2', { feasibility_status: 'INFEASIBLE', sql: '' }),
    block('b3', { feasibility_status: 'EMPTY' }),
    unchecked('b4', { sql: 'SELECT 1', sql_origin: 'HANDWRITTEN' }),
  ]),
  section('s2', []),
])
check('problems come out most severe first', everything.problems.map((p) => p.kind), [
  'no-query', 'stale-sql', 'infeasible', 'sectionless', 'empty',
])
check('…red before amber, so the colour agrees with the order',
  everything.problems.map((p) => p.tone), ['red', 'red', 'red', 'amber', 'amber'])

check('counts read as English at one', /^1 question has never been checked/.test(
  preflightOf([section('s1', [unchecked('b1'), block('b2')])]).problems[0].title,
), true)
check('…and above it', /^2 questions have never been checked/.test(
  preflightOf([section('s1', [unchecked('b1'), unchecked('b2'), block('b3')])]).problems[0].title,
), true)

check('sweepable keeps outline order, because the sweep scrolls down the page',
  preflightOf([
    section('s1', [unchecked('b1'), block('b2')]),
    section('s2', [unchecked('b3'), unchecked('b4')]),
  ]).sweepable.map((b) => b.id),
  ['b1', 'b3', 'b4'])

if (failures > 0) {
  // A thrown error is the non-zero exit; `process` would need Node's types.
  throw new Error(`${failures} readiness checks failed`)
}
console.log('\nall passed')
