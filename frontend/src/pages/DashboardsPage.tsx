/**
 * Dashboards: an index of cards, then one dashboard filling the page.
 *
 * Shaped like Superset and Power BI because that is what people expect from
 * the word "dashboard": a list you open something from, a grid you look at,
 * and a mode switch between reading and arranging. View mode locks the grid
 * and hides the tile chrome; edit mode unlocks dragging and shows "Add tile".
 *
 * The page owns the dashboard document and the layout writes; the grid and the
 * refresh clock live in `components/dashboard.tsx`.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { Layout } from 'react-grid-layout'

import { ApiError, dashboards as api } from '../api/client'
import { applyTheme, type ThemeName } from '../theme/tokens'
import type { Dashboard, DashboardSummary, DashboardTile } from '../api/types'
import {
  DashboardCard, DashboardGrid, DashboardRow, DashboardSettings, STACK_BELOW_PX,
  TileShell, TileSkeleton, rateLabel, type TileAction, useTileScheduler,
} from '../components/dashboard'
import { TileEditor } from '../components/tile-editor'
import {
  Dot, EmptyState, ErrorNote, GhostButton, Icon, Modal, PrimaryButton, Spinner, TextInput,
  relativeTime,
} from '../components/ui'

export default function DashboardsPage() {
  const [openId, setOpenId] = useState<string | null>(null)

  return openId ? (
    <DashboardView id={openId} onBack={() => setOpenId(null)} />
  ) : (
    <DashboardIndex onOpen={setOpenId} />
  )
}

// ── the index ─────────────────────────────────────────────────────────────
/**
 * How the list is ordered.
 *
 * The index used to render whatever order the API returned, which is fine for
 * three dashboards and useless for thirty. "Recently updated" leads because
 * the thing you want is nearly always the thing you touched last.
 */
type SortKey = 'updated' | 'refreshed' | 'name' | 'tiles'
type StatusFilter = 'ACTIVE' | 'ARCHIVED' | 'ALL'
type ViewMode = 'grid' | 'list'

const SORTS: { value: SortKey; label: string }[] = [
  { value: 'updated', label: 'Recently updated' },
  { value: 'refreshed', label: 'Recently run' },
  { value: 'name', label: 'Name (A–Z)' },
  { value: 'tiles', label: 'Most tiles' },
]

const VIEW_KEY = 'raymand.dashboards.view'

function sortCards(cards: DashboardSummary[], key: SortKey): DashboardSummary[] {
  const byTime = (value: string | null) => (value ? new Date(value).getTime() : 0)
  return [...cards].sort((a, b) => {
    if (key === 'name') return a.name.localeCompare(b.name)
    if (key === 'tiles') return b.tile_count - a.tile_count || a.name.localeCompare(b.name)
    if (key === 'refreshed') return byTime(b.last_refreshed_at) - byTime(a.last_refreshed_at)
    return byTime(b.updated_at) - byTime(a.updated_at)
  })
}

function DashboardIndex({ onOpen }: { onOpen: (id: string) => void }) {
  const [cards, setCards] = useState<DashboardSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [renaming, setRenaming] = useState<DashboardSummary | null>(null)
  const [busy, setBusy] = useState(false)

  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<SortKey>('updated')
  // Archiving existed as an action with nowhere for the result to go: an
  // archived dashboard stayed in the list at 72% opacity forever. Defaulting
  // to ACTIVE is what makes the verb mean anything.
  const [status, setStatus] = useState<StatusFilter>('ACTIVE')
  const [view, setView] = useState<ViewMode>(
    () => (localStorage.getItem(VIEW_KEY) as ViewMode) ?? 'grid',
  )

  useEffect(() => localStorage.setItem(VIEW_KEY, view), [view])

  const load = useCallback(async () => {
    try {
      setCards(await api.list())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load dashboards.')
      setCards([])
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const guard = useCallback(
    async (run: () => Promise<unknown>) => {
      setBusy(true)
      try {
        await run()
        await load()
      } catch (err) {
        setError(err instanceof Error ? err.message : 'That did not work.')
      } finally {
        setBusy(false)
      }
    },
    [load],
  )

  /** Composed from the routes that exist: create, then copy each tile over. */
  const duplicate = useCallback(
    async (card: DashboardSummary) => {
      const source = await api.get(card.id)
      const copy = await api.create({
        name: `${card.name} (copy)`.slice(0, 100),
        description: source.description,
        grid_columns: source.grid_columns,
        row_height_px: source.row_height_px,
        gap_px: source.gap_px,
        palette: source.palette,
        theme_override: source.theme_override,
        default_refresh_interval_seconds: source.default_refresh_interval_seconds,
      })
      for (const tile of source.tiles) {
        await api.addTile(copy.id, {
          title: tile.title,
          tile_type: tile.tile_type,
          connection_id: tile.connection_id,
          llm_config_id: tile.llm_config_id,
          question: tile.question,
          sql: tile.sql,
          sql_origin: tile.sql_origin,
          chart_config: tile.chart_config,
          max_rows: tile.max_rows,
          refresh_interval_seconds: tile.refresh_interval_seconds,
          grid_x: tile.grid_x,
          grid_y: tile.grid_y,
          grid_w: tile.grid_w,
          grid_h: tile.grid_h,
          position: tile.position,
        })
      }
    },
    [],
  )

  const visible = useMemo(() => {
    if (!cards) return []
    const needle = query.trim().toLowerCase()
    const matched = cards.filter((card) => {
      if (status !== 'ALL' && card.status !== status) return false
      if (!needle) return true
      return (
        card.name.toLowerCase().includes(needle)
        || (card.description ?? '').toLowerCase().includes(needle)
      )
    })
    return sortCards(matched, sort)
  }, [cards, query, sort, status])

  const archivedCount = useMemo(
    () => (cards ?? []).filter((card) => card.status === 'ARCHIVED').length,
    [cards],
  )

  const rowProps = useCallback(
    (card: DashboardSummary) => ({
      dashboard: card,
      onOpen: () => onOpen(card.id),
      onRename: () => setRenaming(card),
      onDuplicate: () => void guard(() => duplicate(card)),
      onArchive: () =>
        void guard(() =>
          api.update(card.id, {
            status: card.status === 'ARCHIVED' ? 'ACTIVE' : 'ARCHIVED',
          }),
        ),
      onDelete: () => void guard(() => api.remove(card.id)),
    }),
    [duplicate, guard, onOpen],
  )

  const filtering = query.trim().length > 0 || status !== 'ACTIVE'

  return (
    <div className="rm-dash-index rm-page-pad" style={{ flex: 1, overflowY: 'auto' }}>
      <header
        style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-start', gap: 12, marginBottom: 18 }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 0 }}>
          <h1
            style={{
              margin: 0,
              fontSize: 25,
              fontWeight: 700,
              letterSpacing: '-0.022em',
              color: 'var(--text-strong)',
            }}
          >
            Dashboards
          </h1>
          {cards === null || cards.length === 0 ? (
            <span style={{ fontSize: 13.5, color: 'var(--text-dim)' }}>
              Tiles you keep. Each one has its own connection and its own refresh rate.
            </span>
          ) : (
            <IndexSummary cards={cards} />
          )}
        </div>
        <PrimaryButton
          style={{ marginLeft: 'auto', padding: '10px 17px' }}
          onClick={() => setCreating(true)}
          disabled={busy}
        >
          <Icon.Plus /> New dashboard
        </PrimaryButton>
      </header>

      {cards !== null && cards.length > 0 && (
        <IndexAttention cards={cards} onShowAll={() => { setQuery(''); setStatus('ALL') }} />
      )}

      {error && <div style={{ marginBottom: 14 }}><ErrorNote>{error}</ErrorNote></div>}

      {cards !== null && cards.length > 0 && (
        <div className="rm-dash-toolbar">
          <SearchField
            value={query}
            onChange={setQuery}
            placeholder={`Search ${cards.length} dashboard${cards.length === 1 ? '' : 's'}…`}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginLeft: 'auto' }}>
            {/* Only offered once there is something archived to look at — a
                permanently empty filter is furniture, not a control. */}
            {archivedCount > 0 && (
              <Segmented
                ariaLabel="Filter by status"
                value={status}
                onChange={setStatus}
                options={[
                  { value: 'ACTIVE', label: 'Active' },
                  { value: 'ARCHIVED', label: `Archived (${archivedCount})` },
                  { value: 'ALL', label: 'All' },
                ]}
              />
            )}
            <select
              aria-label="Sort dashboards"
              value={sort}
              onChange={(event) => setSort(event.target.value as SortKey)}
              className="rm-toolbar-select"
            >
              {SORTS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <Segmented
              ariaLabel="Layout"
              value={view}
              onChange={setView}
              options={[
                { value: 'grid', label: <Icon.Grid size={14} />, title: 'Cards' },
                { value: 'list', label: <Icon.List size={14} />, title: 'List' },
              ]}
            />
          </div>
        </div>
      )}

      {cards === null ? (
        <IndexSkeleton />
      ) : cards.length === 0 ? (
        <EmptyState
          icon={<Icon.Grid size={20} />}
          title="No dashboards yet"
          body="A dashboard is a grid of saved queries. Create one, then add a tile by asking a question in plain language or writing the SQL yourself."
          action={<PrimaryButton onClick={() => setCreating(true)}>New dashboard</PrimaryButton>}
        />
      ) : visible.length === 0 ? (
        // A filter that hides everything must say so, and offer the way back.
        <EmptyState
          icon={<Icon.Search size={20} />}
          title="Nothing matches"
          body={
            query.trim()
              ? `No dashboard is called “${query.trim()}”, and none mentions it in a description.`
              : 'No dashboard has that status.'
          }
          action={
            filtering && (
              <GhostButton
                onClick={() => {
                  setQuery('')
                  setStatus('ACTIVE')
                }}
              >
                Clear filters
              </GhostButton>
            )
          }
        />
      ) : view === 'grid' ? (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(284px, 1fr))',
            gap: 16,
          }}
        >
          {visible.map((card) => (
            <DashboardCard key={card.id} {...rowProps(card)} />
          ))}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {visible.map((card) => (
            <DashboardRow key={card.id} {...rowProps(card)} />
          ))}
        </div>
      )}

      {creating && (
        <NameDialog
          title="New dashboard"
          confirm="Create"
          onClose={() => setCreating(false)}
          onSubmit={async (name, description) => {
            const created = await api.create({ name, description })
            setCreating(false)
            onOpen(created.id)
          }}
        />
      )}

      {renaming && (
        <NameDialog
          title="Rename dashboard"
          confirm="Save"
          initialName={renaming.name}
          initialDescription={renaming.description ?? ''}
          onClose={() => setRenaming(null)}
          onSubmit={async (name, description) => {
            await api.update(renaming.id, { name, description })
            setRenaming(null)
            await load()
          }}
        />
      )}
    </div>
  )
}

/** What the index knows about the whole collection, derived once. */
function summarise(cards: DashboardSummary[]) {
  const active = cards.filter((card) => card.status === 'ACTIVE')
  const lastRun = cards
    .map((card) => card.last_refreshed_at)
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1)
  return {
    active: active.length,
    archived: cards.length - active.length,
    lastRun,
    // Unfinished: created, never given a tile. Nothing will ever appear on it.
    empty: active.filter((card) => card.tile_count === 0).length,
    // Misconfigured: it has tiles and a refresh rate, and has still never run.
    stale: active.filter(
      (card) =>
        card.tile_count > 0
        && card.default_refresh_interval_seconds > 0
        && !card.last_refreshed_at,
    ).length,
  }
}

/**
 * One line of facts under the page title.
 *
 * This replaced a four-box counter strip reading Dashboards / Tiles /
 * Auto-refreshing / Last run. Three of those four were vanity: the total tile
 * count across every dashboard is a number nobody acts on, and "Auto-refreshing
 * 0" is a zero that prompts no decision — it took a quarter of the strip to say
 * nothing. The count of dashboards is worth keeping, so it stays, at the size a
 * subtitle deserves rather than as a hero number.
 */
function IndexSummary({ cards }: { cards: DashboardSummary[] }) {
  const stats = useMemo(() => summarise(cards), [cards])

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: 7,
        fontSize: 13.5,
        color: 'var(--text-dim)',
      }}
    >
      <strong style={{ color: 'var(--text-strong)', fontWeight: 600 }}>
        {stats.active}
      </strong>
      {stats.active === 1 ? 'dashboard' : 'dashboards'}
      {stats.archived > 0 && (
        <>
          <span aria-hidden style={{ opacity: 0.4 }}>·</span>
          {stats.archived} archived
        </>
      )}
      {/* Liveness, not a count: whether anything on this account has run
          recently is the one aggregate that tells you the system is working. */}
      {stats.lastRun && (
        <>
          <span aria-hidden style={{ opacity: 0.4 }}>·</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <Dot color="var(--green)" />
            last run {relativeTime(stats.lastRun)}
          </span>
        </>
      )}
    </span>
  )
}

/**
 * A note above the list, and only when there is something to say.
 *
 * The two states worth interrupting for are both invisible from a card's face:
 * a dashboard with no tiles will never show anything, and one with a refresh
 * rate that has never run is not doing what its owner thinks. A strip that is
 * absent when all is well is worth more than four counters that are always
 * there — it means its presence carries information.
 */
function IndexAttention({
  cards, onShowAll,
}: {
  cards: DashboardSummary[]
  onShowAll: () => void
}) {
  const stats = useMemo(() => summarise(cards), [cards])
  if (stats.empty === 0 && stats.stale === 0) return null

  const parts: string[] = []
  if (stats.empty > 0) {
    parts.push(`${stats.empty} ${stats.empty === 1 ? 'has' : 'have'} no tiles yet`)
  }
  if (stats.stale > 0) {
    parts.push(`${stats.stale} set to refresh ${stats.stale === 1 ? 'has' : 'have'} never run`)
  }

  return (
    <div className="rm-attention">
      <span aria-hidden style={{ display: 'flex', color: 'var(--amber)' }}>
        <Icon.Alert size={14} />
      </span>
      <span>{parts.join(' · ')}</span>
      <button type="button" onClick={onShowAll}>
        Show all
      </button>
    </div>
  )
}

/** Search with the glyph inside the field, and a clear button once typed in. */
function SearchField({
  value, onChange, placeholder,
}: {
  value: string
  onChange: (next: string) => void
  placeholder: string
}) {
  return (
    <div className="rm-search">
      <span aria-hidden className="rm-search-icon"><Icon.Search size={14} /></span>
      <input
        type="search"
        aria-label="Search dashboards"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onChange('')
        }}
      />
      {value && (
        <button
          type="button"
          aria-label="Clear search"
          className="rm-search-clear rm-icon-btn"
          onClick={() => onChange('')}
          style={{ ['--rm-hover-bg' as string]: 'var(--panel-alt)' }}
        >
          <Icon.Close size={12} />
        </button>
      )}
    </div>
  )
}

/**
 * One control, several mutually exclusive states.
 *
 * Three ghost buttons in a row look like three unrelated actions; a segmented
 * control looks like one choice, which is what a filter and a layout switch
 * both are.
 */
function Segmented<T extends string>({
  value, onChange, options, ariaLabel,
}: {
  value: T
  onChange: (next: T) => void
  options: { value: T; label: React.ReactNode; title?: string }[]
  ariaLabel: string
}) {
  return (
    <div className="rm-seg" role="group" aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          title={option.title}
          aria-pressed={value === option.value}
          className={value === option.value ? 'is-on' : undefined}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/** Cards in outline while the list loads, so the page does not jump. */
function IndexSkeleton() {
  return (
    <div
      aria-hidden
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(284px, 1fr))',
        gap: 16,
      }}
    >
      {[0, 1, 2, 3, 4, 5].map((index) => (
        <div
          key={index}
          style={{
            background: 'var(--panel)',
            border: '1px solid var(--border)',
            borderRadius: 14,
            overflow: 'hidden',
          }}
        >
          <div className="rm-bone" style={{ height: 86, borderRadius: 0 }} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 9, padding: '14px 15px 15px' }}>
            <div className="rm-bone" style={{ width: '58%', height: 12, borderRadius: 6 }} />
            <div className="rm-bone" style={{ width: '92%', height: 9, borderRadius: 6 }} />
            <div className="rm-bone" style={{ width: '44%', height: 9, borderRadius: 6, marginTop: 10 }} />
          </div>
        </div>
      ))}
    </div>
  )
}

function NameDialog({
  title, confirm, initialName = '', initialDescription = '', onClose, onSubmit,
}: {
  title: string
  confirm: string
  initialName?: string
  initialDescription?: string
  onClose: () => void
  onSubmit: (name: string, description: string) => Promise<void>
}) {
  const [name, setName] = useState(initialName)
  const [description, setDescription] = useState(initialDescription)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  async function submit() {
    if (!name.trim()) return setError('A dashboard needs a name.')
    setSaving(true)
    try {
      await onSubmit(name.trim(), description.trim())
    } catch (err) {
      // A duplicate name is a 409 with a sentence worth showing verbatim.
      setError(err instanceof ApiError ? err.message : 'That did not work.')
      setSaving(false)
    }
  }

  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton onClick={() => void submit()} disabled={saving}>
            {saving ? 'Saving…' : confirm}
          </PrimaryButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <TextInput
          autoFocus
          value={name}
          placeholder="Revenue overview"
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void submit()
          }}
        />
        <TextInput
          value={description}
          placeholder="Optional description"
          onChange={(event) => setDescription(event.target.value)}
        />
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Modal>
  )
}

/**
 * A heading you can type into.
 *
 * Committed on blur or Enter, never per keystroke: one PATCH per edit, not one
 * per letter. Escape puts back what was there — the usual contract for editing
 * in place, and the reason this is not just an `<input>` with an `onChange`.
 */
function InlineEdit({
  value, onCommit, ariaLabel, placeholder, required = false, style,
}: {
  value: string
  onCommit: (value: string) => void
  ariaLabel: string
  placeholder?: string
  /** A dashboard must have a name, so an empty commit reverts instead. */
  required?: boolean
  style?: React.CSSProperties
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])

  return (
    <input
      className="rm-inline-edit"
      aria-label={ariaLabel}
      value={draft}
      placeholder={placeholder}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => {
        const next = draft.trim()
        if (required && !next) return setDraft(value)
        if (next !== value) onCommit(next)
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur()
        if (event.key === 'Escape') {
          setDraft(value)
          event.currentTarget.blur()
        }
      }}
      style={style}
    />
  )
}

/** The edit-mode alignment guide: thin lines through the middle of each gap. */
function GridGuide({
  width, columns, rowHeight, gap,
}: {
  width: number
  columns: number
  rowHeight: number
  gap: number
}) {
  const cell = (width - gap * (columns + 1)) / columns
  if (!(cell > 0)) return null
  return (
    <div
      aria-hidden
      className="rm-grid-guide"
      style={{
        backgroundSize: `${cell + gap}px ${rowHeight + gap}px`,
        backgroundPosition: `${gap / 2}px ${gap / 2}px`,
      }}
    />
  )
}

/** The header's ghost buttons, one notch tighter than the default. */
const toolbarBtn: React.CSSProperties = { fontSize: 12.5, padding: '7px 12px' }

// ── one dashboard ─────────────────────────────────────────────────────────
function DashboardView({ id, onBack }: { id: string; onBack: () => void }) {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  // `undefined` = closed; `null` = adding; a tile = editing that one.
  const [editorTile, setEditorTile] = useState<DashboardTile | null | undefined>(undefined)
  /** The tile drawn full-screen, if any. */
  const [focusId, setFocusId] = useState<string | null>(null)
  /** Chrome-free, full-viewport, for a wall display or a meeting. */
  const [presenting, setPresenting] = useState(false)

  const gridRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  const tiles: DashboardTile[] = useMemo(() => dashboard?.tiles ?? [], [dashboard])
  const data = useTileScheduler(id, tiles)

  useEffect(() => {
    let cancelled = false
    api
      .get(id)
      .then((loaded) => {
        if (!cancelled) setDashboard(loaded)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load it.')
      })
    return () => {
      cancelled = true
    }
  }, [id])

  // A dashboard pinned to DARK or LIGHT forces the app theme while it is
  // open, and hands it back on the way out. Storing the preference and then
  // ignoring it would make the setting a decoration.
  const override = dashboard?.theme_override
  useEffect(() => {
    if (!override || override === 'INHERIT') return
    const previous = (document.documentElement.getAttribute('data-theme') as ThemeName) || 'dark'
    const wanted = override === 'DARK' ? 'dark' : 'light'
    if (previous === wanted) return
    applyTheme(wanted)
    return () => applyTheme(previous)
  }, [override])

  // react-grid-layout needs a pixel width. Measured rather than guessed so the
  // grid reflows when the settings drawer opens or the window changes.
  useLayoutEffect(() => {
    const element = gridRef.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(element)
    setWidth(element.clientWidth)
    return () => observer.disconnect()
  }, [dashboard, showSettings])

  // One PATCH per drag-end, debounced so a fast series of drags does not
  // become a series of writes.
  const pendingLayout = useRef<number | null>(null)
  const saveLayout = useCallback(
    (layout: Layout[]) => {
      setDashboard((current) =>
        current
          ? {
              ...current,
              tiles: current.tiles.map((tile) => {
                const box = layout.find((item) => item.i === tile.id)
                return box
                  ? { ...tile, grid_x: box.x, grid_y: box.y, grid_w: box.w, grid_h: box.h }
                  : tile
              }),
            }
          : current,
      )
      if (pendingLayout.current) window.clearTimeout(pendingLayout.current)
      pendingLayout.current = window.setTimeout(() => {
        // `position` is reading order — top-to-bottom, left-to-right — not the
        // order react-grid-layout happens to hand back, which follows whatever
        // was dragged last.
        const ordered = [...layout].sort((a, b) => a.y - b.y || a.x - b.x)
        void api
          .setLayout(
            id,
            ordered.map((item, index) => ({
              tile_id: item.i,
              grid_x: item.x,
              grid_y: item.y,
              grid_w: item.w,
              grid_h: item.h,
              position: index,
            })),
          )
          .catch((err) =>
            setError(err instanceof Error ? err.message : 'The layout could not be saved.'),
          )
      }, 400)
    },
    [id],
  )

  const patchDashboard = useCallback(
    async (patch: Record<string, unknown>) => {
      setDashboard((current) => (current ? { ...current, ...patch } as Dashboard : current))
      try {
        setDashboard(await api.update(id, patch))
      } catch (err) {
        setError(err instanceof Error ? err.message : 'That setting did not save.')
      }
    },
    [id],
  )

  const onTileAction = useCallback(
    async (action: TileAction, tile: DashboardTile) => {
      try {
        if (action === 'expand') return setFocusId(tile.id)
        if (action === 'refresh') return data.refreshNow([tile.id])
        // Editing is a form over the whole screen; leaving the tile expanded
        // behind it means the editor closes back onto a modal, not the grid.
        if (action === 'edit') {
          setFocusId(null)
          return setEditorTile(tile)
        }
        // Both of these apply the change locally rather than re-reading, for
        // the same reason the editor does — see `onSaved` below.
        if (action === 'delete') {
          await api.removeTile(id, tile.id)
          setFocusId((current) => (current === tile.id ? null : current))
          return setDashboard((current) =>
            current
              ? { ...current, tiles: current.tiles.filter((t) => t.id !== tile.id) }
              : current,
          )
        }
        if (action === 'duplicate') {
          const copy = await api.duplicateTile(id, tile.id)
          setDashboard((current) =>
            current ? { ...current, tiles: [...current.tiles, copy] } : current,
          )
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'That did not work.')
      }
    },
    [data, id],
  )

  const focusTile = focusId ? tiles.find((tile) => tile.id === focusId) ?? null : null

  // Escape unwinds one layer at a time — expanded tile, then presentation,
  // then back to the index — which is the order they were entered in. The
  // editor and the settings drawer close themselves.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      const typing = target
        && (target.isContentEditable
          || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
      if (typing) return

      if (event.key === 'Escape') {
        if (focusId) return setFocusId(null)
        if (presenting) return setPresenting(false)
        return
      }
      // Shortcuts, not chords: this is a reading surface, and the two things
      // people do on one repeatedly are refresh it and put it on a screen.
      if (event.key === 'r' || event.key === 'R') {
        event.preventDefault()
        data.refreshNow(focusId ? [focusId] : tiles.map((tile) => tile.id))
      }
      if (event.key === 'p' || event.key === 'P') setPresenting((current) => !current)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [data, focusId, presenting, tiles])

  if (error && !dashboard) {
    return (
      <div style={{ flex: 1, padding: 30 }}>
        <ErrorNote>{error}</ErrorNote>
        <div style={{ marginTop: 12 }}>
          <GhostButton onClick={onBack}>Back to dashboards</GhostButton>
        </div>
      </div>
    )
  }

  if (!dashboard) return <DashboardViewSkeleton onBack={onBack} />

  const content = (
    <div style={{ flex: 1, display: 'flex', minWidth: 0 }}>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <header
          className="rm-dash-header"
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: 10,
            borderBottom: '1px solid var(--border)',
            flexShrink: 0,
          }}
        >
          <button
            onClick={onBack}
            aria-label="Back to dashboards"
            className="rm-icon-btn"
            style={{
              display: 'flex',
              width: 30,
              height: 30,
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: 8,
              border: 'none',
              background: 'transparent',
              color: 'var(--text-dim)',
              cursor: 'pointer',
              ['--rm-hover-bg' as string]: 'var(--panel-alt)',
            }}
          >
            <Icon.ArrowLeft size={15} />
          </button>
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 1,
              minWidth: 0,
              flex: 1,
              maxWidth: 480,
            }}
          >
            {/* The name and description are edited where they are read, in
                any mode: invisible fields until approached (see
                .rm-inline-edit), committed on blur or Enter, reverted on
                Escape. A drawer or dialog for renaming would be a longer road
                to the same PATCH. The empty description's placeholder carries
                the tile count, so the line is never blank. */}
            <InlineEdit
              ariaLabel="Dashboard name"
              value={dashboard.name}
              required
              style={{
                fontSize: 16.5,
                fontWeight: 700,
                letterSpacing: '-0.01em',
                color: 'var(--text-strong)',
              }}
              onCommit={(name) => void patchDashboard({ name })}
            />
            <InlineEdit
              ariaLabel="Dashboard description"
              value={dashboard.description ?? ''}
              placeholder={`${tiles.length} ${tiles.length === 1 ? 'tile' : 'tiles'} — add a description`}
              style={{ fontSize: 12, color: 'var(--text-dim)' }}
              onCommit={(description) => void patchDashboard({ description: description || null })}
            />
          </div>

          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
            {/* While arranging, the toolbar clears down to the one way out —
                a mode should look like a mode, and Refresh/Settings mid-drag
                are distractions. A tile's edit/duplicate/delete live on the
                tile's own kebab in either mode. */}
            {editing ? (
              <>
                <span className="rm-dash-edithint">
                  Drag a header to move · pull a corner to resize
                </span>
                <GhostButton
                  onClick={() => setEditing(false)}
                  style={{
                    ...toolbarBtn,
                    background: 'var(--accent-bg)',
                    borderColor: 'var(--accent-border)',
                    color: 'var(--accent)',
                  }}
                >
                  <Icon.Check size={13} /> Done
                </GhostButton>
              </>
            ) : (
              <>
                <LiveStatus
                  tiles={tiles}
                  refreshing={data.pending.size > 0}
                  onRefresh={() => data.refreshNow(tiles.map((t) => t.id))}
                />
                {/* One control per verb was four ghost buttons of identical
                    weight — a row of equals with no shape. Presentation and
                    arrangement are both *modes*, so they read as one grouped
                    switch; adding a tile is the page's one primary action. */}
                <div className="rm-toolgroup">
                  <button
                    type="button"
                    title="Presentation mode (P)"
                    aria-pressed={presenting}
                    className={presenting ? 'is-on' : undefined}
                    onClick={() => setPresenting((open) => !open)}
                  >
                    <Icon.Play size={12} /> Present
                  </button>
                  <button
                    type="button"
                    aria-pressed={editing}
                    onClick={() => setEditing(true)}
                  >
                    <Icon.Grid size={13} /> Edit grid
                  </button>
                  <button
                    type="button"
                    aria-pressed={showSettings}
                    className={showSettings ? 'is-on' : undefined}
                    onClick={() => setShowSettings((open) => !open)}
                  >
                    <Icon.Gear size={13} /> Settings
                  </button>
                </div>
                <PrimaryButton style={{ padding: '8px 14px' }} onClick={() => setEditorTile(null)}>
                  <Icon.Plus /> Add tile
                </PrimaryButton>
              </>
            )}
          </div>
        </header>

        {(error || data.error) && (
          <div style={{ padding: '10px 20px 0' }}>
            <ErrorNote>{error ?? data.error}</ErrorNote>
          </div>
        )}

        <div ref={gridRef} className="rm-dash-canvas" style={{ flex: 1, overflowY: 'auto' }}>
          {tiles.length === 0 ? (
            <EmptyState
              icon={<Icon.Grid size={20} />}
              title="This dashboard is empty"
              body="A tile is a saved query on its own clock. Add one by asking a question in plain language, or by writing the SQL yourself."
              action={
                <PrimaryButton onClick={() => setEditorTile(null)}>
                  <Icon.Plus /> Add tile
                </PrimaryButton>
              }
            />
          ) : (
            width > 0 && (
              <div style={{ position: 'relative' }}>
                {/* While arranging, a faint guide at the dashboard's own cell
                    geometry — react-grid-layout pads the container by one gap
                    and puts one gap between cells, so a line every
                    (cell + gap) starting half a gap in sits in the middle of
                    every gap. Behind the tiles, and gone in view mode. */}
                {/* No guide in the stacked layout — there is no grid to align to. */}
                {editing && width >= STACK_BELOW_PX && (
                  <GridGuide
                    width={width}
                    columns={dashboard.grid_columns}
                    rowHeight={dashboard.row_height_px}
                    gap={dashboard.gap_px}
                  />
                )}
                <DashboardGrid
                  dashboard={dashboard}
                  tiles={tiles}
                  data={data}
                  editing={editing}
                  width={width}
                  onLayout={saveLayout}
                  onTileAction={(action, tile) => void onTileAction(action, tile)}
                />
              </div>
            )
          )}
        </div>
      </div>

      {showSettings && (
        <DashboardSettings
          dashboard={dashboard}
          onChange={(patch) => void patchDashboard(patch)}
          onClose={() => setShowSettings(false)}
        />
      )}

      {editorTile !== undefined && (
        <TileEditor
          dashboard={dashboard}
          tile={editorTile}
          onClose={() => setEditorTile(undefined)}
          onSaved={(saved) => {
            setEditorTile(undefined)
            // The save response is the tile as stored — clamped row cap,
            // resolved rate, connection and model names and all (§6) — so it
            // is spliced in rather than re-read. A GET fired immediately after
            // a write can also beat the write's commit: `get_db` commits in
            // dependency teardown, which is not ordered before the response
            // reaches the client, and a re-read that loses the tile the user
            // just saved would look exactly like a save that failed.
            setDashboard((current) =>
              current
                ? {
                    ...current,
                    tiles: current.tiles.some((t) => t.id === saved.id)
                      ? current.tiles.map((t) => (t.id === saved.id ? saved : t))
                      : [...current.tiles, saved],
                  }
                : current,
            )
            // Its fingerprint changed, so whatever is cached answers the old
            // query. Waiting for the interval to elapse would show it anyway.
            data.refreshNow([saved.id])
          }}
        />
      )}
    </div>
  )

  return (
    <>
      {/* Presentation mode is a fixed layer over the whole viewport rather
          than a class on the shell: it has to cover the sidebar too, and
          lifting it out means neither App nor the sidebar needs to know this
          mode exists. */}
      {presenting ? (
        <div className="rm-present">
          <div className="rm-present-bar">
            <span className="rm-present-title">{dashboard.name}</span>
            <LiveStatus
              tiles={tiles}
              refreshing={data.pending.size > 0}
              onRefresh={() => data.refreshNow(tiles.map((t) => t.id))}
            />
            <GhostButton style={toolbarBtn} onClick={() => setPresenting(false)}>
              <Icon.Close size={13} /> Exit
            </GhostButton>
          </div>
          {content}
        </div>
      ) : (
        content
      )}

      {/* One tile, full screen. A tile sized to fit a grid cell cannot show a
          long table or a dense chart, and resizing the grid to read something
          then resizing it back is not a reading gesture — it is a layout edit
          with a layout write behind it. */}
      {focusTile && (
        <div
          className="rm-focus"
          role="dialog"
          aria-modal="true"
          aria-label={focusTile.title || 'Expanded tile'}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setFocusId(null)
          }}
        >
          <div className="rm-focus-frame">
            <div className="rm-focus-bar">
              <span className="rm-focus-hint">
                <span className="rm-kbd">Esc</span> to close
                <span aria-hidden style={{ opacity: 0.4 }}>·</span>
                <span className="rm-kbd">R</span> to refresh
              </span>
              <GhostButton style={toolbarBtn} onClick={() => setFocusId(null)}>
                <Icon.Collapse size={13} /> Close
              </GhostButton>
            </div>
            <div style={{ flex: 1, minHeight: 0 }}>
              <TileShell
                tile={focusTile}
                result={data.results[focusTile.id]}
                loading={data.pending.has(focusTile.id)}
                editing={false}
                draggable={false}
                focused
                onAction={(action) => void onTileAction(action, focusTile)}
              />
            </div>
          </div>
        </div>
      )}
    </>
  )
}

/**
 * Whether the numbers on screen are current, and the way to make them so.
 *
 * "Refresh all" alone said nothing about *whether it needed pressing*. The
 * dashboard's real state is the oldest tile on it — a wall of 30-second tiles
 * next to one hourly one is only as fresh as the hour — so that is what this
 * reports, next to the button that fixes it.
 */
function LiveStatus({
  tiles, refreshing, onRefresh,
}: {
  tiles: DashboardTile[]
  refreshing: boolean
  onRefresh: () => void
}) {
  const live = tiles.filter((tile) => tile.effective_refresh_interval_seconds > 0)
  const slowest = live.reduce(
    (worst, tile) => Math.max(worst, tile.effective_refresh_interval_seconds),
    0,
  )

  return (
    <div className="rm-live">
      <span className="rm-live-label">
        {live.length > 0 ? (
          <>
            <span className="rm-live-dot" aria-hidden />
            Live · every {rateLabel(slowest)}
          </>
        ) : (
          <>
            <Dot color="var(--text-faint)" />
            Manual
          </>
        )}
      </span>
      <button
        type="button"
        title="Refresh every tile (R)"
        aria-label="Refresh every tile"
        onClick={onRefresh}
        disabled={tiles.length === 0}
        className="rm-live-refresh rm-icon-btn"
        style={{ ['--rm-hover-bg' as string]: 'var(--panel-alt)' }}
      >
        {refreshing ? <Spinner size={13} /> : <Icon.Refresh size={13} />}
      </button>
    </div>
  )
}

/** The open dashboard, in outline, while its document is on the wire. */
function DashboardViewSkeleton({ onBack }: { onBack: () => void }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      <header
        className="rm-dash-header"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        <button
          onClick={onBack}
          aria-label="Back to dashboards"
          className="rm-icon-btn"
          style={{
            display: 'flex',
            width: 30,
            height: 30,
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 8,
            border: 'none',
            background: 'transparent',
            color: 'var(--text-dim)',
            cursor: 'pointer',
            ['--rm-hover-bg' as string]: 'var(--panel-alt)',
          }}
        >
          <Icon.ArrowLeft size={15} />
        </button>
        <div className="rm-bone" style={{ width: 190, height: 13, borderRadius: 6 }} />
      </header>
      <div className="rm-dash-canvas" style={{ flex: 1, overflow: 'hidden' }}>
        <div
          aria-hidden
          style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}
        >
          <TileSkeleton height={218} />
          <TileSkeleton height={218} />
          <TileSkeleton height={218} />
          <TileSkeleton height={218} />
        </div>
      </div>
    </div>
  )
}
