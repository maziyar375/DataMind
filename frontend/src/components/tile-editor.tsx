/**
 * The tile editor — one modal, two ways to write the same SQL.
 *
 * The tab chooses how the text got into the textarea, **not** what happens to
 * it afterwards. Both paths land in the same box, both are re-checked by the
 * same guard, and neither is privileged: a model's SQL is a draft, a person's
 * SQL is a draft, and the backend re-validates whichever one is saved on every
 * single refresh. `sql_origin` is provenance for the UI's chips, never a trust
 * signal.
 *
 * Three things here are less obvious than they look:
 *
 * * **The guard check is not per tab.** One debounced `POST
 *   /sql/drafts/validate` watches the textarea whatever put text in it, so
 *   editing what the model wrote is checked exactly like typing it yourself.
 * * **"Table only" is a tile type, not a chart intent.** Storing
 *   `chart_type: "none"` would not suppress the chart: `validate_intent`
 *   refuses that intent and `plan_chart` falls through to the heuristic, which
 *   draws something anyway. The only statement that actually means "show me
 *   the numbers" is `tile_type = TABLE`.
 * * **Auto is a value.** `chart_config = null` means "re-decide from every
 *   result", which is the right default for a tile whose data changes shape.
 *   A picked type is stored and gets re-fitted per refresh.
 * * **The type grid is filtered by the preview.** Types this result cannot
 *   carry are disabled with the reason on hover, from the same verdicts the
 *   planner computes. The backend still re-fits on every refresh and still
 *   demotes with a note if a tile's data changes shape under it — that is the
 *   backstop, not the interface. Being told "your pick does not fit this
 *   result" *after* saving was the thing worth removing.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  ApiError, connections as connectionsApi, dashboards as api,
  llmConfigs as llmConfigsApi, sqlDrafts,
} from '../api/client'
import type {
  ChartOption, Connection, Dashboard, DashboardTile, LlmConfig, SqlDraft,
  SqlOrigin, TableColumnConfig, TableConfig, TileType,
} from '../api/types'
import { ChartTypePicker } from './chart-picker'
import { REFRESH_OPTIONS, rateLabel } from './dashboard'
import {
  Chip, ErrorNote, Field, GhostButton, Icon, Modal, PrimaryButton, ResultTable,
  Select, Spinner, TextArea, TextInput,
} from './ui'

/** How long the textarea sits still before the guard is asked about it. */
const CHECK_DEBOUNCE_MS = 600

const TILE_TYPES: { value: TileType; label: string; hint: string }[] = [
  { value: 'CHART', label: 'Chart', hint: 'A picture, with the table as the fallback.' },
  { value: 'TABLE', label: 'Table', hint: 'The rows, as they come back.' },
  { value: 'METRIC', label: 'Big number', hint: 'One row, one number, drawn large.' },
  { value: 'TEXT', label: 'Text', hint: 'A note. No connection, no SQL, nothing to run.' },
]

/** The marks with area to divide between a split. */
const STACKABLE = ['bar', 'area']

/**
 * What the two positional pickers mean for each type.
 *
 * Every chart here puts *something* on x and y, but only for the cartesian
 * family are they "the axes". A pie's are a slice and its angle, a heatmap's
 * are the two sides of a matrix whose measure is a third channel, and a
 * histogram has no y to pick at all — it counts its own rows. Labelling all of
 * them "X" and "Y" is how someone ends up choosing a column for an axis that
 * does not exist on the chart in front of them.
 */
const AXIS_LABELS: Record<string, { x: string; y: string | null }> = {
  pie: { x: 'Slices', y: 'Size' },
  heatmap: { x: 'Rows', y: 'Columns' },
  histogram: { x: 'Values', y: null },
  combo: { x: 'X', y: 'Bars' },
}

/**
 * Which way the bars run. Its own control rather than two entries in the list
 * above, because they were never two charts: same mark, same comparison, same
 * reading. As two types the backend's cardinality rule replaced the pick and
 * then reported that the pick "does not fit this result" — a control that
 * argues with you. Here `auto` is the one that defers, and it is the default.
 */
const ORIENTATIONS: { value: string; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'vertical', label: 'Vertical' },
  { value: 'horizontal', label: 'Horizontal' },
]

/**
 * How a split shares the space. Shown only for a bar or area that *has* a
 * series — one series has nothing to stack against, and the backend resets the
 * field to its default in that case rather than storing a claim the chart
 * cannot honour.
 */
const STACKS: { value: string; label: string; hint: string }[] = [
  { value: 'stacked', label: 'Stacked', hint: 'The parts add up to a total.' },
  { value: 'grouped', label: 'Grouped', hint: 'The parts are compared with each other.' },
  { value: 'normalize', label: '100%', hint: 'Share of the whole, not absolute size.' },
]

interface AxisState {
  type: string
  orientation: string
  stack: string
  x: string
  y: string
  series: string
  size: string
  /** Heatmap: the measure in the cells. Combo: the measure drawn as a line. */
  color: string
  y2: string
}

/**
 * The columns the backend fitted when it judged this type supported.
 *
 * The picker's verdicts are per *type*, and each one was reached by fitting
 * particular columns: a monthly-by-warehouse result is a supported pie because
 * `warehouse_name` has six values, not because `month` has twenty-five. Keeping
 * the previous type's columns across the click is how "supported" became a
 * promise the save path then broke — the tile stored `pie` over `month` and the
 * backend, doing exactly what it says it does, drew a bar instead.
 *
 * So a type change adopts the columns that verdict was about. A manual override
 * afterwards is still obeyed, demotion note and all; what is no longer possible
 * is being demoted for a choice nobody made.
 */
function columnsForType(type: string, options: ChartOption[]): Record<string, string> | null {
  return options.find((option) => option.chart_type === type)?.columns ?? null
}

/** The channels a chart's columns live in, in the order the editor shows them. */
const CHANNELS = ['x', 'y', 'series', 'size', 'color', 'y2'] as const

/**
 * Switch the chart type and move the columns with it.
 *
 * Every channel is re-taken from the new type's verdict, including the ones it
 * does not use — a `series` left over from a bar chart is not a preference a
 * pie can honour, and carrying it into the stored config is how a control that
 * looks clean saves something the backend has to ignore. With no verdict yet
 * (the preview has not run) only the type moves, which is what the picker's
 * empty-list contract already means everywhere else: no opinion, change
 * nothing.
 */
function withType(axes: AxisState, type: string, options: ChartOption[]): AxisState {
  const fitted = columnsForType(type, options)
  if (type === 'auto' || fitted === null) return { ...axes, type }
  const next = { ...axes, type }
  for (const channel of CHANNELS) next[channel] = fitted[channel] ?? ''
  return next
}

/** Read the editor's controls back out of a stored `ChartIntent`. */
function axisStateFrom(config: Record<string, unknown> | null | undefined): AxisState {
  const intent = (config ?? {}) as {
    chart_type?: string
    orientation?: string
    stack?: string
    x_axis?: { field?: string }
    y_axis?: { field?: string }
    series?: { field?: string }
    size?: { field?: string }
    color?: { field?: string }
    y2_axis?: { field?: string }
  }
  // The pre-consolidation spelling, read the way the backend reads it. A tile
  // the migration has not reached yet still opens showing what it draws.
  const legacy = intent.chart_type === 'horizontal_bar'
  return {
    type: legacy ? 'bar' : intent.chart_type ?? 'auto',
    orientation: legacy ? 'horizontal' : intent.orientation ?? 'auto',
    stack: intent.stack ?? 'stacked',
    x: intent.x_axis?.field ?? '',
    y: intent.y_axis?.field ?? '',
    series: intent.series?.field ?? '',
    size: intent.size?.field ?? '',
    color: intent.color?.field ?? '',
    y2: intent.y2_axis?.field ?? '',
  }
}

export function TileEditor({
  dashboard, tile, onClose, onSaved,
}: {
  dashboard: Dashboard
  /** null for "Add tile"; a tile for "Edit". */
  tile: DashboardTile | null
  onClose: () => void
  onSaved: (tile: DashboardTile) => void
}) {
  const [connections, setConnections] = useState<Connection[] | null>(null)
  const [models, setModels] = useState<LlmConfig[] | null>(null)

  const [tab, setTab] = useState<'ask' | 'sql'>(
    tile && tile.sql_origin === 'HANDWRITTEN' ? 'sql' : 'ask',
  )
  const [title, setTitle] = useState(tile?.title ?? '')
  const [tileType, setTileType] = useState<TileType>(tile?.tile_type ?? 'CHART')
  const [connectionId, setConnectionId] = useState(tile?.connection_id ?? '')
  const [llmConfigId, setLlmConfigId] = useState(tile?.llm_config_id ?? '')
  const [question, setQuestion] = useState(tile?.question ?? '')
  const [sql, setSql] = useState(tile?.sql ?? '')
  const [origin, setOrigin] = useState<SqlOrigin>(tile?.sql_origin ?? 'HANDWRITTEN')
  const [maxRows, setMaxRows] = useState(tile?.max_rows == null ? '' : String(tile.max_rows))
  const [refresh, setRefresh] = useState(
    tile?.refresh_interval_seconds == null ? '' : String(tile.refresh_interval_seconds),
  )
  const [axes, setAxes] = useState<AxisState>(() => axisStateFrom(tile?.chart_config))
  const [table, setTable] = useState<TableConfig>(
    () => tile?.table_config ?? { columns: [], sort_column: null, sort_direction: 'asc' },
  )

  const [draft, setDraft] = useState<SqlDraft | null>(null)
  /** The exact text the current `draft` describes — so a keystroke re-checks. */
  const [checkedSql, setCheckedSql] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const connection = connections?.find((c) => c.id === connectionId) ?? null
  const columns = draft?.preview?.columns ?? []
  const needsSql = tileType !== 'TEXT'

  useEffect(() => {
    let cancelled = false
    Promise.all([connectionsApi.list(), llmConfigsApi.list()])
      .then(([loadedConnections, loadedModels]) => {
        if (cancelled) return
        setConnections(loadedConnections)
        setModels(loadedModels)
        // One connection and no choice to make: pick it, so the common case
        // opens ready to type.
        if (!tile && loadedConnections.length === 1) setConnectionId(loadedConnections[0].id)
        if (!tile && loadedModels.length === 1) setLlmConfigId(loadedModels[0].id)
        // Asking is the primary path, but it is not the only one: with no
        // provider configured the editor opens on the tab that needs none.
        if (!tile && loadedModels.length === 0) setTab('sql')
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load connections.')
      })
    return () => {
      cancelled = true
    }
  }, [tile])

  // ── the guard, on a debounce ────────────────────────────────────────────
  // One check for both tabs. What matters is that the text changed, not which
  // tab is showing: editing the model's SQL is exactly as unchecked as typing
  // your own.
  useEffect(() => {
    if (!needsSql || !connectionId || !sql.trim() || sql === checkedSql) return
    let cancelled = false
    const wanted = sql
    setChecking(true)
    const timer = window.setTimeout(() => {
      sqlDrafts
        .validate({ connection_id: connectionId, sql: wanted })
        .then((result) => {
          if (cancelled) return
          setDraft(result)
          setCheckedSql(wanted)
          setError(null)
        })
        .catch((err) => {
          if (!cancelled) {
            setError(err instanceof Error ? err.message : 'The SQL could not be checked.')
          }
        })
        .finally(() => {
          if (!cancelled) setChecking(false)
        })
    }, CHECK_DEBOUNCE_MS)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      setChecking(false)
    }
  }, [sql, checkedSql, connectionId, needsSql])

  // Pre-fill the axis pickers from the shape that just came back, without
  // moving the type off Auto: the suggestion is a sensible default for *if*
  // the user picks a type, not a reason to stop re-planning every refresh.
  useEffect(() => {
    const preview = draft?.preview
    if (!preview) return
    const names = new Set(preview.columns.map((column) => column.name))
    // Defaults for the type actually selected, not for the one the heuristic
    // would have chosen. `chart_suggestion` answers "what should this be
    // drawn as", which is only the right question while the type is Auto —
    // once a type is picked, the columns that make *that* type work are the
    // ones the option carries.
    const fitted = columnsForType(axes.type, draft.chart_options)
    const suggested = axisStateFrom(draft.chart_suggestion)
    setAxes((current) => {
      // A pick this result can still honour is kept; one it cannot is
      // replaced. Leaving it would store an axis naming a column the query no
      // longer returns, and that degrades to Auto at refresh without a word.
      const keep = (field: string) => (names.has(field) ? field : '')
      const fallback = (channel: (typeof CHANNELS)[number]) =>
        fitted?.[channel] ?? (channel === 'x' || channel === 'y' ? suggested[channel] : '')
      const next = { ...current }
      for (const channel of CHANNELS) next[channel] = keep(current[channel]) || fallback(channel)
      return CHANNELS.every((channel) => next[channel] === current[channel]) ? current : next
    })
  }, [draft, axes.type])

  // Every column the result has gets a row in the picker, appended in query
  // order. Entries already configured keep their place — including ones whose
  // column has since disappeared, which the picker greys rather than deletes.
  useEffect(() => {
    const preview = draft?.preview
    if (!preview) return
    setTable((current) => {
      const known = new Set(current.columns.map((column) => column.name))
      const added = preview.columns
        .filter((column) => !known.has(column.name))
        .map((column) => ({ name: column.name }))
      return added.length === 0
        ? current
        : { ...current, columns: [...current.columns, ...added] }
    })
  }, [draft])

  const generate = useCallback(async () => {
    if (!connectionId || !llmConfigId || !question.trim()) return
    setGenerating(true)
    setError(null)
    try {
      const result = await sqlDrafts.draft({
        connection_id: connectionId,
        llm_config_id: llmConfigId,
        question: question.trim(),
      })
      setDraft(result)
      setSql(result.sql)
      setCheckedSql(result.sql)
      setOrigin('GENERATED')
      if (!title.trim()) setTitle(question.trim().slice(0, 200))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The model could not draft that.')
    } finally {
      setGenerating(false)
    }
  }, [connectionId, llmConfigId, question, title])

  /**
   * Provenance follows whoever last wrote the text, not the tab in front.
   * Switching to the SQL tab to correct one join does not erase the fact that
   * a model wrote the other twenty lines.
   */
  const editSql = useCallback((value: string) => {
    setSql(value)
    setOrigin((current) => (current === 'GENERATED' ? 'GENERATED_EDITED' : current))
  }, [])

  const chartConfig = useCallback((): Record<string, unknown> | null => {
    if (tileType !== 'CHART' || axes.type === 'auto') return null
    const wantsY = AXIS_LABELS[axes.type]?.y !== null
    if (!axes.x || (wantsY && !axes.y)) return null
    const axisType = (field: string): string =>
      columns.find((column) => column.name === field)?.semantic_type ?? 'nominal'
    const channel = (field: string) => ({
      field, type: axisType(field), aggregation: 'none',
    })
    // Exactly the keys `ChartIntent` declares: it is `extra="forbid"`, and a
    // config it refuses is silently treated as Auto at refresh time.
    const intent: Record<string, unknown> = {
      chart_type: axes.type,
      x_axis: channel(axes.x),
    }
    // A histogram counts its own rows, so it has no y column to name and the
    // model derives one. Sending an empty y_axis would fail validation and
    // land the tile on Auto.
    if (wantsY) intent.y_axis = channel(axes.y)
    if (axes.type === 'heatmap' && axes.color) intent.color = channel(axes.color)
    if (axes.type === 'combo' && axes.y2) intent.y2_axis = channel(axes.y2)
    // Only when it says something: each of these has a default the backend
    // already applies, so writing it would store a preference nobody expressed
    // and freeze a decision the platform should keep making.
    if (axes.type === 'bar' && axes.orientation !== 'auto') {
      intent.orientation = axes.orientation
    }
    if (axes.series && axes.type !== 'pie') {
      intent.series = { field: axes.series, type: axisType(axes.series), aggregation: 'none' }
      if (STACKABLE.includes(axes.type) && axes.stack !== 'stacked') {
        intent.stack = axes.stack
      }
    }
    if (axes.size && axes.type === 'scatter') {
      intent.size = { field: axes.size, type: axisType(axes.size), aggregation: 'none' }
    }
    return intent
  }, [axes, columns, tileType])

  /**
   * The table settings, or null when they say nothing.
   *
   * NULL is a documented meaning — "as the query returned it" — so a tile
   * whose picker was merely *looked at* stays NULL rather than pinning a
   * column order the user never asked for and would be surprised by when the
   * query changes.
   */
  const tableConfig = useCallback((): TableConfig | null => {
    if (tileType !== 'TABLE') return null
    const said = (column: TableColumnConfig) =>
      column.hidden === true
      || (column.label != null && column.label !== '')
      || (column.align != null && column.align !== 'auto')
      || (column.format != null && column.format !== 'auto')
    const reordered = table.columns.some(
      (column, index) => columns[index]?.name !== undefined && columns[index].name !== column.name,
    )
    if (!table.sort_column && !reordered && !table.columns.some(said)) return null
    return table
  }, [columns, table, tileType])

  /**
   * A new tile lands under everything already on the grid, sized so its own
   * content fits without an inner scrollbar: a chart's plot is a fixed 300px
   * (`VegaChart`), a metric is one big number, a table settles around a few
   * rows. Row count is computed against the dashboard's actual row height and
   * gap — a denser grid gets more rows, not a taller tile — so the first
   * paint is right whatever the grid settings say.
   */
  const placement = useMemo(() => {
    const bottom = dashboard.tiles.reduce((low, t) => Math.max(low, t.grid_y + t.grid_h), 0)
    const { grid_columns: cols, row_height_px: rowH, gap_px: gap } = dashboard
    // Pixel budget per type: header ≈ 50px + body padding + the content itself.
    const needed =
      tileType === 'CHART' ? 400
      : tileType === 'TABLE' ? 330
      : tileType === 'METRIC' ? 150
      : 130
    // Smallest h where h·rowH + (h−1)·gap ≥ needed.
    const rows = Math.max(2, Math.ceil((needed + gap) / (rowH + gap)))
    const fraction =
      tileType === 'CHART' || tileType === 'TABLE' ? 1 / 2
      : tileType === 'METRIC' ? 1 / 4
      : 1 / 3
    return {
      grid_x: 0,
      grid_y: bottom,
      grid_w: Math.max(2, Math.min(cols, Math.round(cols * fraction))),
      grid_h: rows,
      position: dashboard.tiles.length,
    }
  }, [dashboard, tileType])

  const save = useCallback(async () => {
    setSaving(true)
    setError(null)
    const payload: Record<string, unknown> = {
      title: title.trim(),
      tile_type: tileType,
      connection_id: needsSql ? connectionId : null,
      // The model chip is only honest when a model was involved.
      llm_config_id: needsSql && origin !== 'HANDWRITTEN' && llmConfigId ? llmConfigId : null,
      question: question.trim() || null,
      sql: needsSql ? sql : '',
      sql_origin: origin,
      chart_config: chartConfig(),
      table_config: tableConfig(),
      max_rows: maxRows.trim() ? Number(maxRows) : null,
      refresh_interval_seconds: refresh === '' ? null : Number(refresh),
    }
    try {
      const saved = tile
        ? await api.updateTile(dashboard.id, tile.id, payload)
        : await api.addTile(dashboard.id, { ...payload, ...placement })
      onSaved(saved)
    } catch (err) {
      // A guard rejection at save is a 422 whose sentence is the whole point:
      // the preview passing was never authorisation to save.
      setError(err instanceof ApiError ? err.message : 'The tile could not be saved.')
      setSaving(false)
    }
  }, [
    chartConfig, connectionId, dashboard.id, llmConfigId, maxRows, needsSql, onSaved,
    origin, placement, question, refresh, sql, tableConfig, tile, tileType, title,
  ])

  const canSave = needsSql ? Boolean(connectionId && sql.trim()) : true
  const inheritLabel = `Inherit dashboard default (${rateLabel(
    dashboard.default_refresh_interval_seconds,
  )})`

  return (
    <Modal
      title={tile ? 'Edit tile' : 'Add tile'}
      subtitle={
        needsSql
          ? 'Ask for the SQL or write it yourself — both are checked the same way.'
          : 'A note on the grid. Nothing runs.'
      }
      width={880}
      onClose={onClose}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton onClick={() => void save()} disabled={saving || !canSave}>
            {saving ? 'Saving…' : tile ? 'Save tile' : 'Add tile'}
          </PrimaryButton>
        </>
      }
    >
      {connections === null || models === null ? (
        <div style={{ display: 'grid', placeItems: 'center', padding: 40 }}>
          <Spinner size={18} />
        </div>
      ) : (
        <>
          <div className="rm-col-2" style={{ display: 'grid', gridTemplateColumns: '1fr 200px', gap: 12 }}>
            <Field label="Title">
              <TextInput
                autoFocus
                value={title}
                placeholder="Revenue by month"
                onChange={(event) => setTitle(event.target.value)}
              />
            </Field>
            <Field
              label="Tile type"
              hint={TILE_TYPES.find((t) => t.value === tileType)?.hint}
            >
              <Select
                value={tileType}
                onChange={(event) => setTileType(event.target.value as TileType)}
              >
                {TILE_TYPES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </Field>
          </div>

          {!needsSql ? (
            <Field label="Text" hint="Markdown is not rendered; line breaks are kept.">
              <TextArea
                value={question}
                rows={6}
                placeholder="What this section of the dashboard is for."
                onChange={(event) => setQuestion(event.target.value)}
              />
            </Field>
          ) : (
            <>
              <Field
                label="Connection"
                hint="This tile's own database. Two tiles on one dashboard may use two."
              >
                <Select
                  value={connectionId}
                  onChange={(event) => {
                    setConnectionId(event.target.value)
                    // The old report described the old schema; keeping it on
                    // screen would be a claim about a database we did not ask.
                    setDraft(null)
                    setCheckedSql(null)
                  }}
                >
                  <option value="">Choose a connection…</option>
                  {connections.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.name}
                    </option>
                  ))}
                </Select>
              </Field>

              <EditorTabs
                value={tab}
                onChange={setTab}
                items={[
                  { value: 'ask', label: 'Ask' },
                  { value: 'sql', label: 'Write SQL' },
                ]}
              />

              {tab === 'ask' ? (
                <AskPanel
                  models={models}
                  llmConfigId={llmConfigId}
                  question={question}
                  busy={generating}
                  disabled={!connectionId}
                  onModel={setLlmConfigId}
                  onQuestion={setQuestion}
                  onGenerate={() => void generate()}
                />
              ) : (
                <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
                  No model is involved here, and none is required — a dashboard can
                  be built end to end with no LLM provider configured.
                </span>
              )}

              <Field
                label="SQL"
                hint={
                  connectionId
                    ? 'Checked against this connection every time you stop typing — and again on save, and again on every refresh.'
                    : "Choose a connection first: the guard resolves every name against that database's schema."
                }
              >
                <TextArea
                  className="mono"
                  value={sql}
                  rows={9}
                  spellCheck={false}
                  placeholder="SELECT status, SUM(total_amount) FROM orders GROUP BY status"
                  onChange={(event) => editSql(event.target.value)}
                  style={{ fontSize: 12.5, minHeight: 150 }}
                />
              </Field>

              <GuardReport draft={draft} checking={checking} origin={origin} />

              {tileType === 'TABLE' && (
                <Field
                  label="Table"
                  hint="Which columns show, in what order, and how they read."
                >
                  <TablePicker columns={columns} config={table} onChange={setTable} />
                </Field>
              )}

              {tileType === 'CHART' && (
                <ChartPicker
                  axes={axes}
                  columns={columns}
                  options={draft?.chart_options ?? []}
                  onChange={setAxes}
                />
              )}
            </>
          )}

          {/* A TEXT tile computes nothing, so it has no clock and no row cap
              — offering either would be a control that does nothing. */}
          {needsSql && (
            <div className="rm-col-2" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <Field label="Refresh" hint="This tile's own clock.">
                <Select value={refresh} onChange={(event) => setRefresh(event.target.value)}>
                  <option value="">{inheritLabel}</option>
                  {REFRESH_OPTIONS.map((option) => (
                    <option key={option.value} value={String(option.value)}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field
                label="Row cap"
                hint={
                  connection
                    ? `Optional. May only lower this connection's cap of ${connection.max_rows.toLocaleString()}.`
                    : "Optional. May only lower the connection's own cap."
                }
              >
                <TextInput
                  type="number"
                  min={1}
                  value={maxRows}
                  placeholder={connection ? String(connection.max_rows) : ''}
                  onChange={(event) => setMaxRows(event.target.value)}
                />
              </Field>
            </div>
          )}

          {error && <ErrorNote>{error}</ErrorNote>}
        </>
      )}
    </Modal>
  )
}

// ── the Ask tab ───────────────────────────────────────────────────────────
function AskPanel({
  models, llmConfigId, question, busy, disabled, onModel, onQuestion, onGenerate,
}: {
  models: LlmConfig[]
  llmConfigId: string
  question: string
  busy: boolean
  disabled: boolean
  onModel: (id: string) => void
  onQuestion: (value: string) => void
  onGenerate: () => void
}) {
  if (models.length === 0) {
    return (
      <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
        No LLM provider is configured. Add one under LLM providers, or write the
        SQL yourself on the other tab — the tile works the same either way.
      </span>
    )
  }

  const ready = Boolean(llmConfigId && question.trim()) && !disabled
  return (
    <div className="rm-col-2" style={{ display: 'grid', gridTemplateColumns: '200px 1fr auto', gap: 10, alignItems: 'end' }}>
      <Field label="Model">
        <Select value={llmConfigId} onChange={(event) => onModel(event.target.value)}>
          <option value="">Choose a model…</option>
          {models.map((model) => (
            <option key={model.id} value={model.id}>
              {model.name}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Question">
        <TextInput
          value={question}
          placeholder="revenue by month for the last year"
          onChange={(event) => onQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && ready && !busy) onGenerate()
          }}
        />
      </Field>
      <PrimaryButton onClick={onGenerate} disabled={!ready || busy}>
        {busy ? <Spinner size={13} /> : <Icon.Sparkle size={14} />}
        {busy ? 'Drafting…' : 'Generate'}
      </PrimaryButton>
    </div>
  )
}

function EditorTabs({
  value, onChange, items,
}: {
  value: 'ask' | 'sql'
  onChange: (value: 'ask' | 'sql') => void
  items: { value: 'ask' | 'sql'; label: string }[]
}) {
  return (
    <div
      role="tablist"
      style={{
        display: 'inline-flex',
        gap: 2,
        padding: 3,
        borderRadius: 9,
        background: 'var(--panel-alt)',
        border: '1px solid var(--border)',
        alignSelf: 'flex-start',
      }}
    >
      {items.map((item) => {
        const active = item.value === value
        return (
          <button
            key={item.value}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(item.value)}
            style={{
              padding: '6px 14px',
              borderRadius: 7,
              border: 'none',
              cursor: 'pointer',
              fontSize: 12.5,
              fontWeight: 600,
              background: active ? 'var(--panel)' : 'transparent',
              color: active ? 'var(--text-strong)' : 'var(--text-dim)',
            }}
          >
            {item.label}
          </button>
        )
      })}
    </div>
  )
}

// ── the guard's verdict, inline ───────────────────────────────────────────
/**
 * The same inline report the semantic-layer editor shows, for the same reason:
 * the browser cannot know the dialect or the schema, so the only opinion worth
 * rendering is the one the save path will use.
 */
function GuardReport({
  draft, checking, origin,
}: {
  draft: SqlDraft | null
  checking: boolean
  origin: SqlOrigin
}) {
  if (!draft) {
    return (
      <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
        {checking ? 'Checking…' : "The guard's verdict and a preview appear here."}
      </span>
    )
  }

  const rejected = draft.validation_status !== 'VALID'
  const issues = draft.validation_report.issues ?? []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Chip tone={rejected ? 'red' : 'green'}>{rejected ? 'Rejected' : 'Valid'}</Chip>
        {checking && <Spinner size={12} />}
        {origin !== 'HANDWRITTEN' && <Chip tone="accent">{originLabel(origin)}</Chip>}
        {draft.referenced_tables.length > 0 && (
          <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
            {draft.referenced_tables.join(', ')}
          </span>
        )}
        {draft.preview && !rejected && (
          <span style={{ fontSize: 10.5, color: 'var(--text-faint)', marginLeft: 'auto' }}>
            preview: {draft.preview.row_count.toLocaleString()}
            {draft.preview.truncated ? '+' : ''} rows in {draft.preview.duration_ms} ms
          </span>
        )}
      </div>

      {issues.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          {issues.map((issue, index) => (
            <div
              key={`${issue.rule_id}-${index}`}
              style={{
                display: 'flex',
                gap: 8,
                fontSize: 12,
                color: issue.severity === 'ERROR' ? 'var(--red)' : 'var(--text-dim)',
              }}
            >
              <span className="mono" style={{ fontSize: 10.5, opacity: 0.8, flexShrink: 0 }}>
                {issue.rule_id}
              </span>
              <span>
                {issue.message}
                {issue.hint ? ` — ${issue.hint}` : ''}
              </span>
            </div>
          ))}
        </div>
      )}

      {draft.preview?.error && <ErrorNote>{draft.preview.error.message}</ErrorNote>}

      {draft.preview && draft.preview.status === 'OK' && draft.preview.columns.length > 0 && (
        <ResultTable spec={draft.preview} previewRows={5} maxHeight={220} />
      )}
    </div>
  )
}

function originLabel(origin: SqlOrigin): string {
  return origin === 'GENERATED' ? 'Model-written' : 'Model-written, edited'
}

// ── the table pickers ─────────────────────────────────────────────────────
const ALIGNS: { value: string; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'left', label: 'Left' },
  { value: 'right', label: 'Right' },
  { value: 'center', label: 'Center' },
]

const FORMATS: { value: string; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'integer', label: '1,235' },
  { value: 'decimal', label: '1,234.50' },
  { value: 'percent', label: '42%' },
  { value: 'text', label: 'As-is' },
]

/**
 * Which columns a TABLE tile shows, in what order, and how.
 *
 * Driven by the preview's columns, like the chart pickers and for the same
 * reason: a column name typed from the SQL is a guess at what the database
 * will call it. A stored entry whose column the result no longer has is shown
 * greyed rather than dropped, so the user can see why their column vanished
 * instead of finding the setting silently gone.
 */
function TablePicker({
  columns, config, onChange,
}: {
  columns: { name: string; semantic_type: string }[]
  config: TableConfig
  onChange: (config: TableConfig) => void
}) {
  if (columns.length === 0) {
    return (
      <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
        Run the SQL above to choose columns — they come from the result, not from
        the text.
      </span>
    )
  }

  const patch = (name: string, change: Partial<TableColumnConfig>) =>
    onChange({
      ...config,
      columns: config.columns.map((column) =>
        column.name === name ? { ...column, ...change } : column,
      ),
    })

  const move = (index: number, by: number) => {
    const next = [...config.columns]
    const to = index + by
    if (to < 0 || to >= next.length) return
    ;[next[index], next[to]] = [next[to], next[index]]
    onChange({ ...config, columns: next })
  }

  const known = new Set(columns.map((column) => column.name))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div
        style={{
          border: '1px solid var(--border)',
          borderRadius: 9,
          overflow: 'hidden',
        }}
      >
        {config.columns.map((entry, index) => {
          const missing = !known.has(entry.name)
          return (
            <div
              key={entry.name}
              style={{
                display: 'grid',
                gridTemplateColumns: '22px minmax(90px, 1fr) minmax(90px, 1fr) 96px 104px 44px',
                gap: 8,
                alignItems: 'center',
                padding: '7px 9px',
                borderTop: index === 0 ? 'none' : '1px solid var(--border)',
                opacity: missing ? 0.55 : 1,
              }}
            >
              <input
                type="checkbox"
                aria-label={`Show ${entry.name}`}
                checked={!entry.hidden}
                disabled={missing}
                onChange={(event) => patch(entry.name, { hidden: !event.target.checked })}
                style={{ accentColor: 'var(--accent)', cursor: 'pointer' }}
              />
              <span
                className="mono"
                title={missing ? 'This column is not in the current result' : entry.name}
                style={{
                  fontSize: 11.5,
                  color: 'var(--text-dim)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {entry.name}
                {missing && ' (gone)'}
              </span>
              <TextInput
                aria-label={`Heading for ${entry.name}`}
                value={entry.label ?? ''}
                placeholder={entry.name}
                onChange={(event) => patch(entry.name, { label: event.target.value || null })}
                style={{ fontSize: 12, padding: '5px 8px' }}
              />
              <Select
                aria-label={`Alignment of ${entry.name}`}
                value={entry.align ?? 'auto'}
                onChange={(event) => patch(entry.name, { align: event.target.value as never })}
                style={{ fontSize: 12, padding: '5px 6px' }}
              >
                {ALIGNS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </Select>
              <Select
                aria-label={`Format of ${entry.name}`}
                value={entry.format ?? 'auto'}
                onChange={(event) => patch(entry.name, { format: event.target.value as never })}
                style={{ fontSize: 12, padding: '5px 6px' }}
              >
                {FORMATS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </Select>
              <div style={{ display: 'flex', gap: 2 }}>
                <MoveButton label={`Move ${entry.name} up`} up
                  disabled={index === 0} onClick={() => move(index, -1)} />
                <MoveButton label={`Move ${entry.name} down`}
                  disabled={index === config.columns.length - 1} onClick={() => move(index, 1)} />
              </div>
            </div>
          )
        })}
      </div>

      <div className="rm-col-2" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <Field label="Sort by">
          <Select
            value={config.sort_column ?? ''}
            onChange={(event) =>
              onChange({ ...config, sort_column: event.target.value || null })
            }
          >
            <option value="">The order the query returned</option>
            {columns.map((column) => (
              <option key={column.name} value={column.name}>{column.name}</option>
            ))}
          </Select>
        </Field>
        <Field label="Direction">
          <Select
            value={config.sort_direction ?? 'asc'}
            disabled={!config.sort_column}
            onChange={(event) =>
              onChange({ ...config, sort_direction: event.target.value as 'asc' | 'desc' })
            }
          >
            <option value="asc">Ascending</option>
            <option value="desc">Descending</option>
          </Select>
        </Field>
      </div>

      <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
        Presentation only, applied in the browser — none of it re-runs the query,
        and hiding a column hides it rather than withholding it. A column the
        query adds later appears at the end.
      </span>
    </div>
  )
}

function MoveButton({
  label, up = false, disabled, onClick,
}: {
  label: string
  up?: boolean
  disabled: boolean
  onClick: () => void
}) {
  return (
    <button
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="rm-icon-btn"
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 20,
        height: 20,
        borderRadius: 5,
        border: '1px solid var(--border-strong)',
        background: 'transparent',
        color: 'var(--text-dim)',
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled ? 0.35 : 1,
        // One chevron asset, turned: the icon set has no up/down pair and
        // inventing one for two 20px buttons is not worth the divergence.
        transform: up ? 'rotate(-90deg)' : 'rotate(90deg)',
      }}
    >
      <Icon.Chevron size={11} />
    </button>
  )
}

// ── the chart pickers ─────────────────────────────────────────────────────
function ChartPicker({
  axes, columns, options, onChange,
}: {
  axes: AxisState
  columns: { name: string; semantic_type: string }[]
  /** Per-type verdicts from the last preview; empty until one has run. */
  options: ChartOption[]
  onChange: (axes: AxisState) => void
}) {
  // Axes can only be picked from columns we have actually seen come back.
  // Offering names from the SQL text would be guessing at what the database
  // will call them.
  const known = columns.length >= 2
  const labels = AXIS_LABELS[axes.type] ?? { x: 'X', y: 'Y' }

  /** One column dropdown, bound to a key of the picker's own state. */
  const pick = (
    key: 'x' | 'y' | 'series' | 'size' | 'color' | 'y2',
    label: string,
    { empty = 'None', hint }: { empty?: string; hint?: string } = {},
  ) => (
    <Field label={label} hint={hint}>
      <Select
        value={axes[key]}
        disabled={!known}
        onChange={(event) => onChange({ ...axes, [key]: event.target.value })}
      >
        <option value="">{empty}</option>
        {columns.map((column) => (
          <option key={column.name} value={column.name}>
            {column.name}
          </option>
        ))}
      </Select>
    </Field>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <Field label="Chart">
        <ChartTypePicker
          value={axes.type}
          options={options}
          columns={9}
          onChange={(type) => onChange(withType(axes, type, options))}
        />
      </Field>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
        {axes.type !== 'auto' && (
          <>
            {pick('x', labels.x, { empty: '—' })}
            {labels.y !== null && pick('y', labels.y, { empty: '—' })}
            {axes.type === 'heatmap'
              && pick('color', 'Measure', { empty: '—', hint: 'The value in each cell.' })}
            {axes.type === 'combo'
              && pick('y2', 'Line', { empty: '—', hint: 'The second measure, over the bars.' })}
            {!['pie', 'heatmap', 'histogram', 'combo'].includes(axes.type)
              && pick('series', 'Series')}
            {/* These flow onto a second row of the grid, under the type they
                belong to. Each appears only where it means something: an
                orientation on a pie, or a stack with nothing split, would be a
                control that does nothing whichever way it is set. */}
            {axes.type === 'bar' && (
              <Field label="Bars">
                <Select
                  value={axes.orientation}
                  onChange={(event) => onChange({ ...axes, orientation: event.target.value })}
                >
                  {ORIENTATIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            {STACKABLE.includes(axes.type) && axes.series !== '' && (
              <Field label="Split" hint={STACKS.find((s) => s.value === axes.stack)?.hint}>
                <Select
                  value={axes.stack}
                  onChange={(event) => onChange({ ...axes, stack: event.target.value })}
                >
                  {STACKS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            {axes.type === 'scatter'
              && pick('size', 'Size', { hint: "A third measure, read as the point's area." })}
          </>
        )}
      </div>

      <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
        {axes.type === 'auto'
          ? 'Auto re-decides the chart from every result, which is what a tile whose data changes shape wants.'
          : !known
            ? 'Run the SQL above to pick columns — the axes come from the result, not from the text.'
            : 'Kept as written and re-fitted on every refresh. A pick this data cannot support is demoted, and the tile says so.'}
      </span>
    </div>
  )
}
