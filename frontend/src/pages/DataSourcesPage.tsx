/**
 * Data sources: the connections DataMind may read, and everything known about
 * each one.
 *
 * Master–detail, on the frame in `components/settings.tsx` that LLM providers
 * also uses — the two pages are meant to be the same screen with different
 * fields, and sharing the furniture is what keeps them from quietly drifting
 * apart.
 *
 * **The detail is four tabs, and only the first owns a form.** `Settings ·
 * Schema · Semantic layer · Knowledge` — Schema saves nothing, and Semantic
 * layer and Knowledge save themselves, so the header's Test and Save buttons
 * appear on Settings alone. Leaving them up on every tab offered to save a
 * form the reader could not see. The Knowledge tab carries no count either:
 * its badge is *only* the number of things needing a human, which is not known
 * until the tab has loaded, and a badge that always shows a total is
 * decoration rather than a signal.
 *
 * Two things here are load-bearing and are not obvious from the layout:
 *
 *  - **Test probes the form, not the saved row.** While creating, or whenever
 *    the form is dirty, `test()` posts the typed values to `testDraft` and
 *    persists nothing — credentials can be checked without leaving a broken
 *    row behind, and a clean row's test records its status instead. The
 *    verdict says whether the role is genuinely read-only, because that is the
 *    backstop the whole guard is written against.
 *  - **Sync is not a refresh button.** The snapshot it writes is the guard's
 *    source of truth: every table and column in a generated statement is
 *    resolved against it, so an unsynced connection can answer nothing at all.
 *    Since migration `0012` the snapshot also carries `catalog_meta` — the
 *    descriptions the target database's own catalog holds — which is why the
 *    table list renders comments beside names and the search matches them.
 *
 * The schema tab has two views of one snapshot, `tables` and `graph`; the FK
 * graph is the reason schema sync records foreign keys at all.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMatch, useNavigate } from 'react-router-dom'
import { connections as api } from '../api/client'
import { useQueue, useUnsavedWork } from '../shell'
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
import { ListScrim, ListToggle, useListDrawer } from '../components/list-drawer'
import { forConnection } from '../components/knowledge-queue'
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
  conflict_checks_enabled: true,
  // Off, and the default is a measurement rather than caution: this is the one
  // switch in the learning loop that can lower accuracy, and off renders the
  // prompt every recorded baseline was taken on.
  knowledge_examples_enabled: false,
}

/**
 * The detail pane's tabs, and the segment each one is written as.
 *
 * `settings` was one tab holding two unrelated things: the credentials that
 * reach the database, and the rules about what may be sent to a model. They
 * are edited by different people on different days — nobody rotates a
 * password because the disclosure policy changed — and one Save button over
 * both meant every policy change opened a form containing a password field.
 * So they are two tabs with two Save buttons, and `Test connection` follows
 * the half it actually probes.
 *
 * `/sources/:id/settings` is not redirected: an unknown segment already reads
 * as the pane's front door, which now is Connection — the half that old link
 * most likely meant.
 */
const TABS = ['connection', 'policy', 'schema', 'semantic', 'knowledge'] as const
type Tab = (typeof TABS)[number]

/**
 * Which draft fields belong to Policy. Everything else is Connection.
 *
 * Named as the *smaller* half deliberately. A field added to `BLANK` later
 * and forgotten here lands on Connection, where it is visible and saveable;
 * listing the connection fields instead would leave a forgotten field on
 * neither tab and saveable from nowhere. That is the same failure a
 * hand-written list produced once already, when `clarify_enabled` was missing
 * from the dirty check.
 */
const POLICY_KEYS = new Set([
  'disclosure_policy',
  'clarify_enabled',
  'include_db_comments',
  'knowledge_examples_enabled',
  'conflict_checks_enabled',
  'max_rows',
  'statement_timeout_ms',
])

export default function DataSourcesPage() {
  const [list, setList] = useState<Connection[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  // Which connection, and which of its tabs — both in the URL, so a colleague
  // asking "where do I set the disclosure policy?" can be sent the tab rather
  // than told how to reach it. `/sources/new` is the create form: it is a
  // state of this screen like any other, and it has an address like any other.
  const navigate = useNavigate()
  // Below 700px the index is an overlay; above it this does nothing.
  const listDrawer = useListDrawer()
  const withTab = useMatch('/sources/:id/:tab')
  const plain = useMatch('/sources/:id')
  const routeId = withTab?.params.id ?? plain?.params.id ?? null
  const creating = routeId === 'new'
  const selectedId = creating ? null : routeId
  const setSelectedId = useCallback(
    (id: string | null, { replace = false } = {}) =>
      navigate(id ? `/sources/${id}` : '/sources', { replace }),
    [navigate],
  )
  // An unknown tab is not an error worth a screen — it reads as the page's
  // front door, which is what a mistyped or renamed segment most likely meant.
  const tab: Tab = TABS.includes(withTab?.params.tab as Tab)
    ? (withTab!.params.tab as Tab)
    : 'connection'
  const setTab = useCallback(
    // `knowledge` is the one tab that leaves. The console it used to render
    // here is the console `/knowledge/:id` renders — the same component, the
    // same connection, the same everything — and two addresses for one screen
    // is a question ("which one do I use?") the reader has to answer on every
    // visit for no benefit. It stays in the strip because this is where people
    // look for a connection's store; it just goes to the one that exists.
    (next: Tab) =>
      navigate(next === 'knowledge' ? `/knowledge/${routeId}` : `/sources/${routeId}/${next}`),
    [navigate, routeId],
  )
  const [draft, setDraft] = useState<Record<string, any>>(BLANK)
  const [password, setPassword] = useState('')
  const [schema, setSchema] = useState<SchemaSnapshot | null>(null)
  const [schemaView, setSchemaView] = useState<'tables' | 'graph'>('tables')
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<TestResult | null>(null)
  const [syncing, setSyncing] = useState(false)
  // Which Save is in flight, not merely whether one is: two tabs have two
  // buttons, and a boolean would spin both.
  const [saving, setSaving] = useState<'connection' | 'policy' | null>(null)
  const { rows: queue } = useQueue()
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

  // *Which* fields differ from the saved row — the raw material both Save
  // buttons are derived from, and broader than `isDirty` above, which must
  // stay that way: that one answers "would a probe behave differently?" and
  // deliberately ignores the row cap and the disclosure policy.
  //
  // Derived from the draft's own keys rather than a written-out list, because
  // a hand-maintained one silently omits every field added after it — the
  // reason `clarify_enabled` would have been missed here.
  const changed = useMemo(() => {
    if (creating || !selected) return new Set<string>()
    const saved = selected as unknown as Record<string, unknown>
    return new Set(
      Object.keys(draft).filter((key) => {
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
      }),
    )
  }, [creating, selected, draft])

  // One question per Save button. A typed password counts as a connection
  // change, since a blank field means "keep the stored one" — see test().
  const connectionChanges =
    creating
    || password !== ''
    || [...changed].some((key) => !POLICY_KEYS.has(key))
  const policyChanges = [...changed].some((key) => POLICY_KEYS.has(key))

  // What the navigation guard stops for. A brand-new form counts only once
  // something has been typed into it: `hasChanges` is true from the moment
  // Add is clicked (there is nothing saved to differ from), and asking someone
  // to confirm the loss of a form they have not filled in is how a guard
  // becomes a thing people click through without reading.
  const touchedNew = useMemo(
    () =>
      creating
      && (password !== ''
        || Object.keys(BLANK).some(
          (key) => JSON.stringify(draft[key]) !== JSON.stringify((BLANK as Record<string, unknown>)[key]),
        )),
    [creating, draft, password],
  )
  // Two registrations, because there are two forms — and both are scoped to
  // this record's own address, so moving between its tabs is not stopped. The
  // draft outlives a tab switch (it is keyed on the connection, not the tab),
  // so there is nothing to lose in that direction, and a dialog that fires
  // when nothing is at stake is one people learn to dismiss unread.
  const recordPath = creating ? '/sources/new' : selected ? `/sources/${selected.id}` : undefined
  const named = selected?.name ?? 'this connection'
  const releaseConnection = useUnsavedWork(
    'connection-identity',
    creating
      ? (touchedNew ? 'This new connection has not been saved yet.' : null)
      : (connectionChanges
        ? `The connection details for “${named}” have not been saved.`
        : null),
    recordPath,
  )
  const releasePolicy = useUnsavedWork(
    'connection-policy',
    !creating && policyChanges
      ? `The policy for “${named}” has not been saved.`
      : null,
    recordPath,
  )
  const releaseUnsaved = useCallback(() => {
    releaseConnection()
    releasePolicy()
  }, [releaseConnection, releasePolicy])

  // A typed or bookmarked `/sources/:id/knowledge` is not an error — it is
  // where this screen used to be. Replace, so Back does not bounce between
  // the two addresses.
  useEffect(() => {
    if (!creating && tab === 'knowledge' && selectedId) {
      navigate(`/knowledge/${selectedId}`, { replace: true })
    }
  }, [creating, tab, selectedId, navigate])

  const refresh = useCallback(async () => {
    const items = await api.list()
    setList(items)
    // Landing on `/sources` opens the first connection, which is what this
    // screen has always done — as a redirect now, so the address bar says
    // which one is open and a refresh returns to it.
    if (!routeId && items.length > 0) setSelectedId(items[0].id, { replace: true })
    return items
  }, [routeId, setSelectedId])

  useEffect(() => {
    refresh()
      .catch(() => setError('Could not load your data sources.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!selected) return
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
      conflict_checks_enabled: selected.conflict_checks_enabled,
      knowledge_examples_enabled: selected.knowledge_examples_enabled,
    })
    api
      .schema(selected.id)
      .then(setSchema)
      .catch(() => setSchema(null))
    // Keyed on the **loaded row's** id, not on the id in the URL.
    //
    // Those differ for exactly one arrival, and it is the one routing made
    // possible: a deep link. Typing, bookmarking or refreshing
    // `/{route}/:id` sets the id from the URL at mount, while the list it
    // has to be looked up in is still in flight — so an effect keyed on the
    // URL fired once against a `selected` of `null`, bailed, and never ran
    // again. The form stayed on `BLANK`, showing a blank record, reporting
    // unsaved changes in every field, and arming the navigation guard against
    // edits nobody made. Keyed on the row, it hydrates when the row arrives.
    //
    // Still the *id* rather than the row itself: `selected` is a fresh object
    // on every list refresh, and depending on it would re-hydrate the form —
    // discarding what the user had typed — every time a save reloaded the list.
  }, [selected?.id])

  function startCreate() {
    setSchema(null)
    setDraft(BLANK)
    setPassword('')
    setTestResult(null)
    setError(null)
    navigate('/sources/new')
  }

  /**
   * Save one tab's half of the form, and only that half.
   *
   * The payload is filtered rather than sent whole. Sending everything would
   * work — the fields are identical to what is on screen — but it would make
   * the Policy tab's Save capable of writing a host, which is the coupling
   * this split exists to remove: two people editing two tabs of one
   * connection must not overwrite each other with values neither of them
   * looked at.
   */
  async function save(group: 'connection' | 'policy') {
    setSaving(group)
    setError(null)
    try {
      if (creating) {
        const created = await api.create({ ...draft, password })
        await refresh()
        // Let go before navigating: the form has just been saved, and the
        // guard would otherwise stop this page from leaving itself.
        releaseUnsaved()
        // Replace: `/sources/new` describes a form that no longer exists.
        setSelectedId(created.id, { replace: true })
      } else if (selected) {
        const payload: Record<string, unknown> = {}
        for (const key of Object.keys(draft)) {
          if (POLICY_KEYS.has(key) === (group === 'policy')) payload[key] = draft[key]
        }
        if (group === 'connection' && password) payload.password = password
        await api.update(selected.id, payload)
        await refresh()
        if (group === 'connection') setPassword('')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save this connection.')
    } finally {
      setSaving(null)
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
    // Only once the row is actually gone: a refused delete leaves the form,
    // and its edits, exactly where they were.
    releaseUnsaved()
    setSchema(null)
    setError(null)
    const items = await api.list()
    setList(items)
    // Replace: the deleted connection's address is not somewhere Back should
    // be able to return to.
    setSelectedId(items[0]?.id ?? null, { replace: true })
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

  /**
   * The Policy tab's fields — and the tail of the create form.
   *
   * One definition rendered in two places rather than two copies. A new
   * connection is one form with one Save (there is no record yet for a tab to
   * belong to), so the policy travels with the create form and moves onto its
   * own tab the moment the row exists. Two copies of six governance switches
   * is how two forms start disagreeing about what the defaults are.
   *
   * Two cards, because "Safety & limits" was two subjects under one heading:
   * what leaves this database for a model, and what a query is allowed to
   * spend. The first is a disclosure decision somebody may have to defend to
   * their own security team; the second is a resource dial. Reading them as
   * one list is what let the row cap sit beside the sentence about sending
   * your DBA's comments to a third party.
   */
  const policyFields = (
    <>
      <Section
        title="What the model may see"
        description="Everything here decides what leaves your database for the model provider."
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

        <Field
          label="Taught questions as examples"
          hint="Shows the model up to four questions this connection has already been taught, after the schema. Off is exactly the prompt every recorded accuracy measurement was taken on — leave it off until you have measured that turning it on helps."
        >
          <Toggle
            checked={draft.knowledge_examples_enabled === true}
            onChange={(next) =>
              setDraft({ ...draft, knowledge_examples_enabled: next })
            }
            label={
              draft.knowledge_examples_enabled === true
                ? 'Show taught questions to the model'
                : 'Keep them out of prompts'
            }
          />
        </Field>
      </Section>

      <Section
        title="Answering & limits"
        description="Applied to every query DataMind runs on this connection."
        icon={<Icon.Sliders size={14} />}
      >
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
          label="Conflict checks"
          hint="On a schedule, runs pairs of near-duplicate templates and compares the answers, so two templates that disagree are found before somebody quotes one of them. Read-only, row-capped, and the only thing here that queries your database without being asked."
        >
          <Toggle
            checked={draft.conflict_checks_enabled !== false}
            onChange={(next) =>
              setDraft({ ...draft, conflict_checks_enabled: next })
            }
            label={
              draft.conflict_checks_enabled !== false
                ? 'Check the store on a schedule'
                : 'Only when somebody asks'
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
    </>
  )

  return (
    <div style={{ display: 'flex', height: '100%', width: '100%', minWidth: 0 }}>
      <ListScrim open={listDrawer.open} onClick={listDrawer.close} />
      <MasterColumn
        title="Data sources"
        open={listDrawer.open}
        icon={<Icon.Database size={15} />}
        note="Yours only — connections are not shared with your team."
        count={list.length}
        loading={loading}
        query={filter}
        onQuery={setFilter}
        onNew={startCreate}
        newLabel="Add a connection"
        // Not "no data sources yet": the list is per-account, so a colleague's
        // connection is genuinely absent here rather than missing, and a
        // second person's first visit should read as an empty list rather
        // than as a database that failed to appear.
        empty="You have not added a connection yet. Connections belong to the account that made them, so a colleague's will not show up here."
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
              // Carrying the open tab across: someone comparing two schemas
              // (or two policies) is switching the connection, not asking to
              // start again at Connection.
              onClick={() =>
                navigate(`/sources/${connection.id}${tab === 'connection' ? '' : `/${tab}`}`)
              }
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
              body="DataMind reads your schema over a read-only role, writes SQL against only what it finds there, and shows you every query it ran. The connection is yours — each person adds their own."
              action={<PrimaryButton onClick={startCreate}>Add a connection</PrimaryButton>}
            />
          </div>
        ) : (
          <>
            <DetailHeader
              leading={
                <ListToggle open={listDrawer.open} label="Data sources" onClick={listDrawer.toggle} />
              }
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
                // Each Save belongs to the tab whose fields it writes. Schema
                // and Semantic layer save themselves; leaving buttons up on
                // those tabs offered to save a form the reader could not see.
                //
                // Test rides with Connection alone, and always has probed only
                // connectivity fields — it was simply parked on a tab that also
                // held the disclosure policy.
                creating || tab === 'connection' ? (
                  <>
                    {!creating && connectionChanges && <UnsavedNote />}
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
                      onClick={() => save('connection')}
                      disabled={saving !== null || !connectionChanges}
                      title={connectionChanges ? undefined : 'No changes to save.'}
                    >
                      {saving === 'connection' && <Spinner />}
                      {creating ? 'Add connection' : 'Save connection'}
                    </PrimaryButton>
                  </>
                ) : tab === 'policy' ? (
                  <>
                    {policyChanges && <UnsavedNote />}
                    <PrimaryButton
                      onClick={() => save('policy')}
                      disabled={saving !== null || !policyChanges}
                      title={policyChanges ? undefined : 'No changes to save.'}
                    >
                      {saving === 'policy' && <Spinner />}
                      Save policy
                    </PrimaryButton>
                  </>
                ) : undefined
              }
            />

            {!creating && (
              <Tabs
                value={tab}
                onChange={(v) =>
                  setTab(v as Tab)
                }
                items={[
                  { value: 'connection', label: 'Connection' },
                  { value: 'policy', label: 'Policy' },
                  { value: 'schema', label: 'Schema', count: schema?.tables.length },
                  { value: 'semantic', label: 'Semantic layer' },
                  // It has a count now, and the objection that kept it off
                  // is answered rather than overruled: the number was not
                  // known until the tab had loaded, so a badge would have
                  // appeared a second late or shown a total instead of a
                  // signal. The shell counts the queue for the rail already,
                  // so this is the same number, known before the tab opens,
                  // and still absent rather than zero when there is no work.
                  {
                    value: 'knowledge',
                    label: 'Knowledge',
                    count: selected ? forConnection(queue, selected.id) : undefined,
                    // Marked as leaving, because it does: a tab that quietly
                    // navigates out of its own strip is worse than one that
                    // says it will.
                    leaves: true,
                  },
                ]}
              />
            )}

            {(creating || tab === 'connection') && (
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

                {/* A new row is one form with one Save: the policy has no tab
                    of its own until the record exists. */}
                {creating && policyFields}

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

            {!creating && tab === 'policy' && (
              <DetailBody>
                {error && <ErrorNote>{error}</ErrorNote>}
                {policyFields}
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

            {/* No `tab === 'knowledge'` branch: the effect above has already
                sent this address to `/knowledge/:id`, which is where the
                console lives. */}
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

