/**
 * Reading a semantic document as a list of measures.
 *
 * `npm run test:metrics` — Node runs this file directly (type stripping, no
 * bundler, no test framework, no new dependency), the arrangement
 * `semantic-drift.test.ts` uses.
 *
 * The cases that matter are the ones about *naming*: the panel exists to make
 * one name mean one thing, so a collision it fails to show is the only bug
 * here worth calling a bug.
 */
import {
  ambiguousNames, collectMetrics, matchesMetric, metricSummary,
} from './semantic-metrics.ts'
import type { MetricHost } from './semantic-metrics.ts'

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

function host(table: string, metrics: string[][], exclude = false): MetricHost {
  return {
    table,
    exclude,
    metrics: metrics.map(([name, expression]) => ({
      name, expression: expression ?? 'SUM(x)',
    })),
  }
}

// ── one name, one meaning ─────────────────────────────────────────────────
const collision = [
  host('sales.orders', [['revenue', 'SUM(total)']]),
  host('sales.invoices', [['revenue', 'SUM(amount)']]),
]

check('a name on two tables is ambiguous',
      [...ambiguousNames(collision)], ['revenue'])
check('and both rows say so',
      collectMetrics(collision).map((r) => r.ambiguous), [true, true])
check('case and spacing do not hide a collision',
      [...ambiguousNames([
        host('a.x', [['Revenue', 'SUM(a)']]),
        host('b.y', [[' revenue ', 'SUM(b)']]),
      ])], ['revenue'])
check('the same name twice on one table is a collision too',
      [...ambiguousNames([host('a.x', [['revenue', 'SUM(a)'], ['revenue', 'SUM(b)']])])],
      ['revenue'])
check('two different names are not',
      [...ambiguousNames([
        host('sales.orders', [['order_count', 'COUNT(id)']]),
        host('sales.invoices', [['revenue', 'SUM(amount)']]),
      ])], [])
check('an excluded table claims nothing',
      [...ambiguousNames([
        host('sales.orders', [['revenue', 'SUM(total)']]),
        host('staging.orders', [['revenue', 'SUM(total)']], true),
      ])], [])
check('and its own rows are not marked ambiguous either',
      collectMetrics([
        host('sales.orders', [['revenue', 'SUM(total)']]),
        host('staging.orders', [['revenue', 'SUM(total)']], true),
      ]).map((r) => r.ambiguous), [false, false])
check('but they are still listed, labelled as set aside',
      collectMetrics([host('staging.orders', [['revenue', 'SUM(t)']], true)])
        .map((r) => r.excluded), [true])

// ── the order a reader wants ──────────────────────────────────────────────
const spread = [
  host('z.last', [['orders', 'COUNT(id)'], ['', 'SUM(x)']]),
  host('a.first', [['revenue', 'SUM(total)']]),
  host('m.middle', [['orders', 'COUNT(id)']]),
]
check('sorted by name, then by table — so a collision lands adjacent',
      collectMetrics(spread).map((r) => `${r.name}@${r.table}`),
      ['orders@m.middle', 'orders@z.last', 'revenue@a.first', '@z.last'])
check('an unnamed draft sorts last rather than first',
      collectMetrics(spread).at(-1)?.name, '')
check('each row keeps its position in its own entity',
      collectMetrics(spread).map((r) => r.index), [0, 0, 0, 1])

// ── search ────────────────────────────────────────────────────────────────
const [row] = collectMetrics([{
  table: 'sales.orders',
  metrics: [{ name: 'revenue', label: 'Net revenue', expression: 'SUM(total)' }],
}])
check('an empty search matches everything', matchesMetric(row, '  '), true)
check('search reads the name', matchesMetric(row, 'REVEN'), true)
check('the label', matchesMetric(row, 'net'), true)
check('the expression', matchesMetric(row, 'sum(total)'), true)
check('and the table it is defined on', matchesMetric(row, 'sales.orders'), true)
check('and does not match what is not there', matchesMetric(row, 'churn'), false)

// ── the closed panel's one line ───────────────────────────────────────────
check('nothing yet says so', metricSummary([]), 'No metrics yet')
check('a healthy layer names what it measures',
      metricSummary(collectMetrics([
        host('sales.orders', [['revenue', 'SUM(t)'], ['order_count', 'COUNT(id)']]),
      ])),
      '2 metrics — order_count, revenue')
check('and a problem outranks the list of names',
      metricSummary(collectMetrics(collision)),
      '2 metrics — 2 need attention')
check('one problem reads as one',
      metricSummary(collectMetrics([
        { table: 'a.x', metrics: [{ name: 'revenue', expression: 'SUM(nope)', valid: false, issue: 'no such column' }] },
      ])),
      '1 metric — 1 needs attention')

console.log(failures === 0 ? '\nall metric checks passed' : `\n${failures} failed`)
// A throw rather than `process.exit`: the same non-zero exit for npm, and no
// `@types/node` for a file the app's own tsconfig type-checks.
if (failures > 0) throw new Error(`${failures} test(s) failed`)
