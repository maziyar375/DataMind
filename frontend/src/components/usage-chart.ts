/**
 * The usage chart's spec — a stacked bar of tokens per day.
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
import type { UsageBucket } from '../api/types.ts'
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
