/**
 * How many tokens the models have used, and on which models.
 *
 * Every figure on this screen already existed as a column — `0023` landed
 * per-call accounting on `runs`, `report_runs` and `semantic_jobs`, and
 * nothing read it back. A number nobody can see answers *"what are we
 * spending"* exactly as badly as a number nobody records.
 *
 * **Counts, never content.** An integer, a model name and a day. No question, no
 * SQL, no prose ever reaches this page — which is what makes reading somebody
 * else's usage a capability an Auditor may hold rather than a disclosure of
 * their work.
 *
 * **Three scopes, and the gate is between them rather than inside one.** Your
 * own needs no capability, because the scope *is* you and no control on this
 * page can widen it; the two that are about other people need `usage.read`
 * and are tabs this section declines to render without it. The backend
 * refuses them either way — what is here is an affordance, never the
 * boundary, which is what the `curl` in the phase's verification proves.
 *
 * **The partiality is the feature, not a caveat.** A provider that reports no
 * usage block is ordinary, and a total that quietly absorbs it reports less
 * work than was done. `usageTotals` turns the count on the wire into a
 * sentence, and it is rendered **beside** the number rather than under it: a
 * footnote is a thing a reader finds after they have already believed the
 * figure.
 */
import { useEffect, useMemo, useState } from 'react'
import { Navigate, useMatch, useNavigate } from 'react-router-dom'
import { usage as api } from '../api/client'
import type { UsageSeries, UsageTotal } from '../api/types'
import {
  EmptyState, ErrorNote, Icon, PageHeader, Segmented, Spinner, dirOf,
} from '../components/ui'
import { Tabs } from '../components/settings'
import { VegaChart } from '../components/VegaChart'
import { useCan, type Capability } from '../permissions'
import {
  formatTokens, modelRows, usageSpec, usageTotals, windowSince, WINDOW_DAYS,
  type UsageTotals, type WindowDays,
} from '../components/usage-chart'

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

export default function UsagePage() {
  const can = useCan()
  const navigate = useNavigate()
  // A sub-route, so the section stays mounted across a tab change and every
  // tab is an address somebody can link to. `/usage` itself is `me`, which is
  // the scope everybody has — so the section's own root is never a redirect
  // for the people who can reach only one of the three.
  const routeTab = useMatch('/usage/:tab')?.params.tab ?? 'me'
  const [days, setDays] = useState<WindowDays>(30)

  const visible = useMemo(
    () => SCOPES.filter((scope) => !scope.needs || can(scope.needs)),
    [can],
  )
  const active = visible.find((scope) => scope.value === routeTab)

  // A path somebody typed, or a link from when they held the capability. The
  // rail's own row still works; only the scope they cannot read is refused,
  // and the refusal is the scope they can.
  if (!active) return <Navigate to="/usage" replace />

  const header = (
    <PageHeader
      title="Token usage"
      subtitle="How many tokens the models used, and on which models. Counts only — no question, no answer and no SQL is on this page."
      // One control, above everything it scopes. Every figure and every bar
      // below reads the same window, whichever tab is open, so there is
      // nothing per-chart to set and nothing that can end up scoped
      // differently from the chart beside it.
      actions={<WindowPicker value={days} onChange={setDays} />}
    />
  )

  const body = <Scope scope={active.value} days={days} />

  // One tab is not a tab strip. Somebody without `usage.read` sees the page
  // they saw before the other two scopes existed — a plain index — rather
  // than a section band closed by a strip of one, which reads as a navigation
  // that has lost its other half.
  if (visible.length === 1) {
    return (
      <div className="rm-index rm-page-pad" style={{ flex: 1, overflowY: 'auto' }}>
        {header}
        {body}
      </div>
    )
  }

  return (
    // The section surface, as Administration wears it: one accent wash thrown
    // from above the title so it runs behind the header *and* the strip. A
    // per-tab `rm-index` would restart the wash under the tabs, which is the
    // hard horizontal seam `styles.css` describes at `.rm-section`.
    <div
      className="rm-section"
      style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 }}
    >
      <div className="rm-section-head">
        <div className="rm-section-title">{header}</div>
        {/* 18, not the default 28 — the strip is on the index gutter and a
            tab keeps 14px for its hover pill, so this is what lands the first
            label on the same edge as the title above it. */}
        <Tabs
          value={active.value}
          gutter={18}
          onChange={(next) => navigate(next === 'me' ? '/usage' : `/usage/${next}`)}
          items={visible.map((scope) => ({ value: scope.value, label: scope.label }))}
        />
      </div>
      <div className="rm-page-pad" style={{ flex: 1, overflowY: 'auto' }}>
        {body}
      </div>
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
 * One scope's read, and its rendering.
 *
 * Keyed by scope *and* window, and it re-reads on either — there is no cache
 * across tabs on purpose. The three answers are three different aggregations
 * of rows that are still being written, and a remembered one would be the
 * page quietly showing a figure from before the run that just finished.
 */
function Scope({ scope, days }: { scope: string; days: WindowDays }) {
  const [mine, setMine] = useState<UsageSeries | null>(null)
  const [people, setPeople] = useState<UsageSeries[] | null>(null)
  const [total, setTotal] = useState<UsageTotal | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const since = windowSince(days)
    setLoading(true)
    const read =
      scope === 'people' ? api.byPerson({ since }).then((rows) => !cancelled && setPeople(rows))
      : scope === 'total' ? api.total({ since }).then((row) => !cancelled && setTotal(row))
      : api.mine({ since }).then((row) => !cancelled && setMine(row))

    read
      .then(() => !cancelled && setError(null))
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
  }, [scope, days])

  return (
    <>
      {error && <ErrorNote>{error}</ErrorNote>}
      {scope === 'people' ? (
        <People rows={people} loading={loading} />
      ) : scope === 'total' ? (
        <UsageBody series={total} loading={loading}>
          {total && <Unattributed total={total} />}
        </UsageBody>
      ) : (
        <UsageBody series={mine} loading={loading} />
      )}
    </>
  )
}

/**
 * One scope, rendered: the figures, what qualifies them, the shape, and the
 * models.
 *
 * Shared rather than written per scope — your own usage, one other person's
 * and the all-users total are the same answer about different rows, and
 * three renderings of it would be three places for the partiality sentences
 * to be dropped from.
 */
function UsageBody({
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
  // every render above it.
  const spec = useMemo(() => (series ? usageSpec(series.buckets) : null), [series])

  if (!series || !totals) return loading ? <Loading /> : null

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
        <>
          <EmptyState
            icon={<Icon.Bars size={20} />}
            title="Nothing in this window"
            body="No questions, reports or layer generations ran in these days — so there is nothing to count. Try a longer window."
          />
          {children}
        </>
      ) : (
        <>
          <Figures totals={totals} />
          <Qualifiers totals={totals}>{children}</Qualifiers>
          {spec && <VegaChart spec={spec} />}
          <ByModel series={series} />
        </>
      )}
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
 * The numbers, as tiles.
 *
 * Proportional figures, not `tabular-nums`: these sit in a row and align with
 * nothing below them, and equal-width digits make a short number look loose
 * at this size. Tabular is for a column of numbers that must line up, which
 * is the per-person table and not this.
 */
function Figures({ totals }: { totals: UsageTotals }) {
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
      <Figure label="Input" value={totals.tokens && formatTokens(totals.promptTokens)} />
      <Figure label="Output" value={totals.tokens && formatTokens(totals.completionTokens)} />
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
  totals: UsageTotals
  children?: React.ReactNode
}) {
  const lines = [totals.unmeasuredNote].filter(Boolean) as string[]
  if (lines.length === 0 && !children) return null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      {lines.map((line) => (
        <Line key={line}>{line}</Line>
      ))}
      {children}
    </div>
  )
}

function Line({ children }: { children: React.ReactNode }) {
  return (
    <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-dim)' }}>
      {children}
    </p>
  )
}

/**
 * One scope's tokens, split by the model that used them.
 *
 * A table under the chart rather than a colour per model inside it: the chart
 * answers *"when"*, and stacking it by model as well would make the input and
 * output split — the thing it already draws — unreadable past two models.
 * This answers *"on what"*, which is a ranking, and a ranking reads as rows.
 *
 * Every scope renders it through `UsageBody`, so your own tab, one person's
 * row under People and the all-users total all split the same way.
 */
function ByModel({ series }: { series: UsageSeries }) {
  const rows = useMemo(() => modelRows(series), [series])
  if (rows.length === 0) return null

  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <h3
        style={{
          margin: 0,
          fontSize: 13,
          fontWeight: 650,
          color: 'var(--text-strong)',
        }}
      >
        By model
      </h3>
      <div style={{ overflowX: 'auto' }}>
        <table
          style={{
            width: '100%',
            minWidth: 520,
            borderCollapse: 'collapse',
            fontSize: 13,
          }}
        >
          <thead>
            <tr>
              <Th align="start">Model</Th>
              <Th>Tokens</Th>
              <Th>Share</Th>
              <Th>Input</Th>
              <Th>Output</Th>
              <Th>Operations</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <Td align="start">
                  <span
                    dir={dirOf(row.label)}
                    style={{ color: row.recorded ? 'var(--text-strong)' : 'var(--text-dim)' }}
                  >
                    {row.label}
                  </span>
                </Td>
                <Td>{row.tokens ?? '—'}</Td>
                <Td>{row.share ?? '—'}</Td>
                <Td>{row.input ?? '—'}</Td>
                <Td>{row.output ?? '—'}</Td>
                <Td>{row.runs}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

/**
 * The gap between the all-users total and the sum of the people.
 *
 * `/usage/users` inner joins `users` and `/usage/total` joins nothing, so a
 * deleted person's spend leaves the first and stays in the second. That is
 * deliberate — an outer join would attribute a departed person's spend to
 * whoever remains, which is the one answer that is wrong — and it means this
 * total is legitimately larger than the list beside it. Stated here, because
 * a reader who adds the people up and finds a shortfall has been given a
 * reason to disbelieve both numbers.
 */
function Unattributed({ total }: { total: UsageTotal }) {
  if (total.unattributed <= 0) return null
  const tokens = total.unattributed_tokens
  const spent = tokens > 0 ? `, ${formatTokens(tokens)} tokens in all,` : ''
  // Singular where it is one, for the reason `usageTotals` writes a scope of
  // one as one: "1 of these 5 operations were run by" is not a sentence, and
  // one departed person's single run is the likeliest way this line is ever
  // read for the first time.
  return (
    <Line>
      {total.unattributed === 1
        ? `One of these ${total.runs} operations${spent} was run by somebody who has `
        : `${total.unattributed} of these ${total.runs} operations${spent} were run by people who have `}
      since been deleted. They are counted here and not under People, which
      lists only people who still exist.
    </Line>
  )
}

/**
 * Everybody, one row each, busiest first.
 *
 * A table rather than a chart per person: the question this tab answers is
 * *"who"*, which is identity over a ranked magnitude, and forty stacked bars
 * is forty pictures of the same shape. Selecting a row draws that person's
 * days below — from the rows already in hand, since `/usage/users` returns
 * every series whole, so opening somebody costs no request.
 *
 * Every cell goes through `usageTotals`, so a person whose runs went
 * unmeasured shows an em dash here for exactly the reason the tiles do. A
 * table is the easiest place to lose that rule and the worst place to lose
 * it: a column of numbers with one silent zero in it reads as a fact.
 */
function People({ rows, loading }: { rows: UsageSeries[] | null; loading: boolean }) {
  const [openId, setOpenId] = useState<string | null>(null)

  const ranked = useMemo(
    () =>
      (rows ?? [])
        .map((row) => ({ row, totals: usageTotals(row) }))
        // Busiest first — the backend orders by display name, which is the
        // right order for a picker and the wrong one for a ranking. Ties
        // fall back to the name so the list is stable between reads.
        .sort(
          (a, b) =>
            b.totals.totalTokens - a.totals.totalTokens
            || a.row.actor.localeCompare(b.row.actor),
        ),
    [rows],
  )

  const open = ranked.find((entry) => entry.row.actor_id === openId) ?? null

  if (!rows) return loading ? <Loading /> : null

  if (ranked.length === 0) {
    return (
      <EmptyState
        icon={<Icon.Bars size={20} />}
        title="Nobody has used any tokens in this window"
        body="No questions, reports or layer generations ran in these days. Try a longer window."
      />
    )
  }

  return (
    <div
      style={{
        opacity: loading ? 0.55 : 1,
        transition: 'opacity .18s ease',
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
      }}
    >
      {/* The one element on this page allowed to be wider than the screen,
          and it scrolls in its own box rather than pushing the page sideways. */}
      <div style={{ overflowX: 'auto' }}>
        <table
          style={{
            width: '100%',
            minWidth: 480,
            borderCollapse: 'collapse',
            fontSize: 13,
          }}
        >
          <thead>
            <tr>
              <Th align="start">Who</Th>
              <Th>Tokens</Th>
              <Th>Input</Th>
              <Th>Output</Th>
              <Th>Operations</Th>
            </tr>
          </thead>
          <tbody>
            {ranked.map(({ row, totals }) => {
              const isOpen = row.actor_id === openId
              return (
                <tr
                  key={row.actor_id ?? row.actor}
                  style={{ background: isOpen ? 'var(--panel)' : undefined }}
                >
                  {/* The name is the control, rather than the whole row: a
                      `<tr onClick>` is not in the tab order and cannot be
                      pressed with a keyboard, and giving a row `tabIndex` and
                      a key handler is a button with the accessibility written
                      out by hand. A display name is also text a person wrote,
                      so it is read in its own direction — and it is a name,
                      never an address, which is what the API sends. */}
                  <Td align="start">
                    <button
                      type="button"
                      className="rm-usage-who"
                      dir={dirOf(row.actor)}
                      aria-expanded={isOpen}
                      onClick={() => setOpenId(isOpen ? null : row.actor_id)}
                    >
                      {row.actor}
                    </button>
                  </Td>
                  <Td>{totals.tokens ?? '—'}</Td>
                  <Td>{totals.tokens ? formatTokens(totals.promptTokens) : '—'}</Td>
                  <Td>{totals.tokens ? formatTokens(totals.completionTokens) : '—'}</Td>
                  <Td>{totals.runs}</Td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {open && (
        <div
          style={{
            borderTop: '1px solid var(--border)',
            paddingTop: 14,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}
        >
          <h2
            dir={dirOf(open.row.actor)}
            style={{
              margin: 0,
              fontSize: 15,
              fontWeight: 700,
              letterSpacing: '-0.02em',
              color: 'var(--text-strong)',
            }}
          >
            {open.row.actor}
          </h2>
          {/* The same rendering as every other scope, on this person's rows —
              which is what keeps the qualifying sentences from being a thing
              only the tabs above remember to print. */}
          <UsageBody key={open.row.actor_id} series={open.row} loading={false} />
        </div>
      )}
    </div>
  )
}

/**
 * A column of numbers that must line up, which is where `tabular-nums`
 * belongs and the only kind of place on this page it appears.
 */
function Th({ children, align = 'end' }: { children: React.ReactNode; align?: 'start' | 'end' }) {
  return (
    <th
      style={{
        textAlign: align,
        padding: '8px 10px',
        fontSize: 11.5,
        fontWeight: 600,
        color: 'var(--text-dim)',
        borderBottom: '1px solid var(--border)',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </th>
  )
}

function Td({ children, align = 'end' }: { children: React.ReactNode; align?: 'start' | 'end' }) {
  return (
    <td
      style={{
        textAlign: align,
        padding: '9px 10px',
        color: 'var(--text2)',
        borderBottom: '1px solid var(--border)',
        fontVariantNumeric: align === 'end' ? 'tabular-nums' : undefined,
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </td>
  )
}
