/**
 * The dashboard grid: tiles, their chrome, and the clock that refreshes them.
 *
 * Two things here are load-bearing and easy to get wrong:
 *
 * **One scheduler, not one timer per tile.** Every tile has its own rate, and
 * the naive reading of that is a `setInterval` per tile — twelve timers
 * producing twelve interleaved requests, each opening its own connector. This
 * file runs a single 1-second tick per open dashboard, works out which tiles
 * are *due*, and fires one `POST /data {tile_ids}` for all of them. Tiles due
 * in the same second coalesce for free; a 30s tile and an hourly one never
 * wait on each other.
 *
 * **A hidden tab does not poll.** The tick pauses on `document.hidden` and, on
 * return, refreshes whatever went overdue **once** — not once per missed
 * interval. A forgotten background tab that polls forever is how this feature
 * becomes the reason someone's production database is slow.
 *
 * Layout is `react-grid-layout` — a layout engine, not a component library, so
 * the design system is untouched; its two cosmetic defaults are re-stated in
 * `styles.css`.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import GridLayout, { type Layout } from 'react-grid-layout'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'

import { dashboards as api } from '../api/client'
import { dueTileIds } from './dashboard-schedule'
import type { Dashboard, DashboardSummary, DashboardTile, TileResult } from '../api/types'
import { Chip, Dot, ErrorNote, Icon, Kpi, ResultTable, Spinner, relativeTime } from './ui'
import { VegaChart } from './VegaChart'

// ── refresh rates ─────────────────────────────────────────────────────────
/** The rates a tile may run at. `0` is manual; `null` inherits the dashboard. */
export const REFRESH_OPTIONS: { value: number; label: string }[] = [
  { value: 0, label: 'Manual' },
  { value: 15, label: '15s' },
  { value: 30, label: '30s' },
  { value: 60, label: '1m' },
  { value: 300, label: '5m' },
  { value: 900, label: '15m' },
  { value: 3600, label: '1h' },
  { value: 21600, label: '6h' },
  { value: 86400, label: '24h' },
]

export function rateLabel(seconds: number): string {
  return REFRESH_OPTIONS.find((o) => o.value === seconds)?.label
    ?? (seconds >= 3600 ? `${Math.round(seconds / 3600)}h` : `${seconds}s`)
}

/** "as of 14:32" — with every tile on its own clock, this is not decoration. */
function clockTime(iso: string): string {
  const at = new Date(iso)
  return Number.isNaN(at.getTime())
    ? ''
    : at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

// ── the scheduler ─────────────────────────────────────────────────────────
export interface TileData {
  results: Record<string, TileResult>
  pending: Set<string>
  /** Ask for these tiles now, ignoring their cache. */
  refreshNow: (tileIds: string[]) => void
  error: string | null
}

export function useTileScheduler(
  dashboardId: string,
  tiles: DashboardTile[],
  { paused = false }: { paused?: boolean } = {},
): TileData {
  const [results, setResults] = useState<Record<string, TileResult>>({})
  const [pending, setPending] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)

  // The tick reads these through refs: it is created once per dashboard, and
  // a stale closure over `tiles` would keep asking for tiles that were
  // deleted ten minutes ago.
  const tilesRef = useRef(tiles)
  const resultsRef = useRef(results)
  const inFlight = useRef<Set<string>>(new Set())
  tilesRef.current = tiles
  resultsRef.current = results

  const fetchTiles = useCallback(
    async (tileIds: string[], force: boolean) => {
      const wanted = tileIds.filter((id) => !inFlight.current.has(id))
      if (wanted.length === 0) return
      wanted.forEach((id) => inFlight.current.add(id))
      setPending(new Set(inFlight.current))
      try {
        const data = await api.data(dashboardId, wanted, force)
        setResults((current) => ({ ...current, ...data.results }))
        setError(null)
      } catch (err) {
        // The dashboard keeps whatever it last showed; a failed *poll* is not
        // a failed dashboard, and blanking the tiles would be a worse lie
        // than a stale number with its own timestamp on it.
        setError(err instanceof Error ? err.message : 'Refresh failed.')
      } finally {
        wanted.forEach((id) => inFlight.current.delete(id))
        setPending(new Set(inFlight.current))
      }
    },
    [dashboardId],
  )

  /** The rule itself lives in `dashboard-schedule.ts`, DOM-free and testable. */
  const dueNow = useCallback(
    (): string[] => dueTileIds(tilesRef.current, resultsRef.current),
    [],
  )

  // First paint: ask for everything once, cache and all. The backend serves
  // what is still fresh, so opening a dashboard costs a query only for tiles
  // that were actually due.
  useEffect(() => {
    setResults({})
    const initial = tiles.filter((t) => t.tile_type !== 'TEXT').map((t) => t.id)
    if (initial.length > 0) void fetchTiles(initial, false)
    // Deliberately keyed on the dashboard, not on `tiles`: adding a tile must
    // not re-fetch the other eleven.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dashboardId, fetchTiles])

  // A tile added or edited after first paint has no result yet; give it one
  // without disturbing anything else.
  useEffect(() => {
    const missing = tiles
      .filter((t) => t.tile_type !== 'TEXT' && !resultsRef.current[t.id])
      .map((t) => t.id)
    if (missing.length > 0) void fetchTiles(missing, false)
  }, [tiles, fetchTiles])

  // The one tick.
  useEffect(() => {
    if (paused) return
    const id = window.setInterval(() => {
      if (document.hidden) return
      const due = dueNow()
      if (due.length > 0) void fetchTiles(due, false)
    }, 1000)
    return () => window.clearInterval(id)
  }, [paused, dueNow, fetchTiles])

  // Coming back to a tab that was hidden for an hour: catch up exactly once.
  useEffect(() => {
    function onVisibility() {
      if (document.hidden || paused) return
      const due = dueNow()
      if (due.length > 0) void fetchTiles(due, false)
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [paused, dueNow, fetchTiles])

  const refreshNow = useCallback(
    (tileIds: string[]) => void fetchTiles(tileIds, true),
    [fetchTiles],
  )

  return { results, pending, refreshNow, error }
}

// ── the grid ──────────────────────────────────────────────────────────────
/** Below this measured grid width, side-by-side tiles stop making sense. */
export const STACK_BELOW_PX = 620

/**
 * Every edge and every corner.
 *
 * The corners resize two axes at once; the edges resize one and leave the
 * opposite side pinned, which is the move you want nine times out of ten
 * ("this is too tall" / "this needs one more column"). `react-resizable` ships
 * the positioning and cursor for all eight — only the default set is narrow.
 */
// Corners are listed last so they render last and therefore sit above the edge
// strips — otherwise a corner would be unreachable, covered by both edges that
// meet there. (The union is spelled out because react-grid-layout's types keep
// `ResizeHandle` module-local.)
const RESIZE_HANDLES: ('n' | 'e' | 's' | 'w' | 'ne' | 'nw' | 'se' | 'sw')[] =
  ['n', 'e', 's', 'w', 'ne', 'nw', 'se', 'sw']

export function DashboardGrid({
  dashboard, tiles, data, editing, width, onLayout, onTileAction,
}: {
  dashboard: Dashboard
  tiles: DashboardTile[]
  data: TileData
  editing: boolean
  width: number
  onLayout: (layout: Layout[]) => void
  onTileAction: (action: TileAction, tile: DashboardTile) => void
}) {
  // On a narrow surface (phone, tablet portrait, a squeezed window) the grid
  // gives way to a single column in reading order — the same top-to-bottom,
  // left-to-right order `position` persists — with each tile keeping the
  // height its author gave it. Nothing is written back: the stored layout is
  // the desktop's, and widening the window restores it untouched. Dragging
  // and resizing are off here (there is no grid to drag on); the kebab keeps
  // edit, duplicate, and delete.
  if (width < STACK_BELOW_PX) {
    const ordered = [...tiles].sort((a, b) => a.grid_y - b.grid_y || a.grid_x - b.grid_x)
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: dashboard.gap_px }}>
        {ordered.map((tile) => (
          <div
            key={tile.id}
            style={
              tile.tile_type === 'TEXT'
                ? undefined
                : {
                    height:
                      tile.grid_h * dashboard.row_height_px
                      + (tile.grid_h - 1) * dashboard.gap_px,
                    minHeight: 120,
                  }
            }
          >
            <TileShell
              tile={tile}
              result={data.results[tile.id]}
              loading={data.pending.has(tile.id)}
              editing={editing}
              draggable={false}
              onAction={(action) => onTileAction(action, tile)}
            />
          </div>
        ))}
      </div>
    )
  }

  return (
    <SizedGrid
      dashboard={dashboard}
      tiles={tiles}
      data={data}
      editing={editing}
      width={width}
      onLayout={onLayout}
      onTileAction={onTileAction}
    />
  )
}

function SizedGrid({
  dashboard, tiles, data, editing, width, onLayout, onTileAction,
}: {
  dashboard: Dashboard
  tiles: DashboardTile[]
  data: TileData
  editing: boolean
  width: number
  onLayout: (layout: Layout[]) => void
  onTileAction: (action: TileAction, tile: DashboardTile) => void
}) {
  const layout = useMemo<Layout[]>(
    () =>
      tiles.map((tile) => ({
        i: tile.id,
        x: tile.grid_x,
        y: tile.grid_y,
        w: Math.max(1, tile.grid_w),
        h: Math.max(1, tile.grid_h),
        minW: 2,
        minH: 2,
      })),
    [tiles],
  )

  return (
    <div className={editing ? undefined : 'rm-grid-locked'}>
      <GridLayout
        className="layout"
        layout={layout}
        cols={dashboard.grid_columns}
        rowHeight={dashboard.row_height_px}
        margin={[dashboard.gap_px, dashboard.gap_px]}
        width={width}
        isDraggable={editing}
        isResizable={editing}
        // All eight, not just the south-east corner react-grid-layout gives
        // you by default. With one corner, "make this shorter" means dragging
        // the bottom-right up and then dragging the whole tile back to where
        // it was — two moves and a layout write for one intent. Grabbing the
        // edge that is actually in the wrong place is how every other grid
        // editor works.
        resizeHandles={RESIZE_HANDLES}
        // The header is the handle: dragging from anywhere would make
        // selecting a cell in a table impossible.
        draggableHandle=".rm-tile-drag"
        compactType={dashboard.compact_mode === 'NONE' ? null : 'vertical'}
        // "Leave tiles where they are put" turned off compaction but left
        // collision *pushing* on, which is the setting's own worst enemy:
        // nudging a tile one cell shoved whatever it touched, and with no
        // compaction to settle the displaced tiles they stayed shoved and
        // shoved their own neighbours in turn. One small drag rearranged the
        // whole dashboard. Refusing the move instead is what the setting
        // promises — the tile simply does not go where something already is,
        // and nothing else on the board moves.
        //
        // Under "pull tiles upward" the push is wanted: displaced tiles fall
        // back up into place, which is the whole point of that mode.
        preventCollision={dashboard.compact_mode === 'NONE'}
        onDragStop={onLayout}
        onResizeStop={onLayout}
      >
        {tiles.map((tile) => (
          <div key={tile.id}>
            <TileShell
              tile={tile}
              result={data.results[tile.id]}
              loading={data.pending.has(tile.id)}
              editing={editing}
              onAction={(action) => onTileAction(action, tile)}
            />
          </div>
        ))}
      </GridLayout>
    </div>
  )
}

// ── the tile ──────────────────────────────────────────────────────────────
/** `expand` opens the tile full-screen; the dashboard owns that overlay. */
export type TileAction = 'expand' | 'refresh' | 'edit' | 'duplicate' | 'delete'

export function TileShell({
  tile, result, loading, editing, draggable = editing, focused = false, onAction,
}: {
  tile: DashboardTile
  result?: TileResult
  loading: boolean
  editing: boolean
  /** Whether the header is a drag handle. Off in the stacked (narrow) layout,
      where there is no grid to drag on even in edit mode. */
  draggable?: boolean
  /** Drawn inside the focus overlay: no expand button, no drag, no lift. */
  focused?: boolean
  onAction: (action: TileAction) => void
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const rate = tile.effective_refresh_interval_seconds
  const failed = result?.status === 'ERROR' || Boolean(result?.error)

  return (
    <div
      className={`rm-tile${failed ? ' rm-tile-failed' : ''}${focused ? ' rm-tile-focused' : ''}`}
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--panel)',
        borderRadius: 12,
        overflow: 'hidden',
      }}
    >
      {/* The header floats over the body rather than sitting behind a rule:
          the title labels the content the way an axis label does, and the
          divider was chrome the data paid for. */}
      <div
        className={draggable ? 'rm-tile-drag' : undefined}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '10px 12px 6px',
          flexShrink: 0,
        }}
      >
        {/* In edit mode the header is the drag handle; the grip says so
            before the cursor does. */}
        {draggable && (
          <span aria-hidden style={{ display: 'flex', color: 'var(--text-faint)', flexShrink: 0 }}>
            <Icon.Grip size={13} />
          </span>
        )}
        <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span
            style={{
              fontSize: 13.5,
              fontWeight: 600,
              letterSpacing: '-0.005em',
              color: 'var(--text-strong)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {tile.title || 'Untitled tile'}
          </span>
          {/* The freshness stamp is not decoration: it is the only way a
              reader tells a 30-second tile from the hourly one beside it.
              While a refresh is in flight the stamp says so *in place* rather
              than the body blanking — the number on screen is still the last
              true one, and it keeps its timestamp until the new one lands. */}
          {result && (
            <span
              style={{
                fontSize: 11,
                color: 'var(--text-faint)',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 5,
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {loading ? (
                <>
                  <Spinner size={9} />
                  refreshing
                </>
              ) : (
                <>as of {clockTime(result.computed_at)}</>
              )}
            </span>
          )}
        </div>

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
          {/* Source and freshness, both always on. Which database a figure came
              from is not decoration — the same question against staging and
              against production returns different numbers, and a dashboard
              mixing tiles from several connections is exactly the case where
              reading one as the other is costly. It used to be hover-gated
              provenance; on a wall display or a touch screen there is no
              hover, so it was effectively never shown at all.

              The model that wrote the SQL is deliberately *not* here. That is a
              fact about how the tile was authored, not about the figure on
              screen: it does not change with a refresh, it is identical for
              every tile built the same way, and once the SQL is saved the tile
              runs that SQL whoever proposed it. It lives on the tile editor,
              where it is being chosen, and on the run record, where it is
              being audited. */}
          {tile.connection_name && (
            <Chip small>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <Icon.Database size={10} />
                {tile.connection_name}
              </span>
            </Chip>
          )}
          <Chip small tone={rate > 0 ? 'green' : 'neutral'}>
            {rate > 0 ? rateLabel(rate) : 'Manual'}
          </Chip>

          <div className="rm-tile-actions" style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            {/* A tile two rows tall cannot show a twelve-row table, and the
                grid is the wrong place to fix that — resizing to read one
                number then resizing back is not a reading gesture. Every
                comparable product answers this the same way, with a
                full-screen view of one tile. */}
            {!focused && (
              <button
                className="rm-icon-btn"
                aria-label={`Expand ${tile.title || 'tile'}`}
                title="Expand"
                onClick={() => onAction('expand')}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 26,
                  height: 26,
                  borderRadius: 7,
                  border: 'none',
                  background: 'transparent',
                  color: 'var(--text-dim)',
                  cursor: 'pointer',
                  ['--rm-hover-bg' as string]: 'var(--panel-alt)',
                }}
              >
                <Icon.Expand size={14} />
              </button>
            )}
            <div style={{ position: 'relative' }}>
            <button
              className="rm-icon-btn"
              aria-label="Tile actions"
              onClick={() => setMenuOpen((open) => !open)}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 26,
                height: 26,
                borderRadius: 7,
                border: 'none',
                background: menuOpen ? 'var(--panel-alt)' : 'transparent',
                color: 'var(--text-dim)',
                cursor: 'pointer',
                ['--rm-hover-bg' as string]: 'var(--panel-alt)',
              }}
            >
              <Icon.More size={14} />
            </button>
            {menuOpen && (
              <TileMenu
                focused={focused}
                onClose={() => setMenuOpen(false)}
                onAction={(action) => {
                  setMenuOpen(false)
                  onAction(action)
                }}
              />
            )}
            </div>
          </div>
        </div>
      </div>

      {/* A filled chart must not sit in a scroll container: the chart sizes
          itself to the box, so any pixel of overshoot summons a scrollbar,
          which shrinks the box, which re-fits the chart, which dismisses the
          scrollbar — a visible oscillation. The chart fits by construction;
          everything else (tables, text) scrolls as before. */}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflow: tile.tile_type === 'CHART' && result?.vega_spec ? 'hidden' : 'auto',
          padding: '4px 12px 12px',
        }}
      >
        <TileBody tile={tile} result={result} loading={loading} />
      </div>
    </div>
  )
}

function TileMenu({
  onAction, onClose, focused = false,
}: {
  onAction: (action: TileAction) => void
  onClose: () => void
  /** Inside the focus overlay there is nothing left to expand into. */
  focused?: boolean
}) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  // The full set in every mode: the header's toggle governs the *grid* —
  // dragging, resizing, adding — while a single tile's own life cycle (edit,
  // duplicate, delete) belongs to the tile, reachable without a mode switch.
  // Expand is in the menu as well as on the header, because the header button
  // is revealed by hover and a touch device never produces one.
  const items: { action: TileAction; label: string; danger?: boolean }[] = [
    ...(focused ? [] : [{ action: 'expand' as const, label: 'Expand' }]),
    { action: 'refresh', label: 'Refresh now' },
    { action: 'edit', label: 'Edit' },
    { action: 'duplicate', label: 'Duplicate' },
    { action: 'delete', label: 'Delete', danger: true },
  ]

  return (
    <>
      <div
        onClick={onClose}
        style={{ position: 'fixed', inset: 0, zIndex: 40 }}
        aria-hidden
      />
      <div
        role="menu"
        className="rm-enter"
        style={{
          position: 'absolute',
          top: 30,
          right: 0,
          zIndex: 41,
          minWidth: 150,
          padding: 5,
          background: 'var(--panel)',
          border: '1px solid var(--border-strong)',
          borderRadius: 10,
          boxShadow: '0 16px 40px -14px rgba(0,0,0,.5)',
        }}
      >
        {items.map((item) => (
          <button
            key={item.action}
            className="rm-menu-item"
            role="menuitem"
            onClick={() => onAction(item.action)}
            style={{
              display: 'block',
              width: '100%',
              textAlign: 'left',
              padding: '7px 10px',
              borderRadius: 6,
              border: 'none',
              background: 'transparent',
              cursor: 'pointer',
              fontSize: 12.5,
              color: item.danger ? 'var(--red)' : 'var(--text)',
            }}
          >
            {item.label}
          </button>
        ))}
      </div>
    </>
  )
}

/** The three states every tile has: loading, an error, or data. */
function TileBody({
  tile, result, loading,
}: {
  tile: DashboardTile
  result?: TileResult
  loading: boolean
}) {
  if (tile.tile_type === 'TEXT') {
    return (
      <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
        {tile.question || tile.title}
      </div>
    )
  }

  // A tile that has never loaded draws a skeleton in the shape of what is
  // coming; one that is *re*-loading keeps its numbers on screen, with
  // "refreshing" in the header, because blanking a good result for half a
  // second reads as a fault. A spinner used to stand in for both, which told
  // the reader nothing about what the tile was about to become and made a
  // dashboard's first paint a field of unrelated spinners.
  if (!result) {
    return loading ? (
      <TileBodySkeleton kind={tile.tile_type} />
    ) : (
      <div style={{ display: 'grid', placeItems: 'center', height: '100%' }}>
        <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>No data yet</span>
      </div>
    )
  }

  if (result.status === 'ERROR' || result.error) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <ErrorNote>{result.error?.message ?? 'This tile could not be computed.'}</ErrorNote>
        {result.error?.code && (
          <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }} className="mono">
            {result.error.code}
          </span>
        )}
      </div>
    )
  }

  if (result.row_count === 0) {
    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 6,
          height: '100%',
          color: 'var(--text-faint)',
        }}
      >
        <Icon.Inbox size={20} />
        <span style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>No rows</span>
      </div>
    )
  }

  if (tile.tile_type === 'METRIC') return <MetricBody result={result} />

  if (tile.tile_type === 'CHART' && result.vega_spec) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, height: '100%' }}>
        {/* When the backend demoted the chart, the tile says so rather than
            quietly drawing something else. Above the chart, not below it: the
            plot has a fixed height, so anything under it sits below the tile's
            scroll fold — a substitution notice nobody can see isn't one. */}
        {result.chart_note && (
          <span style={{ fontSize: 10.5, color: 'var(--text-faint)', flexShrink: 0 }}>
            {result.chart_note}
          </span>
        )}
        {/* The chart owns whatever height the tile has left — a fixed plot
            floating at the top of a taller tile reads as wasted space. */}
        <div style={{ flex: 1, minHeight: 0 }}>
          <VegaChart spec={result.vega_spec} frameless fill />
        </div>
      </div>
    )
  }

  // TABLE, and any CHART whose data could not be drawn: the numbers are
  // correct whatever happened to the picture.
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {tile.tile_type === 'CHART' && result.chart_note && (
        <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>{result.chart_note}</span>
      )}
      {/* `table_config` is a TABLE tile's own setting. A CHART tile that fell
          back to the table draws it as the query returned it, because there is
          nowhere in the editor to configure a fallback the user did not ask
          for — and invisible stored state is worse than none. */}
      <ResultTable
        spec={result}
        previewRows={Number.POSITIVE_INFINITY}
        maxHeight="none"
        config={tile.tile_type === 'TABLE' ? tile.table_config : null}
      />
    </div>
  )
}

/**
 * One row, one numeric column, drawn big.
 *
 * The number itself is planned on the backend now — which column, how it is
 * written, whether the extra rows are a comparison or just clutter — so this
 * only has to decide what to do when there is no number at all. A tile whose
 * query stopped returning one falls back to the table rather than drawing a
 * dash where a metric used to be.
 */
function MetricBody({ result }: { result: TileResult }) {
  if (!result.kpi) return <ResultTable spec={result} previewRows={5} maxHeight="none" />
  return <Kpi spec={result.kpi} compact />
}

// ── skeletons ─────────────────────────────────────────────────────────────
/** A shimmering block. Inline width/height so callers compose a shape. */
function Bone({ w, h, r = 6, style }: {
  w: number | string
  h: number
  r?: number
  style?: React.CSSProperties
}) {
  return (
    <div
      className="rm-bone"
      aria-hidden
      style={{ width: w, height: h, borderRadius: r, flexShrink: 0, ...style }}
    />
  )
}

/**
 * The shape of the answer, before the answer.
 *
 * Drawn per tile type so first paint already says "a number is coming here, a
 * chart there" — the layout stops jumping when results land, and a slow
 * connection reads as loading rather than broken.
 */
function TileBodySkeleton({ kind }: { kind: DashboardTile['tile_type'] }) {
  if (kind === 'METRIC') {
    return (
      <div
        style={{
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 10,
        }}
      >
        <Bone w={124} h={30} r={8} />
        <Bone w={72} h={9} />
      </div>
    )
  }

  if (kind === 'CHART') {
    // Bars of settled, varied heights — a flat row of equal blocks reads as a
    // table, which is the one thing this must not be mistaken for.
    const bars = [46, 72, 58, 88, 64, 96, 52]
    return (
      <div style={{ height: '100%', display: 'flex', alignItems: 'flex-end', gap: 7, padding: '10px 2px 2px' }}>
        {bars.map((height, index) => (
          <Bone key={index} w="100%" h={0} r={5} style={{ height: `${height}%`, flex: 1 }} />
        ))}
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 9, paddingTop: 6 }}>
      {[92, 78, 85, 64, 72].map((width, index) => (
        <Bone key={index} w={`${width}%`} h={9} />
      ))}
    </div>
  )
}

/**
 * A whole tile, before the dashboard document has arrived.
 *
 * The index and the open dashboard both used a single centred spinner, which
 * paints nothing until everything is ready and then drops the finished layout
 * in at once. Reserving the boxes first is what makes an open feel instant.
 */
export function TileSkeleton({ height }: { height: number }) {
  return (
    <div
      className="rm-tile"
      aria-hidden
      style={{
        height,
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--panel)',
        borderRadius: 12,
        overflow: 'hidden',
        padding: '10px 12px 12px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <Bone w={124} h={11} />
        <Bone w={44} h={14} r={999} style={{ marginLeft: 'auto' }} />
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        <TileBodySkeleton kind="CHART" />
      </div>
    </div>
  )
}

// ── settings drawer ───────────────────────────────────────────────────────
/**
 * Grid geometry, palette, theme, and the default rate tiles inherit.
 *
 * The palette control offers exactly one option on purpose. The palette in
 * `VegaChart.tsx` is *measured* — OKLab ΔE, Machado-2009 CVD simulation,
 * contrast against the chart's own surface, per-mode accent anchoring — and
 * §7 of docs/dashboards.md is explicit that any additional set has to be run
 * through that validator in **both** themes before it ships. Offering four
 * more here, chosen by eye, is exactly the failure that rule exists to
 * prevent, so the picker is honest about having one validated set.
 */
export function DashboardSettings({
  dashboard, onChange, onClose,
}: {
  dashboard: Dashboard
  onChange: (patch: Record<string, unknown>) => void
  onClose: () => void
}) {
  return (
    <aside
      aria-label="Dashboard settings"
      className="rm-drawer"
      style={{
        width: 300,
        flexShrink: 0,
        borderLeft: '1px solid var(--border)',
        background: 'var(--sidebar-bg)',
        padding: '16px 18px',
        display: 'flex',
        flexDirection: 'column',
        gap: 14,
        overflowY: 'auto',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span aria-hidden style={{ display: 'flex', color: 'var(--text-dim)' }}>
          <Icon.Gear size={14} />
        </span>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-strong)' }}>
          Dashboard settings
        </span>
        <button
          onClick={onClose}
          aria-label="Close settings"
          className="rm-icon-btn"
          style={{
            marginLeft: 'auto',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 26,
            height: 26,
            borderRadius: 7,
            border: 'none',
            background: 'transparent',
            color: 'var(--text-dim)',
            cursor: 'pointer',
            ['--rm-hover-bg' as string]: 'var(--panel-alt)',
          }}
        >
          <Icon.Close size={14} />
        </button>
      </div>

      <SectionLabel>Grid</SectionLabel>
      <NumberSetting
        label="Grid columns"
        value={dashboard.grid_columns}
        min={1}
        max={48}
        onCommit={(value) => onChange({ grid_columns: value })}
      />
      <NumberSetting
        label="Row height (px)"
        value={dashboard.row_height_px}
        min={10}
        max={400}
        onCommit={(value) => onChange({ row_height_px: value })}
      />
      <NumberSetting
        label="Gap (px)"
        value={dashboard.gap_px}
        min={0}
        max={64}
        onCommit={(value) => onChange({ gap_px: value })}
      />

      {/* The grid has read this since the first tile was drawn and nothing
          could set it, so every dashboard was stuck compacting upward. Free
          placement is the whole reason the layout is stored per tile. */}
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>Tile placement</span>
        <select
          value={dashboard.compact_mode}
          onChange={(event) => onChange({ compact_mode: event.target.value })}
          style={selectStyle}
        >
          <option value="VERTICAL">Pull tiles upward</option>
          <option value="NONE">Leave tiles where they are put</option>
        </select>
        <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
          Pulling up closes the gaps for you; leaving them lets you park a tile
          with space around it.
        </span>
      </label>

      <SectionLabel>Appearance</SectionLabel>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>Chart palette</span>
        <select
          value={dashboard.palette}
          onChange={(event) => onChange({ palette: event.target.value })}
          style={selectStyle}
        >
          <option value="default">Default (measured)</option>
        </select>
        <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
          The chart palette is measured, not chosen. Another set needs the
          validator re-run in both themes before it can appear here.
        </span>
      </label>

      <SectionLabel>Refresh</SectionLabel>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>Default refresh rate</span>
        <select
          value={dashboard.default_refresh_interval_seconds}
          onChange={(event) =>
            onChange({ default_refresh_interval_seconds: Number(event.target.value) })
          }
          style={selectStyle}
        >
          {REFRESH_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
          What a tile uses when it sets no rate of its own.
        </span>
      </label>
    </aside>
  )
}

/** A quiet group heading, so the drawer scans as three topics, not one list. */
function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <span
      style={{
        fontSize: 10.5,
        fontWeight: 600,
        color: 'var(--text-faint)',
        textTransform: 'uppercase',
        letterSpacing: '0.07em',
        marginTop: 6,
        marginBottom: -6,
      }}
    >
      {children}
    </span>
  )
}

const selectStyle: React.CSSProperties = {
  background: 'var(--input-bg)',
  border: '1px solid var(--border-strong)',
  borderRadius: 7,
  padding: '7px 9px',
  color: 'var(--text)',
  fontSize: 13,
  outline: 'none',
  width: '100%',
}

/** Committed on blur, not per keystroke: one PATCH per edit, not per digit. */
function NumberSetting({
  label, value, min, max, onCommit,
}: {
  label: string
  value: number
  min: number
  max: number
  onCommit: (value: number) => void
}) {
  const [draft, setDraft] = useState(String(value))
  useEffect(() => setDraft(String(value)), [value])

  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          const parsed = Number(draft)
          if (!Number.isFinite(parsed)) return setDraft(String(value))
          const clamped = Math.min(max, Math.max(min, Math.round(parsed)))
          setDraft(String(clamped))
          if (clamped !== value) onCommit(clamped)
        }}
        style={selectStyle}
      />
    </label>
  )
}

// ── index cards ───────────────────────────────────────────────────────────
/**
 * A stable hue per dashboard, so a wall of cards is scannable.
 *
 * Every card used to carry the same accent-blue square and the same grid
 * glyph, which made twelve dashboards twelve identical rectangles — the eye
 * had nothing to lock onto and had to read every title. A hue derived from the
 * id gives each one a constant identity that survives renames and reorders.
 *
 * The six hues are the brand's own (the logo's violet/magenta/blue plus the
 * theme's green and amber), not an arbitrary wheel. The hue is an *identifier*,
 * never a measurement: it tints the card's glyph so the eye can lock onto a
 * shape it has seen before, and it is never used to colour a number, where a
 * meaningless hue would imply a meaning.
 *
 * It replaced a picture. The card used to open with an 86px cover drawing eight
 * rounded blocks — a sketch of "a dashboard" rather than of *this* dashboard,
 * since the list endpoint returns a tile count and no layout. Decoration that
 * occupies the most valuable strip of a card and answers no question is worse
 * than no decoration, so the space went back to the facts.
 */
const CARD_HUES = [250, 300, 340, 25, 80, 160]

function cardHue(id: string): number {
  let hash = 0
  for (let index = 0; index < id.length; index += 1) {
    hash = (hash * 31 + id.charCodeAt(index)) >>> 0
  }
  return CARD_HUES[hash % CARD_HUES.length]
}

/**
 * The dashboard's glyph, tinted with its identity hue.
 *
 * Shared by the card and the row so the same dashboard is the same colour in
 * both layouts — the point of a stable hue is defeated if switching to the list
 * reshuffles it.
 */
function DashboardGlyph({ hue, size = 34 }: { hue: number; size?: number }) {
  return (
    <span
      aria-hidden
      style={{
        display: 'grid',
        placeItems: 'center',
        width: size,
        height: size,
        flexShrink: 0,
        borderRadius: Math.round(size * 0.28),
        background: `oklch(0.7 0.16 ${hue} / 0.16)`,
        border: `1px solid oklch(0.7 0.16 ${hue} / 0.3)`,
        color: `oklch(0.65 0.17 ${hue})`,
      }}
    >
      <Icon.Grid size={Math.round(size * 0.47)} />
    </span>
  )
}

/** Rename / duplicate / archive / delete — shared by the card and the row. */
function DashboardMenu({
  dashboard, onRename, onDuplicate, onArchive, onDelete,
}: {
  dashboard: DashboardSummary
  onRename: () => void
  onDuplicate: () => void
  onArchive: () => void
  onDelete: () => void
}) {
  const [open, setOpen] = useState(false)
  const items = [
    { label: 'Rename', run: onRename },
    { label: 'Duplicate', run: onDuplicate },
    { label: dashboard.status === 'ARCHIVED' ? 'Unarchive' : 'Archive', run: onArchive },
    { label: 'Delete', run: onDelete, danger: true },
  ]

  return (
    // Above the card-wide link overlay, so the kebab stays clickable.
    <div className="rm-tile-actions" style={{ position: 'relative', zIndex: 2 }}>
      <button
        className="rm-icon-btn"
        aria-label={`Actions for ${dashboard.name}`}
        onClick={() => setOpen((current) => !current)}
        style={{
          display: 'flex',
          width: 27,
          height: 27,
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 8,
          border: 'none',
          background: open ? 'var(--panel-alt)' : 'transparent',
          color: 'var(--text-dim)',
          cursor: 'pointer',
          ['--rm-hover-bg' as string]: 'var(--panel-alt)',
        }}
      >
        <Icon.More size={14} />
      </button>
      {open && (
        <>
          <div
            onClick={() => setOpen(false)}
            style={{ position: 'fixed', inset: 0, zIndex: 40 }}
            aria-hidden
          />
          <div
            role="menu"
            className="rm-enter"
            style={{
              position: 'absolute',
              top: 31,
              right: 0,
              zIndex: 41,
              minWidth: 152,
              padding: 5,
              background: 'var(--panel)',
              border: '1px solid var(--border-strong)',
              borderRadius: 10,
              boxShadow: '0 16px 40px -14px rgba(0,0,0,.5)',
            }}
          >
            {items.map((item) => (
              <button
                key={item.label}
                className="rm-menu-item"
                role="menuitem"
                onClick={() => {
                  setOpen(false)
                  item.run()
                }}
                style={{
                  display: 'block',
                  width: '100%',
                  textAlign: 'left',
                  padding: '7px 9px',
                  borderRadius: 6,
                  border: 'none',
                  background: 'transparent',
                  cursor: 'pointer',
                  fontSize: 12.5,
                  color: item.danger ? 'var(--red)' : 'var(--text)',
                }}
              >
                {item.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

/** The shared footer facts, so the card and the row cannot drift apart. */
function CardMeta({ dashboard }: { dashboard: DashboardSummary }) {
  const rate = dashboard.default_refresh_interval_seconds
  return (
    <>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
        <Icon.Grid size={12} />
        {dashboard.tile_count} {dashboard.tile_count === 1 ? 'tile' : 'tiles'}
      </span>
      {/* Whether a dashboard is live or manual is the first thing anyone wants
          from an index of them, and it was the one fact the card omitted. */}
      <span aria-hidden style={{ opacity: 0.45 }}>·</span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
        {rate > 0 ? (
          <>
            <Dot color="var(--green)" />
            every {rateLabel(rate)}
          </>
        ) : (
          <>
            <Dot color="var(--text-faint)" />
            manual
          </>
        )}
      </span>
      <span aria-hidden style={{ opacity: 0.45 }}>·</span>
      <span>
        {dashboard.last_refreshed_at
          ? `ran ${relativeTime(dashboard.last_refreshed_at)}`
          : 'never run'}
      </span>
    </>
  )
}

export function DashboardCard({
  dashboard, onOpen, onRename, onDuplicate, onArchive, onDelete,
}: {
  dashboard: DashboardSummary
  onOpen: () => void
  onRename: () => void
  onDuplicate: () => void
  onArchive: () => void
  onDelete: () => void
}) {
  const hue = cardHue(dashboard.id)
  const archived = dashboard.status === 'ARCHIVED'

  return (
    <div
      className="rm-dash-card"
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        gap: 7,
        padding: '14px 15px 13px',
        background: 'var(--panel)',
        borderRadius: 14,
        opacity: archived ? 0.66 : 1,
      }}
    >
      {/* Title row: the glyph carries identity, the kebab sits opposite it, and
          the whole card is the link underneath both. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
        <DashboardGlyph hue={hue} />
        <button
          className="rm-dash-card-link"
          onClick={onOpen}
          style={{
            flex: 1,
            minWidth: 0,
            textAlign: 'left',
            background: 'transparent',
            border: 'none',
            padding: 0,
            cursor: 'pointer',
            color: 'var(--text-strong)',
            fontSize: 14.5,
            fontWeight: 650,
            letterSpacing: '-0.01em',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {dashboard.name}
        </button>
        <DashboardMenu
          dashboard={dashboard}
          onRename={onRename}
          onDuplicate={onDuplicate}
          onArchive={onArchive}
          onDelete={onDelete}
        />
      </div>

      {/* State worth acting on, and only when there is any: an empty dashboard
          is unfinished and a live one that has never run is misconfigured.
          Both were invisible on the old card, which showed a picture instead. */}
      {(archived || dashboard.tile_count === 0
        || (dashboard.default_refresh_interval_seconds > 0 && !dashboard.last_refreshed_at)) && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {archived && <Chip tone="amber">Archived</Chip>}
          {dashboard.tile_count === 0 && <Chip tone="neutral">No tiles yet</Chip>}
          {dashboard.tile_count > 0
            && dashboard.default_refresh_interval_seconds > 0
            && !dashboard.last_refreshed_at && <Chip tone="amber">Never run</Chip>}
        </div>
      )}

      {/* A fixed two-line well whether or not there is a description, so a
          row of cards keeps one baseline instead of ragging. */}
      <span
        style={{
          fontSize: 12.5,
          color: dashboard.description ? 'var(--text-dim)' : 'var(--text-faint)',
          fontStyle: dashboard.description ? undefined : 'italic',
          lineHeight: 1.5,
          minHeight: 37,
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          overflow: 'hidden',
        }}
      >
        {dashboard.description || 'No description'}
      </span>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 6,
          marginTop: 'auto',
          paddingTop: 10,
          borderTop: '1px solid var(--border)',
          fontSize: 11.5,
          color: 'var(--text-faint)',
        }}
      >
        <CardMeta dashboard={dashboard} />
      </div>
    </div>
  )
}

/**
 * The same dashboard as one dense row.
 *
 * Cards are the right default for a dozen; past that, a row puts four times as
 * many names on screen, and every comparable product offers the switch.
 */
export function DashboardRow({
  dashboard, onOpen, onRename, onDuplicate, onArchive, onDelete,
}: {
  dashboard: DashboardSummary
  onOpen: () => void
  onRename: () => void
  onDuplicate: () => void
  onArchive: () => void
  onDelete: () => void
}) {
  const hue = cardHue(dashboard.id)
  const archived = dashboard.status === 'ARCHIVED'

  return (
    <div
      className="rm-dash-row"
      style={{
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        gap: 13,
        padding: '11px 14px',
        background: 'var(--panel)',
        borderRadius: 11,
        opacity: archived ? 0.66 : 1,
      }}
    >
      <DashboardGlyph hue={hue} />

      <div style={{ minWidth: 0, flex: 1, display: 'flex', flexDirection: 'column', gap: 2 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <button
            className="rm-dash-card-link"
            onClick={onOpen}
            style={{
              minWidth: 0,
              textAlign: 'left',
              background: 'transparent',
              border: 'none',
              padding: 0,
              cursor: 'pointer',
              color: 'var(--text-strong)',
              fontSize: 13.5,
              fontWeight: 600,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {dashboard.name}
          </button>
          {archived && <Chip tone="amber">Archived</Chip>}
        </div>
        {dashboard.description && (
          <span
            style={{
              fontSize: 12,
              color: 'var(--text-dim)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {dashboard.description}
          </span>
        )}
      </div>

      <div
        className="rm-dash-row-meta"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 7,
          flexShrink: 0,
          fontSize: 11.5,
          color: 'var(--text-faint)',
        }}
      >
        <CardMeta dashboard={dashboard} />
      </div>

      <DashboardMenu
        dashboard={dashboard}
        onRename={onRename}
        onDuplicate={onDuplicate}
        onArchive={onArchive}
        onDelete={onDelete}
      />
    </div>
  )
}
