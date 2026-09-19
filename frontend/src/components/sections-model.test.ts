/**
 * The Sections screen's arithmetic.
 *
 * `npm run test:sections` — Node runs this file directly.
 *
 * The cases that matter are the quiet ones: a table in two places or in none,
 * a size that disagrees with `retrieve`, a name the server will refuse after
 * the person has finished editing.
 */
import {
  applySplit, fitOf, formatChars, homeOf, moveTable, nameProblem, newName, problems,
  reorder, sameSet, sizeOf, toDrafts, toWrite, unassignedOf,
} from './sections-model.ts'
import type { SectionDraft } from './sections-model.ts'

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

const CATALOG = ['public.orders', 'public.order_items', 'public.customers', 'public.tags', 'public.audit']
const WEIGHTS = new Map([
  ['public.orders', 300], ['public.order_items', 460], ['public.customers', 220],
  ['public.tags', 100], ['public.audit', 5000],
])

function drafts(): SectionDraft[] {
  return toDrafts([
    { id: 'a', name: 'Sales', description: 'Orders.', tables: ['public.orders', 'public.order_items'] },
    { id: 'b', name: 'People', description: '', tables: ['public.customers'] },
  ])
}

console.log('\n— every table in exactly one place —')
check('the bucket is what the sections leave, in catalog order',
  unassignedOf(drafts(), CATALOG), ['public.tags', 'public.audit'])
const moved = moveTable(drafts(), 'public.orders', 'b')
check('a move takes the table out of where it was',
  moved.map((d) => d.tables), [['public.order_items'], ['public.customers', 'public.orders']])
check('and so the bucket does not change', unassignedOf(moved, CATALOG), ['public.tags', 'public.audit'])
const toBucket = moveTable(drafts(), 'public.orders', '__unassigned__')
check('moving to the bucket unplaces it',
  unassignedOf(toBucket, CATALOG), ['public.orders', 'public.tags', 'public.audit'])
const fromBucket = moveTable(drafts(), 'public.tags', 'a')
check('moving from the bucket places it once',
  fromBucket.flatMap((d) => d.tables).filter((t) => t === 'public.tags').length, 1)
check('a move to where it already is changes nothing',
  moveTable(drafts(), 'public.orders', 'a')[0].tables, ['public.orders', 'public.order_items'])
check('its home is found', homeOf(moved, 'public.orders')?.name, 'People')
check('a bucketed table has no home', homeOf(drafts(), 'public.tags'), null)

console.log('\n— size, in the units retrieve decides with —')
check('a section weighs its members', sizeOf(['public.orders', 'public.tags'], WEIGHTS), 400)
check('a member the snapshot lost weighs nothing', sizeOf(['public.orders', 'public.gone'], WEIGHTS), 300)
check('at the budget it fits', fitOf(['public.orders'], WEIGHTS, 300), 'FITS')
check('over it, it does not', fitOf(['public.orders', 'public.tags'], WEIGHTS, 300), 'TOO_LARGE')
check('no members is empty', fitOf([], WEIGHTS, 300), 'EMPTY')
check('only missing members is empty too', fitOf(['public.gone'], WEIGHTS, 300), 'EMPTY')
check('small numbers are exact', formatChars(640), '640')
check('thousands to one decimal', formatChars(9_240), '9.2k')
check('a round thousand drops the decimal', formatChars(12_000), '12k')
check('big numbers lose the decimal', formatChars(190_400), '190k')

console.log('\n— names the server would refuse —')
check('a plain name is fine', nameProblem('Sales'), null)
check('blank is not', nameProblem('   ') !== null, true)
check('a comma splits the model reply', nameProblem('Sales, marketing') !== null, true)
check('the bucket is reserved', nameProblem('unassigned') !== null, true)
check('the router’s NONE is reserved', nameProblem('None') !== null, true)
check('too long is refused', nameProblem('x'.repeat(61)) !== null, true)
const clash = toDrafts([
  { id: 'a', name: 'Sales', description: '', tables: [] },
  { id: 'b', name: ' sales ', description: '', tables: [] },
])
check('two names that differ only in case clash on the second', [...problems(clash).keys()], ['b'])
check('a clean set has no problems', problems(drafts()).size, 0)
check('a new name avoids the taken ones', newName(toDrafts([
  { id: 'a', name: 'New section', description: '', tables: [] },
  { id: 'b', name: 'new section 2', description: '', tables: [] },
])), 'New section 3')

console.log('\n— what a save sends, and whether there is anything to save —')
check('ids ride along, draft keys do not', toWrite(drafts())[0],
  { id: 'a', name: 'Sales', description: 'Orders.', tables: ['public.orders', 'public.order_items'] })
const fresh = toDrafts([{ id: null, name: 'X', description: '', tables: [] }])
check('a new section sends no id', 'id' in toWrite(fresh)[0], false)
check('a trailing space is not an edit',
  sameSet(drafts(), drafts().map((d) => ({ ...d, name: `${d.name} ` }))), true)
check('a moved table is', sameSet(drafts(), moved), false)
check('so is a reorder', sameSet(drafts(), reorder(drafts(), 'b', -1)), false)
check('reordering past the end does nothing', reorder(drafts(), 'b', 1).map((d) => d.key), ['a', 'b'])

console.log('\n— splitting a section —')
const big = toDrafts([
  { id: 'a', name: 'Ops', description: 'Everything.', tables: ['public.orders', 'public.order_items', 'public.tags'] },
  { id: 'b', name: 'Order', description: '', tables: ['public.customers'] },
])
const split = applySplit(big, 'a', [
  { name: 'Order', description: 'Orders.', tables: ['public.orders', 'public.order_items'] },
  { name: 'Tag', description: 'Tags.', tables: ['public.tags'] },
], [])
check('the first part keeps the original’s id and name',
  [split[0].id, split[0].name, split[0].description], ['a', 'Ops', 'Everything.'])
check('the others are new, with names nobody has', split.slice(1, 2).map((d) => [d.id, d.name]),
  [[null, 'Tag']])
check('neighbours are untouched', split[2].name, 'Order')
check('no table is lost or doubled', unassignedOf(split, CATALOG), ['public.audit'])
const withLeft = applySplit(big, 'a', [
  { name: 'Order', description: 'Orders.', tables: ['public.orders', 'public.order_items'] },
], ['public.tags'])
check('what the proposal could not place stays in the original',
  withLeft.map((d) => [d.id, d.name, d.tables]), [
    ['a', 'Ops', ['public.tags']],
    [null, 'Order 2', ['public.orders', 'public.order_items']],
    ['b', 'Order', ['public.customers']],
  ])

console.log(failures === 0 ? '\nall passed' : `\n${failures} failed`)
// `throw`, not `process.exit` — see the note at the end of every other suite.
if (failures > 0) throw new Error(`${failures} test(s) failed`)
