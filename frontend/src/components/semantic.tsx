/**
 * The semantic layer editor.
 *
 * Two jobs, and the design follows from the tension between them:
 *
 *  - **Generate.** A model describes the schema, table by table, over minutes.
 *    So the button opens a modal that makes the two decisions that cost money
 *    explicit (which model, how much of the schema), then hands over to a
 *    progress bar that says which table is being described right now.
 *  - **Edit.** What the model wrote is a draft. Everything is editable, an
 *    edit is marked so a later regeneration cannot silently overwrite it, and
 *    `Reviewed` is a deliberate act — the layer's authority over the SQL
 *    generator should be something a person granted, not something a model
 *    assumed.
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
  Chip, DangerButton, EmptyState, ErrorNote, Field, GhostButton, Icon,
  PrimaryButton, ProgressBar, Select, Spinner, TextArea, TextInput, Toggle,
  Modal, relativeTime,
} from './ui'
import { FieldRow, Section } from './settings'

const ACTIVE = ['QUEUED', 'RUNNING']

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

type Filter = 'all' | 'review' | 'metrics' | 'issues' | 'missing'

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
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [open, setOpen] = useState<Record<string, boolean>>({})

  const dirty = doc !== null && JSON.stringify(doc) !== baseline

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

  // Read inside `load` without making it depend on `baseline`, which would
  // rebuild the callback on every keystroke.
  const baselineRef = useRef('')
  useEffect(() => {
    baselineRef.current = baseline
  }, [baseline])

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
      if (filter === 'issues') {
        return (
          !entity.valid ||
          entity.issue !== '' ||
          entity.metrics.some((m) => !m.valid) ||
          entity.columns.some((c) => !c.valid)
        )
      }
      return true
    })
  }, [doc, search, filter])

  const undescribed = useMemo(
    () => (layer?.tables ?? []).filter((t) => !t.described).map((t) => t.table),
    [layer],
  )

  if (loading) {
    return (
      <Body>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--text-dim)', fontSize: 13 }}>
          <Spinner />
          Loading the semantic layer…
        </div>
      </Body>
    )
  }

  const running = job !== null && ACTIVE.includes(job.status)

  return (
    <>
      <Body>
        {error && <ErrorNote>{error}</ErrorNote>}

        <Toolbar
          layer={layer}
          connection={connection}
          running={running}
          onGenerate={() => setAskGenerate(true)}
          onToggle={(value) => onConnectionChange({ semantic_layer_enabled: value })}
        />

        {running && job && (
          <RunningPanel job={job} onCancel={cancelGeneration} />
        )}

        {!running && job && job.status !== 'SUCCEEDED' && (
          <FinishedBanner job={job} />
        )}

        {layer?.stale && (
          <Note tone="amber">
            The schema has been re-synced since this layer was written. Anything
            that no longer matches is flagged below and is already being kept out
            of the model's prompt.
          </Note>
        )}

        {!doc || doc.entities.length === 0 ? (
          <EmptyState
            title="No semantic layer yet"
            body="Your schema says what exists. A semantic layer says what it means — the business name of each table, what one row is, and the exact SQL behind measures like revenue. DataMind sends it alongside the schema so the model stops guessing."
            action={
              <PrimaryButton onClick={() => setAskGenerate(true)} disabled={running}>
                <Icon.Sparkle size={15} />
                Generate with AI
              </PrimaryButton>
            }
          />
        ) : (
          <>
            <Overview doc={doc} onChange={patch} />

            <Filters
              value={filter}
              onChange={setFilter}
              search={search}
              onSearch={setSearch}
              shown={entities.length}
              total={doc.entities.length}
              undescribed={undescribed.length}
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
                <p style={{ fontSize: 13, color: 'var(--text-dim)' }}>
                  Nothing matches this filter.
                </p>
              )}
            </div>

            <Glossary doc={doc} onChange={patch} />

            <Section
              title="Danger zone"
              description="Deleting the layer leaves the schema and your conversations untouched."
              danger
            >
              <DangerButton onClick={discardLayer} style={{ alignSelf: 'flex-start' }}>
                <Icon.Trash />
                Delete semantic layer
              </DangerButton>
            </Section>
          </>
        )}
      </Body>

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
    </>
  )
}

// ── chrome ─────────────────────────────────────────────────────────────────
function Body({ children }: { children: React.ReactNode }) {
  return (
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
      {children}
    </div>
  )
}

function Toolbar({
  layer, connection, running, onGenerate, onToggle,
}: {
  layer: SemanticLayer | null
  connection: Connection
  running: boolean
  onGenerate: () => void
  onToggle: (value: boolean) => void
}) {
  const model = layer?.model_snapshot?.model as string | undefined
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <GhostButton onClick={onGenerate} disabled={running}>
          <Icon.Sparkle size={15} />
          {layer?.exists ? 'Regenerate with AI' : 'Generate with AI'}
        </GhostButton>
        <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>
          {layer?.generated_at
            ? `generated ${relativeTime(layer.generated_at)}${model ? ` · ${model}` : ''}`
            : 'never generated'}
          {layer?.edited_at ? ` · edited ${relativeTime(layer.edited_at)}` : ''}
        </span>
        {layer && layer.entity_count > 0 && (
          <span style={{ display: 'flex', gap: 6, marginLeft: 'auto', flexWrap: 'wrap' }}>
            <Chip tone="accent">{layer.entity_count} described</Chip>
            <Chip tone="green">{layer.metric_count} metrics</Chip>
            <Chip tone={layer.reviewed_count > 0 ? 'green' : 'neutral'}>
              {layer.reviewed_count} reviewed
            </Chip>
            {layer.issue_count > 0 && (
              <Chip tone="red">{layer.issue_count} need attention</Chip>
            )}
          </span>
        )}
      </div>

      {layer?.exists && (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: 10,
            background: 'var(--panel)',
            padding: '12px 14px',
          }}
        >
          <Toggle
            checked={connection.semantic_layer_enabled}
            onChange={onToggle}
            label="Send this layer to the model"
            hint="Off keeps the layer but writes SQL from the bare schema — the way to check whether it is helping."
          />
        </div>
      )}
    </div>
  )
}

function RunningPanel({ job, onCancel }: { job: SemanticJob; onCancel: () => void }) {
  return (
    <div
      style={{
        border: '1px solid var(--accent)',
        borderRadius: 10,
        background: 'var(--accent-bg)',
        padding: '14px 16px',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Spinner />
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-strong)' }}>
          {job.status === 'QUEUED' ? 'Starting…' : 'Writing your semantic layer'}
        </span>
        <span style={{ marginLeft: 'auto' }}>
          <GhostButton onClick={onCancel} style={{ padding: '5px 11px', fontSize: 12 }}>
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
        You can leave this page — generation carries on and the result is saved
        when it finishes.
      </span>
    </div>
  )
}

function FinishedBanner({ job }: { job: SemanticJob }) {
  if (job.status === 'CANCELLED') {
    return <Note tone="amber">Generation was stopped. Nothing was saved.</Note>
  }
  return (
    <Note tone="red">
      {job.error_message ?? 'Generation failed.'}
    </Note>
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
      <span style={{ marginTop: 1 }}>
        <Icon.Alert />
      </span>
      <span>{children}</span>
    </div>
  )
}

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
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '12px 28px',
        borderTop: '1px solid var(--border-strong)',
        background: 'var(--panel)',
        boxShadow: '0 -6px 20px -12px rgba(0,0,0,0.5)',
      }}
    >
      <span style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
        You have unsaved changes.
      </span>
      <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
        <GhostButton onClick={onDiscard} disabled={saving}>
          Discard
        </GhostButton>
        <PrimaryButton onClick={onSave} disabled={saving}>
          {saving && <Spinner />}
          Save changes
        </PrimaryButton>
      </span>
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

  return (
    <Section
      title="About this database"
      description="Sent with every question. Two or three sentences and the time conventions are worth more here than anything else on this page."
    >
      <Field label="What this database is for">
        <TextArea
          value={doc.business_context}
          placeholder="An online retailer's order book: customers place orders made of line items, fulfilled from warehouses…"
          onChange={(e) => onChange({ ...doc, business_context: e.target.value })}
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

      <Field
        label="“Last month” means"
        hint="The single most common source of a wrong-looking answer."
      >
        <Select
          value={time.relative_windows}
          onChange={(e) =>
            setTime({ relative_windows: e.target.value as 'calendar' | 'rolling' })
          }
        >
          <option value="calendar">The whole previous calendar month</option>
          <option value="rolling">A rolling 30-day window ending today</option>
        </Select>
      </Field>

      <Field label="Other time conventions" hint="Optional. One sentence.">
        <TextInput
          value={time.notes}
          placeholder="Orders are timestamped when paid, not when placed."
          onChange={(e) => setTime({ notes: e.target.value })}
        />
      </Field>
    </Section>
  )
}

// ── filters ────────────────────────────────────────────────────────────────
function Filters({
  value, onChange, search, onSearch, shown, total, undescribed,
}: {
  value: Filter
  onChange: (next: Filter) => void
  search: string
  onSearch: (next: string) => void
  shown: number
  total: number
  undescribed: number
}) {
  const options: { value: Filter; label: string }[] = [
    { value: 'all', label: `All ${total}` },
    { value: 'review', label: 'Needs review' },
    { value: 'metrics', label: 'Has metrics' },
    { value: 'issues', label: 'Needs attention' },
  ]
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
      <div
        style={{
          display: 'flex',
          gap: 2,
          background: 'var(--panel-alt)',
          borderRadius: 8,
          padding: 3,
        }}
      >
        {options.map((option) => (
          <button
            key={option.value}
            onClick={() => onChange(option.value)}
            style={{
              fontSize: 12.5,
              fontWeight: 600,
              padding: '6px 12px',
              borderRadius: 6,
              cursor: 'pointer',
              border: 'none',
              color: option.value === value ? 'var(--text-strong)' : 'var(--text-dim)',
              background: option.value === value ? 'var(--panel)' : 'transparent',
              boxShadow: option.value === value ? '0 1px 3px rgba(0,0,0,0.10)' : 'none',
            }}
          >
            {option.label}
          </button>
        ))}
      </div>
      {undescribed > 0 && (
        <Chip tone="amber">{undescribed} tables not described</Chip>
      )}
      <TextInput
        placeholder="Search tables, metrics, columns…"
        value={search}
        onChange={(e) => onSearch(e.target.value)}
        style={{ width: 260, marginLeft: 'auto', fontSize: 13, padding: '8px 11px' }}
      />
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
  const broken =
    !entity.valid ||
    entity.metrics.some((m) => !m.valid) ||
    entity.columns.some((c) => !c.valid)

  return (
    <div
      style={{
        border: `1px solid ${broken ? 'var(--red-border)' : 'var(--border)'}`,
        borderRadius: 10,
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
          {entity.label || entity.table.split('.').slice(-1)[0]}
        </span>
        <span className="mono" style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
          {entity.table}
        </span>
        <span
          style={{
            fontSize: 12,
            color: 'var(--text-dim)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            minWidth: 0,
            flex: 1,
          }}
        >
          {entity.grain}
        </span>
        <span style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
          {entity.exclude && <Chip>hidden</Chip>}
          {entity.role !== 'unknown' && <Chip>{entity.role}</Chip>}
          {entity.metrics.length > 0 && (
            <Chip tone="green">{entity.metrics.length} metrics</Chip>
          )}
          {broken && <Chip tone="red">needs attention</Chip>}
          {entity.provenance.reviewed && <Chip tone="accent">reviewed</Chip>}
        </span>
      </button>

      {open && (
        <div
          style={{
            borderTop: '1px solid var(--border)',
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
            gap: 14,
          }}
        >
          {entity.issue && <Note tone="amber">{entity.issue}</Note>}

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

          <Columns
            entity={entity}
            onChange={(columns) => onChange({ columns })}
          />

          <Metrics
            connectionId={connectionId}
            entity={entity}
            onChange={(metrics) => onChange({ metrics })}
          />

          <div
            style={{
              display: 'flex',
              gap: 10,
              alignItems: 'center',
              paddingTop: 4,
              borderTop: '1px solid var(--border)',
              flexWrap: 'wrap',
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
            <span style={{ marginLeft: 'auto' }}>
              <Toggle
                checked={entity.exclude}
                onChange={(exclude) => onChange({ exclude })}
                label="Hide from the model"
                hint="For deprecated or staging tables."
              />
            </span>
          </div>
        </div>
      )}
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

  function update(index: number, change: Partial<SemanticColumn>) {
    onChange(
      entity.columns.map((c, i) =>
        i === index
          ? { ...c, ...change, provenance: { ...c.provenance, edited: true, source: 'human' } }
          : c,
      ),
    )
  }

  return (
    <SubSection
      title="Columns worth explaining"
      hint="Only the ones whose name is not self-evident — codes, units, abbreviations."
    >
      {entity.columns.map((column, index) => (
        <div
          key={`${column.name}-${index}`}
          style={{
            border: `1px solid ${column.valid ? 'var(--border)' : 'var(--red-border)'}`,
            borderRadius: 8,
            padding: 12,
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
            background: 'var(--panel-alt)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="mono" style={{ fontSize: 12.5, color: 'var(--text-strong)' }}>
              {column.name}
            </span>
            {!column.valid && <Chip tone="red">{column.issue}</Chip>}
            <span style={{ marginLeft: 'auto' }}>
              <IconButton
                label={`Remove ${column.name}`}
                onClick={() => onChange(entity.columns.filter((_, i) => i !== index))}
              >
                <Icon.Trash />
              </IconButton>
            </span>
          </div>
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
                value={Object.entries(column.value_meanings)
                  .map(([k, v]) => `${k} = ${v}`)
                  .join('\n')}
                onChange={(e) =>
                  update(index, { value_meanings: parseMeanings(e.target.value) })
                }
              />
            </Field>
          )}
        </div>
      ))}

      <div style={{ display: 'flex', gap: 8 }}>
        <TextInput
          className="mono"
          placeholder="column_name"
          value={adding}
          onChange={(e) => setAdding(e.target.value)}
          style={{ maxWidth: 240, fontSize: 13, padding: '7px 10px' }}
        />
        <GhostButton
          disabled={!adding.trim()}
          onClick={() => {
            onChange([...entity.columns, blankColumn(adding.trim())])
            setAdding('')
          }}
          style={{ padding: '7px 12px', fontSize: 12.5 }}
        >
          <Icon.Plus size={13} />
          Add column
        </GhostButton>
      </div>
    </SubSection>
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
    <SubSection
      title="Metrics"
      hint="The part that changes answers: a named measure bound to exact SQL, including the filters that belong to the definition rather than the question."
    >
      {entity.metrics.map((metric, index) => (
        <MetricCard
          key={index}
          connectionId={connectionId}
          table={entity.table}
          metric={metric}
          onChange={(change) => update(index, change)}
          onRemove={() => onChange(entity.metrics.filter((_, i) => i !== index))}
        />
      ))}
      <GhostButton
        onClick={() => onChange([...entity.metrics, blankMetric()])}
        style={{ alignSelf: 'flex-start', padding: '7px 12px', fontSize: 12.5 }}
      >
        <Icon.Plus size={13} />
        Add metric
      </GhostButton>
    </SubSection>
  )
}

function MetricCard({
  connectionId, table, metric, onChange, onRemove,
}: {
  connectionId: string
  table: string
  metric: SemanticMetric
  onChange: (change: Partial<SemanticMetric>) => void
  onRemove: () => void
}) {
  // Server-side validation, debounced: the browser cannot know the dialect or
  // the schema, and a second opinion here would only be wrong differently.
  const [check, setCheck] = useState<{ valid: boolean; issue: string } | null>(null)
  const [checking, setChecking] = useState(false)

  useEffect(() => {
    if (!metric.expression.trim()) {
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
  }, [connectionId, table, metric.expression, metric.required_joins.join(',')])

  const state = check ?? (metric.valid ? null : { valid: false, issue: metric.issue })

  return (
    <div
      style={{
        border: `1px solid ${state && !state.valid ? 'var(--red-border)' : 'var(--border)'}`,
        borderRadius: 8,
        padding: 12,
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        background: 'var(--panel-alt)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)' }}>
          {metric.label || metric.name || 'New metric'}
        </span>
        <span style={{ marginLeft: 'auto' }}>
          <IconButton label="Remove metric" onClick={onRemove}>
            <Icon.Trash />
          </IconButton>
        </span>
      </div>

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
            onChange({ filters: e.target.value.split('\n').map((l) => l.trim()).filter(Boolean) })
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
    </div>
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
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color }}>
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

  return (
    <Section
      title="Business terms"
      description="Words a user will type that are not the name of a table or a metric — “churn”, “active customer”, “AOV”."
    >
      {doc.glossary.map((term, index) => (
        <FieldRow key={index} columns={3}>
          <Field label="Term">
            <TextInput
              value={term.term}
              onChange={(e) => update(index, { term: e.target.value })}
            />
          </Field>
          <Field label="Means">
            <TextInput
              value={term.meaning}
              onChange={(e) => update(index, { meaning: e.target.value })}
            />
          </Field>
          <Field label=" ">
            <div style={{ display: 'flex', gap: 8 }}>
              <TextInput
                className="mono"
                placeholder="maps to…"
                value={term.maps_to.join(', ')}
                onChange={(e) => update(index, { maps_to: splitList(e.target.value) })}
              />
              <IconButton
                label={`Remove ${term.term}`}
                onClick={() => setTerms(doc.glossary.filter((_, i) => i !== index))}
              >
                <Icon.Trash />
              </IconButton>
            </div>
          </Field>
        </FieldRow>
      ))}
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
        style={{ alignSelf: 'flex-start', padding: '7px 12px', fontSize: 12.5 }}
      >
        <Icon.Plus size={13} />
        Add a term
      </GhostButton>
    </Section>
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
  }, [])

  const total = layer?.tables.length ?? 0
  const count = scope === 'missing' ? undescribed.length : total
  const chosen = configs.find((c) => c.id === configId)

  return (
    <Modal
      title="Generate a semantic layer"
      subtitle="A model reads your schema table by table and writes what each one means."
      onClose={onClose}
      width={560}
      footer={
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
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
        </div>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {configs.length === 0 ? (
          <Note tone="amber">
            Add a model under Models before generating a semantic layer.
          </Note>
        ) : (
          <Field
            label="Model"
            hint="A stronger model is worth it here: this runs once and every question afterwards reads what it wrote."
          >
            <Select value={configId} onChange={(e) => setConfigId(e.target.value)}>
              {configs.map((config) => (
                <option key={config.id} value={config.id}>
                  {config.name} — {config.model}
                  {config.status === 'OK' ? '' : ' (untested)'}
                </option>
              ))}
            </Select>
          </Field>
        )}

        {layer?.exists && (
          <>
            <Field label="Scope">
              <Select
                value={scope}
                onChange={(e) => setScope(e.target.value as 'all' | 'missing')}
              >
                <option value="missing">
                  Only the {undescribed.length} tables not yet described
                </option>
                <option value="all">Every table ({total})</option>
              </Select>
            </Field>

            <Field
              label="What happens to what is already there"
              hint="Anything you edited by hand is kept either way unless you choose to start over."
            >
              <Select
                value={mode}
                onChange={(e) => setMode(e.target.value as 'MERGE' | 'REPLACE')}
              >
                <option value="MERGE">Keep my edits, refresh the rest</option>
                <option value="REPLACE">Start over — discard everything</option>
              </Select>
            </Field>
          </>
        )}

        <div
          style={{
            fontSize: 12,
            lineHeight: 1.6,
            color: 'var(--text-dim)',
            background: 'var(--panel-alt)',
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: '10px 12px',
          }}
        >
          One model call per table, four at a time — roughly{' '}
          <strong style={{ color: 'var(--text-strong)' }}>
            {estimateMinutes(count)}
          </strong>{' '}
          for {count} tables{chosen ? ` on ${chosen.model}` : ''}. The model sees the
          same schema detail it already sees when answering a question, so this
          shares nothing new with your provider. Nothing is saved until it finishes.
        </div>
      </div>
    </Modal>
  )
}

// ── small pieces ───────────────────────────────────────────────────────────
function SubSection({
  title, hint, children,
}: {
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-strong)' }}>
          {title}
        </div>
        {hint && (
          <div style={{ fontSize: 11.5, color: 'var(--text-dim)', marginTop: 2 }}>
            {hint}
          </div>
        )}
      </div>
      {children}
    </div>
  )
}

function IconButton({
  label, onClick, children,
}: {
  label: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      aria-label={label}
      title={label}
      onClick={onClick}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 26,
        height: 26,
        borderRadius: 6,
        border: '1px solid var(--border)',
        background: 'transparent',
        color: 'var(--text-dim)',
        cursor: 'pointer',
      }}
    >
      {children}
    </button>
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
