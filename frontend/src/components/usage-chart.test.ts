/**
 * The usage chart's spec, and the sentences beside it.
 *
 * `npm run test:usage` — Node runs this file directly.
 *
 * Both halves of that module fail quietly. A chart drawn from a wrong reshape
 * is still a chart and nobody can tell by looking; a total that has quietly
 * stopped saying how partial it is looks exactly like a whole one. So the
 * cases below are the ones where being nearly right is worse than being
 * absent — a zero printed for a measurement nobody took, a model breakdown that
 * does not add up to its total, a stack that reorders between renders.
 */
import {
  formatTokens, modelRows, TOKEN_SERIES, UNRECORDED_MODEL, usageSpec, usageTotals,
  WINDOW_DAYS, windowSince,
} from './usage-chart.ts'
import { PALETTES } from './palette.ts'
import type { UsageBucket, UsageModel, UsageSeries } from '../api/types.ts'

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

function bucket(over: Partial<UsageBucket> = {}): UsageBucket {
  return { day: '2026-09-01', prompt_tokens: 0, completion_tokens: 0, runs: 1, ...over }
}

function model(over: Partial<UsageModel> = {}): UsageModel {
  return { model: 'gpt-4o-mini', prompt_tokens: 0, completion_tokens: 0, runs: 1, unmeasured: 0, ...over }
}

function series(over: Partial<UsageSeries> = {}): UsageSeries {
  return {
    actor_id: 'u1',
    actor: 'Ali Rahimi',
    prompt_tokens: 0,
    completion_tokens: 0,
    runs: 0,
    unmeasured: 0,
    buckets: [],
    models: [],
    ...over,
  }
}

/** The spec's long-form rows, which is what every reshape claim is about. */
function rows(spec: Record<string, unknown>): unknown[] {
  return (spec.data as { values: unknown[] }).values
}

function encoding(spec: Record<string, unknown>): Record<string, Record<string, unknown>> {
  return spec.encoding as Record<string, Record<string, unknown>>
}

console.log('\n— an empty window —')
const empty = usageSpec([])
check('an empty bucket list is a spec, not a throw', rows(empty), [])
check('and still declares its encoding', Object.keys(encoding(empty)).sort(), [
  'color', 'order', 'x', 'y',
])
check(
  'and still declares the segments, so the legend does not appear on the first bar',
  (encoding(empty).color.scale as { domain: string[] }).domain,
  ['Input', 'Output'],
)
check('an empty window has no categories', (empty.usermeta as {
  datamind: { categories: number }
}).datamind.categories, 0)

console.log('\n— the reshape —')
const twoDays = usageSpec([
  bucket({ day: '2026-09-01', prompt_tokens: 900, completion_tokens: 100 }),
  bucket({ day: '2026-09-02', prompt_tokens: 40, completion_tokens: 0 }),
])
check('one row per day per segment', rows(twoDays).length, 4)
check('every field the encoding names is on every row', rows(twoDays), [
  { day: '2026-09-01', series: 'Input', tokens: 900, order: 0 },
  { day: '2026-09-01', series: 'Output', tokens: 100, order: 1 },
  { day: '2026-09-02', series: 'Input', tokens: 40, order: 0 },
  { day: '2026-09-02', series: 'Output', tokens: 0, order: 1 },
])
check(
  'a day with no output still draws its input',
  rows(usageSpec([bucket({ prompt_tokens: 40, completion_tokens: 0 })])),
  [
    { day: '2026-09-01', series: 'Input', tokens: 40, order: 0 },
    { day: '2026-09-01', series: 'Output', tokens: 0, order: 1 },
  ],
)
check(
  'a quiet day keeps both segments, so the legend does not flicker',
  rows(usageSpec([bucket()])).length,
  2,
)
check('the categories are the days, not the rows', (twoDays.usermeta as {
  datamind: { categories: number }
}).datamind.categories, 2)

console.log('\n— the order of the stack —')
check(
  'the colour domain is stated rather than read off the data',
  (encoding(twoDays).color.scale as { domain: string[] }).domain,
  ['Input', 'Output'],
)
check('and an order channel fixes the segments', encoding(twoDays).order, {
  field: 'order', type: 'quantitative', sort: 'ascending',
})
check(
  'the same buckets build the same spec twice',
  JSON.stringify(usageSpec([bucket({ prompt_tokens: 5, completion_tokens: 6 })])),
  JSON.stringify(usageSpec([bucket({ prompt_tokens: 5, completion_tokens: 6 })])),
)
check('input is the first segment and stays there', TOKEN_SERIES.map((s) => s.label), [
  'Input', 'Output',
])

console.log('\n— the colours —')
check(
  'no palette leaves the range to the renderer, which knows the theme',
  'range' in (encoding(usageSpec([bucket()])).color.scale as object),
  false,
)
check(
  'a palette pins the first two categorical slots, in series order',
  (encoding(usageSpec([bucket()], PALETTES.dark)).color.scale as { range: string[] }).range,
  [PALETTES.dark.category[0], PALETTES.dark.category[1]],
)
check(
  'and the light theme pins its own two, not the dark ones',
  (encoding(usageSpec([bucket()], PALETTES.light)).color.scale as { range: string[] }).range,
  [PALETTES.light.category[0], PALETTES.light.category[1]],
)
check(
  'no literal colour is in the module',
  (encoding(usageSpec([bucket()], PALETTES.dark)).color.scale as { range: string[] })
    .range.every((hex) => PALETTES.dark.category.includes(hex)),
  true,
)

console.log('\n— the axes —')
check(
  'the day axis is bucketed in UTC, the way the query grouped it',
  encoding(twoDays).x.timeUnit,
  'utcyearmonthdate',
)
check('tokens stack from zero', encoding(twoDays).y.stack, 'zero')
check('a title is omitted rather than drawn empty', 'title' in twoDays, false)
check(
  'and drawn when there is one',
  usageSpec([], null, { title: 'Last 30 days' }).title,
  'Last 30 days',
)

console.log('\n— a total nobody has to qualify —')
const whole = usageTotals(series({
  runs: 12, prompt_tokens: 1_000_000, completion_tokens: 284_301,
}))
check('the tokens add up', whole.totalTokens, 1_284_301)
check('and are grouped for reading', whole.tokens, '1,284,301')
check('nothing is unmeasured, so nothing is said about it', whole.unmeasuredNote, null)
check('and it is not the empty window', whole.empty, false)
check('no price is anywhere in the summary', Object.keys(whole).some((key) => /cost/i.test(key)), false)

console.log('\n— an empty window says so —')
const nothing = usageTotals(series())
check('no operations is empty', nothing.empty, true)
check('and no warning about an absence nobody asked about', nothing.unmeasuredNote, null)

console.log('\n— what is unmeasured —')
const partly = usageTotals(series({
  runs: 12, prompt_tokens: 900, completion_tokens: 100, unmeasured: 3,
}))
check(
  'the count is stated, not a flag',
  partly.unmeasuredNote,
  '3 of 12 operations reported no token count, so every figure here understates.',
)
check('the figure it understates is still shown', partly.tokens, '1,000')
// A window holding one run is the ordinary case on a quiet installation, and
// the real one this was first pointed at held exactly that. "1 of 1 operation
// reported no token count" is arithmetically right and is not a sentence.
check('a scope of one is written as one, not as a ratio of itself', usageTotals(series({
  runs: 1, prompt_tokens: 5, unmeasured: 1,
})).unmeasuredNote, 'This operation reported no token count, so the figures here understate.')
check(
  'a scope that measured nothing at all shows no token figure, and never a zero',
  usageTotals(series({ runs: 4, unmeasured: 4 })).tokens,
  null,
)
check(
  'but genuinely free work is a measurement, and reads as one',
  usageTotals(series({ runs: 4 })).tokens,
  '0',
)

console.log('\n— by model —')
const split = modelRows(series({
  runs: 5, prompt_tokens: 9_000, completion_tokens: 1_000,
  models: [
    model({ model: 'large', prompt_tokens: 6_000, completion_tokens: 200, runs: 2 }),
    model({ model: 'small', prompt_tokens: 2_990, completion_tokens: 750, runs: 2 }),
    model({ model: '', prompt_tokens: 10, completion_tokens: 50, runs: 1 }),
  ],
}))
check('one row per model, in the order the server ranked them', split.map((row) => row.key), [
  'large', 'small', '',
])
check('each row is written for reading', split[0], {
  key: 'large', label: 'large', recorded: true, totalTokens: 6_200,
  tokens: '6,200', input: '6,000', output: '200', share: '62%', runs: 2,
})
check(
  'the rows add up to the scope, which is what makes the split trustworthy',
  split.reduce((sum, row) => sum + row.totalTokens, 0),
  10_000,
)
check('a run with no recorded model is named, not dropped', [split[2].label, split[2].recorded], [
  UNRECORDED_MODEL, false,
])
check('a share too small to round up is not written as nothing', split[2].share, '< 1%')
check(
  'a model whose every run went unmeasured shows no figure, and never a zero',
  modelRows(series({ runs: 2, unmeasured: 2, models: [model({ runs: 2, unmeasured: 2 })] }))[0].tokens,
  null,
)
check(
  'a scope that measured nothing has no whole to take a share of',
  modelRows(series({ runs: 2, unmeasured: 2, models: [model({ runs: 2, unmeasured: 2 })] }))[0].share,
  null,
)
check('no models is no rows', modelRows(series()), [])

console.log('\n— how a count is written —')
check('under a thousand, as it is', formatTokens(999), '999')
check('at a thousand, grouped', formatTokens(1000), '1,000')
check('and every group after it', formatTokens(1_284_301), '1,284,301')
check('zero is zero', formatTokens(0), '0')

console.log('\n— the window —')
check(
  'a week is seven whole buckets, today and the six before it',
  windowSince(7, new Date('2026-09-13T09:40:00Z')),
  '2026-09-07T00:00:00.000Z',
)
check(
  'a day is today, from its own midnight',
  windowSince(1, new Date('2026-09-13T23:59:59Z')),
  '2026-09-13T00:00:00.000Z',
)
check(
  'the hour it is asked at does not move the boundary',
  windowSince(30, new Date('2026-09-13T00:00:01Z')),
  windowSince(30, new Date('2026-09-13T23:59:59Z')),
)
check(
  'and it steps back over a month end',
  windowSince(30, new Date('2026-09-13T09:40:00Z')),
  '2026-08-15T00:00:00.000Z',
)
check('the windows the screen offers', [...WINDOW_DAYS], [7, 30, 90])

console.log(failures === 0 ? '\nall passed' : `\n${failures} failed`)
// `throw`, not `process.exit`: `@types/node` is not a dependency here, and
// adding it would put `process` in scope for the whole application. Every
// other DOM-free test ends the same way.
if (failures > 0) throw new Error(`${failures} test(s) failed`)
