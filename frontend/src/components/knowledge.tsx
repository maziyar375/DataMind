/**
 * The Knowledge tab: the questions this connection has been taught.
 *
 * **Operate, not persuade.** Nobody arrives here to be sold anything — they
 * got a wrong answer, or they were told the system can be taught and want to
 * see whether that is true. So: scanability, consistency and native
 * expectations over expression, and no new colour, component or font. Every
 * token here already exists in `theme/tokens.ts`.
 *
 * Three decisions carry the screen:
 *
 *  1. **One work list, three sections, one detail pane** — not sub-tabs. Read
 *     top to bottom it says *what is broken, what has been taught, what was
 *     archived*, so "what should I do next" is never a navigation problem.
 *  2. **The editor offers parameters; the curator does not type them.** The
 *     backend walks the guard's AST and returns every literal it found —
 *     ticked, unticked, or refused with the reason beside it. The literal each
 *     row would replace is highlighted in the SQL, so the curator sees *what
 *     would change* rather than an abstract list. Nothing is sent to a model,
 *     and the panel says so once, quietly.
 *  3. **Validation is never guessed at locally.** The `✓ Valid` chip comes
 *     from the same parser that will reject the statement on save — exactly
 *     as `semantic.tsx` does with `POST .../semantic/check`. A local "looks
 *     fine" the server then rejects is the worst possible interaction.
 *
 * When the reader may not curate, buttons are **absent, not disabled** — a
 * disabled control nobody can enable is an insult — and the list stays fully
 * readable, because seeing what the system knows is not a privilege.
 *
 * The DOM-free half of this file is `knowledge-template.ts`, which is where
 * the sectioning, the status words, the highlight offsets and the readiness
 * rule are unit-tested (`npm run test:template`).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { knowledge as api } from '../api/client'
import type {
  Connection, KnowledgeTemplate, ParamProposal, Review, Suggestion,
  TemplateCheckResult, TemplateParam,
} from '../api/types'
import {
  Chip, DangerButton, EmptyState, ErrorNote, Field, GhostButton, Icon, Modal,
  PrimaryButton, SearchField, Spinner, TextArea, TextInput, dirOf, relativeTime,
} from './ui'
import type { ChipTone } from './ui'
import { DetailBody } from './settings'
import {
  CORRECTION_SHAPES, markLiterals, matches, previewQuestion, questionParts,
  readiness, resolveReadiness, roleLabel, rowSubtitle, sections, statusOf,
  suggestionView,
} from './knowledge-template'
import type { CorrectionShape, TemplateRow } from './knowledge-template'

/** How long the editor waits after a keystroke before asking the server.
 *  Long enough not to check every character, short enough that the verdict
 *  lands while the curator is still looking at the SQL box. */
const CHECK_DEBOUNCE_MS = 400

const TONE: Record<string, ChipTone> = {
  green: 'green', amber: 'amber', red: 'red', accent: 'accent', neutral: 'neutral',
  faint: 'neutral',
}

/** SQL is **always** `dir="ltr"`, in both themes and both directions.
 *  A bidi-reordered statement is unreadable and, worse, ambiguous. */
const CODE: React.CSSProperties = {
  fontFamily: 'var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)',
  fontSize: 12,
  lineHeight: 1.6,
  background: 'var(--code-bg)',
  color: 'var(--code-text)',
  direction: 'ltr',
  textAlign: 'left',
  whiteSpace: 'pre',
  overflowX: 'auto',
}

export function KnowledgeTab({ connection }: { connection: Connection }) {
  const [rows, setRows] = useState<KnowledgeTemplate[]>([])
  const [staleIds, setStaleIds] = useState<string[]>([])
  const [canCurate, setCanCurate] = useState(true)
  const [synced, setSynced] = useState(true)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [editing, setEditing] = useState<KnowledgeTemplate | 'new' | null>(null)
  const [showArchive, setShowArchive] = useState(false)
  const [reviews, setReviews] = useState<Review[]>([])
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  // A question and a statement to open the editor with, from a flag or a
  // backlog row. Held beside `editing` rather than folded into it, because the
  // editor is also opened with nothing at all.
  const [prefill, setPrefill] = useState<
    { question: string; sql: string; source?: string } | null
  >(null)
  // The flag this editor session is answering, so saving the template resolves
  // it — which is how the person who raised it finds out.
  const [resolving, setResolving] = useState<string | null>(null)
  const [openReview, setOpenReview] = useState<Review | null>(null)

  const refresh = useCallback(async () => {
    const body = await api.list(connection.id, true)
    setRows(body.templates)
    setStaleIds(body.stale_ids)
    setCanCurate(body.can_curate)
    setSynced(body.schema_synced)
    // The queue and the backlog load beside the store, not after it: they are
    // three readings of one screen, and loading them in sequence would make
    // the sections appear one at a time under the reader's cursor.
    const [queue, backlog] = await Promise.all([
      api.reviews(connection.id).catch(() => [] as Review[]),
      api.suggestions(connection.id).catch(() => [] as Suggestion[]),
    ])
    setReviews(queue)
    setSuggestions(backlog)
    return body
  }, [connection.id])

  useEffect(() => {
    setLoading(true)
    refresh()
      .catch(() => setError('Could not load what this connection has been taught.'))
      .finally(() => setLoading(false))
  }, [refresh])

  const visible = useMemo(
    () => rows.filter((r) => matches(asRow(r), search)),
    [rows, search],
  )
  const split = useMemo(() => sections(visible.map(asRow), staleIds), [visible, staleIds])
  const selected = rows.find((r) => r.id === selectedId) ?? null

  async function archive(row: KnowledgeTemplate) {
    try {
      await api.archive(connection.id, row.id)
      await refresh()
    } catch (err) {
      setError(messageOf(err))
    }
  }

  if (loading) {
    return (
      <DetailBody>
        <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
          <Spinner size={20} />
        </div>
      </DetailBody>
    )
  }

  return (
    <DetailBody>
      {error && <ErrorNote>{error}</ErrorNote>}

      {!synced && (
        <div style={hint()}>
          Sync this connection&rsquo;s schema first — a template is checked against it.
        </div>
      )}

      {!canCurate && (
        <div style={hint()}>
          Only administrators can add templates on this connection.
        </div>
      )}

      <div
        style={{
          position: 'sticky', top: 0, zIndex: 2, display: 'flex', gap: 10,
          alignItems: 'center', padding: '2px 0 10px',
          background: 'var(--bg)',
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <SearchField
            value={search}
            onChange={setSearch}
            placeholder="Search taught questions"
            ariaLabel="Search taught questions"
          />
        </div>
        {canCurate && synced && (
          <PrimaryButton onClick={() => setEditing('new')}>
            <Icon.Plus size={14} />
            Teach a question
          </PrimaryButton>
        )}
      </div>

      {rows.length === 0 && !search && <FirstRun canCurate={canCurate && synced} />}

      {rows.length > 0 && visible.length === 0 && (
        <EmptyState
          title={`No templates match “${search}”.`}
          body="A search that finds nothing is itself a curation signal — this may be a question worth teaching."
          action={
            canCurate && synced ? (
              <PrimaryButton onClick={() => setEditing('new')}>
                Teach this question
              </PrimaryButton>
            ) : undefined
          }
        />
      )}

      {reviews.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <SectionHeading>Needs you · {reviews.length}</SectionHeading>
          {reviews.map((review) => (
            <ReviewRow
              key={review.id}
              review={review}
              selected={openReview?.id === review.id}
              onSelect={() =>
                setOpenReview(openReview?.id === review.id ? null : review)
              }
            />
          ))}
        </div>
      )}

      {openReview && (
        <ReviewPane
          review={openReview}
          canCurate={canCurate}
          onTeach={() => {
            setPrefill({
              question: openReview.question,
              sql: openReview.sql,
              source: 'CHAT_CONFIRMED',
            })
            setResolving(openReview.id)
            setEditing('new')
            setOpenReview(null)
          }}
          onDismiss={async (note) => {
            try {
              await api.resolve(connection.id, openReview.id, {
                dismiss: true,
                note,
              })
              setOpenReview(null)
              await refresh()
            } catch (err) {
              setError(messageOf(err))
            }
          }}
          onClose={() => setOpenReview(null)}
        />
      )}

      {suggestions.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <SectionHeading>Suggested · {suggestions.length}</SectionHeading>
          {suggestions
            .filter((s) => s.kind !== 'FLAGGED')
            .map((item, i) => (
              <SuggestionRowView
                key={`${item.kind}-${item.origin_id || i}`}
                item={item}
                canCurate={canCurate && synced}
                onTeach={() => {
                  setPrefill({
                    question: item.question,
                    sql: item.sql,
                    source: item.source || 'MANUAL',
                  })
                  setEditing('new')
                }}
              />
            ))}
        </div>
      )}

      <SectionList
        title="Needs you"
        rows={split.needsYou}
        staleIds={staleIds}
        selectedId={selectedId}
        onSelect={setSelectedId}
      />
      <SectionList
        title="Templates"
        rows={split.taught}
        staleIds={staleIds}
        selectedId={selectedId}
        onSelect={setSelectedId}
      />
      {split.archived.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setShowArchive((v) => !v)}
            style={{
              alignSelf: 'flex-start', background: 'none', border: 'none',
              padding: 0, cursor: 'pointer', fontSize: 12, color: 'var(--text-faint)',
            }}
          >
            {showArchive ? 'Hide' : 'Show'} archive · {split.archived.length}
          </button>
          {showArchive && (
            <SectionList
              title="Archived"
              rows={split.archived}
              staleIds={staleIds}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
          )}
        </>
      )}

      {selected && (
        <Detail
          template={selected}
          drifted={staleIds.includes(selected.id)}
          canCurate={canCurate}
          onEdit={() => setEditing(selected)}
          onArchive={() => archive(selected)}
          onClose={() => setSelectedId(null)}
        />
      )}

      {editing && (
        <TemplateEditor
          connection={connection}
          template={typeof editing === 'string' ? null : editing}
          prefill={prefill ?? undefined}
          onClose={() => {
            setEditing(null)
            setPrefill(null)
          }}
          onSaved={async (saved) => {
            setEditing(null)
            setPrefill(null)
            // A template saved from a flag resolves that flag, which is how
            // the person who raised it finds out their flag became knowledge.
            if (resolving) {
              await api
                .resolve(connection.id, resolving, { template_id: saved.id })
                .catch(() => undefined)
              setResolving(null)
            }
            await refresh().catch(() => undefined)
          }}
        />
      )}
    </DetailBody>
  )
}

/**
 * The first-run state: not an illustration, three things to do right now.
 *
 * (Phase 3 fills the second and third bullets from traffic and from corrected
 * dashboard tiles. Until then the honest version is one sentence, which is
 * better than an empty list dressed up as a feature.)
 */
function FirstRun({ canCurate }: { canCurate: boolean }) {
  return (
    <EmptyState
      icon={<Icon.Sparkle size={20} />}
      title="Teach this connection"
      body={
        canCurate
          ? 'Write a question the way someone would ask it, paste the SQL that answers it, and this connection will answer it the same way next time.'
          : 'Nothing has been taught here yet.'
      }
    />
  )
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 11, fontWeight: 700, letterSpacing: 0.6,
        textTransform: 'uppercase', color: 'var(--text-faint)',
      }}
    >
      {children}
    </div>
  )
}

/** One flag, in the same anatomy every other row uses. */
function ReviewRow({
  review, selected, onSelect,
}: {
  review: Review
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected}
      style={{
        display: 'flex', gap: 10, alignItems: 'flex-start', width: '100%',
        textAlign: 'start', padding: '10px 12px', borderRadius: 10,
        cursor: 'pointer', background: selected ? 'var(--panel-alt)' : 'var(--panel)',
        border: `1px solid ${selected ? 'var(--border-strong)' : 'var(--border)'}`,
      }}
    >
      <span aria-hidden style={{ color: 'var(--amber)', fontSize: 13, lineHeight: '20px' }}>
        ⚠
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span
          dir={dirOf(review.question)}
          style={{ display: 'block', fontSize: 13, color: 'var(--text-strong)' }}
        >
          {review.question || 'A flagged answer'}
        </span>
        <span
          style={{
            display: 'flex', gap: 12, marginTop: 3, fontSize: 11,
            color: 'var(--text-faint)', justifyContent: 'space-between',
          }}
        >
          <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis',
                         whiteSpace: 'nowrap' }}>
            {review.comment || 'No comment left'}
          </span>
          <span style={{ color: 'var(--amber)', flexShrink: 0 }}>
            {review.verdict === 'NEEDS_REVIEW' ? 'Review asked' : 'Flagged'}
            {review.flagged_by ? ` by ${review.flagged_by}` : ''}
          </span>
        </span>
      </span>
      <span className="rm-sr-only">Needs you</span>
    </button>
  )
}

/**
 * A flag, opened.
 *
 * The radio group is §1.5's rule made into an interaction: the curator decides
 * whether a correction is *question-shaped* (it becomes a template) or
 * *definition-shaped* (it belongs in the semantic layer), and the product does
 * not guess. A router that guessed would be wrong often enough to teach people
 * to distrust the whole queue.
 */
function ReviewPane({
  review, canCurate, onTeach, onDismiss, onClose,
}: {
  review: Review
  canCurate: boolean
  onTeach: () => void
  onDismiss: (note: string) => void
  onClose: () => void
}) {
  const [shape, setShape] = useState<CorrectionShape>('template')
  const [note, setNote] = useState('')
  const ready = resolveReadiness(shape, note, false)

  return (
    <div
      style={{
        border: '1px solid var(--amber-border, var(--border-strong))',
        borderRadius: 12, background: 'var(--panel)', overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex', gap: 10, alignItems: 'flex-start', padding: '12px 16px',
          borderBottom: '1px solid var(--border)', background: 'var(--amber-bg)',
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12, color: 'var(--amber)' }}>
            Flagged {relativeTime(review.created_at)}
            {review.flagged_by ? ` by ${review.flagged_by}` : ''}
            {review.comment ? ` · “${review.comment}”` : ''}
          </div>
          <div
            dir={dirOf(review.question)}
            style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-strong)',
                     marginTop: 3 }}
          >
            {review.question}
          </div>
        </div>
        <button type="button" aria-label="Close" className="rm-icon-btn" onClick={onClose}>
          <Icon.Close size={13} />
        </button>
      </div>

      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div>
          <Label>What it answered</Label>
          <pre style={{ ...CODE, margin: 0, padding: 12, borderRadius: 8 }}>
            {review.sql || '(no statement recorded)'}
          </pre>
        </div>

        {canCurate ? (
          <>
            <div>
              <Label>This correction is</Label>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {CORRECTION_SHAPES.map((option) => (
                  <label
                    key={option.value}
                    style={{ display: 'flex', gap: 8, alignItems: 'baseline',
                             fontSize: 12.5 }}
                  >
                    <input
                      type="radio"
                      name={`shape-${review.id}`}
                      checked={shape === option.value}
                      onChange={() => setShape(option.value)}
                    />
                    <span style={{ color: 'var(--text)' }}>{option.label}</span>
                    <span style={{ color: 'var(--text-faint)' }}>→ {option.detail}</span>
                  </label>
                ))}
              </div>
            </div>

            {shape === 'dismiss' && (
              <TextArea
                value={note}
                dir={dirOf(note)}
                placeholder="Why? The person who flagged this will see the reason."
                onChange={(e) => setNote(e.target.value)}
              />
            )}
            {shape === 'definition' && (
              <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                Definitions live in the Semantic layer tab — a grain statement, a
                metric, or a glossary term. Fix it there, then dismiss this with a
                note saying so.
              </div>
            )}

            <div style={{ display: 'flex', gap: 8 }}>
              {shape === 'template' && (
                <PrimaryButton onClick={onTeach}>
                  <Icon.Sparkle size={13} />
                  Correct the SQL and save
                </PrimaryButton>
              )}
              {shape !== 'template' && (
                <PrimaryButton
                  onClick={() => onDismiss(note)}
                  disabled={!ready.ready}
                  title={ready.issue || undefined}
                >
                  Dismiss and resolve
                </PrimaryButton>
              )}
            </div>
            {ready.issue && (
              <div style={{ fontSize: 12, color: 'var(--amber)' }}>{ready.issue}</div>
            )}
          </>
        ) : (
          <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>
            Only administrators can resolve flags on this connection.
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * One backlog row.
 *
 * A suggestion is `○` — not yet knowledge — and never `⚠`, because nothing
 * here is broken. A backlog that looked like a fault list would train people
 * to dread opening the tab.
 */
function SuggestionRowView({
  item, canCurate, onTeach,
}: {
  item: Suggestion
  canCurate: boolean
  onTeach: () => void
}) {
  const view = suggestionView({
    kind: item.kind, question: item.question, count: item.count,
    reason: item.reason, sql: item.sql, words: item.words,
  })
  const actionable = Boolean(view.action) && canCurate

  return (
    <div
      style={{
        display: 'flex', gap: 10, alignItems: 'flex-start',
        padding: '10px 12px', borderRadius: 10, background: 'var(--panel)',
        border: '1px solid var(--border)',
      }}
    >
      <span aria-hidden style={{ color: 'var(--text-faint)', fontSize: 13,
                                 lineHeight: '20px' }}>
        {view.glyph}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          dir={dirOf(item.question)}
          style={{ fontSize: 13, color: 'var(--text-strong)' }}
        >
          “{item.question}”
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 3 }}>
          {item.reason}
        </div>
      </div>
      {actionable && (
        <GhostButton onClick={onTeach}>{view.action} →</GhostButton>
      )}
    </div>
  )
}

function SectionList({
  title, rows, staleIds, selectedId, onSelect,
}: {
  title: string
  rows: TemplateRow[]
  staleIds: string[]
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  if (rows.length === 0) return null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div
        style={{
          fontSize: 11, fontWeight: 700, letterSpacing: 0.6,
          textTransform: 'uppercase', color: 'var(--text-faint)',
        }}
      >
        {title} · {rows.length}
      </div>
      {rows.map((row) => (
        <Row
          key={row.id}
          row={row}
          drifted={staleIds.includes(row.id)}
          selected={row.id === selectedId}
          onSelect={() => onSelect(row.id)}
        />
      ))}
    </div>
  )
}

/**
 * One row, the same anatomy in every section — which is what makes three
 * sections feel like one list rather than three widgets.
 *
 * The leading glyph is status, not decoration, and it is never the only
 * carrier: the status word travels with it.
 */
function Row({
  row, drifted, selected, onSelect,
}: {
  row: TemplateRow
  drifted: boolean
  selected: boolean
  onSelect: () => void
}) {
  const status = statusOf(row, drifted)
  const subtitle = rowSubtitle(row, drifted)
  const role = roleLabel(row.role)

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected}
      style={{
        display: 'flex', gap: 10, alignItems: 'flex-start', width: '100%',
        textAlign: 'start', padding: '10px 12px', borderRadius: 10,
        cursor: 'pointer', background: selected ? 'var(--panel-alt)' : 'var(--panel)',
        border: `1px solid ${selected ? 'var(--border-strong)' : 'var(--border)'}`,
      }}
    >
      <span
        aria-hidden
        style={{ color: `var(--${status.tone === 'faint' ? 'text-faint' : status.tone})`,
                 fontSize: 13, lineHeight: '20px' }}
      >
        {status.glyph}
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span
          dir={dirOf(row.question)}
          style={{
            // Two lines, then clamp. A long question belongs in the detail
            // pane, not stretched down a scanning list.
            display: '-webkit-box', fontSize: 13, color: 'var(--text-strong)',
            overflow: 'hidden', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
          } as React.CSSProperties}
        >
          <Question text={row.question} />
        </span>
        <span
          style={{
            display: 'flex', gap: 12, marginTop: 3, fontSize: 11,
            color: 'var(--text-faint)', justifyContent: 'space-between',
          }}
        >
          <span dir="ltr" style={{ minWidth: 0, overflow: 'hidden',
                                   textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {subtitle.left}
          </span>
          <span style={{ color: subtitle.tone === 'amber' ? 'var(--amber)' : undefined,
                         flexShrink: 0 }}>
            {subtitle.right}
          </span>
        </span>
      </span>
      {role && <Chip tone="neutral" small>{role}</Chip>}
      <span className="rm-sr-only">{status.label}</span>
    </button>
  )
}

/** The question, with its `{slots}` dimmed. Spans, never markup. */
function Question({ text }: { text: string }) {
  return (
    <>
      {questionParts(text).map((part, i) => (
        <span key={i} style={part.slot ? { color: 'var(--text-dim)' } : undefined}>
          {part.text}
        </span>
      ))}
    </>
  )
}

function Detail({
  template, drifted, canCurate, onEdit, onArchive, onClose,
}: {
  template: KnowledgeTemplate
  drifted: boolean
  canCurate: boolean
  onEdit: () => void
  onArchive: () => void
  onClose: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const status = statusOf(asRow(template), drifted)

  return (
    <div
      style={{
        border: '1px solid var(--border-strong)', borderRadius: 12,
        background: 'var(--panel)', overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex', gap: 10, alignItems: 'flex-start',
          padding: '12px 16px', borderBottom: '1px solid var(--border)',
          background: 'var(--panel-alt)',
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div dir={dirOf(template.question)}
               style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-strong)' }}>
            <Question text={template.question} />
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 3 }}>
            {template.verified_at
              ? `Verified ${relativeTime(template.verified_at)}`
              : 'Not yet verified'}
            {' · '}
            {template.hit_count} {template.hit_count === 1 ? 'hit' : 'hits'}
          </div>
        </div>
        <Chip tone={TONE[status.tone]}>{status.label}</Chip>
        <button type="button" aria-label="Close" className="rm-icon-btn"
                onClick={onClose}>
          <Icon.Close size={13} />
        </button>
      </div>

      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
        {status.needsYou && (
          <div style={{ ...hint(), borderColor: 'var(--amber-border, var(--border))',
                        background: 'var(--amber-bg)', color: 'var(--amber)' }}>
            {template.status_reason ||
              'This template stopped working when the schema changed.'}
          </div>
        )}

        <div>
          <Label>SQL</Label>
          <pre style={{ ...CODE, margin: 0, padding: 12, borderRadius: 8 }}>
            {template.sql}
          </pre>
        </div>

        {template.params.length > 0 && (
          <div>
            <Label>Parameters</Label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {template.params.map((p) => (
                <div key={p.name} style={{ fontSize: 12, display: 'flex', gap: 8 }}>
                  <code style={{ ...CODE, background: 'none', padding: 0 }}>
                    :{p.name}
                  </code>
                  <span style={{ color: 'var(--text-faint)' }}>{p.type}</span>
                  <span style={{ color: 'var(--text-dim)' }}>{p.comment}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {template.note && (
          <div>
            <Label>Note for the next person</Label>
            <div dir={dirOf(template.note)}
                 style={{ fontSize: 12, color: 'var(--text-dim)', whiteSpace: 'pre-wrap' }}>
              {template.note}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 4 }}>
              Written for people, not the model. This never goes into a prompt.
            </div>
          </div>
        )}

        {canCurate && template.status !== 'ARCHIVED' && (
          <div style={{ display: 'flex', gap: 8 }}>
            <GhostButton onClick={onEdit}>
              <Icon.Pencil size={13} />
              Edit the SQL
            </GhostButton>
            {confirming ? (
              <>
                <DangerButton onClick={onArchive}>Archive it</DangerButton>
                <GhostButton onClick={() => setConfirming(false)}>Cancel</GhostButton>
              </>
            ) : (
              <GhostButton onClick={() => setConfirming(true)}>Archive</GhostButton>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * The editor — the screen that matters most.
 *
 * The whole design goal in one sentence: **the curator pastes SQL and gets
 * offered a family.** Everything else follows from that — the live preview of
 * what the question would match, the literals marked in the statement itself,
 * and the refused proposal shown unticked with its reason rather than hidden.
 */
export function TemplateEditor({
  connection, template, prefill, onClose, onSaved,
}: {
  connection: Connection
  template: KnowledgeTemplate | null
  /**
   * A question and a statement to start from, when the editor is opened from
   * somewhere other than the Knowledge tab — a chat answer that worked, or a
   * backlog row. `source` decides whether the literals are the model's or a
   * person's, which is a disclosure question and not a cosmetic one.
   */
  prefill?: { question: string; sql: string; source?: string }
  onClose: () => void
  onSaved: (template: KnowledgeTemplate) => void
}) {
  const [question, setQuestion] = useState(
    template?.question ?? prefill?.question ?? '',
  )
  const [sql, setSql] = useState(template?.sql ?? prefill?.sql ?? '')
  const [note, setNote] = useState(template?.note ?? '')
  const [benchmark, setBenchmark] = useState(template?.role === 'BENCHMARK_ONLY')
  const [accepted, setAccepted] = useState<string[]>(
    () => (template?.params ?? []).map((p) => p.name),
  )
  const [check, setCheck] = useState<TemplateCheckResult | null>(null)
  const [checking, setChecking] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hovered, setHovered] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // The verdict comes from the backend on every pause in typing, never from a
  // local guess. `null` means "not answered yet", which is a state the Save
  // button waits in rather than guesses through.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current)
    if (!sql.trim()) {
      setCheck(null)
      return
    }
    timer.current = setTimeout(() => {
      setChecking(true)
      api
        .check(connection.id, { sql, question, accept: accepted })
        .then(setCheck)
        .catch(() => setCheck(null))
        .finally(() => setChecking(false))
    }, CHECK_DEBOUNCE_MS)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [connection.id, sql, question, accepted])

  const params: TemplateParam[] = check?.params ?? []
  const ready = readiness(question, sql, params, check?.valid ?? null)
  const proposals = check?.proposals ?? []
  const marked = useMemo(
    () => markLiterals(sql, proposals.filter((p) => p.eligible)),
    [sql, proposals],
  )

  function toggle(name: string) {
    setAccepted((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    )
  }

  // The proposals arrive with the backend's own defaults ticked. Adopted once,
  // on the first verdict for a new template, so a curator's un-tick is never
  // overwritten by the next keystroke's response.
  const adopted = useRef(template !== null)
  useEffect(() => {
    if (adopted.current || !check) return
    adopted.current = true
    setAccepted(check.proposals.filter((p) => p.suggested).map((p) => p.name))
  }, [check])

  async function save() {
    setSaving(true)
    setError(null)
    try {
      const payload = {
        question,
        sql: check?.sql || sql,
        params,
        note,
        role: benchmark
          ? ('BENCHMARK_ONLY' as const)
          : ('RETRIEVABLE' as const),
        // If the curator edited the statement they were shown, the literals
        // are now theirs and travel with structure; if they only confirmed it,
        // they are still the model's and are gated like sample values.
        // `docs/security.md`.
        source: prefill
          ? sql.trim() === prefill.sql.trim()
            ? prefill.source ?? 'CHAT_CONFIRMED'
            : 'CHAT_CORRECTED'
          : 'MANUAL',
      }
      const saved = template
        ? await api.update(connection.id, template.id, payload)
        : await api.create(connection.id, payload)
      onSaved(saved)
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title={template ? 'Edit template' : 'Teach a question'}
      subtitle={
        template
          ? undefined
          : prefill
            ? 'From an answer you just saw work — check the question, then save it.'
            : 'Write it the way someone would ask it, then paste the SQL that answers it.'
      }
      width={720}
      onClose={onClose}
      footer={
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton onClick={save} disabled={!ready.ready || saving}>
            {saving && <Spinner />}
            Save template
          </PrimaryButton>
        </div>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {error && <ErrorNote>{error}</ErrorNote>}

        <Field label="Question" hint="Write it the way someone would ask it.">
          <TextInput
            value={question}
            dir={dirOf(question)}
            placeholder="revenue by month for {region} in {year}"
            onChange={(e) => setQuestion(e.target.value)}
          />
        </Field>
        {question.includes('{') && params.length > 0 && (
          <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: -10 }}>
            Matches questions like &ldquo;{previewQuestion(question, params)}&rdquo;
          </div>
        )}

        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <Label>SQL</Label>
            <span style={{ flex: 1 }} />
            <span aria-live="polite" style={{ fontSize: 11 }}>
              {checking && <Spinner />}
              {!checking && check?.valid && (
                <Chip tone="green">
                  Valid · {check.referenced_tables.join(', ') || 'no tables'}
                </Chip>
              )}
              {!checking && check && !check.valid && <Chip tone="red">Rejected</Chip>}
            </span>
          </div>
          <TextArea
            dir="ltr"
            value={sql}
            placeholder="SELECT …"
            onChange={(e) => setSql(e.target.value)}
            style={{ ...CODE, minHeight: 150, whiteSpace: 'pre-wrap' }}
          />
          {marked.some((s) => s.slot) && (
            <pre
              aria-hidden
              style={{ ...CODE, margin: '6px 0 0', padding: 10, borderRadius: 8,
                       whiteSpace: 'pre-wrap' }}
            >
              {marked.map((span, i) => (
                <span
                  key={i}
                  style={
                    span.slot
                      ? {
                          background:
                            hovered === span.slot ? 'var(--accent)' : 'var(--accent-bg)',
                          color: hovered === span.slot ? 'var(--bg)' : 'var(--accent)',
                          borderRadius: 3,
                        }
                      : undefined
                  }
                >
                  {span.text}
                </span>
              ))}
            </pre>
          )}
          {check && !check.valid && check.issue && <ErrorNote>{check.issue}</ErrorNote>}
        </div>

        {proposals.length > 0 && (
          <div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <Label>Parameters</Label>
              <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
                found by reading your SQL — nothing was sent
              </span>
            </div>
            <div
              style={{
                border: '1px solid var(--border)', borderRadius: 8,
                display: 'flex', flexDirection: 'column',
              }}
            >
              {proposals.map((proposal) => (
                <Proposal
                  key={`${proposal.name}-${proposal.occurrence}`}
                  proposal={proposal}
                  checked={accepted.includes(proposal.name)}
                  onToggle={() => toggle(proposal.name)}
                  onHover={setHovered}
                />
              ))}
            </div>
          </div>
        )}

        <Field
          label="Note for the next person"
          hint="Written for people, not the model. This never goes into a prompt."
        >
          <TextArea
            value={note}
            dir={dirOf(note)}
            placeholder="Cancelled orders are never revenue…"
            onChange={(e) => setNote(e.target.value)}
          />
        </Field>

        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12,
                        color: 'var(--text-dim)' }}>
          <input
            type="checkbox"
            checked={benchmark}
            onChange={(e) => setBenchmark(e.target.checked)}
          />
          Use this to measure accuracy, not to answer questions
        </label>

        {ready.issue && (
          <div style={{ fontSize: 12, color: 'var(--amber)' }}>{ready.issue}</div>
        )}
      </div>
    </Modal>
  )
}

/**
 * One parameter proposal: a real checkbox in a real list.
 *
 * A refusal is rendered rather than hidden, unticked and with its reason next
 * to it — showing the rejected candidate teaches the rule better than hiding
 * it, and the curator occasionally knows better.
 */
function Proposal({
  proposal, checked, onToggle, onHover,
}: {
  proposal: ParamProposal
  checked: boolean
  onToggle: () => void
  onHover: (name: string | null) => void
}) {
  return (
    <label
      onMouseEnter={() => onHover(proposal.name)}
      onMouseLeave={() => onHover(null)}
      style={{
        display: 'flex', gap: 10, alignItems: 'baseline', padding: '8px 10px',
        borderTop: '1px solid var(--border)', fontSize: 12,
        opacity: proposal.eligible ? 1 : 0.75,
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={!proposal.eligible}
        onChange={onToggle}
      />
      <code style={{ ...CODE, background: 'var(--accent-bg)', color: 'var(--accent)',
                     padding: '1px 5px', borderRadius: 3 }}>
        {proposal.literal}
      </code>
      <span aria-hidden style={{ color: 'var(--text-faint)' }}>→</span>
      <code style={{ ...CODE, background: 'none', padding: 0 }}>:{proposal.name}</code>
      <span style={{ color: 'var(--text-faint)' }}>{proposal.type}</span>
      <span style={{ flex: 1, color: 'var(--text-dim)', minWidth: 0 }}>
        {proposal.comment || proposal.reason}
      </span>
    </label>
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 11, fontWeight: 700, letterSpacing: 0.5,
        textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 4,
      }}
    >
      {children}
    </div>
  )
}

function hint(): React.CSSProperties {
  return {
    fontSize: 12, color: 'var(--text-dim)', padding: '8px 12px',
    border: '1px solid var(--border)', borderRadius: 8,
    background: 'var(--panel-alt)',
  }
}

/** The subset `knowledge-template.ts` reads, from the API shape. */
function asRow(t: KnowledgeTemplate): TemplateRow {
  return {
    id: t.id,
    question: t.question,
    params: t.params,
    referenced_tables: t.referenced_tables,
    status: t.status,
    status_reason: t.status_reason,
    role: t.role,
    hit_count: t.hit_count,
    last_hit_at: t.last_hit_at,
    verified_at: t.verified_at,
  }
}

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : 'Something went wrong.'
}
