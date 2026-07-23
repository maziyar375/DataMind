import { useCallback, useEffect, useMemo, useState } from 'react'
import { connections as api } from '../api/client'
import type { Connection, SchemaSnapshot, SchemaTable, TestResult } from '../api/types'
import {
  Chip, DangerButton, Dot, EmptyState, ErrorNote, Field, GhostButton, Icon,
  PrimaryButton, Select, Spinner, TextInput,
} from '../components/ui'

const BLANK = {
  name: 'New connection',
  database_type: 'postgres',
  host: 'localhost',
  port: 5432,
  database_name: '',
  username: '',
  password: '',
  ssl_mode: 'require',
  schema_allowlist: [] as string[],
  max_rows: 1000,
  statement_timeout_ms: 30000,
  disclosure_policy: 'SAMPLE',
}

export default function DataSourcesPage() {
  const [list, setList] = useState<Connection[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<Record<string, any>>(BLANK)
  const [password, setPassword] = useState('')
  const [creating, setCreating] = useState(false)
  const [schema, setSchema] = useState<SchemaSnapshot | null>(null)
  const [tab, setTab] = useState<'tables' | 'graph'>('tables')
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<TestResult | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const selected = useMemo(
    () => list.find((c) => c.id === selectedId) ?? null,
    [list, selectedId],
  )

  const refresh = useCallback(async () => {
    const items = await api.list()
    setList(items)
    if (!selectedId && items.length > 0) setSelectedId(items[0].id)
    return items
  }, [selectedId])

  useEffect(() => {
    refresh().catch(() => setError('Could not load your data sources.'))
  }, [])

  useEffect(() => {
    if (!selected) return
    setCreating(false)
    setPassword('')
    setTestResult(null)
    setDraft({
      name: selected.name,
      database_type: selected.database_type,
      host: selected.host,
      port: selected.port,
      database_name: selected.database_name,
      username: selected.username,
      ssl_mode: selected.ssl_mode ?? 'require',
      schema_allowlist: selected.schema_allowlist,
      max_rows: selected.max_rows,
      statement_timeout_ms: selected.statement_timeout_ms,
      disclosure_policy: selected.disclosure_policy,
    })
    api
      .schema(selected.id)
      .then(setSchema)
      .catch(() => setSchema(null))
  }, [selectedId])

  function startCreate() {
    setCreating(true)
    setSelectedId(null)
    setSchema(null)
    setDraft(BLANK)
    setPassword('')
    setTestResult(null)
  }

  async function save() {
    setSaving(true)
    setError(null)
    try {
      if (creating) {
        const created = await api.create({ ...draft, password })
        await refresh()
        setSelectedId(created.id)
        setCreating(false)
      } else if (selected) {
        const payload: Record<string, unknown> = { ...draft }
        if (password) payload.password = password
        await api.update(selected.id, payload)
        await refresh()
        setPassword('')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save this connection.')
    } finally {
      setSaving(false)
    }
  }

  async function test() {
    if (!selected) return
    setTesting(true)
    setTestResult(null)
    try {
      setTestResult(await api.test(selected.id))
      await refresh()
    } catch (err) {
      setTestResult({
        ok: false,
        latency_ms: 0,
        message: err instanceof Error ? err.message : 'Test failed.',
      })
    } finally {
      setTesting(false)
    }
  }

  async function sync() {
    if (!selected) return
    setSyncing(true)
    setError(null)
    try {
      setSchema(await api.syncSchema(selected.id))
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not read the schema.')
    } finally {
      setSyncing(false)
    }
  }

  async function remove() {
    if (!selected) return
    await api.remove(selected.id)
    setSelectedId(null)
    setSchema(null)
    const items = await api.list()
    setList(items)
    if (items.length > 0) setSelectedId(items[0].id)
  }

  const filteredTables = useMemo(() => {
    if (!schema) return []
    const needle = search.trim().toLowerCase()
    if (!needle) return schema.tables
    return schema.tables.filter(
      (table) =>
        table.name.toLowerCase().includes(needle) ||
        table.columns.some((c) => c.name.toLowerCase().includes(needle)),
    )
  }, [schema, search])

  return (
    <div style={{ display: 'flex', height: '100%', width: '100%', minWidth: 0 }}>
      {/* left: list + form */}
      <div
        style={{
          width: 380,
          flexShrink: 0,
          overflowY: 'auto',
          padding: 28,
          borderRight: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
          gap: 18,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ fontSize: 16, fontWeight: 700 }}>Data sources</div>
          <button
            onClick={startCreate}
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: 'var(--accent)',
              background: 'var(--accent-bg)',
              border: '1px solid var(--accent-border)',
              padding: '5px 10px',
              borderRadius: 6,
              cursor: 'pointer',
            }}
          >
            + New
          </button>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {list.map((connection) => {
            const active = connection.id === selectedId
            return (
              <button
                key={connection.id}
                onClick={() => setSelectedId(connection.id)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '10px 12px',
                  borderRadius: 9,
                  cursor: 'pointer',
                  textAlign: 'left',
                  background: active ? 'var(--panel-hover)' : 'var(--panel)',
                  border: `1px solid ${active ? 'var(--accent-border)' : 'var(--border)'}`,
                }}
              >
                <span style={{ fontSize: 13, fontWeight: 600 }}>{connection.name}</span>
                <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
                  {connection.host}:{connection.port}
                </span>
                {connection.is_default && (
                  <span style={{ marginLeft: 'auto' }}>
                    <Chip tone="green">Default</Chip>
                  </span>
                )}
              </button>
            )
          })}
          {list.length === 0 && !creating && (
            <div style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
              No data sources yet. Add one to start asking questions.
            </div>
          )}
        </div>

        {(selected || creating) && (
          <div
            style={{
              borderTop: '1px solid var(--border)',
              paddingTop: 16,
              display: 'flex',
              flexDirection: 'column',
              gap: 14,
            }}
          >
            {error && <ErrorNote>{error}</ErrorNote>}

            <Field label="Name">
              <TextInput
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </Field>

            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 10 }}>
              <Field label="Host">
                <TextInput
                  value={draft.host}
                  onChange={(e) => setDraft({ ...draft, host: e.target.value })}
                />
              </Field>
              <Field label="Port">
                <TextInput
                  type="number"
                  value={draft.port}
                  onChange={(e) => setDraft({ ...draft, port: Number(e.target.value) })}
                />
              </Field>
            </div>

            <Field label="Database">
              <TextInput
                value={draft.database_name}
                onChange={(e) => setDraft({ ...draft, database_name: e.target.value })}
              />
            </Field>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <Field label="User">
                <TextInput
                  value={draft.username}
                  onChange={(e) => setDraft({ ...draft, username: e.target.value })}
                />
              </Field>
              <Field
                label="Password"
                hint={creating ? undefined : 'Leave blank to keep the stored one'}
              >
                <TextInput
                  type="password"
                  autoComplete="new-password"
                  placeholder={creating ? '' : '••••••••'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>
            </div>

            <Field label="SSL mode">
              <Select
                value={draft.ssl_mode}
                onChange={(e) => setDraft({ ...draft, ssl_mode: e.target.value })}
              >
                <option value="require">require</option>
                <option value="verify-full">verify-full</option>
                <option value="disable">disable</option>
              </Select>
            </Field>

            <Field label="Schema allowlist (optional)">
              <TextInput
                placeholder="public, analytics"
                value={(draft.schema_allowlist ?? []).join(', ')}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    schema_allowlist: e.target.value
                      .split(',')
                      .map((s) => s.trim())
                      .filter(Boolean),
                  })
                }
              />
            </Field>

            <Field
              label="Result sharing"
              hint="How much of a query result may be sent to the model provider."
            >
              <Select
                value={draft.disclosure_policy}
                onChange={(e) => setDraft({ ...draft, disclosure_policy: e.target.value })}
              >
                <option value="NONE">Nothing — the model never sees result rows</option>
                <option value="AGGREGATE">Totals only</option>
                <option value="SAMPLE">A sample of rows</option>
                <option value="FULL">All returned rows</option>
              </Select>
            </Field>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <Field label="Row limit">
                <TextInput
                  type="number"
                  value={draft.max_rows}
                  onChange={(e) => setDraft({ ...draft, max_rows: Number(e.target.value) })}
                />
              </Field>
              <Field label="Query timeout (ms)">
                <TextInput
                  type="number"
                  value={draft.statement_timeout_ms}
                  onChange={(e) =>
                    setDraft({ ...draft, statement_timeout_ms: Number(e.target.value) })
                  }
                />
              </Field>
            </div>

            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <PrimaryButton onClick={save} disabled={saving}>
                {saving && <Spinner />}
                {creating ? 'Add connection' : 'Save changes'}
              </PrimaryButton>
              {!creating && (
                <GhostButton onClick={test} disabled={testing}>
                  {testing && <Spinner />}
                  Test connection
                </GhostButton>
              )}
            </div>

            {testResult && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  fontSize: 12.5,
                  color: testResult.ok ? 'var(--green)' : 'var(--red)',
                }}
              >
                <Dot color={testResult.ok ? 'var(--green)' : 'var(--red)'} />
                {testResult.ok
                  ? `Connected · ${
                      testResult.readonly_confirmed
                        ? 'read-only role confirmed'
                        : 'this role can write — use a read-only role'
                    } · ${testResult.latency_ms}ms`
                  : testResult.message}
              </div>
            )}

            {!creating && selected && !selected.is_default && (
              <GhostButton
                onClick={async () => {
                  await api.update(selected.id, { is_default: true })
                  await refresh()
                }}
                style={{
                  alignSelf: 'flex-start',
                  color: 'var(--accent)',
                  borderColor: 'var(--accent-border)',
                }}
              >
                Set as default
              </GhostButton>
            )}

            {!creating && selected && (
              <DangerButton onClick={remove} style={{ alignSelf: 'flex-start' }}>
                <Icon.Trash />
                Delete connection
              </DangerButton>
            )}

            {!creating && selected && (
              <div
                style={{
                  borderTop: '1px solid var(--border)',
                  paddingTop: 16,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 8,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <GhostButton onClick={sync} disabled={syncing}>
                    {syncing && <Spinner />}
                    Re-sync schema
                  </GhostButton>
                  <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
                    {selected.last_synced_at
                      ? `last synced ${relativeTime(selected.last_synced_at)}`
                      : 'never synced'}
                  </span>
                </div>
                {schema && (
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <Chip tone="green">{schema.tables.length} tables</Chip>
                    <Chip tone="neutral">
                      {schema.relationships.length} relationships
                    </Chip>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* right: schema explorer */}
      <div style={{ flex: 1, overflow: 'auto', padding: 28, minWidth: 0 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 14,
          }}
        >
          <div style={{ display: 'flex', gap: 6 }}>
            <TabButton active={tab === 'tables'} onClick={() => setTab('tables')}>
              Table list
            </TabButton>
            <TabButton active={tab === 'graph'} onClick={() => setTab('graph')}>
              Graph view
            </TabButton>
          </div>
          {tab === 'tables' && (
            <TextInput
              placeholder="Search tables & columns…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: 260, fontSize: 13, padding: '8px 11px' }}
            />
          )}
        </div>

        {!schema && (
          <EmptyState
            title="No schema yet"
            body="Sync this connection to read its tables, columns, and foreign keys. Raymand only ever writes SQL against what it finds here."
            action={
              selected ? (
                <PrimaryButton onClick={sync} disabled={syncing}>
                  {syncing && <Spinner />}
                  Sync schema
                </PrimaryButton>
              ) : undefined
            }
          />
        )}

        {schema && tab === 'tables' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {filteredTables.map((table) => (
              <TableCard
                key={`${table.schema}.${table.name}`}
                table={table}
                open={!!expanded[`${table.schema}.${table.name}`]}
                onToggle={() =>
                  setExpanded((prev) => ({
                    ...prev,
                    [`${table.schema}.${table.name}`]:
                      !prev[`${table.schema}.${table.name}`],
                  }))
                }
              />
            ))}
          </div>
        )}

        {schema && tab === 'graph' && <GraphView schema={schema} />}
      </div>
    </div>
  )
}

function TabButton({
  active, onClick, children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      style={{
        fontSize: 12.5,
        fontWeight: 600,
        padding: '7px 13px',
        borderRadius: 7,
        cursor: 'pointer',
        color: active ? 'var(--text-strong)' : 'var(--text-dim)',
        background: active ? 'var(--panel-alt)' : 'transparent',
        border: `1px solid ${active ? 'var(--border-strong)' : 'transparent'}`,
      }}
    >
      {children}
    </button>
  )
}

function TableCard({
  table, open, onToggle,
}: {
  table: SchemaTable
  open: boolean
  onToggle: () => void
}) {
  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: 10,
        background: 'var(--panel)',
        overflow: 'hidden',
      }}
    >
      <button
        onClick={onToggle}
        aria-expanded={open}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          width: '100%',
          padding: '11px 14px',
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <Icon.Chevron open={open} size={13} stroke="var(--text-dim)" />
        <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-strong)' }}>
          {table.name}
        </span>
        <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
          {table.schema}
        </span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          <Chip>{table.columns.length} cols</Chip>
          {table.approx_row_count != null && (
            <Chip>~{table.approx_row_count.toLocaleString()} rows</Chip>
          )}
        </span>
      </button>

      {open && (
        <div style={{ borderTop: '1px solid var(--border)' }}>
          {table.columns.map((column) => (
            <div
              key={column.name}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '8px 14px 8px 37px',
                borderTop: '1px solid var(--border)',
              }}
            >
              <span
                className="mono"
                style={{ fontSize: 12.5, color: 'var(--text2)', minWidth: 170 }}
              >
                {column.name}
              </span>
              <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
                {column.data_type}
              </span>
              <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                {column.is_primary_key && <Chip tone="accent">PK</Chip>}
                {column.is_foreign_key && (
                  <Chip tone="green">FK → {column.references?.split('.').slice(-2, -1)}</Chip>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Foreign keys drawn as a graph. This is why schema sync records FKs from the
 * first release even though the view arrived later — backfilling them would
 * mean re-syncing every connection.
 */
function GraphView({ schema }: { schema: SchemaSnapshot }) {
  const layout = useMemo(() => {
    const columns = 3
    const nodeWidth = 220
    const gapX = 90
    const gapY = 70

    const heights = schema.tables.map((t) => 44 + Math.min(t.columns.length, 6) * 20)
    const positions = schema.tables.map((table, index) => {
      const col = index % columns
      const row = Math.floor(index / columns)
      const priorInColumn = heights.filter(
        (_, i) => i % columns === col && Math.floor(i / columns) < row,
      )
      const y = priorInColumn.reduce((sum, h) => sum + h + gapY, 40)
      return {
        table,
        x: 40 + col * (nodeWidth + gapX),
        y,
        w: nodeWidth,
        h: heights[index],
      }
    })

    const byName = new Map(
      positions.map((p) => [`${p.table.schema}.${p.table.name}`, p]),
    )
    const edges = schema.relationships
      .map((rel) => {
        const from = byName.get(rel.from_table)
        const to = byName.get(rel.to_table)
        return from && to ? { from, to, rel } : null
      })
      .filter(Boolean) as { from: any; to: any; rel: any }[]

    const width = 40 + columns * (nodeWidth + gapX)
    const height = Math.max(...positions.map((p) => p.y + p.h), 300) + 60
    return { positions, edges, width, height }
  }, [schema])

  if (schema.tables.length === 0) {
    return (
      <EmptyState title="Nothing to graph" body="This schema has no tables yet." />
    )
  }

  return (
    <div style={{ overflow: 'auto' }}>
      <svg
        width={layout.width}
        height={layout.height}
        style={{ minWidth: '100%' }}
        role="img"
        aria-label="Schema relationship graph"
      >
        <defs>
          <marker
            id="arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--border-strong)" />
          </marker>
        </defs>

        {layout.edges.map((edge, index) => {
          const x1 = edge.from.x + edge.from.w / 2
          const y1 = edge.from.y + edge.from.h / 2
          const x2 = edge.to.x + edge.to.w / 2
          const y2 = edge.to.y + edge.to.h / 2
          const mx = (x1 + x2) / 2
          return (
            <path
              key={index}
              d={`M ${x1} ${y1} Q ${mx} ${y1} ${x2} ${y2}`}
              fill="none"
              stroke="var(--border-strong)"
              strokeWidth={1.4}
              markerEnd="url(#arrow)"
              opacity={0.75}
            />
          )
        })}

        {layout.positions.map((node) => (
          <g key={`${node.table.schema}.${node.table.name}`}>
            <rect
              x={node.x}
              y={node.y}
              width={node.w}
              height={node.h}
              rx={10}
              fill="var(--panel)"
              stroke="var(--border-strong)"
              strokeWidth={1}
            />
            <text
              x={node.x + 14}
              y={node.y + 26}
              fill="var(--text-strong)"
              fontSize={13}
              fontWeight={700}
              fontFamily="Inter, sans-serif"
            >
              {node.table.name}
            </text>
            {node.table.columns.slice(0, 6).map((column, i) => (
              <text
                key={column.name}
                x={node.x + 14}
                y={node.y + 46 + i * 20}
                fill="var(--text-dim)"
                fontSize={11}
                fontFamily="'JetBrains Mono', monospace"
              >
                {column.is_primary_key ? '◆ ' : column.is_foreign_key ? '→ ' : '  '}
                {column.name}
              </text>
            ))}
          </g>
        ))}
      </svg>
    </div>
  )
}

function relativeTime(iso: string): string {
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}
