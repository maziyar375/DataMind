/**
 * The token usage screen's arithmetic: periods, slots, ticks, labels and the
 * sentences that keep a total honest.
 *
 * **No React, no DOM.** One React import turns this suite into a thing that
 * cannot run, and `npm test` is not in CI, so nothing would say so. The chart
 * itself is `usage-timeline.tsx`, which draws what this module computes.
 *
 * **Every clock here is explicit.** Times are epoch milliseconds, and every
 * label is written for a UTC offset passed in — the same offset the page sent
 * the server, which aligned its buckets to it. Nothing reads the machine's own
 * zone, so a label cannot disagree with the bucket it names, and this suite
 * means the same thing on every machine that runs it.
 *
 * `npm run test:usage`.
 */
import type { UsageBucket, UsageModel, UsageSeries } from '../api/types.ts'

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

// ── numbers ───────────────────────────────────────────────────────────────
/**
 * Digits in groups of three: `1,284,301`.
 *
 * Not `toLocaleString`, whose output depends on the runtime's locale — which
 * would make this module's suite pass or fail by environment.
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
 * An axis figure: `950`, `1.2k`, `40k`, `1.5M`.
 *
 * Only for ticks, where the shape is the message and the exact number is one
 * hover away. One decimal below ten of a unit, none above, and a trailing
 * `.0` is dropped so `2k` never reads as `2.0k`.
 */
export function formatCompact(n: number): string {
  const abs = Math.abs(n)
  const unit = abs >= 1e6 ? { d: 1e6, s: 'M' } : abs >= 1e3 ? { d: 1e3, s: 'k' } : null
  if (!unit) return String(Math.round(n))
  const scaled = n / unit.d
  const text = Math.abs(scaled) < 10 ? scaled.toFixed(1) : String(Math.round(scaled))
  return `${text.replace(/\.0$/, '')}${unit.s}`
}

/** `1 operation`, `12 operations`. */
function operations(n: number): string {
  return n === 1 ? '1 operation' : `${n} operations`
}

// ── the figures a scope reports ───────────────────────────────────────────
/** What `usageTotals` reads — a series, or one model of it. */
export interface UsageFigures {
  prompt_tokens: number
  completion_tokens: number
  runs: number
  unmeasured: number
  /**
   * What of `prompt_tokens` the provider served from, or wrote into, its
   * cache — **a subset of it, never an addition**, so nothing here adds them
   * into a total. `null` is *nothing in this scope reported a cache figure*,
   * which is a different fact from a reported `0`; optional so every caller
   * that has no opinion about caching is unchanged.
   */
  cache_read_tokens?: number | null
  cache_write_tokens?: number | null
  /** How many operations reported a cache figure at all. */
  cache_measured?: number
}

/**
 * What a scope used, and — where it matters — how much of that is unknown.
 *
 * `unmeasured` exists because a partial total that does not say it is partial
 * is the failure the carried-over rule names: it counts operations that
 * reported no token count at all, so every token figure understates. It is
 * rendered as a **sentence**, next to the number, rather than as an asterisk.
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
   * screen must not spell the second as the first.
   */
  tokens: string | null
  /** Input's part of the measured tokens, 0–1. `null` when there are none. */
  inputShare: number | null
  /** Tokens per measured operation, rounded. `null` when nothing was measured. */
  perOperation: number | null
  /** Why every token figure above understates. `null` when none does. */
  unmeasuredNote: string | null
  /**
   * The cached share of input: what the provider did not have to read again.
   *
   * `null` where nothing reported a cache figure, which is most providers —
   * and it must read as *unknown*, never as `0%`. The two answer opposite
   * questions about whether re-sending a schema block once per step is
   * affordable, which is the reason these columns exist at all.
   */
  cachedShare: number | null
  /** The cache figures, grouped. `null` where nothing reported one. */
  cacheRead: string | null
  cacheWrite: string | null
  /**
   * The cache in one sentence, or `null` where nothing reported it.
   *
   * A sentence rather than a percentage on its own, for `unmeasuredNote`'s
   * reason: `0%` from a provider that reports caching and served none is a
   * finding, and it reads identically to a missing measurement unless the
   * screen spells the difference out.
   */
  cacheNote: string | null
}

export function usageTotals(figures: UsageFigures): UsageTotals {
  const runs = figures.runs
  const promptTokens = figures.prompt_tokens
  const completionTokens = figures.completion_tokens
  const totalTokens = promptTokens + completionTokens
  const unmeasured = Math.max(0, figures.unmeasured)
  const measuredRuns = Math.max(0, runs - unmeasured)

  const cache = cacheFigures(figures, promptTokens)

  if (runs <= 0) {
    return {
      empty: true,
      runs: 0,
      promptTokens,
      completionTokens,
      totalTokens,
      tokens: totalTokens > 0 ? formatTokens(totalTokens) : null,
      inputShare: null,
      perOperation: null,
      unmeasuredNote: null,
      ...cache,
    }
  }

  // Every operation in the scope reported nothing, so the zero is arithmetic
  // over an empty set rather than a measurement of no work.
  const nothingMeasured = unmeasured >= runs && totalTokens <= 0

  return {
    empty: false,
    runs,
    promptTokens,
    completionTokens,
    totalTokens,
    tokens: nothingMeasured ? null : formatTokens(totalTokens),
    inputShare: totalTokens > 0 ? promptTokens / totalTokens : null,
    perOperation: measuredRuns > 0 && !nothingMeasured
      ? Math.round(totalTokens / measuredRuns)
      : null,
    // A scope of one is written as one rather than as a ratio of itself:
    // "1 of 1 operation reported no token count" is not a sentence.
    unmeasuredNote:
      unmeasured <= 0
        ? null
        : runs === 1
          ? 'This operation reported no token count, so the figures here understate.'
          : `${unmeasured} of ${operations(runs)} reported no token count, `
            + 'so every figure here understates.',
    ...cache,
  }
}

/**
 * The four cache fields of `UsageTotals`, from a scope's figures.
 *
 * Split out because the empty branch above needs them too, and because the
 * arithmetic has exactly one trap in it: **a cache read is part of the prompt
 * it was sent with**, so the share is against `prompt_tokens` and never
 * against the total. Anything else states a number larger than the thing it
 * is a share of.
 */
function cacheFigures(
  figures: UsageFigures,
  promptTokens: number,
): Pick<UsageTotals, 'cachedShare' | 'cacheRead' | 'cacheWrite' | 'cacheNote'> {
  const read = figures.cache_read_tokens
  const write = figures.cache_write_tokens
  // `null` **and** `undefined`, because the field is optional: a caller that
  // predates caching passes neither, and it means the same thing the server's
  // null means — nothing measured this.
  if (read === null || read === undefined) {
    return { cachedShare: null, cacheRead: null, cacheWrite: null, cacheNote: null }
  }

  const share = promptTokens > 0 ? Math.min(1, read / promptTokens) : null
  const written = write === null || write === undefined ? null : formatTokens(write)
  const note = share === null
    ? null
    : read > 0
      ? `${formatShare(share)} of input was served from cache`
      : 'Nothing was served from cache, though this provider reports it'

  return {
    cachedShare: share,
    cacheRead: formatTokens(read),
    cacheWrite: written,
    cacheNote: written && write ? `${note}; ${written} written to it` : note,
  }
}

/**
 * A percentage for reading: `62%`, and `< 1%` rather than `0%` for a real
 * share too small to round up — a model that did real work is not a model
 * that did none.
 */
export function formatShare(fraction: number): string {
  const percent = fraction * 100
  if (percent > 0 && percent < 1) return '< 1%'
  return `${Math.round(percent)}%`
}

// ── models ────────────────────────────────────────────────────────────────
/** What a run that recorded no model name is listed as. */
export const UNRECORDED_MODEL = 'Model not recorded'

/**
 * A model name split for reading: the provider path, which is quiet, and the
 * model itself, which is the part a reader scans for.
 * `openai/deepseek/deepseek-v4-flash` → `openai/deepseek/` + `deepseek-v4-flash`.
 */
export function splitModelName(model: string): { path: string; name: string } {
  if (!model) return { path: '', name: UNRECORDED_MODEL }
  const cut = model.lastIndexOf('/')
  if (cut <= 0 || cut === model.length - 1) return { path: '', name: model }
  return { path: model.slice(0, cut + 1), name: model.slice(cut + 1) }
}

/**
 * One scope as the filter sees it: the whole scope, or the models chosen.
 *
 * `models` empty is all models. With a selection, the figures are the chosen
 * models' sums and the buckets are theirs merged by start — the same rows the
 * scope's own buckets were folded from, so a selection of every model reads
 * exactly as all models does. A chosen model the window holds no rows for
 * contributes nothing rather than throwing: the filter may name a model from
 * a wider period than the one now selected, and "nothing on these models
 * here" is an answer.
 */
export function scopeView(
  series: UsageSeries,
  models: readonly string[],
): { figures: UsageFigures; buckets: UsageBucket[] } {
  if (models.length === 0) return { figures: series, buckets: series.buckets }
  const chosen = new Set(models)
  const figures: UsageFigures = {
    prompt_tokens: 0, completion_tokens: 0, runs: 0, unmeasured: 0,
    // `null` until a chosen model reports one, so a selection of models that
    // all run on a provider reporting no caching reads as unknown rather than
    // as zero — `addReported` is the whole of the rule.
    cache_read_tokens: null, cache_write_tokens: null, cache_measured: 0,
  }
  const merged = new Map<string, UsageBucket>()
  for (const model of series.models) {
    if (!chosen.has(model.model)) continue
    figures.prompt_tokens += model.prompt_tokens
    figures.completion_tokens += model.completion_tokens
    figures.runs += model.runs
    figures.unmeasured += model.unmeasured
    figures.cache_read_tokens = addReported(
      figures.cache_read_tokens ?? null, model.cache_read_tokens,
    )
    figures.cache_write_tokens = addReported(
      figures.cache_write_tokens ?? null, model.cache_write_tokens,
    )
    figures.cache_measured = (figures.cache_measured ?? 0) + (model.cache_measured ?? 0)
    for (const bucket of model.buckets) {
      const key = String(Date.parse(bucket.start))
      const into = merged.get(key)
      merged.set(key, into
        ? {
          start: into.start,
          prompt_tokens: into.prompt_tokens + bucket.prompt_tokens,
          completion_tokens: into.completion_tokens + bucket.completion_tokens,
          runs: into.runs + bucket.runs,
          cache_read_tokens: addReported(into.cache_read_tokens, bucket.cache_read_tokens),
          cache_write_tokens: addReported(into.cache_write_tokens, bucket.cache_write_tokens),
        }
        : { ...bucket })
    }
  }
  const buckets = [...merged.values()].sort((a, b) => Date.parse(a.start) - Date.parse(b.start))
  return { figures, buckets }
}

/**
 * Add a count that may not have been reported, keeping *unreported* intact.
 *
 * The browser half of `add_reported` in `app/domain/ports/llm.py`, and it has
 * to mean the same thing or a merged selection disagrees with the same
 * selection computed by the server. `null` is *never reported*, not zero: the
 * obvious `(a ?? 0) + (b ?? 0)` turns a scope nothing measured into a scope
 * that cached nothing, which are the two facts the column exists to separate.
 */
export function addReported(total: number | null, reported: number | null | undefined): number | null {
  if (reported === null || reported === undefined) return total
  return total === null ? reported : total + reported
}

/**
 * How a selection is named in a caption: `null` for all models, the model's
 * own name for one, and a count for several.
 */
export function selectionLabel(models: readonly string[]): string | null {
  if (models.length === 0) return null
  if (models.length === 1) return splitModelName(models[0]).name
  return `${models.length} models`
}

/** A selection with `model` added, or taken out if it was already in it. */
export function toggleModel(models: readonly string[], model: string): string[] {
  return models.includes(model) ? models.filter((m) => m !== model) : [...models, model]
}

/** One line of a ranked breakdown — a model, or a person — already written. */
export interface RankedRow {
  key: string
  totalTokens: number
  /** Grouped figures, or `null` where nothing on this row reported a count. */
  tokens: string | null
  input: string | null
  output: string | null
  /** This row's part of the whole: `62%`, `< 1%`, or `null` with no whole. */
  share: string | null
  /** The two bar segments, as fractions of the whole (so the bar *is* the share). */
  inputFraction: number
  outputFraction: number
  runs: number
}

/** A row's figures against a whole, for `RankedRow`. */
export function rankedRow(key: string, figures: UsageFigures, whole: number): RankedRow {
  const totalTokens = figures.prompt_tokens + figures.completion_tokens
  const nothingMeasured = figures.unmeasured >= figures.runs && totalTokens <= 0
  return {
    key,
    totalTokens,
    tokens: nothingMeasured ? null : formatTokens(totalTokens),
    input: nothingMeasured ? null : formatTokens(figures.prompt_tokens),
    output: nothingMeasured ? null : formatTokens(figures.completion_tokens),
    // A row that measured nothing has no share to state — `0%` would read as a
    // measurement of none, the same lie the em dash in `tokens` refuses.
    share: whole > 0 && !nothingMeasured ? formatShare(totalTokens / whole) : null,
    inputFraction: whole > 0 ? figures.prompt_tokens / whole : 0,
    outputFraction: whole > 0 ? figures.completion_tokens / whole : 0,
    runs: figures.runs,
  }
}

/** The by-model breakdown's rows, in the order the server ranked them. */
export function modelRows(series: UsageSeries): (RankedRow & { model: UsageModel })[] {
  const whole = series.prompt_tokens + series.completion_tokens
  return series.models.map((model) => ({ ...rankedRow(model.model, model, whole), model }))
}

// ── periods ───────────────────────────────────────────────────────────────
export type PresetKey = '1h' | '6h' | '24h' | '7d' | '30d' | '90d'
export type PeriodKey = PresetKey | 'custom'

/** The periods the screen offers, shortest first. The server picks the bars. */
export const PRESETS: readonly { key: PresetKey; label: string; title: string; ms: number }[] = [
  { key: '1h', label: '1h', title: 'Last hour', ms: HOUR },
  { key: '6h', label: '6h', title: 'Last 6 hours', ms: 6 * HOUR },
  { key: '24h', label: '24h', title: 'Last 24 hours', ms: DAY },
  { key: '7d', label: '7d', title: 'Last 7 days', ms: 7 * DAY },
  { key: '30d', label: '30d', title: 'Last 30 days', ms: 30 * DAY },
  { key: '90d', label: '90d', title: 'Last 90 days', ms: 90 * DAY },
]

export const DEFAULT_PERIOD: PresetKey = '30d'

export function isPeriodKey(value: string | null): value is PeriodKey {
  return value === 'custom' || PRESETS.some((preset) => preset.key === value)
}

/**
 * The instants a preset asks the server for: the period, ending now.
 *
 * The server aligns `since` to its bucket grid and returns the aligned value,
 * so the page draws from the response rather than from this.
 */
export function presetRange(key: PresetKey, now: number): { since: string; until: string } {
  const preset = PRESETS.find((entry) => entry.key === key) ?? PRESETS[4]
  return {
    since: new Date(now - preset.ms).toISOString(),
    until: new Date(now).toISOString(),
  }
}

/** The offset the page sends and every label is written in: minutes east of UTC. */
export function localOffsetMinutes(at: Date = new Date()): number {
  // `getTimezoneOffset` is minutes *behind* UTC, so Tehran is -210. The API
  // takes the ISO sign, east positive — and `-0` is not a query value.
  return -at.getTimezoneOffset() || 0
}

// ── a local wall clock, for a given offset ───────────────────────────────
/**
 * `YYYY-MM-DDTHH:MM` for an instant on the reader's clock — the value a
 * `datetime-local` input holds.
 */
export function toLocalInput(ms: number, offsetMinutes: number): string {
  return new Date(ms + offsetMinutes * MINUTE).toISOString().slice(0, 16)
}

/** The inverse of `toLocalInput`. `null` for a value that is not a time. */
export function fromLocalInput(value: string, offsetMinutes: number): number | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return null
  const ms = Date.parse(`${value}:00Z`)
  return Number.isNaN(ms) ? null : ms - offsetMinutes * MINUTE
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function wall(ms: number, offsetMinutes: number): Date {
  return new Date(ms + offsetMinutes * MINUTE)
}

function hhmm(date: Date): string {
  return `${String(date.getUTCHours()).padStart(2, '0')}:${String(date.getUTCMinutes()).padStart(2, '0')}`
}

function monthDay(date: Date): string {
  return `${MONTHS[date.getUTCMonth()]} ${date.getUTCDate()}`
}

/** `Sep 14, 10:02` on the reader's clock. */
export function formatInstant(ms: number, offsetMinutes: number): string {
  const date = wall(ms, offsetMinutes)
  return `${monthDay(date)}, ${hhmm(date)}`
}

/**
 * What one bar covers, for its tooltip and its table row.
 *
 * A day bar is its date (`Mon, Sep 14`); anything narrower is a range on one
 * date (`Sep 14, 10:00–10:15`), with the end written `24:00` rather than
 * `00:00` when a bar closes at midnight, so a range never reads backwards.
 */
export function formatSlot(startMs: number, bucketSeconds: number, offsetMinutes: number): string {
  const start = wall(startMs, offsetMinutes)
  if (bucketSeconds >= DAY / 1000) {
    return `${WEEKDAYS[start.getUTCDay()]}, ${monthDay(start)}`
  }
  const end = wall(startMs + bucketSeconds * 1000, offsetMinutes)
  const endText = end.getUTCHours() === 0 && end.getUTCMinutes() === 0 ? '24:00' : hhmm(end)
  return `${monthDay(start)}, ${hhmm(start)}–${endText}`
}

/** How wide the bars are, as the chart's caption says it. */
export function bucketLabel(bucketSeconds: number): string {
  switch (bucketSeconds) {
    case 300: return '5-minute bars'
    case 900: return '15-minute bars'
    case 3600: return 'Hourly bars'
    case 21_600: return '6-hour bars'
    case 86_400: return 'Daily bars'
    default: return bucketSeconds < 3600
      ? `${Math.round(bucketSeconds / 60)}-minute bars`
      : `${Math.round(bucketSeconds / 3600)}-hour bars`
  }
}

// ── the chart's slots ─────────────────────────────────────────────────────
/** One bar's place on the timeline, filled or empty. */
export interface Slot {
  start: number
  end: number
  input: number
  output: number
  runs: number
  /**
   * The cached part of `input`, and the part written into the cache — **both
   * inside `input`, never beside it**. `null` where the bucket reported no
   * cache figure, which is what a provider that says nothing about caching
   * leaves behind.
   */
  cacheRead: number | null
  cacheWrite: number | null
}

/** One drawn piece of a column, bottom to top. */
export interface Segment {
  key: 'cacheRead' | 'cacheWrite' | 'input' | 'output'
  label: string
  tokens: number
}

/**
 * A column's pieces, bottom to top, with the input bar **subdivided**.
 *
 * This is the one piece of arithmetic on this screen that a reasonable person
 * gets wrong. A provider reports cache reads as the part of the prompt it did
 * not have to read again, and LiteLLM folds Anthropic's separate figures into
 * `prompt_tokens` for the same reason — so both cache counts are already
 * *inside* `input`. Stacking them on top would draw a column taller than the
 * tokens it represents and inflate the axis with numbers nobody spent.
 *
 * So the input bar is cut into three: served from cache, written to cache, and
 * the rest — which is the part that was actually read. The column's height is
 * unchanged from before this existed, and a bucket that reported no cache
 * figure yields the same two segments it always did.
 *
 * The two cache figures are clamped to `input` together. A provider that
 * reports more cached tokens than prompt tokens is reporting something this
 * screen cannot draw, and the right response is a bar that still adds up
 * rather than a negative remainder that renders as an upside-down rectangle.
 */
export function stackSegments(slot: Slot): Segment[] {
  const read = Math.max(0, slot.cacheRead ?? 0)
  const write = Math.max(0, slot.cacheWrite ?? 0)
  const scale = read + write > slot.input && read + write > 0
    ? slot.input / (read + write)
    : 1
  const cached = Math.round(read * scale)
  const written = Math.round(write * scale)
  const fresh = Math.max(0, slot.input - cached - written)

  const segments: Segment[] = []
  if (cached > 0) segments.push({ key: 'cacheRead', label: 'From cache', tokens: cached })
  if (written > 0) segments.push({ key: 'cacheWrite', label: 'Written to cache', tokens: written })
  if (fresh > 0) segments.push({ key: 'input', label: 'Input', tokens: fresh })
  if (slot.output > 0) segments.push({ key: 'output', label: 'Output', tokens: slot.output })
  return segments
}

/**
 * Every bucket in the window, including the ones nothing ran in.
 *
 * The response is sparse, and a chart drawn from it alone ends at the last
 * bucket anything happened in — which is the axis that stops at the latest
 * token instead of at now. So the slots are laid from the window's own
 * `since` to its `until`, and a bucket the server did not send is a zero:
 * nothing ran in it, which is a fact, not a gap in the measurement (those are
 * counted in `unmeasured`).
 */
export function denseSlots(
  buckets: readonly UsageBucket[],
  since: string,
  until: string,
  bucketSeconds: number,
): Slot[] {
  const width = bucketSeconds * 1000
  const first = Date.parse(since)
  const end = Date.parse(until)
  if (!(width > 0) || Number.isNaN(first) || Number.isNaN(end) || end <= first) return []

  const count = Math.min(2000, Math.ceil((end - first) / width))
  const byStart = new Map<number, UsageBucket>()
  for (const bucket of buckets) byStart.set(Date.parse(bucket.start), bucket)

  const slots: Slot[] = []
  for (let index = 0; index < count; index += 1) {
    const start = first + index * width
    const bucket = byStart.get(start)
    slots.push({
      start,
      end: start + width,
      input: bucket?.prompt_tokens ?? 0,
      output: bucket?.completion_tokens ?? 0,
      runs: bucket?.runs ?? 0,
      // `null` rather than `0` for a bucket the server did not send: nothing
      // ran in it, so nothing measured its caching either — and the empty
      // slots must not read as a provider that cached nothing.
      cacheRead: bucket?.cache_read_tokens ?? null,
      cacheWrite: bucket?.cache_write_tokens ?? null,
    })
  }
  return slots
}

/**
 * The value axis: a round maximum and the ticks up to it.
 *
 * Steps of 1, 2, 2.5 or 5 times a power of ten, so every label is a number a
 * person would write. An empty chart still gets an axis — `0` and one step —
 * rather than a scale that divides by zero.
 */
export function valueTicks(max: number, count = 4): { max: number; ticks: number[] } {
  if (!(max > 0)) return { max: 1, ticks: [0] }
  const raw = max / count
  const power = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? 10 * power
  const top = Math.ceil(max / step) * step
  const ticks: number[] = []
  for (let value = 0; value <= top + step / 2; value += step) ticks.push(Math.round(value))
  return { max: top, ticks }
}

/** The steps the time axis may use, narrowest first. */
const TIME_STEPS = [
  5 * MINUTE, 10 * MINUTE, 15 * MINUTE, 30 * MINUTE,
  HOUR, 2 * HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR,
  DAY, 2 * DAY, 7 * DAY, 14 * DAY,
]

/**
 * Labelled instants along the time axis, at round times on the reader's clock.
 *
 * The narrowest step that yields no more than `maxTicks` labels, so a wide
 * chart says more and a phone says less. Below a day the label is the time,
 * except at midnight, where it is the date — the one place a run of times
 * needs telling which day it has moved into.
 */
export function timeTicks(
  startMs: number,
  endMs: number,
  offsetMinutes: number,
  maxTicks: number,
): { at: number; label: string }[] {
  const span = endMs - startMs
  if (!(span > 0) || maxTicks < 1) return []
  const step = TIME_STEPS.find((s) => span / s <= maxTicks) ?? 28 * DAY
  const offset = offsetMinutes * MINUTE
  const first = Math.ceil((startMs + offset) / step) * step - offset

  const ticks: { at: number; label: string }[] = []
  for (let at = first; at < endMs; at += step) {
    const date = wall(at, offsetMinutes)
    const midnight = date.getUTCHours() === 0 && date.getUTCMinutes() === 0
    ticks.push({ at, label: step >= DAY || midnight ? monthDay(date) : hhmm(date) })
  }
  return ticks
}
