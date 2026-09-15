/**
 * The token usage screen's arithmetic: slots, ticks, labels, and the
 * sentences beside the figures.
 *
 * `npm run test:usage` — Node runs this file directly.
 *
 * Everything here fails quietly. A timeline laid from the sparse buckets
 * alone still draws, and ends at the last token instead of now; a label
 * written in the machine's zone still reads, and names the wrong hour; a
 * total that has stopped saying how partial it is looks exactly like a whole
 * one. So the cases below are the ones where being nearly right is worse than
 * being absent. Every clock in them is explicit — no case depends on the zone
 * of the machine running it.
 */
import {
  bucketLabel, denseSlots, formatCompact, formatInstant, formatShare, formatSlot,
  formatTokens, fromLocalInput, isPeriodKey, localOffsetMinutes, modelRows, presetRange,
  PRESETS, rankedRow, scopeView, splitModelName, timeTicks, toLocalInput, UNRECORDED_MODEL,
  usageTotals, valueTicks,
} from './usage-chart.ts'
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

const TEHRAN = 210
const HOUR = 3_600_000
const DAY = 24 * HOUR

function bucket(over: Partial<UsageBucket> = {}): UsageBucket {
  return { start: '2026-09-13T00:00:00Z', prompt_tokens: 0, completion_tokens: 0, runs: 1, ...over }
}

function model(over: Partial<UsageModel> = {}): UsageModel {
  return {
    model: 'gpt-4o-mini', prompt_tokens: 0, completion_tokens: 0, runs: 1, unmeasured: 0,
    buckets: [], ...over,
  }
}

function series(over: Partial<UsageSeries> = {}): UsageSeries {
  return {
    actor_id: 'u1',
    actor: 'Ali Rahimi',
    prompt_tokens: 0,
    completion_tokens: 0,
    runs: 0,
    unmeasured: 0,
    since: '2026-09-13T00:00:00Z',
    until: '2026-09-13T06:00:00Z',
    bucket_seconds: 3600,
    buckets: [],
    models: [],
    ...over,
  }
}

console.log('\n— a total nobody has to qualify —')
const whole = usageTotals(series({ runs: 12, prompt_tokens: 1_000_000, completion_tokens: 284_301 }))
check('the tokens add up', whole.totalTokens, 1_284_301)
check('and are grouped for reading', whole.tokens, '1,284,301')
check('nothing is unmeasured, so nothing is said about it', whole.unmeasuredNote, null)
check('input is its share of the whole', whole.inputShare?.toFixed(4), '0.7786')
check('tokens per operation are over every measured run', whole.perOperation, 107_025)
check('and it is not the empty window', whole.empty, false)
check('no price is anywhere in the summary', Object.keys(whole).some((key) => /cost|price/i.test(key)), false)

console.log('\n— an empty window says so —')
const nothing = usageTotals(series())
check('no operations is empty', nothing.empty, true)
check('with no warning about an absence nobody asked about', nothing.unmeasuredNote, null)
check('and no split of nothing', [nothing.inputShare, nothing.perOperation], [null, null])

console.log('\n— what is unmeasured —')
const partly = usageTotals(series({ runs: 12, prompt_tokens: 900, completion_tokens: 100, unmeasured: 3 }))
check(
  'the count is stated, not a flag',
  partly.unmeasuredNote,
  '3 of 12 operations reported no token count, so every figure here understates.',
)
check('the figure it understates is still shown', partly.tokens, '1,000')
check('and the average is over the nine that were measured', partly.perOperation, 111)
check('a scope of one is written as one, not as a ratio of itself', usageTotals(series({
  runs: 1, prompt_tokens: 5, unmeasured: 1,
})).unmeasuredNote, 'This operation reported no token count, so the figures here understate.')
check(
  'a scope that measured nothing at all shows no token figure, and never a zero',
  usageTotals(series({ runs: 4, unmeasured: 4 })).tokens,
  null,
)
check(
  'nor an average of nothing',
  usageTotals(series({ runs: 4, unmeasured: 4 })).perOperation,
  null,
)
check('but genuinely free work is a measurement, and reads as one', usageTotals(series({ runs: 4 })).tokens, '0')

console.log('\n— numbers —')
check('under a thousand, as it is', formatTokens(999), '999')
check('at a thousand, grouped', formatTokens(1000), '1,000')
check('and every group after it', formatTokens(1_284_301), '1,284,301')
check('an axis figure below a thousand is whole', formatCompact(950), '950')
check('one decimal below ten of a unit', formatCompact(1_250), '1.3k')
check('and none where it is round', formatCompact(2_000), '2k')
check('none above ten', formatCompact(40_000), '40k')
check('and millions', formatCompact(1_500_000), '1.5M')
check('a share rounds', formatShare(0.936), '94%')
check('a real share too small to round is not written as nothing', formatShare(0.004), '< 1%')
check('an actual zero is', formatShare(0), '0%')

console.log('\n— model names —')
check('the provider path is split from the model', splitModelName('openai/deepseek/deepseek-v4-flash'), {
  path: 'openai/deepseek/', name: 'deepseek-v4-flash',
})
check('a bare name has no path', splitModelName('gpt-4o-mini'), { path: '', name: 'gpt-4o-mini' })
check('a trailing slash is not an empty model', splitModelName('odd/'), { path: '', name: 'odd/' })
check('no recorded model is named, not blank', splitModelName(''), { path: '', name: UNRECORDED_MODEL })

console.log('\n— the model filter —')
const scoped = series({
  runs: 5, prompt_tokens: 9_000, completion_tokens: 1_000,
  buckets: [bucket({ prompt_tokens: 9_000, completion_tokens: 1_000, runs: 5 })],
  models: [
    model({ model: 'large', prompt_tokens: 6_000, completion_tokens: 200, runs: 2,
      buckets: [bucket({ prompt_tokens: 6_000, completion_tokens: 200, runs: 2 })] }),
    model({ model: 'small', prompt_tokens: 2_990, completion_tokens: 750, runs: 2 }),
    model({ model: '', prompt_tokens: 10, completion_tokens: 50, runs: 1 }),
  ],
})
check('all models is the whole scope', scopeView(scoped, null).figures.prompt_tokens, 9_000)
check('one model is that model, figures and buckets', [
  scopeView(scoped, 'large').figures.prompt_tokens,
  scopeView(scoped, 'large').buckets.length,
], [6_000, 1])
check('the unrecorded row is a model the filter can choose', scopeView(scoped, '').figures.runs, 1)
check(
  'a model the window does not hold is a zero view, not a throw',
  scopeView(scoped, 'gone'),
  { figures: { prompt_tokens: 0, completion_tokens: 0, runs: 0, unmeasured: 0 }, buckets: [] },
)

console.log('\n— ranked rows —')
const split = modelRows(scoped)
check('one row per model, in the order the server ranked them', split.map((row) => row.key), ['large', 'small', ''])
check('each row is written for reading', {
  tokens: split[0].tokens, input: split[0].input, output: split[0].output, share: split[0].share, runs: split[0].runs,
}, { tokens: '6,200', input: '6,000', output: '200', share: '62%', runs: 2 })
check(
  'the bar is the share, split input and output',
  [split[0].inputFraction, split[0].outputFraction],
  [0.6, 0.02],
)
check(
  'the rows add up to the scope, which is what makes the split trustworthy',
  split.reduce((sum, row) => sum + row.totalTokens, 0),
  10_000,
)
check('a share too small to round up is not written as nothing', split[2].share, '< 1%')
check(
  'a row whose every run went unmeasured shows no figure, and never a zero',
  rankedRow('quiet', { prompt_tokens: 0, completion_tokens: 0, runs: 2, unmeasured: 2 }, 100).tokens,
  null,
)
check(
  'with no whole there is no share to take',
  rankedRow('x', { prompt_tokens: 0, completion_tokens: 0, runs: 2, unmeasured: 2 }, 0).share,
  null,
)

console.log('\n— periods —')
check('the presets, shortest first', PRESETS.map((p) => p.key), ['1h', '6h', '24h', '7d', '30d', '90d'])
check('a preset is the period ending now', presetRange('6h', Date.parse('2026-09-13T10:02:00Z')), {
  since: '2026-09-13T04:02:00.000Z', until: '2026-09-13T10:02:00.000Z',
})
check('custom is a period', isPeriodKey('custom'), true)
check('and anything else in the address is not', [isPeriodKey('2w'), isPeriodKey(null)], [false, false])
check(
  'the offset sent is east-positive, the opposite of getTimezoneOffset',
  localOffsetMinutes({ getTimezoneOffset: () => -210 } as Date),
  210,
)
check('and Greenwich is zero, not minus zero', Object.is(localOffsetMinutes({ getTimezoneOffset: () => 0 } as Date), 0), true)

console.log('\n— the reader\'s clock —')
const late = Date.parse('2026-09-12T22:10:00Z')
check('a local input is written on the reader\'s clock', toLocalInput(late, TEHRAN), '2026-09-13T01:40')
check('and read back to the same instant', fromLocalInput('2026-09-13T01:40', TEHRAN), late)
check('a value that is not a time is refused', fromLocalInput('tomorrow', TEHRAN), null)
check('an instant is written on that clock too', formatInstant(late, TEHRAN), 'Sep 13, 01:40')
check('a day bar is its date', formatSlot(Date.parse('2026-09-12T20:30:00Z'), 86_400, TEHRAN), 'Sun, Sep 13')
check('a narrower bar is a range', formatSlot(Date.parse('2026-09-13T06:30:00Z'), 900, TEHRAN), 'Sep 13, 10:00–10:15')
check(
  'a range that closes at midnight ends at 24:00, never reading backwards',
  formatSlot(Date.parse('2026-09-13T14:30:00Z'), 21_600, TEHRAN),
  'Sep 13, 18:00–24:00',
)
check('the bar width, in words', [300, 900, 3600, 21_600, 86_400].map(bucketLabel), [
  '5-minute bars', '15-minute bars', 'Hourly bars', '6-hour bars', 'Daily bars',
])

console.log('\n— the timeline runs to its end, not to its last token —')
const slots = denseSlots(
  [bucket({ start: '2026-09-13T01:00:00Z', prompt_tokens: 40, completion_tokens: 4, runs: 2 })],
  '2026-09-13T00:00:00Z',
  '2026-09-13T05:20:00Z',
  3600,
)
check('every bucket of the window is a slot, the one in progress included', slots.length, 6)
check('the last slot holds the window\'s end', [
  slots[5].start <= Date.parse('2026-09-13T05:20:00Z'),
  slots[5].end > Date.parse('2026-09-13T05:20:00Z'),
], [true, true])
check('a bucket the server sent lands in its own slot', [slots[1].input, slots[1].output, slots[1].runs], [40, 4, 2])
check('and one it did not is a zero, which is a fact', [slots[4].input, slots[4].runs], [0, 0])
check('an empty or reversed window has no slots', denseSlots([], '2026-09-13T05:00:00Z', '2026-09-13T05:00:00Z', 300), [])

console.log('\n— the value axis —')
check('ticks are round numbers a person would write', valueTicks(55_830), { max: 60_000, ticks: [0, 20_000, 40_000, 60_000] })
check('and steps of 2.5 where they fit better', valueTicks(9_000), { max: 10_000, ticks: [0, 2_500, 5_000, 7_500, 10_000] })
check('an empty chart still has an axis, and no division by zero', valueTicks(0), { max: 1, ticks: [0] })

console.log('\n— the time axis —')
const dayStart = Date.parse('2026-09-12T11:30:00Z')
const dayTicks = timeTicks(dayStart, dayStart + DAY, TEHRAN, 8)
check('no more labels than asked for', dayTicks.length <= 8, true)
check('at round hours on the reader\'s clock', dayTicks.slice(0, 3).map((t) => t.label), ['15:00', '18:00', '21:00'])
check('and midnight is the date it moves into', dayTicks.find((t) => t.label.startsWith('Sep'))?.label, 'Sep 13')
check(
  'a month is labelled in dates',
  timeTicks(Date.parse('2026-08-15T20:30:00Z'), Date.parse('2026-09-14T20:30:00Z'), TEHRAN, 6).every((t) => /^[A-Z][a-z]{2} \d+$/.test(t.label)),
  true,
)
check('a narrow chart says less', timeTicks(dayStart, dayStart + DAY, TEHRAN, 3).length <= 3, true)
check('an empty span has no ticks', timeTicks(dayStart, dayStart, TEHRAN, 6), [])

console.log(failures === 0 ? '\nall passed' : `\n${failures} failed`)
// `throw`, not `process.exit`: `@types/node` is not a dependency here, and
// adding it would put `process` in scope for the whole application. Every
// other DOM-free test ends the same way.
if (failures > 0) throw new Error(`${failures} test(s) failed`)
