/**
 * The usage chart's spec, and the sentences that keep a total honest.
 *
 * **This is not a planned chart, and the distinction is the reason this file
 * exists.** `app/charts/` answers "what picture does this *result* want?", a
 * question nobody knows the answer to in advance: a model proposes a shape and
 * the platform vetoes it. A usage chart has no such question. There is one
 * shape — days along the bottom, tokens up the side, split into input and
 * output — and it was decided when the screen was, so routing it through a
 * planner would be asking a model to rediscover a constant.
 *
 * So the spec is arithmetic, it is written here, and it is DOM-free and tested
 * for the reason every module in this list is: its failures are quiet. A chart
 * drawn from a wrong reshape is still a chart, and a reader has no way to tell
 * one from a right one by looking at it.
 *
 * **No React, no DOM, no vega import.** One React import turns this suite into
 * a thing that cannot run, and `npm test` is not in CI, so nothing would say
 * so. The spec is a plain object; `VegaChart.tsx` is what paints it.
 *
 * `npm run test:usage`.
 */
import type { UsageBucket, UsageSeries } from '../api/types.ts'
import type { Palette } from './palette.ts'

/**
 * A Vega-Lite spec, as `VegaChart` takes one.
 *
 * `Record<string, unknown>` rather than vega-embed's `VisualizationSpec`:
 * that type is a union of interfaces, which TypeScript will not hand to the
 * index-signature prop `VegaChart` declares, and importing it would put a
 * node_modules specifier in a file whose whole point is that it needs none.
 */
export type UsageSpec = Record<string, unknown>

/**
 * One stacked segment: the word the legend uses, and the figure it draws.
 *
 * A list rather than two hardcoded encodings, because there are two more
 * segments already named and deliberately not built — cache reads and cache
 * writes, deferred in the specification precisely so this stayed a
 * read-and-render phase. When those columns land, they are an entry here and
 * a colour slot, not a rewrite of this function.
 */
export interface TokenSeries {
  /** The legend's word for it. Also the colour scale's domain value. */
  label: string
  /** The `UsageBucket` field it reads. */
  field: 'prompt_tokens' | 'completion_tokens'
}

/** What a usage chart draws today. Order is the stack order, bottom first. */
export const TOKEN_SERIES: readonly TokenSeries[] = [
  { label: 'Input', field: 'prompt_tokens' },
  { label: 'Output', field: 'completion_tokens' },
]

export interface UsageSpecOptions {
  /** A heading drawn above the plot. Omitted entirely when absent. */
  title?: string
  /** Override the segments. Defaults to `TOKEN_SERIES`. */
  series?: readonly TokenSeries[]
}

/** One row of the long-form data a stacked bar is drawn from. */
interface UsageDatum {
  day: string
  series: string
  tokens: number
  /** The segment's index, so the stack cannot reorder between renders. */
  order: number
}

/**
 * The spec for one scope's buckets.
 *
 * `palette` is the theme's colours, or **`null` to inherit the renderer's**.
 * Null is what a screen passes and it is not laziness: `VegaChart` sets
 * `config.range.category` from `palette.ts` for whichever theme is in force
 * *and re-embeds when the reader flips it*, so a spec that names no range is
 * repainted correctly for free, while one that pins a range is frozen in the
 * theme it was built in. A palette is passed where the theme is fixed rather
 * than followed — print, a pinned board — and by the suite, which needs the
 * range to be somewhere it can read it.
 *
 * Either way the colours are `palette.ts`'s first two categorical slots, in
 * series order, and no literal enters this file.
 */
export function usageSpec(
  buckets: readonly UsageBucket[],
  palette: Palette | null = null,
  opts: UsageSpecOptions = {},
): UsageSpec {
  const series = opts.series ?? TOKEN_SERIES

  // Long form: one row per day per segment. Every segment is emitted for every
  // day, including the ones that drew nothing, so the colour domain and the
  // legend are the same on a quiet Sunday as on a busy Tuesday.
  //
  // Nothing is coalesced here. A bucket's token counts are measured integers —
  // the operations that reported no count at all are in the series' own
  // `unmeasured`, contributing to nothing — so a zero on this chart is a day
  // that spent nothing, which is a fact, and never a day nobody measured.
  const values: UsageDatum[] = []
  for (const bucket of buckets) {
    series.forEach((segment, index) => {
      values.push({
        day: bucket.day,
        series: segment.label,
        tokens: bucket[segment.field],
        order: index,
      })
    })
  }

  const domain = series.map((segment) => segment.label)
  const scale: Record<string, unknown> = { domain }
  if (palette) {
    scale.range = series.map(
      (_, index) => palette.category[index % palette.category.length],
    )
  }

  const spec: UsageSpec = {
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
    data: { values },
    mark: { type: 'bar', tooltip: true },
    encoding: {
      // `utcyearmonthdate`, not a bare temporal field. The backend buckets on
      // `date_trunc('day', …)` at UTC and sends `YYYY-MM-DD`, which Vega parses
      // as midnight UTC and would then *label* in the reader's own zone —
      // west of Greenwich that draws every bar under the previous day's tick.
      // Binning and formatting in UTC keeps the axis saying what the query
      // grouped by.
      x: {
        field: 'day',
        type: 'temporal',
        timeUnit: 'utcyearmonthdate',
        title: null,
        // `labelOverlap` rather than a tick count: ninety days is a legitimate
        // window and Vega drops labels far better than a guessed stride does.
        axis: { format: '%b %d', labelOverlap: true, labelAngle: 0 },
      },
      y: {
        field: 'tokens',
        type: 'quantitative',
        title: 'Tokens',
        stack: 'zero',
        // `12,400,000` is four characters of axis and no more information than
        // `12M` — the shape is the message on this chart, and the exact figure
        // is in the summary beside it and in the tooltip.
        axis: { format: '~s' },
      },
      color: {
        field: 'series',
        type: 'nominal',
        title: null,
        scale,
        legend: { orient: 'top', direction: 'horizontal', offset: 4 },
      },
      // The stack's order stated rather than inherited from row order. Without
      // it the segments are stacked in whatever order the data arrived, and a
      // day whose first row happened to be output would draw its stack upside
      // down against the day beside it.
      order: { field: 'order', type: 'quantitative', sort: 'ascending' },
    },
    // What `VegaChart` needs that the encoding does not say — the same slot
    // the backend compiler fills, read the same way. Stating it keeps this
    // chart out of the `mark === 'bar'` sniffing branch that exists only for
    // specs compiled before `usermeta` did.
    usermeta: {
      datamind: {
        chart_type: 'bar',
        orientation: 'vertical',
        stack: 'stacked',
        categories: buckets.length,
      },
    },
  }

  // Only when there is one: an empty title draws an empty line above the plot.
  if (opts.title) spec.title = opts.title

  return spec
}

// ── the summary beside the chart ──────────────────────────────────────────
/**
 * What a scope spent, and — where it matters — how much of that is unknown.
 *
 * The two counts on the wire exist because a partial total that does not say
 * it is partial is the failure both carried-over rules name. `unmeasured` is
 * operations that reported no token count at all, so every token figure
 * understates; `unpriced` is operations that reported tokens and no price, so
 * the cost covers only part of the work. Two counts and not one flag, because
 * *how* partial a number is decides whether anybody should act on it — three
 * unpriced calls out of four hundred is noise, and three out of four is not a
 * cost figure at all.
 *
 * Both are therefore rendered as **sentences**, next to the number, in the
 * reader's own language rather than as an asterisk. A footnote is a thing a
 * reader finds after they have already believed the number.
 */
export interface UsageTotals {
  /** Nothing at all happened in this window. Distinct from "nothing cost". */
  empty: boolean
  runs: number
  promptTokens: number
  completionTokens: number
  totalTokens: number
  /**
   * The headline figure, grouped — or `null` where nothing reported a count.
   *
   * Null and not `"0"`: a scope whose every operation is unmeasured has a
   * token total of zero in arithmetic and no measurement in fact, and the
   * screen must not spell the second as the first. The same rule the step
   * chip follows one layer down.
   */
  tokens: string | null
  /** The figure, or `null` where there is not one to report. */
  costUsd: number | null
  /** `$12.34`, `$0.0042`, `< $0.0001` — or `null`, on the same terms. */
  cost: string | null
  /** Why every token figure above understates. `null` when none does. */
  unmeasuredNote: string | null
  /** How partial the cost is. `null` when it is whole, or absent entirely. */
  costNote: string | null
}

/**
 * Digits in groups of three: `1,284,301`.
 *
 * Not `toLocaleString`, whose output depends on the runtime's locale — which
 * would make this module's suite pass or fail by environment, and a tested
 * module whose test means something different on another machine is one of
 * the quiet failures this file is here to avoid.
 *
 * Not the chip's `tokenCount` either, and the difference is the box: a chip
 * has room for `4.2k` and a summary line has room for the number, where the
 * digits are what somebody is going to put in a spreadsheet.
 */
export function formatTokens(n: number): string {
  const [whole, fraction] = String(n).split('.')
  return fraction ? `${group(whole)}.${fraction}` : group(whole)
}

/** Thousands separators into a run of digits. */
function group(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

/**
 * A cost, at the precision the figure deserves.
 *
 * Two places above a cent, four below it, and **`< $0.0001` rather than
 * `$0.0000`** for real spend too small to write: rounding a measurement down
 * to a zero is the same lie as summing a null as one, and this is the only
 * place on this screen where it could happen silently.
 *
 * The dollars keep both decimal places whatever the figure is — `$4.20`, not
 * `$4.2`, which reads as a truncation rather than a price.
 */
export function formatCost(value: number): string {
  if (value >= 1) {
    const [whole, fraction] = value.toFixed(2).split('.')
    return `$${group(whole)}.${fraction}`
  }
  if (value >= 0.01) return `$${value.toFixed(2)}`
  if (value >= 0.0001) return `$${value.toFixed(4)}`
  return value > 0 ? '< $0.0001' : '$0.00'
}

/** `1 operation`, `12 operations`. */
function operations(n: number): string {
  return n === 1 ? '1 operation' : `${n} operations`
}

export function usageTotals(series: UsageSeries): UsageTotals {
  const runs = series.runs
  const promptTokens = series.prompt_tokens
  const completionTokens = series.completion_tokens
  const totalTokens = promptTokens + completionTokens

  // Nothing happened. Every note would be a sentence about an absence, which
  // reads as a warning about a problem the reader does not have.
  if (runs <= 0) {
    return {
      empty: true,
      runs: 0,
      promptTokens,
      completionTokens,
      totalTokens,
      tokens: totalTokens > 0 ? formatTokens(totalTokens) : null,
      costUsd: null,
      cost: null,
      unmeasuredNote: null,
      costNote: null,
    }
  }

  const unmeasured = Math.max(0, series.unmeasured)
  const unpriced = Math.max(0, series.unpriced)

  // Every operation in the scope reported nothing, so the zero below is
  // arithmetic over an empty set rather than a measurement of free work.
  const nothingMeasured = unmeasured >= runs && totalTokens <= 0

  // The refusal, and it is not defensive politeness about a case that cannot
  // happen: `cost_usd` is summed with `SUM`, which returns null only when
  // *every* row is null, so a backend that ever coalesced it to zero would
  // arrive here as a `0.0` covering a fully unpriced scope. This is the line
  // that refuses to print it.
  const wholesalePriceless = unpriced >= runs
  const costUsd = wholesalePriceless ? null : series.cost_usd

  return {
    empty: false,
    runs,
    promptTokens,
    completionTokens,
    totalTokens,
    tokens: nothingMeasured ? null : formatTokens(totalTokens),
    costUsd,
    cost: costUsd == null ? null : formatCost(costUsd),
    unmeasuredNote:
      unmeasured > 0
        ? `${unmeasured} of ${operations(runs)} reported no token count, ` +
          'so every figure here understates.'
        : null,
    costNote:
      unpriced <= 0
        ? null
        : wholesalePriceless
          ? `No price is known for any of these ${operations(runs)}.`
          : `Cost is known for ${runs - unpriced} of ${operations(runs)}.`,
  }
}
