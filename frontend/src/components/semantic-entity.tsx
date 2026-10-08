/**
 * One entity of the semantic layer, opened: its meaning, its columns, its
 * metrics, and the glossary beside them. Rendered by `SemanticLayerTab`.
 */
import { useEffect, useState } from 'react'
import { semantic as api } from '../api/client'
import type {
  GlossaryTerm, SemanticColumn, SemanticDocument, SemanticEntity, SemanticMetric,
} from '../api/types'
import { Chip, Field, GhostButton, Icon, Select, Spinner, TextArea, TextInput, Toggle } from './ui'
import { FieldRow } from './settings'
import {
  COLUMN_ROLE_TONE, IconButton, Line, Note, Panel, PillTabs, ROLE_META, SubCard,
  SubCardHead, blankColumn, blankMetric, hasIssue, parseMeanings, slug,
  splitList,
} from './semantic-parts'

// ── one entity ─────────────────────────────────────────────────────────────
/**
 * One table in the layer — a row when closed, three tabs when open.
 *
 * Opening one used to produce a single 1,800px form: the meaning fields, then
 * every described column, then every metric, then two switches at the very
 * bottom. Three lists of near-identical collapsed rows in one scroll, with the
 * table's own name off the top of the screen by the time you reached the
 * metrics — you could not tell which list you were in, and the one control
 * that says *I have checked this* was the furthest thing from the heading.
 *
 * So the open card is **one region at a time**, under a header that stays put:
 *
 *  - the head sticks while the body scrolls, so the table being edited is
 *    always named;
 *  - `PillTabs` splits the body into Meaning, Columns and Metrics, each with
 *    its own count, so the page has a size the reader can hold;
 *  - a tab whose contents are broken carries its count in red, because a tab
 *    is only allowed to hide things that are not asking for you.
 *
 * The role tone is a chip and no longer a 2px stripe down the left of the
 * header: the stripe covered the head and stopped at the body, so an open card
 * looked cut in half.
 */
export type Section = 'meaning' | 'columns' | 'metrics'

export function EntityCard({
  connectionId, entity, open, focusedAt = 0, focusedSection, onToggle, onChange, onHistory,
  readOnly = false,
}: {
  connectionId: string
  entity: SemanticEntity
  /** Viewable, not editable: the card opens, its fields do not take input. */
  readOnly?: boolean
  open: boolean
  /** When the metrics panel last sent a reader here, or 0. A timestamp rather
   *  than a boolean, so arriving twice at the same card works: the second
   *  visit has to reopen the metrics section the reader may have left. */
  focusedAt?: number
  /** Which part to open on when focused — Metrics unless a caller says. */
  focusedSection?: Section
  onToggle: () => void
  onChange: (change: Partial<SemanticEntity>) => void
  /** This table's history, or one metric's on it. */
  onHistory: (item?: string) => void
}) {
  const broken = hasIssue(entity)
  const role = ROLE_META[entity.role] ?? ROLE_META.unknown
  const [section, setSection] = useState<Section>('meaning')

  // Arriving from the metrics list lands on metrics. Anything else — opening
  // the card by hand — still starts on meaning, which is where a table is
  // read rather than measured.
  useEffect(() => {
    if (focusedAt) setSection(focusedSection ?? 'metrics')
  }, [focusedAt])

  const badColumns = entity.columns.filter((c) => !c.valid).length
  const badMetrics = entity.metrics.filter((m) => !m.valid).length

  return (
    <div
      id={entityDomId(entity.table)}
      style={{
        border: `1px solid ${broken ? 'var(--red-border)' : 'var(--border)'}`,
        borderRadius: 11,
        background: 'var(--panel)',
        // Clears the sticky filter bar when the metrics list scrolls a card
        // to the top, so the row it sent you to is not under it.
        scrollMarginTop: 64,
        // Deliberately **not** `overflow: hidden`. That is the tidy way to keep
        // children inside a rounded card, and it also makes this card the
        // scrollport for anything sticky inside it — which turned the header
        // below into a block offset 56px down its own card, floating over the
        // first field row and sticking to nothing. Nothing here needs clipping:
        // the head and the body are both `--panel` on a `--panel` card.
        opacity: entity.exclude ? 0.6 : 1,
      }}
    >
      {/* Sticky only while open, and only as tall as the head plus its tabs:
          a closed row has nothing to stay behind, and a sticky element in a
          list of forty of them would pile up down the page.
          `top` clears the filter bar, which is sticky at 6 and ~44 tall. */}
      <div
        style={{
          position: open ? 'sticky' : undefined,
          top: 56,
          zIndex: open ? 3 : undefined,
          background: 'var(--panel)',
          borderBottom: open ? '1px solid var(--border)' : undefined,
          borderRadius: '10px 10px 0 0',
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
              {/* What is inside, without opening it — and once it *is* open the
                  tabs carry the same two numbers, so this stands down. */}
              {!open && (
                <span
                  style={{ color: 'var(--text-faint)', whiteSpace: 'nowrap', flexShrink: 0 }}
                >
                  {entity.columns.length > 0 && `${entity.columns.length} cols`}
                  {entity.columns.length > 0 && entity.metrics.length > 0 && ' · '}
                  {entity.metrics.length > 0 &&
                    `${entity.metrics.length} ${entity.metrics.length === 1 ? 'metric' : 'metrics'}`}
                </span>
              )}
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
              padding: '0 14px 12px 38px',
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              flexWrap: 'wrap',
            }}
          >
            <PillTabs
              value={section}
              onChange={setSection}
              ariaLabel={`Sections of ${entity.label || entity.table}`}
              options={[
                {
                  value: 'meaning',
                  label: 'Meaning',
                  alert: !entity.valid || entity.issue !== '',
                },
                {
                  value: 'columns',
                  label: 'Columns',
                  count: entity.columns.length,
                  alert: badColumns > 0,
                },
                {
                  value: 'metrics',
                  label: 'Metrics',
                  count: entity.metrics.length,
                  alert: badMetrics > 0,
                },
              ]}
            />
            <GhostButton
              onClick={() => onHistory()}
              title={`Every saved change to ${entity.table}`}
              style={{ marginLeft: 'auto', padding: '4px 9px', fontSize: 12 }}
            >
              <Icon.History size={13} />
              History
            </GhostButton>
          </div>
        )}
      </div>

      {open && (
        <fieldset disabled={readOnly} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <div
          style={{
            padding: 18,
            display: 'flex',
            flexDirection: 'column',
            gap: 18,
            background: 'var(--panel)',
          }}
        >
          {entity.issue && section === 'meaning' && <Note tone="amber">{entity.issue}</Note>}

          {section === 'meaning' && (
            <>
              <FieldRow>
                <Field label="Business name">
                  <TextInput
                    value={entity.label}
                    placeholder="e.g. Orders"
                    onChange={(e) => onChange({ label: e.target.value })}
                  />
                </Field>
                <Field label="Also called" hint="Comma separated.">
                  <TextInput
                    value={entity.synonyms.join(', ')}
                    placeholder="e.g. purchases, sales orders"
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
                  placeholder="e.g. one row per line item on an order"
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
                    placeholder="e.g. ordered_at"
                    onChange={(e) => onChange({ default_time_column: e.target.value })}
                  />
                </Field>
              </FieldRow>

              {/* What to *do* with this table, at the end of the tab that
                  describes it — rather than at the foot of a scroll that used
                  to run past every column and metric to reach it. */}
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
            </>
          )}

          {section === 'columns' && (
            <Columns entity={entity} onChange={(columns) => onChange({ columns })} />
          )}

          {section === 'metrics' && (
            <Metrics
              connectionId={connectionId}
              entity={entity}
              onChange={(metrics) => onChange({ metrics })}
              onHistory={(name) => onHistory(name)}
            />
          )}
        </div>
        </fieldset>
      )}
    </div>
  )
}

/** A labelled band inside an expanded entity. Keeps a long form readable as
 *  three or four parts rather than one wall of inputs. */
function Group({
  title, hint, count, action, children,
}: {
  /**
   * Optional, because inside an expanded table the tab above already names
   * the region: a heading that repeats the tab you just pressed is a line of
   * type spent saying nothing. Without one this is the region's guidance and
   * its one action, over the rule.
   */
  title?: string
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
          {title && (
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
          )}
          {hint && (
            <div
              style={{
                fontSize: 11.5,
                lineHeight: 1.5,
                color: 'var(--text-dim)',
                marginTop: title ? 4 : 0,
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
  // `null` is "not adding"; a string is the name being typed. Two states in
  // one value, because an empty string is a legitimate thing to be typing.
  const [adding, setAdding] = useState<string | null>(null)
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
    const name = (adding ?? '').trim()
    if (!name) return
    onChange([...entity.columns, blankColumn(name)])
    // Open what was just added — a new row that arrives collapsed and empty
    // looks like the button did nothing.
    setOpen((prev) => new Set(prev).add(entity.columns.length))
    setAdding(null)
  }

  return (
    <Group
      hint="Only the ones whose name is not self-evident — codes, units, abbreviations."
      action={
        // The same shape Metrics has always had, in the same place. This used
        // to be a naked text field and a button loose at the foot of the list,
        // which read as a twelfth column that behaved differently from the
        // other eleven.
        adding === null ? (
          <GhostButton
            onClick={() => setAdding('')}
            style={{ padding: '6px 11px', fontSize: 12.5, flexShrink: 0 }}
          >
            <Icon.Plus size={13} />
            Add column
          </GhostButton>
        ) : (
          <span style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
            <TextInput
              autoFocus
              className="mono"
              placeholder="e.g. ordered_at"
              value={adding}
              onChange={(e) => setAdding(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  add()
                }
                if (e.key === 'Escape') setAdding(null)
              }}
              style={{ width: 180, fontSize: 13, padding: '6px 10px' }}
            />
            <GhostButton
              disabled={!adding.trim()}
              onClick={add}
              style={{ padding: '6px 11px', fontSize: 12.5 }}
            >
              Add
            </GhostButton>
            <GhostButton
              onClick={() => setAdding(null)}
              style={{ padding: '6px 11px', fontSize: 12.5 }}
            >
              Cancel
            </GhostButton>
          </span>
        )
      }
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

      {entity.columns.length === 0 && (
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
          No columns explained yet. Most tables need only the few whose names
          give nothing away.
        </div>
      )}
    </Group>
  )
}

// ── metrics ────────────────────────────────────────────────────────────────
function Metrics({
  connectionId, entity, onChange, onHistory,
}: {
  connectionId: string
  entity: SemanticEntity
  onChange: (metrics: SemanticMetric[]) => void
  onHistory: (name: string) => void
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
          onHistory={metric.name.trim() ? () => onHistory(metric.name.trim().toLowerCase()) : undefined}
        />
      ))}
    </Group>
  )
}

function MetricCard({
  connectionId, table, metric, open, onToggle, onChange, onRemove, onHistory,
}: {
  connectionId: string
  table: string
  metric: SemanticMetric
  open: boolean
  onToggle: () => void
  onChange: (change: Partial<SemanticMetric>) => void
  onRemove: () => void
  /** Absent for a metric with no name yet: there is no entry to look up. */
  onHistory?: () => void
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
        onHistory={onHistory}
        historyLabel={`History of ${metric.name}`}
      />

      {!open ? null : (
        <>
      <FieldRow>
        <Field label="Identifier" hint="snake_case, unique on this table.">
          <TextInput
            className="mono"
            value={metric.name}
            placeholder="e.g. net_revenue"
            onChange={(e) => onChange({ name: slug(e.target.value) })}
          />
        </Field>
        <Field label="What a person calls it">
          <TextInput
            value={metric.label}
            placeholder="e.g. Net revenue"
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
          // Code, not prose: pinned LTR so a Persian comment or string literal
          // cannot flip the whole expression right-to-left.
          dir="ltr"
          value={metric.expression}
          placeholder={`e.g. SUM(${table}.amount)`}
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
          placeholder={`e.g. ${table}.status <> 'CANCELLED'`}
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
            placeholder="e.g. USD"
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
            placeholder="e.g. revenue, GMV, net sales"
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

// ── glossary ───────────────────────────────────────────────────────────────
export function Glossary({
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
              placeholder="e.g. churn"
              onChange={(e) => update(index, { term: e.target.value })}
            />
          </Field>
          <Field label="Means">
            <TextInput
              value={term.meaning}
              placeholder="e.g. a customer with no order in the last 90 days"
              onChange={(e) => update(index, { meaning: e.target.value })}
            />
          </Field>
          <Field label="Maps to">
            <TextInput
              className="mono"
              placeholder="e.g. public.customers"
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

/** Where a metric row jumps to. One id scheme, so the panel and the card do
 *  not have to agree about anything except the table's own name. */
export function entityDomId(table: string): string {
  return `entity-${slug(table)}`
}
