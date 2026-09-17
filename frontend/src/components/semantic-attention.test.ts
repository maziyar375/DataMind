/**
 * *Needs attention*, grouped and worded.
 *
 * `npm run test:attention` — Node runs this file directly, the arrangement
 * `semantic-changes.test.ts` uses. The server decides every reason; what is
 * tested here is that one table is one row, that undescribed tables do not
 * bury the rest, and that each sentence says what the counts say.
 */
import {
  attentionLine, attentionSection, attentionView, listNames, undescribedWords,
} from './semantic-attention.ts'
import type { AttentionItemLike } from './semantic-attention.ts'

let failures = 0
function check(name: string, actual: unknown, expected: unknown): void {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures += 1
  console.log(
    ok
      ? `ok    ${name}`
      : `FAIL  ${name}\n      expected ${JSON.stringify(expected)}\n      got      ${JSON.stringify(actual)}`,
  )
}

function item(reason: string, table = '', detail: Record<string, unknown> = {}, name = ''): AttentionItemLike {
  return { reason, table, item: name, detail }
}

// The server's order: most urgent first.
const ITEMS: AttentionItemLike[] = [
  item('DRAFT_OLD', '', { days: 9 }),
  item('INVALID', 'public.orders', { entity: false, columns: 1, metrics: 1, issue: '`amount` is not a column of public.orders.' }),
  item('COLUMNS_CHANGED', 'public.orders', { version: 3, added: ['discount', 'tax', 'fee', 'tip'], removed: [], retyped: ['amount'] }),
  item('COLUMNS_CHANGED', 'public.customers', { version: 2, added: [], removed: ['legacy'], retyped: [] }),
  item('METRIC_IGNORED', 'public.orders', { used: 0, ignored: 4, days: 30 }, 'revenue'),
  item('UNREVIEWED_RELIED_ON', 'public.customers', { answers: 1, days: 30 }),
  item('UNDESCRIBED', 'public.refunds', { columns: 2 }),
  item('UNDESCRIBED', 'public.stores', { columns: 7 }),
]

// ── shape ─────────────────────────────────────────────────────────────────
const view = attentionView(ITEMS)
check('one row per table, in the order the server ranked them', view.rows.map((r) => r.table), ['public.orders', 'public.customers'])
check('each row carries every reason for its table', view.rows.map((r) => r.lines.map((l) => l.reason)), [
  ['INVALID', 'COLUMNS_CHANGED', 'METRIC_IGNORED'],
  ['COLUMNS_CHANGED', 'UNREVIEWED_RELIED_ON'],
])
check('undescribed tables are one row, not one each', view.undescribed, [
  { table: 'public.refunds', columns: 2 }, { table: 'public.stores', columns: 7 },
])
check('the count is the draft, each table row, and each undescribed table', view.count, 1 + 2 + 2)
check('the cards shown are the tables with rows', [...view.tables], ['public.orders', 'public.customers'])
check('a row takes its worst tone', view.rows.map((r) => r.tone), ['red', 'amber'])
check('and so does the whole list', view.tone, 'red')
check('only a table that gained columns can have its gaps filled', view.rows.map((r) => r.fillable), [true, false])

const none = attentionView([])
check('nothing needs attention is an empty view',
  [none.draft, none.rows, none.undescribed, none.count, none.tables.size, none.tone],
  [null, [], [], 0, 0, 'neutral'])
check('undescribed tables alone are not an alarm', attentionView([ITEMS[6]]).tone, 'neutral')
check('a reason this build does not know is skipped, not drawn blank',
  attentionView([item('SOMETHING_NEW', 'public.orders')]).count, 0)

// ── where a row opens ─────────────────────────────────────────────────────
const [ordersRow, customersRow] = view.rows
check('a broken metric opens the Metrics tab', attentionSection(ordersRow, ordersRow.broken), 'metrics')
check('a table whose columns moved opens Columns', attentionSection(customersRow), 'columns')
check('a broken column alone opens Columns',
  attentionSection(attentionView([item('INVALID', 't', { entity: false, columns: 2, metrics: 0 })]).rows[0],
    { metrics: 0, columns: 2 }),
  'columns')
check('a table gone from the schema opens Meaning, where its issue is shown',
  attentionSection(attentionView([item('INVALID', 't', { entity: true, columns: 0, metrics: 0 })]).rows[0],
    { metrics: 0, columns: 0 }),
  'meaning')
check('unreviewed text opens Meaning, where it is reviewed',
  attentionSection(attentionView([ITEMS[5]]).rows[0]), 'meaning')
check('a definition left out opens Metrics', attentionSection(attentionView([ITEMS[4]]).rows[0]), 'metrics')

// ── words ─────────────────────────────────────────────────────────────────
const words = (i: AttentionItemLike) => attentionLine(i).text
check('the draft', words(ITEMS[0]), 'This draft has not been touched in 9 days, and no answer reads it until it is published.')
check('what the schema broke, with the binder\'s own words beside it', [words(ITEMS[1]), attentionLine(ITEMS[1]).note], [
  '1 metric and 1 column no longer match the schema, and are kept out of the prompt.',
  '`amount` is not a column of public.orders.',
])
check('one broken entry is singular', words(item('INVALID', 't', { entity: false, columns: 0, metrics: 1 })),
  '1 metric no longer matches the schema, and is kept out of the prompt.')
check('a table gone from the schema', words(item('INVALID', 't', { entity: true, columns: 0, metrics: 0 })),
  'Its table is not in the schema any more, so none of it reaches the model.')
check('columns that moved, named', words(ITEMS[2]),
  'Its columns changed after v3 described it: 4 added (discount, tax, fee and 1 more); 1 changed type (amount).')
check('a definition left out', words(ITEMS[4]),
  '4 answers in the last 30 days left part of the “revenue” definition out, and none used it.')
check('unreviewed text answers stood on', words(ITEMS[5]),
  'A model wrote this and nobody has reviewed it, yet 1 Grounded answer in the last 30 days stood on it.')
check('undescribed, plural and singular', [undescribedWords(2), undescribedWords(1)], [
  '2 tables have no description, so answers about them read the bare schema.',
  '1 table has no description, so answers about it read the bare schema.',
])
check('a short list is joined', listNames(['a', 'b']), 'a and b')
check('a long one is cut with a count', listNames(['a', 'b', 'c', 'd', 'e']), 'a, b, c and 2 more')
check('glyphs carry the state without colour', ITEMS.map((i) => attentionLine(i).glyph).join(''), '◐✕△△◆◌○○')

// A throw rather than `process.exit`: the same non-zero exit for npm, and no
// `@types/node` for a file the app's own tsconfig type-checks.
if (failures > 0) throw new Error(`${failures} test(s) failed`)
