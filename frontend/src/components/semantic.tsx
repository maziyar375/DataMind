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
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { llmConfigs as llmApi, semantic as api } from '../api/client'
import type {
  Connection, GlossaryTerm, LlmConfig, SemanticColumn, SemanticDocument,
  SemanticEntity, SemanticJob, SemanticLayer, SemanticMetric,
} from '../api/types'
import {
  Chip, DangerButton, ErrorNote, Field, GhostButton, Icon, Modal,
  PrimaryButton, ProgressBar, Select, Spinner, TextArea, TextInput, Toggle,
  relativeTime,
} from './ui'
import type { ChipTone } from './ui'
import { FieldRow } from './settings'

const ACTIVE = ['QUEUED', 'RUNNING']
const CONTENT_WIDTH = 900

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

const ROLE_TONE: Record<string, string> = {
  fact: 'var(--accent)',
  dimension: 'var(--green)',
  bridge: 'var(--amber)',
  lookup: 'var(--text-faint)',
  unknown: 'var(--border-strong)',
}

/** What each table's kind is called, and the chip tone that carries it.
 *
 *  The kind used to be encoded only as the 3px stripe down the left of a
 *  collapsed row — a colour with no legend, so the one thing a reader most
 *  wants while scanning ("which of these are facts?") was the one thing they
 *  had to open every row to find out. The stripe stays; this names it. */
const ROLE_META: Record<string, { label: string; tone: ChipTone }> = {
  fact: { label: 'Fact', tone: 'accent' },
  dimension: { label: 'Dimension', tone: 'green' },
  bridge: { label: 'Bridge', tone: 'amber' },
  lookup: { label: 'Lookup', tone: 'neutral' },
  unknown: { label: 'Kind not set', tone: 'neutral' },
}

/** The same idea one level down: a column's role, visible without opening it. */
const COLUMN_ROLE_TONE: Record<string, ChipTone> = {
  key: 'neutral',
  time: 'amber',
  dimension: 'green',
  measure: 'accent',
  attribute: 'neutral',
}

type Filter = 'all' | 'review' | 'metrics' | 'issues'

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
  const [baseline, setBaseline] = useState('')
  const [job, setJob] = useState<SemanticJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [askGenerate, setAskGenerate] = useState(false)
  const [askDelete, setAskDelete] = useState(false)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [open, setOpen] = useState<Record<string, boolean>>({})

  const dirty = doc !== null && JSON.stringify(doc) !== baseline

  // Read inside `load` without making it depend on `baseline`, which would
  // rebuild the callback on every keystroke.
  const baselineRef = useRef('')
  useEffect(() => {
    baselineRef.current = baseline
  }, [baseline])

  const load = useCallback(async () => {
    const next = await api.get(connection.id)
    setLayer(next)
    setJob(next.job)
    // A reload mid-edit would silently discard what the user has typed, so
    // the document is only adopted when there is nothing unsaved to lose.
    setDoc((current) =>
      current !== null && JSON.stringify(current) !== baselineRef.current
        ? current
        : next.document,
    )
    setBaseline(JSON.stringify(next.document))
    return next
  }, [connection.id])

  useEffect(() => {
    setLoading(true)
    setDoc(null)
    setBaseline('')
    baselineRef.current = ''
    load()
      .catch(() => setError('Could not load the semantic layer.'))
      .finally(() => setLoading(false))
  }, [connection.id])

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

  async function save() {
    if (!doc) return
    setSaving(true)
    setError(null)
    try {
      const next = await api.save(connection.id, doc)
      setLayer(next)
      setDoc(next.document)
      setBaseline(JSON.stringify(next.document))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save this layer.')
    } finally {
      setSaving(false)
    }
  }

  async function startGeneration(payload: {
    llm_config_id: string
    mode: 'MERGE' | 'REPLACE'
    only_tables?: string[]
  }) {
    setError(null)
    try {
      setJob(await api.generate(connection.id, payload))
      setAskGenerate(false)
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
    await load()
  }

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
      return true
    })
  }, [doc, search, filter])

  const undescribed = useMemo(
    () => (layer?.tables ?? []).filter((t) => !t.described).map((t) => t.table),
    [layer],
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

  return (
    <>
      <Shell padBottom={dirty}>
        {error && <ErrorNote>{error}</ErrorNote>}

        <Hero
          layer={layer}
          connection={connection}
          running={running}
          job={job}
          onGenerate={() => setAskGenerate(true)}
          onDelete={() => setAskDelete(true)}
          onCancel={cancelGeneration}
          onToggle={(value) => onConnectionChange({ semantic_layer_enabled: value })}
          onFocusFilter={(next) => {
            setFilter(next)
            setSearch('')
          }}
        />

        {layer?.stale && !running && (
          <Note tone="amber">
            The schema has been re-synced since this layer was written. Anything
            that no longer matches is flagged below, and is already being kept
            out of the model's prompt.
          </Note>
        )}

        {!empty && (
          <>
            {/* The two panels that describe the whole database sit together,
                above the per-table list. "Business terms" used to sit below
                it — under forty-odd rows, several of them expandable — which
                put a document-level section behind the entire working surface
                and made it read as an appendix to the last table rather than a
                peer of "About this database". It also fell under the filter
                bar, whose search and filters never applied to it. */}
            <Overview doc={doc!} onChange={patch} />
            <Glossary doc={doc!} onChange={patch} />

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
              }}
              shown={entities.length}
            />

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {entities.map((entity) => (
                <EntityCard
                  key={entity.table}
                  connectionId={connection.id}
                  entity={entity}
                  open={!!open[entity.table]}
                  onToggle={() =>
                    setOpen((prev) => ({ ...prev, [entity.table]: !prev[entity.table] }))
                  }
                  onChange={(change) => updateEntity(entity.table, change)}
                />
              ))}
              {entities.length === 0 && (
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
      </Shell>


      {dirty && (
        <SaveBar
          saving={saving}
          onSave={save}
          onDiscard={() => setDoc(JSON.parse(baseline) as SemanticDocument)}
        />
      )}

      {askGenerate && (
        <GenerateModal
          layer={layer}
          undescribed={undescribed}
          onClose={() => setAskGenerate(false)}
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
/** The scroll container. Content is capped rather than stretched: a 1600px
 *  form field is unreadable, and every other detail tab already caps. */
function Shell({
  children, padBottom,
}: {
  children: React.ReactNode
  padBottom?: boolean
}) {
  return (
    <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
      <div
        style={{
          maxWidth: CONTENT_WIDTH,
          margin: '0 auto',
          // Extra room so the floating save bar never covers the last card.
          padding: `24px 28px ${padBottom ? 96 : 32}px`,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}
      >
        {children}
      </div>
    </div>
  )
}

// ── hero ───────────────────────────────────────────────────────────────────
function Hero({
  layer, connection, running, job, onGenerate, onDelete, onCancel, onToggle,
  onFocusFilter,
}: {
  layer: SemanticLayer | null
  connection: Connection
  running: boolean
  job: SemanticJob | null
  onGenerate: () => void
  onDelete: () => void
  onCancel: () => void
  onToggle: (value: boolean) => void
  onFocusFilter: (next: Filter) => void
}) {
  const exists = !!layer?.exists
  const model = layer?.model_snapshot?.model as string | undefined
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
          {exists ? (
            <GhostButton onClick={onGenerate} disabled={running}>
              <Icon.Sparkle size={14} />
              Regenerate
            </GhostButton>
          ) : (
            <PrimaryButton onClick={onGenerate} disabled={running}>
              <Icon.Sparkle size={14} />
              Generate with AI
            </PrimaryButton>
          )}
          {exists && (
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
            You can leave this page — generation carries on and the result is
            saved when it finishes.
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
          <Note tone="amber">Generation was stopped. Nothing was saved.</Note>
        </div>
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
            <Stat
              value={layer!.issue_count}
              label="need attention"
              tone={layer!.issue_count > 0 ? 'red' : 'neutral'}
              onClick={layer!.issue_count > 0 ? () => onFocusFilter('issues') : undefined}
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
              label={
                connection.semantic_layer_enabled
                  ? 'Sent to the model'
                  : 'Not sent to the model'
              }
              hint="Turn off to write SQL from the bare schema — the way to check whether this layer is helping."
            />
            <span
              style={{
                marginLeft: 'auto',
                fontSize: 11.5,
                color: 'var(--text-faint)',
                textAlign: 'right',
              }}
            >
              {layer!.generated_at
                ? `generated ${relativeTime(layer!.generated_at)}`
                : 'written by hand'}
              {model ? ` · ${model}` : ''}
              {layer!.edited_at ? ` · edited ${relativeTime(layer!.edited_at)}` : ''}
            </span>
          </div>
        </>
      )}
    </section>
  )
}

function Stat({
  value, label, hint, tone = 'neutral', onClick, last,
}: {
  value: number
  label: string
  hint?: string
  tone?: 'neutral' | 'green' | 'accent' | 'red'
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
        go back to being answered from the bare schema. You can generate a new
        layer at any time.
      </p>
    </Modal>
  )
}

// ── panels ─────────────────────────────────────────────────────────────────
/** A titled card. Local rather than `settings.Section` so the semantic tab can
 *  carry an action in the header without changing every other settings page.
 *
 *  Optionally collapsible: the two document-level panels sit above a list of
 *  however many tables the database has, and a filled-in one should state what
 *  it holds in a line rather than spend a screen of form between the reader and
 *  the tables they came for. Pass `summary` to make it collapsible. */
function Panel({
  title, description, summary, defaultOpen = true, action, children,
}: {
  title: string
  description?: string
  summary?: string
  defaultOpen?: boolean
  action?: React.ReactNode
  children: React.ReactNode
}) {
  const collapsible = summary !== undefined
  const [open, setOpen] = useState(defaultOpen)
  const shown = !collapsible || open

  // Open, the description explains what to write here; closed, the summary
  // reports what is already written. Never both.
  const titleBlock = (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontSize: 12.5,
          fontWeight: 700,
          color: 'var(--text-strong)',
        }}
      >
        {collapsible && <Icon.Chevron open={open} size={12} stroke="var(--text-dim)" />}
        {title}
      </div>
      {(shown ? description : summary) && (
        <div
          style={{
            fontSize: 11.5,
            color: 'var(--text-dim)',
            marginTop: 3,
            marginLeft: collapsible ? 20 : 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {shown ? description : summary}
        </div>
      )}
    </div>
  )

  return (
    <section
      style={{
        border: '1px solid var(--border)',
        borderRadius: 12,
        background: 'var(--panel)',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '13px 18px',
          borderBottom: shown ? '1px solid var(--border)' : 'none',
        }}
      >
        {collapsible ? (
          // The action stays outside the toggle: a button inside a button is
          // invalid, and "Add a term" must not also collapse the panel.
          <button
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              flex: 1,
              minWidth: 0,
              padding: 0,
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              textAlign: 'left',
            }}
          >
            {titleBlock}
          </button>
        ) : (
          titleBlock
        )}
        {shown && action}
      </div>
      {shown && (
        <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
          {children}
        </div>
      )}
    </section>
  )
}

function Note({ tone, children }: { tone: 'amber' | 'red'; children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 9,
        alignItems: 'flex-start',
        fontSize: 12.5,
        lineHeight: 1.55,
        color: `var(--${tone})`,
        background: `var(--${tone}-bg)`,
        border: `1px solid var(--${tone}-border)`,
        borderRadius: 9,
        padding: '11px 13px',
      }}
    >
      <span style={{ marginTop: 1, flexShrink: 0 }}>
        <Icon.Alert />
      </span>
      <span>{children}</span>
    </div>
  )
}

/** Floats clear of the content instead of eating a strip of the pane, so the
 *  last card is never half-hidden behind it. */
function SaveBar({
  saving, onSave, onDiscard,
}: {
  saving: boolean
  onSave: () => void
  onDiscard: () => void
}) {
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
        style={{
          pointerEvents: 'auto',
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          padding: '10px 12px 10px 18px',
          borderRadius: 12,
          background: 'var(--panel)',
          border: '1px solid var(--border-strong)',
          boxShadow: '0 18px 44px -18px rgba(0,0,0,0.6)',
        }}
      >
        <span style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
          Unsaved changes
        </span>
        <span style={{ display: 'flex', gap: 8 }}>
          <GhostButton onClick={onDiscard} disabled={saving} style={{ padding: '7px 12px' }}>
            Discard
          </GhostButton>
          <PrimaryButton onClick={onSave} disabled={saving} style={{ padding: '7px 14px' }}>
            {saving && <Spinner />}
            Save changes
          </PrimaryButton>
        </span>
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
    `${time.relative_windows === 'calendar' ? 'Calendar' : 'Rolling'} windows`,
    `FY from ${MONTHS[time.fiscal_year_start_month - 1] ?? '—'}`,
    `weeks from ${time.week_starts_on === 'monday' ? 'Mon' : 'Sun'}`,
  ].join(' · ')

  return (
    <Panel
      title="About this database"
      description="Sent with every question. Two or three sentences and the time conventions are worth more here than anything else on this page."
      summary={summary}
      // Unwritten context is the highest-value thing on the page, so an empty
      // one opens itself; a filled one gets out of the way of the tables.
      defaultOpen={!context}
    >
      <Field label="What this database is for">
        <TextArea
          value={doc.business_context}
          placeholder="An online retailer's order book: customers place orders made of line items, fulfilled from warehouses…"
          onChange={(e) => onChange({ ...doc, business_context: e.target.value })}
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

      <Field label="Other time conventions" hint="Optional. One sentence.">
        <TextInput
          value={time.notes}
          placeholder="Orders are timestamped when paid, not when placed."
          onChange={(e) => setTime({ notes: e.target.value })}
        />
      </Field>
    </Panel>
  )
}

// ── filters ────────────────────────────────────────────────────────────────
/** Sticks to the top of the scroll area: a 42-table schema scrolls past this
 *  in a second, and losing the search box is what makes a long list feel
 *  unmanageable. */
function FilterBar({
  value, onChange, search, onSearch, counts, shown,
}: {
  value: Filter
  onChange: (next: Filter) => void
  search: string
  onSearch: (next: string) => void
  counts: Record<Filter, number>
  shown: number
}) {
  const options: { value: Filter; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'review', label: 'Needs review' },
    { value: 'metrics', label: 'Has metrics' },
    { value: 'issues', label: 'Needs attention' },
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
        boxShadow: '0 10px 24px -18px rgba(0,0,0,0.55)',
      }}
    >
      <div
        style={{
          display: 'flex',
          gap: 2,
          background: 'var(--panel-alt)',
          borderRadius: 9,
          padding: 3,
        }}
      >
        {options.map((option) => {
          const active = option.value === value
          const count = counts[option.value]
          return (
            <button
              key={option.value}
              onClick={() => onChange(option.value)}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                fontSize: 12.5,
                fontWeight: 600,
                padding: '6px 11px',
                borderRadius: 6,
                cursor: 'pointer',
                border: 'none',
                color: active ? 'var(--text-strong)' : 'var(--text-dim)',
                background: active ? 'var(--panel)' : 'transparent',
                boxShadow: active ? '0 1px 3px rgba(0,0,0,0.10)' : 'none',
              }}
            >
              {option.label}
              <span
                style={{
                  fontSize: 10.5,
                  fontWeight: 700,
                  fontVariantNumeric: 'tabular-nums',
                  color:
                    option.value === 'issues' && count > 0
                      ? 'var(--red)'
                      : 'var(--text-faint)',
                }}
              >
                {count}
              </span>
            </button>
          )
        })}
      </div>

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

// ── one entity ─────────────────────────────────────────────────────────────
function EntityCard({
  connectionId, entity, open, onToggle, onChange,
}: {
  connectionId: string
  entity: SemanticEntity
  open: boolean
  onToggle: () => void
  onChange: (change: Partial<SemanticEntity>) => void
}) {
  const broken = hasIssue(entity)
  const role = ROLE_META[entity.role] ?? ROLE_META.unknown

  return (
    <div
      style={{
        border: `1px solid ${broken ? 'var(--red-border)' : 'var(--border)'}`,
        borderRadius: 11,
        background: 'var(--panel)',
        overflow: 'hidden',
        opacity: entity.exclude ? 0.6 : 1,
      }}
    >
      <button
        onClick={onToggle}
        aria-expanded={open}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 11,
          width: '100%',
          padding: '12px 14px',
          background: 'transparent',
          border: 'none',
          borderLeft: `3px solid ${ROLE_TONE[entity.role] ?? ROLE_TONE.unknown}`,
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <Icon.Chevron open={open} size={13} stroke="var(--text-dim)" />

        <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0, flex: 1 }}>
          <span style={{ display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 }}>
            <span
              style={{
                fontSize: 13.5,
                fontWeight: 600,
                color: 'var(--text-strong)',
                whiteSpace: 'nowrap',
              }}
            >
              {entity.label || entity.table.split('.').slice(-1)[0]}
            </span>
            <span
              className="mono"
              style={{
                fontSize: 11,
                color: 'var(--text-faint)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {entity.table}
            </span>
          </span>
          <span
            style={{
              display: 'flex',
              alignItems: 'baseline',
              gap: 8,
              minWidth: 0,
              fontSize: 11.5,
            }}
          >
            <span
              style={{
                color: entity.grain ? 'var(--text-dim)' : 'var(--text-faint)',
                fontStyle: entity.grain ? 'normal' : 'italic',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {entity.grain || 'no grain described yet'}
            </span>
            {/* What is inside, without opening it. Quiet text rather than more
                chips — the chips to the right are for the exceptions. */}
            <span
              style={{ color: 'var(--text-faint)', whiteSpace: 'nowrap', flexShrink: 0 }}
            >
              {entity.columns.length > 0 && `${entity.columns.length} cols`}
              {entity.columns.length > 0 && entity.metrics.length > 0 && ' · '}
              {entity.metrics.length > 0 &&
                `${entity.metrics.length} ${entity.metrics.length === 1 ? 'metric' : 'metrics'}`}
            </span>
          </span>
        </span>

        <span style={{ display: 'flex', gap: 6, flexShrink: 0, alignItems: 'center' }}>
          <Chip tone={role.tone}>{role.label}</Chip>
          {entity.exclude && <Chip>hidden</Chip>}
          {broken && <Chip tone="red">needs attention</Chip>}
          {entity.provenance.reviewed && <Chip tone="accent">reviewed</Chip>}
        </span>
      </button>

      {open && (
        <div
          style={{
            borderTop: '1px solid var(--border)',
            padding: 18,
            display: 'flex',
            flexDirection: 'column',
            gap: 18,
            background: 'var(--panel)',
          }}
        >
          {entity.issue && <Note tone="amber">{entity.issue}</Note>}

          <Group title="Meaning">
            <FieldRow>
              <Field label="Business name">
                <TextInput
                  value={entity.label}
                  placeholder="Orders"
                  onChange={(e) => onChange({ label: e.target.value })}
                />
              </Field>
              <Field label="Also called" hint="Comma separated.">
                <TextInput
                  value={entity.synonyms.join(', ')}
                  placeholder="purchases, sales orders"
                  onChange={(e) => onChange({ synonyms: splitList(e.target.value) })}
                />
              </Field>
            </FieldRow>

            <Field
              label="One row is…"
              hint="The most valuable sentence here — it is what stops a join from double-counting."
            >
              <TextInput
                value={entity.grain}
                placeholder="one row per line item on an order"
                onChange={(e) => onChange({ grain: e.target.value })}
              />
            </Field>

            <Field label="Description">
              <TextArea
                value={entity.description}
                placeholder="What this table records, and when a row appears."
                onChange={(e) => onChange({ description: e.target.value })}
              />
            </Field>

            <FieldRow>
              <Field label="Kind of table">
                <Select
                  value={entity.role}
                  onChange={(e) =>
                    onChange({ role: e.target.value as SemanticEntity['role'] })
                  }
                >
                  <option value="unknown">Not specified</option>
                  <option value="fact">Fact — events or transactions</option>
                  <option value="dimension">Dimension — things being described</option>
                  <option value="bridge">Bridge — joins two others</option>
                  <option value="lookup">Lookup — reference codes</option>
                </Select>
              </Field>
              <Field
                label="Date column"
                hint="Which column answers “when did this happen”."
              >
                <TextInput
                  className="mono"
                  value={entity.default_time_column}
                  placeholder="ordered_at"
                  onChange={(e) => onChange({ default_time_column: e.target.value })}
                />
              </Field>
            </FieldRow>
          </Group>

          <Columns entity={entity} onChange={(columns) => onChange({ columns })} />

          <Metrics
            connectionId={connectionId}
            entity={entity}
            onChange={(metrics) => onChange({ metrics })}
          />

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
              gap: 14,
              paddingTop: 14,
              borderTop: '1px solid var(--border)',
            }}
          >
            <Toggle
              checked={entity.provenance.reviewed}
              onChange={(reviewed) =>
                onChange({ provenance: { ...entity.provenance, reviewed } })
              }
              label="Reviewed"
              hint="You have checked this description is true."
            />
            <Toggle
              checked={entity.exclude}
              onChange={(exclude) => onChange({ exclude })}
              label="Hide from the model"
              hint="For deprecated or staging tables."
            />
          </div>
        </div>
      )}
    </div>
  )
}

/** A labelled band inside an expanded entity. Keeps a long form readable as
 *  three or four parts rather than one wall of inputs. */
function Group({
  title, hint, count, action, children,
}: {
  title: string
  hint?: string
  count?: number
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-end',
          gap: 12,
          // A rule under the heading, so an expanded table reads as three
          // labelled regions instead of one continuous run of inputs. The
          // heading itself is ordinary sentence case at readable size: the
          // old 10.5px uppercase whisper was quieter than the field labels
          // beneath it, which inverted the hierarchy.
          borderBottom: '1px solid var(--border)',
          paddingBottom: 8,
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'baseline',
              gap: 7,
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--text-strong)',
            }}
          >
            {title}
            {count !== undefined && (
              <span style={{ fontSize: 11.5, fontWeight: 500, color: 'var(--text-faint)' }}>
                {count}
              </span>
            )}
          </div>
          {hint && (
            <div
              style={{
                fontSize: 11.5,
                lineHeight: 1.5,
                color: 'var(--text-dim)',
                marginTop: 4,
              }}
            >
              {hint}
            </div>
          )}
        </div>
        {action}
      </div>
      {children}
    </div>
  )
}

// ── columns ────────────────────────────────────────────────────────────────
function Columns({
  entity, onChange,
}: {
  entity: SemanticEntity
  onChange: (columns: SemanticColumn[]) => void
}) {
  const [adding, setAdding] = useState('')
  // Collapsed by default, and several may be open at once — the same rule the
  // table list above follows, so the two levels behave alike.
  const [open, setOpen] = useState<Set<number>>(new Set())

  function toggle(index: number) {
    setOpen((prev) => {
      const next = new Set(prev)
      if (!next.delete(index)) next.add(index)
      return next
    })
  }

  function update(index: number, change: Partial<SemanticColumn>) {
    onChange(
      entity.columns.map((c, i) =>
        i === index
          ? { ...c, ...change, provenance: { ...c.provenance, edited: true, source: 'human' } }
          : c,
      ),
    )
  }

  function add() {
    const name = adding.trim()
    if (!name) return
    onChange([...entity.columns, blankColumn(name)])
    // Open what was just added — a new row that arrives collapsed and empty
    // looks like the button did nothing.
    setOpen((prev) => new Set(prev).add(entity.columns.length))
    setAdding('')
  }

  return (
    <Group
      title="Columns worth explaining"
      count={entity.columns.length}
      hint="Only the ones whose name is not self-evident — codes, units, abbreviations."
    >
      {entity.columns.map((column, index) => (
        <SubCard key={`${column.name}-${index}`} invalid={!column.valid} compact={!open.has(index)}>
          <SubCardHead
            title={column.name}
            mono
            open={open.has(index)}
            onToggle={() => toggle(index)}
            summary={column.label || column.description}
            badge={
              !column.valid ? (
                <Chip tone="red">{column.issue}</Chip>
              ) : column.role !== 'attribute' ? (
                // "attribute" is the default and sits on most rows; a chip
                // repeating it on every line is noise, not information.
                <Chip tone={COLUMN_ROLE_TONE[column.role] ?? 'neutral'}>{column.role}</Chip>
              ) : null
            }
            onRemove={() => onChange(entity.columns.filter((_, i) => i !== index))}
            removeLabel={`Remove ${column.name}`}
          />
          {open.has(index) && (
            <>
          <FieldRow columns={3}>
            <Field label="Label">
              <TextInput
                value={column.label}
                onChange={(e) => update(index, { label: e.target.value })}
              />
            </Field>
            <Field label="Role">
              <Select
                value={column.role}
                onChange={(e) =>
                  update(index, { role: e.target.value as SemanticColumn['role'] })
                }
              >
                <option value="attribute">Attribute</option>
                <option value="key">Key</option>
                <option value="time">Time</option>
                <option value="dimension">Dimension</option>
                <option value="measure">Measure</option>
              </Select>
            </Field>
            <Field label="Unit" hint="USD, cents, days…">
              <TextInput
                value={column.unit}
                onChange={(e) => update(index, { unit: e.target.value })}
              />
            </Field>
          </FieldRow>
          <Field label="What it means">
            <TextInput
              value={column.description}
              onChange={(e) => update(index, { description: e.target.value })}
            />
          </Field>
          {Object.keys(column.value_meanings).length > 0 && (
            <Field
              label="Value meanings"
              hint="One per line, as CODE = meaning. Only values present in the schema snapshot are kept."
            >
              <TextArea
                className="mono"
                value={Object.entries(column.value_meanings)
                  .map(([k, v]) => `${k} = ${v}`)
                  .join('\n')}
                onChange={(e) =>
                  update(index, { value_meanings: parseMeanings(e.target.value) })
                }
                style={{ fontSize: 12.5, minHeight: 56 }}
              />
            </Field>
          )}
            </>
          )}
        </SubCard>
      ))}

      <div style={{ display: 'flex', gap: 8 }}>
        <TextInput
          className="mono"
          placeholder="column_name"
          value={adding}
          onChange={(e) => setAdding(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add()
            }
          }}
          style={{ maxWidth: 240, fontSize: 13, padding: '7px 10px' }}
        />
        <GhostButton
          disabled={!adding.trim()}
          onClick={add}
          style={{ padding: '7px 12px', fontSize: 12.5 }}
        >
          <Icon.Plus size={13} />
          Add column
        </GhostButton>
      </div>
    </Group>
  )
}

// ── metrics ────────────────────────────────────────────────────────────────
function Metrics({
  connectionId, entity, onChange,
}: {
  connectionId: string
  entity: SemanticEntity
  onChange: (metrics: SemanticMetric[]) => void
}) {
  const [open, setOpen] = useState<Set<number>>(new Set())

  function toggle(index: number) {
    setOpen((prev) => {
      const next = new Set(prev)
      if (!next.delete(index)) next.add(index)
      return next
    })
  }

  function update(index: number, change: Partial<SemanticMetric>) {
    onChange(
      entity.metrics.map((m, i) =>
        i === index
          ? { ...m, ...change, provenance: { ...m.provenance, edited: true, source: 'human' } }
          : m,
      ),
    )
  }

  return (
    <Group
      title="Metrics"
      count={entity.metrics.length}
      hint="The part that changes answers: a named measure bound to exact SQL, including the filters that belong to the definition rather than the question."
      action={
        <GhostButton
          onClick={() => {
            setOpen((prev) => new Set(prev).add(entity.metrics.length))
            onChange([...entity.metrics, blankMetric()])
          }}
          style={{ padding: '6px 11px', fontSize: 12.5, flexShrink: 0 }}
        >
          <Icon.Plus size={13} />
          Add metric
        </GhostButton>
      }
    >
      {entity.metrics.length === 0 && (
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
          No metrics on this table. Lookup and bridge tables usually have none.
        </div>
      )}
      {entity.metrics.map((metric, index) => (
        <MetricCard
          key={index}
          connectionId={connectionId}
          table={entity.table}
          metric={metric}
          open={open.has(index)}
          onToggle={() => toggle(index)}
          onChange={(change) => update(index, change)}
          onRemove={() => onChange(entity.metrics.filter((_, i) => i !== index))}
        />
      ))}
    </Group>
  )
}

function MetricCard({
  connectionId, table, metric, open, onToggle, onChange, onRemove,
}: {
  connectionId: string
  table: string
  metric: SemanticMetric
  open: boolean
  onToggle: () => void
  onChange: (change: Partial<SemanticMetric>) => void
  onRemove: () => void
}) {
  // Server-side validation, debounced: the browser cannot know the dialect or
  // the schema, and a second opinion here would only be wrong differently.
  const [check, setCheck] = useState<{ valid: boolean; issue: string } | null>(null)
  const [checking, setChecking] = useState(false)

  useEffect(() => {
    // Only while the metric is open for editing. Collapsed, the document's own
    // stored validity is already the answer, and re-checking every metric on a
    // table the moment it expands spent a round trip per metric to redisplay
    // what the server had just sent.
    if (!open || !metric.expression.trim()) {
      setCheck(null)
      return
    }
    let cancelled = false
    setChecking(true)
    const timer = setTimeout(async () => {
      try {
        const result = await api.check(connectionId, {
          table,
          expression: metric.expression,
          required_joins: metric.required_joins,
        })
        if (!cancelled) setCheck(result)
      } catch {
        if (!cancelled) setCheck(null)
      } finally {
        if (!cancelled) setChecking(false)
      }
    }, 500)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [open, connectionId, table, metric.expression, metric.required_joins.join(',')])

  const state = check ?? (metric.valid ? null : { valid: false, issue: metric.issue })

  return (
    <SubCard invalid={!!state && !state.valid} compact={!open}>
      <SubCardHead
        title={metric.label || metric.name || 'New metric'}
        open={open}
        onToggle={onToggle}
        summary={metric.expression}
        badge={
          state && !state.valid ? (
            <Chip tone="red">invalid</Chip>
          ) : metric.unit ? (
            <Chip>{metric.unit}</Chip>
          ) : null
        }
        onRemove={onRemove}
        removeLabel="Remove metric"
      />

      {!open ? null : (
        <>
      <FieldRow>
        <Field label="Identifier" hint="snake_case, unique on this table.">
          <TextInput
            className="mono"
            value={metric.name}
            placeholder="net_revenue"
            onChange={(e) => onChange({ name: slug(e.target.value) })}
          />
        </Field>
        <Field label="What a person calls it">
          <TextInput
            value={metric.label}
            placeholder="Net revenue"
            onChange={(e) => onChange({ label: e.target.value })}
          />
        </Field>
      </FieldRow>

      <Field
        label="SQL expression"
        hint={`An aggregate over ${table}. Qualify columns fully.`}
      >
        <TextArea
          className="mono"
          value={metric.expression}
          placeholder={`SUM(${table}.amount)`}
          onChange={(e) => onChange({ expression: e.target.value })}
          style={{ minHeight: 56, fontSize: 12.5 }}
        />
      </Field>

      <CheckLine checking={checking} state={state} />

      <Field
        label="Filters that are part of the definition"
        hint="One per line. These always apply, whatever the question asks."
      >
        <TextArea
          className="mono"
          value={metric.filters.join('\n')}
          placeholder={`${table}.status <> 'CANCELLED'`}
          onChange={(e) =>
            onChange({
              filters: e.target.value.split('\n').map((l) => l.trim()).filter(Boolean),
            })
          }
          style={{ minHeight: 48, fontSize: 12.5 }}
        />
      </Field>

      <FieldRow columns={3}>
        <Field label="Rolls up" hint="Can it be summed?">
          <Select
            value={metric.additive}
            onChange={(e) =>
              onChange({ additive: e.target.value as SemanticMetric['additive'] })
            }
          >
            <option value="additive">Yes, across everything</option>
            <option value="semi_additive">Not across time (a balance)</option>
            <option value="non_additive">No (a ratio or average)</option>
          </Select>
        </Field>
        <Field label="Unit">
          <TextInput
            value={metric.unit}
            placeholder="USD"
            onChange={(e) => onChange({ unit: e.target.value })}
          />
        </Field>
        <Field label="Needs joins to" hint="Comma separated, qualified.">
          <TextInput
            className="mono"
            value={metric.required_joins.join(', ')}
            onChange={(e) => onChange({ required_joins: splitList(e.target.value) })}
          />
        </Field>
      </FieldRow>

      <FieldRow>
        <Field label="Asked for as" hint="Comma separated synonyms.">
          <TextInput
            value={metric.synonyms.join(', ')}
            placeholder="revenue, GMV, net sales"
            onChange={(e) => onChange({ synonyms: splitList(e.target.value) })}
          />
        </Field>
        <Field label="Description">
          <TextInput
            value={metric.description}
            onChange={(e) => onChange({ description: e.target.value })}
          />
        </Field>
      </FieldRow>
        </>
      )}
    </SubCard>
  )
}

function CheckLine({
  checking, state,
}: {
  checking: boolean
  state: { valid: boolean; issue: string } | null
}) {
  if (checking) {
    return (
      <Line color="var(--text-faint)">
        <Spinner size={12} />
        Checking against your schema…
      </Line>
    )
  }
  if (!state) return null
  if (!state.valid) {
    return (
      <Line color="var(--red)">
        <Icon.Alert size={13} />
        {state.issue}
      </Line>
    )
  }
  if (state.issue) {
    return (
      <Line color="var(--amber)">
        <Icon.Alert size={13} />
        {state.issue}
      </Line>
    )
  }
  return (
    <Line color="var(--green)">
      <Icon.Check size={13} />
      Resolves against your schema.
    </Line>
  )
}

function Line({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 12,
        color,
        marginTop: -4,
      }}
    >
      {children}
    </div>
  )
}

// ── glossary ───────────────────────────────────────────────────────────────
function Glossary({
  doc, onChange,
}: {
  doc: SemanticDocument
  onChange: (next: SemanticDocument) => void
}) {
  function setTerms(glossary: GlossaryTerm[]) {
    onChange({ ...doc, glossary })
  }
  function update(index: number, change: Partial<GlossaryTerm>) {
    setTerms(
      doc.glossary.map((term, i) =>
        i === index
          ? { ...term, ...change, provenance: { ...term.provenance, edited: true, source: 'human' } }
          : term,
      ),
    )
  }

  const named = doc.glossary.map((t) => t.term.trim()).filter(Boolean)

  return (
    <Panel
      title="Business terms"
      description="Words a user will type that are not the name of a table or a metric — “churn”, “active customer”, “AOV”."
      summary={
        named.length === 0
          ? 'No terms yet'
          : `${named.length} ${named.length === 1 ? 'term' : 'terms'} — ${named.slice(0, 6).join(', ')}${named.length > 6 ? '…' : ''}`
      }
      defaultOpen={false}
      action={
        <GhostButton
          onClick={() =>
            setTerms([
              ...doc.glossary,
              {
                term: '',
                meaning: '',
                maps_to: [],
                provenance: { source: 'human', edited: true, reviewed: false },
              },
            ])
          }
          style={{ padding: '6px 11px', fontSize: 12.5, flexShrink: 0 }}
        >
          <Icon.Plus size={13} />
          Add a term
        </GhostButton>
      }
    >
      {doc.glossary.length === 0 && (
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
          No terms yet. Add one when a word your team uses has no table behind it.
        </div>
      )}
      {doc.glossary.map((term, index) => (
        <div
          key={index}
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(120px, 1fr) minmax(180px, 2fr) minmax(120px, 1fr) 30px',
            gap: 10,
            alignItems: 'end',
          }}
        >
          <Field label="Term">
            <TextInput
              value={term.term}
              placeholder="churn"
              onChange={(e) => update(index, { term: e.target.value })}
            />
          </Field>
          <Field label="Means">
            <TextInput
              value={term.meaning}
              placeholder="a customer with no order in the last 90 days"
              onChange={(e) => update(index, { meaning: e.target.value })}
            />
          </Field>
          <Field label="Maps to">
            <TextInput
              className="mono"
              placeholder="public.customers"
              value={term.maps_to.join(', ')}
              onChange={(e) => update(index, { maps_to: splitList(e.target.value) })}
            />
          </Field>
          <div style={{ paddingBottom: 1 }}>
            <IconButton
              label={`Remove ${term.term || 'term'}`}
              onClick={() => setTerms(doc.glossary.filter((_, i) => i !== index))}
            >
              <Icon.Trash />
            </IconButton>
          </div>
        </div>
      ))}
    </Panel>
  )
}

// ── generate modal ─────────────────────────────────────────────────────────
function GenerateModal({
  layer, undescribed, onClose, onStart,
}: {
  layer: SemanticLayer | null
  undescribed: string[]
  onClose: () => void
  onStart: (payload: {
    llm_config_id: string
    mode: 'MERGE' | 'REPLACE'
    only_tables?: string[]
  }) => void
}) {
  const [configs, setConfigs] = useState<LlmConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [configId, setConfigId] = useState('')
  const [mode, setMode] = useState<'MERGE' | 'REPLACE'>('MERGE')
  const [scope, setScope] = useState<'all' | 'missing'>(
    undescribed.length > 0 && layer?.exists ? 'missing' : 'all',
  )
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    llmApi
      .list()
      .then((items) => {
        setConfigs(items)
        // A model that has been reached is a better default than the first
        // row: generation is dozens of calls, and failing on all of them
        // because the key is wrong is an expensive way to find out.
        const reachable = items.find((c) => c.status === 'OK')
        setConfigId((reachable ?? items[0])?.id ?? '')
      })
      .catch(() => setConfigs([]))
      .finally(() => setLoading(false))
  }, [])

  const total = layer?.tables.length ?? 0
  const count = scope === 'missing' ? undescribed.length : total

  return (
    <Modal
      title="Generate a semantic layer"
      subtitle="A model reads your schema table by table and writes what each one means."
      onClose={onClose}
      width={600}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton
            disabled={!configId || count === 0 || starting}
            onClick={() => {
              setStarting(true)
              onStart({
                llm_config_id: configId,
                mode,
                only_tables: scope === 'missing' ? undescribed : [],
              })
            }}
          >
            {starting && <Spinner />}
            Describe {count} {count === 1 ? 'table' : 'tables'}
          </PrimaryButton>
        </>
      }
    >
      <ModalGroup
        title="Model"
        hint="Worth spending your best model here: this runs once, and every question afterwards reads what it wrote."
      >
        {loading ? (
          <Line color="var(--text-faint)">
            <Spinner size={12} />
            Loading your models…
          </Line>
        ) : configs.length === 0 ? (
          <Note tone="amber">
            Add a model under <strong>Models</strong> before generating a
            semantic layer.
          </Note>
        ) : (
          <div
            role="radiogroup"
            aria-label="Model"
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
              maxHeight: 232,
              overflowY: 'auto',
            }}
          >
            {configs.map((config) => (
              <ModelOption
                key={config.id}
                config={config}
                selected={config.id === configId}
                onSelect={() => setConfigId(config.id)}
              />
            ))}
          </div>
        )}
      </ModalGroup>

      {layer?.exists && (
        <>
          <ModalGroup title="How much to describe">
            <ChoiceRow
              value={scope}
              onChange={(next) => setScope(next as 'all' | 'missing')}
              options={[
                {
                  value: 'missing',
                  label: 'Only what is missing',
                  hint: `${undescribed.length} ${undescribed.length === 1 ? 'table' : 'tables'}`,
                  disabled: undescribed.length === 0,
                },
                {
                  value: 'all',
                  label: 'Every table',
                  hint: `${total} tables`,
                },
              ]}
            />
          </ModalGroup>

          <ModalGroup title="What happens to what is already there">
            <ChoiceRow
              value={mode}
              onChange={(next) => setMode(next as 'MERGE' | 'REPLACE')}
              options={[
                {
                  value: 'MERGE',
                  label: 'Keep my edits',
                  hint: 'Refresh the rest',
                },
                {
                  value: 'REPLACE',
                  label: 'Start over',
                  hint: 'Discard everything',
                  tone: 'red',
                },
              ]}
            />
          </ModalGroup>
        </>
      )}

      <div
        style={{
          fontSize: 12,
          lineHeight: 1.6,
          color: 'var(--text-dim)',
          background: 'var(--panel-alt)',
          border: '1px solid var(--border)',
          borderRadius: 9,
          padding: '11px 13px',
        }}
      >
        One model call per table, four at a time — roughly{' '}
        <strong style={{ color: 'var(--text-strong)' }}>
          {estimateMinutes(count)}
        </strong>{' '}
        for {count} {count === 1 ? 'table' : 'tables'}. The model sees the same
        schema detail it already sees when answering a question, so this shares
        nothing new with your provider. Nothing is saved until it finishes.
      </div>
    </Modal>
  )
}

/** A model, shown as the thing you actually choose on: which model id, and
 *  whether it has ever been reached. A `<select>` hides both. */
function ModelOption({
  config, selected, onSelect,
}: {
  config: LlmConfig
  selected: boolean
  onSelect: () => void
}) {
  const [hover, setHover] = useState(false)
  const ok = config.status === 'OK'
  return (
    <button
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 11,
        width: '100%',
        padding: '11px 13px',
        textAlign: 'left',
        borderRadius: 9,
        cursor: 'pointer',
        background: selected ? 'var(--accent-bg)' : 'transparent',
        border: `1px solid ${
          selected
            ? 'var(--accent)'
            : hover
              ? 'var(--border-strong)'
              : 'var(--border)'
        }`,
      }}
    >
      <span
        style={{
          width: 15,
          height: 15,
          borderRadius: '50%',
          flexShrink: 0,
          border: `1.5px solid ${selected ? 'var(--accent)' : 'var(--border-strong)'}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {selected && (
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: '50%',
              background: 'var(--accent)',
            }}
          />
        )}
      </span>

      <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
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
          {config.name}
        </span>
        <span
          className="mono"
          style={{
            fontSize: 11,
            color: 'var(--text-faint)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {config.model}
        </span>
      </span>

      <span style={{ flexShrink: 0, display: 'flex', gap: 6, alignItems: 'center' }}>
        <Chip>temp {config.temperature}</Chip>
        <Chip tone={ok ? 'green' : 'neutral'}>{ok ? 'reachable' : 'untested'}</Chip>
      </span>
    </button>
  )
}

function ModalGroup({
  title, hint, children,
}: {
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-strong)' }}>
          {title}
        </div>
        {hint && (
          <div
            style={{
              fontSize: 11.5,
              lineHeight: 1.5,
              color: 'var(--text-dim)',
              marginTop: 3,
            }}
          >
            {hint}
          </div>
        )}
      </div>
      {children}
    </div>
  )
}

/** Two or three mutually exclusive options, laid out as cards. Better than a
 *  `<select>` when the options need a line of explanation each — which is
 *  exactly when the choice is worth making carefully. */
function ChoiceRow({
  value, onChange, options,
}: {
  value: string
  onChange: (next: string) => void
  options: {
    value: string
    label: string
    hint?: string
    tone?: 'red'
    disabled?: boolean
  }[]
}) {
  return (
    <div
      role="radiogroup"
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))`,
        gap: 8,
      }}
    >
      {options.map((option) => {
        const selected = option.value === value
        const accent = option.tone === 'red' ? 'var(--red)' : 'var(--accent)'
        return (
          <button
            key={option.value}
            role="radio"
            aria-checked={selected}
            disabled={option.disabled}
            onClick={() => onChange(option.value)}
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 2,
              padding: '10px 12px',
              borderRadius: 9,
              textAlign: 'left',
              cursor: option.disabled ? 'not-allowed' : 'pointer',
              opacity: option.disabled ? 0.45 : 1,
              background: selected
                ? option.tone === 'red'
                  ? 'var(--red-bg)'
                  : 'var(--accent-bg)'
                : 'transparent',
              border: `1px solid ${selected ? accent : 'var(--border)'}`,
            }}
          >
            <span
              style={{
                fontSize: 12.5,
                fontWeight: 600,
                color: selected ? accent : 'var(--text-strong)',
              }}
            >
              {option.label}
            </span>
            {option.hint && (
              <span style={{ fontSize: 11.5, color: 'var(--text-dim)' }}>
                {option.hint}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

// ── small pieces ───────────────────────────────────────────────────────────
function SubCard({
  invalid, compact, children,
}: {
  invalid?: boolean
  /** Collapsed to a single row: tighter, so a list of them reads as a list. */
  compact?: boolean
  children: React.ReactNode
}) {
  return (
    <div
      style={{
        border: `1px solid ${invalid ? 'var(--red-border)' : 'var(--border)'}`,
        borderRadius: 9,
        padding: compact ? '8px 12px' : 13,
        display: 'flex',
        flexDirection: 'column',
        gap: 11,
        background: 'var(--panel-alt)',
      }}
    >
      {children}
    </div>
  )
}

/** The head of a column or metric card, and the row it collapses to.
 *
 *  Every entry used to render its whole form at once, so opening a table with
 *  a dozen described columns produced a page of near-identical inputs with
 *  nothing to navigate by. Collapsed, each entry is one line — name, role,
 *  and whatever it means — and the form appears only for the one being
 *  edited. Same disclosure the table list above already uses, one level down. */
function SubCardHead({
  title, mono, badge, summary, open, onToggle, onRemove, removeLabel,
}: {
  title: string
  mono?: boolean
  badge?: React.ReactNode
  summary?: string
  open: boolean
  onToggle: () => void
  onRemove: () => void
  removeLabel: string
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <button
        onClick={onToggle}
        aria-expanded={open}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          flex: 1,
          minWidth: 0,
          padding: 0,
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <Icon.Chevron open={open} size={11} stroke="var(--text-faint)" />
        <span
          className={mono ? 'mono' : undefined}
          style={{
            fontSize: 12.5,
            fontWeight: 600,
            color: 'var(--text-strong)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            flexShrink: 0,
          }}
        >
          {title}
        </span>
        {badge}
        {!open && summary && (
          <span
            style={{
              fontSize: 11.5,
              color: 'var(--text-dim)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              minWidth: 0,
            }}
          >
            {summary}
          </span>
        )}
      </button>
      <span style={{ flexShrink: 0 }}>
        <IconButton label={removeLabel} onClick={onRemove}>
          <Icon.Trash />
        </IconButton>
      </span>
    </div>
  )
}

/** A quiet destructive action: neutral until hovered, then unmistakably red.
 *  Every use of it is behind a confirmation, which is where the real safety
 *  lives — a red panel parked in the page reads as a warning about the
 *  content, not about the button. */
function IconButton({
  label, onClick, children, size = 28,
}: {
  label: string
  onClick: () => void
  children: React.ReactNode
  size?: number
}) {
  const [hover, setHover] = useState(false)
  return (
    <button
      aria-label={label}
      title={label}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: size,
        height: size,
        borderRadius: size > 30 ? 8 : 7,
        border: `1px solid ${hover ? 'var(--red-border)' : 'var(--border-strong)'}`,
        background: hover ? 'var(--red-bg)' : 'transparent',
        color: hover ? 'var(--red)' : 'var(--text-faint)',
        cursor: 'pointer',
        flexShrink: 0,
      }}
    >
      {children}
    </button>
  )
}

function hasIssue(entity: SemanticEntity): boolean {
  return (
    !entity.valid ||
    entity.issue !== '' ||
    entity.metrics.some((m) => !m.valid) ||
    entity.columns.some((c) => !c.valid)
  )
}

function splitList(value: string): string[] {
  return value.split(',').map((s) => s.trim()).filter(Boolean)
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '')
}

function parseMeanings(value: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of value.split('\n')) {
    const [key, ...rest] = line.split('=')
    if (key?.trim() && rest.length > 0) out[key.trim()] = rest.join('=').trim()
  }
  return out
}

function estimateMinutes(tables: number): string {
  const seconds = Math.ceil((tables / 4) * 8) + 20
  if (seconds < 90) return 'under a minute'
  return `about ${Math.ceil(seconds / 60)} minutes`
}

function blankColumn(name: string): SemanticColumn {
  return {
    name,
    label: '',
    description: '',
    synonyms: [],
    role: 'attribute',
    unit: '',
    value_meanings: {},
    provenance: { source: 'human', edited: true, reviewed: false },
    valid: true,
    issue: '',
  }
}

function blankMetric(): SemanticMetric {
  return {
    name: '',
    label: '',
    description: '',
    synonyms: [],
    expression: '',
    filters: [],
    required_joins: [],
    additive: 'additive',
    unit: '',
    format: '',
    provenance: { source: 'human', edited: true, reviewed: false },
    valid: true,
    issue: '',
  }
}
