/**
 * What the knowledge tab decides before it draws anything.
 *
 * `npm run test:template` — Node runs this file directly, the arrangement
 * `semantic-drift.test.ts` uses.
 *
 * The cases that matter are the ones where being *nearly* right is worse than
 * being absent: a parameter highlight that lands on the wrong `'EMEA'`, a Save
 * button that goes green before the server has answered, a tab badge showing a
 * total when there is nothing to do.
 */
import {
  CORRECTION_SHAPES,
  conflictEvidence, differingCells, embeddingView, indexSummary, percent,
  scoreView, sparkHeights,
  isUnused, markLiterals, matches, previewQuestion, questionParts, questionSlots,
  readiness, resolveReadiness, roleLabel, rowSubtitle, sections, statusOf,
  suggestionSection, suggestionView, tabCount, valuesOf,
} from './knowledge-template.ts'
import type {
  EmbeddingState, ProposalRow, ScoreRun, SuggestionRow, TemplateRow,
  TemplateSlot,
} from './knowledge-template.ts'

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

const NOW = new Date('2026-08-31T12:00:00Z')

function row(over: Partial<TemplateRow> = {}): TemplateRow {
  return {
    id: 'a',
    question: 'revenue by month for {region} in {year}',
    params: [
      { name: 'region', type: 'string', comment: 'one of: EMEA, NA, APAC' },
      { name: 'year', type: 'date', comment: '' },
    ],
    referenced_tables: ['public.orders', 'public.stores'],
    status: 'ACTIVE',
    status_reason: '',
    role: 'RETRIEVABLE',
    hit_count: 14,
    last_hit_at: '2026-08-28T09:00:00Z',
    verified_at: '2026-08-01T09:00:00Z',
    ...over,
  }
}

function slot(name: string, type = 'string', comment = ''): TemplateSlot {
  return { name, type, comment }
}

// ── status: never colour alone ────────────────────────────────────────────
check('an active template reads as active', statusOf(row()), {
  glyph: '✓', label: 'Active', tone: 'green', needsYou: false,
})
check('a stored STALE row needs a human', statusOf(row({ status: 'STALE' })), {
  glyph: '⚠', label: 'Stale', tone: 'amber', needsYou: true,
})
// The list endpoint reports drift on read without writing it. A row can be
// ACTIVE in the database and stale on the screen, and the screen is the one
// telling the truth about right now.
check('read-time drift outranks the stored status', statusOf(row(), true), {
  glyph: '⚠', label: 'Stale', tone: 'amber', needsYou: true,
})
check('drift on an archived row does not resurrect it',
  statusOf(row({ status: 'ARCHIVED' }), true).label, 'Archived')
check('an unknown status falls back rather than throwing',
  statusOf(row({ status: 'SOMETHING_NEW' })).label, 'Active')

check('the default role earns no chip', roleLabel('RETRIEVABLE'), '')
check('a benchmark row says what it is for', roleLabel('BENCHMARK_ONLY'),
  'Measures accuracy')

// ── the second line ───────────────────────────────────────────────────────
check('a healthy row says what it touches and how often it hit',
  rowSubtitle(row(), false, NOW),
  { left: 'public.orders, public.stores', right: '14 hits', tone: 'neutral' })
check('one hit is not "1 hits"',
  rowSubtitle(row({ hit_count: 1 }), false, NOW).right, '1 hit')
check('a never-matched template is stated, not accused',
  rowSubtitle(row({ hit_count: 0, last_hit_at: null }), false, NOW),
  { left: 'public.orders, public.stores', right: 'No matches yet', tone: 'faint' })
check('a stale row leads with the reason, verbatim',
  rowSubtitle(row({ status: 'STALE', status_reason: '`orders.region` no longer exists.' }),
    false, NOW).right,
  '`orders.region` no longer exists.')
check('a stale row with no reason still says something true',
  rowSubtitle(row({ status: 'STALE' }), false, NOW).right,
  'This template stopped working when the schema changed.')

// ── unused: information, not an accusation ────────────────────────────────
check('a template matched last week is not unused',
  isUnused(row(), NOW), false)
check('ninety days without a match is worth a faint line',
  isUnused(row({ last_hit_at: '2026-01-01T00:00:00Z' }), NOW), true)
check('a template nobody has ever matched is not "unused", it is new',
  isUnused(row({ hit_count: 0, last_hit_at: null }), NOW), false)
check('the unused line rides along with the hit count, quietly',
  rowSubtitle(row({ last_hit_at: '2026-01-01T00:00:00Z' }), false, NOW),
  { left: 'public.orders, public.stores',
    right: '14 hits · no matches in 90 days', tone: 'faint' })

// ── the braces the curator learns ─────────────────────────────────────────
check('a question splits into text and slots, braces kept',
  questionParts('revenue for {region} now'),
  [{ text: 'revenue for ', slot: false },
   { text: '{region}', slot: true },
   { text: ' now', slot: false }])
check('a question that is only a slot still splits',
  questionParts('{region}'), [{ text: '{region}', slot: true }])
check('a question with no braces is one part',
  questionParts('revenue'), [{ text: 'revenue', slot: false }])
// Read at display time into spans, never into markup — a question is user text.
check('an unclosed brace is text, not a slot',
  questionParts('revenue for {region').every((p) => !p.slot), true)
check('slots come back in order without duplicates',
  questionSlots('{a} then {b} then {a}'), ['a', 'b'])

// ── the live preview ──────────────────────────────────────────────────────
check('a preview shows the values a curator listed',
  previewQuestion('revenue by month for {region} in {year}',
    [slot('region', 'string', 'one of: EMEA, NA, APAC'), slot('year', 'date')]),
  'revenue by month for EMEA in 2026-01-01')
check('a string slot with no list falls back to its own name',
  previewQuestion('revenue for {region}', [slot('region')]), 'revenue for REGION')
check('a number slot previews as a number',
  previewQuestion('over {threshold}', [slot('threshold', 'number')]), 'over 1000')
// One slot, one value — the preview shows a question somebody could ask, and
// `{a} and {a}` asked with two different values is not one question.
check('a slot repeated twice gets the same value both times',
  previewQuestion('{a} and {a}', [slot('a', 'string', 'one of: X, Y')]), 'X and X')

check('a value list is read off the comment', valuesOf('one of: EMEA, NA, APAC'),
  ['EMEA', 'NA', 'APAC'])
check('prose is not a value list', valuesOf('the first day of the year'), [])
check('a colon with no comma is prose too', valuesOf('note: whatever'), [])

// ── highlighting the literal a proposal would replace ─────────────────────
function proposal(over: Partial<ProposalRow> = {}): ProposalRow {
  return {
    name: 'region', literal: "'EMEA'", occurrence: 0,
    eligible: true, suggested: true, reason: '', ...over,
  }
}

check('the literal is marked in place',
  markLiterals("WHERE region = 'EMEA'", [proposal()]),
  [{ text: 'WHERE region = ', slot: null }, { text: "'EMEA'", slot: 'region' }])

// The reason `occurrence` exists at all: a statement that filters on 'EMEA'
// and also mentions it in a CASE has two identical texts, and marking the
// wrong one tells the curator their filter is about to change when it is not.
const twice = "CASE WHEN region = 'EMEA' THEN 1 END, x FROM t WHERE region = 'EMEA'"
check('the second occurrence is the one marked',
  markLiterals(twice, [proposal({ occurrence: 1 })])
    .filter((s) => s.slot !== null).length, 1)
check('and it is the second one, not the first',
  markLiterals(twice, [proposal({ occurrence: 1 })])[0].text.includes('CASE WHEN'),
  true)
check('two proposals mark two literals',
  markLiterals("a = 'X' AND b = 'Y'",
    [proposal({ name: 'a', literal: "'X'" }), proposal({ name: 'b', literal: "'Y'" })])
    .filter((s) => s.slot !== null).map((s) => s.slot), ['a', 'b'])
check('a literal the SQL no longer contains is skipped, not thrown',
  markLiterals('SELECT 1', [proposal()]), [{ text: 'SELECT 1', slot: null }])
check('marks come back in statement order however they arrived',
  markLiterals("a = 'X' AND b = 'Y'",
    [proposal({ name: 'b', literal: "'Y'" }), proposal({ name: 'a', literal: "'X'" })])
    .filter((s) => s.slot !== null).map((s) => s.slot), ['a', 'b'])

// ── the Save button ───────────────────────────────────────────────────────
const SQL = "SELECT SUM(amount) FROM orders WHERE region = :region"
check('a complete template is ready',
  readiness('revenue for {region}', SQL, [slot('region')], true),
  { ready: true, issue: '' })
// The one thing the browser must not do: promise something the server rejects.
check('nothing is ready until the server has answered',
  readiness('revenue for {region}', SQL, [slot('region')], null),
  { ready: false, issue: '' })
check('a rejected statement is not ready',
  readiness('revenue for {region}', SQL, [slot('region')], false).ready, false)
check('an empty question is not ready, and says nothing yet',
  readiness('', SQL, [], true), { ready: false, issue: '' })
check('a slot the question never names is refused with a sentence',
  readiness('revenue', SQL, [slot('region')], true),
  { ready: false,
    issue: 'The question does not mention {region} — a parameter the question ' +
      'never names can never be filled in.' })
check('a brace with no parameter behind it is refused too',
  readiness('revenue for {region}', 'SELECT 1', [], true),
  { ready: false,
    issue: '{region} is in the question but not in the SQL. ' +
      'Tick the literal it should replace.' })
check('two loose braces read as a plural',
  readiness('{a} and {b}', 'SELECT 1', [], true).issue.startsWith('{a}, {b} are'), true)

// ── the sections, and the tab badge ───────────────────────────────────────
const rows = [
  row({ id: 'ok' }),
  row({ id: 'broken', status: 'STALE' }),
  row({ id: 'gone', status: 'ARCHIVED' }),
  row({ id: 'drifting' }),
]
const split = sections(rows, ['drifting'])
check('what is broken, what is taught, what was archived',
  [split.needsYou.map((r) => r.id), split.taught.map((r) => r.id),
   split.archived.map((r) => r.id)],
  [['broken', 'drifting'], ['ok'], ['gone']])

// Zero work, no number. A badge that always shows a total is decoration.
check('the tab counts only what needs a human', tabCount(rows, ['drifting']), 2)
check('a healthy store shows no badge at all',
  tabCount([row({ id: 'ok' })], []), undefined)

// ── search ────────────────────────────────────────────────────────────────
check('an empty search matches everything', matches(row(), '   '), true)
check('search reads the question', matches(row(), 'REVENUE'), true)
check('search reads the tables it touches', matches(row(), 'stores'), true)
check('search reads the parameter names', matches(row(), 'region'), true)
check('and does not match what is not there', matches(row(), 'churn'), false)

// ── the backlog and the queue ─────────────────────────────────────────────
function suggestion(over: Partial<SuggestionRow> = {}): SuggestionRow {
  return {
    kind: 'TRAFFIC',
    question: 'which stores beat target last quarter',
    count: 9,
    reason: 'Asked 9× this month, never matched',
    sql: '',
    words: [],
    ...over,
  }
}

// A suggestion is `○` — *not yet knowledge* — and never `⚠`, because nothing
// here is broken. A backlog that looked like a fault list would train people
// to dread opening the tab.
check('traffic is an offer, not a fault', suggestionView(suggestion()), {
  glyph: '○', tone: 'faint', action: 'Teach this',
})
check(
  'a corrected tile is the same offer',
  suggestionView(suggestion({ kind: 'BACKFILL' })).action,
  'Teach this',
)
// The one suggestion that *is* work somebody is waiting on.
check('a flag is work', suggestionView(suggestion({ kind: 'FLAGGED' })), {
  glyph: '⚠', tone: 'amber', action: 'Review',
})
// It names a word, not a question — and the fix is often a synonym in the
// semantic layer, so "Teach this" would be the wrong verb.
check(
  'an unrecognised word offers no one-click action',
  suggestionView(suggestion({ kind: 'UNKNOWN_WORDS', words: ['churn'] })).action,
  '',
)
check(
  'flags are work and everything else is a queue',
  [
    suggestionSection(suggestion({ kind: 'FLAGGED' })),
    suggestionSection(suggestion({ kind: 'TRAFFIC' })),
    suggestionSection(suggestion({ kind: 'UNKNOWN_WORDS' })),
  ],
  ['needsYou', 'suggested', 'suggested'],
)

// ── resolving a flag ──────────────────────────────────────────────────────
// §1.5 as an interaction: question-shaped, definition-shaped, or neither. The
// curator decides and the product does not guess.
check(
  'there are exactly three shapes a correction can have',
  CORRECTION_SHAPES.map((s) => s.value),
  ['template', 'definition', 'dismiss'],
)
check(
  'a dismissal without a reason cannot be sent',
  resolveReadiness('dismiss', '   ', false),
  { ready: false, issue: 'Say why — the person who flagged this will see the reason.' },
)
check(
  'a dismissal with a reason can',
  resolveReadiness('dismiss', 'The rollup is right; refunds net out.', false).ready,
  true,
)
check(
  'question-shaped waits for the template to exist',
  resolveReadiness('template', '', false),
  { ready: false, issue: 'Save the template first.' },
)
check(
  'and is ready once it does',
  resolveReadiness('template', '', true).ready,
  true,
)
// A definition goes to the semantic layer, so there is nothing to save here.
check('definition-shaped needs nothing else', resolveReadiness('definition', '', false).ready, true)

// ── the conflict's evidence (Phase 4) ─────────────────────────────────────
// The rows *are* the evidence. Fabric reasons over SQL text and reports a
// confidence of 1–5; this ran both statements and compared the answers, so the
// pane shows the disagreement rather than a warning about one.
const EVIDENCE = {
  summary: 'The two statements return the same rows with different values.',
  left_columns: ['month', 'revenue'],
  right_columns: ['month', 'revenue'],
  left_rows: [['2026-07', '481220']],
  right_rows: [['2026-07', '512940']],
}

check(
  'the evidence comes through in the shape the pane renders',
  conflictEvidence(EVIDENCE),
  {
    summary: EVIDENCE.summary,
    mine: { columns: ['month', 'revenue'], rows: [['2026-07', '481220']] },
    theirs: { columns: ['month', 'revenue'], rows: [['2026-07', '512940']] },
    hasRows: true,
  },
)

// A pane that threw on a missing key would take the whole tab down over a
// template nobody was looking at — and this comes from a JSONB column.
check(
  'a healthy template has no evidence and does not throw',
  conflictEvidence({}),
  { summary: '', mine: { columns: [], rows: [] },
    theirs: { columns: [], rows: [] }, hasRows: false },
)
check('undefined evidence is the same as none', conflictEvidence(undefined).hasRows, false)
check('a malformed row is dropped rather than rendered', conflictEvidence({
  left_rows: [['a'], 'not-a-row', 7],
}).mine.rows, [['a']])
check('every cell is a string by the time the pane sees it', conflictEvidence({
  left_rows: [[1, null]],
}).mine.rows, [['1', 'null']])

// A conflict recorded before the evidence column existed still shows its
// reason — the pane says the rows are gone rather than drawing an empty table.
check(
  'a reason with no rows is still a reason',
  conflictEvidence({ summary: 'Two templates answer this differently.' }),
  {
    summary: 'Two templates answer this differently.',
    mine: { columns: [], rows: [] }, theirs: { columns: [], rows: [] },
    hasRows: false,
  },
)

// The reader is not asked to compare two tables by eye: the cell that moved is
// marked, positionally, because the comparator that produced the rows is
// positional too.
check(
  'the differing cell is the one that moved',
  differingCells(['2026-07', '481220'], ['2026-07', '512940']),
  [false, true],
)
check('identical rows differ nowhere', differingCells(['a', 'b'], ['a', 'b']), [false, false])
check(
  'a missing counterpart row marks every cell',
  differingCells(['a', 'b'], []),
  [true, true],
)
check(
  'a wider row is compared to its full width',
  differingCells(['a'], ['a', 'b']),
  [false, true],
)

// ── the two statuses Phase 4 writes ───────────────────────────────────────
// Both are withdrawn from answering and both stay visible: deleting a person's
// work to hide drift is worse than showing it.
check('a conflicted template is work for a human', statusOf({
  ...row(), status: 'CONFLICTED',
}).needsYou, true)
check('and it says so in words, not only in colour', statusOf({
  ...row(), status: 'CONFLICTED',
}).label, 'Conflicted')
check('a stale template is work too', statusOf({ ...row(), status: 'STALE' }).glyph, '⚠')
check(
  "the conflict reason is what the row's second line says",
  rowSubtitle(
    { ...row(), status: 'CONFLICTED',
      status_reason: 'Two templates answer this differently — “revenue by month” disagrees.' },
    false,
  ).right,
  'Two templates answer this differently — “revenue by month” disagrees.',
)

// ── the score (Phase 6) ───────────────────────────────────────────────────
// Two numbers, and the strip says which to believe. Genie's Evaluations tab
// shows one; that is a weakness to improve on, not a design to copy.
const run = (over: Partial<ScoreRun> = {}): ScoreRun => ({
  status: 'SUCCEEDED',
  total: 10,
  scored: 10,
  held_out_total: 4,
  held_out_matched: 3,
  taught_total: 6,
  taught_matched: 5,
  finished_at: '2026-09-01T09:00:00Z',
  created_at: '2026-09-01T08:00:00Z',
  ...over,
})

check('the held-out number is the one from the latest run', scoreView([run()]).heldOut, 0.75)
check('and the taught number is beside it', scoreView([run()]).taught, 5 / 6)
check(
  'a run with no held-out question has no held-out accuracy',
  scoreView([run({ held_out_total: 0, held_out_matched: 0 })]).heldOut,
  null,
)
// `—`, not `0%`. A run that measured nothing has no accuracy, and printing
// zero for it would be the loudest possible wrong answer.
check('and it renders as an em dash, never as zero', percent(null), '—')
check('a real number renders as a whole percent', percent(0.7234), '72%')
check('zero really is zero when it was measured', percent(0), '0%')

check(
  'questions that could not be scored are surfaced, not hidden',
  scoreView([run({ total: 10, scored: 7 })]).unscored,
  3,
)

// The sparkline is the held-out series only: one line, one series, and it is
// the honest one.
check(
  'the sparkline reads oldest to newest',
  scoreView([
    run({ held_out_matched: 3 }),
    run({ held_out_matched: 2 }),
    run({ held_out_matched: 1 }),
  ]).spark,
  [0.25, 0.5, 0.75],
)
check(
  'a failed run contributes no point to the line',
  scoreView([run({ status: 'FAILED' }), run({ held_out_matched: 2 })]).spark,
  [0.5],
)
check('a set with no finished run has not run', scoreView([]).ran, false)
check(
  'a queued run says it is running',
  scoreView([run({ status: 'QUEUED' })]).running,
  true,
)
check(
  'a failed newest run shows its reason',
  scoreView([run({ status: 'FAILED', error_message: 'the model is gone' })]).failed,
  'the model is gone',
)
check(
  'the held-out count falls back to the set until a run exists',
  scoreView([], 25).heldOutCount,
  25,
)

// Against the fixed 0–100% scale, not the series' own range: self-normalising
// would turn 71/72/73% into a dramatic climb, which is exactly the misreading
// a score strip must not invite.
check('bars are heights against the full scale', sparkHeights([0.71, 0.72, 0.73]),
      [0.71, 0.72, 0.73])
check('and are clamped rather than allowed to overflow', sparkHeights([-1, 2]), [0, 1])

// ── the embedding matcher (Phase 7) ──────────────────────────────────────
// The four states, and the reason there are four: a boolean would collapse
// "pinned but not yet indexed" into "on", which promises a behaviour the next
// question will not show.
function index(over: Partial<EmbeddingState> = {}): EmbeddingState {
  return {
    enabled: true, model: 'text-embedding-3-small', dimension: 1536, providers: 1,
    templates: 10, indexed: 10, message: '', ...over,
  }
}

check('word matching is a state, not a warning',
      embeddingView(index({ enabled: false })).tone, 'off')
check('and it describes what the other mode adds, not what it lacks',
      embeddingView(index({ enabled: false })).detail.includes('mean the same thing'),
      true)
check('a fully indexed store is on', embeddingView(index()).tone, 'on')
check('a pinned store with vectors missing is indexing, not on',
      embeddingView(index({ indexed: 4 })).tone, 'indexing')
check('and it says how far along it is',
      embeddingView(index({ indexed: 4 })).detail, '4 of 10 questions indexed — 6 still match on words alone.')
check('one missing question reads as one', embeddingView(index({ templates: 5, indexed: 4 })).detail,
      '4 of 5 questions indexed — 1 still matches on words alone.')
check('the provider\'s own sentence wins over every other state',
      embeddingView(index({ message: 'Anthropic does not offer an embedding endpoint.' })).tone,
      'problem')
check('and it is shown verbatim, because it names the fix',
      embeddingView(index({ message: 'Anthropic does not offer an embedding endpoint.' })).detail,
      'Anthropic does not offer an embedding endpoint.')
check('a message on a disabled store still wins',
      embeddingView(index({ enabled: false, message: 'no key' })).tone, 'problem')
check('an on store names the model it was indexed with',
      embeddingView(index()).detail.includes('text-embedding-3-small'), true)

// A store with nothing taught in it. This is the state Aurora Coffee is in,
// and the one the strip used to be hidden for entirely — so the two branches
// below had no reader at all until the gate came off.
check('an empty store can still be switched, and the off state still reads as a choice',
      embeddingView(index({ enabled: false, templates: 0, indexed: 0 })).tone, 'off')
check('with no provider able to embed, the sentence names the one thing to do',
      embeddingView(index({ enabled: false, providers: 0 })).detail.includes(
        'give one an embedding model in LLM providers'), true)
check('and with a provider available it makes no such demand',
      embeddingView(index({ enabled: false })).detail.includes('LLM providers'), false)
check('a pinned store with nothing taught yet is not claiming to be on',
      embeddingView(index({ templates: 0, indexed: 0 })).tone, 'indexing')
check('and it says what will happen rather than counting zero of zero',
      embeddingView(index({ templates: 0, indexed: 0 })).detail,
      'Ready with text-embedding-3-small. The first question taught here is indexed as it is saved.')

// The sweep's indexing sentence. It can report a failure without the sweep
// having failed — staleness and conflicts both completed, and the vectors are
// simply a pass behind.
const sweep = { indexed: 0, index_current: 0, index_truncated: false, index_error: '' }
check('a sweep that indexed nothing says nothing', indexSummary(sweep), '')
check('a lexical connection adds no sentence at all',
      indexSummary({ ...sweep, index_current: 12 }), '')
check('re-indexed questions are counted', indexSummary({ ...sweep, indexed: 3 }),
      ' 3 questions re-indexed for embedding search.')
check('one reads as one', indexSummary({ ...sweep, indexed: 1 }),
      ' 1 question re-indexed for embedding search.')
check('a truncated pass says the rest are coming',
      indexSummary({ ...sweep, indexed: 200, index_truncated: true }).includes('next check'),
      true)
check('a failure is reported as the index being behind, not as the check breaking',
      indexSummary({ ...sweep, index_error: 'the key was revoked' }),
      ' The index could not be brought up to date: the key was revoked')
check('and a failure outranks a partial success',
      indexSummary({ ...sweep, indexed: 3, index_error: 'the key was revoked' }).startsWith(' The index'),
      true)

console.log(failures === 0 ? '\nall passed' : `\n${failures} failed`)
if (failures > 0) throw new Error(`${failures} test(s) failed`)
