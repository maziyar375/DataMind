/**
 * The table rules, exercised without a DOM.
 *
 * `npm run test:format` — Node runs this file directly (type stripping, no
 * bundler, no test framework, no new dependency), exactly like
 * `dashboard-schedule.test.ts`. These rules get checked rather than assumed
 * because every way they can be wrong is quiet: a column the query added goes
 * missing from the tile, a descending sort opens on a screen of blanks, a
 * format turns a value into `NaN`. Nothing throws; it just reads wrong.
 */
import {
  formatCell, resolveColumns, sortRows,
  type ResultTableConfig, type TableColumns,
} from './table-format.ts'

const spec: TableColumns = {
  columns: [
    { name: 'status', semantic_type: 'nominal' },
    { name: 'total', semantic_type: 'quantitative' },
    { name: 'region', semantic_type: 'nominal' },
  ],
}

// No `node:assert`, no `process`: keeping this file free of Node's types is
// what lets it sit under `src/` and be type-checked with everything else.
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

const names = (config?: ResultTableConfig | null) =>
  resolveColumns(spec, config).map((column) => column.name)

// ── which columns, in what order ─────────────────────────────────────────
check('no config is the query’s own columns, in its own order', names(null), [
  'status', 'total', 'region',
])
check('an empty column list is also "as returned"', names({ columns: [] }), [
  'status', 'total', 'region',
])
check(
  'the config decides the order',
  names({ columns: [{ name: 'total' }, { name: 'region' }, { name: 'status' }] }),
  ['total', 'region', 'status'],
)
check(
  'a hidden column is dropped',
  names({ columns: [{ name: 'status' }, { name: 'total', hidden: true }] }),
  ['status', 'region'],
)
check(
  'a column the query added is appended, not lost',
  names({ columns: [{ name: 'total' }, { name: 'status' }] }),
  ['total', 'status', 'region'],
)
check(
  'a configured column the result no longer has is skipped, not a hole',
  names({ columns: [{ name: 'gone' }, { name: 'total' }] }),
  ['total', 'status', 'region'],
)
check(
  'the value still comes from the right column after a reorder',
  resolveColumns(spec, { columns: [{ name: 'region' }, { name: 'status' }] })
    .map((column) => column.index),
  [2, 0, 1],
)

// ── headings and alignment ───────────────────────────────────────────────
check(
  'a label renames the heading; an empty one blanks it deliberately',
  resolveColumns(spec, {
    columns: [{ name: 'status', label: 'Order status' }, { name: 'total', label: '' }],
  }).map((column) => column.heading),
  ['Order status', '', 'region'],
)
check(
  'numbers are right-aligned unless told otherwise',
  resolveColumns(spec, { columns: [{ name: 'total', align: 'left' }] })
    .map((column) => column.align),
  ['left', 'left', 'left'],
)
check(
  'auto alignment still follows the semantic type',
  resolveColumns(spec, null).map((column) => column.align),
  ['left', 'right', 'left'],
)

// ── sorting ──────────────────────────────────────────────────────────────
const rows: unknown[][] = [
  ['paid', 30, 'north'],
  ['new', null, 'south'],
  ['shipped', 10, 'east'],
]
const sorted = (config: ResultTableConfig) =>
  sortRows(rows, spec, config).map((row) => row[0])

check('no sort column leaves the query’s order', sortRows(rows, spec, {}), rows)
check('ascending, and nulls last', sorted({ sort_column: 'total' }), [
  'shipped', 'paid', 'new',
])
check(
  'descending, and nulls still last — not first',
  sorted({ sort_column: 'total', sort_direction: 'desc' }),
  ['paid', 'shipped', 'new'],
)
check(
  'a sort column the result lost changes nothing',
  sortRows(rows, spec, { sort_column: 'gone' }),
  rows,
)
check('the caller’s rows are never reordered in place', rows.map((row) => row[0]), [
  'paid', 'new', 'shipped',
])

// ── cells ────────────────────────────────────────────────────────────────
check('null reads as an em dash, not "null"', formatCell(null), '—')
check('auto groups an integer', formatCell(1234567), '1,234,567')
check('auto trims a long decimal', formatCell(1234.5678), '1,234.57')
check('integer rounds', formatCell(1234.56, 'integer'), '1,235')
check('decimal pads to two places', formatCell(1234.5, 'decimal'), '1,234.50')
check('percent reads a fraction as a rate', formatCell(0.42, 'percent'), '42%')
check('as-is leaves a number alone', formatCell(1234.5, 'text'), '1234.5')
check(
  'a number format on text shows the text rather than NaN',
  formatCell('pending', 'decimal'),
  'pending',
)
check('a numeric string still formats', formatCell('1234.5', 'decimal'), '1,234.50')

if (failures > 0) {
  // A thrown error is the non-zero exit; `process` would need Node's types.
  throw new Error(`${failures} table-format checks failed`)
}
console.log('\nall passed')
