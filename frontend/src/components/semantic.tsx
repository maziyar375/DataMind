/**
 * The semantic layer editor.
 *
 * Two jobs, and the layout follows from the tension between them:
 *
 *  - **Generate.** A model describes the schema, table by table, over minutes.
 *    The two decisions that cost money — which model, how much of the schema —
 *    are made on picker *cards*, not in a dropdown: choosing a model is the
 *    single biggest lever on how good the result is, and a `<select>` hides
 *    exactly the things you choose on (which model id, has it been reached).
 *  - **Edit.** What the model wrote is a draft. Everything is editable, an
 *    edit is marked so a later regeneration cannot silently overwrite it, and
 *    `Reviewed` is a deliberate act — the layer's authority over the SQL
 *    generator should be something a person granted, not something a model
 *    assumed.
 *
 * Layout rules worth keeping: content is capped at a readable width rather
 * than stretched across the pane; the search and filter bar sticks, because a
 * 42-table schema scrolls past it in a second; and destructive actions live in
 * an overflow menu behind a confirmation, never as a red panel parked in the
 * middle of an editing flow.
 *
 * Validation is never guessed at locally: metric expressions are checked by
 * the same backend parser that will reject them at save time.
 *
 * **A save is a draft; a publish is a version.** Save writes the draft, which
 * no question reads, and *Review and publish* (`semantic-publish.tsx`) is the
 * act that makes it what the model reads — with a note when a change moves a
 * number. A generation and a restore land in the same draft. Every write names
 * the revision it was edited from and is refused — not merged — when somebody
 * wrote in between; the refusal says who, beside the button, and keeps the
 * edits in the tab. What changed is always the server's answer (the layer's
 * `unpublished_changes`, and `POST …/semantic/diff` for the edits a conflict
 * displaced), the same differ the History tab reads (`semantic-history.tsx`).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMatch, useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError, access, semantic as api } from '../api/client'
import type {
  Actions, Connection, ProblemDetail, SemanticAttention, SemanticChange,
  SemanticDocument, SemanticEntity, SemanticGenerationMode, SemanticJob,
  SemanticLayer, SemanticMetricUse,
} from '../api/types'
import {
  Chip, DangerButton, ErrorNote, Field, GhostButton, Icon, Modal, PrimaryButton,
  ProgressBar, Select, Spinner, TextArea, TextInput, Toggle, relativeTime,
} from './ui'
import { AccessPopover } from './access'
import { DetailBody, FieldRow } from './settings'
import { useBackgroundWatch } from '../shell'
import { explainRekey, rekeyDrift } from './semantic-drift'
import { SemanticHistory } from './semantic-history'
import { ChangeList, PublishDialog } from './semantic-publish'
import { ExportDialog, ImportDialog } from './semantic-transfer'
import { authorship, historyPath, unpublishedWords } from './semantic-changes'
import {
  attentionSection, attentionView, undescribedWords, type AttentionLine,
  type AttentionRow, type AttentionTone, type AttentionView,
} from './semantic-attention'
import { collectMetrics, matchesMetric, metricSummary, type MetricRow } from './semantic-metrics'
import { IconButton, Note, Panel, PillTabs, blankMetric, hasIssue } from './semantic-parts'
import { EntityCard, Glossary, type Section, entityDomId } from './semantic-entity'
import { ChoiceRow, GenerateModal } from './semantic-generate'

const ACTIVE = ['QUEUED', 'RUNNING']

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

type Filter = 'all' | 'review' | 'metrics' | 'issues' | 'attention'

/** Shown in place of the stats strip before anything has been generated —
 *  three concrete examples beat a paragraph about "semantics". */
const WHAT_IT_HOLDS = [
  {
    title: 'Grain',
    body: '“One row per line item on an order” — what stops a join from double-counting.',
  },
  {
    title: 'Metrics',
    body: 'revenue = SUM(quantity × unit_price), excluding cancelled orders. Bound to real SQL.',
  },
  {
    title: 'Time',
    body: 'Whether “last month” means the calendar month or a rolling 30 days.',
  },
]

export function SemanticLayerTab({
  connection, onConnectionChange,
}: {
  connection: Connection
  onConnectionChange: (patch: Partial<Connection>) => void
}) {
  const [layer, setLayer] = useState<SemanticLayer | null>(null)
  const [doc, setDoc] = useState<SemanticDocument | null>(null)
  // The server document the edits in this tab were made from, and its
  // revision. Moved only when a server document is *adopted* — never by a
  // reload that kept local edits — because the revision a save presents has
  // to be the one those edits were made against, or a generation that landed
  // mid-edit would be overwritten by the next Save without a word.
  const [base, setBase] = useState<{ json: string; revision: number }>({ json: '', revision: 0 })
  const [job, setJob] = useState<SemanticJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState<ProblemDetail | null>(null)
  // The edits a conflict reload displaced — the server's change list, never a
  // local comparison.
  const [displaced, setDisplaced] = useState<SemanticChange[] | null>(null)
  const [publishing, setPublishing] = useState(false)
  const [askDiscardDraft, setAskDiscardDraft] = useState(false)
  const [askExport, setAskExport] = useState(false)
  const [askImport, setAskImport] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  // The generate dialog, and the tables it opens with already chosen — set when
  // a *Needs attention* row asks for a table's gaps to be filled.
  const [generateFor, setGenerateFor] = useState<{ tables?: string[] } | null>(null)
  const [attention, setAttention] = useState<SemanticAttention | null>(null)
  const [attentionFailed, setAttentionFailed] = useState(false)
  const [askDelete, setAskDelete] = useState(false)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const watch = useBackgroundWatch()
  const navigate = useNavigate()
  // What this reader may do to the **layer** — a separate grant from the data
  // source's. A Data Engineer curates it without the credential; somebody it
  // was shared with for viewing reads it and changes nothing. Until the answer
  // arrives nothing that writes is drawn, rather than drawn and taken away.
  const [layerAccess, setLayerAccess] = useState<Actions | null>(null)
  useEffect(() => {
    let cancelled = false
    access.actions(`connections/${connection.id}/semantic`)
      .then((next) => !cancelled && setLayerAccess(next))
      .catch(() => !cancelled && setLayerAccess(null))
    return () => {
      cancelled = true
    }
  }, [connection.id])
  const readOnly = !(layerAccess?.can.edit ?? false)
  // The on/off switch is a field of the *data source*, so it needs edit
  // access there — which a layer curator may well not have.
  const mayToggle = (connection.privileges ?? []).includes('modify')

  // History is three sub-routes of this tab, read here rather than nested, so
  // the editor — and anything unsaved in it — stays mounted while somebody
  // looks back.
  const historyVersion = useMatch('/sources/:id/semantic/history/:version')
  const historyList = useMatch('/sources/:id/semantic/history')
  const [query, setQuery] = useSearchParams()
  const inHistory = historyVersion !== null || historyList !== null
  const shownVersion = historyVersion ? Number(historyVersion.params.version) : null

  const dirty = doc !== null && JSON.stringify(doc) !== base.json

  // Read inside `load` without making it depend on `base` or `doc`, which
  // would rebuild the callback on every keystroke.
  const baseRef = useRef(base)
  const docRef = useRef(doc)
  useEffect(() => {
    baseRef.current = base
    docRef.current = doc
  }, [base, doc])

  const adopt = useCallback((next: SemanticLayer) => {
    setLayer(next)
    setDoc(next.document)
    setBase({ json: JSON.stringify(next.document), revision: next.revision })
  }, [])

  const load = useCallback(async () => {
    const next = await api.get(connection.id)
    setJob(next.job)
    setLayer(next)
    // A reload mid-edit would silently discard what the user has typed, so
    // the document is only adopted when there is nothing unsaved to lose —
    // and the base revision stays where those edits were made.
    const current = docRef.current
    if (current !== null && JSON.stringify(current) !== baseRef.current.json) return next
    const adopted = { json: JSON.stringify(next.document), revision: next.revision }
    baseRef.current = adopted
    docRef.current = next.document
    setBase(adopted)
    setDoc(next.document)
    return next
  }, [connection.id])

  useEffect(() => {
    setLoading(true)
    setDoc(null)
    setBase({ json: '', revision: 0 })
    baseRef.current = { json: '', revision: 0 }
    docRef.current = null
    load()
      .catch(() => setError('Could not load the semantic layer.'))
      .finally(() => setLoading(false))
  }, [connection.id])

  // `?publish=1` — the generation notice's link — opens the publish dialog once
  // there is a draft to publish, and is then taken out of the address so a
  // reload does not open it again.
  useEffect(() => {
    if (query.get('publish') !== '1' || !layer) return
    if (layer.has_draft) setPublishing(true)
    const next = new URLSearchParams(query)
    next.delete('publish')
    setQuery(next, { replace: true })
  }, [query, layer, setQuery])

  // Poll while a generation is in flight; reload the document when it ends.
  useEffect(() => {
    if (!job || !ACTIVE.includes(job.status)) return
    let stopped = false
    const timer = setInterval(async () => {
      try {
        const next = await api.job(connection.id, job.id)
        if (stopped) return
        setJob(next)
        if (!ACTIVE.includes(next.status)) {
          clearInterval(timer)
          await load()
        }
      } catch {
        clearInterval(timer)
      }
    }, 1500)
    return () => {
      stopped = true
      clearInterval(timer)
    }
  }, [job?.id, job?.status, connection.id, load])

  // *Needs attention* is a reading of what the server holds, so it is asked for
  // again whenever that moves: a write (the revision), a re-sync (the schema
  // version), or a generation landing — never on a keystroke.
  useEffect(() => {
    if (!layer) return
    let live = true
    api.attention(connection.id)
      .then((next) => {
        if (!live) return
        setAttention(next)
        setAttentionFailed(false)
      })
      .catch(() => {
        if (!live) return
        setAttention(null)
        setAttentionFailed(true)
      })
    return () => {
      live = false
    }
  }, [connection.id, layer?.revision, layer?.schema_version, layer?.published_version])

  function patch(next: SemanticDocument) {
    setDoc({ ...next })
  }

  /** Every entity edit records that a human touched it — that flag is what
   *  makes "Generate" safe to press a second time. */
  function updateEntity(table: string, change: Partial<SemanticEntity>) {
    if (!doc) return
    patch({
      ...doc,
      entities: doc.entities.map((e) =>
        e.table === table
          ? {
              ...e,
              ...change,
              provenance: { ...e.provenance, edited: true, source: 'human' },
            }
          : e,
      ),
    })
  }

  /** Save the edits to the draft. Nothing a question reads moves.
   *
   *  No note here: a note belongs to the version the draft becomes, and the
   *  publish dialog asks for it when a change moves a number. An edit that
   *  changes nothing the model reads — a flag flipped back, a field typed and
   *  restored — is not a draft, and the bar simply stands down. */
  async function saveDraft() {
    if (!doc) return
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      adopt(await api.saveDraft(connection.id, doc, { baseRevision: base.revision }))
      setConflict(null)
      setDisplaced(null)
    } catch (err) {
      if (err instanceof ApiError && err.code === 'E_SEMANTIC_CONFLICT') {
        setConflict(err.detail ?? {})
      } else if (err instanceof ApiError && err.code === 'E_SEMANTIC_NO_CHANGES') {
        setBase({ json: JSON.stringify(doc), revision: base.revision })
        setNotice('Nothing the model reads has changed, so there was nothing to save.')
      } else {
        setError(err instanceof Error ? err.message : 'Could not save this draft.')
      }
    } finally {
      setSaving(false)
    }
  }

  /** Throw the saved draft away; the published version is untouched. */
  async function discardDraft() {
    setAskDiscardDraft(false)
    setSaving(true)
    setError(null)
    try {
      adopt(await api.discardDraft(connection.id, { baseRevision: base.revision }))
      setConflict(null)
    } catch (err) {
      if (err instanceof ApiError && err.code === 'E_SEMANTIC_CONFLICT') {
        setConflict(err.detail ?? {})
      } else {
        setError(err instanceof Error ? err.message : 'Could not discard this draft.')
      }
    } finally {
      setSaving(false)
    }
  }

  /** Take the version somebody else wrote, and list what this tab had.
   *
   *  No automatic re-apply (D6): the list is the server's reading of the
   *  edits against the document they were made from, so they can be made
   *  again by hand on top of what is there now. */
  async function reloadAfterConflict() {
    if (!doc) return
    setSaving(true)
    try {
      const lost = base.json
        ? await api.diff(connection.id, JSON.parse(base.json) as SemanticDocument, doc)
        : []
      adopt(await api.get(connection.id))
      setDisplaced(lost.length ? lost : null)
      setConflict(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reload this layer.')
    } finally {
      setSaving(false)
    }
  }

  async function startGeneration(payload: {
    llm_config_id: string
    mode: SemanticGenerationMode
    only_tables?: string[]
  }) {
    setError(null)
    try {
      const started = await api.generate(connection.id, payload)
      setJob(started)
      setGenerateFor(null)
      // Writing a layer is minutes of model calls, and nobody watches a
      // progress bar for four minutes. The poll above draws that bar while
      // this tab is open; this hands the *ending* to the shell, which is
      // still mounted wherever the reader has gone.
      watch({
        key: `semantic:${started.id}`,
        poll: async () => {
          const next = await api.job(connection.id, started.id)
          if (ACTIVE.includes(next.status)) return null
          if (next.status === 'SUCCEEDED') {
            // Written to the draft, not to what questions read — which is the
            // one thing a person three screens away must not assume otherwise.
            return {
              tone: 'ok',
              title: `Semantic layer draft written for ${connection.name}`,
              body: `${describeOutcome(next)} Nothing reaches an answer until you review and publish it.`,
              to: `/sources/${connection.id}/semantic?publish=1`,
              toLabel: 'Review and publish',
            }
          }
          if (next.status === 'CANCELLED') return null
          return {
            tone: 'error',
            title: `Semantic layer failed for ${connection.name}`,
            body: next.error_message || undefined,
            to: `/sources/${connection.id}/semantic`,
            toLabel: 'Open',
          }
        },
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start generation.')
    }
  }

  async function cancelGeneration() {
    if (!job) return
    try {
      setJob(await api.cancelJob(connection.id, job.id))
    } catch {
      /* the poll will pick up the real state */
    }
  }

  async function discardLayer() {
    setAskDelete(false)
    await api.remove(connection.id)
    adopt(await api.get(connection.id))
  }

  const needs = useMemo<AttentionView>(() => attentionView(attention?.items ?? []), [attention])

  const entities = useMemo(() => {
    if (!doc) return []
    const needle = search.trim().toLowerCase()
    return doc.entities.filter((entity) => {
      if (needle) {
        const haystack = [
          entity.table, entity.label, entity.description, entity.grain,
          ...entity.synonyms,
          ...entity.metrics.map((m) => `${m.name} ${m.label} ${m.expression}`),
          ...entity.columns.map((c) => `${c.name} ${c.label}`),
        ].join(' ').toLowerCase()
        if (!haystack.includes(needle)) return false
      }
      if (filter === 'review') return !entity.provenance.reviewed
      if (filter === 'metrics') return entity.metrics.length > 0
      if (filter === 'issues') return hasIssue(entity)
      if (filter === 'attention') return needs.tables.has(entity.table.toLowerCase())
      return true
    })
  }, [doc, search, filter, needs])

  // Which card the metrics panel sent the reader to, and when. The timestamp
  // is what makes a second click on the *same* table work: the card has to be
  // told again, and a value that did not change tells it nothing.
  const [focus, setFocus] = useState<{ table: string; at: number; section?: Section } | null>(null)

  /** Open a table's card on its metrics section and scroll to it.
   *
   *  The filter is cleared first, because the card the reader asked for may be
   *  one the current filter hides — and a click that appears to do nothing is
   *  worse than one that changes two things. */
  const revealMetrics = useCallback((table: string) => {
    setFilter('all')
    setSearch('')
    setOpen((prev) => ({ ...prev, [table]: true }))
    setFocus({ table, at: Date.now() })
  }, [])

  // After the card has rendered — it may have been filtered out a frame ago,
  // in which case there was nothing to scroll to yet.
  useEffect(() => {
    if (!focus) return
    const id = requestAnimationFrame(() => {
      document
        .getElementById(entityDomId(focus.table))
        ?.scrollIntoView({ block: 'start', behavior: 'smooth' })
    })
    return () => cancelAnimationFrame(id)
  }, [focus])

  const undescribed = useMemo(
    () => (layer?.tables ?? []).filter((t) => !t.described).map((t) => t.table),
    [layer],
  )

  // Forty red rows with one cause deserve one sentence, not forty. Only fires
  // when the layer as a whole was re-keyed — see `semantic-drift.ts`.
  const rekey = useMemo(
    () => (doc && layer ? rekeyDrift(doc.entities, layer.tables) : null),
    [doc, layer],
  )

  if (loading) {
    return (
      <Shell>
        <div
          style={{
            display: 'flex', gap: 9, alignItems: 'center',
            color: 'var(--text-dim)', fontSize: 13, padding: '40px 0',
          }}
        >
          <Spinner />
          Loading the semantic layer…
        </div>
      </Shell>
    )
  }

  const running = job !== null && ACTIVE.includes(job.status)
  const empty = !doc || doc.entities.length === 0
  const hasDraft = !!layer?.has_draft
  const barShown = dirty || !!notice || !!conflict || hasDraft

  /** Open one table's card in the editor, from a line in the history. */
  const openEntity = (table: string) => {
    navigate(`/sources/${connection.id}/semantic`)
    setFilter('all')
    setSearch('')
    setOpen((prev) => ({ ...prev, [table]: true }))
    setFocus({ table, at: Date.now() })
  }

  return (
    <>
      <Shell padBottom={barShown}>
        {error && <ErrorNote>{error}</ErrorNote>}

        {inHistory ? (
          <SemanticHistory
            connectionId={connection.id}
            layer={layer}
            version={shownVersion !== null && Number.isFinite(shownVersion) ? shownVersion : null}
            entity={query.get('entity')}
            item={query.get('item')}
            dirty={dirty}
            onRestored={(next) => {
              adopt(next)
              setDisplaced(null)
              navigate(`/sources/${connection.id}/semantic`)
            }}
            onOpenEntity={openEntity}
          />
        ) : (
        <>
        {readOnly && layerAccess && (
          <Note tone="amber">
            You can read this semantic layer, not change it
            {layerAccess.owner_name ? ` — ${layerAccess.owner_name} and anyone they give edit access can` : ''}.
          </Note>
        )}
        <Hero
          layer={layer}
          connection={connection}
          running={running}
          job={job}
          readOnly={readOnly}
          mayToggle={mayToggle}
          onGenerate={() => setGenerateFor({})}
          onDelete={() => setAskDelete(true)}
          onCancel={cancelGeneration}
          onToggle={(value) => onConnectionChange({ semantic_layer_enabled: value })}
          needs={attention ? needs : null}
          needsFailed={attentionFailed}
          onFocusFilter={(next) => {
            setFilter(next)
            setSearch('')
          }}
          onHistory={() => navigate(historyPath(connection.id))}
          onPublish={() => setPublishing(true)}
          onExport={() => setAskExport(true)}
          onImport={() => setAskImport(true)}
          dirty={dirty}
        />

        {displaced && (
          <Displaced changes={displaced} onDismiss={() => setDisplaced(null)} />
        )}

        {/* The re-key note replaces the stale one rather than joining it: it
            says everything the stale note says and then names the cause, and
            two amber notes above the same red list read as two problems. */}
        {rekey && !running ? (
          <Note tone="amber">
            {explainRekey(rekey, layer?.schema_dialect ?? '')}
          </Note>
        ) : layer?.stale && !running ? (
          <Note tone="amber">
            The schema has been re-synced since this layer was written. Anything
            that no longer matches is flagged below, and is already being kept
            out of the model's prompt.
          </Note>
        ) : null}

        {!empty && (
          <>
            {/* The two panels that describe the whole database sit together,
                above the per-table list. "Business terms" used to sit below
                it — under forty-odd rows, several of them expandable — which
                put a document-level section behind the entire working surface
                and made it read as an appendix to the last table rather than a
                peer of "About this database". It also fell under the filter
                bar, whose search and filters never applied to it. */}
            <fieldset disabled={readOnly} style={READ_ONLY_FIELDSET}>
            <Overview doc={doc!} onChange={patch} />
            {/* Between the two: what the database *is*, then what it
                *measures*, then the words people use for both. Metrics sit
                above the glossary because a term routinely maps to one. */}
            <MetricsPanel
              connectionId={connection.id}
              doc={doc!}
              onOpen={revealMetrics}
              onAdd={(table) => {
                const entity = doc!.entities.find((e) => e.table === table)
                if (!entity) return
                updateEntity(table, { metrics: [...entity.metrics, blankMetric()] })
                revealMetrics(table)
              }}
            />
            <Glossary doc={doc!} onChange={patch} />
            </fieldset>

            <FilterBar
              value={filter}
              onChange={setFilter}
              search={search}
              onSearch={setSearch}
              counts={{
                all: doc!.entities.length,
                review: doc!.entities.filter((e) => !e.provenance.reviewed).length,
                metrics: doc!.entities.filter((e) => e.metrics.length > 0).length,
                issues: doc!.entities.filter(hasIssue).length,
                attention: needs.count,
              }}
              attentionTone={needs.tone}
              shown={entities.length}
            />

            {filter === 'attention' && (
              <AttentionList
                view={needs}
                loaded={attention !== null}
                failed={attentionFailed}
                days={attention?.days ?? 30}
                running={running}
                onOpen={(row) => {
                  // The server keys a table in lower case; the card is keyed
                  // by the entity's own spelling.
                  const table = doc!.entities.find((e) => e.table.toLowerCase() === row.table)?.table
                  if (!table) return
                  setOpen((prev) => ({ ...prev, [table]: true }))
                  setFocus({ table, at: Date.now(), section: attentionSection(row, row.broken) })
                }}
                onFill={(tables) => setGenerateFor({ tables })}
                onPublish={() => setPublishing(true)}
              />
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {entities.map((entity) => (
                <EntityCard
                  key={entity.table}
                  connectionId={connection.id}
                  entity={entity}
                  readOnly={readOnly}
                  focusedAt={focus?.table === entity.table ? focus.at : 0}
                  focusedSection={focus?.table === entity.table ? focus.section : undefined}
                  open={!!open[entity.table]}
                  onToggle={() =>
                    setOpen((prev) => ({ ...prev, [entity.table]: !prev[entity.table] }))
                  }
                  onChange={(change) => updateEntity(entity.table, change)}
                  onHistory={(item) =>
                    navigate(historyPath(connection.id, { entity: entity.table.toLowerCase(), item }))
                  }
                />
              ))}
              {entities.length === 0 && filter !== 'attention' && (
                <div
                  style={{
                    border: '1px dashed var(--border-strong)',
                    borderRadius: 10,
                    padding: '28px 20px',
                    textAlign: 'center',
                    fontSize: 13,
                    color: 'var(--text-dim)',
                  }}
                >
                  Nothing matches this filter.
                </div>
              )}
            </div>
          </>
        )}
        </>
        )}
      </Shell>

      {barShown && !readOnly && (
        <SaveBar
          dirty={dirty}
          saving={saving}
          notice={notice}
          conflict={conflict}
          unpublished={hasDraft ? layer!.unpublished_changes.length : 0}
          onSave={saveDraft}
          onPublish={() => setPublishing(true)}
          onDiscardDraft={() => setAskDiscardDraft(true)}
          onSeeConflict={() => {
            if (conflict?.published_version) {
              navigate(historyPath(connection.id, { version: conflict.published_version }))
            }
          }}
          onReload={reloadAfterConflict}
          onDismissNotice={() => setNotice(null)}
          onDiscard={async () => {
            // Back to what the server holds now — which after a conflict is not
            // the document these edits started from, so it is asked for again.
            if (conflict) {
              setConflict(null)
              try {
                adopt(await api.get(connection.id))
                return
              } catch {
                /* fall back to the last layer this tab read */
              }
            }
            if (layer) adopt(layer)
          }}
        />
      )}

      {publishing && layer && (
        <PublishDialog
          connectionId={connection.id}
          layer={layer}
          onClose={() => setPublishing(false)}
          onPublished={(next) => {
            setPublishing(false)
            adopt(next)
            setNotice(`Published v${next.published_version}. The next question reads it.`)
          }}
          onConflict={(detail) => {
            setPublishing(false)
            setConflict(detail)
          }}
        />
      )}

      {askExport && layer && (
        <ExportDialog
          connectionId={connection.id}
          connectionName={connection.name}
          layer={layer}
          onClose={() => setAskExport(false)}
        />
      )}

      {askImport && (
        <ImportDialog
          connectionId={connection.id}
          layer={layer}
          dirty={dirty}
          onClose={() => setAskImport(false)}
          onImported={(next) => {
            adopt(next)
            setDisplaced(null)
            setConflict(null)
          }}
          onPublish={() => {
            setAskImport(false)
            setPublishing(true)
          }}
        />
      )}

      {askDiscardDraft && layer && (
        <ConfirmDiscardDraft
          layer={layer}
          onClose={() => setAskDiscardDraft(false)}
          onConfirm={discardDraft}
        />
      )}

      {generateFor && (
        <GenerateModal
          layer={layer}
          undescribed={undescribed}
          initialTables={generateFor.tables}
          onClose={() => setGenerateFor(null)}
          onStart={startGeneration}
        />
      )}

      {askDelete && (
        <ConfirmDelete
          count={layer?.entity_count ?? 0}
          onClose={() => setAskDelete(false)}
          onConfirm={discardLayer}
        />
      )}
    </>
  )
}

// ── shell ──────────────────────────────────────────────────────────────────
/**
 * The scroll container — `DetailBody`, the one every tab of this record uses.
 *
 * It used to be a local copy capped at 900 and centred, which put this tab's
 * cards on a different left edge from the Settings and Schema tabs beside it:
 * the tab strip stayed still and the content jumped when you switched. The cap
 * itself was right and is kept (a 1600px form field is unreadable) — it is now
 * the shared one, so there is a single answer to how wide a detail tab is.
 * `padBottom` still buys room for the floating save bar below.
 */
function Shell({
  children, padBottom,
}: {
  children: React.ReactNode
  padBottom?: boolean
}) {
  return <DetailBody padBottom={padBottom}>{children}</DetailBody>
}

// ── hero ───────────────────────────────────────────────────────────────────
function Hero({
  layer, connection, running, job, onGenerate, onDelete, onCancel, onToggle, needs, needsFailed,
  onFocusFilter, onHistory, onPublish, onExport, onImport, dirty, readOnly, mayToggle,
}: {
  layer: SemanticLayer | null
  connection: Connection
  running: boolean
  job: SemanticJob | null
  /** The reader may view the layer, not change it: nothing that writes. */
  readOnly: boolean
  /** Whether the reader may flip the data source's own on/off switch. */
  mayToggle: boolean
  /** *Needs attention*, or `null` until the server has answered. */
  needs: AttentionView | null
  needsFailed: boolean
  onGenerate: () => void
  onDelete: () => void
  onCancel: () => void
  onToggle: (value: boolean) => void
  onFocusFilter: (next: Filter) => void
  onHistory: () => void
  onPublish: () => void
  onExport: () => void
  onImport: () => void
  dirty: boolean
}) {
  const exists = !!layer?.exists
  const model = layer?.model_snapshot?.model as string | undefined
  const version = layer?.published_version ?? null
  const described = layer?.entity_count ?? 0
  const total = layer?.tables.length ?? 0

  return (
    <section
      style={{
        border: '1px solid var(--border)',
        borderRadius: 14,
        background: 'var(--panel)',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 14,
          padding: '18px 20px',
        }}
      >
        <span
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 38,
            height: 38,
            borderRadius: 10,
            flexShrink: 0,
            color: 'var(--accent)',
            background: 'var(--accent-bg)',
            border: '1px solid var(--accent-border)',
          }}
        >
          <Icon.Sparkle size={19} />
        </span>

        <div style={{ flex: 1, minWidth: 0 }}>
          <h2
            style={{
              margin: 0,
              fontSize: 15,
              fontWeight: 700,
              color: 'var(--text-strong)',
            }}
          >
            Semantic layer
          </h2>
          <p
            style={{
              margin: '3px 0 0',
              fontSize: 12.5,
              lineHeight: 1.55,
              color: 'var(--text-dim)',
            }}
          >
            What your schema <em>means</em> — business names, what one row is, and
            the exact SQL behind measures like revenue. Sent with every question.
          </p>
        </div>

        <div style={{ display: 'flex', gap: 8, flexShrink: 0, alignItems: 'center' }}>
          {/* The layer is its own grantable resource — `semantic_layer`,
              carrying this connection's id — so a Data Engineer can hold
              `(semantic_layer, manage)` over every connection and still need
              `select` on one to read a row of its data. Its Access control
              therefore belongs here rather than on the connection's tab, and
              it renders nothing for a viewer who cannot share it. */}
          <AccessPopover
            base={`connections/${connection.id}/semantic`}
            resourceLabel="this semantic layer"
          />
          {readOnly ? null : exists ? (
            <GhostButton onClick={onGenerate} disabled={running}>
              <Icon.Sparkle size={14} />
              Regenerate
            </GhostButton>
          ) : (
            <>
              {/* Nothing to describe yet, so the other way in sits beside the
                  first one: a layer somebody already wrote, as a file. */}
              <GhostButton onClick={onImport} disabled={running}>
                Import a file
              </GhostButton>
              <PrimaryButton onClick={onGenerate} disabled={running}>
                <Icon.Sparkle size={14} />
                Generate with AI
              </PrimaryButton>
            </>
          )}
          {exists && !readOnly && (
            <IconButton
              label="Delete semantic layer"
              onClick={onDelete}
              size={34}
            >
              <Icon.Trash />
            </IconButton>
          )}
        </div>
      </div>

      {running && job && (
        <div
          style={{
            padding: '14px 20px',
            borderTop: '1px solid var(--border)',
            background: 'var(--accent-bg)',
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <span style={{ color: 'var(--accent)', display: 'flex' }}>
              <Spinner size={13} />
            </span>
            <span
              style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)' }}
            >
              {job.status === 'QUEUED' ? 'Starting…' : 'Writing your semantic layer'}
            </span>
            <span style={{ marginLeft: 'auto' }}>
              <GhostButton
                onClick={onCancel}
                style={{ padding: '4px 10px', fontSize: 12 }}
              >
                Stop
              </GhostButton>
            </span>
          </div>
          <ProgressBar
            current={job.progress_current}
            total={job.progress_total}
            label={job.phase || 'Preparing'}
          />
          <span style={{ fontSize: 11.5, color: 'var(--text-dim)' }}>
            You can leave this page — generation carries on, and the result is
            written to your draft for you to review and publish.
          </span>
        </div>
      )}

      {!running && job && job.status === 'FAILED' && (
        <div style={{ padding: '0 20px 16px' }}>
          <Note tone="red">{job.error_message ?? 'Generation failed.'}</Note>
        </div>
      )}
      {!running && job && job.status === 'CANCELLED' && (
        <div style={{ padding: '0 20px 16px' }}>
          <Note tone="amber">Generation was stopped. Nothing was written.</Note>
        </div>
      )}
      {!running && job && job.status === 'SUCCEEDED' && (
        <PartialOutcome job={job} />
      )}

      {/* Nothing generated yet: the same card teaches what a layer is, so
          there is one box and one call to action rather than two of each. */}
      {!exists && !running && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
            gap: 10,
            padding: '0 20px 20px',
          }}
        >
          {WHAT_IT_HOLDS.map((item) => (
            <div
              key={item.title}
              style={{
                border: '1px solid var(--border)',
                borderRadius: 10,
                background: 'var(--panel-alt)',
                padding: '13px 14px',
              }}
            >
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  letterSpacing: 0.4,
                  textTransform: 'uppercase',
                  color: 'var(--accent)',
                }}
              >
                {item.title}
              </div>
              <div
                style={{
                  fontSize: 12.5,
                  lineHeight: 1.55,
                  color: 'var(--text-dim)',
                  marginTop: 5,
                }}
              >
                {item.body}
              </div>
            </div>
          ))}
        </div>
      )}

      {exists && (
        <>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
              borderTop: '1px solid var(--border)',
            }}
          >
            <Stat value={described} label="tables described" hint={`of ${total}`} />
            <Stat value={layer!.metric_count} label="metrics" tone="green" />
            <Stat
              value={layer!.reviewed_count}
              label="reviewed"
              tone={layer!.reviewed_count > 0 ? 'accent' : 'neutral'}
              onClick={() => onFocusFilter('review')}
            />
            {/* The same count the *Needs attention* filter carries, and the
                way into it. Until the server has answered it says so, rather
                than showing a number that is about to change. */}
            <Stat
              value={needs ? needs.count : needsFailed ? '—' : '…'}
              label="need attention"
              tone={!needs || needs.count === 0 ? 'neutral' : needs.tone}
              onClick={needs && needs.count > 0 ? () => onFocusFilter('attention') : undefined}
              last
            />
          </div>

          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 14,
              flexWrap: 'wrap',
              padding: '13px 20px',
              borderTop: '1px solid var(--border)',
              background: 'var(--panel-alt)',
            }}
          >
            <Toggle
              checked={connection.semantic_layer_enabled}
              onChange={onToggle}
              disabled={!mayToggle}
              label={
                connection.semantic_layer_enabled
                  ? 'Sent to the model'
                  : 'Not sent to the model'
              }
              hint="Turn off to write SQL from the bare schema — the way to check whether this layer is helping."
            />
            <VersionLine
              layer={layer!}
              model={model}
              onHistory={onHistory}
              onPublish={onPublish}
              onExport={onExport}
              onImport={onImport}
              dirty={dirty}
              readOnly={readOnly}
            />
          </div>
        </>
      )}

      {/* A deleted layer still has a history, and restoring it is the undo —
          so the way back is offered where the stats would have been. */}
      {!exists && !running && version !== null && (
        <div
          style={{
            display: 'flex', alignItems: 'center', gap: 10, padding: '11px 20px',
            borderTop: '1px solid var(--border)', background: 'var(--panel-alt)',
            fontSize: 12, color: 'var(--text-dim)',
          }}
        >
          <span style={{ flex: 1 }}>
            v{version} · {authorship(layer!.published_origin, layer!.published_by_name)}
            {layer!.published_at ? ` · ${relativeTime(layer!.published_at)}` : ''}
          </span>
          <GhostButton onClick={onHistory} style={{ padding: '5px 10px', fontSize: 12 }}>
            <Icon.History size={13} />
            History
          </GhostButton>
        </div>
      )}
    </section>
  )
}

/**
 * `Published v12 · by Sara Karimi · 2 days ago`, then whether anything is
 * waiting: `No unpublished changes`, or an amber `◐ 3 unpublished changes` chip
 * that opens the publish dialog. Every state has a glyph and a word.
 *
 * A layer written before versions existed reads `v1 · recorded at migration`,
 * which is true and says nothing earlier was kept.
 */
function VersionLine({
  layer, model, onHistory, onPublish, onExport, onImport, dirty, readOnly = false,
}: {
  layer: SemanticLayer
  model: string | undefined
  onHistory: () => void
  onPublish: () => void
  onExport: () => void
  onImport: () => void
  readOnly?: boolean
  dirty: boolean
}) {
  const version = layer.published_version
  const how = authorship(layer.published_origin, layer.published_by_name)
  const waiting = layer.has_draft ? layer.unpublished_changes.length : 0
  return (
    <span
      style={{
        marginLeft: 'auto',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        flexWrap: 'wrap',
        justifyContent: 'flex-end',
        fontSize: 11.5,
        color: 'var(--text-faint)',
        textAlign: 'right',
      }}
    >
      <span>
        {version !== null ? (
          <>
            Published{' '}
            <span className="mono" style={{ color: 'var(--text-dim)', fontWeight: 600 }}>
              v{version}
            </span>
            {` · ${how}`}
            {layer.published_at ? ` · ${relativeTime(layer.published_at)}` : ''}
          </>
        ) : (
          'Not published yet'
        )}
        {model ? ` · ${model}` : ''}
      </span>
      {layer.has_draft && !readOnly ? (
        <button
          onClick={onPublish}
          disabled={dirty}
          title={dirty ? 'Save your edits to the draft first' : 'Review and publish'}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 5,
            padding: '3px 9px', borderRadius: 999, fontSize: 11.5, fontWeight: 600,
            color: 'var(--amber)', background: 'var(--amber-bg)',
            border: '1px solid var(--amber-border)',
            cursor: dirty ? 'default' : 'pointer',
          }}
        >
          <span aria-hidden>◐</span>
          {unpublishedWords(waiting)}
        </button>
      ) : version !== null ? (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <span aria-hidden style={{ color: 'var(--green)' }}>●</span>
          {unpublishedWords(0)}
        </span>
      ) : null}
      {version !== null && (
        <GhostButton onClick={onHistory} style={{ padding: '4px 9px', fontSize: 12 }}>
          <Icon.History size={13} />
          History
        </GhostButton>
      )}
      {/* A file is a published version, so Export waits for one; Import lands
          in the draft and is always offered. */}
      {version !== null && (
        <GhostButton onClick={onExport} style={{ padding: '4px 9px', fontSize: 12 }}>
          <Icon.ArrowDown size={12} />
          Export
        </GhostButton>
      )}
      {!readOnly && (
        <GhostButton onClick={onImport} style={{ padding: '4px 9px', fontSize: 12 }}>
          Import
        </GhostButton>
      )}
    </span>
  )
}

/** The edits a conflict reload took out of the tab, as the server words them.
 *  Listed so they can be made again by hand; nothing re-applies them (D6). */
function Displaced({
  changes, onDismiss,
}: {
  changes: SemanticChange[]
  onDismiss: () => void
}) {
  return (
    <section
      style={{
        border: '1px solid var(--amber-border)',
        background: 'var(--amber-bg)',
        borderRadius: 12,
        padding: '12px 16px',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ color: 'var(--amber)', display: 'flex' }}><Icon.Alert /></span>
        <span style={{ flex: 1, fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)' }}>
          Your unsaved edits — make them again on top of the version below
        </span>
        <GhostButton onClick={onDismiss} style={{ padding: '4px 9px', fontSize: 12 }}>
          Done
        </GhostButton>
      </div>
      <ChangeList changes={changes} />
    </section>
  )
}

function Stat({
  value, label, hint, tone = 'neutral', onClick, last,
}: {
  value: number | string
  label: string
  hint?: string
  tone?: 'neutral' | 'green' | 'accent' | 'red' | 'amber'
  onClick?: () => void
  last?: boolean
}) {
  const color =
    tone === 'neutral' ? 'var(--text-strong)' : `var(--${tone})`
  const inner = (
    <>
      <span
        style={{
          fontSize: 21,
          fontWeight: 700,
          lineHeight: 1.1,
          color,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {value}
      </span>
      <span
        style={{
          fontSize: 10.5,
          fontWeight: 600,
          letterSpacing: 0.4,
          textTransform: 'uppercase',
          color: 'var(--text-faint)',
        }}
      >
        {label}
        {hint ? ` ${hint}` : ''}
      </span>
    </>
  )
  const style: React.CSSProperties = {
    display: 'flex',
    flexDirection: 'column',
    gap: 3,
    padding: '14px 20px',
    background: 'transparent',
    border: 'none',
    borderRight: last ? 'none' : '1px solid var(--border)',
    textAlign: 'left',
    minWidth: 0,
  }
  if (!onClick) return <div style={style}>{inner}</div>
  return (
    <button
      onClick={onClick}
      style={{ ...style, cursor: 'pointer' }}
      title={`Show only these`}
    >
      {inner}
    </button>
  )
}

function ConfirmDelete({
  count, onClose, onConfirm,
}: {
  count: number
  onClose: () => void
  onConfirm: () => void
}) {
  return (
    <Modal
      title="Delete this semantic layer?"
      subtitle={`${count} described ${count === 1 ? 'table' : 'tables'}, including anything you edited by hand.`}
      onClose={onClose}
      width={440}
      footer={
        <>
          <GhostButton onClick={onClose}>Keep it</GhostButton>
          <DangerButton onClick={onConfirm} style={{ padding: '9px 16px', fontSize: 13 }}>
            <Icon.Trash />
            Delete
          </DangerButton>
        </>
      }
    >
      <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: 'var(--text-dim)' }}>
        Your schema, connection and conversations are untouched. Questions will
        go back to being answered from the bare schema, and any unpublished
        draft is discarded. The history is kept, so a deleted layer can be
        restored from it.
      </p>
    </Modal>
  )
}

function ConfirmDiscardDraft({
  layer, onClose, onConfirm,
}: {
  layer: SemanticLayer
  onClose: () => void
  onConfirm: () => void
}) {
  const count = layer.unpublished_changes.length
  const by = layer.draft_updated_by_name
  return (
    <Modal
      title="Discard the draft?"
      subtitle={`${unpublishedWords(count)}${by ? `, last saved by ${by}` : ''}.`}
      onClose={onClose}
      width={440}
      footer={
        <>
          <GhostButton onClick={onClose}>Keep it</GhostButton>
          <DangerButton onClick={onConfirm} style={{ padding: '9px 16px', fontSize: 13 }}>
            <Icon.Trash />
            Discard draft
          </DangerButton>
        </>
      }
    >
      <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: 'var(--text-dim)' }}>
        {layer.published_version !== null
          ? `The published v${layer.published_version} is untouched, and it is what questions keep reading.`
          : 'Nothing has been published, so questions keep being answered from the bare schema.'}
        {' '}A draft is not a version: once discarded, it cannot be restored.
      </p>
    </Modal>
  )
}

/**
 * What a *successful* generation did not manage.
 *
 * `SUCCEEDED` meant nothing on screen, whatever was in the job's stats. One
 * run against a 42-table schema described 21 of them, wrote no glossary, and
 * reported success — the tables it lost were in `stats.tables_failed` the whole
 * time, delivered to the browser and read by nobody. A layer with half its
 * tables missing is not a failure to retry, it is a layer to *finish*, so this
 * says which tables and how to fill them.
 *
 * Silent when there is nothing to report: a clean run draws no box.
 */
/**
 * A finished generation in one line, for the notice.
 *
 * The counts, not the verdict: `SUCCEEDED` with four tables refused is a
 * success the reader still needs to know about, which is the same reason
 * `PartialOutcome` exists on the tab itself. This is its sentence-sized
 * cousin, for someone who is three screens away.
 */
function describeOutcome(job: SemanticJob): string {
  const described = job.stats.tables_described ?? 0
  const failed = (job.stats.tables_failed ?? []).length
  const parts = [`${described} ${described === 1 ? 'table' : 'tables'} described`]
  if (failed) parts.push(`${failed} could not be`)
  const dropped = job.stats.metrics_dropped ?? 0
  if (dropped) parts.push(`${dropped} ${dropped === 1 ? 'metric' : 'metrics'} dropped`)
  return `${parts.join(', ')}.`
}

// ── panels ─────────────────────────────────────────────────────────────────
function PartialOutcome({ job }: { job: SemanticJob }) {
  const failed = job.stats.tables_failed ?? []
  const dropped = job.stats.metrics_dropped ?? 0
  const described = job.stats.tables_described ?? 0
  if (failed.length === 0 && dropped === 0 && !job.stats.glossary_failed) return null

  return (
    <div style={{ padding: '0 20px 16px' }}>
      <Note tone="amber">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
          <span style={{ fontWeight: 600 }}>
            {failed.length > 0
              ? `${failed.length} of ${failed.length + described} tables have no description.`
              : 'Generation finished with gaps.'}
          </span>

          {failed.length > 0 && (
            <div
              className="mono"
              style={{
                fontSize: 11,
                lineHeight: 1.7,
                maxHeight: 96,
                overflowY: 'auto',
                opacity: 0.85,
                // The names are the actionable part — "21 tables failed" with
                // no list leaves the reader to diff two schemas by hand.
                wordBreak: 'break-word',
              }}
            >
              {failed.join(', ')}
            </div>
          )}

          {job.stats.glossary_failed && (
            <span>
              The glossary could not be written, so there are no business terms.
            </span>
          )}
          {dropped > 0 && (
            <span>
              {dropped} generated {dropped === 1 ? 'metric was' : 'metrics were'}{' '}
              dropped because the SQL did not check out.
            </span>
          )}

          <span style={{ opacity: 0.85 }}>
            Generate again in <strong>Merge</strong> mode to fill these in —
            everything you have edited is kept.
          </span>
        </div>
      </Note>
    </div>
  )
}

/** A fieldset that only disables what it holds — no box, no spacing of its own
 *  beyond the column gap the panels it wraps already had. */
const READ_ONLY_FIELDSET: React.CSSProperties = {
  border: 0,
  padding: 0,
  margin: 0,
  minWidth: 0,
  display: 'flex',
  flexDirection: 'column',
  gap: 16,
}

/** Floats clear of the content instead of eating a strip of the pane, so the
 *  last card is never half-hidden behind it.
 *
 *  Two states, never both, because they are two different acts:
 *
 *   - edits in this tab not saved yet — `Unsaved edits [Discard] [Save draft]`;
 *   - a saved draft that differs from what is published —
 *     `3 unpublished changes [Discard draft] [Review and publish]`.
 *
 *  Everything a write can answer lands here, beside the button that was
 *  pressed — not in a toast: the conflict when somebody wrote first, and the
 *  quiet "nothing to save". */
function SaveBar({
  dirty, saving, notice, conflict, unpublished, onSave, onPublish, onDiscardDraft,
  onSeeConflict, onReload, onDismissNotice, onDiscard,
}: {
  dirty: boolean
  saving: boolean
  notice: string | null
  conflict: ProblemDetail | null
  /** Changes in the saved draft against the published layer; 0 for no draft. */
  unpublished: number
  onSave: () => void
  onPublish: () => void
  onDiscardDraft: () => void
  onSeeConflict: () => void
  onReload: () => void
  onDismissNotice: () => void
  onDiscard: () => void
}) {
  const who = conflict?.updated_by_name || 'Someone'
  const drafted = !dirty && !conflict && unpublished > 0
  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        bottom: 20,
        display: 'flex',
        justifyContent: 'center',
        pointerEvents: 'none',
        zIndex: 30,
      }}
    >
      <div
        className="rm-enter"
        role={conflict ? 'alert' : undefined}
        style={{
          pointerEvents: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
          width: conflict ? 'min(560px, calc(100% - 32px))' : undefined,
          maxWidth: 'calc(100% - 32px)',
          padding: '10px 12px 10px 18px',
          borderRadius: 12,
          background: 'var(--panel)',
          border: `1px solid ${
            conflict ? 'var(--red-border)' : drafted ? 'var(--amber-border)' : 'var(--border-strong)'
          }`,
          boxShadow: 'inset 0 1px 0 0 var(--sheen), var(--elev-3)',
        }}
      >
        {conflict && (
          <div
            style={{
              display: 'flex', alignItems: 'flex-start', gap: 8, fontSize: 12.5,
              lineHeight: 1.55, color: 'var(--red)', paddingTop: 4,
            }}
          >
            <span style={{ marginTop: 2, flexShrink: 0 }}><Icon.Alert /></span>
            <span style={{ flex: 1 }}>
              <strong>{who}</strong> changed this layer
              {conflict.published_version ? ` (published v${conflict.published_version})` : ''} while
              you were editing. Your edits are still in this tab.
            </span>
          </div>
        )}

        {notice && !conflict && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: 'var(--text-dim)' }}>
            <span style={{ color: 'var(--green)', display: 'flex' }}><Icon.Check /></span>
            <span style={{ flex: 1 }}>{notice}</span>
            {!dirty && !drafted && (
              <GhostButton onClick={onDismissNotice} style={{ padding: '4px 9px', fontSize: 12 }}>
                OK
              </GhostButton>
            )}
          </div>
        )}

        {(dirty || conflict || drafted) && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, justifyContent: 'space-between', flexWrap: 'wrap' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: 'var(--text-dim)' }}>
              {drafted && <span aria-hidden style={{ color: 'var(--amber)' }}>◐</span>}
              {conflict ? 'Not saved' : dirty ? 'Unsaved edits' : unpublishedWords(unpublished)}
            </span>
            <span style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
              {conflict ? (
                <>
                  <GhostButton onClick={onSeeConflict} disabled={saving} style={{ padding: '7px 12px' }}>
                    See what changed
                  </GhostButton>
                  <PrimaryButton onClick={onReload} disabled={saving} style={{ padding: '7px 14px' }}>
                    {saving && <Spinner />}
                    Reload
                  </PrimaryButton>
                </>
              ) : dirty ? (
                <>
                  <GhostButton onClick={onDiscard} disabled={saving} style={{ padding: '7px 12px' }}>
                    Discard
                  </GhostButton>
                  <PrimaryButton onClick={onSave} disabled={saving} style={{ padding: '7px 14px' }}>
                    {saving && <Spinner />}
                    Save draft
                  </PrimaryButton>
                </>
              ) : (
                <>
                  <GhostButton onClick={onDiscardDraft} disabled={saving} style={{ padding: '7px 12px' }}>
                    Discard draft
                  </GhostButton>
                  <PrimaryButton onClick={onPublish} disabled={saving} style={{ padding: '7px 14px' }}>
                    Review and publish
                  </PrimaryButton>
                </>
              )}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}

// ── overview ───────────────────────────────────────────────────────────────
function Overview({
  doc, onChange,
}: {
  doc: SemanticDocument
  onChange: (next: SemanticDocument) => void
}) {
  const time = doc.time
  function setTime(change: Partial<SemanticDocument['time']>) {
    onChange({
      ...doc,
      time: { ...time, ...change, provenance: { ...time.provenance, edited: true } },
    })
  }

  // Closed, the panel says what it is holding: the first clause of the context
  // and the conventions that decide what "last month" resolves to.
  const context = doc.business_context.trim()
  const summary = [
    context ? (context.length > 90 ? `${context.slice(0, 90)}…` : context) : 'No context written',
    // Worth a word even closed: a reader comparing a total against another
    // tool needs to know whether rows are being held back.
    doc.default_exclusions.trim() ? 'Exclusions set' : null,
    `${time.relative_windows === 'calendar' ? 'Calendar' : 'Rolling'} windows`,
    `FY from ${MONTHS[time.fiscal_year_start_month - 1] ?? '—'}`,
    `weeks from ${time.week_starts_on === 'monday' ? 'Mon' : 'Sun'}`,
  ].filter(Boolean).join(' · ')

  return (
    <Panel
      title="About this database"
      description="Sent with every question. Two or three sentences and the time conventions are worth more here than anything else on this page."
      summary={summary}
      // Unwritten context is the highest-value thing on the page, so an empty
      // one opens itself; a filled one gets out of the way of the tables.
      defaultOpen={!context}
    >
      <Field
        label="What this database is for"
        hint="Two or three sentences. This is the one field that reaches the model on every single question."
      >
        <TextArea
          value={doc.business_context}
          // Sized for what it is asking for. At the default three lines, the
          // field looked like it wanted a phrase, and a phrase is not what
          // makes `dim_cust_x` readable as customers.
          rows={6}
          placeholder="e.g. An online retailer's order book: customers place orders made of line items, fulfilled from warehouses…"
          onChange={(e) =>
            onChange({
              ...doc,
              business_context: e.target.value,
              // Its own flag, so a regeneration keeps what a person wrote here
              // even when nothing else in the layer was touched.
              context_provenance: { source: 'human', reviewed: false, ...doc.context_provenance, edited: true },
            })
          }
          style={{ minHeight: 132 }}
        />
      </Field>

      {/* Sits directly under the context because it is read with it, and
          because it is the one field on this panel that changes the SQL
          rather than the reading of it. */}
      <Field
        label="Rows that should not count"
        hint="Soft deletes, test accounts, internal orders. A metric can carry its own filters — this is for the questions that have no metric, where a plain COUNT(*) quietly includes everything."
      >
        <TextArea
          value={doc.default_exclusions}
          rows={2}
          placeholder="e.g. Rows where is_archived is true. Customers whose email ends in @internal.example — these are test accounts."
          onChange={(e) =>
            onChange({
              ...doc,
              default_exclusions: e.target.value,
              exclusions_provenance: { source: 'human', reviewed: false, ...doc.exclusions_provenance, edited: true },
            })
          }
          style={{ minHeight: 58 }}
        />
      </Field>

      <Field
        label="“Last month” means"
        hint="The single most common source of a wrong-looking answer."
      >
        <ChoiceRow
          value={time.relative_windows}
          onChange={(next) =>
            setTime({ relative_windows: next as 'calendar' | 'rolling' })
          }
          options={[
            { value: 'calendar', label: 'Calendar', hint: 'The whole previous month' },
            { value: 'rolling', label: 'Rolling', hint: 'The last 30 days' },
          ]}
        />
      </Field>

      <FieldRow columns={3}>
        <Field label="Fiscal year starts" hint="Drives “this year” and “YTD”.">
          <Select
            value={String(time.fiscal_year_start_month)}
            onChange={(e) => setTime({ fiscal_year_start_month: Number(e.target.value) })}
          >
            {MONTHS.map((month, index) => (
              <option key={month} value={index + 1}>{month}</option>
            ))}
          </Select>
        </Field>
        <Field label="Weeks start on">
          <Select
            value={time.week_starts_on}
            onChange={(e) =>
              setTime({ week_starts_on: e.target.value as 'monday' | 'sunday' })
            }
          >
            <option value="monday">Monday</option>
            <option value="sunday">Sunday</option>
          </Select>
        </Field>
        <Field label="Time zone" hint="How stored timestamps are read.">
          <TextInput
            value={time.timezone}
            onChange={(e) => setTime({ timezone: e.target.value })}
          />
        </Field>
      </FieldRow>

      {/* A one-line input for a catch-all: most schemas have more than one
          convention worth stating, and the box said otherwise. */}
      <Field
        label="Other time conventions"
        hint="Optional. Anything else that changes what a date means."
      >
        <TextArea
          value={time.notes}
          rows={2}
          placeholder="e.g. Orders are timestamped when paid, not when placed. Data loads nightly at 02:00, so today is partial."
          onChange={(e) => setTime({ notes: e.target.value })}
          style={{ minHeight: 58 }}
        />
      </Field>
    </Panel>
  )
}

// ── filters ────────────────────────────────────────────────────────────────
function FilterBar({
  value, onChange, search, onSearch, counts, attentionTone, shown,
}: {
  value: Filter
  onChange: (next: Filter) => void
  search: string
  onSearch: (next: string) => void
  counts: Record<Filter, number>
  /** Red only when something is broken: an undescribed table is work, not an alarm. */
  attentionTone: AttentionTone
  shown: number
}) {
  const options: { value: Filter; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'review', label: 'Needs review' },
    { value: 'metrics', label: 'Has metrics' },
    { value: 'issues', label: 'Has issues' },
    { value: 'attention', label: 'Needs attention' },
  ]
  return (
    <div
      style={{
        // Reads as a floating toolbar rather than a strip of page: a solid
        // panel background is the only thing that stays right over the app's
        // light-theme background wash.
        position: 'sticky',
        top: 6,
        zIndex: 10,
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        flexWrap: 'wrap',
        padding: '8px 10px',
        borderRadius: 11,
        background: 'var(--panel)',
        border: '1px solid var(--border)',
        boxShadow: 'var(--elev-1)',
      }}
    >
      <PillTabs
        value={value}
        onChange={onChange}
        ariaLabel="Filter tables"
        options={options.map((option) => ({
          value: option.value,
          label: option.label,
          count: counts[option.value],
          alert:
            (option.value === 'issues' && counts.issues > 0) ||
            (option.value === 'attention' && counts.attention > 0 && attentionTone === 'red'),
        }))}
      />

      <div style={{ position: 'relative', marginLeft: 'auto', width: 260 }}>
        <span
          style={{
            position: 'absolute',
            left: 10,
            top: '50%',
            transform: 'translateY(-50%)',
            color: 'var(--text-faint)',
            display: 'flex',
            pointerEvents: 'none',
          }}
        >
          <Icon.Search size={13} />
        </span>
        <TextInput
          placeholder="Search tables, metrics, columns…"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          style={{ fontSize: 13, padding: '8px 11px 8px 30px' }}
        />
      </div>
      {search && (
        <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>{shown} shown</span>
      )}
    </div>
  )
}

// ── needs attention ────────────────────────────────────────────────────────
/** How many undescribed tables are named before *Show all*. */
const UNDESCRIBED_SHOWN = 12

/**
 * *Needs attention*, above the cards it opens.
 *
 * Every sentence is the server's reason in words (`semantic-attention.ts`);
 * nothing here decides whether a table needs anybody. The shape is one line
 * for a draft left sitting, **a row per table** carrying each of its reasons,
 * and the undescribed tables together in one row — twenty-one "no
 * description" lines would bury the one that says a metric is broken.
 *
 * Each row offers the act that answers it. *Open* expands the table's card,
 * which the filter keeps in the list below. A table whose columns grew offers
 * *Fill the gaps…*, and the undescribed row *Describe…*: both open the
 * generate dialog with those tables already chosen, so the one fix that needs
 * a model is one click from the reason that asked for it.
 */
function AttentionList({
  view, loaded, failed, days, running, onOpen, onFill, onPublish,
}: {
  view: AttentionView
  loaded: boolean
  failed: boolean
  days: number
  running: boolean
  onOpen: (row: AttentionRow) => void
  onFill: (tables: string[]) => void
  onPublish: () => void
}) {
  const [everyTable, setEveryTable] = useState(false)

  if (failed) {
    return <ErrorNote>Could not work out what needs attention. Reload the page to try again.</ErrorNote>
  }
  if (!loaded) {
    return (
      <span style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12.5, color: 'var(--text-faint)', padding: '6px 2px' }}>
        <Spinner size={12} /> Looking for what needs attention…
      </span>
    )
  }
  if (view.count === 0) {
    return (
      <div
        style={{
          display: 'flex', gap: 10, alignItems: 'baseline',
          border: '1px dashed var(--border-strong)', borderRadius: 10,
          padding: '16px 18px', fontSize: 13, color: 'var(--text-dim)', lineHeight: 1.55,
        }}
      >
        <span aria-hidden style={{ color: 'var(--green)', fontWeight: 700 }}>✓</span>
        <span>
          Nothing needs attention. Every table is described, the schema breaks
          nothing, and no answer in the last {days} days leaned on text nobody
          reviewed.
        </span>
      </div>
    )
  }

  const shown = everyTable ? view.undescribed : view.undescribed.slice(0, UNDESCRIBED_SHOWN)
  return (
    <div role="list" aria-label="Needs attention" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {view.draft && (
        <AttentionRowFrame
          tone={view.draft.tone}
          heading="Your draft"
          lines={[view.draft]}
          actions={<GhostButton onClick={onPublish} style={ROW_BUTTON}>Review and publish</GhostButton>}
        />
      )}
      {view.rows.map((row) => (
        <AttentionRowFrame
          key={row.table}
          tone={row.tone}
          heading={row.table}
          mono
          lines={row.lines}
          actions={
            <>
              {row.fillable && (
                <GhostButton
                  onClick={() => onFill([row.table])}
                  disabled={running}
                  title={running ? 'A generation is already running.' : undefined}
                  style={ROW_BUTTON}
                >
                  Fill the gaps…
                </GhostButton>
              )}
              <GhostButton onClick={() => onOpen(row)} style={ROW_BUTTON}>Open</GhostButton>
            </>
          }
        />
      ))}
      {view.undescribed.length > 0 && (
        <AttentionRowFrame
          tone="neutral"
          heading={`${view.undescribed.length} undescribed`}
          lines={[{ reason: 'UNDESCRIBED', glyph: '○', tone: 'neutral', text: undescribedWords(view.undescribed.length) }]}
          actions={
            <GhostButton
              onClick={() => onFill(view.undescribed.map((t) => t.table))}
              disabled={running}
              title={running ? 'A generation is already running.' : undefined}
              style={ROW_BUTTON}
            >
              Describe…
            </GhostButton>
          }
        >
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 7 }}>
            {shown.map((t) => (
              <Chip key={t.table} tone="neutral" small>
                <span className="mono" dir="ltr">{t.table}</span>
              </Chip>
            ))}
            {view.undescribed.length > UNDESCRIBED_SHOWN && (
              <button
                type="button"
                onClick={() => setEveryTable((v) => !v)}
                style={{
                  font: 'inherit', fontSize: 11.5, color: 'var(--accent)', background: 'none',
                  border: 'none', padding: '0 4px', cursor: 'pointer',
                }}
              >
                {everyTable ? 'Show fewer' : `and ${view.undescribed.length - UNDESCRIBED_SHOWN} more`}
              </button>
            )}
          </div>
        </AttentionRowFrame>
      )}
    </div>
  )
}

const ROW_BUTTON: React.CSSProperties = { padding: '4px 10px', fontSize: 12 }

function AttentionRowFrame({
  tone, heading, mono, lines, actions, children,
}: {
  tone: AttentionTone
  heading: string
  mono?: boolean
  lines: AttentionLine[]
  actions: React.ReactNode
  children?: React.ReactNode
}) {
  const border =
    tone === 'red' ? 'var(--red-border)' : tone === 'amber' ? 'var(--amber-border)' : 'var(--border)'
  return (
    <div
      role="listitem"
      style={{
        display: 'flex', gap: 12, rowGap: 8, flexWrap: 'wrap', alignItems: 'flex-start',
        padding: '10px 12px', borderRadius: 9,
        background: 'var(--panel-alt)', border: `1px solid ${border}`,
      }}
    >
      <div style={{ minWidth: 0, flex: '1 1 260px' }}>
        <div
          className={mono ? 'mono' : undefined}
          dir={mono ? 'ltr' : undefined}
          style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)', overflowWrap: 'anywhere' }}
        >
          {heading}
        </div>
        {lines.map((line, index) => (
          <div key={`${line.reason}-${index}`} style={{ display: 'flex', gap: 7, marginTop: 4, fontSize: 12.5, lineHeight: 1.5 }}>
            <span
              aria-hidden
              style={{
                flexShrink: 0, width: 14, textAlign: 'center', fontWeight: 700,
                color: line.tone === 'neutral' ? 'var(--text-faint)' : `var(--${line.tone})`,
              }}
            >
              {line.glyph}
            </span>
            <span style={{ minWidth: 0, color: 'var(--text)' }}>
              {line.text}
              {line.note && (
                <span style={{ display: 'block', fontSize: 11.5, color: 'var(--text-faint)', marginTop: 2 }}>
                  {line.note}
                </span>
              )}
            </span>
          </div>
        ))}
        {children}
      </div>
      <div style={{ display: 'flex', gap: 6, flexShrink: 0, marginInlineStart: 'auto' }}>{actions}</div>
    </div>
  )
}

/**
 * Every measure this database defines, in one list.
 *
 * **A lens, not a second home.** The metric still lives on the entity it
 * measures — an aggregate needs a grain and columns, which is why every
 * product with this feature anchors the definition somewhere: a dataset in
 * Superset, a home table in Power BI, a source in a Databricks metric view.
 * What none of them do is make *browsing* follow *defining*, and this panel is
 * the difference. Editing still happens in the table's own card: one editor,
 * two ways in, and no second copy of a metric to keep in agreement.
 *
 * It exists for two questions the per-table tree cannot answer:
 *
 * * **What does this database measure?** The first question anybody asks of a
 *   layer, and in the tree it is a walk through forty expandable cards.
 * * **Does a name mean one thing?** `revenue` on `orders` and `revenue` on
 *   `invoices` are each valid, sit two screens apart, and are invisible to
 *   each other. Here they sort adjacent and both say so — the server refuses
 *   them on save, and this says it while it is still being typed.
 */
function MetricsPanel({
  connectionId, doc, onOpen, onAdd,
}: {
  connectionId: string
  doc: SemanticDocument
  /** Reveal a metric where it is edited: its table's card, on its metrics
   *  section, scrolled to. */
  onOpen: (table: string) => void
  onAdd: (table: string) => void
}) {
  const rows = useMemo(() => collectMetrics(doc.entities), [doc])
  const [query, setQuery] = useState('')
  const [target, setTarget] = useState('')
  const shown = useMemo(
    () => rows.filter((row) => matchesMetric(row, query)),
    [rows, query],
  )
  // A table to add to has to be chosen, and the first one is a fine default
  // for a layer with one table and a poor one for a layer with forty — so it
  // is a picker either way rather than a guess that is right sometimes.
  const tables = doc.entities.map((e) => e.table)
  const addTo = target || tables[0] || ''

  return (
    <Panel
      title="Metrics"
      description="Every measure this database defines, and where each is defined."
      summary={metricSummary(rows)}
      defaultOpen={false}
      action={
        rows.length > 6 ? (
          <TextInput
            value={query}
            placeholder="Filter metrics…"
            aria-label="Filter metrics"
            onChange={(e) => setQuery(e.target.value)}
            style={{ maxWidth: 200, flexShrink: 0 }}
          />
        ) : undefined
      }
    >
      {rows.length === 0 && (
        <div
          style={{
            border: '1px dashed var(--border-strong)',
            borderRadius: 9,
            padding: '16px 14px',
            fontSize: 12.5,
            color: 'var(--text-faint)',
            textAlign: 'center',
          }}
        >
          No metrics yet. A metric is the part of this layer that changes
          answers — “revenue” stops being re-derived per question, filters
          included.
        </div>
      )}

      {rows.length > 0 && shown.length === 0 && (
        <div style={{ fontSize: 12.5, color: 'var(--text-faint)', padding: '4px 2px' }}>
          No metric matches “{query.trim()}”.
        </div>
      )}

      {shown.map((row) => (
        <MetricLine
          key={`${row.table}-${row.index}`}
          row={row}
          onOpen={() => onOpen(row.table)}
        />
      ))}

      {rows.length > 0 && <MetricsInUse connectionId={connectionId} />}

      {tables.length > 0 && (
        <div
          style={{
            display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap',
            paddingTop: 2,
          }}
        >
          <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>
            Add a metric to
          </span>
          <Select
            value={addTo}
            aria-label="Table to define the metric on"
            style={{ maxWidth: 260 }}
            onChange={(e) => setTarget(e.target.value)}
          >
            {tables.map((table) => (
              <option key={table} value={table}>{table}</option>
            ))}
          </Select>
          <GhostButton
            onClick={() => onAdd(addTo)}
            disabled={!addTo}
            style={{ padding: '6px 11px', fontSize: 12.5 }}
          >
            <Icon.Plus size={13} />
            Add metric
          </GhostButton>
        </div>
      )}
    </Panel>
  )
}

/** Rows *Metrics in use* shows before *Show all*. */
const IN_USE_ROWS = 8

/**
 * *Metrics in use*: how the answers of the last 30 days fared against each
 * definition — how many touched the metric's table, how many used the
 * definition, how many left part of it out. Most-left-out first, which is the
 * order a curator should look in.
 *
 * Counts only: the server names no question and no asker. `left out` here is a
 * count over many answers and accuses no single one; on an answer it is not
 * shown at all until its precision has been measured.
 */
function MetricsInUse({ connectionId }: { connectionId: string }) {
  const [use, setUse] = useState<SemanticMetricUse | null>(null)
  const [failed, setFailed] = useState(false)
  const [all, setAll] = useState(false)
  useEffect(() => {
    let live = true
    setUse(null)
    setFailed(false)
    api.metricUse(connectionId)
      .then((next) => live && setUse(next))
      .catch(() => live && setFailed(true))
    return () => {
      live = false
    }
  }, [connectionId])

  if (failed) return null
  const head = (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
      <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-strong)' }}>In use</span>
      <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
        the last {use?.days ?? 30} days of answers, counted per metric over those
        whose SQL touched its table — most left out first
      </span>
    </div>
  )
  if (use === null) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, paddingTop: 4 }}>
        {head}
        <span style={{ display: 'flex', gap: 7, alignItems: 'center', fontSize: 12, color: 'var(--text-faint)' }}>
          <Spinner size={12} /> Counting…
        </span>
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, paddingTop: 4 }}>
      {head}
      {use.rows.length === 0 ? (
        <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>
          No answer in that time touched a table with a metric.
        </span>
      ) : (
        <div style={{ overflowX: 'auto', border: '1px solid var(--border)', borderRadius: 9 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ color: 'var(--text-faint)', textAlign: 'start' }}>
                {['Metric', 'Answers on its table', 'Used it', 'Left part out'].map((label, index) => (
                  <th
                    key={label}
                    scope="col"
                    style={{
                      padding: '7px 10px', fontWeight: 600, fontSize: 11,
                      textAlign: index === 0 ? 'start' : 'end',
                      borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap',
                    }}
                  >
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(all ? use.rows : use.rows.slice(0, IN_USE_ROWS)).map((row) => (
                <tr key={`${row.entity}.${row.metric}`} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '7px 10px' }}>
                    <span className="mono" style={{ fontWeight: 600, color: 'var(--text-strong)' }}>{row.metric}</span>
                    <span className="mono" style={{ display: 'block', fontSize: 11, color: 'var(--text-faint)' }}>{row.entity}</span>
                  </td>
                  <td style={{ padding: '7px 10px', textAlign: 'end', fontVariantNumeric: 'tabular-nums' }}>{row.questions}</td>
                  <td style={{ padding: '7px 10px', textAlign: 'end', fontVariantNumeric: 'tabular-nums', color: 'var(--text-strong)' }}>{row.used}</td>
                  <td
                    style={{
                      padding: '7px 10px', textAlign: 'end', fontVariantNumeric: 'tabular-nums',
                      color: row.ignored > row.used ? 'var(--amber)' : 'var(--text-dim)',
                      fontWeight: row.ignored > row.used ? 700 : 400,
                    }}
                  >
                    {row.ignored > row.used ? `◆ ${row.ignored}` : row.ignored}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {use.rows.length > IN_USE_ROWS && (
        <GhostButton
          onClick={() => setAll((v) => !v)}
          style={{ alignSelf: 'flex-start', padding: '4px 9px', fontSize: 12 }}
        >
          {all ? 'Show fewer' : `Show all ${use.rows.length}`}
        </GhostButton>
      )}
      <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
        The rest of each row’s answers either did not compute the metric or wrote
        it in a form that could not be compared — neither is counted against it.
      </span>
    </div>
  )
}

/** One metric in that list. The whole row opens it where it is edited. */
function MetricLine({ row, onOpen }: { row: MetricRow; onOpen: () => void }) {
  const broken = row.valid === false || row.ambiguous
  return (
    <button
      type="button"
      onClick={onOpen}
      className="rm-krow"
      style={{
        // Wraps on a phone: the table name drops under the expression rather
        // than squeezing it into four characters and an ellipsis.
        display: 'flex', gap: 12, rowGap: 4, flexWrap: 'wrap',
        alignItems: 'baseline', width: '100%',
        textAlign: 'start', padding: '9px 11px', borderRadius: 9,
        cursor: 'pointer', font: 'inherit', color: 'inherit',
        background: 'var(--panel-alt)',
        border: `1px solid ${broken ? 'var(--red-border)' : 'var(--border)'}`,
      }}
    >
      <span style={{ minWidth: 0, flex: '1 1 200px' }}>
        <span
          className="mono"
          style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)' }}
        >
          {row.name || 'unnamed'}
        </span>
        {row.label && (
          <span style={{ fontSize: 12, color: 'var(--text-dim)', marginInlineStart: 8 }}>
            {row.label}
          </span>
        )}
        <span
          className="mono"
          dir="ltr"
          style={{
            display: 'block', fontSize: 11.5, color: 'var(--text-faint)',
            marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {row.expression || 'no expression yet'}
        </span>
        {/* Ambiguity first: a valid expression under a contested name is the
            problem that a per-table view could not show at all. */}
        {row.ambiguous && (
          <span style={{ display: 'block', fontSize: 11.5, color: 'var(--red)', marginTop: 3 }}>
            Another table defines “{row.name}” too — neither is used until one
            is renamed.
          </span>
        )}
        {!row.ambiguous && row.valid === false && row.issue && (
          <span style={{ display: 'block', fontSize: 11.5, color: 'var(--red)', marginTop: 3 }}>
            {row.issue}
          </span>
        )}
      </span>
      <span
        className="mono"
        style={{
          fontSize: 11.5, color: 'var(--text-faint)', flexShrink: 0,
          marginInlineStart: 'auto',
        }}
      >
        {row.table}
      </span>
      {row.excluded && <Chip tone="neutral" small>Set aside</Chip>}
      {row.required_joins && row.required_joins.length > 0 && (
        <Chip tone="neutral" small>
          {row.required_joins.length === 1
            ? '1 join'
            : `${row.required_joins.length} joins`}
        </Chip>
      )}
    </button>
  )
}
