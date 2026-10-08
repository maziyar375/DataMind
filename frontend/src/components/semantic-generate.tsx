/**
 * The Generate dialog of the semantic layer: which model, which tables, and
 * what happens to what is already written. Opened by `SemanticLayerTab`.
 */
import { useEffect, useMemo, useState } from 'react'
import { llmConfigs as llmApi } from '../api/client'
import type { LlmConfig, SemanticGenerationMode, SemanticLayer } from '../api/types'
import { Chip, GhostButton, Modal, PrimaryButton, Spinner, TextInput } from './ui'
import { Line, Note, estimateMinutes } from './semantic-parts'

// ── generate modal ─────────────────────────────────────────────────────────
type Scope = 'missing' | 'chosen' | 'all'

/**
 * Generate: which model, which tables, and what happens to what is written.
 *
 * **Which tables** is three answers: what is missing, tables somebody picks,
 * or every table. The picker is how a described table is described again —
 * the one a re-sync grew six columns on — and *Needs attention* opens it with
 * that table already chosen.
 *
 * **What happens to a table that already has an entity** is two modes (Phase 5):
 * *Fill the gaps*, the default, keeps every field a person wrote and adds only
 * what is missing; *Rewrite* replaces it. Over chosen tables either one changes
 * those tables and nothing else. Both land in the draft, so a rewrite is a
 * change list somebody reads before it answers anything.
 */
export function GenerateModal({
  layer, undescribed, initialTables, onClose, onStart,
}: {
  layer: SemanticLayer | null
  undescribed: string[]
  /** Tables to open with chosen — from a *Needs attention* row. */
  initialTables?: string[]
  onClose: () => void
  onStart: (payload: {
    llm_config_id: string
    mode: SemanticGenerationMode
    only_tables?: string[]
  }) => void
}) {
  const tables = layer?.tables ?? []
  const exists = !!layer?.exists
  const [configs, setConfigs] = useState<LlmConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [configId, setConfigId] = useState('')
  const [mode, setMode] = useState<'FILL_GAPS' | 'REPLACE'>('FILL_GAPS')
  const [scope, setScope] = useState<Scope>(() => {
    const asked = (initialTables ?? []).map((t) => t.toLowerCase())
    const missing = new Set(undescribed.map((t) => t.toLowerCase()))
    if (asked.length > 0) {
      return exists && asked.length === missing.size && asked.every((t) => missing.has(t))
        ? 'missing'
        : 'chosen'
    }
    return undescribed.length > 0 && exists ? 'missing' : 'all'
  })
  const [chosen, setChosen] = useState<Set<string>>(
    () => new Set((initialTables ?? []).map((t) => t.toLowerCase())),
  )
  const [pick, setPick] = useState('')
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    llmApi
      // Generation asks a model to write; an embeddings-only row cannot.
      .list('chat')
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

  const total = tables.length
  const picked = tables.filter((t) => chosen.has(t.table))
  const onlyTables =
    scope === 'missing' ? undescribed : scope === 'chosen' ? picked.map((t) => t.table) : []
  const count = scope === 'all' ? total : onlyTables.length
  // The mode only means something for a table that already has an entity.
  const describedInScope =
    scope === 'all'
      ? tables.filter((t) => t.described).length
      : scope === 'chosen'
        ? picked.filter((t) => t.described).length
        : 0
  // Tables the dialog was opened with come first, so the reason it was opened
  // for is on screen. Fixed at open: re-sorting on every tick would move a row
  // out from under the pointer.
  const [first] = useState(() => new Set((initialTables ?? []).map((t) => t.toLowerCase())))
  const ordered = useMemo(
    () => [...tables.filter((t) => first.has(t.table)), ...tables.filter((t) => !first.has(t.table))],
    [tables, first],
  )
  const needle = pick.trim().toLowerCase()
  const listed = needle ? ordered.filter((t) => t.table.includes(needle)) : ordered

  const scopes: { value: Scope; label: string; hint: string; disabled?: boolean }[] = [
    ...(exists
      ? [{
          value: 'missing' as const,
          label: 'What is missing',
          hint: `${undescribed.length} ${undescribed.length === 1 ? 'table' : 'tables'}`,
          disabled: undescribed.length === 0,
        }]
      : []),
    {
      value: 'chosen',
      label: 'Tables I choose',
      hint: picked.length ? `${picked.length} chosen` : 'pick below',
    },
    { value: 'all', label: 'Every table', hint: `${total} tables` },
  ]

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
                // Nothing is there to keep or replace for a table with no entity.
                mode: scope === 'missing' ? 'FILL_GAPS' : mode,
                only_tables: onlyTables,
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

      {total > 0 && (
        <ModalGroup title="How much to describe">
          <ChoiceRow value={scope} onChange={(next) => setScope(next as Scope)} options={scopes} />
          {scope === 'chosen' && (
            <TablePicker
              tables={listed}
              filtered={needle !== ''}
              chosen={chosen}
              query={pick}
              onQuery={setPick}
              onChange={setChosen}
            />
          )}
        </ModalGroup>
      )}

      {exists && describedInScope > 0 && (
        <ModalGroup title="Tables that are already described">
          <ChoiceRow
            value={mode}
            onChange={(next) => setMode(next as 'FILL_GAPS' | 'REPLACE')}
            options={[
              { value: 'FILL_GAPS', label: 'Fill the gaps', hint: 'Keep what people wrote' },
              {
                value: 'REPLACE',
                label: 'Rewrite',
                hint: scope === 'all' ? 'Start over, edits too' : 'Replace these tables',
                tone: 'red',
              },
            ]}
          />
          <span style={{ fontSize: 11.5, lineHeight: 1.55, color: 'var(--text-dim)' }}>
            {mode === 'FILL_GAPS'
              ? 'A table somebody edited keeps every field they wrote and gains only what it lacks: empty fields, and columns and metrics it does not have. Tables nobody edited are described afresh. Nothing is removed.'
              : scope === 'all'
                ? 'Every table is described from scratch, and edits are replaced along with everything else.'
                : `${describedInScope === 1 ? 'The described table is' : `${describedInScope} described tables are`} written from scratch, edits included.`}
            {scope !== 'all' &&
              ' Other tables stay as they are, and the business context and glossary are only filled where empty.'}
          </span>
        </ModalGroup>
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
        nothing new with your provider. It lands in your draft when it
        finishes, and no answer reads it until you publish.
      </div>
    </Modal>
  )
}

/** The generate dialog's table list: search, choose, and see which tables
 *  already have a description — the ones the mode below applies to. */
function TablePicker({
  tables, filtered, chosen, query, onQuery, onChange,
}: {
  tables: SemanticLayer['tables']
  filtered: boolean
  chosen: Set<string>
  query: string
  onQuery: (next: string) => void
  onChange: (next: Set<string>) => void
}) {
  const allShown = tables.length > 0 && tables.every((t) => chosen.has(t.table))
  function toggle(table: string, on: boolean) {
    const next = new Set(chosen)
    if (on) next.add(table)
    else next.delete(table)
    onChange(next)
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <TextInput
          placeholder="Find a table…"
          aria-label="Find a table"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          style={{ fontSize: 12.5, padding: '6px 10px', flex: '1 1 180px', minWidth: 0 }}
        />
        <GhostButton
          onClick={() => {
            const next = new Set(chosen)
            for (const t of tables) {
              if (allShown) next.delete(t.table)
              else next.add(t.table)
            }
            onChange(next)
          }}
          disabled={tables.length === 0}
          style={{ padding: '4px 10px', fontSize: 12 }}
        >
          {allShown ? 'Clear' : filtered ? 'Choose these' : 'Choose all'}
        </GhostButton>
      </div>
      <div
        role="group"
        aria-label="Tables"
        style={{
          maxHeight: 216, overflowY: 'auto', border: '1px solid var(--border)',
          borderRadius: 9, padding: 4,
        }}
      >
        {tables.length === 0 && (
          <div style={{ fontSize: 12, color: 'var(--text-faint)', padding: '8px 8px' }}>
            No table matches.
          </div>
        )}
        {tables.map((t) => (
          <label
            key={t.table}
            className="rm-krow"
            style={{
              display: 'flex', gap: 9, alignItems: 'center', padding: '6px 8px',
              borderRadius: 6, cursor: 'pointer',
            }}
          >
            <input
              type="checkbox"
              checked={chosen.has(t.table)}
              onChange={(e) => toggle(t.table, e.target.checked)}
              style={{ accentColor: 'var(--accent)', cursor: 'pointer', flexShrink: 0 }}
            />
            <span
              className="mono"
              dir="ltr"
              style={{
                fontSize: 12, color: 'var(--text-strong)', minWidth: 0, flex: 1,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}
            >
              {t.table}
            </span>
            <span style={{ fontSize: 11, color: 'var(--text-faint)', flexShrink: 0 }}>
              {t.column_count} {t.column_count === 1 ? 'column' : 'columns'}
            </span>
            <Chip tone={t.described ? 'accent' : 'neutral'} small>
              {t.described ? '● described' : '○ new'}
            </Chip>
          </label>
        ))}
      </div>
    </div>
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
export function ChoiceRow({
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
