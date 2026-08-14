import { useCallback, useEffect, useMemo, useState } from 'react'
import { connections as api } from '../api/client'
import type { Connection, SchemaSnapshot, SchemaTable, TestResult } from '../api/types'
import {
  Chip, DangerButton, DisclosureBadge, EmptyState, ErrorNote, Field, GhostButton,
  GlyphBadge, Icon, PrimaryButton, SearchField, Segmented, Select, Spinner,
  TextInput, Toggle, engineHue, relativeTime,
} from '../components/ui'
import {
  DetailBody, DetailHeader, FieldRow, MasterColumn, MasterItem, Section,
  StatusLine, Tabs, UnsavedNote,
} from '../components/settings'
import { SemanticLayerTab } from '../components/semantic'
import { DATABASE_TYPES } from '../theme/tokens'

/** What a stored row's `status` says, in the words the chips and dots use. */
function reachability(status: string): { tone: 'green' | 'red' | 'neutral'; label: string } {
  if (status === 'OK') return { tone: 'green', label: 'Reachable' }
  if (status === 'ERROR') return { tone: 'red', label: 'Unreachable' }
  return { tone: 'neutral', label: 'Untested' }
}

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
  clarify_enabled: true,
  include_db_comments: true,
}

export default function DataSourcesPage() {
  const [list, setList] = useState<Connection[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<Record<string, any>>(BLANK)
  const [password, setPassword] = useState('')
  const [creating, setCreating] = useState(false)
  const [schema, setSchema] = useState<SchemaSnapshot | null>(null)
  const [tab, setTab] = useState<'settings' | 'schema' | 'semantic'>('settings')
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

  // True when the form differs from the saved row in *any* field — the
  // question "is there anything to save?", which is broader than `isDirty`
  // above and must stay that way: that one answers "would a probe behave
  // differently?" and deliberately ignores the row cap and disclosure policy.
  //
  // Derived from the draft's own keys rather than a written-out list, because
  // a hand-maintained one silently omits every field added after it — the
  // reason `clarify_enabled` would have been missed here.
  const hasChanges = useMemo(() => {
    if (creating) return true      // nothing saved yet to differ from
    if (!selected) return false
    if (password !== '') return true
    const saved = selected as unknown as Record<string, unknown>
    return Object.keys(draft).some((key) => {
      // The draft hydrates a null `ssl_mode` as 'require' (the Select has no
      // empty option), so compare through the same lens or a form that was
      // only just opened reads as edited.
      const a = key === 'ssl_mode' ? (draft[key] ?? 'require') : draft[key]
      const b = key === 'ssl_mode' ? (saved[key] ?? 'require') : saved[key]
      if (Array.isArray(a) || Array.isArray(b)) {
        return JSON.stringify(a ?? []) !== JSON.stringify(b ?? [])
      }
      if (typeof b === 'number') return Number(a) !== b
      return (a ?? null) !== (b ?? null)
    })
  }, [creating, selected, draft, password])

  const refresh = useCallback(async () => {
    const items = await api.list()
    setList(items)
    if (!selectedId && items.length > 0) setSelectedId(items[0].id)
    return items
  }, [selectedId])

  useEffect(() => {
    refresh()
      .catch(() => setError('Could not load your data sources.'))
      .finally(() => setLoading(false))
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
      clarify_enabled: selected.clarify_enabled,
      include_db_comments: selected.include_db_comments,
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

  /** A one-field update saved immediately, for switches that live outside the
   *  Settings form and so have no Save button of their own. */
  async function patchConnection(patch: Partial<Connection>) {
    if (!selected) return
    setList((prev) =>
      prev.map((c) => (c.id === selected.id ? { ...c, ...patch } : c)),
    )
    try {
      await api.update(selected.id, patch as Record<string, unknown>)
    } catch (err) {
      await refresh()
      setError(err instanceof Error ? err.message : 'Could not update this connection.')
    }
  }

  async function remove() {
    if (!selected) return
    // A rejected delete used to reach nobody: the promise threw into a click
    // handler, the list never refreshed, and the row simply stayed — which
    // looks identical to a delete that silently did nothing, and is exactly how
    // the `runs` constraint went unnoticed until a user reported "it doesn't
    // delete". Whatever the reason, say it.
    try {
      await api.remove(selected.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete this connection.')
      return
    }
    setSelectedId(null)
    setSchema(null)
    setError(null)
    const items = await api.list()
    setList(items)
    if (items.length > 0) setSelectedId(items[0].id)
  }

  const filteredTables = useMemo(() => {
    if (!schema) return []
    const needle = search.trim().toLowerCase()
    if (!needle) return schema.tables
    // Descriptions are searched alongside names because that is the search a
    // description makes possible: nobody names a table `cancellations`, but a
    // DBA wrote the word in a comment, and a list that shows the sentence but
    // will not match on it invites the search that silently finds nothing.
    return schema.tables.filter(
      (table) =>
        table.name.toLowerCase().includes(needle) ||
        (table.comment ?? '').toLowerCase().includes(needle) ||
        table.columns.some(
          (c) =>
            c.name.toLowerCase().includes(needle) ||
            (c.comment ?? '').toLowerCase().includes(needle),
        ),
    )
  }, [schema, search])

  // Table and column descriptions the last sync read out of the catalog. The
  // counts are stored beside the snapshot rather than derived here so this
  // stays one field read instead of a walk over every column on every render.
  const describedCount =
    (schema?.catalog_meta?.counts?.tables ?? 0) +
    (schema?.catalog_meta?.counts?.columns ?? 0)

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

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    if (!needle) return list
    return list.filter(
      (connection) =>
        connection.name.toLowerCase().includes(needle)
        || connection.host.toLowerCase().includes(needle)
        || connection.database_name.toLowerCase().includes(needle)
        || engineLabel(connection.database_type).toLowerCase().includes(needle),
    )
  }, [list, filter])

  return (
    <div style={{ display: 'flex', height: '100%', width: '100%', minWidth: 0 }}>
      <MasterColumn
        title="Data sources"
        icon={<Icon.Database size={15} />}
        count={list.length}
        loading={loading}
        query={filter}
        onQuery={setFilter}
        onNew={startCreate}
        newLabel="Add a connection"
        empty="No data sources yet. Add one to start asking questions."
      >
        {visible.map((connection) => {
          const state = reachability(connection.status)
          return (
            <MasterItem
              key={connection.id}
              title={connection.name}
              subtitle={`${engineLabel(connection.database_type)} · ${connection.host}:${connection.port}`}
              active={connection.id === selectedId}
              tone={state.tone}
              toneLabel={state.label}
              glyph={
                <GlyphBadge size={30} hue={engineHue(connection.database_type)}>
                  <Icon.Database size={15} />
                </GlyphBadge>
              }
              onClick={() => setSelectedId(connection.id)}
            />
          )
        })}
        {visible.length === 0 && list.length > 0 && (
          <p style={{ fontSize: 12.5, color: 'var(--text-dim)', padding: '4px 6px', margin: 0 }}>
            Nothing matches “{filter.trim()}”.
          </p>
        )}
      </MasterColumn>

      {/* `position: relative` (from `.rm-detail-pane`) anchors the semantic
          tab's floating save bar, which hovers over the content instead of
          eating a strip of the pane. */}
      <div
        className="rm-detail-pane"
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          minWidth: 0,
        }}
      >
        {!editing ? (
          // Centred in the pane rather than pinned to the top of it — see the
          // same note on the LLM providers page.
          <div
            className="rm-emptyfield"
            style={{ flex: 1, display: 'grid', placeItems: 'center' }}
          >
            <EmptyState
              icon={<Icon.Database size={20} />}
              title="Connect a database"
              body="DataMind reads your schema over a read-only role, writes SQL against only what it finds there, and shows you every query it ran."
              action={<PrimaryButton onClick={startCreate}>Add a connection</PrimaryButton>}
            />
          </div>
        ) : (
          <>
            <DetailHeader
              glyph={
                <GlyphBadge size={40} hue={engineHue(draft.database_type)}>
                  <Icon.Database size={19} />
                </GlyphBadge>
              }
              title={creating ? 'New connection' : selected!.name}
              subtitle={`${engine.label} · ${draft.host}:${draft.port}/${draft.database_name || '—'}`}
              chips={
                creating ? undefined : (
                  <>
                    <Chip tone={reachability(selected!.status).tone}>
                      {reachability(selected!.status).label}
                    </Chip>
                    <Chip tone={selected!.readonly_confirmed ? 'green' : 'amber'}>
                      {selected!.readonly_confirmed ? 'Read-only confirmed' : 'Role can write'}
                    </Chip>
                    {/* The same badge the chat header shows, so the policy in
                        force reads identically wherever it is stated. */}
                    <DisclosureBadge policy={selected!.disclosure_policy} />
                    <Chip>
                      {selected!.last_synced_at
                        ? `Synced ${relativeTime(selected!.last_synced_at)}`
                        : 'Never synced'}
                    </Chip>
                  </>
                )
              }
              actions={
                // Only on the tab that owns the form. Schema and Semantic
                // layer save themselves; leaving these here offered to save a
                // form the reader could not see.
                creating || tab === 'settings' ? (
                  <>
                    {!creating && hasChanges && <UnsavedNote />}
                    <GhostButton
                      onClick={test}
                      disabled={testing || !canTest}
                      title={
                        canTest
                          ? undefined
                          : 'Fill in host, database, user, and password first.'
                      }
                    >
                      {testing ? <Spinner /> : <Icon.Zap size={14} />}
                      Test connection
                    </GhostButton>
                    <PrimaryButton
                      onClick={save}
                      disabled={saving || !hasChanges}
                      title={hasChanges ? undefined : 'No changes to save.'}
                    >
                      {saving && <Spinner />}
                      {creating ? 'Add connection' : 'Save changes'}
                    </PrimaryButton>
                  </>
                ) : undefined
              }
            />

            {!creating && (
              <Tabs
                value={tab}
                onChange={(v) => setTab(v as 'settings' | 'schema' | 'semantic')}
                items={[
                  { value: 'settings', label: 'Settings' },
                  { value: 'schema', label: 'Schema', count: schema?.tables.length },
                  { value: 'semantic', label: 'Semantic layer' },
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
                  icon={<Icon.Server size={14} />}
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
                        placeholder="e.g. public, analytics"
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
                  icon={<Icon.Shield size={14} />}
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

                  <Field
                    label="Ambiguous questions"
                    hint="Only fires when a question genuinely cannot be answered without guessing; everything else runs straight through."
                  >
                    <Toggle
                      checked={draft.clarify_enabled !== false}
                      onChange={(next) =>
                        setDraft({ ...draft, clarify_enabled: next })
                      }
                      label={
                        draft.clarify_enabled !== false
                          ? 'Ask before answering'
                          : 'Always answer, never ask'
                      }
                    />
                  </Field>

                  <Field
                    label="Schema descriptions"
                    hint="Descriptions your DBA wrote in the database itself (COMMENT ON, MS_Description). They travel with the column names, under every result-sharing setting — turn this off if your comments hold anything you would not send."
                  >
                    <Toggle
                      checked={draft.include_db_comments !== false}
                      onChange={(next) =>
                        setDraft({ ...draft, include_db_comments: next })
                      }
                      label={
                        draft.include_db_comments !== false
                          ? 'Send them to the model'
                          : 'Keep them out of prompts'
                      }
                    />
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
                    icon={<Icon.Alert size={14} />}
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
              // The same frame the Settings tab uses, rather than a full-bleed
              // one of its own — three tabs of one record must not move under
              // the tab strip that switches them. See `DetailBody`.
              <DetailBody>
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
                    {syncing ? <Spinner /> : <Icon.Refresh size={14} />}
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
                      {/* The confirmation that somebody's documentation is
                          actually being read. Absent rather than "0" when a
                          database carries no comments: a zero here reads as a
                          failure, and having none is the normal case. */}
                      {describedCount > 0 && (
                        // The breakdown rides on a wrapper rather than on the
                        // Chip: `Chip` takes a deliberately narrow set of props
                        // and one caller wanting a tooltip is not a reason to
                        // widen a primitive every page uses.
                        <span
                          title={`${schema.catalog_meta?.counts?.tables ?? 0} table and ${
                            schema.catalog_meta?.counts?.columns ?? 0
                          } column descriptions read from the database's own catalog`}
                          style={{ display: 'flex' }}
                        >
                          <Chip>{describedCount} descriptions</Chip>
                        </span>
                      )}
                    </span>
                  )}
                </div>

                {!schema ? (
                  <EmptyState
                    icon={<Icon.Database size={20} />}
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
                    {/* What the database says it is, in its own words. Only
                        PostgreSQL and SQL Server carry this at all — MySQL has
                        none outside MariaDB and Oracle has neither — so it is
                        absent far more often than not, and nothing below it
                        depends on it being there. */}
                    {schema.catalog_meta?.database_comment && (
                      <div
                        dir="auto"
                        style={{
                          padding: '10px 13px',
                          borderRadius: 9,
                          background: 'var(--panel-alt)',
                          border: '1px solid var(--border)',
                          fontSize: 12.5,
                          lineHeight: 1.55,
                          color: 'var(--text2)',
                        }}
                      >
                        {schema.catalog_meta.database_comment}
                      </div>
                    )}

                    {/* The index pages' own toolbar, at the width of this pane:
                        the shared segmented control and the shared search field,
                        rather than the local copies of both that used to live
                        here and had already drifted from them. */}
                    <div className="rm-dash-toolbar" style={{ marginBottom: 0 }}>
                      <Segmented
                        ariaLabel="Schema view"
                        value={schemaView}
                        onChange={setSchemaView}
                        options={[
                          { value: 'tables', label: 'Table list' },
                          { value: 'graph', label: 'Graph view' },
                        ]}
                      />
                      {schemaView === 'tables' && (
                        <div className="rm-toolbar-group">
                          <SearchField
                            value={search}
                            onChange={setSearch}
                            ariaLabel="Search tables and columns"
                            placeholder={`Search ${schema.tables.length} tables…`}
                          />
                        </div>
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
                          search.trim() ? (
                            <EmptyState
                              icon={<Icon.Search size={20} />}
                              title="Nothing matches"
                              body={`No table or column in this schema is called “${search.trim()}”.`}
                              action={<GhostButton onClick={() => setSearch('')}>Clear search</GhostButton>}
                            />
                          ) : (
                            <EmptyState
                              icon={<Icon.Database size={20} />}
                              title="No tables"
                              body="This snapshot holds no tables. Re-sync once the role can see them."
                            />
                          )
                        )}
                      </div>
                    )}

                    {schemaView === 'graph' && <GraphView schema={schema} />}
                  </>
                )}
              </DetailBody>
            )}

            {!creating && tab === 'semantic' && (
              <SemanticLayerTab
                key={selected!.id}
                connection={selected!}
                onConnectionChange={patchConnection}
              />
            )}
          </>
        )}
      </div>
    </div>
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
      className="rm-table-card"
      style={{
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
          transition: 'background .12s ease',
        }}
      >
        <Icon.Chevron open={open} size={13} stroke="var(--text-dim)" />
        <span
          className="mono"
          style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-strong)' }}
        >
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

      {/* Shown collapsed, because "which of these 42 tables did I mean?" is the
          question the description answers and the collapsed list is where it is
          asked. One line, clipped — the full text is the `title`, and the whole
          sentence is in the semantic layer editor if it matters. `dir="auto"`
          for the same reason every free-text field in this app has it: a
          Persian comment laid out left-to-right reads with its clauses in the
          wrong order. */}
      {table.comment && (
        <div
          dir="auto"
          title={table.comment}
          style={{
            padding: '0 14px 10px 37px',
            marginTop: -4,
            fontSize: 11.5,
            lineHeight: 1.45,
            color: 'var(--text-dim)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {table.comment}
        </div>
      )}

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
              {column.comment && (
                <span
                  dir="auto"
                  title={column.comment}
                  style={{
                    fontSize: 11.5,
                    color: 'var(--text-dim)',
                    // `minWidth: 0` or the flex item refuses to shrink below its
                    // content and pushes the key chips off the row instead of
                    // clipping itself.
                    minWidth: 0,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {column.comment}
                </span>
              )}
              <span
                style={{
                  marginLeft: 'auto',
                  display: 'flex',
                  gap: 6,
                  flexShrink: 0,
                  paddingLeft: 8,
                }}
              >
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
                fontFamily="'JetBrains Mono', Vazirmatn, monospace"
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

