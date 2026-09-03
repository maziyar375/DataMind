/**
 * Master–detail furniture for the settings pages.
 *
 * Data sources and LLM providers are the same shape of screen: pick a record
 * on the left, edit it on the right. Sharing the frame here keeps the two
 * pages visually identical and leaves each page file holding only its own
 * fields.
 */
import React from 'react'
import { LIST_DRAWER_ID } from './list-drawer'
import { GlyphBadge, Icon, ListNewButton } from './ui'

// ── left column ───────────────────────────────────────────────────────────
/**
 * The index side of the screen: what exists, and the button that adds to it.
 *
 * Three rows of chrome above the list — identity, filter, action — because the
 * list is the page's navigation and a name is the only way back to a record.
 * The filter is offered only when a page passes `onQuery`; a search box over
 * four rows is furniture, which is the same rule the Dashboards toolbar
 * follows for its archived filter.
 */
export function MasterColumn({
  title, icon, note, count, showCount = true, onNew, newLabel, empty, query,
  onQuery, loading, open, children,
}: {
  title: string
  /** The section's own glyph — the same mark the sidebar uses for this page. */
  icon?: React.ReactNode
  /**
   * A quiet line under the title, for something true of the whole list.
   *
   * Both settings pages sit in the rail beside Users and read as workspace
   * configuration, and neither is: connections and model providers are scoped
   * to `owner_id`, so two colleagues see the same two labels and two
   * different lists. That is worth one sentence in the place the list is
   * actually read, and worth no more than one.
   */
  note?: string
  /** How many rows the list holds. Drives the skeleton and the empty text. */
  count: number
  /**
   * Whether to show that number beside the title.
   *
   * True where the count *is* the list's subject — Data sources has two data
   * sources. False where the list is a filter rather than the thing being
   * counted: the Knowledge console lists connections, so a pill reading `2`
   * under the word "Knowledge" contradicts the rail's badge reading `42`
   * three inches away. Two different numbers under one word is worse than no
   * number at all.
   */
  showCount?: boolean
  /**
   * The list's own new-verb, if it has one.
   *
   * Optional because not every column adds to itself. A page whose primary
   * action would navigate *out of the section* should not put it here — this
   * is the loudest control in the column, and pointing it somewhere else
   * makes the page's most prominent offer a way to leave.
   */
  onNew?: () => void
  newLabel?: string
  empty: string
  query?: string
  onQuery?: (next: string) => void
  /** Outline rows while the first read is in flight, so the column has shape. */
  loading?: boolean
  /**
   * Below 700px this column is an overlay rather than a column — see
   * `list-drawer.tsx`. Above it the flag does nothing at all, because the
   * stylesheet only gives `.is-open` a meaning inside that media query.
   */
  open?: boolean
  children: React.ReactNode
}) {
  return (
    <div
      id={LIST_DRAWER_ID}
      className={`rm-master${open ? ' is-open' : ''}`}
      style={{
        width: 274,
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
        borderRight: '1px solid var(--border)',
        background: 'var(--sidebar-bg)',
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
          padding: '16px 14px 12px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          {icon && <GlyphBadge size={28}>{icon}</GlyphBadge>}
          {/* The page's one <h1>. Data sources and LLM providers have no title
              block of their own — this column header is where the page names
              itself, so it is the heading in the document too, at the size the
              column was already drawn at rather than a heading's default. */}
          <h1
            style={{
              margin: 0,
              fontSize: 14.5,
              fontWeight: 700,
              letterSpacing: '-0.01em',
              color: 'var(--text-strong)',
            }}
          >
            {title}
          </h1>
          {showCount && (
            <span
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: 'var(--text-faint)',
                background: 'var(--panel-alt)',
                padding: '2px 7px',
                borderRadius: 20,
              }}
            >
              {loading ? '–' : count}
            </span>
          )}
        </div>

        {note && (
          <p
            style={{
              margin: '-3px 0 0',
              fontSize: 11.5,
              lineHeight: 1.45,
              color: 'var(--text-faint)',
            }}
          >
            {note}
          </p>
        )}

        {onNew && newLabel && <ListNewButton label={newLabel} onClick={onNew} />}

        {onQuery && count > 0 && (
          <div className="rm-search rm-master-search">
            <span aria-hidden className="rm-search-icon"><Icon.Search size={14} /></span>
            <input
              type="search"
              aria-label={`Filter ${title.toLowerCase()}`}
              value={query ?? ''}
              placeholder="Filter…"
              onChange={(event) => onQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') onQuery('')
              }}
            />
            {query && (
              <button
                type="button"
                aria-label="Clear filter"
                className="rm-search-clear rm-icon-btn"
                onClick={() => onQuery('')}
                style={{ ['--rm-hover-bg' as string]: 'var(--panel-alt)' }}
              >
                <Icon.Close size={12} />
              </button>
            )}
          </div>
        )}

      </div>

      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '0 10px 16px',
          display: 'flex',
          flexDirection: 'column',
          gap: 3,
        }}
      >
        {loading ? (
          <MasterSkeleton />
        ) : count === 0 ? (
          <p
            style={{
              fontSize: 12.5,
              color: 'var(--text-dim)',
              lineHeight: 1.5,
              padding: '4px 6px',
              margin: 0,
            }}
          >
            {empty}
          </p>
        ) : (
          children
        )}
      </div>
    </div>
  )
}

/** Rows in outline while the list loads — the column's own `rm-bone`. */
function MasterSkeleton() {
  return (
    <div aria-hidden style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {[0, 1, 2, 3].map((index) => (
        <div
          key={index}
          style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 10px' }}
        >
          <div className="rm-bone" style={{ width: 30, height: 30, borderRadius: 9 }} />
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 5 }}>
            <div className="rm-bone" style={{ width: `${70 - index * 8}%`, height: 9, borderRadius: 5 }} />
            <div className="rm-bone" style={{ width: '52%', height: 8, borderRadius: 5 }} />
          </div>
        </div>
      ))}
    </div>
  )
}

/**
 * One record in that column.
 *
 * The row carries three things and no more: what it is (the glyph), what it is
 * called (name over the address it points at), and whether it works (the tone
 * dot, which keeps a text label in `toneLabel` so colour is never the only
 * carrier). Hover and selection are in the stylesheet — `.rm-master-item` —
 * rather than in React state, so a list of forty rows costs forty listeners
 * fewer than it used to.
 */
export function MasterItem({
  title, subtitle, active, tone, toneLabel, glyph, onClick,
}: {
  title: string
  subtitle: string
  active: boolean
  /**
   * `amber` exists because a dot that can only say good / bad / unknown
   * cannot say *attention*. The knowledge console needs the difference: a
   * connection with flags raised on it is red, one with only unanswered
   * questions waiting is amber, and calling the second red says something is
   * broken when nothing is.
   */
  tone: 'green' | 'amber' | 'red' | 'neutral'
  /** What the dot means, in words — a tooltip, and the screen-reader text. */
  toneLabel?: string
  glyph?: React.ReactNode
  onClick: () => void
}) {
  const dotColor =
    tone === 'green' ? 'var(--green)'
      : tone === 'amber' ? 'var(--amber)'
        : tone === 'red' ? 'var(--red)'
          : 'var(--text-faint)'

  return (
    <button
      onClick={onClick}
      aria-current={active ? 'true' : undefined}
      className={`rm-master-item${active ? ' is-on' : ''}`}
    >
      {glyph}
      <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, gap: 1, flex: 1 }}>
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: active ? 'var(--text-strong)' : 'var(--text)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {title}
        </span>
        <span
          className="mono"
          style={{
            fontSize: 10.5,
            color: 'var(--text-faint)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {subtitle}
        </span>
      </span>
      <span
        aria-hidden
        title={toneLabel}
        style={{
          width: 7,
          height: 7,
          borderRadius: '50%',
          background: dotColor,
          flexShrink: 0,
        }}
      />
      {/* The dot's meaning in words, for anyone who cannot see the colour. */}
      {toneLabel && <span className="rm-sr">{toneLabel}</span>}
    </button>
  )
}

// ── detail pane ───────────────────────────────────────────────────────────
/**
 * The header of the pane that edits one record: what is open, what state it is
 * in, and what can be done to it.
 *
 * A whisper of panel tone lifts it off the body below without drawing another
 * hard box — the same treatment `.rm-dash-header` gives the dashboard toolbar,
 * which is the equivalent strip on the pages next door.
 */
export function DetailHeader({
  title, subtitle, chips, actions, glyph, leading,
}: {
  title: string
  subtitle: React.ReactNode
  chips?: React.ReactNode
  actions: React.ReactNode
  glyph?: React.ReactNode
  /**
   * Before the glyph: the control that opens the list column when it is an
   * overlay. It goes here rather than beside the actions because it is a way
   * *back*, and back is on the left of every screen anyone has ever used.
   */
  leading?: React.ReactNode
}) {
  return (
    <div
      className="rm-detail-header"
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        flexWrap: 'wrap',
        gap: 16,
        padding: '18px 28px 16px',
        borderBottom: '1px solid var(--border)',
        flexShrink: 0,
      }}
    >
      {leading}
      {glyph}
      {/* A basis, not just `minWidth: 0`: with the actions unshrinkable beside
          it, a pure `min-width: 0` column collapses to nothing on a narrow
          pane and the record's name — the one thing the header exists to
          state — disappears. Below the basis the actions wrap under instead. */}
      <div style={{ flex: '1 1 240px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 5 }}>
        <div
          style={{
            fontSize: 18,
            fontWeight: 700,
            letterSpacing: '-0.015em',
            color: 'var(--text-strong)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {title}
        </div>
        <div
          className="mono"
          style={{
            fontSize: 12,
            color: 'var(--text-dim)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {subtitle}
        </div>
        {chips && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 3 }}>
            {chips}
          </div>
        )}
      </div>
      <div
        style={{
          marginLeft: 'auto',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'flex-end',
          // Wrapping, not shrinking: two buttons and an "Unsaved changes"
          // note are ~330px, which is wider than a phone's content column.
          // Unshrinkable *and* unwrappable is how Save left the screen.
          flexWrap: 'wrap',
          gap: 8,
        }}
      >
        {actions}
      </div>
    </div>
  )
}

/**
 * Says *why* Save is live, next to the button rather than at the field that
 * changed. A disabled button explains itself only on hover; this makes the
 * difference between "nothing to do" and "you have edits pending" readable at
 * a glance, and it is also the only acknowledgement that a save happened —
 * the note disappearing is the receipt.
 *
 * Lives here rather than in either page because both settings screens are the
 * same shape and must read the same way.
 */
export function UnsavedNote() {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 12,
        color: 'var(--text-dim)',
        marginRight: 2,
        whiteSpace: 'nowrap',
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: 999,
          background: 'var(--amber)',
          flexShrink: 0,
        }}
      />
      Unsaved changes
    </span>
  )
}

export function Tabs({
  value, onChange, items,
}: {
  value: string
  onChange: (value: string) => void
  /**
   * `leaves` marks a tab that navigates out of its own strip.
   *
   * Data sources has exactly one: Knowledge, whose console is a destination
   * of its own (`/knowledge/:id`) rather than a second copy rendered here.
   * The tab stays because this is where people look for a connection's store
   * — but a tab that quietly takes you somewhere else is worse than one that
   * says it will, so it carries an arrow and says so in its tooltip.
   */
  items: { value: string; label: string; count?: number; leaves?: boolean }[]
}) {
  return (
    <div
      // Five tabs are ~430px, and a connection's Knowledge tab must be
      // reachable on a phone — silently clipping the last two is the failure
      // this class prevents.
      className="rm-tabs"
      style={{
        display: 'flex',
        gap: 2,
        padding: '0 28px',
        borderBottom: '1px solid var(--border)',
        flexShrink: 0,
      }}
    >
      {items.map((item) => {
        const active = item.value === value
        return (
          <button
            key={item.value}
            onClick={() => onChange(item.value)}
            aria-current={active ? 'true' : undefined}
            title={item.leaves ? `${item.label} — opens the curation console` : undefined}
            className="rm-tab"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 13,
              fontWeight: 600,
              padding: '11px 14px',
              background: 'transparent',
              border: 'none',
              borderBottom: `2px solid ${active ? 'var(--accent)' : 'transparent'}`,
              color: active ? 'var(--text-strong)' : 'var(--text-dim)',
              cursor: 'pointer',
              marginBottom: -1,
            }}
          >
            {item.label}
            {item.leaves && (
              <span aria-hidden style={{ display: 'flex', opacity: 0.55 }}>
                <Icon.ArrowRight size={12} />
              </span>
            )}
            {item.count != null && (
              <span
                style={{
                  fontSize: 10.5,
                  fontWeight: 600,
                  color: 'var(--text-faint)',
                  background: 'var(--panel-alt)',
                  padding: '1px 6px',
                  borderRadius: 20,
                }}
              >
                {item.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

/**
 * A titled card. Groups related fields so a long form reads as a few parts.
 *
 * The glyph is what makes the group findable on the way back: a reader
 * returning to "the credentials one" recognises the key before they read the
 * word, and a form of five identical white cards gives them nothing to aim at.
 */
export function Section({
  title, description, icon, danger, children,
}: {
  title: string
  description?: string
  icon?: React.ReactNode
  danger?: boolean
  children: React.ReactNode
}) {
  return (
    <section
      style={{
        border: `1px solid ${danger ? 'var(--red-border)' : 'var(--border)'}`,
        borderRadius: 12,
        background: 'var(--panel)',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 10,
          padding: '12px 16px',
          borderBottom: '1px solid var(--border)',
          background: danger ? 'var(--red-bg)' : 'var(--panel-alt)',
        }}
      >
        {icon && (
          <span
            aria-hidden
            style={{
              display: 'grid',
              placeItems: 'center',
              width: 26,
              height: 26,
              flexShrink: 0,
              borderRadius: 8,
              background: danger ? 'var(--red-bg)' : 'var(--panel)',
              border: `1px solid ${danger ? 'var(--red-border)' : 'var(--border)'}`,
              color: danger ? 'var(--red)' : 'var(--text-dim)',
            }}
          >
            {icon}
          </span>
        )}
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              fontSize: 12.5,
              fontWeight: 700,
              letterSpacing: '0.01em',
              color: danger ? 'var(--red)' : 'var(--text-strong)',
            }}
          >
            {title}
          </div>
          {description && (
            <div style={{ fontSize: 11.5, color: 'var(--text-dim)', marginTop: 3, lineHeight: 1.5 }}>
              {description}
            </div>
          )}
        </div>
      </div>
      <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
        {children}
      </div>
    </section>
  )
}

/** Lays fields out in equal columns, collapsing to one on a narrow pane. */
export function FieldRow({
  columns = 2, children,
}: {
  columns?: number
  children: React.ReactNode
}) {
  return (
    <div
      // Host / Port / Database is three columns of ~85px on a phone, which is
      // a port field showing three of five digits. The stylesheet collapses
      // this to one column below 640px, the same rule `.rm-col-2` follows.
      className="rm-fieldrow"
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
        gap: 12,
      }}
    >
      {children}
    </div>
  )
}

/**
 * The scrollable body of a detail tab — **the** one, for every tab.
 *
 * The three tabs of a connection used to disagree about this, and the tab
 * strip above them did not move while everything under it did: Settings was
 * capped at 720 and hung on the left, Schema was full-bleed, and the Semantic
 * layer was capped at 900 and *centred*. Switching tabs therefore moved both
 * edges of the content, which reads as a broken page rather than as three
 * views of one record.
 *
 * Each of those choices was defensible alone — a form wants a measure, a table
 * wants width — but they were made three times, independently, and that is the
 * whole of the bug. So there is one frame now, and its rules are:
 *
 * - **One left edge.** The content column hangs from the same rail as the
 *   record's name and the tab labels above it. Centring is what made the
 *   semantic tab look detached from its own tabs.
 * - **One measure.** 1000px: wide enough for a three-column field row and a
 *   schema table, narrow enough that a text input never runs the width of a
 *   27-inch display. At the widths this pane actually gets — a sidebar and a
 *   274px index are already spent — it is usually the full pane anyway.
 * - **One set of paddings**, so nothing shifts vertically on a tab switch
 *   either.
 */
export const DETAIL_WIDTH = 1000

export function DetailBody({
  children, padBottom,
}: {
  children: React.ReactNode
  /** Room for the semantic tab's floating save bar, so it covers no card. */
  padBottom?: boolean
}) {
  return (
    <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
      <div
        style={{
          maxWidth: DETAIL_WIDTH,
          padding: `24px 28px ${padBottom ? 96 : 32}px`,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}
      >
        {children}
      </div>
    </div>
  )
}

export function StatusLine({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 12.5,
        color: ok ? 'var(--green)' : 'var(--red)',
        background: ok ? 'var(--green-bg)' : 'var(--red-bg)',
        border: `1px solid ${ok ? 'transparent' : 'var(--red-border)'}`,
        borderRadius: 8,
        padding: '9px 12px',
      }}
    >
      {ok ? <Icon.Check size={14} stroke="var(--green)" /> : <Icon.Alert size={14} stroke="var(--red)" />}
      <span>{children}</span>
    </div>
  )
}
