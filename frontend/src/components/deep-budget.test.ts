/**
 * `npm run test:deep` (second half) — the deep budget form's arithmetic.
 *
 * What it pins is the part a person could be misled by: a number over the
 * ceiling is named before Save rather than refused after it, a zero is a
 * choice and not an error, and the sentence under the section says a
 * refused question is refused — never "answered smaller".
 */
import {
  budgetChanged,
  budgetProblems,
  budgetSentence,
  refusal,
  toLimits,
  type DeepBudget,
  type DeepLimits,
} from './deep-budget.ts'

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

const CEILING: DeepLimits = {
  max_steps: 5, max_queries: 12, max_rows_total: 20_000,
  max_prompt_tokens: 400_000, deadline_seconds: 600,
}
const SET: DeepLimits = {
  max_steps: 3, max_queries: 8, max_rows_total: 5_000,
  max_prompt_tokens: 100_000, deadline_seconds: 300,
}
const asDraft = (l: DeepLimits) =>
  Object.fromEntries(Object.entries(l).map(([k, v]) => [k, String(v)])) as Record<
    keyof DeepLimits, string
  >

// ── problems ────────────────────────────────────────────────────────────
check(budgetProblems(asDraft(SET), CEILING), {}, 'a budget inside the ceiling has no problems')
check(budgetProblems(asDraft(CEILING), CEILING), {}, 'the ceiling itself is allowed')
check(
  budgetProblems({ ...asDraft(SET), max_steps: '6' }, CEILING),
  { max_steps: 'At most 5 on this installation.' },
  'over the ceiling is named, not clipped',
)
check(
  budgetProblems({ ...asDraft(SET), max_prompt_tokens: '500000' }, CEILING).max_prompt_tokens,
  'At most 400,000 on this installation.',
  'large ceilings are grouped for reading',
)
check(
  budgetProblems({ ...asDraft(SET), max_queries: '-1', max_rows_total: '2.5' }, CEILING),
  { max_queries: 'A whole number, 0 or more.', max_rows_total: 'A whole number, 0 or more.' },
  'negatives and fractions are not budgets',
)
check(
  budgetProblems({ ...asDraft(SET), deadline_seconds: '' }, CEILING),
  { deadline_seconds: 'A whole number, 0 or more.' },
  'an empty field is not zero',
)
check(
  budgetProblems({ ...asDraft(SET), deadline_seconds: '30' }, CEILING),
  { deadline_seconds: '0, or at least 60 — a shorter one cannot finish a step.' },
  'a deadline too short to finish a step',
)
check(
  budgetProblems({ ...asDraft(SET), max_steps: '0', deadline_seconds: '0' }, CEILING),
  {},
  'zero is a choice — deep analysis off here — not an error',
)

// ── changed, and the numbers ─────────────────────────────────────────────
check(budgetChanged(asDraft(SET), SET), false, 'the stored budget is not a change')
check(budgetChanged({ ...asDraft(SET), max_steps: ' 3 ' }, SET), false, 'whitespace is not a change')
check(budgetChanged({ ...asDraft(SET), max_steps: '2' }, SET), true, 'one number moved')
check(toLimits({ ...asDraft(SET), max_steps: ' 4' }), { ...SET, max_steps: 4 }, 'the draft as numbers')

// ── refusal, and the sentence ────────────────────────────────────────────
check(refusal(SET), null, 'a budget with room refuses nothing')
check(refusal({ ...SET, max_rows_total: 0, max_steps: 0 }), 'max_steps', 'the first zero, in server order')

const stored: DeepBudget = { effective: SET, ceiling: CEILING, is_default: false, refused: null }
const fallback: DeepBudget = { effective: CEILING, ceiling: CEILING, is_default: true, refused: null }
check(budgetSentence(stored), 'Up to 3 steps and 8 queries, within 5 minutes.', 'a set budget')
check(
  budgetSentence(fallback),
  "Up to 5 steps and 12 queries, within 10 minutes. These are the installation's limits; nothing narrower is set here.",
  'the default says it is the default',
)
check(
  budgetSentence({ ...stored, effective: { ...SET, max_steps: 0 }, refused: 'max_steps' }),
  'Deep analysis is off on this data source: it allows no steps. A deep question is refused, not answered smaller.',
  'a zero reads as off, and refused',
)
check(
  budgetSentence(stored, { ...SET, deadline_seconds: 0 }),
  'Deep analysis is off on this data source: it allows no time. A deep question is refused, not answered smaller.',
  'the sentence follows the draft before it is saved',
)
check(
  budgetSentence({ ...stored, refused: 'unreadable' }),
  'The stored budget cannot be read, so deep analysis is refused here until it is saved again.',
  'an unreadable budget is said to be refused, never the ceiling',
)
check(budgetSentence(stored, { ...SET, deadline_seconds: 90 }).endsWith('within 1.5 minutes.'), true, 'fractional minutes')

if (failures) {
  console.log(`\n${failures} of ${counter} failed`)
  throw new Error('deep-budget tests failed')
}
console.log('\nall passed')
