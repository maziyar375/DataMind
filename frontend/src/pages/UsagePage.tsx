/**
 * How many tokens the models have used — when, and on which models.
 *
 * Every figure on this screen already existed as a column — `0023` landed
 * per-call accounting on `runs`, `report_runs` and `semantic_jobs`, and this
 * reads it back. A number nobody can see answers *"what are we using"* exactly
 * as badly as a number nobody records.
 *
 * **Counts, never content.** An integer, a model name and a time. No question,
 * no SQL, no prose ever reaches this page — which is what makes reading
 * somebody else's usage a capability an Auditor may hold rather than a
 * disclosure of their work.
 *
 * **Three scopes, and the gate is between them rather than inside one.** Your
 * own needs no capability, because the scope *is* you and no control on this
 * page can widen it; the two that are about other people need `usage.read`
 * and are tabs this section declines to render without it. The backend
 * refuses them either way — what is here is an affordance, never the boundary.
 *
 * **One filter row scopes everything under it.** The period and the model sit
 * above the summary, the timeline and the breakdowns, and every one of those
 * re-renders against the same slice, so no two numbers on the page disagree.
 * Both live in the address (`?period=24h&model=a&model=b`), so a view is a
 * link and survives a tab change. The model filter is a *set*: none chosen is
 * all models, and any number may be chosen together.
 *
 * **The partiality is the feature, not a caveat.** A provider that reports no
 * usage block is ordinary, and a total that quietly absorbs it reports less
 * work than was done. `usageTotals` turns the count on the wire into a
 * sentence, rendered beside the number rather than under it.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Navigate, useLocation, useMatch, useNavigate, useSearchParams } from 'react-router-dom'
import { usage as api, type UsageRange } from '../api/client'
import type { UsageSeries, UsageTotal } from '../api/types'
import {
  EmptyState, ErrorNote, Icon, PageHeader, Segmented, Spinner, dirOf, inputStyle,
} from '../components/ui'
import { Tabs } from '../components/settings'
import { useCan, type Capability } from '../permissions'
import {
  DEFAULT_PERIOD, PRESETS, UNRECORDED_MODEL, bucketLabel, denseSlots, formatInstant,
  formatShare, formatTokens, fromLocalInput, isPeriodKey, localOffsetMinutes, modelRows,
  presetRange, rankedRow, scopeView, selectionLabel, splitModelName, toLocalInput, toggleModel,
  usageTotals,
  type PeriodKey, type RankedRow, type Slot, type UsageTotals,
} from '../components/usage-chart'
import {
  presentSegments, UsageSlotTable, UsageTimeline, useSegmentColors,
} from '../components/usage-timeline'

/**
 * The three scopes, and what each one needs.
 *
 * `me` is first and carries no capability; the other two carry the same one.
 * The list is the tab strip, the route table and the gate at once, so a scope
 * cannot appear in one of the three and not the others.
 */
const SCOPES: { value: string; label: string; needs?: Capability }[] = [
  { value: 'me', label: 'Yours' },
  { value: 'people', label: 'People', needs: 'usage.read' },
  { value: 'total', label: 'All users', needs: 'usage.read' },
]

/** How often a period that ends now is read again, quietly. */
const REFRESH_MS = 60_000

export default function UsagePage() {
  const can = useCan()
  const navigate = useNavigate()
  const location = useLocation()
  // A sub-route, so the section stays mounted across a tab change and every
  // tab is an address somebody can link to. `/usage` itself is `me`, which is
  // the scope everybody has.
  const routeTab = useMatch('/usage/:tab')?.params.tab ?? 'me'

  const visible = useMemo(
    () => SCOPES.filter((scope) => !scope.needs || can(scope.needs)),
    [can],
  )
  const active = visible.find((scope) => scope.value === routeTab)

  // A path somebody typed, or a link from when they held the capability. The
  // refusal is the scope they can read.
  if (!active) return <Navigate to={{ pathname: '/usage', search: location.search }} replace />

  const header = (
    <PageHeader
      title="Token usage"
      subtitle="How many tokens the models used, when, and on which models. Counts only — no question, no answer and no SQL is on this page."
    />
  )

  const body = <Scope scope={active.value} />

  // One tab is not a tab strip. Somebody without `usage.read` sees a plain
  // index rather than a section band closed by a strip of one.
  if (visible.length === 1) {
    return (
      <div className="rm-index rm-page-pad" style={{ flex: 1, overflowY: 'auto' }}>
        {header}
        {body}
      </div>
    )
  }

  return (
    <div
      className="rm-section"
      style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 }}
    >
      <div className="rm-section-head">
        <div className="rm-section-title">{header}</div>
        <Tabs
          value={active.value}
          gutter={18}
          // The filters ride along: a tab change is a different scope over the
          // same slice, not a reset.
          onChange={(next) => navigate({
            pathname: next === 'me' ? '/usage' : `/usage/${next}`,
            search: location.search,
          })}
          items={visible.map((scope) => ({ value: scope.value, label: scope.label }))}
        />
      </div>
      <div className="rm-page-pad" style={{ flex: 1, overflowY: 'auto' }}>
        {body}
      </div>
    </div>
  )
}

// ── the slice: period and model, in the address ──────────────────────────
interface Slice {
  period: PeriodKey
  /** `datetime-local` values, on the reader's clock. Only for `custom`. */
  from: string
  to: string
  /** The models chosen — empty for all. `''` is the model-not-recorded row. */
  models: string[]
}

function useSlice(): [Slice, (patch: Partial<Slice>) => void] {
  const [params, setParams] = useSearchParams()
  const raw = params.get('period')
  const slice: Slice = {
    period: isPeriodKey(raw) ? raw : DEFAULT_PERIOD,
    from: params.get('from') ?? '',
    to: params.get('to') ?? '',
    models: [...new Set(params.getAll('model'))],
  }
  const update = (patch: Partial<Slice>) => {
    const next = { ...slice, ...patch }
    const out = new URLSearchParams()
    if (next.period !== DEFAULT_PERIOD) out.set('period', next.period)
    if (next.period === 'custom') {
      if (next.from) out.set('from', next.from)
      if (next.to) out.set('to', next.to)
    }
    for (const model of next.models) out.append('model', model)
    setParams(out, { replace: true })
  }
  return [slice, update]
}

/**
 * What the slice asks the server for, or why it cannot ask.
 *
 * A preset is resolved against the clock at the moment of the read, so a
 * refresh moves the window forward with it. A custom range is two wall-clock
 * times converted on the reader's offset, and a range that ends before it
 * starts is a sentence rather than a request.
 */
function resolveRange(slice: Slice, offset: number, now: number):
  { range: UsageRange; endsNow: boolean } | { error: string } {
  if (slice.period !== 'custom') {
    return { range: { ...presetRange(slice.period, now), tz_offset: offset }, endsNow: true }
  }
  const from = fromLocalInput(slice.from, offset)
  const to = fromLocalInput(slice.to, offset)
  if (from === null || to === null) return { error: 'Choose a start and an end.' }
  if (from >= to) return { error: 'The start has to be before the end.' }
  const until = Math.min(to, now)
  if (from >= until) return { error: 'That range is in the future — choose a start before now.' }
  return {
    range: {
      since: new Date(from).toISOString(),
      until: new Date(until).toISOString(),
      tz_offset: offset,
    },
    // An end in the future is clamped to now, and then it does end now.
    endsNow: now - until < 60_000,
  }
}

/**
 * One scope's read, its filters and its rendering.
 *
 * Re-reads on the scope, the period or the range — and, for a period that
 * ends now, every minute, quietly: the window moves with the clock, so the
 * timeline's last column keeps being the bucket in progress.
 */
function Scope({ scope }: { scope: string }) {
  const [slice, setSlice] = useSlice()
  const offset = useMemo(() => localOffsetMinutes(), [])
  const [tick, setTick] = useState(0)

  const [mine, setMine] = useState<UsageSeries | null>(null)
  const [people, setPeople] = useState<UsageSeries[] | null>(null)
  const [total, setTotal] = useState<UsageTotal | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [endsNow, setEndsNow] = useState(true)

  // `tick` is the clock: a preset is resolved again on every refresh.
  const resolved = useMemo(
    () => resolveRange(slice, offset, Date.now()),
    [slice.period, slice.from, slice.to, offset, tick], // eslint-disable-line react-hooks/exhaustive-deps
  )
  const rangeError = 'error' in resolved ? resolved.error : null
  const readKey = `${scope}|${slice.period}|${slice.from}|${slice.to}`

  useEffect(() => {
    if (slice.period === 'custom') return
    const id = window.setInterval(() => setTick((n) => n + 1), REFRESH_MS)
    return () => window.clearInterval(id)
  }, [slice.period])

  // A change of what is being read dims the page; a refresh of the same read
  // does not, or the page would flicker once a minute.
  useEffect(() => setLoading(true), [readKey])

  useEffect(() => {
    if ('error' in resolved) {
      setLoading(false)
      return
    }
    let cancelled = false
    const { range } = resolved
    const read =
      scope === 'people' ? api.byPerson(range).then((rows) => !cancelled && setPeople(rows))
      : scope === 'total' ? api.total(range).then((row) => !cancelled && setTotal(row))
      : api.mine(range).then((row) => !cancelled && setMine(row))

    read
      .then(() => {
        if (cancelled) return
        setError(null)
        setEndsNow(resolved.endsNow)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Could not read usage.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [scope, resolved])

  // The models the picker offers: whatever this scope used in the window,
  // busiest first, plus any chosen one the window no longer holds — so
  // changing the period never silently drops part of a selection.
  const models = useMemo(() => {
    const tokens = new Map<string, number>()
    const scopes = scope === 'people' ? people ?? [] : [scope === 'total' ? total : mine]
    for (const one of scopes) {
      for (const model of one?.models ?? []) {
        tokens.set(model.model, (tokens.get(model.model) ?? 0) + model.prompt_tokens + model.completion_tokens)
      }
    }
    const ranked = [...tokens.entries()].sort((a, b) => b[1] - a[1]).map(([name]) => name)
    for (const chosen of slice.models) if (!tokens.has(chosen)) ranked.push(chosen)
    return ranked
  }, [scope, people, total, mine, slice.models])

  const chooseModels = (models: string[]) => setSlice({ models })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Filters
        slice={slice}
        onChange={setSlice}
        models={models}
        offset={offset}
        error={rangeError}
      />
      {error && <ErrorNote>{error}</ErrorNote>}
      {scope === 'people' ? (
        <People
          rows={people}
          loading={loading}
          models={slice.models}
          endsNow={endsNow}
          offset={offset}
          onModels={chooseModels}
        />
      ) : (
        <UsageBody
          series={scope === 'total' ? total : mine}
          loading={loading}
          models={slice.models}
          endsNow={endsNow}
          offset={offset}
          onModels={chooseModels}
        >
          {scope === 'total' && total && slice.models.length === 0 && total.unattributed > 0 && (
            <Unattributed total={total} />
          )}
        </UsageBody>
      )}
    </div>
  )
}

// ── the filter row ────────────────────────────────────────────────────────
function Filters({
  slice, onChange, models, offset, error,
}: {
  slice: Slice
  onChange: (patch: Partial<Slice>) => void
  models: string[]
  offset: number
  error: string | null
}) {
  const choosePeriod = (period: PeriodKey) => {
    if (period !== 'custom') {
      onChange({ period, from: '', to: '' })
      return
    }
    // Custom opens on the week up to now, so both fields start filled and the
    // page never waits on a range it cannot read.
    const now = Date.now()
    onChange({
      period,
      from: slice.from || toLocalInput(now - 7 * 86_400_000, offset),
      to: slice.to || toLocalInput(now, offset),
    })
  }

  const dateStyle: React.CSSProperties = { ...inputStyle, width: 'auto', padding: '4px 9px', fontSize: 12.5 }

  return (
    <div className="rm-usage-filters" role="group" aria-label="Filters">
      <div className="rm-usage-filter">
        <span className="rm-usage-filter-label" aria-hidden="true">Period</span>
        <Segmented<PeriodKey>
          ariaLabel="Period"
          value={slice.period}
          onChange={choosePeriod}
          options={[
            ...PRESETS.map((preset) => ({ value: preset.key, label: preset.label, title: preset.title })),
            { value: 'custom' as const, label: 'Custom', title: 'Choose a start and an end' },
          ]}
        />
      </div>

      {slice.period === 'custom' && (
        <div className="rm-usage-range">
          <label className="rm-usage-filter">
            <span className="rm-usage-filter-label">From</span>
            <input
              type="datetime-local"
              value={slice.from}
              max={slice.to || undefined}
              onChange={(event) => onChange({ from: event.target.value })}
              style={dateStyle}
            />
          </label>
          <label className="rm-usage-filter">
            <span className="rm-usage-filter-label">To</span>
            <input
              type="datetime-local"
              value={slice.to}
              min={slice.from || undefined}
              onChange={(event) => onChange({ to: event.target.value })}
              style={dateStyle}
            />
          </label>
        </div>
      )}

      <div className="rm-usage-filter rm-usage-model-filter">
        <span className="rm-usage-filter-label" id="usage-models-label">Models</span>
        <ModelPicker
          options={models}
          selected={slice.models}
          onChange={(next) => onChange({ models: next })}
        />
      </div>

      {error && (
        <p className="rm-usage-range-error" role="alert">
          <Icon.Alert size={13} />
          <span>{error}</span>
        </p>
      )}
    </div>
  )
}

/**
 * The model filter in the filter row: a button that opens a checklist.
 *
 * A native `<select multiple>` is a scrolling box that needs a modifier key
 * to pick a second item, which is the opposite of what a filter wants. So the
 * list is a popover of checkboxes that stays open while models are ticked, and
 * "All models" at its head clears the set. It edits the same selection the By
 * model rows toggle — two doors onto one filter.
 */
function ModelPicker({
  options, selected, onChange,
}: {
  options: string[]
  selected: string[]
  onChange: (next: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setOpen(false)
      trigger.current?.focus()
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const label = selected.length === 0
    ? 'All models'
    : selected.length === 1
      ? splitModelName(selected[0]).name
      : `${selected.length} models`

  return (
    <div ref={ref} className="rm-usage-picker">
      <button
        ref={trigger}
        type="button"
        className="rm-usage-picker-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-labelledby="usage-models-label usage-models-value"
        onClick={() => setOpen((value) => !value)}
      >
        <span id="usage-models-value" className="rm-usage-picker-value">{label}</span>
        <Icon.Chevron open={open} size={13} stroke="var(--text-faint)" />
      </button>

      {open && (
        <div className="rm-usage-picker-panel" role="listbox" aria-multiselectable="true" aria-label="Models">
          <button
            type="button"
            role="option"
            aria-selected={selected.length === 0}
            className="rm-usage-option rm-menu-item"
            onClick={() => onChange([])}
          >
            <span className="rm-usage-check" aria-hidden="true">
              {selected.length === 0 && <Icon.Check size={11} strokeWidth={3} />}
            </span>
            <span className="rm-usage-option-label">All models</span>
          </button>
          {options.length > 0 && <div className="rm-usage-picker-rule" role="presentation" />}
          {options.map((model) => {
            const on = selected.includes(model)
            return (
              <button
                key={model}
                type="button"
                role="option"
                aria-selected={on}
                className="rm-usage-option rm-menu-item"
                onClick={() => onChange(toggleModel(selected, model))}
                title={model || UNRECORDED_MODEL}
              >
                <span className="rm-usage-check" aria-hidden="true">
                  {on && <Icon.Check size={11} strokeWidth={3} />}
                </span>
                <ModelName model={model} />
              </button>
            )
          })}
          {options.length === 0 && (
            <p className="rm-usage-quiet" style={{ padding: '6px 10px' }}>No models in this period.</p>
          )}
        </div>
      )}
    </div>
  )
}

// ── one scope, rendered ───────────────────────────────────────────────────
/**
 * One scope: the summary, the timeline, and the models.
 *
 * Shared rather than written per scope — your own usage, one other person's
 * and the all-users total are the same answer about different rows, and three
 * renderings of it would be three places for the partiality sentence to be
 * dropped from.
 */
function UsageBody({
  series, loading, models, endsNow, offset, onModels, children,
}: {
  series: UsageSeries | null
  loading: boolean
  models: string[]
  endsNow: boolean
  offset: number
  onModels: (models: string[]) => void
  /** Anything the scope adds to the summary — the total's own gap, say. */
  children?: React.ReactNode
}) {
  const view = useMemo(() => (series ? scopeView(series, models) : null), [series, models])
  const totals = useMemo(() => (view ? usageTotals(view.figures) : null), [view])
  const slots = useMemo(
    () => (series && view
      ? denseSlots(view.buckets, series.since, series.until, series.bucket_seconds)
      : []),
    [series, view],
  )

  if (!series || !totals) return loading ? <Loading /> : null

  const start = formatInstant(Date.parse(series.since), offset)
  const end = endsNow ? 'now' : formatInstant(Date.parse(series.until), offset)
  const span = `${start} – ${end}`
  const modelName = selectionLabel(models)
  const emptyTitle = models.length === 0
    ? 'No usage in this period'
    : models.length === 1
      ? `${modelName} was not used in this period`
      : `None of the ${models.length} chosen models was used in this period`

  return (
    // A refetch holds the previous render at reduced opacity rather than
    // dropping back to the spinner, so the figure being compared against
    // stays on screen.
    <div className="rm-usage-body" data-loading={loading || undefined}>
      {totals.empty ? (
        <>
          <EmptyState
            icon={<Icon.Bars size={20} />}
            title={emptyTitle}
            body={`Nothing ran between ${start} and ${end}. Try a longer period${modelName ? ', or all models' : ''}.`}
          />
          {children}
        </>
      ) : (
        <>
          <Summary totals={totals} caption={modelName ? `${modelName} · ${span}` : span}>
            {children}
          </Summary>
          <Timeline
            slots={slots}
            bucketSeconds={series.bucket_seconds}
            endsNow={endsNow}
            offset={offset}
            span={span}
            modelName={modelName}
          />
        </>
      )}
      <ByModel series={series} selected={models} onSelect={onModels} />
    </div>
  )
}

function Loading() {
  return (
    <div style={{ display: 'grid', placeItems: 'center', padding: 60 }}>
      <Spinner size={18} />
    </div>
  )
}

/**
 * The headline: how many tokens, how they split, how many operations.
 *
 * One panel rather than a row of equal tiles, because the figures are not
 * equal: the total is the answer, the split is its shape, and the operations
 * are its context. The split bar and its keys use the timeline's two colours,
 * so the summary also teaches the chart below it.
 */
function Summary({
  totals, caption, children,
}: {
  totals: UsageTotals
  caption: string
  children?: React.ReactNode
}) {
  const colors = useSegmentColors()
  const share = totals.inputShare
  return (
    <section className="rm-usage-panel rm-usage-summary" aria-label="Summary">
      <div className="rm-usage-summary-main">
        <div className="rm-usage-summary-head">
          <span className="rm-usage-label">Tokens</span>
          <span className="rm-usage-caption">{caption}</span>
        </div>
        <div className="rm-usage-hero">{totals.tokens ?? '—'}</div>
        {share !== null && (
          <>
            <SplitBar input={share} output={1 - share} colors={colors} label="Input and output share" />
            <div className="rm-usage-keys">
              <Key color={colors.input} label="Input" value={formatTokens(totals.promptTokens)} share={formatShare(share)} />
              <Key color={colors.output} label="Output" value={formatTokens(totals.completionTokens)} share={formatShare(1 - share)} />
            </div>
          </>
        )}
      </div>
      <dl className="rm-usage-summary-side">
        <div>
          <dt>Operations</dt>
          <dd>{formatTokens(totals.runs)}</dd>
        </div>
        <div>
          <dt>Tokens per operation</dt>
          <dd>{totals.perOperation === null ? '—' : formatTokens(totals.perOperation)}</dd>
        </div>
        {/* Shown only where something reported a cache figure. An em dash here
            would read as "nothing was cached", and most providers report
            nothing at all — two facts a blank row cannot tell apart, so the
            row is absent instead. */}
        {totals.cacheRead !== null && (
          <div>
            <dt>Served from cache</dt>
            <dd>{totals.cacheRead}</dd>
          </div>
        )}
      </dl>
      {(totals.unmeasuredNote || totals.cacheNote || children) && (
        <div className="rm-usage-notes">
          {totals.unmeasuredNote && (
            <p className="rm-usage-note">
              <Icon.Info size={13} />
              <span>{totals.unmeasuredNote}</span>
            </p>
          )}
          {totals.cacheNote && (
            <p className="rm-usage-note">
              <Icon.Info size={13} />
              <span>{totals.cacheNote}. These tokens are part of the input figure, not additional to it.</span>
            </p>
          )}
          {children}
        </div>
      )}
    </section>
  )
}

function Key({ color, label, value, share }: { color: string; label: string; value: string; share: string }) {
  return (
    <span className="rm-usage-key">
      <span className="rm-usage-swatch" style={{ background: color }} aria-hidden="true" />
      <span className="rm-usage-key-label">{label}</span>
      <strong>{value}</strong>
      <span className="rm-usage-key-share">{share}</span>
    </span>
  )
}

/**
 * A share bar: input then output, each segment its fraction of the track.
 *
 * The filled part is its own rounded box, so its right end is a data end
 * rather than a cut; the track under it is the whole the fractions are of.
 */
function SplitBar({
  input, output, colors, label, thin = false,
}: {
  input: number
  output: number
  colors: { input: string; output: string }
  label: string
  thin?: boolean
}) {
  const filled = Math.max(0, Math.min(1, input + output))
  const inputPart = filled > 0 ? input / filled : 0
  return (
    <div className={thin ? 'rm-usage-bar is-thin' : 'rm-usage-bar'} role="img" aria-label={label}>
      <div className="rm-usage-bar-fill" style={{ width: `${filled * 100}%` }}>
        {input > 0 && <span style={{ flexGrow: inputPart, background: colors.input }} />}
        {output > 0 && <span style={{ flexGrow: 1 - inputPart, background: colors.output }} />}
      </div>
    </div>
  )
}

/** The timeline panel: the chart, or its table twin, over the same slots. */
function Timeline({
  slots, bucketSeconds, endsNow, offset, span, modelName,
}: {
  slots: Slot[]
  bucketSeconds: number
  endsNow: boolean
  offset: number
  span: string
  modelName: string | null
}) {
  const [asTable, setAsTable] = useState(false)
  const colors = useSegmentColors()
  return (
    <section className="rm-usage-panel" aria-labelledby="usage-timeline-title">
      <header className="rm-usage-panel-head">
        <div className="rm-usage-panel-titles">
          <h2 id="usage-timeline-title" className="rm-usage-h2">
            Tokens over time
            {modelName && <span className="rm-usage-h2-aside"> · {modelName}</span>}
          </h2>
          <span className="rm-usage-caption">{bucketLabel(bucketSeconds)} · {span}</span>
        </div>
        <div className="rm-usage-panel-tools">
          {!asTable && (
            <div className="rm-usage-legend">
              {/* Only the series these columns contain: most providers report
                  no caching, and a legend naming a colour that never appears
                  teaches a distinction the chart is not making. */}
              {presentSegments(slots).map((segment) => (
                <span key={segment.key} className="rm-usage-key">
                  <span className="rm-usage-swatch" style={{ background: colors[segment.key] }} aria-hidden="true" />
                  <span className="rm-usage-key-label">{segment.label}</span>
                </span>
              ))}
            </div>
          )}
          <Segmented<'chart' | 'table'>
            ariaLabel="Show as"
            value={asTable ? 'table' : 'chart'}
            onChange={(next) => setAsTable(next === 'table')}
            options={[
              { value: 'chart', label: <><Icon.Bars size={13} /><span className="rm-sr-only">Chart</span></>, title: 'Show as a chart' },
              { value: 'table', label: <><Icon.List size={13} /><span className="rm-sr-only">Table</span></>, title: 'Show as a table' },
            ]}
          />
        </div>
      </header>
      {asTable ? (
        <UsageSlotTable slots={slots} bucketSeconds={bucketSeconds} offsetMinutes={offset} endsNow={endsNow} />
      ) : (
        <UsageTimeline
          slots={slots}
          bucketSeconds={bucketSeconds}
          offsetMinutes={offset}
          endsNow={endsNow}
          label={`Tokens over time, ${modelName ?? 'all models'}, ${span}`}
        />
      )}
    </section>
  )
}

// ── ranked breakdowns ─────────────────────────────────────────────────────
/**
 * One scope's tokens, split by the model that used them — and the model filter.
 *
 * Ranked rows over a share bar rather than a table of figures: the question is
 * *"which model, and how much of the whole"*, and the bar answers the second
 * half before a digit is read. Each bar is split input/output in the
 * timeline's colours, so a model that is mostly prompt reads differently from
 * one that writes long answers.
 *
 * **Each row toggles a model in the filter**, and any number may be on at once.
 * The rows stay the whole scope while some are chosen — the breakdown is how
 * models are compared, and filtering it down to the chosen ones would leave
 * nothing to compare them against — so chosen rows are ticked rather than the
 * others hidden. Taking the last one out shows all models again.
 */
function ByModel({
  series, selected, onSelect,
}: {
  series: UsageSeries
  selected: string[]
  onSelect: (models: string[]) => void
}) {
  const rows = useMemo(() => modelRows(series), [series])
  if (rows.length === 0) return null

  return (
    <section className="rm-usage-panel" aria-labelledby="usage-models-title">
      <header className="rm-usage-panel-head">
        <div className="rm-usage-panel-titles">
          <h2 id="usage-models-title" className="rm-usage-h2">By model</h2>
          <span className="rm-usage-caption">
            {rows.length === 1 ? 'One model' : `${rows.length} models`} in this period ·{' '}
            {selected.length === 0
              ? 'select any to filter the page'
              : `${selected.length} chosen · select again to take one out`}
          </span>
        </div>
        {selected.length > 0 && (
          <button type="button" className="rm-usage-clear" onClick={() => onSelect([])}>
            <Icon.Close size={12} />
            All models
          </button>
        )}
      </header>
      <RankedList
        head="Model"
        rows={rows}
        render={(row) => <ModelName model={row.key} />}
        isOn={(row) => selected.includes(row.key)}
        mode="filter"
        onToggle={(row) => onSelect(toggleModel(selected, row.key))}
      />
    </section>
  )
}

function ModelName({ model }: { model: string }) {
  const { path, name } = splitModelName(model)
  return (
    <span className="rm-usage-name" dir="ltr" title={model || UNRECORDED_MODEL}>
      {path && <span className="rm-usage-name-path">{path}</span>}
      <span className={model ? 'rm-usage-name-main' : 'rm-usage-name-main is-unrecorded'}>{name}</span>
    </span>
  )
}

/**
 * Rows of name · tokens · share · operations, each over its share bar.
 *
 * One component for both breakdowns — models and people — so the two read
 * alike and a column cannot drift between them. Every figure comes from
 * `rankedRow`, so an unmeasured row shows an em dash here for exactly the
 * reason the summary does.
 */
function RankedList<T extends RankedRow>({
  head, rows, render, isOn, onToggle, mode,
}: {
  head: string
  rows: T[]
  render: (row: T) => React.ReactNode
  isOn: (row: T) => boolean
  onToggle: (row: T) => void
  /** A filter is `aria-pressed`; opening a person below is `aria-expanded`. */
  mode: 'filter' | 'disclose'
}) {
  const colors = useSegmentColors()
  return (
    <div className="rm-usage-rows">
      <div className="rm-usage-row is-head" aria-hidden="true">
        <span className="rm-usage-row-name">{head}</span>
        <span className="rm-usage-num">Tokens</span>
        <span className="rm-usage-num rm-usage-col-share">Share</span>
        <span className="rm-usage-num rm-usage-col-ops">Operations</span>
      </div>
      <ul className="rm-usage-row-list">
        {rows.map((row) => {
          const on = isOn(row)
          return (
            <li key={row.key}>
              <button
                type="button"
                className="rm-usage-row"
                aria-pressed={mode === 'filter' ? on : undefined}
                aria-expanded={mode === 'disclose' ? on : undefined}
                onClick={() => onToggle(row)}
              >
                <span className="rm-usage-row-name">
                  {mode === 'filter' && (
                    <span className="rm-usage-check" aria-hidden="true">
                      {on && <Icon.Check size={11} strokeWidth={3} />}
                    </span>
                  )}
                  {render(row)}
                </span>
                <span className="rm-usage-num rm-usage-strong">{row.tokens ?? '—'}</span>
                <span className="rm-usage-num rm-usage-col-share">{row.share ?? '—'}</span>
                <span className="rm-usage-num rm-usage-col-ops">{formatTokens(row.runs)}</span>
                <span className="rm-usage-row-bar">
                  <SplitBar
                    thin
                    input={row.inputFraction}
                    output={row.outputFraction}
                    colors={colors}
                    label={row.share ? `${row.share} of the tokens` : 'No measured tokens'}
                  />
                  <span className="rm-usage-row-detail">
                    <span className="rm-usage-detail-narrow">
                      {row.share ?? '—'} · {formatTokens(row.runs)} {row.runs === 1 ? 'op' : 'ops'} ·{' '}
                    </span>
                    {row.input ?? '—'} in · {row.output ?? '—'} out
                  </span>
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/**
 * The gap between the all-users total and the sum of the people.
 *
 * `/usage/users` inner joins `users` and `/usage/total` joins nothing, so a
 * deleted person's usage leaves the first and stays in the second. That is
 * deliberate — an outer join would attribute it to whoever remains — and it
 * means this total is legitimately larger than the list beside it. Stated
 * here, because a reader who adds the people up and finds a shortfall has been
 * given a reason to disbelieve both numbers.
 */
function Unattributed({ total }: { total: UsageTotal }) {
  if (total.unattributed <= 0) return null
  const tokens = total.unattributed_tokens
  const spent = tokens > 0 ? `, ${formatTokens(tokens)} tokens in all,` : ''
  return (
    <p className="rm-usage-note">
      <Icon.Info size={13} />
      <span>
        {total.unattributed === 1
          ? `One of these ${total.runs} operations${spent} was run by somebody who has `
          : `${total.unattributed} of these ${total.runs} operations${spent} were run by people who have `}
        since been deleted. They are counted here and not under People, which
        lists only people who still exist.
      </span>
    </p>
  )
}

/**
 * Everybody, one row each, busiest first — under the same period and model.
 *
 * Selecting a person draws their own summary, timeline and models below, from
 * the rows already in hand: `/usage/users` returns every series whole, so
 * opening somebody costs no request. With models chosen, each person is
 * ranked by those models alone, and people who used none of them are left out.
 */
function People({
  rows, loading, models, endsNow, offset, onModels,
}: {
  rows: UsageSeries[] | null
  loading: boolean
  models: string[]
  endsNow: boolean
  offset: number
  onModels: (models: string[]) => void
}) {
  const [openId, setOpenId] = useState<string | null>(null)

  const ranked = useMemo(() => {
    const views = (rows ?? [])
      .map((series) => ({ series, figures: scopeView(series, models).figures }))
      .filter(({ figures }) => figures.runs > 0)
    const whole = views.reduce(
      (sum, { figures }) => sum + figures.prompt_tokens + figures.completion_tokens,
      0,
    )
    return views
      .map(({ series, figures }) => ({
        ...rankedRow(series.actor_id ?? series.actor, figures, whole),
        series,
      }))
      // Busiest first — the backend orders by display name, which is right
      // for a picker and wrong for a ranking. Ties fall back to the name.
      .sort((a, b) => b.totalTokens - a.totalTokens || a.series.actor.localeCompare(b.series.actor))
  }, [rows, models])

  const open = ranked.find((row) => row.key === openId) ?? null

  if (!rows) return loading ? <Loading /> : null

  const modelName = selectionLabel(models)

  if (ranked.length === 0) {
    return (
      <div className="rm-usage-body" data-loading={loading || undefined}>
        <EmptyState
          icon={<Icon.Bars size={20} />}
          title={models.length === 0
            ? 'Nobody used any tokens in this period'
            : models.length === 1
              ? `Nobody used ${modelName} in this period`
              : `Nobody used the ${models.length} chosen models in this period`}
          body={`No questions, reports or layer generations ran in this period. Try a longer one${modelName ? ', or all models' : ''}.`}
        />
      </div>
    )
  }

  return (
    <div className="rm-usage-body" data-loading={loading || undefined}>
      <section className="rm-usage-panel" aria-labelledby="usage-people-title">
        <header className="rm-usage-panel-head">
          <div className="rm-usage-panel-titles">
            <h2 id="usage-people-title" className="rm-usage-h2">
              People
              {modelName && <span className="rm-usage-h2-aside"> · {modelName}</span>}
            </h2>
            <span className="rm-usage-caption">
              {ranked.length === 1 ? 'One person' : `${ranked.length} people`} · select someone to see their usage
            </span>
          </div>
        </header>
        <RankedList
          head="Who"
          rows={ranked}
          render={(row) => (
            <span className="rm-usage-name-main" dir={dirOf(row.series.actor)}>{row.series.actor}</span>
          )}
          isOn={(row) => row.key === openId}
          mode="disclose"
          onToggle={(row) => setOpenId(row.key === openId ? null : row.key)}
        />
      </section>

      {open && (
        <section className="rm-usage-person" aria-labelledby="usage-person-name">
          <h2 id="usage-person-name" className="rm-usage-person-name" dir={dirOf(open.series.actor)}>
            {open.series.actor}
          </h2>
          {/* The same rendering as every other scope, on this person's rows —
              which keeps the qualifying sentence from being a thing only the
              tabs above remember to print. */}
          <UsageBody
            key={open.key}
            series={open.series}
            loading={false}
            models={models}
            endsNow={endsNow}
            offset={offset}
            onModels={onModels}
          />
        </section>
      )}
    </div>
  )
}
