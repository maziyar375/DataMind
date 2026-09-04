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
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { knowledge as api } from '../api/client'
import type {
  BenchmarkOverview, Connection, EmbeddingStatus, KnowledgeHealth,
  KnowledgeTemplate, MaintenanceResult, ParamProposal, Review, Suggestion,
  TemplateCheckResult, TemplateParam,
} from '../api/types'
import {
  Chip, DangerButton, Dot, EmptyState, ErrorNote, Field, GhostButton, Icon,
  Modal, PrimaryButton, SearchField, Segmented, Spinner, TextArea, TextInput,
  dirOf, relativeTime,
} from './ui'
import { DetailBody } from './settings'
import { useBackgroundWatch, useNotify, useQueue } from '../shell'
import {
  CORRECTION_SHAPES, conflictEvidence, differingCells, embeddingView,
  indexSummary, markLiterals, matches, matchesReview, matchesSuggestion,
  percent, previewQuestion, questionParts, readiness, resolveReadiness,
  roleLabel, rowSubtitle, scoreView, sections, sparkHeights, statusOf,
  suggestionView,
} from './knowledge-template'
import type { CorrectionShape, TemplateRow } from './knowledge-template'

/**
 * Which of the four things a store holds is on screen.
 *
 * They were four stacked sections in one scroll, in build order rather than
 * reading order, and the consequence was not cosmetic: a store with one taught
 * question and thirty suggestions showed the question **last**, under thirty
 * rows of things nobody had decided to teach. What a connection knows is the
 * subject of this screen, so it is the view that opens and the one a backlog
 * cannot push off the bottom.
 */
export type KnowledgeView = 'taught' | 'suggested' | 'flagged' | 'archived'

/** Four words, each naming what is *in* the view rather than what to do there
 *  — the tab is a place, and the verbs live on the rows. "Archive" rather than
 *  "Archived" for the same reason: it is somewhere to look, not a state. */
const VIEW_LABEL: Record<KnowledgeView, string> = {
  taught: 'Taught',
  suggested: 'Suggested',
  flagged: 'Flagged',
  archived: 'Archive',
}

/** What the one search box is searching, said in the box. Four labels rather
 *  than one generic *Search* because the same control over four different
 *  lists is only reassuring if it says which one it has. */
const SEARCH_LABEL: Record<KnowledgeView, string> = {
  taught: 'Search taught questions',
  suggested: 'Search what people asked',
  flagged: 'Search flagged answers',
  archived: 'Search the archive',
}

/** How long the editor waits after a keystroke before asking the server.
 *  Long enough not to check every character, short enough that the verdict
 *  lands while the curator is still looking at the SQL box. */
const CHECK_DEBOUNCE_MS = 400

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
  const [health, setHealth] = useState<KnowledgeHealth | null>(null)
  const [score, setScore] = useState<BenchmarkOverview | null>(null)
  const [embeddings, setEmbeddings] = useState<EmbeddingStatus | null>(null)
  const [switching, setSwitching] = useState(false)
  const [sweeping, setSweeping] = useState(false)
  const [sweepNote, setSweepNote] = useState<string | null>(null)
  const [canCurate, setCanCurate] = useState(true)
  const [synced, setSynced] = useState(true)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [editing, setEditing] = useState<KnowledgeTemplate | 'new' | null>(null)
  // Which of the four things this store holds is on screen.
  //
  // **The fix for the screen's oldest problem.** All four used to be stacked
  // in one scroll, in the order they were built rather than the order anybody
  // reads them, so a store with one taught question and thirty suggestions
  // showed the question last — below thirty rows of things nobody had decided
  // to teach yet. What the store *knows* is now the default view and cannot be
  // buried by a backlog, however long the backlog gets.
  const [view, setView] = useState<KnowledgeView>('taught')
  const [restoring, setRestoring] = useState<string | null>(null)
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
  const notify = useNotify()
  const navigate = useNavigate()
  const watch = useBackgroundWatch()
  // This screen reads both feeds to draw its own two sections, so telling the
  // shell what it found keeps the rail's badge exact for free — no second
  // fan-out, and resolving a flag updates the count while the reader is still
  // looking at the row they resolved.
  const { noteFor } = useQueue()

  const refresh = useCallback(async () => {
    const body = await api.list(connection.id, true)
    setRows(body.templates)
    setStaleIds(body.stale_ids)
    setHealth(body.health)
    setCanCurate(body.can_curate)
    setSynced(body.schema_synced)
    // The queue and the backlog load beside the store, not after it: they are
    // three readings of one screen, and loading them in sequence would make
    // the sections appear one at a time under the reader's cursor.
    const [queue, backlog, benchmarks, index] = await Promise.all([
      api.reviews(connection.id).catch(() => [] as Review[]),
      api.suggestions(connection.id).catch(() => [] as Suggestion[]),
      api.benchmarks(connection.id).catch(() => null),
      api.embeddings(connection.id).catch(() => null),
    ])
    setReviews(queue)
    setSuggestions(backlog)
    setScore(benchmarks)
    setEmbeddings(index)
    noteFor(connection.id, {
      name: connection.name,
      reviews: queue.length,
      // The same filter the backlog section renders with — a FLAGGED
      // suggestion is the review beside it, and counting both would show a
      // badge of four over a list of two.
      suggestions: backlog.filter((s) => s.kind !== 'FLAGGED').length,
    })
    return body
  }, [connection.id, connection.name, noteFor])

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
  // Counted before the search, deliberately: a tab whose number moved as you
  // typed would make the store look like it was losing questions.
  const total = useMemo(() => sections(rows.map(asRow), staleIds), [rows, staleIds])
  const backlog = useMemo(
    () => suggestions.filter((s) => s.kind !== 'FLAGGED'),
    [suggestions],
  )
  // Every list on this screen is searched by the same box. It used to exist
  // only where templates did, so a connection with nothing taught and thirty
  // questions waiting showed no search at all, while the one beside it in the
  // same rail showed one — the same control appearing and disappearing by
  // which connection you clicked.
  const visibleBacklog = useMemo(
    () => backlog.filter((item) => matchesSuggestion(item, search)),
    [backlog, search],
  )
  const visibleReviews = useMemo(
    () => reviews.filter((review) => matchesReview(review, search)),
    [reviews, search],
  )
  const counts: Record<KnowledgeView, number> = {
    taught: total.needsYou.length + total.taught.length,
    suggested: backlog.length,
    flagged: reviews.length,
    archived: total.archived.length,
  }
  // Taught and Suggested are always offered — one is the store, the other is
  // where the next entry comes from, and a tab that vanishes when it empties
  // takes the reader's map with it. Flagged and Archive appear only when they
  // hold something, because neither is a place to go and find nothing.
  const tabs: KnowledgeView[] = [
    'taught',
    'suggested',
    ...(counts.flagged > 0 ? (['flagged'] as const) : []),
    ...(counts.archived > 0 ? (['archived'] as const) : []),
  ]
  // A view that stops existing under the reader — the last flag resolved, the
  // last archived row restored — hands them back the store rather than an
  // empty pane that no longer has a tab.
  useEffect(() => {
    if (!tabs.includes(view)) setView('taught')
  }, [tabs, view])
  // A query typed against one list carried to the next and filtered it to
  // nothing, which reads as an empty list rather than as a search still on.
  useEffect(() => { setSearch('') }, [view])
  /** Open a row, or close the one that is open. One handler for the row's own
   *  click and for the detail's Close, because they are the same gesture. */
  const toggle = (id: string) =>
    setSelectedId((current) => (current === id ? null : id))

  async function archive(row: KnowledgeTemplate) {
    try {
      await api.archive(connection.id, row.id)
      await refresh()
    } catch (err) {
      setError(messageOf(err))
    }
  }

  /** Put an archived template back in use.
   *
   *  Archiving is the only delete this store has — a question somebody wrote
   *  is months of work and the API refuses to destroy one — so the archive is
   *  a place things come back from, and the row that goes in says so by
   *  offering the way out on the way in. */
  async function restore(row: TemplateRow) {
    setRestoring(row.id)
    try {
      await api.restore(connection.id, row.id)
      await refresh()
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setRestoring(null)
    }
  }

  /** Sweep now: re-validate every live template, then run near-duplicate pairs
   *  against each other and compare the rows. The result is a sentence about
   *  what changed, not a job id — the list under it has already refreshed to
   *  whatever the sweep found. */
  async function sweep() {
    setSweeping(true)
    setSweepNote(null)
    try {
      const result = await api.revalidate(connection.id)
      await refresh()
      setSweepNote(sweepSummary(result) + indexSummary(result))
      // The inline note stays: it is the full report, next to the list that
      // has already changed under it. The notice is narrower on purpose —
      // only a *finding*, never a completion — because a sweep that changed
      // nothing is not news, and a corner of the screen repeating what is
      // already on the page is how a notification layer becomes wallpaper.
      // It survives the curator having walked away, which the note cannot.
      if (result.conflicted.length > 0) {
        notify({
          tone: 'warn',
          title: `${result.conflicted.length} ${
            result.conflicted.length === 1 ? 'template disagrees' : 'templates disagree'
          } with another on ${connection.name}`,
          body: 'Two near-duplicate templates returned different rows. Until one is fixed, either could be quoted.',
          to: `/sources/${connection.id}/knowledge`,
          toLabel: 'Open the store',
        })
      }
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setSweeping(false)
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

      {score && score.sets.length === 0 &&
        score.can_curate &&
        score.candidates >= score.min_set_size && (
          <StartMeasuring
            candidates={score.candidates}
            onCreate={async () => {
              try {
                const rows = await api.benchmarkCandidates(connection.id)
                await api.createBenchmark(connection.id, {
                  name: 'Accuracy',
                  description:
                    'Built from the questions taught up to this point.',
                  template_ids: rows.map((r) => r.id),
                })
                await refresh()
              } catch (err) {
                setError(messageOf(err))
              }
            }}
          />
        )}

      {score && score.sets.length > 0 && (
        <ScoreStrip
          overview={score}
          onRun={async (setId) => {
            try {
              const run = await api.runBenchmark(connection.id, setId)
              await refresh()
              // 202 and a row: this is minutes of model calls, one per
              // member of the set. The strip above shows RUNNING while this
              // tab is open; the shell carries the score to wherever the
              // curator actually is when it lands.
              watch({
                key: `benchmark:${run.id}`,
                poll: async () => {
                  const overview = await api.benchmarks(connection.id)
                  const finished = overview.sets
                    .flatMap((set) => set.runs)
                    .find((r) => r.id === run.id)
                  if (!finished || finished.status === 'QUEUED'
                      || finished.status === 'RUNNING') {
                    return null
                  }
                  if (finished.status === 'FAILED') {
                    return {
                      tone: 'error',
                      title: `Benchmark run failed on ${connection.name}`,
                      body: finished.error_message || undefined,
                      to: `/sources/${connection.id}/knowledge`,
                      toLabel: 'Open the store',
                    }
                  }
                  return {
                    tone: 'ok',
                    title: `Benchmark finished on ${connection.name}`,
                    // The denominator, always: an accuracy over a shrinking
                    // set of questions is the classic silent lie, and the
                    // strip on the tab makes the same point at more length.
                    body: `${finished.matched} of ${finished.scored} scored `
                      + `${finished.scored === 1 ? 'question' : 'questions'} matched`
                      + (finished.scored < finished.total
                        ? ` — ${finished.total - finished.scored} could not be probed.`
                        : '.'),
                    to: `/sources/${connection.id}/knowledge`,
                    toLabel: 'See the score',
                  }
                },
              })
            } catch (err) {
              setError(messageOf(err))
            }
          }}
        />
      )}

      <div
        style={{
          position: 'sticky', top: 0, zIndex: 2, display: 'flex',
          flexDirection: 'column', gap: 10, padding: '2px 0 10px',
          background: 'var(--bg)',
        }}
      >
        <div
          style={{
            display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
          }}
        >
          <Segmented
            ariaLabel="What to show"
            value={view}
            onChange={setView}
            options={tabs.map((tab) => ({
              value: tab,
              label: (
                <>
                  {VIEW_LABEL[tab]}
                  <span
                    style={{
                      fontSize: 11, fontWeight: 600,
                      color: view === tab ? 'var(--text-dim)' : 'var(--text-faint)',
                    }}
                  >
                    {counts[tab]}
                  </span>
                </>
              ),
            }))}
          />
          <span style={{ flex: 1 }} />
          {canCurate && synced && rows.length > 1 && (
            <GhostButton onClick={sweep} disabled={sweeping}>
              {sweeping ? <Spinner size={13} /> : <Icon.Refresh size={13} />}
              Check the store
            </GhostButton>
          )}
          {canCurate && synced && (
            <PrimaryButton onClick={() => { setPrefill(null); setEditing('new') }}>
              <Icon.Plus size={14} />
              Teach a question
            </PrimaryButton>
          )}
        </div>
        {/* Wherever the open view has rows in it — which is the only honest
            rule. Hidden only over an empty list, where the panel underneath is
            already saying the one thing there is to say. */}
        {counts[view] > 0 && (
          <SearchField
            value={search}
            onChange={setSearch}
            placeholder={SEARCH_LABEL[view]}
            ariaLabel={SEARCH_LABEL[view]}
          />
        )}
      </div>

      {sweepNote && (
        <div style={hint()}>{sweepNote}</div>
      )}

      {/* Store-wide notes, on the view they are about. A line about unused
          templates over a list of suggestions is a sentence about something
          the reader cannot see. */}
      {view === 'taught' && health && health.unused.length > 0 && (
        // The quietest possible treatment, from §4.7: a faint line and no
        // action button. A template written for a question asked once a year
        // is not waste — this is information, not an accusation.
        <div style={{ fontSize: 11, color: 'var(--text-faint)' }}>
          {health.unused.length}{' '}
          {health.unused.length === 1 ? 'template has' : 'templates have'} had no
          matches in {health.unused_after_days} days.
        </div>
      )}

      {view === 'taught' && embeddings && (
        // **Not** gated on `rows.length`, and that gate was the bug.
        //
        // How a store is searched is a property of the *connection*, not of
        // how many questions it happens to hold right now, and the two states
        // the gate hid are the two that matter most: a connection with nothing
        // taught could never be switched to embedding search *before* teaching
        // anything, and a connection that had it switched on lost the only
        // control that turns it off the moment its last template was archived
        // — pin intact, invisible.
        <MatchingMode
          status={embeddings}
          canCurate={canCurate}
          busy={switching}
          onToggle={async (enabled) => {
            setSwitching(true)
            setSweepNote(null)
            try {
              const next = await api.setEmbeddings(connection.id, enabled)
              setEmbeddings(next)
            } catch (err) {
              setError(messageOf(err))
            } finally {
              setSwitching(false)
            }
          }}
        />
      )}

      {/* ── what the store knows ─────────────────────────────────────── */}
      {view === 'taught' && counts.taught === 0 && (
        <FirstRun
          canCurate={canCurate && synced}
          reviews={counts.flagged}
          suggestions={counts.suggested}
          onTeach={() => { setPrefill(null); setEditing('new') }}
          onSuggestions={() => setView('suggested')}
          onFlagged={() => setView('flagged')}
        />
      )}

      {view === 'taught' && counts.taught > 0 && visible.length === 0 && (
        <EmptyState
          title={`No taught question matches “${search}”.`}
          body="A search that finds nothing is itself a curation signal — this may be a question worth teaching."
          action={
            canCurate && synced ? (
              <PrimaryButton onClick={() => { setPrefill(null); setEditing('new') }}>
                Teach this question
              </PrimaryButton>
            ) : undefined
          }
        />
      )}

      {view === 'taught' && (
        <>
          {/* Two groups in one list rather than two lists: a template that
              stopped working is the same record as one that works, and it is
              first because it is the only one with anything to do. The
              headings appear only when there is a second group to tell it
              apart from. */}
          <TemplateList
            heading={split.needsYou.length > 0 && split.taught.length > 0
              ? 'Needs attention' : undefined}
            rows={split.needsYou}
            all={rows}
            staleIds={staleIds}
            selectedId={selectedId}
            canCurate={canCurate}
            onSelect={toggle}
            onEdit={(t) => setEditing(t)}
            onArchive={archive}
          />
          <TemplateList
            heading={split.needsYou.length > 0 && split.taught.length > 0
              ? 'Working' : undefined}
            rows={split.taught}
            all={rows}
            staleIds={staleIds}
            selectedId={selectedId}
            canCurate={canCurate}
            onSelect={toggle}
            onEdit={(t) => setEditing(t)}
            onArchive={archive}
          />
        </>
      )}

      {/* ── questions people asked that nobody has taught ────────────── */}
      {view === 'suggested' && (
        backlog.length === 0 ? (
          <EmptyState
            title="Nothing waiting."
            body="Questions people ask that this store cannot answer collect here, so the next thing worth teaching is a list rather than a memory."
          />
        ) : visibleBacklog.length === 0 ? (
          <EmptyState
            title={`Nothing asked here matches “${search}”.`}
            body="This list is what people actually asked. A word that is not in it is a word nobody has used here yet."
          />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {visibleBacklog.map((item, i) => (
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
                onDefine={() => navigate(`/sources/${connection.id}/semantic`)}
              />
            ))}
          </div>
        )
      )}

      {/* ── answers somebody flagged ─────────────────────────────────── */}
      {view === 'flagged' && visibleReviews.length === 0 && (
        <EmptyState
          title={`No flag matches “${search}”.`}
          body="Every answer somebody marked wrong on this connection is in this list."
        />
      )}

      {view === 'flagged' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {visibleReviews.map((review) => (
            <Fragment key={review.id}>
              <ReviewRow
                review={review}
                selected={openReview?.id === review.id}
                onSelect={() =>
                  setOpenReview(openReview?.id === review.id ? null : review)
                }
              />
              {openReview?.id === review.id && (
                <ReviewPane
                  review={review}
                  canCurate={canCurate}
                  onTeach={() => {
                    setPrefill({
                      question: review.question,
                      sql: review.sql,
                      source: 'CHAT_CONFIRMED',
                    })
                    setResolving(review.id)
                    setEditing('new')
                    setOpenReview(null)
                  }}
                  onDismiss={async (note) => {
                    try {
                      await api.resolve(connection.id, review.id, {
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
            </Fragment>
          ))}
        </div>
      )}

      {/* ── what was taken out of use, and the way back ──────────────── */}
      {view === 'archived' && (
        split.archived.length === 0 ? (
          <EmptyState
            title={`Nothing archived matches “${search}”.`}
            body="Archiving never destroys a question — everything taken out of use is still here."
          />
        ) : (
          <TemplateList
            rows={split.archived}
            all={rows}
            staleIds={staleIds}
            selectedId={selectedId}
            canCurate={canCurate}
            busyId={restoring}
            onSelect={toggle}
            onEdit={(t) => setEditing(t)}
            onArchive={archive}
            onRestore={restore}
          />
        )
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
/**
 * Nothing taught here yet — said differently depending on what *is* here.
 *
 * The first version was one hero panel reading *"write a question the way
 * someone would ask it, paste the SQL that answers it"*, rendered whenever the
 * template list was empty. On a connection with an empty store and a backlog
 * that is a screen arguing with itself: it tells you to start from a blank
 * page directly above twenty-two questions people really asked, each with a
 * *Teach this* button beside it. It buries the better path under an invitation
 * to ignore it, and it spends 200px of the top of the page doing so.
 *
 * So the panel is the hero only when the page really is empty. With a queue
 * below it, the framing shrinks to one line and points at the queue — which
 * is both shorter and the correct advice, because a question somebody actually
 * asked is a better first template than one you invent.
 */
function FirstRun({
  canCurate, reviews, suggestions, onTeach, onSuggestions, onFlagged,
}: {
  canCurate: boolean
  /** Flags raised on wrong answers, one tab away. */
  reviews: number
  /** Questions that went unanswered, one tab away. */
  suggestions: number
  onTeach: () => void
  onSuggestions: () => void
  onFlagged: () => void
}) {
  if (!canCurate) {
    return (
      <EmptyState
        icon={<Icon.Book size={20} />}
        title="Nothing taught here yet"
        body="When somebody with curator access teaches this connection a question, it appears here — and the connection answers that question the same way every time it is asked."
      />
    )
  }

  // The two doors out of an empty store, and they are not equal: writing one
  // from scratch is the thing this screen is for, and the backlog is the
  // shortcut. Both are offered because a first-run panel that only says what
  // *could* be done is a page nobody leaves.
  return (
    <EmptyState
      icon={<Icon.Book size={20} />}
      title="Teach this connection"
      body={
        suggestions > 0 || reviews > 0
          ? 'Write a question the way someone would ask it, paste the SQL that answers it, and this connection answers it the same way next time. You do not have to start from a blank page — real questions are already waiting.'
          : 'Write a question the way someone would ask it, paste the SQL that answers it, and this connection answers it the same way next time.'
      }
      action={
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap',
                      justifyContent: 'center' }}>
          <PrimaryButton onClick={onTeach}>
            <Icon.Plus size={14} />
            Teach a question
          </PrimaryButton>
          {suggestions > 0 && (
            <GhostButton onClick={onSuggestions}>
              {suggestions} already asked
            </GhostButton>
          )}
          {reviews > 0 && (
            <GhostButton onClick={onFlagged}>
              {reviews} flagged {reviews === 1 ? 'answer' : 'answers'}
            </GhostButton>
          )}
        </div>
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
  item, canCurate, onTeach, onDefine,
}: {
  item: Suggestion
  canCurate: boolean
  onTeach: () => void
  /** Where a row that is **not** a question goes. `suggestionView` gives
   *  `UNKNOWN_WORDS` no action on purpose — the fix for a word nobody here
   *  recognises is usually a synonym in the semantic layer, not a template —
   *  and the consequence was a row in a work queue offering nothing at all.
   *  It has an action; it is just a different one. */
  onDefine?: () => void
}) {
  const view = suggestionView({
    kind: item.kind, question: item.question, count: item.count,
    reason: item.reason, sql: item.sql, words: item.words,
  })
  const actionable = Boolean(view.action) && canCurate
  const definable = !view.action && canCurate && !!onDefine

  return (
    <div
      className="rm-krow"
      style={{
        display: 'flex', gap: 10, alignItems: 'flex-start',
        padding: '10px 12px', borderRadius: 10, background: 'var(--panel)',
        border: '1px solid var(--border)',
      }}
    >
      <span
        aria-hidden
        style={{ display: 'grid', placeItems: 'center', width: 18, height: 20,
                 color: view.tone === 'amber' ? 'var(--amber)' : 'var(--text-faint)' }}
      >
        {view.glyph === '⚠' ? (
          <Icon.Alert size={13} />
        ) : (
          <svg width="9" height="9" viewBox="0 0 9 9" fill="none" aria-hidden>
            <circle cx="4.5" cy="4.5" r="3.6" stroke="currentColor" strokeWidth="1.6" />
          </svg>
        )}
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
      {definable && (
        <GhostButton onClick={onDefine}>Name these words →</GhostButton>
      )}
    </div>
  )
}

/**
 * The taught questions, and everything a curator does to one.
 *
 * **The detail opens under the row it belongs to.** It used to render once, at
 * the bottom of the page, below every other section — so clicking the third
 * row of a store scrolled the answer somewhere the reader was not looking, and
 * the connection between the row and what it opened had to be remembered
 * rather than seen. Attached to its own row, the two read as one record.
 */
function TemplateList({
  heading, rows, all, staleIds, selectedId, canCurate, busyId,
  onSelect, onEdit, onArchive, onRestore,
}: {
  /** Only when a second group exists to tell this one apart from. */
  heading?: string
  rows: TemplateRow[]
  /** The full records, so an expanded row can show its detail and its actions
   *  can send the thing the API takes rather than the thing the list draws. */
  all: KnowledgeTemplate[]
  staleIds: string[]
  selectedId: string | null
  canCurate: boolean
  /** The row with a request in flight, so its own control spins rather than
   *  the page going quiet. */
  busyId?: string | null
  onSelect: (id: string) => void
  onEdit: (template: KnowledgeTemplate) => void
  onArchive: (template: KnowledgeTemplate) => void
  /** Present only in the archive, which is the one place a row comes back. */
  onRestore?: (row: TemplateRow) => void
}) {
  if (rows.length === 0) return null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {heading && <SectionHeading>{heading} · {rows.length}</SectionHeading>}
      {rows.map((row) => {
        const full = all.find((t) => t.id === row.id)
        const open = row.id === selectedId && !!full
        return (
          <Fragment key={row.id}>
            <Row
              row={row}
              drifted={staleIds.includes(row.id)}
              selected={row.id === selectedId}
              attached={open}
              onSelect={() => onSelect(row.id)}
              actions={
                canCurate && full ? (
                  <RowActions
                    busy={busyId === row.id}
                    onEdit={() => onEdit(full)}
                    onArchive={() => onArchive(full)}
                    onRestore={onRestore ? () => onRestore(row) : undefined}
                  />
                ) : null
              }
            />
            {open && full && (
              <Detail
                attached
                template={full}
                drifted={staleIds.includes(full.id)}
                canCurate={canCurate}
                siblings={all}
                onEdit={() => onEdit(full)}
                onArchive={() => onArchive(full)}
                onClose={() => onSelect(full.id)}
              />
            )}
          </Fragment>
        )
      })}
    </div>
  )
}

/**
 * Edit and archive, on the row itself.
 *
 * They were three clicks away — select the row, read the detail, find the
 * footer — for two actions a curator performs more than any other. Icons
 * rather than words because the row is dense and the two verbs are the two
 * every list of records has; the label travels in `aria-label` and the
 * tooltip, never in colour alone.
 *
 * **Archiving asks.** Not a modal, and not an undo toast either: the button
 * becomes the question, in the place the answer is expected, and the
 * destructive half is the one wearing the word.
 */
function RowActions({
  busy, onEdit, onArchive, onRestore,
}: {
  busy: boolean
  onEdit: () => void
  onArchive: () => void
  onRestore?: () => void
}) {
  const [confirming, setConfirming] = useState(false)

  if (onRestore) {
    return (
      <GhostButton
        onClick={onRestore}
        disabled={busy}
        style={{ padding: '4px 9px', fontSize: 12 }}
      >
        {busy ? <Spinner size={12} /> : <Icon.Refresh size={12} />}
        Restore
      </GhostButton>
    )
  }

  if (confirming) {
    return (
      <>
        <DangerButton onClick={onArchive} style={{ padding: '4px 9px', fontSize: 12 }}>
          Archive it
        </DangerButton>
        <GhostButton
          onClick={() => setConfirming(false)}
          style={{ padding: '4px 9px', fontSize: 12 }}
        >
          Cancel
        </GhostButton>
      </>
    )
  }

  return (
    <>
      <GhostButton
        onClick={onEdit}
        aria-label="Edit this question"
        title="Edit this question"
        style={{ padding: '5px 7px' }}
      >
        <Icon.Pencil size={13} />
      </GhostButton>
      <GhostButton
        onClick={() => setConfirming(true)}
        aria-label="Archive this question"
        title="Archive this question"
        style={{ padding: '5px 7px' }}
      >
        <Icon.Trash size={13} />
      </GhostButton>
    </>
  )
}

/**
 * The mark a status draws, alone.
 *
 * `statusOf` still returns `✓ ⚠ ○` and the tests still read them: they are the
 * *model's* answer, and they are what makes a status legible in greyscale.
 * What the screen draws is an icon from the same set as every other icon in
 * the product, at the same stroke, because a typeface's tick beside a drawn
 * pencil is two icon systems in one row.
 */
function StatusMark({ status, size = 13 }: {
  status: ReturnType<typeof statusOf>
  size?: number
}) {
  return (
    <>
      {status.glyph === '✓' && <Icon.Check size={size} />}
      {status.glyph === '⚠' && <Icon.Alert size={size} />}
      {status.glyph === '○' && (
        <svg width={size - 4} height={size - 4} viewBox="0 0 9 9" fill="none" aria-hidden>
          <circle cx="4.5" cy="4.5" r="3.6" stroke="currentColor" strokeWidth="1.6" />
        </svg>
      )}
    </>
  )
}

/** The tinted ground each tone stands on. `faint` has no `--faint-bg` and
 *  should not invent one: an archived row is not a warning, it is a row set
 *  aside, and the panel's own alternate surface is what "set aside" looks
 *  like everywhere else in this product. */
const FLAG_TONE: Record<string, { fg: string; bg: string }> = {
  green: { fg: 'var(--green)', bg: 'var(--green-bg)' },
  amber: { fg: 'var(--amber)', bg: 'var(--amber-bg)' },
  faint: { fg: 'var(--text-dim)', bg: 'var(--panel-alt)' },
}

/**
 * What a template's status looks like when it is *said* rather than marked.
 *
 * There were two vocabularies for one fact: a coloured tick at the head of the
 * row, and — two lines below it, in the detail — a bare word in a
 * rounded-rectangle chip, sitting beside a square button and agreeing with
 * nothing. A flag is a mark **and** a word in one pill, at one size, in one
 * shape, so *Active*, *Stale* and *Archived* are the same object saying
 * different things rather than three different-looking treatments.
 *
 * The word never travels on colour alone: the mark differs in shape too, and
 * greyscale keeps both.
 */
function StatusFlag({ status }: { status: ReturnType<typeof statusOf> }) {
  const tone = FLAG_TONE[status.tone] ?? FLAG_TONE.faint
  return (
    <span
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5, flexShrink: 0,
        padding: '3px 9px 3px 7px', borderRadius: 999, fontSize: 11,
        fontWeight: 600, lineHeight: 1.45, whiteSpace: 'nowrap',
        color: tone.fg, background: tone.bg,
      }}
    >
      <span aria-hidden style={{ display: 'grid', placeItems: 'center' }}>
        <StatusMark status={status} size={12} />
      </span>
      {status.label}
    </span>
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
  row, drifted, selected, attached, actions, onSelect,
}: {
  row: TemplateRow
  drifted: boolean
  selected: boolean
  /** The detail is open directly below, so the row gives up its bottom edge
   *  and the two draw as one card instead of two stacked ones. */
  attached?: boolean
  actions?: React.ReactNode
  onSelect: () => void
}) {
  const status = statusOf(row, drifted)
  const subtitle = rowSubtitle(row, drifted)
  const role = roleLabel(row.role)
  // A flag is for an exception. Every taught question is Active, so a pill
  // saying so on every row is a column of the same word — the mark at the head
  // of the row already carries it. Stale, Conflicted and Archived are the ones
  // worth stopping on, and they get the word.
  const flagged = status.label !== 'Active'
  // …and it is said once. `rowSubtitle` puts *why you are looking at this row*
  // in the right slot, which for an archived row is the word "Archived" — the
  // flag beside it now. Two of the same word in one row reads as two facts.
  const right = flagged && subtitle.right === status.label ? '' : subtitle.right

  return (
    // A row, not a button: the actions inside it are buttons of their own, and
    // a button inside a button is invalid markup that browsers resolve by
    // dropping one of them. The whole question stays clickable — that is the
    // inner button — and Tab reaches the row, then Edit, then Archive.
    <div
      className="rm-krow"
      style={{
        display: 'flex', gap: 8, alignItems: 'flex-start',
        padding: '4px 6px 4px 4px', borderRadius: 10,
        background: selected ? 'var(--panel-alt)' : 'var(--panel)',
        border: `1px solid ${selected ? 'var(--border-strong)' : 'var(--border)'}`,
        ...(attached
          ? {
            borderBottomLeftRadius: 0,
            borderBottomRightRadius: 0,
            borderBottomColor: 'transparent',
          }
          : null),
      }}
    >
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected}
        aria-expanded={selected}
        style={{
          display: 'flex', gap: 10, alignItems: 'flex-start', flex: 1,
          minWidth: 0, textAlign: 'start', padding: '6px 6px 6px 8px',
          borderRadius: 8, border: 'none', background: 'transparent',
          color: 'inherit', font: 'inherit', cursor: 'pointer',
        }}
      >
        <span
          aria-hidden
          style={{
            display: 'grid', placeItems: 'center', width: 18, height: 20,
            flexShrink: 0,
            color: `var(--${status.tone === 'faint' ? 'text-faint' : status.tone})`,
          }}
        >
          <StatusMark status={status} />
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
              {right}
            </span>
          </span>
        </span>
        {role && <Chip tone="neutral" small>{role}</Chip>}
        {flagged && <StatusFlag status={status} />}
        <span className="rm-sr-only">{status.label}</span>
      </button>
      {actions && (
        <span
          className="rm-krow-actions"
          style={{
            display: 'flex', alignItems: 'center', gap: 2, flexShrink: 0,
            minHeight: 30,
          }}
        >
          {actions}
        </span>
      )}
    </div>
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
  template, drifted, canCurate, siblings, attached, onEdit, onArchive, onClose,
}: {
  template: KnowledgeTemplate
  drifted: boolean
  canCurate: boolean
  /** Every other template on this connection, so a conflict can name the
   *  question it disagrees with rather than printing a uuid. */
  siblings: KnowledgeTemplate[]
  /** Rendered directly under its own row: square the top edge so the row and
   *  what it opened are one card, not a card under a card. */
  attached?: boolean
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
        ...(attached
          ? { marginTop: -6, borderTopLeftRadius: 0, borderTopRightRadius: 0 }
          : null),
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
          {/* The row this is attached to is showing the question two lines
              above, in the same words. Printing it again is not emphasis, it
              is a reader wondering whether they are two records. */}
          {!attached && (
            <div dir={dirOf(template.question)}
                 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-strong)' }}>
              <Question text={template.question} />
            </div>
          )}
          <div style={{ fontSize: 11, color: 'var(--text-faint)',
                        marginTop: attached ? 0 : 3 }}>
            {template.verified_at
              ? `Verified ${relativeTime(template.verified_at)}`
              : 'Not yet verified'}
            {' · '}
            {template.hit_count} {template.hit_count === 1 ? 'hit' : 'hits'}
          </div>
        </div>
        <StatusFlag status={status} />
        <button
          type="button"
          aria-label="Close"
          title="Close"
          className="rm-icon-btn"
          onClick={onClose}
          style={{
            // Round, quiet, and the same 24px target as every other icon
            // button in the product. It used to inherit the browser's default
            // button chrome — a grey square beside a tinted pill, two shapes
            // neither of which was chosen.
            display: 'grid', placeItems: 'center', width: 24, height: 24,
            flexShrink: 0, border: 'none', borderRadius: 999,
            background: 'transparent', color: 'var(--text-faint)',
            cursor: 'pointer',
            ['--rm-hover-bg' as string]: 'var(--panel)',
          }}
        >
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

        {template.status === 'CONFLICTED' && (
          <ConflictEvidencePane
            evidence={template.conflict_evidence}
            others={conflictLabels(template, siblings)}
          />
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
 * The offer to start measuring, before any set exists.
 *
 * One sentence and one button, and it says the cost up front: the questions
 * that go into a set **stop answering questions**, because §1.3's rule is that
 * a template is retrievable or benchmarkable and never both. Hiding that until
 * afterwards would be the kind of surprise that makes people distrust a
 * number.
 */
function StartMeasuring({
  candidates, onCreate,
}: {
  candidates: number
  onCreate: () => void | Promise<void>
}) {
  const [busy, setBusy] = useState(false)

  return (
    <div
      style={{
        border: '1px dashed var(--border)', borderRadius: 12,
        padding: '12px 16px', display: 'flex', gap: 14,
        alignItems: 'center', flexWrap: 'wrap',
      }}
    >
      <div style={{ flex: 1, minWidth: 240 }}>
        <div style={{ fontSize: 13, color: 'var(--text-strong)' }}>
          Measure whether teaching this connection helped
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 3 }}>
          Turns {candidates} taught {candidates === 1 ? 'question' : 'questions'}{' '}
          into a benchmark and holds some back, so the score is measured on
          questions the system cannot look up. Those questions stop answering
          chat until you delete the benchmark.
        </div>
      </div>
      <GhostButton
        onClick={async () => {
          setBusy(true)
          try {
            await onCreate()
          } finally {
            setBusy(false)
          }
        }}
        disabled={busy}
      >
        {busy ? <Spinner size={13} /> : <Icon.Check size={13} />}
        Create a benchmark
      </GhostButton>
    </div>
  )
}


/**
 * The score strip — §4.8. One line at the top of the tab, and only once a
 * benchmark set exists: **never an empty chart.**
 *
 * **The held-out number is first, larger, and the one on the sparkline.** The
 * taught number is shown because hiding it would be dishonest, and shown
 * second and smaller because it is the number that goes up for the wrong
 * reasons. Genie's Evaluations tab shows one number; this shows two and says
 * which one to believe.
 *
 * `—` and not `0%` when a run has no held-out questions to score. A run that
 * measured nothing has no accuracy, and printing zero for it would be the
 * loudest possible wrong answer.
 */
function ScoreStrip({
  overview, onRun,
}: {
  overview: BenchmarkOverview
  onRun: (setId: string) => void | Promise<void>
}) {
  const set = overview.sets[0]
  const view = scoreView(set.runs, set.held_out_count)

  return (
    <div
      style={{
        border: '1px solid var(--border)', borderRadius: 12,
        background: 'var(--panel)', padding: '12px 16px',
        display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap',
      }}
    >
      <div style={{ flex: 1, minWidth: 220 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <span style={{ fontSize: 11, color: 'var(--text-faint)', width: 62 }}>
            Accuracy
          </span>
          <span style={{ fontSize: 24, fontWeight: 600, color: 'var(--text-strong)',
                         lineHeight: 1.1 }}>
            {percent(view.heldOut)}
          </span>
          <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
            on {view.heldOutCount} held-out{' '}
            {view.heldOutCount === 1 ? 'question' : 'questions'}
          </span>
          {view.spark.length > 1 && (
            <Sparkline values={sparkHeights(view.spark)} />
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10,
                      marginTop: 2 }}>
          <span style={{ width: 62 }} />
          <span style={{ fontSize: 14, color: 'var(--text-dim)' }}>
            {percent(view.taught)}
          </span>
          <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
            on questions answered from a template
          </span>
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 5 }}>
          {set.name}
          {view.ran && set.runs[0]?.finished_at
            ? ` · last run ${relativeTime(set.runs[0].finished_at)}`
            : view.ran ? '' : ' · not run yet'}
          {view.unscored > 0 &&
            ` · ${view.unscored} could not be scored`}
        </div>
        {view.failed && (
          <div style={{ fontSize: 11, color: 'var(--amber)', marginTop: 4 }}>
            {view.failed}
          </div>
        )}
      </div>

      {overview.can_curate && (
        <GhostButton onClick={() => onRun(set.id)} disabled={view.running}>
          {view.running ? <Spinner size={13} /> : <Icon.Refresh size={13} />}
          {view.running ? 'Running' : 'Run benchmark'}
        </GhostButton>
      )}
    </div>
  )
}

/**
 * Six bars, against the **fixed 0–100% scale** rather than the series' own
 * range. Self-normalising would turn 71/72/73% into a dramatic climb, which is
 * exactly the misreading a score strip must not invite.
 */
function Sparkline({ values }: { values: number[] }) {
  return (
    <span
      aria-hidden
      style={{ display: 'inline-flex', alignItems: 'flex-end', gap: 2,
               height: 16, marginInlineStart: 4 }}
    >
      {values.map((v, i) => (
        <span
          key={i}
          style={{
            width: 4,
            height: `${Math.max(2, Math.round(v * 16))}px`,
            borderRadius: 1,
            background: i === values.length - 1
              ? 'var(--accent)' : 'var(--border-strong)',
          }}
        />
      ))}
    </span>
  )
}


/**
 * The conflict's evidence: two answers to one question, side by side.
 *
 * **This is the pane no competitor can draw.** Fabric detects conflicting
 * instructions by reasoning over SQL text and reports a confidence score of
 * one to five; DataMind ran both statements through the guard, read-only and
 * row-capped, and compared the result sets — so what goes here is *"481,220
 * against 512,940"*, and the cell that moved is marked.
 *
 * Deliberately not a diff widget and deliberately no new colour: two small
 * tables in the tokens the tab already uses, the differing cell in `--amber`,
 * and the reason above them in the curator's own language.
 */
function ConflictEvidencePane({
  evidence, others,
}: {
  evidence: unknown
  others: string[]
}) {
  const view = conflictEvidence(evidence)

  return (
    <div>
      <Label>Why this is flagged</Label>
      <div style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 8 }}>
        {view.summary ||
          'Two templates answer this question differently.'}
      </div>

      {!view.hasRows ? (
        <div style={{ fontSize: 11, color: 'var(--text-faint)' }}>
          The rows that showed this are no longer stored. Run the check again to
          see them.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <EvidenceTable
            caption="This template"
            columns={view.mine.columns}
            rows={view.mine.rows}
            against={view.theirs.rows}
          />
          <EvidenceTable
            caption={others[0] ? `“${others[0]}”` : 'The other template'}
            columns={view.theirs.columns}
            rows={view.theirs.rows}
            against={view.mine.rows}
          />
        </div>
      )}

      <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 8 }}>
        Both are still stored and neither answers questions until one is fixed
        or archived — the system does not pick a winner.
      </div>
    </div>
  )
}

/** One side of the disagreement. The cell that differs is marked, so the
 *  reader is not asked to compare two tables by eye. */
function EvidenceTable({
  caption, columns, rows, against,
}: {
  caption: string
  columns: string[]
  rows: string[][]
  against: string[][]
}) {
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-faint)', marginBottom: 3 }}>
        {caption}
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ ...CODE, borderCollapse: 'collapse', width: '100%',
                        padding: 0, borderRadius: 8 }}>
          {columns.length > 0 && (
            <thead>
              <tr>
                {columns.map((c, i) => (
                  <th key={i} style={{ ...cellStyle, color: 'var(--text-faint)',
                                       fontWeight: 500 }}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
          )}
          <tbody>
            {rows.map((row, r) => {
              const differs = differingCells(row, against[r] ?? [])
              return (
                <tr key={r}>
                  {row.map((value, c) => (
                    <td key={c}
                        style={{ ...cellStyle,
                                 color: differs[c] ? 'var(--amber)' : undefined }}>
                      {value}
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

const cellStyle: React.CSSProperties = {
  padding: '4px 8px',
  borderBottom: '1px solid var(--border)',
  textAlign: 'left',
  whiteSpace: 'nowrap',
}

/** The questions this template disagrees with, by id — never a raw uuid. */
function conflictLabels(
  template: KnowledgeTemplate,
  siblings: KnowledgeTemplate[],
): string[] {
  const byId = new Map(siblings.map((t) => [t.id, t.question]))
  return (template.conflicts_with ?? [])
    .map((id) => byId.get(id))
    .filter((q): q is string => Boolean(q))
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

  const tables = (check?.referenced_tables ?? []).map((t) => t.split('.').pop() ?? t)
  const eligible = proposals.filter((p) => p.eligible)

  // What the box under the statement says about it, in one line. The long
  // green pill this replaces ("Valid · public.order_items, public.orders,
  // public.products") grew with the query until it pushed the label off its
  // own row; the verdict is a chip beside the label now, and the tables it
  // reads are a sentence in the box's own footer, where a long list can wrap.
  const boxStatus = checking
    ? 'Checking…'
    : !sql.trim()
      ? 'Paste the statement that answers the question.'
      : check?.valid
        ? tables.length > 0
          ? `Reads ${tables.join(', ')}`
          : 'Valid — it reads no tables.'
        : check
          // The guard's own sentence, in the footer of the box that produced
          // it — one place for the refusal, attached to the statement. It used
          // to be a chip saying *Rejected* and a red note underneath saying it
          // again before getting to the reason.
          ? check.issue || 'Rejected.'
          : 'Not checked yet'

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
        <div
          style={{
            display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap',
          }}
        >
          {/* Why Save is off, beside the button that is off — not at the far
              end of a scrolling form where the reader has to go looking for
              the reason they were refused. */}
          {ready.issue && (
            <span
              style={{
                display: 'flex', alignItems: 'flex-start', gap: 7, flex: '1 1 240px',
                minWidth: 0, fontSize: 11.5, lineHeight: 1.5, color: 'var(--amber)',
              }}
            >
              <span style={{ display: 'flex', paddingTop: 1 }}>
                <Icon.Alert size={13} />
              </span>
              {ready.issue}
            </span>
          )}
          <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
            <GhostButton onClick={onClose}>Cancel</GhostButton>
            <PrimaryButton onClick={save} disabled={!ready.ready || saving}>
              {saving && <Spinner />}
              Save template
            </PrimaryButton>
          </div>
        </div>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        {error && <ErrorNote>{error}</ErrorNote>}

        <Field
          label="Question"
          hint={
            question.includes('{')
              ? undefined
              : 'Write it the way someone would ask it. Wrap the parts that change in braces — {region}.'
          }
        >
          <TextInput
            value={question}
            dir={dirOf(question)}
            placeholder="revenue by month for {region} in {year}"
            onChange={(e) => setQuestion(e.target.value)}
          />
          {question.includes('{') && params.length > 0 && (
            <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 5 }}>
              Matches questions like &ldquo;{previewQuestion(question, params)}&rdquo;
            </div>
          )}
        </Field>

        <Field
          label="SQL"
          status={
            // The last verdict stays up while the next one is being fetched —
            // the box's own footer is what says "Checking…", and a chip that
            // vanished on every keystroke made the header flicker for the
            // whole time somebody was typing a statement.
            <span aria-live="polite" style={{ display: 'flex', alignItems: 'center' }}>
              {check?.valid && (
                <Chip tone="green">
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                    <Dot color="var(--green)" />
                    Valid
                  </span>
                </Chip>
              )}
              {check && !check.valid && <Chip tone="red">Rejected</Chip>}
            </span>
          }
        >
          {/* The same shell the report and tile editors use: a statement and
              the verdict on it are one object, so they share one border. */}
          <div className="rm-sqlbox">
            <TextArea
              className="mono"
              dir="ltr"
              value={sql}
              spellCheck={false}
              placeholder="SELECT …"
              onChange={(e) => setSql(e.target.value)}
              // `.rm-sqlbox` owns the surface now, so the statement gives up
              // the code background it used to draw for itself — two shades
              // inside one border read as a box inside a box.
              style={{
                ...CODE, background: 'transparent', minHeight: 150,
                whiteSpace: 'pre-wrap',
              }}
            />
            <div className="rm-sqlbox-bar">
              <span
                className={`rm-sqlbox-hint${
                  check && !check.valid && !checking ? ' is-error' : ''
                }`}
                style={{ whiteSpace: 'normal' }}
              >
                {checking && <Spinner size={11} />}
                {boxStatus}
              </span>
            </div>
          </div>
          {marked.some((s) => s.slot) && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 11, color: 'var(--text-faint)', marginBottom: 4 }}>
                Stored like this — the highlighted values become parameters:
              </div>
              <pre
                aria-hidden
                style={{ ...CODE, margin: 0, padding: 10, borderRadius: 8,
                         background: 'var(--code-bg)', whiteSpace: 'pre-wrap' }}
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
            </div>
          )}
        </Field>

        {proposals.length > 0 && (
          <Field
            label="Parameters"
            hint="Found by reading your SQL — nothing was sent anywhere."
            status={
              <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
                {accepted.length} of {eligible.length} used
              </span>
            }
          >
            <div
              style={{
                border: '1px solid var(--border)', borderRadius: 8,
                overflow: 'hidden',
                display: 'flex', flexDirection: 'column',
              }}
            >
              {proposals.map((proposal, index) => (
                <Proposal
                  key={`${proposal.name}-${proposal.occurrence}`}
                  proposal={proposal}
                  first={index === 0}
                  checked={accepted.includes(proposal.name)}
                  onToggle={() => toggle(proposal.name)}
                  onHover={setHovered}
                />
              ))}
            </div>
          </Field>
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

        {/* An option with its consequence attached, rather than a bare
            checkbox and a sentence the reader has to finish themselves. */}
        <label
          style={{
            display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer',
            padding: '10px 12px', borderRadius: 8,
            border: `1px solid ${benchmark ? 'var(--accent-border)' : 'var(--border)'}`,
            background: benchmark ? 'var(--accent-bg)' : 'transparent',
            transition: 'border-color .12s ease, background .12s ease',
          }}
        >
          <input
            type="checkbox"
            checked={benchmark}
            onChange={(e) => setBenchmark(e.target.checked)}
            style={{ accentColor: 'var(--accent)', cursor: 'pointer', marginTop: 1 }}
          />
          <span style={{ minWidth: 0 }}>
            <span style={{ display: 'block', fontSize: 12.5, color: 'var(--text)' }}>
              Use this to measure accuracy, not to answer questions
            </span>
            <span
              style={{
                display: 'block', fontSize: 11, color: 'var(--text-faint)', marginTop: 2,
              }}
            >
              It joins the benchmark set. Questions are never answered from it.
            </span>
          </span>
        </label>
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
  proposal, first, checked, onToggle, onHover,
}: {
  proposal: ParamProposal
  /** No rule above the first row: the container already draws that edge. */
  first: boolean
  checked: boolean
  onToggle: () => void
  onHover: (name: string | null) => void
}) {
  const [hover, setHover] = useState(false)
  return (
    <label
      onMouseEnter={() => {
        setHover(true)
        onHover(proposal.name)
      }}
      onMouseLeave={() => {
        setHover(false)
        onHover(null)
      }}
      style={{
        display: 'flex', gap: 9, alignItems: 'center', padding: '9px 11px',
        borderTop: first ? 'none' : '1px solid var(--border)', fontSize: 12,
        cursor: proposal.eligible ? 'pointer' : 'default',
        background: hover && proposal.eligible ? 'var(--panel-alt)' : 'transparent',
        transition: 'background .12s ease',
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={!proposal.eligible}
        onChange={onToggle}
        style={{
          accentColor: 'var(--accent)',
          cursor: proposal.eligible ? 'pointer' : 'default',
          flexShrink: 0,
        }}
      />
      {/* The substitution, read left to right as one phrase: this literal
          becomes this name. The arrow is the only thing between them. */}
      <span
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0,
          opacity: proposal.eligible ? 1 : 0.6,
        }}
      >
        <code style={{ ...CODE, background: 'var(--accent-bg)', color: 'var(--accent)',
                       padding: '1px 5px', borderRadius: 3, maxWidth: 160,
                       overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {proposal.literal}
        </code>
        <Icon.ArrowRight size={12} stroke="var(--text-faint)" />
        <code style={{ ...CODE, background: 'none', padding: 0,
                       color: 'var(--text)' }}>
          :{proposal.name}
        </code>
        <Chip small>{proposal.type}</Chip>
      </span>
      {/* A refusal is rendered rather than hidden, unticked and with its reason
          beside it — showing the rejected candidate teaches the rule better
          than hiding it, and the curator occasionally knows better. */}
      <span
        style={{
          flex: 1, minWidth: 0, textAlign: 'right', fontSize: 11.5,
          color: proposal.eligible ? 'var(--text-faint)' : 'var(--amber)',
        }}
      >
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
    conflicts_with: t.conflicts_with,
    conflict_evidence: t.conflict_evidence,
  }
}

/**
 * What a sweep did, in one sentence a curator can act on.
 *
 * Reported rather than left to a silent refresh: a button that appears to do
 * nothing is a button people press twice and then stop trusting. The "was not
 * allowed to look" case is named explicitly, because printing *"found no
 * conflicts"* for a connection whose checks are switched off would be a lie.
 */
/** How this store is searched, and the one control that changes it.
 *
 * §4.10's quietest treatment, and deliberately so: **word matching is not a
 * degraded state.** `pg_trgm` needs no provider, no key and no budget, and a
 * connection that stays there has lost nothing. So the off state gets a plain
 * sentence describing what the *other* mode adds rather than a warning about
 * what this one lacks, and the button reads as an upgrade rather than a fix.
 *
 * The four tones come from `embeddingView`, which is where the reasoning is.
 * The one worth naming here is `indexing`: a model can be pinned with vectors
 * still missing, and calling that "on" would promise a behaviour the next
 * question will not show.
 *
 * **There is no provider to pick here, and that is the point.** One embedder
 * serves the whole deployment — set up once in LLM providers, resolved by the
 * server for every connection — so this control is a switch and not a form.
 * The model a curator *does* choose is the one that answers, and it is offered
 * where a question is asked: chat, a dashboard tile, a report. `embedder` is
 * still reported, because "what made these vectors?" is a question a store has
 * to be able to answer; it is a sentence here rather than a dropdown.
 */
function MatchingMode({
  status, canCurate, busy, onToggle,
}: {
  status: EmbeddingStatus
  canCurate: boolean
  busy: boolean
  onToggle: (enabled: boolean) => void
}) {
  const view = embeddingView({ ...status, hasEmbedder: status.embedder !== null })
  const tint: Record<string, string> = {
    off: 'var(--text-faint)',
    indexing: 'var(--text-muted)',
    on: 'var(--accent)',
    problem: 'var(--warn)',
  }
  // Which provider made these vectors, or would. Never a picker: with one
  // embedder the answer is not a decision, and with none the button is hidden
  // instead, because an offer that cannot succeed teaches people to stop
  // pressing things.
  const using = status.embedder
  const canSwitchOn = using !== null

  return (
    <div
      style={{
        // Wraps rather than squeezing: on a phone the button used to sit
        // beside the sentence and fold it into a twelve-line column two words
        // wide. `minWidth` on the text keeps them side by side wherever there
        // is room for both.
        display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap',
        padding: '8px 10px', borderRadius: 8,
        border: '1px solid var(--border-subtle)',
      }}
    >
      <div style={{ flex: 1, minWidth: 230 }}>
        <div style={{ fontSize: 12, color: tint[view.tone], fontWeight: 500 }}>
          {view.label}
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 2 }}>
          {view.detail}
        </div>
        {status.enabled && using && (
          // Which endpoint made these vectors, said rather than implied: a
          // store is only reproducible if the provider is known as well as the
          // model name and the width, and that is the sentence somebody needs
          // when two providers serve one model name at two widths.
          <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 2 }}>
            Indexed by {using.name} at {status.dimension} dimensions.
          </div>
        )}
      </div>
      {canCurate && (status.enabled || canSwitchOn) && (
        <GhostButton onClick={() => onToggle(!status.enabled)} disabled={busy}>
          {busy ? <Spinner size={13} /> : null}
          {status.enabled ? 'Use word matching' : 'Use embedding search'}
        </GhostButton>
      )}
    </div>
  )
}

function sweepSummary(result: MaintenanceResult): string {
  const parts: string[] = []
  if (result.staled.length) {
    parts.push(`${result.staled.length} stopped working`)
  }
  if (result.revived.length) {
    parts.push(`${result.revived.length} started working again`)
  }
  if (result.conflicted.length) {
    parts.push(`${result.conflicted.length} disagree with another template`)
  }
  if (result.cleared.length) {
    parts.push(`${result.cleared.length} no longer disagree`)
  }

  const checked = `Checked ${result.checked} ${
    result.checked === 1 ? 'template' : 'templates'
  }`
  if (!result.conflicts_checked) {
    return `${checked}${parts.length ? `: ${parts.join(', ')}` : ' — nothing changed'}. ` +
      'Conflict checks are switched off for this connection, so no statements were run.'
  }
  const skipped = result.skipped.length
    ? ` ${result.skipped.length} pair${result.skipped.length === 1 ? '' : 's'} could ` +
      'not be checked — a parameter had no values to try.'
    : ''
  if (!parts.length) {
    return `${checked} and ${result.pairs_executed} pair${
      result.pairs_executed === 1 ? '' : 's'
    }. Nothing changed.${skipped}`
  }
  return `${checked}: ${parts.join(', ')}.${skipped}`
}

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : 'Something went wrong.'
}
