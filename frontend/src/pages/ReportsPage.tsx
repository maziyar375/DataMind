/**
 * Reports: the index, and the dialog that starts one.
 *
 * A third section peer to Chat and Dashboards. Chat answers one question and
 * moves on; a dashboard watches numbers that are always current; a **report is
 * a document** — a structure the user approved, prose written over real
 * results, and a run kept as a snapshot of a moment (docs/reports-plan.md §1).
 *
 * This file is the shell: list, create, rename, archive, delete, and the switch
 * between the index and one open report. The outline editor itself lives in
 * `components/report.tsx`, on the same split `DashboardsPage` uses — an index
 * of cards, then one document filling the page.
 *
 * The create dialog carries the one rule that is not CRUD: **a report cannot be
 * created against a connection that shares no result values.** A document whose
 * charts carry real numbers and whose paragraphs carry none is worse than no
 * document, so the picker greys those connections out and says why, rather than
 * accepting the choice and apologising after the fact (§7).
 */
import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError, connections as connectionsApi, llmConfigs as modelsApi, reports as api } from '../api/client'
import type { Connection, LlmConfig, Report, ReportLanguage, ReportSummary } from '../api/types'
import { ReportOutlineEditor, ReportRunViewer } from '../components/report'
import { ReportRunHistory } from '../components/report-history'
import {
  Chip, DisclosureBadge, EmptyState, ErrorNote, Field, GhostButton, Icon, Modal,
  PrimaryButton, SearchField, Segmented, Spinner, TextArea, TextInput, relativeTime,
} from '../components/ui'

/** The policies a report can be written from — the frontend half of §7. */
const WIDE_ENOUGH = ['SAMPLE', 'FULL']

const LANGUAGES: { value: ReportLanguage; label: string }[] = [
  { value: 'en', label: 'English' },
  { value: 'fa', label: 'فارسی' },
]

type SortKey = 'updated' | 'created' | 'name'
type StatusFilter = 'ACTIVE' | 'ARCHIVED' | 'ALL'

const SORTS: { value: SortKey; label: string }[] = [
  { value: 'updated', label: 'Recently updated' },
  { value: 'created', label: 'Recently created' },
  { value: 'name', label: 'Name (A–Z)' },
]

function sortCards(cards: ReportSummary[], key: SortKey): ReportSummary[] {
  const at = (value: string) => new Date(value).getTime()
  return [...cards].sort((a, b) => {
    if (key === 'name') return a.name.localeCompare(b.name)
    if (key === 'created') return at(b.created_at) - at(a.created_at)
    return at(b.updated_at) - at(a.updated_at)
  })
}

export default function ReportsPage() {
  const [openId, setOpenId] = useState<string | null>(null)
  // Which of the report's runs is being read, if any. The outline and the
  // document are two views of one open report, not two pages: Generate goes
  // from the first to the second, and Back comes home.
  const [runId, setRunId] = useState<string | null>(null)
  // …and the third view: every generation this report has produced. It is a
  // view of the open report rather than a page of its own, so it clears with
  // the report the way the run does.
  const [history, setHistory] = useState(false)
  // The index's own copy of the list, held here so an edit made inside the
  // editor is spliced into the card the user comes back to rather than costing
  // a re-read of every report.
  const [cards, setCards] = useState<ReportSummary[] | null>(null)

  const card = openId ? (cards?.find((entry) => entry.id === openId) ?? null) : null

  /** Home is the outline: it is the report, and the other two are views of it. */
  function toOutline() {
    setRunId(null)
    setHistory(false)
  }

  if (openId && runId) {
    return (
      <ReportRunViewer
        reportId={openId}
        runId={runId}
        // The card, not just the name: a document's cover states which database
        // it describes, and only the index holds that.
        report={card}
        reportName={card?.name ?? 'Report'}
        onBack={toOutline}
        onHistory={() => {
          setRunId(null)
          setHistory(true)
        }}
      />
    )
  }

  if (openId && history) {
    return (
      <ReportRunHistory
        reportId={openId}
        reportName={card?.name ?? 'Report'}
        currentRunId={runId}
        onOpenRun={(id) => {
          setHistory(false)
          setRunId(id)
        }}
        onBack={toOutline}
      />
    )
  }

  return openId ? (
    <ReportOutlineEditor
      reportId={openId}
      onBack={() => setOpenId(null)}
      onOpenRun={setRunId}
      onHistory={() => setHistory(true)}
      onChanged={(report) =>
        setCards((current) =>
          (current ?? []).map((card) => (card.id === report.id ? toCard(report) : card)),
        )
      }
    />
  ) : (
    <ReportsIndex
      cards={cards}
      setCards={setCards}
      onOpen={(id) => {
        // A report opens on its outline, whatever view the last one was left in.
        setRunId(null)
        setHistory(false)
        setOpenId(id)
      }}
    />
  )
}

function ReportsIndex({
  cards, setCards, onOpen,
}: {
  cards: ReportSummary[] | null
  setCards: React.Dispatch<React.SetStateAction<ReportSummary[] | null>>
  onOpen: (id: string) => void
}) {
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [renaming, setRenaming] = useState<ReportSummary | null>(null)
  const [busy, setBusy] = useState(false)

  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<SortKey>('updated')
  const [status, setStatus] = useState<StatusFilter>('ACTIVE')

  const load = useCallback(async () => {
    try {
      setCards(await api.list())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load reports.')
      setCards([])
    }
  }, [setCards])

  useEffect(() => {
    void load()
  }, [load])

  const guard = useCallback(
    async (run: () => Promise<unknown>) => {
      setBusy(true)
      try {
        await run()
        await load()
      } catch (err) {
        setError(err instanceof Error ? err.message : 'That did not work.')
      } finally {
        setBusy(false)
      }
    },
    [load],
  )

  const visible = useMemo(() => {
    if (!cards) return []
    const needle = query.trim().toLowerCase()
    const matched = cards.filter((card) => {
      if (status !== 'ALL' && card.status !== status) return false
      if (!needle) return true
      return (
        card.name.toLowerCase().includes(needle)
        || (card.description ?? '').toLowerCase().includes(needle)
      )
    })
    return sortCards(matched, sort)
  }, [cards, query, sort, status])

  const archivedCount = useMemo(
    () => (cards ?? []).filter((card) => card.status === 'ARCHIVED').length,
    [cards],
  )
  const activeCount = (cards ?? []).length - archivedCount
  const filtering = query.trim().length > 0 || status !== 'ACTIVE'

  return (
    <div className="rm-dash-index rm-page-pad" style={{ flex: 1, overflowY: 'auto' }}>
      <header
        style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-start', gap: 12, marginBottom: 18 }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 0 }}>
          <h1
            style={{
              margin: 0,
              fontSize: 25,
              fontWeight: 700,
              letterSpacing: '-0.022em',
              color: 'var(--text-strong)',
            }}
          >
            Reports
          </h1>
          <span style={{ fontSize: 13.5, color: 'var(--text-dim)' }}>
            {cards === null || cards.length === 0 ? (
              'Written documents built from your data — describe one, approve its outline, and keep every run.'
            ) : (
              <>
                <strong style={{ color: 'var(--text-strong)', fontWeight: 600 }}>{activeCount}</strong>
                {` ${activeCount === 1 ? 'report' : 'reports'}`}
                {archivedCount > 0 && (
                  <>
                    <span aria-hidden style={{ opacity: 0.4 }}> · </span>
                    {archivedCount} archived
                  </>
                )}
              </>
            )}
          </span>
        </div>
        <PrimaryButton
          style={{ marginLeft: 'auto', padding: '10px 17px' }}
          onClick={() => setCreating(true)}
          disabled={busy}
        >
          <Icon.Plus /> New report
        </PrimaryButton>
      </header>

      {error && <div style={{ marginBottom: 14 }}><ErrorNote>{error}</ErrorNote></div>}

      {cards !== null && cards.length > 0 && (
        <div className="rm-dash-toolbar">
          <SearchField
            value={query}
            onChange={setQuery}
            ariaLabel="Search reports"
            placeholder={`Search ${cards.length} report${cards.length === 1 ? '' : 's'}…`}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginLeft: 'auto' }}>
            {/* Offered only once something is archived: a permanently empty
                filter is furniture, not a control. */}
            {archivedCount > 0 && (
              <Segmented
                ariaLabel="Filter by status"
                value={status}
                onChange={setStatus}
                options={[
                  { value: 'ACTIVE', label: 'Active' },
                  { value: 'ARCHIVED', label: `Archived (${archivedCount})` },
                  { value: 'ALL', label: 'All' },
                ]}
              />
            )}
            <select
              aria-label="Sort reports"
              value={sort}
              onChange={(event) => setSort(event.target.value as SortKey)}
              className="rm-toolbar-select"
            >
              {SORTS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {cards === null ? (
        <IndexSkeleton />
      ) : cards.length === 0 ? (
        <EmptyState
          icon={<Icon.Doc size={20} />}
          title="No reports yet"
          body="Describe what you need in plain language — “an analysis of the last three months of sales” — and a report proposes its own structure. Approve it, and every section is written from your database."
          action={<PrimaryButton onClick={() => setCreating(true)}>New report</PrimaryButton>}
        />
      ) : visible.length === 0 ? (
        <EmptyState
          icon={<Icon.Search size={20} />}
          title="Nothing matches"
          body={
            query.trim()
              ? `No report is called “${query.trim()}”, and none mentions it in a description.`
              : 'No report has that status.'
          }
          action={
            filtering && (
              <GhostButton
                onClick={() => {
                  setQuery('')
                  setStatus('ACTIVE')
                }}
              >
                Clear filters
              </GhostButton>
            )
          }
        />
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(284px, 1fr))',
            gap: 16,
          }}
        >
          {visible.map((card) => (
            <ReportCard
              key={card.id}
              report={card}
              onOpen={() => onOpen(card.id)}
              onRename={() => setRenaming(card)}
              onArchive={() =>
                void guard(() =>
                  api.update(card.id, {
                    status: card.status === 'ARCHIVED' ? 'ACTIVE' : 'ARCHIVED',
                  }),
                )
              }
              onDelete={() => void guard(() => api.remove(card.id))}
            />
          ))}
        </div>
      )}

      {creating && (
        <CreateDialog
          onClose={() => setCreating(false)}
          onCreated={(report) => {
            // Spliced from the response, never re-read: the read-after-write
            // race is app-wide and this page must not walk into it.
            setCards((current) => [toCard(report), ...(current ?? [])])
            setStatus('ACTIVE')
            setCreating(false)
            // Straight into the outline. A report with no structure can do
            // nothing at all, and the create dialog just asked for the request
            // the structure is proposed from — sending the user back to a card
            // to find their way in would be one step for no reason.
            onOpen(report.id)
          }}
        />
      )}

      {renaming && (
        <RenameDialog
          report={renaming}
          onClose={() => setRenaming(null)}
          onSaved={(updated) => {
            setCards((current) =>
              (current ?? []).map((card) => (card.id === updated.id ? updated : card)),
            )
            setRenaming(null)
          }}
        />
      )}
    </div>
  )
}

/** A full report as the index holds it — the same fields, one shape. */
function toCard(report: Report): ReportSummary {
  return {
    id: report.id,
    name: report.name,
    description: report.description,
    connection_id: report.connection_id,
    connection_name: report.connection_name,
    llm_config_id: report.llm_config_id,
    llm_config_name: report.llm_config_name,
    language: report.language,
    status: report.status,
    section_count: report.sections.length,
    created_at: report.created_at,
    updated_at: report.updated_at,
  }
}

// ── one card ──────────────────────────────────────────────────────────────
const CARD_HUES = [265, 210, 150, 25, 330, 190]

function cardHue(id: string): number {
  let hash = 0
  for (let index = 0; index < id.length; index += 1) {
    hash = (hash * 31 + id.charCodeAt(index)) >>> 0
  }
  return CARD_HUES[hash % CARD_HUES.length]
}

function ReportCard({
  report, onOpen, onRename, onArchive, onDelete,
}: {
  report: ReportSummary
  onOpen: () => void
  onRename: () => void
  onArchive: () => void
  onDelete: () => void
}) {
  const hue = cardHue(report.id)
  const archived = report.status === 'ARCHIVED'
  const language = LANGUAGES.find((entry) => entry.value === report.language)

  return (
    // `rm-dash-card` carries the pointer and the hover lift of a card you can
    // open — withheld until there *was* something to open, which is now.
    <div
      className="rm-dash-card"
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        gap: 7,
        padding: '14px 15px 13px',
        background: 'var(--panel)',
        borderRadius: 14,
        opacity: archived ? 0.66 : 1,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
        <span
          aria-hidden
          style={{
            display: 'grid',
            placeItems: 'center',
            width: 34,
            height: 34,
            flexShrink: 0,
            borderRadius: 10,
            background: `oklch(0.7 0.16 ${hue} / 0.16)`,
            border: `1px solid oklch(0.7 0.16 ${hue} / 0.3)`,
            color: `oklch(0.65 0.17 ${hue})`,
          }}
        >
          <Icon.Doc size={16} />
        </span>
        {/* The name is the link, and `rm-dash-card-link` stretches its hit area
            over the whole card — so the kebab beside it still gets its own
            clicks instead of opening the report. */}
        <button
          className="rm-dash-card-link"
          onClick={onOpen}
          dir="auto"
          title={report.name}
          style={{
            flex: 1,
            minWidth: 0,
            textAlign: 'left',
            background: 'transparent',
            border: 'none',
            padding: 0,
            cursor: 'pointer',
            color: 'var(--text-strong)',
            fontSize: 14.5,
            fontWeight: 650,
            letterSpacing: '-0.01em',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {report.name}
        </button>
        <ReportMenu
          report={report}
          onRename={onRename}
          onArchive={onArchive}
          onDelete={onDelete}
        />
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {archived && <Chip tone="amber">Archived</Chip>}
        {/* Unfinished, and invisible otherwise: a report with no outline has
            nothing to generate. */}
        {report.section_count === 0 ? (
          <Chip tone="neutral">No outline yet</Chip>
        ) : (
          <Chip tone="neutral">
            {report.section_count} {report.section_count === 1 ? 'section' : 'sections'}
          </Chip>
        )}
        {language && <Chip tone="neutral">{language.label}</Chip>}
      </div>

      <span
        dir="auto"
        style={{
          fontSize: 12.5,
          color: report.description ? 'var(--text-dim)' : 'var(--text-faint)',
          fontStyle: report.description ? undefined : 'italic',
          lineHeight: 1.5,
          minHeight: 37,
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          overflow: 'hidden',
        }}
      >
        {report.description || 'No description'}
      </span>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 6,
          marginTop: 'auto',
          paddingTop: 10,
          borderTop: '1px solid var(--border)',
          fontSize: 11.5,
          color: 'var(--text-faint)',
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, minWidth: 0 }}>
          <Icon.Database size={12} />
          {/* A deleted connection is SET NULL, and the report stays readable —
              so the card has to have something to say about that. */}
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {report.connection_name ?? 'Connection removed'}
          </span>
        </span>
        <span aria-hidden style={{ opacity: 0.4 }}>·</span>
        <span>updated {relativeTime(report.updated_at)}</span>
      </div>
    </div>
  )
}

function ReportMenu({
  report, onRename, onArchive, onDelete,
}: {
  report: ReportSummary
  onRename: () => void
  onArchive: () => void
  onDelete: () => void
}) {
  const [open, setOpen] = useState(false)
  const items = [
    { label: 'Rename', run: onRename },
    { label: report.status === 'ARCHIVED' ? 'Unarchive' : 'Archive', run: onArchive },
    { label: 'Delete', run: onDelete, danger: true },
  ]

  return (
    <div style={{ position: 'relative', zIndex: 2 }}>
      <button
        className="rm-icon-btn"
        aria-label={`Actions for ${report.name}`}
        onClick={() => setOpen((current) => !current)}
        style={{
          display: 'flex',
          width: 27,
          height: 27,
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 8,
          border: 'none',
          background: open ? 'var(--panel-alt)' : 'transparent',
          color: 'var(--text-dim)',
          cursor: 'pointer',
          ['--rm-hover-bg' as string]: 'var(--panel-alt)',
        }}
      >
        <Icon.More size={14} />
      </button>
      {open && (
        <>
          <div
            onClick={() => setOpen(false)}
            style={{ position: 'fixed', inset: 0, zIndex: 40 }}
            aria-hidden
          />
          <div
            role="menu"
            className="rm-enter"
            style={{
              position: 'absolute',
              top: 31,
              right: 0,
              zIndex: 41,
              minWidth: 152,
              padding: 5,
              background: 'var(--panel)',
              border: '1px solid var(--border-strong)',
              borderRadius: 10,
              boxShadow: '0 16px 40px -14px rgba(0,0,0,.5)',
            }}
          >
            {items.map((item) => (
              <button
                key={item.label}
                role="menuitem"
                className="rm-menu-item"
                onClick={() => {
                  setOpen(false)
                  item.run()
                }}
                style={{
                  display: 'block',
                  width: '100%',
                  padding: '7px 9px',
                  textAlign: 'left',
                  background: 'transparent',
                  border: 'none',
                  borderRadius: 7,
                  cursor: 'pointer',
                  fontSize: 12.5,
                  fontFamily: 'inherit',
                  color: item.danger ? 'var(--red)' : 'var(--text)',
                }}
              >
                {item.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

/** Cards in outline while the list loads, so the page does not jump. */
function IndexSkeleton() {
  return (
    <div
      aria-hidden
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(284px, 1fr))',
        gap: 16,
      }}
    >
      {[0, 1, 2].map((index) => (
        <div
          key={index}
          style={{
            background: 'var(--panel)',
            border: '1px solid var(--border)',
            borderRadius: 14,
            padding: '14px 15px',
            display: 'flex',
            flexDirection: 'column',
            gap: 9,
          }}
        >
          <div className="rm-bone" style={{ width: '58%', height: 14, borderRadius: 6 }} />
          <div className="rm-bone" style={{ width: '35%', height: 9, borderRadius: 6 }} />
          <div className="rm-bone" style={{ width: '92%', height: 9, borderRadius: 6, marginTop: 8 }} />
          <div className="rm-bone" style={{ width: '44%', height: 9, borderRadius: 6, marginTop: 10 }} />
        </div>
      ))}
    </div>
  )
}

// ── creating one ──────────────────────────────────────────────────────────
/**
 * Everything a report is pinned to, asked once.
 *
 * The connection and the language are **pinned forever** — one because a report
 * keyed to two connections would cross disclosure policies, the other because
 * every past run's prose is written in it. The model is not: it decides who
 * writes the prose, not what is in it. The dialog says so rather than leaving
 * the user to discover it from a 422.
 */
function CreateDialog({
  onClose, onCreated,
}: {
  onClose: () => void
  onCreated: (report: Awaited<ReturnType<typeof api.create>>) => void
}) {
  const [choices, setChoices] = useState<{ connections: Connection[]; models: LlmConfig[] } | null>(null)
  const [name, setName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [connectionId, setConnectionId] = useState<string | null>(null)
  const [modelId, setModelId] = useState<string>('')
  const [language, setLanguage] = useState<ReportLanguage>('en')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const [connections, models] = await Promise.all([
          connectionsApi.list(),
          modelsApi.list(),
        ])
        if (cancelled) return
        setChoices({ connections, models })
        // Preselect the first connection a report can actually be written
        // from, and the first model — the common case is one of each.
        setConnectionId(connections.find((c) => eligible(c))?.id ?? null)
        setModelId(models[0]?.id ?? '')
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load your connections.')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const connection = choices?.connections.find((c) => c.id === connectionId) ?? null

  async function submit() {
    if (!name.trim()) return setError('A report needs a name.')
    if (!connectionId) return setError('Choose the database this report reads from.')
    setSaving(true)
    setError(null)
    try {
      onCreated(
        await api.create({
          name: name.trim(),
          prompt: prompt.trim(),
          connection_id: connectionId,
          llm_config_id: modelId || null,
          language,
        }),
      )
    } catch (err) {
      // A duplicate name is a 409 and the disclosure refusal is a 422; both
      // carry a sentence worth showing exactly as written.
      setError(err instanceof ApiError ? err.message : 'That did not work.')
      setSaving(false)
    }
  }

  return (
    <Modal
      title="New report"
      subtitle="The database and the language are fixed once it exists. The model can change any time."
      width={560}
      onClose={onClose}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton onClick={() => void submit()} disabled={saving || !choices}>
            {saving ? 'Creating…' : 'Create report'}
          </PrimaryButton>
        </>
      }
    >
      {!choices ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '18px 0', color: 'var(--text-dim)', fontSize: 13 }}>
          <Spinner /> Loading your connections…
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Field label="Name">
            <TextInput
              autoFocus
              value={name}
              placeholder="e.g. Quarterly sales review"
              onChange={(event) => setName(event.target.value)}
            />
          </Field>

          <Field
            label="What should this report cover?"
            hint="Plain language. This is what the outline is proposed from, and what every paragraph is written towards."
          >
            <TextArea
              value={prompt}
              rows={3}
              placeholder="e.g. an analysis of the last three months of sales, with the trend and the products that carried it"
              onChange={(event) => setPrompt(event.target.value)}
            />
          </Field>

          <Field label="Database">
            <ConnectionPicker
              connections={choices.connections}
              value={connectionId}
              onChange={setConnectionId}
            />
          </Field>

          {/* An unsynced connection can answer nothing, so its outline would be
              invention — the backend refuses at that point. Said here, where it
              is still cheap to fix, and as a warning rather than a block: the
              report is fine to create now and sync later. */}
          {connection && !connection.last_synced_at && (
            <div
              style={{
                display: 'flex',
                gap: 8,
                padding: '9px 11px',
                fontSize: 12,
                lineHeight: 1.5,
                color: 'var(--text2)',
                background: 'var(--amber-bg)',
                border: '1px solid var(--amber-border)',
                borderRadius: 9,
              }}
            >
              <span aria-hidden style={{ display: 'flex', color: 'var(--amber)', paddingTop: 1 }}>
                <Icon.Alert size={13} />
              </span>
              <span>
                <strong style={{ fontWeight: 600 }}>{connection.name}</strong> has never had its
                schema synced. Sync it under Data sources before proposing an outline.
              </span>
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 12, alignItems: 'end' }}>
            <Field
              label="Model"
              hint={
                choices.models.length === 0
                  ? 'Add one under LLM providers — a report needs a model to propose its outline and write its prose.'
                  : 'Changeable at any time.'
              }
            >
              <select
                aria-label="Model"
                value={modelId}
                disabled={choices.models.length === 0}
                onChange={(event) => setModelId(event.target.value)}
                className="rm-toolbar-select"
                style={{ width: '100%', padding: '9px 11px' }}
              >
                {choices.models.length === 0 && <option value="">No models configured</option>}
                {choices.models.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.name}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Language">
              <Segmented
                ariaLabel="Report language"
                value={language}
                onChange={setLanguage}
                options={LANGUAGES.map((entry) => ({ value: entry.value, label: entry.label }))}
              />
            </Field>
          </div>

          {error && <ErrorNote>{error}</ErrorNote>}
        </div>
      )}
    </Modal>
  )
}

/** Whether a report can be written from this connection at all — §7. */
function eligible(connection: Connection): boolean {
  return WIDE_ENOUGH.includes(connection.disclosure_policy)
}

/**
 * A list, not a `<select>`.
 *
 * The rule this control exists to express — *a connection that shares no result
 * values cannot carry a report* — needs a reason next to the choice it rules
 * out. A native option can be `disabled`, but it cannot say why, and "greyed
 * out for reasons unknown" is how a user concludes the product is broken.
 */
function ConnectionPicker({
  connections, value, onChange,
}: {
  connections: Connection[]
  value: string | null
  onChange: (id: string) => void
}) {
  if (connections.length === 0) {
    return (
      <span style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
        No databases yet. Add one under Data sources first.
      </span>
    )
  }

  return (
    <div role="radiogroup" aria-label="Database" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {connections.map((connection) => {
        const usable = eligible(connection)
        const selected = connection.id === value
        return (
          <button
            key={connection.id}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={!usable}
            onClick={() => onChange(connection.id)}
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
              width: '100%',
              padding: '9px 11px',
              textAlign: 'left',
              fontFamily: 'inherit',
              background: selected ? 'var(--accent-bg)' : 'var(--input-bg)',
              border: `1px solid ${selected ? 'var(--accent)' : 'var(--border)'}`,
              borderRadius: 9,
              cursor: usable ? 'pointer' : 'not-allowed',
              opacity: usable ? 1 : 0.55,
            }}
          >
            <span style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%' }}>
              <span style={{ display: 'flex', color: 'var(--text-dim)' }}>
                <Icon.Database size={13} />
              </span>
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
                {connection.name}
              </span>
              <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
                {connection.database_type}
              </span>
              <span style={{ marginLeft: 'auto' }}>
                <DisclosureBadge policy={connection.disclosure_policy} />
              </span>
            </span>
            {/* The reason, verbatim in spirit with the backend's own message:
                the policy in force, and both ways out of it. */}
            {!usable && (
              <span style={{ fontSize: 11.5, lineHeight: 1.45, color: 'var(--text-dim)' }}>
                Its policy is {connection.disclosure_policy}, so no result values reach the
                model. A report’s analysis is written from those values — change the policy
                under Data sources, or pick another database.
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

function RenameDialog({
  report, onClose, onSaved,
}: {
  report: ReportSummary
  onClose: () => void
  onSaved: (updated: ReportSummary) => void
}) {
  const [name, setName] = useState(report.name)
  const [description, setDescription] = useState(report.description ?? '')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  async function submit() {
    if (!name.trim()) return setError('A report needs a name.')
    setSaving(true)
    try {
      const updated = await api.update(report.id, {
        name: name.trim(),
        description: description.trim() || null,
      })
      onSaved({ ...report, name: updated.name, description: updated.description })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'That did not work.')
      setSaving(false)
    }
  }

  return (
    <Modal
      title="Rename report"
      onClose={onClose}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton onClick={() => void submit()} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </PrimaryButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <TextInput
          autoFocus
          value={name}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void submit()
          }}
        />
        <TextInput
          value={description}
          placeholder="Optional — what this report is for"
          onChange={(event) => setDescription(event.target.value)}
        />
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Modal>
  )
}
