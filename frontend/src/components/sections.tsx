/**
 * The Sections tab: a large database divided into named parts.
 *
 * `docs/plans/retrieval-sections.md` §8. A section is a name, a sentence and a
 * list of tables. The system proposes the division — first open with nothing
 * saved shows a complete one, never a blank form — and a person corrects it:
 * renames, moves a table by dragging its chip or by typing its name into
 * another card, and above all writes the **description**, which is what the
 * model reads to choose a section.
 *
 * Each card shows what the section costs in the units `retrieve` decides with,
 * and whether it fits the budget whole; a section that does not is the one
 * thing this screen asks a person to fix, and it says so in the card.
 *
 * The draft is the whole set and is saved whole, because sections partition
 * the tables: the arithmetic lives in `sections-model.ts`, DOM-free and tested.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, sections as api } from '../api/client'
import type { Connection, SectionSet } from '../api/types'
import {
  EmptyState, ErrorNote, GhostButton, Icon, Modal, PrimaryButton, QuietAction, Spinner,
  TextArea, TextInput, DangerButton, dirOf,
} from './ui'
import { DetailBody } from './settings'
import { useUnsavedWork } from '../shell'
import {
  UNASSIGNED, UNASSIGNED_KEY, applySplit, fitOf, formatChars, freshKey, homeOf, moveTable,
  newName, problems, reorder, sameSet, sizeOf, toDrafts, toWrite, unassignedOf,
} from './sections-model'
import type { Fit, SectionDraft } from './sections-model'

/** How many chips a card shows before it asks to be opened. A 300-table
 * section drawn in full is a wall nobody reads. */
const CHIPS_SHOWN = 36
const DRAG_TYPE = 'application/x-datamind-table'

export function SectionsTab({ connection }: { connection: Connection }) {
  const mayEdit = (connection.privileges ?? []).includes('modify')
  const [set, setSet] = useState<SectionSet | null>(null)
  const [drafts, setDrafts] = useState<SectionDraft[]>([])
  // What is saved (for the Save button) and what was first shown (for the
  // navigation guard). They differ on first open: a proposal nobody has saved
  // is not unsaved *work* until somebody touches it.
  const [saved, setSaved] = useState<SectionDraft[]>([])
  const [shown, setShown] = useState<SectionDraft[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [splitting, setSplitting] = useState<string | null>(null)
  const [askClear, setAskClear] = useState(false)
  const [focusKey, setFocusKey] = useState<string | null>(null)

  const adopt = useCallback((next: SectionSet, { proposal }: { proposal: SectionSet | null }) => {
    setSet(next)
    const savedDrafts = toDrafts(next.sections)
    setSaved(savedDrafts)
    const first = proposal ? toDrafts(proposal.sections) : savedDrafts
    setDrafts(first)
    setShown(first)
  }, [])

  const load = useCallback(async () => {
    const current = await api.get(connection.id)
    // Nothing saved: the proposal, straight away. D4 — never a blank form.
    // A reader who cannot edit sees it too, marked as unsaved: how the
    // database *would* be divided is a question `select` may ask.
    const proposal = !current.saved && current.has_snapshot
      ? await api.propose(connection.id)
      : null
    adopt(current, { proposal })
  }, [connection.id, adopt])

  useEffect(() => {
    setLoading(true)
    setError(null)
    setNotice(null)
    load()
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load the sections.'))
      .finally(() => setLoading(false))
  }, [load])

  const weights = useMemo(
    () => new Map((set?.catalog ?? []).map((e) => [e.table, e.chars] as const)),
    [set],
  )
  const catalog = useMemo(() => (set?.catalog ?? []).map((e) => e.table), [set])
  const budget = set?.budget_chars ?? 0
  const unassigned = useMemo(() => unassignedOf(drafts, catalog), [drafts, catalog])
  const found = useMemo(() => problems(drafts), [drafts])
  const changed = !sameSet(drafts, saved)
  const touched = !sameSet(drafts, shown)
  const firstRun = !!set && !set.saved && drafts.length > 0

  useUnsavedWork(
    `sections-${connection.id}`,
    mayEdit && touched ? `The sections for “${connection.name}” have not been saved.` : null,
    `/sources/${connection.id}`,
  )

  const edit = useCallback((key: string, patch: Partial<SectionDraft>) => {
    setNotice(null)
    setDrafts((prev) => prev.map((d) => (d.key === key ? { ...d, ...patch } : d)))
  }, [])
  const move = useCallback((table: string, to: string) => {
    setNotice(null)
    setDrafts((prev) => moveTable(prev, table, to))
  }, [])

  async function save() {
    if (found.size > 0) return
    setSaving(true)
    setError(null)
    try {
      const next = await api.save(connection.id, toWrite(drafts))
      adopt(next, { proposal: null })
      setNotice(
        next.sections.length === 1
          ? 'Saved 1 section.'
          : `Saved ${next.sections.length} sections.`,
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the sections.')
    } finally {
      setSaving(false)
    }
  }

  async function split(section: SectionDraft) {
    setSplitting(section.key)
    setError(null)
    try {
      const result = await api.propose(connection.id, section.tables)
      const parts = result.sections
      if (parts.length <= 1) {
        setNotice(
          `“${section.name}” has no natural split — its tables share no naming pattern. `
          + 'Move tables into another section by hand.',
        )
        return
      }
      setDrafts((prev) => applySplit(
        prev, section.key,
        parts.map((p) => ({ name: p.name, description: p.description, tables: p.tables })),
        result.unassigned,
      ))
      setNotice(`Split “${section.name}” into ${parts.length} sections. Check them, then save.`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not split this section.')
    } finally {
      setSplitting(null)
    }
  }

  async function clear() {
    setAskClear(false)
    setSaving(true)
    try {
      await api.clear(connection.id)
      await load()
      setNotice('Sections are off for this connection.')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not turn sections off.')
    } finally {
      setSaving(false)
    }
  }

  function addSection() {
    const key = freshKey()
    setDrafts((prev) => [
      ...prev,
      { key, id: null, name: newName(prev), description: '', tables: [] },
    ])
    setFocusKey(key)
  }

  if (loading) {
    return (
      <DetailBody>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-dim)', fontSize: 13 }}>
          <Spinner /> Reading the schema…
        </div>
      </DetailBody>
    )
  }

  if (set && !set.has_snapshot) {
    return (
      <DetailBody>
        <EmptyState
          icon={<Icon.Grid size={20} />}
          title="No schema to divide yet"
          body="This connection has no schema snapshot. Sync it, then try again."
        />
      </DetailBody>
    )
  }

  const sectionsTables = drafts.reduce((n, d) => n + d.tables.length, 0)
  // On first open the banner is the save action; the bar still carries what
  // an action reports (a move, a split), just not a second Save.
  const barChanged = changed && !firstRun
  const barShown = mayEdit && (barChanged || !!notice)

  return (
    <>
      <DetailBody padBottom={barShown}>
        {error && <ErrorNote>{error}</ErrorNote>}

        <Intro
          sections={drafts.length}
          tables={catalog.length}
          placed={sectionsTables}
          unassigned={unassigned.length}
          budget={budget}
          action={mayEdit ? (
            <GhostButton onClick={addSection} style={{ padding: '6px 11px', fontSize: 12.5 }}>
              <Icon.Plus size={13} /> Add section
            </GhostButton>
          ) : undefined}
        />

        {firstRun && (
          <Banner
            tables={catalog.length}
            sections={drafts.length}
            mayEdit={mayEdit}
            saving={saving}
            blocked={found.size > 0}
            onUse={save}
          />
        )}

        {found.get('') && <ErrorNote>{found.get('')}</ErrorNote>}

        <datalist id={`rm-section-tables-${connection.id}`}>
          {catalog.map((t) => <option key={t} value={t} />)}
        </datalist>

        {drafts.map((draft, index) => (
          <SectionCard
            key={draft.key}
            draft={draft}
            index={index}
            count={drafts.length}
            weights={weights}
            budget={budget}
            problem={found.get(draft.key) ?? null}
            mayEdit={mayEdit}
            splitting={splitting === draft.key}
            autoFocus={focusKey === draft.key}
            listId={`rm-section-tables-${connection.id}`}
            onEdit={(patch) => edit(draft.key, patch)}
            onMove={move}
            onAdd={(table) => {
              const from = homeOf(drafts, table)
              move(table, draft.key)
              if (from && from.key !== draft.key) {
                setNotice(`Moved ${table} from “${from.name}” to “${draft.name}”.`)
              }
            }}
            onReorder={(delta) => setDrafts((prev) => reorder(prev, draft.key, delta))}
            onDelete={() => setDrafts((prev) => prev.filter((d) => d.key !== draft.key))}
            onSplit={() => split(draft)}
          />
        ))}

        <UnassignedCard tables={unassigned} mayEdit={mayEdit} onMove={move} />

        {mayEdit && set?.saved && (
          <div
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              gap: 12, flexWrap: 'wrap', paddingTop: 8,
              borderTop: '1px solid var(--border)', marginTop: 4,
            }}
          >
            <span style={{ fontSize: 12.5, color: 'var(--text-dim)', maxWidth: 560, lineHeight: 1.55 }}>
              Turning sections off deletes them. Every question is then answered
              from the whole schema again, as it was before any were saved.
            </span>
            <DangerButton onClick={() => setAskClear(true)} disabled={saving}>
              Turn off sections
            </DangerButton>
          </div>
        )}
      </DetailBody>

      {barShown && (
        <SaveBar
          changed={barChanged}
          saving={saving}
          notice={notice}
          blocked={found.size > 0}
          onSave={save}
          onDiscard={() => {
            setDrafts(set?.saved ? saved : shown)
            setNotice(null)
          }}
          onDismiss={() => setNotice(null)}
        />
      )}

      {askClear && (
        <Modal
          title="Turn off sections?"
          onClose={() => setAskClear(false)}
          footer={
            <>
              <GhostButton onClick={() => setAskClear(false)}>Keep them</GhostButton>
              <DangerButton onClick={clear}>Turn off and delete</DangerButton>
            </>
          }
        >
          <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: 'var(--text)' }}>
            All {saved.length} sections of <strong>{connection.name}</strong> are
            deleted, with their descriptions. A new proposal is shown the next
            time this tab opens.
          </p>
        </Modal>
      )}
    </>
  )
}

// ── the top of the tab ────────────────────────────────────────────────────
function Intro({
  sections, tables, placed, unassigned, budget, action,
}: {
  sections: number
  tables: number
  placed: number
  unassigned: number
  budget: number
  action?: React.ReactNode
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: 'var(--text-dim)', maxWidth: 720 }}>
        A database too large to send to the model whole is divided into
        sections. A question is answered from the section it is about, and a
        section that fits under {formatChars(budget)} characters of schema is
        sent whole — every column of every table in it. The description is
        what the model reads to choose.
      </p>
      <p
        style={{
          margin: 0, fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-dim)',
          display: 'flex', gap: 7, alignItems: 'flex-start', maxWidth: 720,
        }}
      >
        <span style={{ display: 'flex', marginTop: 2, color: 'var(--text-faint)' }}><Icon.Info size={13} /></span>
        A section changes what the model is shown, never what it may query: an
        answer or a saved tile over a table outside the section still runs.
        When no section fits a question, it is answered from the whole schema.
      </p>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
          <strong style={{ color: 'var(--text-strong)' }}>{sections}</strong>{' '}
          section{sections === 1 ? '' : 's'} · {placed} of {tables} tables placed
          {unassigned > 0 && <> · {unassigned} unassigned</>}
        </span>
        {action && <span style={{ marginLeft: 'auto' }}>{action}</span>}
      </div>
    </div>
  )
}

function Banner({
  tables, sections, mayEdit, saving, blocked, onUse,
}: {
  tables: number
  sections: number
  mayEdit: boolean
  saving: boolean
  blocked: boolean
  onUse: () => void
}) {
  return (
    <div
      className="rm-enter"
      style={{
        display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
        padding: '12px 14px 12px 16px',
        borderRadius: 10,
        background: 'var(--accent-bg)',
        border: '1px solid var(--accent-border)',
      }}
    >
      <span style={{ display: 'flex', color: 'var(--accent)' }}><Icon.Sparkle size={16} /></span>
      <span style={{ flex: 1, minWidth: 220, fontSize: 13, lineHeight: 1.55, color: 'var(--text-strong)' }}>
        We divided your {tables} tables into {sections} section{sections === 1 ? '' : 's'}.{' '}
        {mayEdit
          ? 'Check them, then save.'
          : 'Nothing is saved — somebody who can edit this connection can save them.'}
      </span>
      {mayEdit && (
        <PrimaryButton onClick={onUse} disabled={saving || blocked} style={{ padding: '8px 14px' }}>
          {saving && <Spinner />}
          Use these sections
        </PrimaryButton>
      )}
    </div>
  )
}

// ── one section ───────────────────────────────────────────────────────────
const FIT_LOOK: Record<Fit, { glyph: string; word: string; ink: string; bg: string }> = {
  FITS: { glyph: '✓', word: 'Fits whole', ink: 'var(--green)', bg: 'var(--green-bg)' },
  TOO_LARGE: { glyph: '⚠', word: 'Too large', ink: 'var(--amber)', bg: 'var(--amber-bg)' },
  EMPTY: { glyph: '○', word: 'Empty', ink: 'var(--text-dim)', bg: 'var(--panel-alt)' },
}

function FitBadge({ fit }: { fit: Fit }) {
  const look = FIT_LOOK[fit]
  return (
    <span
      data-fit={fit}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        fontSize: 11.5, fontWeight: 600, padding: '3px 8px', borderRadius: 999,
        color: look.ink, background: look.bg, whiteSpace: 'nowrap',
      }}
    >
      <span aria-hidden>{look.glyph}</span>
      {look.word}
    </span>
  )
}

function useDrop(onDrop: (table: string) => void, enabled: boolean) {
  const [over, setOver] = useState(false)
  const depth = useRef(0)
  if (!enabled) return { over: false, handlers: {} }
  return {
    over,
    handlers: {
      onDragEnter: (e: React.DragEvent) => {
        if (!e.dataTransfer.types.includes(DRAG_TYPE)) return
        depth.current += 1
        setOver(true)
      },
      onDragLeave: () => {
        depth.current = Math.max(0, depth.current - 1)
        if (depth.current === 0) setOver(false)
      },
      onDragOver: (e: React.DragEvent) => {
        if (!e.dataTransfer.types.includes(DRAG_TYPE)) return
        e.preventDefault()
        e.dataTransfer.dropEffect = 'move'
      },
      onDrop: (e: React.DragEvent) => {
        e.preventDefault()
        depth.current = 0
        setOver(false)
        const table = e.dataTransfer.getData(DRAG_TYPE)
        if (table) onDrop(table)
      },
    },
  }
}

function SectionCard({
  draft, index, count, weights, budget, problem, mayEdit, splitting, autoFocus, listId,
  onEdit, onMove, onAdd, onReorder, onDelete, onSplit,
}: {
  draft: SectionDraft
  index: number
  count: number
  weights: Map<string, number>
  budget: number
  problem: string | null
  mayEdit: boolean
  splitting: boolean
  autoFocus: boolean
  listId: string
  onEdit: (patch: Partial<SectionDraft>) => void
  onMove: (table: string, to: string) => void
  onAdd: (table: string) => void
  onReorder: (delta: -1 | 1) => void
  onDelete: () => void
  onSplit: () => void
}) {
  const fit = fitOf(draft.tables, weights, budget)
  const chars = sizeOf(draft.tables, weights)
  const [adding, setAdding] = useState('')
  const [open, setOpen] = useState(false)
  const drop = useDrop((table) => onMove(table, draft.key), mayEdit)
  const shownTables = open ? draft.tables : draft.tables.slice(0, CHIPS_SHOWN)

  function commitAdd(value: string) {
    const table = value.trim()
    if (!weights.has(table)) return
    onAdd(table)
    setAdding('')
  }

  return (
    <section
      className="rm-section-card"
      data-drop={drop.over ? 'true' : undefined}
      aria-label={`Section ${draft.name}`}
      {...drop.handlers}
      style={{
        border: `1px solid ${problem ? 'var(--red-border)' : 'var(--border)'}`,
        borderRadius: 12,
        background: 'var(--panel)',
        boxShadow: 'inset 0 1px 0 0 var(--sheen)',
        padding: '14px 16px 16px',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        {mayEdit ? (
          <TextInput
            aria-label="Section name"
            value={draft.name}
            autoFocus={autoFocus}
            onChange={(e) => onEdit({ name: e.target.value })}
            style={{
              flex: '1 1 220px', maxWidth: 360, fontSize: 14.5, fontWeight: 650,
              padding: '5px 9px', background: 'transparent',
              borderColor: problem ? 'var(--red-border)' : 'transparent',
            }}
            className="rm-section-name"
          />
        ) : (
          <span dir={dirOf(draft.name)} style={{ flex: 1, fontSize: 14.5, fontWeight: 650, color: 'var(--text-strong)' }}>
            {draft.name}
          </span>
        )}
        <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 12, color: 'var(--text-dim)', fontVariantNumeric: 'tabular-nums' }}>
            {draft.tables.length} table{draft.tables.length === 1 ? '' : 's'} · {formatChars(chars)}
          </span>
          <FitBadge fit={fit} />
        </span>
      </div>
      {problem && (
        <span role="alert" style={{ fontSize: 12, color: 'var(--red)', marginTop: -4 }}>{problem}</span>
      )}

      {mayEdit ? (
        <label style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
            What it answers — <strong style={{ color: 'var(--text)', fontWeight: 600 }}>the model reads this to choose a section</strong>
          </span>
          <TextArea
            value={draft.description}
            onChange={(e) => onEdit({ description: e.target.value })}
            placeholder="Orders, order lines, payments and refunds. Answers questions about revenue, order volume, discounts and returns."
            rows={3}
            style={{ minHeight: 78, fontSize: 13, lineHeight: 1.55 }}
          />
          <span style={{ fontSize: 11.5, color: 'var(--text-dim)' }}>
            Sent to the model with the schema, under every disclosure policy — describe the tables, never paste values from them.
          </span>
        </label>
      ) : (
        <p dir={dirOf(draft.description)} style={{ margin: 0, fontSize: 13, lineHeight: 1.55, color: draft.description ? 'var(--text)' : 'var(--text-faint)' }}>
          {draft.description || 'No description.'}
        </p>
      )}

      {fit === 'TOO_LARGE' && (
        <div
          style={{
            display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
            fontSize: 12.5, lineHeight: 1.5, color: 'var(--text)',
            padding: '8px 10px 8px 12px', borderRadius: 8,
            background: 'var(--amber-bg)', border: '1px solid var(--amber-border)',
          }}
        >
          <span aria-hidden style={{ color: 'var(--amber)' }}>⚠</span>
          <span style={{ flex: 1, minWidth: 200 }}>
            Too large to send whole ({formatChars(chars)} of {formatChars(budget)}) — a
            question here falls back to matching within the section.
          </span>
          {mayEdit && (
            <GhostButton onClick={onSplit} disabled={splitting} style={{ padding: '5px 10px', fontSize: 12 }}>
              {splitting && <Spinner size={12} />}
              Split this section
            </GhostButton>
          )}
        </div>
      )}

      <TableChips
        tables={shownTables}
        weights={weights}
        mayEdit={mayEdit}
        onRemove={(table) => onMove(table, UNASSIGNED_KEY)}
        empty={mayEdit ? 'Drag tables here, or add one by name below.' : 'No tables.'}
      />
      {draft.tables.length > CHIPS_SHOWN && (
        <QuietAction onClick={() => setOpen((v) => !v)} style={{ alignSelf: 'flex-start' }}>
          {open ? 'Show fewer' : `Show all ${draft.tables.length}`}
        </QuietAction>
      )}

      {mayEdit && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <TextInput
            aria-label={`Add a table to ${draft.name}`}
            list={listId}
            value={adding}
            dir="ltr"
            placeholder="Add a table by name…"
            onChange={(e) => {
              setAdding(e.target.value)
              // Picking from the list fills the whole name at once.
              if (weights.has(e.target.value) && !draft.tables.includes(e.target.value)) {
                commitAdd(e.target.value)
              }
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                commitAdd(adding)
              }
            }}
            style={{ flex: '1 1 220px', maxWidth: 320, fontSize: 12.5, padding: '5px 9px' }}
          />
          <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 2 }}>
            <QuietAction onClick={() => onReorder(-1)} disabled={index === 0} aria-label="Move section up">
              ↑ Up
            </QuietAction>
            <QuietAction onClick={() => onReorder(1)} disabled={index === count - 1} aria-label="Move section down">
              ↓ Down
            </QuietAction>
            <QuietAction tone="red" onClick={onDelete}>
              <Icon.Trash size={12} /> Delete section
            </QuietAction>
          </span>
        </div>
      )}
    </section>
  )
}

function TableChips({
  tables, weights, mayEdit, onRemove, empty,
}: {
  tables: string[]
  weights: Map<string, number>
  mayEdit: boolean
  onRemove?: (table: string) => void
  empty: string
}) {
  if (tables.length === 0) {
    return <span style={{ fontSize: 12.5, color: 'var(--text-faint)' }}>{empty}</span>
  }
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
      {tables.map((table) => {
        const missing = !weights.has(table)
        return (
          <span
            key={table}
            className="rm-table-chip mono"
            draggable={mayEdit && !missing}
            onDragStart={(e) => {
              e.dataTransfer.setData(DRAG_TYPE, table)
              e.dataTransfer.setData('text/plain', table)
              e.dataTransfer.effectAllowed = 'move'
            }}
            title={missing ? 'Not in the current schema — kept, never sent' : table}
            dir="ltr"
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              fontSize: 12,
              padding: mayEdit ? '3px 4px 3px 8px' : '3px 8px',
              borderRadius: 6,
              border: '1px solid var(--border)',
              background: 'var(--panel-alt)',
              color: missing ? 'var(--text-faint)' : 'var(--text)',
              textDecoration: missing ? 'line-through' : undefined,
              cursor: mayEdit && !missing ? 'grab' : 'default',
              maxWidth: '100%',
            }}
          >
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{table}</span>
            {mayEdit && onRemove && (
              <button
                type="button"
                className="rm-chip-x"
                aria-label={`Remove ${table}`}
                onClick={() => onRemove(table)}
                style={{
                  display: 'grid', placeItems: 'center', width: 18, height: 18,
                  border: 'none', borderRadius: 4, background: 'transparent',
                  color: 'var(--text-dim)', cursor: 'pointer', padding: 0,
                }}
              >
                <Icon.Close size={11} />
              </button>
            )}
          </span>
        )
      })}
    </div>
  )
}

function UnassignedCard({
  tables, mayEdit, onMove,
}: {
  tables: string[]
  mayEdit: boolean
  onMove: (table: string, to: string) => void
}) {
  const drop = useDrop((table) => onMove(table, UNASSIGNED_KEY), mayEdit)
  const [open, setOpen] = useState(false)
  const shownTables = open ? tables : tables.slice(0, CHIPS_SHOWN)
  const weights = useMemo(() => new Map(tables.map((t) => [t, 1] as const)), [tables])
  return (
    <section
      className="rm-section-card"
      data-drop={drop.over ? 'true' : undefined}
      aria-label={UNASSIGNED}
      {...drop.handlers}
      style={{
        border: '1px dashed var(--border-strong)',
        borderRadius: 12,
        background: 'transparent',
        padding: '14px 16px 16px',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 14, fontWeight: 650, color: 'var(--text-strong)' }}>{UNASSIGNED}</span>
        <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
          {tables.length} table{tables.length === 1 ? '' : 's'} · never chosen for a question on their own
        </span>
      </div>
      <TableChips
        tables={shownTables}
        weights={weights}
        mayEdit={mayEdit}
        empty="Every table is in a section."
      />
      {tables.length > CHIPS_SHOWN && (
        <QuietAction onClick={() => setOpen((v) => !v)} style={{ alignSelf: 'flex-start' }}>
          {open ? 'Show fewer' : `Show all ${tables.length}`}
        </QuietAction>
      )}
    </section>
  )
}

// ── the save bar ──────────────────────────────────────────────────────────
function SaveBar({
  changed, saving, notice, blocked, onSave, onDiscard, onDismiss,
}: {
  changed: boolean
  saving: boolean
  notice: string | null
  blocked: boolean
  onSave: () => void
  onDiscard: () => void
  onDismiss: () => void
}) {
  return (
    <div
      style={{
        position: 'absolute', left: 0, right: 0, bottom: 20,
        display: 'flex', justifyContent: 'center', pointerEvents: 'none', zIndex: 30,
      }}
    >
      <div
        className="rm-enter"
        style={{
          pointerEvents: 'auto',
          display: 'flex', flexDirection: 'column', gap: 10,
          maxWidth: 'calc(100% - 32px)',
          padding: '10px 12px 10px 18px',
          borderRadius: 12,
          background: 'var(--panel)',
          border: '1px solid var(--border-strong)',
          boxShadow: 'inset 0 1px 0 0 var(--sheen), var(--elev-3)',
        }}
      >
        {notice && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: 'var(--text-dim)' }}>
            <span style={{ color: 'var(--green)', display: 'flex' }}><Icon.Check /></span>
            <span style={{ flex: 1 }}>{notice}</span>
            {!changed && (
              <GhostButton onClick={onDismiss} style={{ padding: '4px 9px', fontSize: 12 }}>OK</GhostButton>
            )}
          </div>
        )}
        {changed && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, justifyContent: 'space-between', flexWrap: 'wrap' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: 'var(--text-dim)' }}>
              <span aria-hidden style={{ width: 6, height: 6, borderRadius: 999, background: 'var(--amber)' }} />
              {blocked ? 'Fix the highlighted names to save' : 'Unsaved changes'}
            </span>
            <span style={{ display: 'flex', gap: 8 }}>
              <GhostButton onClick={onDiscard} disabled={saving} style={{ padding: '7px 12px' }}>
                Discard
              </GhostButton>
              <PrimaryButton onClick={onSave} disabled={saving || blocked} style={{ padding: '7px 14px' }}>
                {saving && <Spinner />}
                Save sections
              </PrimaryButton>
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
