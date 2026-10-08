/**
 * The pieces the semantic layer screens share: the panel and sub-card frames,
 * the pill tabs, the role metadata, the inline note, and the small helpers that
 * build blank entries and parse a form's lists.
 */
import { useState } from 'react'
import type { SemanticColumn, SemanticEntity, SemanticMetric } from '../api/types'
import { Icon, type ChipTone } from './ui'

/** What each table's kind is called, and the chip tone that carries it.
 *
 *  The kind used to be encoded only as a stripe down the left of a collapsed
 *  row — a colour with no legend, so the one thing a reader most wants while
 *  scanning ("which of these are facts?") was the one thing they had to open
 *  every row to find out. The chip is the legend now; the stripe stays as a
 *  scanning echo, thinned to 2px so a row reads as a card with an accent
 *  rather than a colour-tabbed template. */
export const ROLE_META: Record<string, { label: string; tone: ChipTone }> = {
  fact: { label: 'Fact', tone: 'accent' },
  dimension: { label: 'Dimension', tone: 'green' },
  bridge: { label: 'Bridge', tone: 'amber' },
  lookup: { label: 'Lookup', tone: 'neutral' },
  unknown: { label: 'Kind not set', tone: 'neutral' },
}

/** The same idea one level down: a column's role, visible without opening it. */
export const COLUMN_ROLE_TONE: Record<string, ChipTone> = {
  key: 'neutral',
  time: 'amber',
  dimension: 'green',
  measure: 'accent',
  attribute: 'neutral',
}

// ── panels ─────────────────────────────────────────────────────────────────
/** A titled card. Local rather than `settings.Section` so the semantic tab can
 *  carry an action in the header without changing every other settings page.
 *
 *  Optionally collapsible: the two document-level panels sit above a list of
 *  however many tables the database has, and a filled-in one should state what
 *  it holds in a line rather than spend a screen of form between the reader and
 *  the tables they came for. Pass `summary` to make it collapsible. */
export function Panel({
  title, description, summary, defaultOpen = true, action, children,
}: {
  title: string
  description?: string
  summary?: string
  defaultOpen?: boolean
  action?: React.ReactNode
  children: React.ReactNode
}) {
  const collapsible = summary !== undefined
  const [open, setOpen] = useState(defaultOpen)
  const shown = !collapsible || open

  // Open, the description explains what to write here; closed, the summary
  // reports what is already written. Never both.
  const titleBlock = (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontSize: 12.5,
          fontWeight: 700,
          color: 'var(--text-strong)',
        }}
      >
        {collapsible && <Icon.Chevron open={open} size={12} stroke="var(--text-dim)" />}
        {title}
      </div>
      {(shown ? description : summary) && (
        <div
          style={{
            fontSize: 11.5,
            color: 'var(--text-dim)',
            marginTop: 3,
            marginLeft: collapsible ? 20 : 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {shown ? description : summary}
        </div>
      )}
    </div>
  )

  return (
    <section
      style={{
        border: '1px solid var(--border)',
        borderRadius: 12,
        background: 'var(--panel)',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '13px 18px',
          borderBottom: shown ? '1px solid var(--border)' : 'none',
        }}
      >
        {collapsible ? (
          // The action stays outside the toggle: a button inside a button is
          // invalid, and "Add a term" must not also collapse the panel.
          <button
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              flex: 1,
              minWidth: 0,
              padding: 0,
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              textAlign: 'left',
            }}
          >
            {titleBlock}
          </button>
        ) : (
          titleBlock
        )}
        {shown && action}
      </div>
      {shown && (
        <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
          {children}
        </div>
      )}
    </section>
  )
}

export function Note({ tone, children }: { tone: 'amber' | 'red'; children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 9,
        alignItems: 'flex-start',
        fontSize: 12.5,
        lineHeight: 1.55,
        color: `var(--${tone})`,
        background: `var(--${tone}-bg)`,
        border: `1px solid var(--${tone}-border)`,
        borderRadius: 9,
        padding: '11px 13px',
      }}
    >
      <span style={{ marginTop: 1, flexShrink: 0 }}>
        <Icon.Alert />
      </span>
      <span>{children}</span>
    </div>
  )
}

// ── filters ────────────────────────────────────────────────────────────────
/** Sticks to the top of the scroll area: a 42-table schema scrolls past this
 *  in a second, and losing the search box is what makes a long list feel
 *  unmanageable. */
/**
 * The pill switcher, in the two places this file needs one.
 *
 * It was drawn inline in the filter bar and then wanted again inside an
 * expanded table, which is the moment a shape stops being a layout and starts
 * being a control. `Segmented` in `ui.tsx` is the app's other one and is
 * deliberately not this: that is a two- or three-way *view* toggle with no
 * counts, and stretching it to carry a number per option would have made both
 * callers worse.
 *
 * `alert` is what makes a count worth having — a red number is the difference
 * between a tab that says how much is in there and a tab that says something
 * in there needs you.
 */
export function PillTabs<T extends string>({
  value, onChange, options, ariaLabel,
}: {
  value: T
  onChange: (next: T) => void
  options: { value: T; label: string; count?: number; alert?: boolean }[]
  ariaLabel: string
}) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      style={{
        display: 'flex',
        gap: 2,
        background: 'var(--panel-alt)',
        borderRadius: 9,
        padding: 3,
        // Scrolls sideways when it is wider than its row — five filters do not
        // fit a phone, and a clipped tab is a tab nobody can reach.
        flexShrink: 1,
        minWidth: 0,
        maxWidth: '100%',
        overflowX: 'auto',
        scrollbarWidth: 'none',
      }}
    >
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(option.value)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              flexShrink: 0,
              whiteSpace: 'nowrap',
              fontSize: 12.5,
              fontWeight: 600,
              padding: '6px 11px',
              borderRadius: 6,
              cursor: 'pointer',
              border: 'none',
              color: active ? 'var(--text-strong)' : 'var(--text-dim)',
              background: active ? 'var(--panel)' : 'transparent',
              boxShadow: active ? 'inset 0 1px 0 0 var(--sheen), var(--elev-1)' : 'none',
            }}
          >
            {option.label}
            {option.count !== undefined && (
              <span
                style={{
                  fontSize: 10.5,
                  fontWeight: 700,
                  fontVariantNumeric: 'tabular-nums',
                  color: option.alert ? 'var(--red)' : 'var(--text-faint)',
                }}
              >
                {option.count}
              </span>
            )}
            {option.alert && option.count === undefined && (
              <span
                aria-label="needs attention"
                style={{
                  width: 5, height: 5, borderRadius: '50%', background: 'var(--red)',
                }}
              />
            )}
          </button>
        )
      })}
    </div>
  )
}

export function Line({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 12,
        color,
        marginTop: -4,
      }}
    >
      {children}
    </div>
  )
}

// ── small pieces ───────────────────────────────────────────────────────────
export function SubCard({
  invalid, compact, children,
}: {
  invalid?: boolean
  /** Collapsed to a single row: tighter, so a list of them reads as a list. */
  compact?: boolean
  children: React.ReactNode
}) {
  return (
    <div
      // `rm-subcard` is only the hover hook for the row's delete button; the
      // look stays here with everything else in this file.
      className={`rm-subcard${compact ? '' : ' is-open'}`}
      style={{
        border: `1px solid ${invalid ? 'var(--red-border)' : 'var(--border)'}`,
        borderRadius: 9,
        padding: compact ? '8px 12px' : 13,
        display: 'flex',
        flexDirection: 'column',
        gap: 11,
        background: 'var(--panel-alt)',
      }}
    >
      {children}
    </div>
  )
}

/** The head of a column or metric card, and the row it collapses to.
 *
 *  Every entry used to render its whole form at once, so opening a table with
 *  a dozen described columns produced a page of near-identical inputs with
 *  nothing to navigate by. Collapsed, each entry is one line — name, role,
 *  and whatever it means — and the form appears only for the one being
 *  edited. Same disclosure the table list above already uses, one level down. */
export function SubCardHead({
  title, mono, badge, summary, open, onToggle, onRemove, removeLabel, onHistory,
  historyLabel = 'History',
}: {
  title: string
  mono?: boolean
  badge?: React.ReactNode
  summary?: string
  open: boolean
  onToggle: () => void
  onRemove: () => void
  removeLabel: string
  onHistory?: () => void
  historyLabel?: string
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <button
        onClick={onToggle}
        aria-expanded={open}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          flex: 1,
          minWidth: 0,
          padding: 0,
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <Icon.Chevron open={open} size={11} stroke="var(--text-faint)" />
        <span
          className={mono ? 'mono' : undefined}
          style={{
            fontSize: 12.5,
            fontWeight: 600,
            color: 'var(--text-strong)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            flexShrink: 0,
          }}
        >
          {title}
        </span>
        {badge}
        {!open && summary && (
          <span
            style={{
              fontSize: 11.5,
              color: 'var(--text-dim)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              minWidth: 0,
            }}
          >
            {summary}
          </span>
        )}
      </button>
      {/* Eleven columns and seven metrics meant eighteen bordered delete
          buttons down one card, which made *remove* the most repeated thing on
          a screen whose job is describing. It waits for the row now — and
          stays for a row that is open, or focused from the keyboard. */}
      <span className="rm-row-actions" style={{ flexShrink: 0, display: 'flex', gap: 6 }}>
        {onHistory && (
          <IconButton label={historyLabel} onClick={onHistory} tone="neutral">
            <Icon.History size={13} />
          </IconButton>
        )}
        <IconButton label={removeLabel} onClick={onRemove}>
          <Icon.Trash />
        </IconButton>
      </span>
    </div>
  )
}

/** A quiet destructive action: neutral until hovered, then unmistakably red.
 *  Every use of it is behind a confirmation, which is where the real safety
 *  lives — a red panel parked in the page reads as a warning about the
 *  content, not about the button. */
export function IconButton({
  label, onClick, children, size = 28, tone = 'danger',
}: {
  label: string
  onClick: () => void
  children: React.ReactNode
  size?: number
  /** `neutral` for a control that goes somewhere rather than removes something. */
  tone?: 'danger' | 'neutral'
}) {
  const [hover, setHover] = useState(false)
  const hot = tone === 'danger'
    ? { border: 'var(--red-border)', background: 'var(--red-bg)', color: 'var(--red)' }
    : { border: 'var(--accent-border)', background: 'var(--accent-bg)', color: 'var(--accent)' }
  return (
    <button
      aria-label={label}
      title={label}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: size,
        height: size,
        borderRadius: size > 30 ? 8 : 7,
        border: `1px solid ${hover ? hot.border : 'var(--border-strong)'}`,
        background: hover ? hot.background : 'transparent',
        color: hover ? hot.color : 'var(--text-faint)',
        cursor: 'pointer',
        flexShrink: 0,
      }}
    >
      {children}
    </button>
  )
}

export function hasIssue(entity: SemanticEntity): boolean {
  return (
    !entity.valid ||
    entity.issue !== '' ||
    entity.metrics.some((m) => !m.valid) ||
    entity.columns.some((c) => !c.valid)
  )
}

export function splitList(value: string): string[] {
  return value.split(',').map((s) => s.trim()).filter(Boolean)
}

export function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '')
}

export function parseMeanings(value: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of value.split('\n')) {
    const [key, ...rest] = line.split('=')
    if (key?.trim() && rest.length > 0) out[key.trim()] = rest.join('=').trim()
  }
  return out
}

export function estimateMinutes(tables: number): string {
  const seconds = Math.ceil((tables / 4) * 8) + 20
  if (seconds < 90) return 'under a minute'
  return `about ${Math.ceil(seconds / 60)} minutes`
}

export function blankColumn(name: string): SemanticColumn {
  return {
    name,
    label: '',
    description: '',
    synonyms: [],
    role: 'attribute',
    unit: '',
    value_meanings: {},
    provenance: { source: 'human', edited: true, reviewed: false },
    valid: true,
    issue: '',
  }
}

export function blankMetric(): SemanticMetric {
  return {
    name: '',
    label: '',
    description: '',
    synonyms: [],
    expression: '',
    filters: [],
    required_joins: [],
    additive: 'additive',
    unit: '',
    format: '',
    provenance: { source: 'human', edited: true, reviewed: false },
    valid: true,
    issue: '',
  }
}
