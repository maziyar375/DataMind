/**
 * What the models have been asked to do, and what it cost.
 *
 * Every figure on this screen already existed as a column — `0023` landed
 * per-call accounting on `runs`, `report_runs` and `semantic_jobs`, and
 * nothing read it back. A number nobody can see answers *"what are we
 * spending"* exactly as badly as a number nobody records.
 *
 * **Counts, never content.** An integer, a price and a day. No question, no
 * SQL, no prose ever reaches this page — which is what makes reading somebody
 * else's usage a capability an Auditor may hold rather than a disclosure of
 * their work. `usage.read` gates the two scopes that are about other people;
 * your own needs nothing, because the scope *is* you.
 *
 * **The partiality is the feature, not a caveat.** A provider that reports no
 * usage block and a model litellm cannot price are both ordinary, and a total
 * that quietly absorbs them reports a deployment as free. `usageTotals` turns
 * the two counts on the wire into sentences, and they are rendered **beside**
 * the number rather than under it: a footnote is a thing a reader finds after
 * they have already believed the figure.
 */
import { useEffect, useMemo, useState } from 'react'
import { usage as api } from '../api/client'
import type { UsageSeries } from '../api/types'
import { EmptyState, ErrorNote, Icon, PageHeader, Segmented, Spinner } from '../components/ui'
import { VegaChart } from '../components/VegaChart'
import {
  formatTokens, usageSpec, usageTotals, windowSince, WINDOW_DAYS,
  type WindowDays,
} from '../components/usage-chart'

export default function UsagePage() {
  const [days, setDays] = useState<WindowDays>(30)
  const [mine, setMine] = useState<UsageSeries | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api
      .mine({ since: windowSince(days) })
      .then((series) => {
        if (cancelled) return
        setMine(series)
        setError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Could not read your usage.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [days])

  return (
    <div className="rm-index rm-page-pad" style={{ flex: 1, overflowY: 'auto' }}>
      <PageHeader
        title="Token usage"
        subtitle="What the models were asked to do, and what it cost. Counts only — no question, no answer and no SQL is on this page."
        // One control, above everything it scopes. Every figure and every bar
        // below reads the same window, so there is nothing per-chart to set
        // and nothing that can end up scoped differently from the chart
        // beside it.
        actions={<WindowPicker value={days} onChange={setDays} />}
      />

      {error && <ErrorNote>{error}</ErrorNote>}

      <UsageBody series={mine} loading={loading} />
    </div>
  )
}

/** 7 / 30 / 90 days. The buckets are days, so the window is counted in them. */
function WindowPicker({
  value, onChange,
}: {
  value: WindowDays
  onChange: (next: WindowDays) => void
}) {
  return (
    <Segmented
      ariaLabel="Window"
      value={String(value)}
      onChange={(next) => onChange(Number(next) as WindowDays)}
      options={WINDOW_DAYS.map((count) => ({
        value: String(count),
        label: `${count} days`,
      }))}
    />
  )
}

/**
 * One scope, rendered: the figures, what qualifies them, and the shape.
 *
 * Shared rather than written per scope — your own usage, one other person's,
 * and the installation total are the same answer about different rows, and
 * three renderings of it would be three places for the partiality sentences
 * to be dropped from.
 */
export function UsageBody({
  series, loading, children,
}: {
  series: UsageSeries | null
  loading: boolean
  /** Anything the scope adds to the summary — the total's own gap, say. */
  children?: React.ReactNode
}) {
  const totals = useMemo(() => (series ? usageTotals(series) : null), [series])
  // Memoised because `VegaChart` re-embeds whenever the spec's *identity*
  // changes, and a spec rebuilt on every render would redraw the plot on
  // every keystroke anywhere above it.
  const spec = useMemo(() => (series ? usageSpec(series.buckets) : null), [series])

  if (!series || !totals) {
    return loading ? (
      <div style={{ display: 'grid', placeItems: 'center', padding: 60 }}>
        <Spinner size={18} />
      </div>
    ) : null
  }

  return (
    // A refetch holds the previous render at reduced opacity rather than
    // dropping back to the spinner: the window picker changes one number on
    // every tile, and blanking the screen to say so costs the reader the
    // figure they were comparing against.
    <div
      style={{
        opacity: loading ? 0.55 : 1,
        transition: 'opacity .18s ease',
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
      }}
    >
      {totals.empty ? (
        <EmptyState
          icon={<Icon.Bars size={20} />}
          title="Nothing in this window"
          body="No questions, reports or layer generations ran in these days — so there is nothing to count. Try a longer window."
        />
      ) : (
        <>
          <Figures totals={totals} />
          <Qualifiers totals={totals}>{children}</Qualifiers>
          {spec && <VegaChart spec={spec} />}
        </>
      )}
    </div>
  )
}

/**
 * The numbers, as tiles.
 *
 * Proportional figures, not `tabular-nums`: these sit in a row and align with
 * nothing below them, and equal-width digits make a short number look loose
 * at this size. Tabular is for a column of numbers that must line up, which
 * is the per-person list and not this.
 */
function Figures({ totals }: { totals: ReturnType<typeof usageTotals> }) {
  return (
    <div
      style={{
        display: 'grid',
        // Wraps to one column on a phone without a media query, and never
        // gives the page a second fixed column at any width.
        gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
        gap: 10,
      }}
    >
      <Figure label="Tokens" value={totals.tokens} />
      {/* The split is shown only where the whole is: if nothing in the window
          reported a count, its halves are just as unmeasured, and two zeros
          under an em dash would contradict the tile beside them. */}
      <Figure
        label="Input"
        value={totals.tokens && formatTokens(totals.promptTokens)}
      />
      <Figure
        label="Output"
        value={totals.tokens && formatTokens(totals.completionTokens)}
      />
      <Figure label="Cost" value={totals.cost} />
      <Figure label="Operations" value={String(totals.runs)} />
    </div>
  )
}

function Figure({ label, value }: { label: string; value: string | null }) {
  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: 10,
        background: 'var(--panel)',
        padding: '12px 14px',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        minWidth: 0,
      }}
    >
      <span style={{ fontSize: 11.5, color: 'var(--text-dim)' }}>{label}</span>
      <span
        style={{
          fontSize: 23,
          fontWeight: 650,
          lineHeight: 1.15,
          letterSpacing: '-0.02em',
          color: value === null ? 'var(--text-faint)' : 'var(--text-strong)',
          overflowWrap: 'anywhere',
        }}
      >
        {/* An em dash, never `0`. A figure nobody measured and a figure that
            measured nothing are different facts, and `0` is the second one —
            see `usageTotals`, which is where the two are told apart. */}
        {value ?? '—'}
      </span>
    </div>
  )
}

/**
 * What the figures above do not say on their own.
 *
 * Prose, in the reader's own language, beside the numbers — never a colour,
 * never an asterisk. Each sentence appears only when it is true, so a whole
 * total carries no chrome at all and a partial one cannot be read as whole.
 */
function Qualifiers({
  totals, children,
}: {
  totals: ReturnType<typeof usageTotals>
  children?: React.ReactNode
}) {
  const lines = [totals.unmeasuredNote, totals.costNote].filter(Boolean) as string[]
  if (lines.length === 0 && !children) return null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      {lines.map((line) => (
        <p
          key={line}
          style={{ margin: 0, fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-dim)' }}
        >
          {line}
        </p>
      ))}
      {children}
    </div>
  )
}
