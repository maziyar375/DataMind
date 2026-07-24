import { useCallback, useEffect, useMemo, useState } from 'react'
import { connections as api } from '../api/client'
import type { Connection, SchemaSnapshot, SchemaTable, TestResult } from '../api/types'
import {
  Chip, DangerButton, EmptyState, ErrorNote, Field, GhostButton, Icon,
  PrimaryButton, Select, Spinner, TextInput,
} from '../components/ui'
import {
  DetailBody, DetailHeader, FieldRow, MasterColumn, MasterItem, Section,
  StatusLine, Tabs,
} from '../components/settings'
import { DATABASE_TYPES } from '../theme/tokens'

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
  const [tab, setTab] = useState<'settings' | 'schema'>('settings')
  const [schemaView, setSchemaView] = useState<'tables' | 'graph'>('tables')
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

  // True when the form holds edits to a saved connection that affect the
  // probe. A new password counts, since a blank field means "keep the stored
  // one" — see test(). Non-connectivity fields (row cap, disclosure) don't
  // change what a probe does, so they are left out.
  const isDirty = useMemo(() => {
    if (!selected) return false
    return (
      password !== '' ||
      draft.database_type !== selected.database_type ||
      draft.host !== selected.host ||
      Number(draft.port) !== selected.port ||
      draft.database_name !== selected.database_name ||
      draft.username !== selected.username ||
      (draft.ssl_mode ?? null) !== (selected.ssl_mode ?? null)
    )
  }, [selected, draft, password])

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
    setError(null)
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
    setError(null)
    setTab('settings')
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
    setTesting(true)
    setTestResult(null)
    try {
      if (creating || (selected && isDirty)) {
        // Probe the form values, not the saved row. `connection_id` (absent
        // while creating) lets the backend reuse the stored password if none
        // was typed. This never persists, since the form may differ from what
        // is saved.
        setTestResult(
          await api.testDraft({
            connection_id: selected?.id,
            database_type: draft.database_type,
            host: draft.host,
            port: draft.port,
            database_name: draft.database_name,
            username: draft.username,
            password: password || undefined,
            ssl_mode: draft.ssl_mode,
          }),
        )
      } else if (selected) {
        // No unsaved edits: test the stored row, which records its status.
        setTestResult(await api.test(selected.id))
        await refresh()
      }
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

  const editing = creating || !!selected

  const engine =
    DATABASE_TYPES.find((t) => t.value === draft.database_type) ?? DATABASE_TYPES[0]

  /** Switching engine carries the previous engine's port, which is never right. */
  function changeEngine(value: string) {
    const next = DATABASE_TYPES.find((t) => t.value === value)
    setDraft({
      ...draft,
      database_type: value,
      port: next ? next.port : draft.port,
    })
  }

  // Everything the probe needs must be on the form before Test can mean
  // anything. A new connection has no stored password to fall back on, so the
  // password is required too; an edit can reuse the saved one, so it is not.
  const hasConnFields = Boolean(
    draft.host && draft.port && draft.database_name && draft.username,
  )
  const canTest = creating
    ? hasConnFields && Boolean(password)
    : isDirty
      ? hasConnFields
      : true

  return (
    <div style={{ display: 'flex', height: '100%', width: '100%', minWidth: 0 }}>
      <MasterColumn
        title="Data sources"
        count={list.length}
        onNew={startCreate}
        newLabel="Add a connection"
        empty="No data sources yet. Add one to start asking questions."
      >
        {list.map((connection) => (
          <MasterItem
            key={connection.id}
            title={connection.name}
            subtitle={`${engineLabel(connection.database_type)} · ${connection.host}:${connection.port}`}
            active={connection.id === selectedId}
            tone={
              connection.status === 'OK'
                ? 'green'
                : connection.status === 'ERROR'
                  ? 'red'
                  : 'neutral'
            }
            isDefault={connection.is_default}
            onClick={() => setSelectedId(connection.id)}
          />
        ))}
      </MasterColumn>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        {!editing ? (
          <EmptyState
            title="Connect a database"
            body="DataMind reads your schema over a read-only role, writes SQL against only what it finds there, and shows you every query it ran."
            action={<PrimaryButton onClick={startCreate}>Add a connection</PrimaryButton>}
          />
        ) : (
          <>
            <DetailHeader
              title={creating ? 'New connection' : selected!.name}
              subtitle={`${engine.label} · ${draft.host}:${draft.port}/${draft.database_name || '—'}`}
              chips={
                creating ? undefined : (
                  <>
                    <Chip tone={selected!.status === 'OK' ? 'green' : selected!.status === 'ERROR' ? 'red' : 'neutral'}>
                      {selected!.status === 'OK'
                        ? 'reachable'
                        : selected!.status === 'ERROR'
                          ? 'unreachable'
                          : 'untested'}
                    </Chip>
                    <Chip tone={selected!.readonly_confirmed ? 'green' : 'amber'}>
                      {selected!.readonly_confirmed ? 'read-only confirmed' : 'role can write'}
                    </Chip>
                    {selected!.is_default && <Chip tone="accent">default</Chip>}
                    <Chip>
                      {selected!.last_synced_at
                        ? `synced ${relativeTime(selected!.last_synced_at)}`
                        : 'never synced'}
                    </Chip>
                  </>
                )
              }
              actions={
                <>
                  {!creating && selected && !selected.is_default && (
                    <GhostButton
                      onClick={async () => {
                        await api.update(selected.id, { is_default: true })
                        await refresh()
                      }}
                    >
                      Set as default
                    </GhostButton>
                  )}
                  <GhostButton
                    onClick={test}
                    disabled={testing || !canTest}
                    title={
                      canTest
                        ? undefined
                        : 'Fill in host, database, user, and password first.'
                    }
                  >
                    {testing && <Spinner />}
                    Test connection
                  </GhostButton>
                  <PrimaryButton onClick={save} disabled={saving}>
                    {saving && <Spinner />}
                    {creating ? 'Add connection' : 'Save changes'}
                  </PrimaryButton>
                </>
              }
            />

            {!creating && (
              <Tabs
                value={tab}
                onChange={(v) => setTab(v as 'settings' | 'schema')}
                items={[
                  { value: 'settings', label: 'Settings' },
                  { value: 'schema', label: 'Schema', count: schema?.tables.length },
                ]}
              />
            )}

            {(creating || tab === 'settings') && (
              <DetailBody>
                {error && <ErrorNote>{error}</ErrorNote>}
                {testResult && (
                  <StatusLine ok={testResult.ok}>
                    {testResult.ok
                      ? `Connected · ${
                          testResult.readonly_confirmed
                            ? 'read-only role confirmed'
                            : 'this role can write — use a read-only role'
                        } · ${testResult.latency_ms}ms`
                      : testResult.message}
                  </StatusLine>
                )}

                <Section
                  title="Connection"
                  description="Point DataMind at the database. Use a role with read-only rights."
                >
                  <FieldRow>
                    <Field label="Name">
                      <TextInput
                        value={draft.name}
                        onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                      />
                    </Field>
                    <Field label="Engine">
                      <Select
                        value={draft.database_type}
                        onChange={(e) => changeEngine(e.target.value)}
                      >
                        {DATABASE_TYPES.map((type) => (
                          <option key={type.value} value={type.value}>
                            {type.label}
                          </option>
                        ))}
                      </Select>
                    </Field>
                  </FieldRow>

                  <FieldRow columns={3}>
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
                    <Field label={engine.databaseLabel} hint={engine.databaseHint || undefined}>
                      <TextInput
                        value={draft.database_name}
                        onChange={(e) =>
                          setDraft({ ...draft, database_name: e.target.value })
                        }
                      />
                    </Field>
                  </FieldRow>

                  <FieldRow>
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
                  </FieldRow>

                  <FieldRow>
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
                    <Field
                      label="Schema allowlist"
                      hint={`Optional, comma separated. ${engine.schemaHint}`}
                    >
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
                  </FieldRow>
                </Section>

                <Section
                  title="Safety & limits"
                  description="Applied to every query DataMind runs on this connection."
                >
                  <Field
                    label="Result sharing"
                    hint="How much of a query result may be sent to the model provider."
                  >
                    <Select
                      value={draft.disclosure_policy}
                      onChange={(e) =>
                        setDraft({ ...draft, disclosure_policy: e.target.value })
                      }
                    >
                      <option value="NONE">Nothing — the model never sees result rows</option>
                      <option value="AGGREGATE">Totals only</option>
                      <option value="SAMPLE">A sample of rows</option>
                      <option value="FULL">All returned rows</option>
                    </Select>
                  </Field>

                  <FieldRow>
                    <Field label="Row limit" hint="Rows a single query may return.">
                      <TextInput
                        type="number"
                        value={draft.max_rows}
                        onChange={(e) =>
                          setDraft({ ...draft, max_rows: Number(e.target.value) })
                        }
                      />
                    </Field>
                    <Field label="Query timeout (ms)" hint="Cancelled past this budget.">
                      <TextInput
                        type="number"
                        value={draft.statement_timeout_ms}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            statement_timeout_ms: Number(e.target.value),
                          })
                        }
                      />
                    </Field>
                  </FieldRow>
                </Section>

                {!creating && (
                  <Section
                    title="Danger zone"
                    description="Conversations that used this connection keep their recorded history."
                    danger
                  >
                    <DangerButton onClick={remove} style={{ alignSelf: 'flex-start' }}>
                      <Icon.Trash />
                      Delete connection
                    </DangerButton>
                  </Section>
                )}
              </DetailBody>
            )}

            {!creating && tab === 'schema' && (
              <div
                style={{
                  flex: 1,
                  overflowY: 'auto',
                  padding: 28,
                  minHeight: 0,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 16,
                }}
              >
                {error && <ErrorNote>{error}</ErrorNote>}

                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                    flexWrap: 'wrap',
                  }}
                >
                  <GhostButton onClick={sync} disabled={syncing}>
                    {syncing && <Spinner />}
                    Re-sync schema
                  </GhostButton>
                  <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>
                    {selected!.last_synced_at
                      ? `last synced ${relativeTime(selected!.last_synced_at)}`
                      : 'never synced'}
                  </span>
                  {schema && (
                    <span style={{ display: 'flex', gap: 6, marginLeft: 'auto' }}>
                      <Chip tone="green">{schema.tables.length} tables</Chip>
                      <Chip>{schema.relationships.length} relationships</Chip>
                    </span>
                  )}
                </div>

                {!schema ? (
                  <EmptyState
                    title="No schema yet"
                    body="Sync this connection to read its tables, columns, and foreign keys. DataMind only ever writes SQL against what it finds here."
                    action={
                      <PrimaryButton onClick={sync} disabled={syncing}>
                        {syncing && <Spinner />}
                        Sync schema
                      </PrimaryButton>
                    }
                  />
                ) : (
                  <>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 10,
                        flexWrap: 'wrap',
                      }}
                    >
                      <div
                        style={{
                          display: 'flex',
                          gap: 2,
                          background: 'var(--panel-alt)',
                          borderRadius: 8,
                          padding: 3,
                        }}
                      >
                        <SegButton
                          active={schemaView === 'tables'}
                          onClick={() => setSchemaView('tables')}
                        >
                          Table list
                        </SegButton>
                        <SegButton
                          active={schemaView === 'graph'}
                          onClick={() => setSchemaView('graph')}
                        >
                          Graph view
                        </SegButton>
                      </div>
                      {schemaView === 'tables' && (
                        <TextInput
                          placeholder="Search tables & columns…"
                          value={search}
                          onChange={(e) => setSearch(e.target.value)}
                          style={{
                            width: 280,
                            marginLeft: 'auto',
                            fontSize: 13,
                            padding: '8px 11px',
                          }}
                        />
                      )}
                    </div>

                    {schemaView === 'tables' && (
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
                        {filteredTables.length === 0 && (
                          <p style={{ fontSize: 13, color: 'var(--text-dim)' }}>
                            Nothing matches “{search}”.
                          </p>
                        )}
                      </div>
                    )}

                    {schemaView === 'graph' && <GraphView schema={schema} />}
                  </>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function SegButton({
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
        padding: '6px 12px',
        borderRadius: 6,
        cursor: 'pointer',
        border: 'none',
        color: active ? 'var(--text-strong)' : 'var(--text-dim)',
        background: active ? 'var(--panel)' : 'transparent',
        boxShadow: active ? '0 1px 3px rgba(0,0,0,0.10)' : 'none',
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

function engineLabel(value: string): string {
  return DATABASE_TYPES.find((t) => t.value === value)?.label ?? value
}

function relativeTime(iso: string): string {
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}
