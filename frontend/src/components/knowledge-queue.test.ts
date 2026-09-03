/**
 * The badge that says curation work is waiting.
 *
 * `npm run test:queue` — Node runs this file directly.
 *
 * Everything here fails quietly by construction: a badge is a number nobody
 * checks against the list behind it, so the cases that matter are the ones
 * where being nearly right is worse than being absent — a zero drawn as a
 * badge, a flag counted twice because it appears in two feeds, an order that
 * reshuffles under the cursor.
 */
import {
  badge, byUrgency, forConnection, queueTone, toneOf, totalWaiting, waiting,
} from './knowledge-queue.ts'
import type { QueueRow } from './knowledge-queue.ts'

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

function row(over: Partial<QueueRow> = {}): QueueRow {
  return { connectionId: 'a', name: 'sales', reviews: 0, suggestions: 0, ...over }
}

console.log('\n— one connection —')
check('an empty queue is zero', waiting(row()), 0)
check('flags and backlog add up', waiting(row({ reviews: 2, suggestions: 3 })), 5)

console.log('\n— the badge —')
check('nothing waiting shows no badge', badge(0), undefined)
check('a negative count cannot draw one either', badge(-1), undefined)
check('one is worth showing', badge(1), '1')
check('ninety-nine still counts', badge(99), '99')
check('past that it stops', badge(100), '99+')
check('well past that it still stops', badge(4210), '99+')

console.log('\n— across connections —')
const rows = [
  row({ connectionId: 'a', name: 'sales', reviews: 3, suggestions: 1 }),
  row({ connectionId: 'b', name: 'Aurora Coffee', reviews: 0, suggestions: 2 }),
  row({ connectionId: 'c', name: 'warehouse' }),
]
check('the total is every connection', totalWaiting(rows), 6)
check('an empty list totals zero', totalWaiting([]), 0)
check('one connection can be asked about', forConnection(rows, 'b'), 2)
check('a quiet one shows nothing rather than zero', forConnection(rows, 'c'), undefined)
check('an unknown id is not an error', forConnection(rows, 'nope'), undefined)

console.log('\n— the order —')
check(
  'busy first, then alphabetical within each half',
  byUrgency(rows).map((r) => r.name),
  ['Aurora Coffee', 'sales', 'warehouse'],
)
check(
  'resolving a flag does not reshuffle the busy half',
  byUrgency([
    row({ connectionId: 'a', name: 'sales', reviews: 1 }),
    row({ connectionId: 'b', name: 'Aurora Coffee', reviews: 9 }),
  ]).map((r) => r.name),
  ['Aurora Coffee', 'sales'],
)
check('the input is not mutated', rows.map((r) => r.name), ['sales', 'Aurora Coffee', 'warehouse'])

console.log('\n— what colour it is —')
check('nothing waiting draws no alarm', toneOf(row()), 'neutral')
check('a backlog is attention, not a fault', toneOf(row({ suggestions: 9 })), 'amber')
check('a flag someone raised is a fault', toneOf(row({ reviews: 1 })), 'red')
check(
  'a flag outranks any number of suggestions',
  toneOf(row({ reviews: 1, suggestions: 40 })),
  'red',
)
check('a quiet workspace is neutral', queueTone([row(), row({ connectionId: 'b' })]), 'neutral')
check('no connections at all is neutral', queueTone([]), 'neutral')
check(
  'suggestions anywhere and flags nowhere is amber',
  queueTone([row({ suggestions: 3 }), row({ connectionId: 'b' })]),
  'amber',
)
check(
  'one flag on one connection reddens the whole badge',
  queueTone([row({ suggestions: 30 }), row({ connectionId: 'b', reviews: 1 })]),
  'red',
)

console.log(failures === 0 ? '\nall passed' : `\n${failures} failed`)
// `throw`, not `process.exit`: `@types/node` is not a dependency here, and
// adding it would put `process` in scope for the whole application. Every
// other DOM-free test ends the same way.
if (failures > 0) throw new Error(`${failures} test(s) failed`)
