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
  DashboardCard, DashboardGrid, DashboardSettings, STACK_BELOW_PX,
  type TileAction, useTileScheduler,
} from '../components/dashboard'
import { TileEditor } from '../components/tile-editor'
import {
  EmptyState, ErrorNote, GhostButton, Icon, Modal, PrimaryButton, Spinner, TextInput,
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
function DashboardIndex({ onOpen }: { onOpen: (id: string) => void }) {
  const [cards, setCards] = useState<DashboardSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [renaming, setRenaming] = useState<DashboardSummary | null>(null)
  const [busy, setBusy] = useState(false)

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

  return (
    <div className="rm-page-pad" style={{ flex: 1, overflowY: 'auto' }}>
      <header
        style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12, marginBottom: 22 }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <h1
            style={{
              margin: 0,
              fontSize: 20,
              fontWeight: 700,
              letterSpacing: '-0.01em',
              color: 'var(--text-strong)',
            }}
          >
            Dashboards
          </h1>
          <span style={{ fontSize: 13, color: 'var(--text-dim)' }}>
            Tiles you keep. Each one has its own connection and its own refresh rate.
          </span>
        </div>
        <PrimaryButton
          style={{ marginLeft: 'auto' }}
          onClick={() => setCreating(true)}
          disabled={busy}
        >
          <Icon.Plus /> New dashboard
        </PrimaryButton>
      </header>

      {error && <div style={{ marginBottom: 14 }}><ErrorNote>{error}</ErrorNote></div>}

      {cards === null ? (
        <div style={{ display: 'grid', placeItems: 'center', padding: 60 }}>
          <Spinner size={18} />
        </div>
      ) : cards.length === 0 ? (
        <EmptyState
          icon={<Icon.Grid size={20} />}
          title="No dashboards yet"
          body="A dashboard is a grid of saved queries. Create one, then add a tile by asking a question in plain language or writing the SQL yourself."
          action={<PrimaryButton onClick={() => setCreating(true)}>New dashboard</PrimaryButton>}
        />
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(270px, 1fr))',
            gap: 16,
          }}
        >
          {cards.map((card) => (
            <DashboardCard
              key={card.id}
              dashboard={card}
              onOpen={() => onOpen(card.id)}
              onRename={() => setRenaming(card)}
              onDuplicate={() => void guard(() => duplicate(card))}
              onArchive={() =>
                void guard(() =>
                  api.update(card.id, {
                    status: card.status === 'ARCHIVED' ? 'ACTIVE' : 'ARCHIVED',
                  }),
                )
              }
              onDelete={() => void guard(() => api.remove(card.id))}
            />
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

// ── one dashboard ─────────────────────────────────────────────────────────
function DashboardView({ id, onBack }: { id: string; onBack: () => void }) {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  // `undefined` = closed; `null` = adding; a tile = editing that one.
  const [editorTile, setEditorTile] = useState<DashboardTile | null | undefined>(undefined)

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
        if (action === 'refresh') return data.refreshNow([tile.id])
        if (action === 'edit') return setEditorTile(tile)
        // Both of these apply the change locally rather than re-reading, for
        // the same reason the editor does — see `onSaved` below.
        if (action === 'delete') {
          await api.removeTile(id, tile.id)
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

  if (!dashboard) {
    return (
      <div style={{ flex: 1, display: 'grid', placeItems: 'center' }}>
        <Spinner size={18} />
      </div>
    )
  }

  return (
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
              minWidth: 0,
              flex: 1,
              maxWidth: 420,
            }}
          >
            {/* Edit mode edits the dashboard, not only its tiles: the name and
                the description are part of the document, and renaming it from
                a card on another screen is a strange place to have to go. */}
            {editing ? (
              <>
                <InlineEdit
                  ariaLabel="Dashboard name"
                  value={dashboard.name}
                  required
                  style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-strong)' }}
                  onCommit={(name) => void patchDashboard({ name })}
                />
                <InlineEdit
                  ariaLabel="Dashboard description"
                  value={dashboard.description ?? ''}
                  placeholder="Add a description"
                  style={{ fontSize: 11.5, color: 'var(--text-dim)' }}
                  onCommit={(description) => void patchDashboard({ description: description || null })}
                />
              </>
            ) : (
              <>
                <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-strong)' }}>
                  {dashboard.name}
                </span>
                <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
                  {dashboard.description
                    ? dashboard.description
                    : `${tiles.length} ${tiles.length === 1 ? 'tile' : 'tiles'}`}
                  {data.pending.size > 0 ? ' · refreshing' : ''}
                </span>
              </>
            )}
          </div>

          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
            <GhostButton onClick={() => data.refreshNow(tiles.map((t) => t.id))}>
              <Icon.Refresh size={13} /> Refresh all
            </GhostButton>
            <GhostButton
              onClick={() => setShowSettings((open) => !open)}
              style={
                showSettings
                  ? { background: 'var(--panel-alt)', borderColor: 'var(--border-strong)' }
                  : undefined
              }
            >
              <Icon.Gear size={13} /> Settings
            </GhostButton>
            {/* One toggle, as §7 asks: reading or arranging, never both. The
                filled state is the mode indicator: while arranging, the way
                back to reading stays lit. */}
            <GhostButton
              onClick={() => setEditing((on) => !on)}
              style={
                editing
                  ? {
                      background: 'var(--accent-bg)',
                      borderColor: 'var(--accent-border)',
                      color: 'var(--accent)',
                    }
                  : undefined
              }
            >
              {editing ? (
                <>
                  <Icon.Check size={13} /> Done
                </>
              ) : (
                <>
                  <Icon.Pencil size={12} /> Edit
                </>
              )}
            </GhostButton>
            {editing && (
              <PrimaryButton onClick={() => setEditorTile(null)}>
                <Icon.Plus /> Add tile
              </PrimaryButton>
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
                <PrimaryButton
                  onClick={() => {
                    setEditing(true)
                    setEditorTile(null)
                  }}
                >
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
}
