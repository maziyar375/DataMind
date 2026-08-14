/**
 * When the layer editor is allowed to say "you are pointed somewhere else".
 *
 * `npm run test:drift` — Node runs this file directly (type stripping, no
 * bundler, no test framework, no new dependency), the arrangement
 * `report-readiness.test.ts` uses.
 *
 * The cases that matter are the ones where the message must *not* appear: a
 * layer with one surviving table is ordinary drift, and so is a re-sync that
 * dropped every table out of one schema while another still matches. Both look
 * alike from a distance and only one has a single-edit cause.
 */
import { explainRekey, rekeyDrift } from './semantic-drift.ts'
import type { DriftEntity } from './semantic-drift.ts'

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

function entity(table: string, valid = false): DriftEntity {
  return { table, valid }
}

function snapshot(...names: string[]): { table: string }[] {
  return names.map((table) => ({ table }))
}

// ── the Oracle re-key: the connection's username changed ──────────────────
const rekeyed = rekeyDrift(
  [entity('hr.employees'), entity('hr.departments'), entity('hr.salaries')],
  snapshot('scott.employees', 'scott.departments', 'scott.salaries'),
)
check('a re-key is detected', rekeyed, {
  missing: 3,
  was: ['hr'],
  now: ['scott'],
})

check(
  'oracle names the username as the cause',
  explainRekey(rekeyed!, 'oracle').includes('a schema is a database user'),
  true,
)
check(
  'oracle says which schema to go back to',
  explainRekey(rekeyed!, 'oracle').includes('Point the connection back at hr'),
  true,
)
check(
  'another engine blames the allowlist instead',
  explainRekey(rekeyed!, 'postgres').includes("schema allowlist changed"),
  true,
)
check(
  'no engine claims the work was deleted',
  explainRekey(rekeyed!, 'oracle').includes('nothing is deleted'),
  true,
)

// ── the cases that must stay quiet ────────────────────────────────────────
check(
  'one surviving entity is drift, not a re-key',
  rekeyDrift(
    [entity('hr.employees', true), entity('hr.departments')],
    snapshot('hr.employees'),
  ),
  null,
)

check(
  'a shared schema name is drift, not a re-key',
  rekeyDrift(
    [entity('sales.orders'), entity('marts.revenue')],
    snapshot('sales.customers'),
  ),
  null,
)

check(
  'an empty layer says nothing',
  rekeyDrift([], snapshot('sales.orders')),
  null,
)

check(
  'an empty snapshot says nothing',
  rekeyDrift([entity('sales.orders')], []),
  null,
)

check(
  'an unqualified entity name says nothing',
  rekeyDrift([entity('orders')], snapshot('sales.orders')),
  null,
)

// ── multi-schema wording ──────────────────────────────────────────────────
const wide = rekeyDrift(
  [entity('a.one'), entity('b.two'), entity('c.three')],
  snapshot('x.one', 'y.two'),
)
check('every schema is reported, sorted', wide, {
  missing: 3,
  was: ['a', 'b', 'c'],
  now: ['x', 'y'],
})
check(
  'three schemas read as a list',
  explainRekey(wide!, 'postgres').includes('written against a, b and c'),
  true,
)
check(
  'two schemas read as a pair',
  explainRekey(wide!, 'postgres').includes('returned x and y'),
  true,
)

// ── case folding: Oracle upper-cases what a document lower-cases ──────────
check(
  'schema names compare case-insensitively',
  rekeyDrift([entity('hr.employees')], snapshot('HR.EMPLOYEES')),
  null,
)

console.log(failures === 0 ? '\nall passed' : `\n${failures} failed`)
if (failures > 0) throw new Error(`${failures} test(s) failed`)
