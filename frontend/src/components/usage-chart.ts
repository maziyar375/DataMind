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
 * What a scope used, and — where it matters — how much of that is unknown.
 *
 * `unmeasured` on the wire exists because a partial total that does not say it
 * is partial is the failure the carried-over rule names: it counts operations
 * that reported no token count at all, so every token figure understates. A
 * count and not a flag, because *how* partial a number is decides whether
 * anybody should act on it — three unmeasured calls out of four hundred is
 * noise, and three out of four is not a usage figure at all.
 *
 * It is therefore rendered as a **sentence**, next to the number, in the
 * reader's own language rather than as an asterisk. A footnote is a thing a
 * reader finds after they have already believed the number.
 */
export interface UsageTotals {
  /** Nothing at all happened in this window. Distinct from "used nothing". */
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
  /** Why every token figure above understates. `null` when none does. */
  unmeasuredNote: string | null
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
      unmeasuredNote: null,
    }
  }

  const unmeasured = Math.max(0, series.unmeasured)

  // Every operation in the scope reported nothing, so the zero below is
  // arithmetic over an empty set rather than a measurement of no work.
  const nothingMeasured = unmeasured >= runs && totalTokens <= 0

  return {
    empty: false,
    runs,
    promptTokens,
    completionTokens,
    totalTokens,
    tokens: nothingMeasured ? null : formatTokens(totalTokens),
    // A scope of one is written as one rather than as a ratio of itself.
    // "1 of 1 operation reported no token count" is arithmetically right and
    // not a sentence — and a window holding a single run is the ordinary case
    // on a quiet installation, not an edge.
    unmeasuredNote:
      unmeasured <= 0
        ? null
        : runs === 1
          ? 'This operation reported no token count, so the figures here understate.'
          : `${unmeasured} of ${operations(runs)} reported no token count, `
            + 'so every figure here understates.',
  }
}

// ── by model ──────────────────────────────────────────────────────────────
/** What a run that recorded no model name is listed as. */
export const UNRECORDED_MODEL = 'Model not recorded'

/** One line of the by-model table, already written for reading. */
export interface ModelRow {
  /** The server's key for the row — the recorded name, `''` for none. */
  key: string
  /** The name to show: the model, or `UNRECORDED_MODEL`. */
  label: string
  /** Whether a model was recorded at all, so the page can set it apart. */
  recorded: boolean
  totalTokens: number
  /** Grouped figures, or `null` where nothing on this model reported a count. */
  tokens: string | null
  input: string | null
  output: string | null
  /**
   * This model's part of the scope's measured tokens: `62%`, `< 1%` — or
   * `null` when the scope measured nothing, so there is no whole to be part of.
   */
  share: string | null
  runs: number
}

/**
 * The by-model table's rows, in the order the server ranked them.
 *
 * A share that rounds to zero is written `< 1%` rather than `0%`, for the
 * reason the token tiles refuse a zero nobody measured: a model that did real
 * work is not a model that did none.
 */
export function modelRows(series: UsageSeries): ModelRow[] {
  const whole = series.prompt_tokens + series.completion_tokens
  return series.models.map((model) => {
    const totalTokens = model.prompt_tokens + model.completion_tokens
    const nothingMeasured = model.unmeasured >= model.runs && totalTokens <= 0
    const percent = whole > 0 ? (totalTokens / whole) * 100 : null
    return {
      key: model.model,
      label: model.model || UNRECORDED_MODEL,
      recorded: model.model !== '',
      totalTokens,
      tokens: nothingMeasured ? null : formatTokens(totalTokens),
      input: nothingMeasured ? null : formatTokens(model.prompt_tokens),
      output: nothingMeasured ? null : formatTokens(model.completion_tokens),
      share:
        percent === null ? null
        : percent > 0 && percent < 1 ? '< 1%'
        : `${Math.round(percent)}%`,
      runs: model.runs,
    }
  })
}

// ── the window ────────────────────────────────────────────────────────────
/** The windows the screen offers. Days, because the buckets are days. */
export const WINDOW_DAYS = [7, 30, 90] as const
export type WindowDays = (typeof WINDOW_DAYS)[number]

/**
 * When a window of `days` starts, as the ISO instant the API takes.
 *
 * **UTC midnight, `days - 1` days back**, and both halves of that are the
 * reason this is here rather than inlined in the page. Midnight, because the
 * backend buckets on `date_trunc('day', …)` at UTC: asking from 09:40 leaves
 * the oldest bucket holding two thirds of a day and drawn beside whole ones,
 * which is a bar that is short for a reason nothing on the screen explains.
 * And `days - 1`, because a seven-day window ending today is seven buckets —
 * today and the six before it — where a naive `now - 7 days` spans eight.
 *
 * Off-by-one in a window boundary is the quiet kind: the chart still draws,
 * the total is still a total, and the only symptom is a figure that disagrees
 * with the same figure somewhere else.
 */
export function windowSince(days: number, now: Date = new Date()): string {
  const midnight = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
  return new Date(midnight - (days - 1) * 86_400_000).toISOString()
}
