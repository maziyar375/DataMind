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
import type { Dashboard, DashboardTile, TileResult } from '../api/types'
import { Chip, ErrorNote, Icon, ResultTable, Spinner, relativeTime } from './ui'
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
        // The header is the handle: dragging from anywhere would make
        // selecting a cell in a table impossible.
        draggableHandle=".rm-tile-drag"
        compactType={dashboard.compact_mode === 'NONE' ? null : 'vertical'}
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
export type TileAction = 'refresh' | 'edit' | 'duplicate' | 'delete'

export function TileShell({
  tile, result, loading, editing, onAction,
}: {
  tile: DashboardTile
  result?: TileResult
  loading: boolean
  editing: boolean
  onAction: (action: TileAction) => void
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const rate = tile.effective_refresh_interval_seconds

  return (
    <div
      className="rm-tile"
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--panel)',
        border: '1px solid var(--border)',
        borderRadius: 12,
        overflow: 'hidden',
      }}
    >
      <div
        className={editing ? 'rm-tile-drag' : undefined}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '10px 12px',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        {/* In edit mode the header is the drag handle; the grip says so
            before the cursor does. */}
        {editing && (
          <span aria-hidden style={{ display: 'flex', color: 'var(--text-faint)', flexShrink: 0 }}>
            <Icon.Grip size={13} />
          </span>
        )}
        <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--text-strong)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {tile.title || 'Untitled tile'}
          </span>
          {/* The freshness stamp is not decoration: it is the only way a
              reader tells a 30-second tile from the hourly one beside it. */}
          {result && (
            <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
              as of {clockTime(result.computed_at)}
              {loading ? ' · refreshing' : ''}
            </span>
          )}
        </div>

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
          {tile.connection_name && <Chip>{tile.connection_name}</Chip>}
          {tile.llm_config_name && tile.sql_origin !== 'HANDWRITTEN' && (
            <Chip tone="accent">{tile.llm_config_name}</Chip>
          )}
          <Chip tone={rate > 0 ? 'green' : 'neutral'}>
            {rate > 0 ? rateLabel(rate) : 'Manual'}
          </Chip>

          <div className="rm-tile-actions" style={{ position: 'relative' }}>
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
                editing={editing}
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

      <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 10 }}>
        <TileBody tile={tile} result={result} loading={loading} />
      </div>
    </div>
  )
}

function TileMenu({
  editing, onAction, onClose,
}: {
  editing: boolean
  onAction: (action: TileAction) => void
  onClose: () => void
}) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const items: { action: TileAction; label: string; danger?: boolean }[] = [
    { action: 'refresh', label: 'Refresh now' },
    ...(editing
      ? ([
          { action: 'edit', label: 'Edit' },
          { action: 'duplicate', label: 'Duplicate' },
          { action: 'delete', label: 'Delete', danger: true },
        ] as const)
      : []),
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

  // A tile that has never loaded shows a spinner; one that is *re*-loading
  // keeps its numbers on screen, with "refreshing" in the header, because
  // blanking a good result for half a second reads as a fault.
  if (!result) {
    return (
      <div style={{ display: 'grid', placeItems: 'center', height: '100%' }}>
        {loading ? <Spinner /> : <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>No data yet</span>}
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
      <div style={{ display: 'grid', placeItems: 'center', height: '100%' }}>
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
        <VegaChart spec={result.vega_spec} frameless />
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

/** One row, one numeric column, drawn big. */
function MetricBody({ result }: { result: TileResult }) {
  const index = Math.max(
    0,
    result.columns.findIndex((column) => column.semantic_type === 'quantitative'),
  )
  const value = result.rows[0]?.[index]
  const label = result.columns[index]?.name ?? ''

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 4,
      }}
    >
      <span
        className="mono"
        style={{
          fontSize: 34,
          fontWeight: 700,
          color: 'var(--text-strong)',
          lineHeight: 1.1,
          letterSpacing: '-0.01em',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {typeof value === 'number' ? value.toLocaleString() : String(value ?? '—')}
      </span>
      <span
        style={{
          fontSize: 11,
          color: 'var(--text-dim)',
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
        }}
      >
        {label}
      </span>
      {result.row_count > 1 && (
        <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
          first of {result.row_count.toLocaleString()} rows
        </span>
      )}
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

      <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>Theme</span>
        <select
          value={dashboard.theme_override}
          onChange={(event) => onChange({ theme_override: event.target.value })}
          style={selectStyle}
        >
          <option value="INHERIT">Follow the app</option>
          <option value="DARK">Always dark</option>
          <option value="LIGHT">Always light</option>
        </select>
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
export function DashboardCard({
  dashboard, onOpen, onRename, onDuplicate, onArchive, onDelete,
}: {
  dashboard: {
    id: string
    name: string
    description: string | null
    status: string
    tile_count: number
    last_refreshed_at: string | null
  }
  onOpen: () => void
  onRename: () => void
  onDuplicate: () => void
  onArchive: () => void
  onDelete: () => void
}) {
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <div
      className="rm-dash-card"
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        padding: 16,
        background: 'var(--panel)',
        border: '1px solid var(--border)',
        borderRadius: 12,
        opacity: dashboard.status === 'ARCHIVED' ? 0.72 : 1,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <span
          aria-hidden
          style={{
            display: 'grid',
            placeItems: 'center',
            width: 32,
            height: 32,
            flexShrink: 0,
            borderRadius: 9,
            background: 'var(--accent-bg)',
            color: 'var(--accent)',
          }}
        >
          <Icon.Grid size={16} />
        </span>
        <button
          className="rm-dash-card-link"
          onClick={onOpen}
          style={{
            flex: 1,
            minWidth: 0,
            marginTop: 5,
            textAlign: 'left',
            background: 'transparent',
            border: 'none',
            padding: 0,
            cursor: 'pointer',
            color: 'var(--text-strong)',
            fontSize: 14.5,
            fontWeight: 600,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {dashboard.name}
        </button>
        {dashboard.status === 'ARCHIVED' && <Chip tone="amber">Archived</Chip>}
        {/* Above the card-wide link overlay, so the kebab stays clickable. */}
        <div className="rm-tile-actions" style={{ position: 'relative', zIndex: 1 }}>
          <button
            className="rm-icon-btn"
            aria-label="Dashboard actions"
            onClick={() => setMenuOpen((open) => !open)}
            style={{
              display: 'flex',
              width: 26,
              height: 26,
              alignItems: 'center',
              justifyContent: 'center',
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
            <>
              <div
                onClick={() => setMenuOpen(false)}
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
                {[
                  { label: 'Rename', run: onRename },
                  { label: 'Duplicate', run: onDuplicate },
                  {
                    label: dashboard.status === 'ARCHIVED' ? 'Unarchive' : 'Archive',
                    run: onArchive,
                  },
                  { label: 'Delete', run: onDelete, danger: true },
                ].map((item) => (
                  <button
                    key={item.label}
                    className="rm-menu-item"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false)
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
      </div>

      {dashboard.description && (
        <span
          style={{
            fontSize: 12.5,
            color: 'var(--text-dim)',
            lineHeight: 1.5,
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {dashboard.description}
        </span>
      )}

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          marginTop: 'auto',
          paddingTop: 4,
          fontSize: 11.5,
          color: 'var(--text-faint)',
        }}
      >
        <span>
          {dashboard.tile_count} {dashboard.tile_count === 1 ? 'tile' : 'tiles'}
        </span>
        <span aria-hidden>·</span>
        <span>
          {dashboard.last_refreshed_at
            ? `refreshed ${relativeTime(dashboard.last_refreshed_at)}`
            : 'never refreshed'}
        </span>
      </div>
    </div>
  )
}
