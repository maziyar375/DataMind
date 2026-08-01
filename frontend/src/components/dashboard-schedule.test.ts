/**
 * The refresh rule, exercised against a clock we control.
 *
 * `npm run test:schedule` — Node runs this file directly (type stripping, no
 * bundler, no test framework, no new dependency). The frontend has no test
 * runner and this is not an argument for adding one: it is one pure function
 * whose two failure modes are "a background tab hammers the customer's
 * database" and "a 30-second tile silently shows ten-minute-old numbers", so
 * it gets checked rather than assumed.
 */
import { dueTileIds, type SchedulableTile } from './dashboard-schedule.ts'

const NOW = Date.parse('2026-08-01T12:00:00Z')
const ago = (seconds: number) => ({
  computed_at: new Date(NOW - seconds * 1000).toISOString(),
})
const tile = (id: string, interval: number, type = 'CHART'): SchedulableTile => ({
  id,
  tile_type: type,
  effective_refresh_interval_seconds: interval,
})

// No `node:assert`, no `process`: keeping this file free of Node's types is
// what lets it sit under `src/` and be type-checked with everything else,
// without adding @types/node to a frontend that has no other use for it.
let failures = 0
function check(name: string, actual: string[], expected: string[]): void {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures += 1
  console.log(
    ok
      ? `ok    ${name}`
      : `FAIL  ${name}\n        got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`,
  )
}

check(
  'a tile that has never loaded is due at once',
  dueTileIds([tile('a', 30)], {}, NOW),
  ['a'],
)
check(
  'inside its interval a tile is not due',
  dueTileIds([tile('a', 30)], { a: ago(29) }, NOW),
  [],
)
check(
  'at its interval it is',
  dueTileIds([tile('a', 30)], { a: ago(30) }, NOW),
  ['a'],
)
check(
  'a manual tile is never due on a tick, however old',
  dueTileIds([tile('a', 0)], { a: ago(86_400) }, NOW),
  [],
)
check(
  'a TEXT tile computes nothing',
  dueTileIds([tile('a', 30, 'TEXT')], {}, NOW),
  [],
)
check(
  'two rates on one dashboard expire independently',
  dueTileIds(
    [tile('quick', 30), tile('slow', 3600)],
    { quick: ago(60), slow: ago(60) },
    NOW,
  ),
  ['quick'],
)
check(
  'tiles due in the same second coalesce into one request',
  dueTileIds(
    [tile('a', 30), tile('b', 30), tile('c', 300)],
    { a: ago(31), b: ago(31), c: ago(31) },
    NOW,
  ),
  ['a', 'b'],
)
check(
  'an hour hidden catches up once, not once per missed interval',
  dueTileIds([tile('a', 30)], { a: ago(3600) }, NOW),
  ['a'],
)
check(
  'an unreadable timestamp refreshes rather than freezing forever',
  dueTileIds([tile('a', 30)], { a: { computed_at: 'not-a-date' } }, NOW),
  ['a'],
)

if (failures > 0) {
  // A thrown error is the non-zero exit; `process` would need Node's types.
  throw new Error(`${failures} scheduling checks failed`)
}
console.log('\nall passed')
