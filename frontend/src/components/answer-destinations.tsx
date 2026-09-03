/**
 * Where an answer can go next: a dashboard tile, or a block in a report.
 *
 * The backend has always treated Chat, Dashboards and Reports as one guarded
 * query path. The frontend did not: a finished answer offered Copy, Copy SQL
 * and *Save as template*, and building a tile from what you were looking at
 * meant reopening the same question in a different box and asking again.
 *
 * Two rules hold both of these dialogs together, and they are the reason this
 * is worth a module rather than an inline menu:
 *
 *  - **Nothing is re-asked and nothing is re-run.** The statement that travels
 *    is the run's own — the one the reader watched succeed — exactly as the
 *    chart picker redraws from rows already returned. Asking the model again
 *    would spend a call to maybe get different SQL, and a tile whose query is
 *    not the query you approved is the whole problem this feature exists to
 *    solve.
 *  - **A destination is picked, never guessed.** Both dialogs list what exists
 *    and let one be made on the spot, because "add to dashboard" with no
 *    dashboard is a dead end, and silently creating one called *Untitled* is
 *    worse than asking for a name.
 *
 * The two differ in one way that is not cosmetic: a dashboard tile carries its
 * own connection, so any dashboard will do, while a report is **pinned to one
 * connection forever** — so the report list is filtered to the thread's own,
 * and a new one is created against it.
 */
import { useEffect, useMemo, useState } from 'react'

import { dashboards as dashboardsApi, reports as reportsApi } from '../api/client'
import type { Connection, DashboardSummary, ReportSummary } from '../api/types'
import {
  ErrorNote, Field, GhostButton, Icon, Modal, PrimaryButton, Spinner, TextInput,
} from './ui'

/** The name a new destination gets when the answer's question is the only clue. */
function suggestedName(question: string): string {
  const clean = question.trim().replace(/\s+/g, ' ')
  return clean.length > 60 ? `${clean.slice(0, 57)}…` : clean
}

// ── one list, both dialogs ────────────────────────────────────────────────
/**
 * The pick list: what exists, then the row that makes a new one.
 *
 * A radio group would be the textbook control and reads worse here — these are
 * destinations, not settings, and the list is usually two or three long. The
 * selected row carries the accent the rail and the master column use for "you
 * are here", so the whole product marks a choice the same way.
 */
function DestinationList<T extends { id: string; name: string }>({
  items, value, onChange, newLabel, subtitleOf,
}: {
  items: T[]
  /** An id, or `null` for "make a new one". */
  value: string | null
  onChange: (value: string | null) => void
  newLabel: string
  subtitleOf?: (item: T) => string | null
}) {
  return (
    <div
      role="listbox"
      aria-label="Destination"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 2,
        maxHeight: 260,
        overflowY: 'auto',
      }}
    >
      {items.map((item) => {
        const active = item.id === value
        const subtitle = subtitleOf?.(item)
        return (
          <button
            key={item.id}
            type="button"
            role="option"
            aria-selected={active}
            className={active ? undefined : 'rm-menu-item'}
            onClick={() => onChange(item.id)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 9,
              width: '100%',
              padding: '9px 11px',
              borderRadius: 8,
              border: 'none',
              textAlign: 'left',
              cursor: 'pointer',
              background: active ? 'var(--accent-bg)' : 'transparent',
              color: active ? 'var(--text-strong)' : 'var(--text)',
            }}
          >
            <span style={{ flex: 1, minWidth: 0 }}>
              <span
                style={{
                  display: 'block',
                  fontSize: 13,
                  fontWeight: active ? 600 : 500,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {item.name}
              </span>
              {subtitle && (
                <span style={{ display: 'block', fontSize: 11.5, color: 'var(--text-faint)' }}>
                  {subtitle}
                </span>
              )}
            </span>
            {active && <Icon.Check size={14} stroke="var(--accent)" />}
          </button>
        )
      })}

      <button
        type="button"
        role="option"
        aria-selected={value === null}
        className={value === null ? undefined : 'rm-menu-item'}
        onClick={() => onChange(null)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 9,
          width: '100%',
          padding: '9px 11px',
          borderRadius: 8,
          border: 'none',
          textAlign: 'left',
          cursor: 'pointer',
          background: value === null ? 'var(--accent-bg)' : 'transparent',
          color: value === null ? 'var(--text-strong)' : 'var(--text-dim)',
          fontSize: 13,
          fontWeight: value === null ? 600 : 500,
        }}
      >
        <Icon.Plus size={13} />
        {newLabel}
      </button>
    </div>
  )
}

// ── add to a dashboard ────────────────────────────────────────────────────
/**
 * Pick the board, then hand off to the tile editor.
 *
 * This dialog deliberately does **not** create the tile. The editor is where a
 * tile is given its type, its chart, its refresh rate and its title, and it
 * opens over the grid the tile is joining — so this ends by opening that
 * editor prefilled, which is one screen further on rather than one screen
 * back. `onPicked` navigates.
 */
export function AddToDashboardDialog({
  question, onClose, onPicked,
}: {
  /** Names the new dashboard, when one is made here. */
  question: string
  onClose: () => void
  onPicked: (dashboardId: string) => void
}) {
  const [list, setList] = useState<DashboardSummary[] | null>(null)
  const [choice, setChoice] = useState<string | null>(null)
  const [name, setName] = useState(() => suggestedName(question))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    dashboardsApi
      .list()
      .then((items) => {
        if (cancelled) return
        const active = items.filter((item) => item.status !== 'ARCHIVED')
        setList(active)
        // The most recently touched one is where work is going, so it is the
        // offer; with nothing to offer, the dialog opens on the name field.
        setChoice(active[0]?.id ?? null)
      })
      .catch(() => {
        if (!cancelled) setError('Your dashboards could not be loaded.')
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function confirm() {
    setBusy(true)
    setError(null)
    try {
      const id = choice ?? (await dashboardsApi.create({ name: name.trim() })).id
      onPicked(id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That dashboard could not be created.')
      setBusy(false)
    }
  }

  const canConfirm = choice !== null || name.trim().length > 0

  return (
    <Modal
      title="Add to a dashboard"
      subtitle="The tile editor opens next, carrying this answer's question and the statement that produced it."
      width={440}
      onClose={onClose}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton onClick={() => void confirm()} disabled={busy || !canConfirm}>
            {busy ? 'Opening…' : 'Continue'}
          </PrimaryButton>
        </>
      }
    >
      {list === null ? (
        <div style={{ display: 'grid', placeItems: 'center', padding: 24 }}>
          <Spinner size={16} />
        </div>
      ) : (
        <>
          {list.length > 0 && (
            <DestinationList
              items={list}
              value={choice}
              onChange={setChoice}
              newLabel="New dashboard…"
              subtitleOf={(item) =>
                `${item.tile_count} ${item.tile_count === 1 ? 'tile' : 'tiles'}`
              }
            />
          )}
          {choice === null && (
            <Field
              label="Name"
              hint={list.length === 0 ? 'Your first dashboard. Tiles from other connections can join it later.' : undefined}
            >
              <TextInput
                autoFocus
                value={name}
                placeholder="e.g. Weekly trading"
                onChange={(event) => setName(event.target.value)}
              />
            </Field>
          )}
          {error && <ErrorNote>{error}</ErrorNote>}
        </>
      )}
    </Modal>
  )
}

// ── add to a report ───────────────────────────────────────────────────────
/**
 * Pick the report, then write the block.
 *
 * Unlike the dashboard road this one finishes the job here: a block is a
 * question and a statement, both of which are already in hand, and there is no
 * per-block editor to hand off to. Two calls do it — create the block from the
 * question, then `PUT .../sql` with the run's own statement — and that order
 * is what marks the SQL as the user's own work: the write route derives
 * provenance from what the block held, and a block that has never held a
 * generated statement comes out `HANDWRITTEN`. That matters beyond a chip:
 * *Check all* sweeps only blocks a check would **fill**, so this statement is
 * never quietly replaced by one a model wrote later.
 */
export function AddToReportDialog({
  connection, modelId, question, sql, onClose, onAdded,
}: {
  /** The thread's connection. A report is pinned to one forever. */
  connection: Connection
  /** The thread's model, for a report created here. Narration needs one. */
  modelId: string | null
  question: string
  sql: string
  onClose: () => void
  onAdded: (reportId: string, sectionHeading: string) => void
}) {
  const [list, setList] = useState<ReportSummary[] | null>(null)
  const [choice, setChoice] = useState<string | null>(null)
  const [name, setName] = useState(() => suggestedName(question))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    reportsApi
      .list()
      .then((items) => {
        if (cancelled) return
        // A report cannot cross connections, so one bound elsewhere is not a
        // destination for this answer — it is filtered out rather than offered
        // and refused.
        const usable = items.filter(
          (item) => item.status !== 'ARCHIVED' && item.connection_id === connection.id,
        )
        setList(usable)
        setChoice(usable[0]?.id ?? null)
      })
      .catch(() => {
        if (!cancelled) setError('Your reports could not be loaded.')
      })
    return () => {
      cancelled = true
    }
  }, [connection.id])

  async function confirm() {
    setBusy(true)
    setError(null)
    try {
      const reportId =
        choice
        ?? (
          await reportsApi.create({
            name: name.trim(),
            // The question is the request: the document's language is read off
            // this, and there is nothing else here that was written by a human
            // about what the report is for.
            prompt: question.trim(),
            connection_id: connection.id,
            llm_config_id: modelId,
          })
        ).id

      const report = await reportsApi.get(reportId)
      // Never the executive summary: that section is written *from* the rest
      // of the document, and a figure filed under it would be evidence for a
      // paragraph that summarises evidence.
      const body = report.sections.filter((section) => section.kind !== 'EXECUTIVE_SUMMARY')
      const section =
        body.at(-1)
        ?? (await reportsApi.addSection(reportId, { heading: 'Findings', intent: '' }))

      // No title: an empty one means the document captions the figure with the
      // question, which is what a caption nobody wrote should be.
      const block = await reportsApi.addBlock(reportId, section.id, { question: question.trim() })
      await reportsApi.editBlockSql(reportId, block.id, sql)
      onAdded(reportId, section.heading)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That block could not be added.')
      setBusy(false)
    }
  }

  const canConfirm = choice !== null || name.trim().length > 0
  const nothingBound = useMemo(() => list !== null && list.length === 0, [list])

  return (
    <Modal
      title="Add to a report"
      subtitle={`As a figure, with the statement that produced it kept as written. ${connection.name} only — a report is pinned to one connection.`}
      width={440}
      onClose={onClose}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton onClick={() => void confirm()} disabled={busy || !canConfirm}>
            {busy ? 'Adding…' : 'Add figure'}
          </PrimaryButton>
        </>
      }
    >
      {list === null ? (
        <div style={{ display: 'grid', placeItems: 'center', padding: 24 }}>
          <Spinner size={16} />
        </div>
      ) : (
        <>
          {list.length > 0 && (
            <DestinationList
              items={list}
              value={choice}
              onChange={setChoice}
              newLabel="New report…"
              subtitleOf={(item) =>
                `${item.section_count} ${item.section_count === 1 ? 'section' : 'sections'}`
              }
            />
          )}
          {choice === null && (
            <Field
              label="Name"
              hint={
                nothingBound
                  ? `No report is bound to ${connection.name} yet. This one will be, and the question becomes its request.`
                  : undefined
              }
            >
              <TextInput
                autoFocus
                value={name}
                placeholder="e.g. Quarterly trading review"
                onChange={(event) => setName(event.target.value)}
              />
            </Field>
          )}
          {error && <ErrorNote>{error}</ErrorNote>}
        </>
      )}
    </Modal>
  )
}
