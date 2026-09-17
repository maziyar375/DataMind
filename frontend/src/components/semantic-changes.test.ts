/**
 * A change list, grouped and worded.
 *
 * `npm run test:changes` — Node runs this file directly (type stripping, no
 * bundler, no test framework, no new dependency), the arrangement
 * `semantic-metrics.test.ts` uses.
 *
 * The cases that matter most are about **order** and **honesty**: a change
 * that moves a number must never sit below one that moves wording, and a
 * sentence must not claim a detail the change did not carry.
 */
import {
  authorship, byVersion, changeCount, describeChange, firstLine, groupChanges, historyPath,
  originWords, plain, unpublishedWords,
} from './semantic-changes.ts'
import type { ChangeLike } from './semantic-changes.ts'

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

function change(
  kind: string, entity = '', item = '', extra: Partial<ChangeLike> = {},
): ChangeLike {
  const affects = new Set([
    'exclusions_changed', 'metric_added', 'metric_removed', 'metric_expression_changed',
    'metric_filters_changed', 'metric_joins_changed',
  ]).has(kind)
  return { kind, entity_key: entity, item_key: item, affects_sql: affects, ...extra }
}

const say = (c: ChangeLike) => plain(describeChange(c))

// ── sentences ─────────────────────────────────────────────────────────────
check('a filter added to a metric names the filter',
      say(change('metric_filters_changed', 'public.orders', 'revenue', {
        fields: ['filters'],
        before: { filters: ["status <> 'CANCELLED'"] },
        after: { filters: ["status <> 'CANCELLED'", "status <> 'REFUNDED'"] },
      })),
      "revenue now also filters on status <> 'REFUNDED'")
check('and a removed one says so',
      say(change('metric_filters_changed', 'public.orders', 'revenue', {
        fields: ['filters'],
        before: { filters: ['a > 0', 'b > 0'] },
        after: { filters: ['b > 0'] },
      })),
      'revenue no longer filters on a > 0')
check('names and expressions are code, prose is not',
      describeChange(change('metric_expression_changed', 'public.orders', 'revenue', {
        fields: ['expression'],
        before: { expression: 'SUM(amount)' },
        after: { expression: 'SUM(net_amount)' },
      })).map((s) => !!s.code),
      [true, false, true, false, true, false])
check('a grain change quotes both sides',
      say(change('entity_described', 'public.orders', '', {
        fields: ['grain'],
        before: { grain: 'one row per order' },
        after: { grain: 'one row per order line' },
      })),
      'Grain changed from “one row per order” to “one row per order line”')
check('a field set from nothing is "set", not "changed from nothing"',
      say(change('entity_described', 'public.orders', '', {
        fields: ['label'], before: { label: '' }, after: { label: 'Orders' },
      })),
      'Business name set to “Orders”')
check('a field emptied is "cleared"',
      say(change('column_described', 'public.orders', 'amount', {
        fields: ['unit'], before: { unit: 'USD' }, after: { unit: '' },
      })),
      'Column amount: unit cleared')
check('a fiscal month is a month name',
      say(change('time_changed', '', '', {
        fields: ['fiscal_year_start_month'],
        before: { fiscal_year_start_month: 1 }, after: { fiscal_year_start_month: 4 },
      })),
      'Time conventions: fiscal year start changed from January to April')
check('value meanings name what was added and what went',
      say(change('value_meanings_changed', 'public.orders', 'status', {
        fields: ['value_meanings'],
        before: { value_meanings: { C: 'cancelled', X: 'void' } },
        after: { value_meanings: { C: 'cancelled', R: 'refunded' } },
      })),
      'Column status: R = refunded; no longer explains X')
check('a history row with no detail says less rather than guess',
      say(change('metric_filters_changed', 'public.orders', 'revenue')),
      'revenue: filters changed')
check('an added metric shows its definition when it has one',
      say(change('metric_added', 'public.orders', 'aov', {
        after: { name: 'aov', expression: 'AVG(amount)' },
      })),
      'Added metric aov = AVG(amount)')
check('reviewing an entity, and un-reviewing a metric',
      [say(change('reviewed_changed', 'public.orders', '', { after: { reviewed: true } })),
       say(change('reviewed_changed', 'public.orders', 'revenue', { after: { reviewed: false } }))],
      ['Marked reviewed', 'revenue: no longer marked reviewed'])
check('an unknown kind is still readable',
      say(change('something_new', 'x')), 'something new')

// ── grouping and order ────────────────────────────────────────────────────
const mixed = [
  change('context_changed'),
  change('glossary_added', '', 'aov'),
  change('entity_described', 'public.customers', '', { fields: ['label'], before: { label: '' }, after: { label: 'Buyers' } }),
  change('column_described', 'public.orders', 'amount'),
  change('metric_filters_changed', 'public.orders', 'revenue'),
]
const grouped = groupChanges(mixed)
check('a group that changes numbers comes first',
      grouped.map((g) => g.key), ['public.orders', 'doc', 'glossary', 'public.customers'])
check('and inside it, the number-changing line leads',
      grouped[0].lines.map((l) => l.kind), ['metric_filters_changed', 'column_described'])
check('the group says whether it changes numbers',
      grouped.map((g) => g.affectsSql), [true, false, false, false])
check('a table is a code title; the document is a heading',
      grouped.map((g) => [g.title, g.titleIsCode]),
      [['public.orders', true], ['This database', false], ['Business terms', false],
       ['public.customers', true]])
check('document-level exclusions change numbers and lead too',
      groupChanges([change('context_changed'), change('exclusions_changed')])[0].lines.map((l) => l.kind),
      ['exclusions_changed', 'context_changed'])

const added = groupChanges([
  change('entity_added', 'public.items', '', { after: { table: 'public.items', label: 'Items' } }),
  change('column_added', 'public.items', 'qty'),
  change('column_added', 'public.items', 'price'),
  change('metric_added', 'public.items', 'units'),
])
check('a new table folds its columns into one line, and leads its group…',
      added[0].lines.map((l) => plain(l.segments)),
      ['Added to the layer as “Items”, with 2 columns', 'Added metric units'])
check('…but never its metrics, which change numbers',
      added[0].lines.filter((l) => l.kind === 'metric_added').length, 1)
check('columns of a table that stayed are not folded',
      groupChanges([change('column_added', 'public.orders', 'qty')])[0].lines.length, 1)
check('an empty list is no groups', groupChanges([]), [])

// ── one entry's history ───────────────────────────────────────────────────
const history = [
  { ...change('metric_filters_changed', 'public.orders', 'revenue'), version: 3 },
  { ...change('entity_added', 'public.orders'), version: 1 },
  { ...change('column_added', 'public.orders', 'qty'), version: 1 },
  { ...change('column_added', 'public.orders', 'price'), version: 1 },
]
check('history rows gather under their version, newest first',
      byVersion(history).map((v) => [v.version, v.rows.length]), [[3, 1], [1, 3]])
check('and a version that added the table reads as one line',
      groupChanges(byVersion(history)[1].rows).flatMap((g) => g.lines).map((l) => plain(l.segments)),
      ['Added to the layer, with 2 columns'])
check('a detail-less description change does not claim which field',
      say(change('entity_described', 'public.orders')), 'Described differently')

// ── origin, authorship, counts ────────────────────────────────────────────
check('origins as words',
      [originWords({ migrated: true }), originWords({ deleted: true }),
       originWords({ restored_from: 9 }), originWords({ generated_job_ids: ['j'] }),
       originWords({ imported: true }), originWords({}), originWords(null)],
      ['recorded at migration', 'deleted', 'restored from v9', 'generated', 'imported', '', ''])
check('authorship puts the person after what happened',
      [authorship({}, 'Sara Karimi'), authorship({ restored_from: 3 }, 'Ali'),
       authorship({ generated_job_ids: ['j'] }, 'Sara'), authorship({ migrated: true }, ''),
       authorship({}, '')],
      ['published by Sara Karimi', 'restored from v3, published by Ali',
       'generated, published by Sara', 'recorded at migration', 'published'])
check('the draft chip counts its changes, and says so when there are none',
      [unpublishedWords(0), unpublishedWords(1), unpublishedWords(3)],
      ['No unpublished changes', '1 unpublished change', '3 unpublished changes'])
check('counts sum the kinds', changeCount({ metric_added: 2, column_added: 1 }), '3 changes')
check('one change is singular', changeCount({ entity_described: 1 }), '1 change')
check('a note row shows its first non-empty line',
      firstLine('\n  Refunds are not revenue.\nAsked by finance.'), 'Refunds are not revenue.')

// ── addresses ─────────────────────────────────────────────────────────────
check('the history list', historyPath('c1'), '/sources/c1/semantic/history')
check('one version', historyPath('c1', { version: 13 }), '/sources/c1/semantic/history/13')
check('one metric',
      historyPath('c1', { entity: 'public.orders', item: 'revenue' }),
      '/sources/c1/semantic/history?entity=public.orders&item=revenue')

// A throw rather than `process.exit`: the same non-zero exit for npm, and no
// `@types/node` for a file the app's own tsconfig type-checks.
if (failures > 0) throw new Error(`${failures} test(s) failed`)
