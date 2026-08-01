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
 *   A picked type is stored and gets re-fitted per refresh, and a pick the
 *   data cannot support is demoted by the backend with a note rather than
 *   blocking the save.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  ApiError, connections as connectionsApi, dashboards as api,
  llmConfigs as llmConfigsApi, sqlDrafts,
} from '../api/client'
import type {
  Connection, Dashboard, DashboardTile, LlmConfig, SqlDraft, SqlOrigin, TileType,
} from '../api/types'
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

/**
 * The chart picker. `auto` stores null; `table` is not a chart at all and
 * switches the tile's type — see the header.
 */
const CHART_TYPES: { value: string; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'bar', label: 'Bar' },
  { value: 'horizontal_bar', label: 'Horizontal bar' },
  { value: 'line', label: 'Line' },
  { value: 'area', label: 'Area' },
  { value: 'scatter', label: 'Scatter' },
  { value: 'pie', label: 'Pie' },
  { value: 'table', label: 'Table only' },
]

interface AxisState {
  type: string
  x: string
  y: string
  series: string
}

/** Read the editor's four controls back out of a stored `ChartIntent`. */
function axisStateFrom(config: Record<string, unknown> | null | undefined): AxisState {
  const intent = (config ?? {}) as {
    chart_type?: string
    x_axis?: { field?: string }
    y_axis?: { field?: string }
    series?: { field?: string }
  }
  return {
    type: intent.chart_type ?? 'auto',
    x: intent.x_axis?.field ?? '',
    y: intent.y_axis?.field ?? '',
    series: intent.series?.field ?? '',
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
    const suggested = axisStateFrom(draft.chart_suggestion)
    setAxes((current) => {
      // A pick this result can still honour is kept; one it cannot is
      // replaced. Leaving it would store an axis naming a column the query no
      // longer returns, and that degrades to Auto at refresh without a word.
      const keep = (field: string) => (names.has(field) ? field : '')
      const next = {
        ...current,
        x: keep(current.x) || suggested.x,
        y: keep(current.y) || suggested.y,
        series: keep(current.series),
      }
      return next.x === current.x && next.y === current.y && next.series === current.series
        ? current
        : next
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
    if (tileType !== 'CHART' || axes.type === 'auto' || axes.type === 'table') return null
    if (!axes.x || !axes.y) return null
    const axisType = (field: string): string =>
      columns.find((column) => column.name === field)?.semantic_type ?? 'nominal'
    // Exactly the keys `ChartIntent` declares: it is `extra="forbid"`, and a
    // config it refuses is silently treated as Auto at refresh time.
    const intent: Record<string, unknown> = {
      chart_type: axes.type,
      x_axis: { field: axes.x, type: axisType(axes.x), aggregation: 'none' },
      y_axis: { field: axes.y, type: axisType(axes.y), aggregation: 'none' },
    }
    if (axes.series && axes.type !== 'pie') {
      intent.series = { field: axes.series, type: axisType(axes.series), aggregation: 'none' }
    }
    return intent
  }, [axes, columns, tileType])

  /** A new tile lands under everything already on the grid, full width of 4. */
  const placement = useMemo(() => {
    const bottom = dashboard.tiles.reduce((low, t) => Math.max(low, t.grid_y + t.grid_h), 0)
    return {
      grid_x: 0,
      grid_y: bottom,
      grid_w: Math.min(4, dashboard.grid_columns),
      grid_h: 4,
      position: dashboard.tiles.length,
    }
  }, [dashboard])

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
    origin, placement, question, refresh, sql, tile, tileType, title,
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
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 200px', gap: 12 }}>
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

              {tileType === 'CHART' && (
                <ChartPicker
                  axes={axes}
                  columns={columns}
                  onChange={(next) => {
                    if (next.type === 'table') {
                      // Not a chart at all: the honest storage of "show me the
                      // numbers" is the tile's type.
                      setTileType('TABLE')
                      setAxes({ ...next, type: 'auto' })
                      return
                    }
                    setAxes(next)
                  }}
                />
              )}
            </>
          )}

          {/* A TEXT tile computes nothing, so it has no clock and no row cap
              — offering either would be a control that does nothing. */}
          {needsSql && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
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
    <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr auto', gap: 10, alignItems: 'end' }}>
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

// ── the chart pickers ─────────────────────────────────────────────────────
function ChartPicker({
  axes, columns, onChange,
}: {
  axes: AxisState
  columns: { name: string; semantic_type: string }[]
  onChange: (axes: AxisState) => void
}) {
  // Axes can only be picked from columns we have actually seen come back.
  // Offering names from the SQL text would be guessing at what the database
  // will call them.
  const known = columns.length >= 2

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
        <Field label="Chart">
          <Select
            value={axes.type}
            onChange={(event) => onChange({ ...axes, type: event.target.value })}
          >
            {CHART_TYPES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </Field>
        {axes.type !== 'auto' && (
          <>
            <Field label={axes.type === 'pie' ? 'Slices' : 'X'}>
              <Select
                value={axes.x}
                disabled={!known}
                onChange={(event) => onChange({ ...axes, x: event.target.value })}
              >
                <option value="">—</option>
                {columns.map((column) => (
                  <option key={column.name} value={column.name}>
                    {column.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={axes.type === 'pie' ? 'Size' : 'Y'}>
              <Select
                value={axes.y}
                disabled={!known}
                onChange={(event) => onChange({ ...axes, y: event.target.value })}
              >
                <option value="">—</option>
                {columns.map((column) => (
                  <option key={column.name} value={column.name}>
                    {column.name}
                  </option>
                ))}
              </Select>
            </Field>
            {axes.type !== 'pie' && (
              <Field label="Series">
                <Select
                  value={axes.series}
                  disabled={!known}
                  onChange={(event) => onChange({ ...axes, series: event.target.value })}
                >
                  <option value="">None</option>
                  {columns.map((column) => (
                    <option key={column.name} value={column.name}>
                      {column.name}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
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
