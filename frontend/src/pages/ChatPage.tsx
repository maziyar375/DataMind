/**
 * Chat: the section the product opens on, and the one a question is asked in.
 *
 * Three columns of responsibility. A rail of saved threads on the left; a
 * transcript in the middle capped at a readable measure rather than stretched
 * across the pane; a composer under it. This file owns all three, the run in
 * flight, and the pickers in the header. An individual turn — the step chips,
 * the "Generated SQL" disclosure, the result table, the metadata chips — is
 * `components/chat.tsx`, which renders a finished turn and a streaming one
 * with the same component on purpose (see its own header).
 *
 * Four decisions carry the screen:
 *
 *  - **A thread is bound to one database and one model.** Both are chosen in
 *    the header before the first message and lock the moment the transcript is
 *    non-empty, because every run in a thread has to stay explainable against
 *    a single pair — the same reason `runs.model_snapshot` exists on the
 *    backend. The database picker also carries the disclosure policy, so what
 *    leaves the customer's database is visible at the moment the question is
 *    asked and not by reading documentation.
 *  - **Live run state is kept apart from persisted messages.** `liveSteps`,
 *    `liveText`, `thinking` and `livePreview` are this component's; the
 *    transcript is the server's. That split is what lets a refresh mid-run
 *    recover from `GET /runs/{id}` rather than from React, and it is why
 *    reopening an old conversation replays the whole trail of how an answer
 *    was reached instead of showing a bare paragraph.
 *  - **The stream is the fast path, not the only one.** `attachStream` degrades
 *    to polling, replays by `Last-Event-ID`, and reattaches to a run still in
 *    flight when a thread is opened. Tokens are batched on a fixed cadence
 *    (`TEXT_FLUSH_MS`) rather than painted per token.
 *  - **Nothing is preselected at boot.** No thread, and the three bootstrap
 *    requests are `allSettled` so one failure cannot empty the other two
 *    pickers. Landing at the bottom of whatever was asked last makes the
 *    composer read as a follow-up to a conversation that may be days old.
 *
 * What this page deliberately does *not* own: the template editor opened from
 * an answer is the Knowledge tab's own `TemplateEditor`, reused rather than
 * reimplemented — the parameter proposals, the guard verdict and the
 * disclosure rule for a statement's literals must be identical, and two
 * editors are two chances to get one of them wrong.
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useMatch, useNavigate } from 'react-router-dom'
import {
  conversations, connections as connectionsApi, llmConfigs,
  isRunInFlight, runs, streamRun,
} from '../api/client'
import type {
  Connection, ConversationSummary, LlmConfig, MessageWithRun, RunDetail, RunStep,
  TableArtifactSpec,
} from '../api/types'
import {
  AssistantTurn, RunErrorCard, RunStoppedCard, UserBubble,
} from '../components/chat'
import { absorbThought, endThought } from '../components/thinking'
import type { ThinkingState } from '../components/thinking'
import { TemplateEditor } from '../components/knowledge'
import { AddToDashboardDialog, AddToReportDialog } from '../components/answer-destinations'
import {
  DisclosureBadge, ErrorNote, GlyphBadge, Icon, ListNewButton, SearchField, Spinner,
  dirOf, engineHue,
} from '../components/ui'
import { LIST_DRAWER_ID, ListScrim, ListToggle, useListDrawer } from '../components/list-drawer'

/**
 * How long streamed tokens are collected before they are painted.
 *
 * Short enough that the text still reads as typing, long enough that a
 * provider sending forty tokens a second costs the page a handful of renders
 * a second rather than forty.
 */
const TEXT_FLUSH_MS = 40

/**
 * `smooth`, unless the reader asked for less motion.
 *
 * The reduced-motion guard in styles.css can silence a CSS transition but not
 * a programmatic scroll, and the transcript glides on every arriving turn —
 * which is the movement that setting exists to stop.
 */
function glideBehavior(): ScrollBehavior {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
}

/**
 * An answer on its way somewhere else — the payload both destinations take.
 *
 * The question the reader typed, the statement that actually ran, and the
 * chart it was drawn as. No model call and no re-execution: this is the run's
 * own work, carried, which is the difference between *add to dashboard* and
 * *ask the same question again in a different box*.
 */
interface AnswerHandoff {
  to: 'dashboard' | 'report'
  question: string
  sql: string
  chartConfig: Record<string, unknown> | null
}

/**
 * What the picture on screen can honestly say about itself.
 *
 * The compiler stamps its decision into the Vega-Lite spec's `usermeta`
 * (`app/charts`), so the type, orientation and stack are read rather than
 * guessed from the encoding — a horizontal bar swaps its axes on the way to
 * the spec, and sniffing them back out is how a tile ends up drawn sideways.
 * The *columns* are deliberately not carried: the tile editor checks the same
 * statement itself and fills the axes from that result's own fields.
 */
function chartIntentOf(run: RunDetail): Record<string, unknown> | null {
  const spec = run.artifacts.find((artifact) => artifact.kind === 'CHART')?.spec as
    | { usermeta?: { datamind?: { chart_type?: string; orientation?: string; stack?: string } } }
    | undefined
  const meta = spec?.usermeta?.datamind
  if (!meta?.chart_type) return null
  const intent: Record<string, unknown> = { chart_type: meta.chart_type }
  // Only when they say something: each has a default the backend applies, and
  // writing it would freeze a decision the planner should keep making.
  if (meta.orientation && meta.orientation !== 'auto') intent.orientation = meta.orientation
  if (meta.stack && meta.stack !== 'stacked') intent.stack = meta.stack
  return intent
}

export default function ChatPage() {
  const [conversationList, setConversationList] = useState<ConversationSummary[]>([])
  // The open thread is the URL. Everything below still reads `activeId` the
  // way it did when this was `useState`, so the effects that clear a stream on
  // a thread change did not have to move; what changed is where the value
  // comes from and that a refresh keeps the conversation open.
  const navigate = useNavigate()
  // Below 700px the thread list is an overlay; above it this does nothing.
  const listDrawer = useListDrawer()
  const { pathname } = useLocation()
  const activeId = useMatch('/chat/:conversationId')?.params.conversationId ?? null
  const setActiveId = useCallback(
    (id: string | null, { replace = false } = {}) => {
      const next = id ? `/chat/${id}` : '/chat'
      // New chat pressed while already on an empty one is not a navigation:
      // pushing the same address again gives Back a step that does nothing.
      if (next === pathname) return
      navigate(next, { replace })
    },
    [navigate, pathname],
  )
  const [messages, setMessages] = useState<MessageWithRun[]>([])
  // Read by `regenerate`, which is given a stable identity so a transcript of
  // memoised turns does not re-render on every streamed token. It needs the
  // *current* transcript to find the question behind an answer, and a
  // dependency array would defeat the point.
  const messagesRef = useRef<MessageWithRun[]>([])
  useEffect(() => {
    messagesRef.current = messages
  }, [messages])
  // The answer a reader asked to teach, if any: the question they typed and
  // the statement it produced, handed straight to the Knowledge tab's editor.
  const [teaching, setTeaching] = useState<{ question: string; sql: string } | null>(
    null,
  )
  // Which destination dialog is open, and what it is carrying. One piece of
  // state rather than two booleans: the two are alternatives, and an answer
  // goes to exactly one place at a time.
  const [sending, setSending] = useState<AnswerHandoff | null>(null)
  const [connections, setConnections] = useState<Connection[]>([])
  const [models, setModels] = useState<LlmConfig[]>([])
  const [connectionId, setConnectionId] = useState<string>('')
  const [modelId, setModelId] = useState<string>('')
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Model-proposed follow-ups, refreshed after each answered turn. This is a
  // full completion against the whole schema on the connection's own model,
  // measured at 12-18s on a 42-table snapshot — several times longer than the
  // answer it follows, because the answer streams and this does not. So it
  // carries a pending flag (an empty row for twenty seconds reads as "there
  // are none") and a ticket (a reply that lands after the reader has moved on
  // belongs to a turn that is over, and must not repopulate the row).
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [suggestionsPending, setSuggestionsPending] = useState(false)
  const suggestionTicket = useRef(0)

  // Live run state, kept separate from persisted messages so a refresh
  // mid-run recovers from the server rather than from this component.
  const [liveSteps, setLiveSteps] = useState<RunStep[]>([])
  const [liveText, setLiveText] = useState('')
  // The model's reasoning channel for the run in flight. Null for a model
  // that does not think out loud, which is most of them.
  const [thinking, setThinking] = useState<ThinkingState | null>(null)
  // Whether reasoning is still arriving. A ref, not state: the first token
  // of the actual answer is what ends the thinking phase, and testing that
  // against state would mean a set on every token of the answer.
  const thinkingLive = useRef(false)
  // The rows, as soon as `execute` has them — half a minute before `present`
  // has finished writing the sentence about them, and a whole round trip
  // before the persisted turn arrives. Superseded by the run's TABLE artifact
  // the moment the turn is swapped in; see `RESULT_PREVIEW` in the backend.
  const [livePreview, setLivePreview] = useState<TableArtifactSpec | null>(null)
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  // A stop that has been asked for but not yet landed. The button has to stop
  // looking like a button the instant it is pressed, or it gets pressed twice.
  const [stopping, setStopping] = useState(false)
  const stopStreamRef = useRef<(() => void) | null>(null)
  // Which attachment owns the live view. A stream that finishes after another
  // has taken over must not clear the newer one's steps out from under it.
  const streamToken = useRef(0)
  const scrollRef = useRef<HTMLDivElement>(null)
  const followRef = useRef(true)
  // Id of a conversation `send` just created and is already populating, so the
  // load effect below doesn't re-fetch it out from under the optimistic turn.
  const justCreatedRef = useRef<string | null>(null)
  const [showJump, setShowJump] = useState(false)

  // Tokens arrive faster than a screen can usefully show them, and each one
  // used to be its own `setState` — so the whole transcript re-rendered and
  // re-scrolled forty times a second while the reader was trying to read it.
  // They are collected here and painted on a fixed cadence instead: the same
  // text, a fraction of the work.
  const pendingText = useRef('')
  const flushTimer = useRef<number | null>(null)

  const flushText = useCallback(() => {
    flushTimer.current = null
    const chunk = pendingText.current
    if (!chunk) return
    pendingText.current = ''
    setLiveText((prev) => prev + chunk)
  }, [])

  const appendText = useCallback(
    (delta: string) => {
      if (!delta) return
      pendingText.current += delta
      if (flushTimer.current === null) {
        flushTimer.current = window.setTimeout(flushText, TEXT_FLUSH_MS)
      }
    },
    [flushText],
  )

  /** Drop what has not been painted yet: a `TEXT_RESET`, or the end of a run. */
  const clearText = useCallback(() => {
    pendingText.current = ''
    if (flushTimer.current !== null) {
      window.clearTimeout(flushTimer.current)
      flushTimer.current = null
    }
    setLiveText('')
  }, [])

  /** Reasoning belongs to one run and is never replayed, so it goes with it. */
  const clearThinking = useCallback(() => {
    thinkingLive.current = false
    setThinking(null)
  }, [])

  // ── bootstrap ───────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        // Settled, not `all`. These three fill three independent pickers, so
        // one failing request must not empty the other two: with `all`, a
        // single expired call left the whole workspace blank — no history, no
        // database, no model — which reads as "my data is gone" rather than
        // "one request failed".
        const [convs, conns, llms] = await Promise.allSettled([
          conversations.list(),
          connectionsApi.list(),
          llmConfigs.list('chat'),
        ])
        if (cancelled) return
        if (convs.status === 'fulfilled') {
          setConversationList(convs.value)
          // Deliberately no preselection: opening the sidebar lands on an
          // empty new chat, not at the bottom of whatever was asked last.
          // Arriving mid-thread makes the composer read as a follow-up to a
          // conversation the reader may have finished days ago, and a thread
          // is bound to one database — so the header's pickers arrive locked
          // to a choice nobody just made. `activeId` stays null; the reader
          // picks a database and a model here, and selecting a saved
          // conversation from the list restores whatever it was started with.
        }
        if (conns.status === 'fulfilled') setConnections(conns.value)
        if (llms.status === 'fulfilled') setModels(llms.value)

        const missing = [
          convs.status === 'rejected' ? 'conversations' : null,
          conns.status === 'rejected' ? 'data sources' : null,
          llms.status === 'rejected' ? 'models' : null,
        ].filter((name): name is string => name !== null)
        if (missing.length > 0) {
          setError(`Could not load ${missing.join(', ')}. Try reloading.`)
        }
      } catch {
        if (!cancelled) setError('Could not load your workspace.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
      stopStreamRef.current?.()
      if (flushTimer.current !== null) window.clearTimeout(flushTimer.current)
    }
  }, [])

  // ── load a conversation ─────────────────────────────────────────────────
  const loadMessages = useCallback(async (conversationId: string) => {
    const loaded = await conversations.messages(conversationId)
    setMessages(loaded)

    // If the newest run is still in flight, reattach to its stream instead of
    // showing a conversation that looks frozen.
    //
    // `isRunInFlight` and not "not terminal", which is why it is worth the
    // import rather than a local guess. `NEEDS_CLARIFICATION` is deliberately
    // non-terminal on the backend — the exchange is unfinished, so `cancel`
    // still applies and the reconciler leaves it alone — but nothing more will
    // happen until the user answers. Counting it as in-flight here reattached
    // the stream to a run that immediately replayed its `RUN_FINISHED`, whose
    // `onDone` reloaded the thread and reattached again: a tight loop that
    // flickered the step trail and, because `send` refuses while `activeRunId`
    // is set, locked the composer and the option chips at exactly the moment
    // the user was being asked to reply.
    const lastRun = loaded.at(-1)?.run
    if (lastRun && isRunInFlight(lastRun.status)) {
      attachStream(lastRun.id, conversationId)
    }
    return loaded
  }, [])

  // Fetch follow-up suggestions for a thread. Best-effort — a failure just
  // leaves the row empty and never surfaces an error to the reader.
  const refreshSuggestions = useCallback(async (conversationId: string) => {
    const ticket = (suggestionTicket.current += 1)
    setSuggestionsPending(true)
    try {
      const { suggestions: next } = await conversations.suggestions(conversationId)
      if (ticket !== suggestionTicket.current) return
      setSuggestions(next)
    } catch {
      if (ticket === suggestionTicket.current) setSuggestions([])
    } finally {
      if (ticket === suggestionTicket.current) setSuggestionsPending(false)
    }
  }, [])

  /** Abandon whatever follow-ups are in flight: they belong to a past turn. */
  const dropSuggestions = useCallback(() => {
    suggestionTicket.current += 1
    setSuggestions([])
    setSuggestionsPending(false)
  }, [])

  useEffect(() => {
    // A conversation `send` just created already holds the optimistic turn and
    // owns its stream; re-loading it here would race that POST and could blank
    // the question the reader just asked.
    if (justCreatedRef.current === activeId) {
      justCreatedRef.current = null
      return
    }
    // Anything still streaming belongs to the thread being left. Left running,
    // its steps and its tokens kept arriving into whichever conversation was
    // opened next — an answer to a question that thread never asked.
    stopStreamRef.current?.()
    stopStreamRef.current = null
    streamToken.current += 1
    setActiveRunId(null)
    setStopping(false)
    setLiveSteps([])
    setLivePreview(null)
    clearText()

    if (!activeId) {
      setMessages([])
      dropSuggestions()
      return
    }
    dropSuggestions()
    loadMessages(activeId).catch(() => setError('Could not load this conversation.'))
    const conversation = conversationList.find((c) => c.id === activeId)
    if (conversation?.default_connection_id) setConnectionId(conversation.default_connection_id)
    if (conversation?.default_llm_config_id) setModelId(conversation.default_llm_config_id)
  }, [activeId, loadMessages])

  // Follow new content only when the reader is already at the end. Scrolling
  // back to re-read an earlier answer should not be yanked forward by a
  // streaming token.
  function handleScroll() {
    const el = scrollRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    followRef.current = distance < 120
    setShowJump(distance > 240)
  }

  function jumpToEnd() {
    const el = scrollRef.current
    if (!el) return
    followRef.current = true
    el.scrollTo({ top: el.scrollHeight, behavior: glideBehavior() })
  }

  // A turn arriving is worth a glide. A token is not: asking for `smooth`
  // again on every flush restarts an animation that never gets to finish, so
  // the transcript crawls along behind the text instead of staying pinned to
  // it — which is most of what made streaming feel unsteady.
  useEffect(() => {
    if (!followRef.current) return
    const el = scrollRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: glideBehavior() })
  }, [messages])

  useEffect(() => {
    if (!followRef.current) return
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [liveText, liveSteps, thinking])

  // ── streaming ───────────────────────────────────────────────────────────
  function attachStream(runId: string, conversationId: string) {
    stopStreamRef.current?.()
    const token = (streamToken.current += 1)
    setActiveRunId(runId)
    setStopping(false)
    setLiveSteps([])
    setLivePreview(null)
    clearText()
    clearThinking()

    stopStreamRef.current = streamRun(runId, {
      onEvent: (event) => {
        switch (event.type) {
          case 'STEP_STARTED':
            // A thought belongs to the step that had it. `clarify` can think
            // for half a minute and then produce no prose at all — it decides
            // the question is answerable and moves on — so the first word of
            // the answer cannot be the only thing that stops the clock, or its
            // panel would still be counting while `generate` runs.
            if (thinkingLive.current) {
              thinkingLive.current = false
              setThinking(endThought)
            }
            setLiveSteps((prev) => [
              ...prev.filter((s) => s.seq !== event.data.seq),
              {
                seq: event.data.seq,
                name: event.data.name,
                status: 'RUNNING',
                detail: null,
                duration_ms: null,
              },
            ])
            break
          case 'STEP_FINISHED':
            setLiveSteps((prev) =>
              prev.map((step) =>
                step.seq === event.data.seq
                  ? {
                      ...step,
                      status: event.data.status,
                      detail: event.data.detail ?? null,
                      duration_ms: event.data.duration_ms ?? null,
                    }
                  : step,
              ),
            )
            break
          // The model working before it has anything to say. Kept live-only
          // and paced by the server; see `ThinkingPanel`.
          case 'REASONING_DELTA':
            thinkingLive.current = true
            setThinking((prev) =>
              absorbThought(prev, event.data.text ?? '', event.data.elapsed_ms),
            )
            break
          case 'TEXT_DELTA':
            // The first word of the answer is what ends the thinking phase.
            // The panel stays — collapsed, reading "Thought for 47s" — because
            // how long the answer took to start is part of what happened.
            if (thinkingLive.current) {
              thinkingLive.current = false
              setThinking(endThought)
            }
            appendText(event.data.text ?? '')
            break
          // Narration failed part-way through: the deltas already rendered are
          // half a sentence, and the fallback that follows replaces them
          // rather than continuing them. Same path on replay, since polling
          // and SSE both land here.
          case 'TEXT_RESET':
            clearText()
            break
          // A repair re-runs the query and emits again, so the last one wins
          // — the same rule the persisted artifact follows.
          case 'RESULT_PREVIEW':
            setLivePreview(event.data as unknown as TableArtifactSpec)
            break
          default:
            break
        }
      },
      onDone: async () => {
        // Fetch first, *then* swap — in one batch, so the persisted turn
        // replaces the live one within a single paint. Clearing first and
        // loading afterwards left the answer, its steps and its table off the
        // page for the length of a round trip, which read as the whole reply
        // vanishing and then coming back.
        let loaded: MessageWithRun[] | null = null
        try {
          loaded = await conversations.messages(conversationId)
        } catch {
          /* handled below: the live turn is what the reader still has */
        }

        // Another run took the view over while this one was finishing (a
        // clarification answered fast, a thread switched). It owns the live
        // state now; ours is stale.
        if (streamToken.current !== token) return

        setActiveRunId(null)
        setStopping(false)
        setLiveSteps([])
        setLivePreview(null)
        clearText()
        clearThinking()
        if (loaded === null) {
          setError('Could not refresh this conversation. Try reloading.')
          return
        }
        setMessages(loaded)

        // The reply may itself still be in flight — a reconnect landing on a
        // run that has more to say — in which case the newest run keeps the
        // live view rather than the thread looking frozen.
        const lastRun = loaded.at(-1)?.run
        if (lastRun && isRunInFlight(lastRun.status)) {
          attachStream(lastRun.id, conversationId)
          return
        }

        // Titles and ordering move when a turn lands, but nothing on screen
        // waits for them.
        conversations.list().then(setConversationList).catch(() => {
          /* a list refresh failure is not worth an error over an answer */
        })
        // After the answer lands, offer where the reader might go next — but
        // not when the turn ended by asking a question of its own. The row is
        // hidden in that state anyway, and computing it is a model call.
        if (lastRun?.status !== 'NEEDS_CLARIFICATION') {
          void refreshSuggestions(conversationId)
        }
      },
      onError: () => {
        /* the client falls back to polling internally */
      },
    })
  }

  /**
   * *Stop.*
   *
   * The run is cancelled at the server, not merely detached here: closing the
   * stream would leave the model generating — and being billed for — an answer
   * nobody is waiting for. `RunService.cancel` writes the terminal status and
   * `cancel_requested`, which is what reaches the process actually executing;
   * the stream then closes on its own `RUN_FINISHED` and `onDone` reloads the
   * thread exactly as it does for a run that finished by itself.
   *
   * Refusing to stop is not an error worth a banner: the run reaching a
   * terminal state on its own in the same second is the common case, and the
   * reader wanted it stopped either way.
   */
  async function stopRun() {
    const runId = activeRunId
    if (!runId || stopping) return
    setStopping(true)
    try {
      await runs.cancel(runId)
    } catch {
      setStopping(false)
    }
  }

  /**
   * *Retry.*
   *
   * The same question, run again — **not** sent again. `POST /runs/{id}/retry`
   * writes a second run against the user message that is already in the
   * transcript, so the thread keeps one question where the reader asked one.
   * Re-posting the text was the obvious implementation and the wrong one: it
   * left a duplicate bubble on screen and a duplicate turn in the history
   * every later prompt is built from.
   *
   * The stopped run is dropped from its message here rather than waiting for
   * the reload, so the card the reader clicked is replaced by the live turn in
   * the same frame — the first step of a run is a model call and can take a
   * few seconds, and a screen that does not move in that gap reads as a click
   * that did nothing.
   */
  async function retryRun(run: RunDetail) {
    if (activeRunId) return
    try {
      const accepted = await runs.retry(run.id)
      setMessages((prev) =>
        prev.map((m) => (m.run?.id === run.id ? { ...m, run: null } : m)),
      )
      if (activeId) attachStream(accepted.run_id, activeId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not run that again.')
    }
  }

  // Escape stops a run from anywhere on the page, which is where a reader's
  // hand already is — the composer has the focus while an answer streams.
  //
  // Except over a dialog, where Escape already means *close this*: the
  // template editor can be open with a run still going behind it, and one key
  // press must not both shut the editor and cancel the answer.
  useEffect(() => {
    if (!activeRunId) return
    function onKey(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      if (document.querySelector('[role="dialog"]')) return
      event.preventDefault()
      void stopRunRef.current()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [activeRunId])

  // ── send ────────────────────────────────────────────────────────────────
  /** `override` lets a suggestion chip send without waiting for a state tick. */
  async function send(override?: string, options?: { skipTemplates?: boolean }) {
    const content = (override ?? draft).trim()
    if (!content || activeRunId) return

    if (!connectionId || !modelId) {
      setError('Add a data source and a model before asking a question.')
      return
    }

    setError(null)
    setDraft('')
    dropSuggestions()  // the prior turn's follow-ups no longer apply

    try {
      let conversationId = activeId
      if (!conversationId) {
        const created = await conversations.create({
          connection_id: connectionId,
          llm_config_id: modelId,
        })
        conversationId = created.id
        justCreatedRef.current = created.id
        // Replace: the empty composer this thread was started from is not a
        // screen Back should return to — it no longer exists.
        setActiveId(created.id, { replace: true })
        setConversationList((prev) => [created, ...prev])
      }

      // Optimistic user turn, so the question appears immediately.
      setMessages((prev) => [
        ...prev,
        {
          id: `pending-${Date.now()}`,
          seq: (prev.at(-1)?.seq ?? 0) + 1,
          role: 'USER',
          content,
          created_at: new Date().toISOString(),
          run: null,
        },
      ])

      const accepted = await conversations.send(conversationId, {
        content,
        connection_id: connectionId,
        llm_config_id: modelId,
        skip_templates: options?.skipTemplates,
      })
      attachStream(accepted.run_id, conversationId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send that message.')
      setDraft(content)
    }
  }

  // `send` is redefined every render, and a turn given a fresh callback is a
  // turn that re-renders on every streamed token however well it is memoised.
  // So the transcript gets this one, which never changes identity, and it
  // reads the current `send` at the moment it is actually clicked.
  const sendRef = useRef(send)
  const stopRunRef = useRef(stopRun)
  const retryRef = useRef(retryRun)
  useEffect(() => {
    sendRef.current = send
    stopRunRef.current = stopRun
    retryRef.current = retryRun
  })
  const pickOption = useCallback((text: string) => {
    void sendRef.current(text)
  }, [])
  const retry = useCallback((run: RunDetail) => {
    void retryRef.current(run)
  }, [])

  /**
   * *Generate a fresh answer instead.*
   *
   * Two steps, and the order matters. The override is recorded **first**, so
   * the measurement survives a reader who then closes the tab — and the
   * override rate is the honest number for whether the short-circuit is
   * trusted, which is what its threshold gets tuned from. Only then is the
   * question re-asked, with the store switched off for that run.
   *
   * A failed recording does not block the re-ask: the reader asked for an
   * answer, not for bookkeeping.
   */
  /**
   * *Was this right?* — recorded, then reflected back in place.
   *
   * The answer's own `knowledge.feedback` is updated locally rather than by
   * refetching the transcript: the reader pressed a button and the
   * acknowledgement has to land immediately, and a round trip through the
   * whole conversation to redraw one footer line would be the slowest possible
   * way to say "thanks".
   */
  const leaveFeedback = useCallback(
    async (run: RunDetail, verdict: string, comment: string) => {
      const given = await runs.feedback(run.id, { verdict, comment })
      setMessages((prev) =>
        prev.map((m) =>
          m.run?.id === run.id && m.run
            ? { ...m, run: { ...m.run, knowledge: { ...m.run.knowledge, feedback: given } } }
            : m,
        ),
      )
    },
    [],
  )

  /** The question behind an answer, from the turn just before it. */
  const askedFor = useCallback((run: RunDetail): string => {
    const answer = messagesRef.current.find(
      (m) => m.role === 'ASSISTANT' && m.run?.id === run.id,
    )
    const index = answer ? messagesRef.current.indexOf(answer) : -1
    return index > 0 ? (messagesRef.current[index - 1].content ?? '') : ''
  }, [])

  /**
   * *Save as a template.* One click from an answer that worked to the
   * knowledge that keeps it working.
   *
   * The editor opens prefilled with the question the reader typed and the
   * statement they just watched succeed, which is the whole reason this lives
   * on the answer rather than on the Knowledge tab: the SQL is the one they
   * saw work, and they know it did.
   */
  const teach = useCallback((run: RunDetail) => {
    const sql = run.queries.at(-1)?.raw_sql ?? ''
    if (!sql) return
    setTeaching({ question: askedFor(run), sql })
  }, [askedFor])

  /**
   * *Add to dashboard* / *Add to report.* The other half of the same idea as
   * `teach`: the answer in front of the reader already carries validated SQL
   * and a fitted chart, and until now the only way to a tile was to retype the
   * question into the tile editor and spend another model call on it.
   *
   * This only opens the picker. What travels is assembled here, once, so both
   * destinations carry identical work.
   */
  const handOff = useCallback(
    (to: 'dashboard' | 'report') => (run: RunDetail) => {
      // The last attempt is the one that passed the guard and ran; `raw_sql`
      // rather than the rewritten form, because the row cap and the rewrite
      // belong to the connection at execution time and both destinations
      // re-guard from scratch. Same statement `teach` carries.
      const sql = run.queries.at(-1)?.raw_sql ?? ''
      if (!sql) return
      setSending({ to, question: askedFor(run), sql, chartConfig: chartIntentOf(run) })
    },
    [askedFor],
  )
  const addToDashboard = useMemo(() => handOff('dashboard'), [handOff])
  const addToReport = useMemo(() => handOff('report'), [handOff])

  // How far setup has got, and whether the reader has waved the list away.
  const setup = useMemo(() => setupStateOf(connections, models), [connections, models])
  const [dismissedSetup, setDismissedSetup] = useState(
    () => localStorage.getItem(SETUP_DISMISSED) === '1',
  )
  const dismissSetup = useCallback(() => {
    localStorage.setItem(SETUP_DISMISSED, '1')
    setDismissedSetup(true)
  }, [])

  // The connection this thread is bound to, as it stands *now*. A saved thread
  // reads it from the conversation rather than from the picker, because the
  // picker holds whatever was last chosen and the thread's own binding is the
  // only thing an answer in it can be sent onward under. A deletion sets the
  // column NULL, which is why a missing row is the test.
  const threadConnection = useMemo(() => {
    const conversation = conversationList.find((c) => c.id === activeId) ?? null
    const id = conversation ? conversation.default_connection_id : connectionId
    return connections.find((c) => c.id === id) ?? null
  }, [activeId, connectionId, connections, conversationList])

  // Why an answer cannot be sent onward, in a sentence. Memoised because it is
  // handed to every memoised turn in the transcript, and a fresh object each
  // render would re-render all of them on every keystroke.
  const blocked = useMemo(() => {
    if (!threadConnection) {
      const gone = "This conversation's database was removed, so this answer cannot become a tile or a figure. The answer and its SQL stay readable."
      return { dashboard: gone, report: gone }
    }
    // A tile never sends a row to a model, so no policy rules one out. A
    // report's prose is written from the values, which is why §7 refuses the
    // two narrow policies — said here rather than at the create call, where it
    // would arrive as a 422 after the reader had already chosen a name.
    const narrow = !['SAMPLE', 'FULL'].includes(threadConnection.disclosure_policy)
    return {
      dashboard: null,
      report: narrow
        ? `Result sharing on ${threadConnection.name} is ${threadConnection.disclosure_policy}, so no values reach the model — and a report's analysis is written from them. A tile has no such limit.`
        : null,
    }
  }, [threadConnection])

  const regenerate = useCallback((run: RunDetail) => {
    const question = askedFor(run)

    void (async () => {
      try {
        const knowledge = await runs.override(run.id)
        setMessages((prev) =>
          prev.map((m) =>
            m.run?.id === run.id && m.run
              ? { ...m, run: { ...m.run, knowledge } }
              : m,
          ),
        )
      } catch {
        /* the reader asked for an answer, not for bookkeeping */
      }
      if (question) await sendRef.current(question, { skipTemplates: true })
    })()
  }, [askedFor])

  // A new chat starts empty and unbound: no database, no model, nothing
  // persisted. The conversation row is created lazily on the first send (see
  // `send`), stored with exactly the database/model pair chosen there — the
  // pair the thread then stays locked to.
  function newChat() {
    stopStreamRef.current?.()
    streamToken.current += 1
    setActiveRunId(null)
    setStopping(false)
    setLiveSteps([])
    setLivePreview(null)
    clearText()
    setActiveId(null)
    setMessages([])
    dropSuggestions()
    setConnectionId('')
    setModelId('')
    setDraft('')
    setError(null)
  }

  async function deleteConversation(id: string) {
    // Remove optimistically so the row disappears the instant it's confirmed.
    const remaining = conversationList.filter((c) => c.id !== id)
    setConversationList(remaining)
    if (activeId === id) {
      stopStreamRef.current?.()
      streamToken.current += 1
      setActiveRunId(null)
      setLiveSteps([])
      clearText()
      setMessages([])
      // Replace, for the same reason: the deleted thread's URL is not
      // somewhere Back should be able to go.
      setActiveId(remaining[0]?.id ?? null, { replace: true })
    }
    try {
      await conversations.remove(id)
    } catch (err) {
      // Put it back if the server refused, so the list stays truthful.
      setConversationList(await conversations.list().catch(() => conversationList))
      setError(err instanceof Error ? err.message : 'Could not delete that conversation.')
    }
  }

  async function renameConversation(id: string, title: string) {
    const trimmed = title.trim()
    const current = conversationList.find((c) => c.id === id)
    // A blank title or an unchanged one is not worth a request.
    if (!trimmed || trimmed === current?.title) return

    // Update optimistically; the sidebar and header both read from this list.
    setConversationList((prev) =>
      prev.map((c) => (c.id === id ? { ...c, title: trimmed } : c)),
    )
    try {
      await conversations.update(id, { title: trimmed })
    } catch (err) {
      setConversationList(await conversations.list().catch(() => conversationList))
      setError(err instanceof Error ? err.message : 'Could not rename that conversation.')
    }
  }

  if (loading) {
    return (
      <div style={{ flex: 1, display: 'grid', placeItems: 'center' }}>
        <Spinner size={20} />
      </div>
    )
  }

  const activeTitle =
    conversationList.find((c) => c.id === activeId)?.title ?? 'New chat'

  // The database and model are chosen once, before the first message, then
  // frozen: every run in a thread must stay explainable against a single pair,
  // so the pickers lock the moment the transcript is non-empty. Until both are
  // chosen, a brand-new chat cannot send.
  const locked = messages.length > 0
  const ready = Boolean(connectionId && modelId)
  const teachConnection = connections.find((c) => c.id === connectionId) ?? null
  // The last turn asked the user something rather than answering. A normal
  // state, not a failure: the thread is simply mid-exchange.
  const awaitingAnswer = messages.at(-1)?.run?.status === 'NEEDS_CLARIFICATION'

  return (
    <div style={{ display: 'flex', height: '100%', width: '100%', minWidth: 0 }}>
      <ConversationSidebar
        conversations={conversationList}
        connections={connections}
        activeId={activeId}
        onSelect={setActiveId}
        onNew={newChat}
        open={listDrawer.open}
        onDelete={deleteConversation}
        onRename={renameConversation}
      />
      <ListScrim open={listDrawer.open} onClick={listDrawer.close} />

      {/* main column */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <header
          className="rm-chat-header"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            padding: '14px 28px',
            borderBottom: '1px solid var(--border)',
            flexShrink: 0,
          }}
        >
          {/* Before the title: the way back to the list, on the left, where
              back has always been. */}
          <ListToggle open={listDrawer.open} label="Chats" onClick={listDrawer.toggle} />
          <HeaderTitle
            key={activeId ?? 'none'}
            title={activeTitle}
            editable={!!activeId}
            onRename={(title) => activeId && renameConversation(activeId, title)}
          />

          <div
            className="rm-chat-pickers"
            style={{
              marginLeft: 'auto',
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              flexShrink: 0,
            }}
          >
            <HeaderSelect
              icon={<Icon.Database size={15} stroke="var(--accent)" />}
              label="Database"
              value={connectionId}
              onChange={setConnectionId}
              options={connections.map((c) => ({ value: c.id, label: c.name }))}
              width={232}
              disabled={locked}
              emptyLabel="Connect a database…"
              onEmpty={() => navigate('/sources')}
              badge={
                <DisclosureBadge
                  policy={connections.find((c) => c.id === connectionId)?.disclosure_policy}
                />
              }
            />
            <HeaderSelect
              icon={<Icon.Sparkle size={15} stroke="var(--accent)" />}
              label="Model"
              value={modelId}
              onChange={setModelId}
              options={models.map((m) => ({ value: m.id, label: m.name }))}
              disabled={locked}
              emptyLabel="Add a model provider…"
              onEmpty={() => navigate('/providers')}
            />
          </div>
        </header>

        <div style={{ flex: 1, position: 'relative', minHeight: 0 }}>
          <div
            ref={scrollRef}
            onScroll={handleScroll}
            style={{ height: '100%', overflowY: 'auto' }}
          >
            <div
              style={{
                maxWidth: 820,
                margin: '0 auto',
                padding: '28px 28px 16px',
                display: 'flex',
                flexDirection: 'column',
                gap: 22,
              }}
            >
              {error && <ErrorNote>{error}</ErrorNote>}

              {messages.length === 0 && !activeRunId && (
                <Welcome
                  ready={ready}
                  setup={setup}
                  connections={connections}
                  dismissedSetup={dismissedSetup}
                  onDismissSetup={dismissSetup}
                  onPick={(text) => void send(text)}
                />
              )}

              {messages.map((message, index) => {
                if (message.role === 'USER') {
                  // A run that died before writing an answer has no assistant
                  // message to hang off, so the server attaches it here.
                  // Dropping it was what made a failed turn look like the
                  // question had simply vanished.
                  return (
                    <Fragment key={message.id}>
                      <UserBubble text={message.content ?? ''} />
                      {/* A run the reader stopped is not a run that broke, and
                          the red card it used to get said otherwise. */}
                      {message.run && isStopped(message.run.status) && (
                        <RunStoppedCard run={message.run} onRetry={retry} />
                      )}
                      {message.run && isFailure(message.run.status) && (
                        <RunErrorCard run={message.run} />
                      )}
                    </Fragment>
                  )
                }
                if (message.run && isStopped(message.run.status)) {
                  return (
                    <RunStoppedCard key={message.id} run={message.run} onRetry={retry} />
                  )
                }
                if (message.run && isFailure(message.run.status)) {
                  return <RunErrorCard key={message.id} run={message.run} />
                }
                return (
                  <AssistantTurn
                    key={message.id}
                    text={message.content ?? ''}
                    run={message.run}
                    // Answering a clarifying question is just the next
                    // message, so the chips send exactly what typing would.
                    onPickOption={pickOption}
                    optionsDisabled={Boolean(activeRunId)}
                    onRegenerate={regenerate}
                    onFeedback={leaveFeedback}
                    onSaveAsTemplate={teach}
                    onAddToDashboard={addToDashboard}
                    onAddToReport={addToReport}
                    blocked={blocked}
                    // The turn before this one is the question it answered —
                    // what a downloaded result should be called.
                    title={messages[index - 1]?.content ?? ''}
                  />
                )
              })}

              {/* One turn for the whole run, from the first step chip to the
                  last token. It used to be two components with a swap the
                  moment text started arriving, and the swap took the step
                  trail — route, retrieve, clarify, generate — off the screen
                  at exactly the moment the answer began writing itself. */}
              {activeRunId && (
                <AssistantTurn
                  text={liveText}
                  run={null}
                  steps={liveSteps}
                  thinking={thinking}
                  preview={livePreview}
                  streaming
                />
              )}

              {/* Not while the thread is waiting on an answer: the turn
                  already offers chips, and a second row of unrelated ones
                  reads as a choice between them. */}
              {!activeRunId && !awaitingAnswer &&
                (suggestions.length > 0 || suggestionsPending) && (
                <SuggestedFollowups
                  items={suggestions}
                  pending={suggestionsPending}
                  onPick={(text) => void send(text)}
                />
              )}
            </div>
          </div>

          {showJump && (
            <button
              onClick={jumpToEnd}
              aria-label="Jump to latest"
              title="Jump to latest"
              style={{
                position: 'absolute',
                bottom: 14,
                left: '50%',
                transform: 'translateX(-50%)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 32,
                height: 32,
                borderRadius: '50%',
                background: 'var(--panel)',
                border: '1px solid var(--border-strong)',
                color: 'var(--text-dim)',
                cursor: 'pointer',
                boxShadow: '0 4px 14px rgba(0,0,0,0.16)',
              }}
            >
              <Icon.ArrowDown size={15} />
            </button>
          )}
        </div>

        <Composer
          value={draft}
          onChange={setDraft}
          onSubmit={() => void send()}
          onStop={() => void stopRun()}
          busy={!!activeRunId}
          stopping={stopping}
          ready={ready}
        />
      </div>

      {/* The same editor the Knowledge tab uses, opened from an answer.
          Reused rather than reimplemented: the parameter proposals, the guard
          verdict and the disclosure rule for a statement's literals all have
          to be identical, and two editors would be two chances to get one of
          them wrong. */}
      {teaching && teachConnection && (
        <TemplateEditor
          connection={teachConnection}
          template={null}
          prefill={{ ...teaching, source: 'CHAT_CONFIRMED' }}
          onClose={() => setTeaching(null)}
          onSaved={() => setTeaching(null)}
        />
      )}

      {/* Pick the board, then leave for the tile editor carrying the answer.
          The editor is where a tile is given its type, its chart and its
          clock, and it opens over the grid the tile is joining — so this ends
          one screen further on rather than in a second form here. */}
      {sending?.to === 'dashboard' && (
        <AddToDashboardDialog
          question={sending.question}
          onClose={() => setSending(null)}
          onPicked={(dashboardId) => {
            setSending(null)
            navigate(`/dashboards/${dashboardId}/tiles/new`, {
              state: {
                prefill: {
                  question: sending.question,
                  sql: sending.sql,
                  connectionId: threadConnection?.id,
                  chartConfig: sending.chartConfig,
                },
              },
            })
          }}
        />
      )}

      {sending?.to === 'report' && threadConnection && (
        <AddToReportDialog
          connection={threadConnection}
          modelId={modelId || null}
          question={sending.question}
          sql={sending.sql}
          onClose={() => setSending(null)}
          onAdded={(reportId) => {
            setSending(null)
            // Into the outline, where the block now is: a figure added to a
            // document nobody is shown is indistinguishable from one that was
            // not added at all.
            navigate(`/reports/${reportId}`)
          }}
        />
      )}
    </div>
  )
}

/**
 * How far a fresh install has got, read off what is already on screen.
 *
 * No new endpoint and no extra request: the chat page fetches connections and
 * providers at boot for its own pickers, and every fact this needs is on those
 * two lists. The real order — provider, connection, schema sync, semantic
 * layer — lived only in the README, and the third step is not optional in the
 * way it looks: **an unsynced connection can answer nothing at all**, because
 * the guard resolves every name in a generated statement against the stored
 * snapshot.
 */
interface SetupState {
  provider: boolean
  connection: boolean
  synced: boolean
  /** Every step that gates asking a question is done. */
  usable: boolean
}

function setupStateOf(connections: Connection[], models: LlmConfig[]): SetupState {
  const provider = models.length > 0
  const connection = connections.length > 0
  const synced = connections.some((c) => Boolean(c.last_synced_at))
  return { provider, connection, synced, usable: provider && connection && synced }
}

/** Remembered per browser: a checklist dismissed once should stay dismissed. */
const SETUP_DISMISSED = 'raymand.setup.dismissed'

/**
 * The four steps, while any of the first three is outstanding.
 *
 * It replaces the starter questions rather than joining them: a chip that
 * cannot be clicked is worse than no chip, and until a connection is synced
 * none of them can be answered. The moment the product works, this goes and
 * the starters come back — which is also why the fourth step, the semantic
 * layer, is marked optional and does not hold the list open. It improves
 * answers; it does not gate them.
 */
function SetupChecklist({
  state, connections, onDismiss,
}: {
  state: SetupState
  connections: Connection[]
  onDismiss: () => void
}) {
  const navigate = useNavigate()
  const first = connections[0]
  const steps = [
    {
      done: state.provider,
      label: 'Add a model provider',
      hint: 'The model that writes the SQL. OpenAI-compatible or Anthropic.',
      action: 'LLM providers',
      to: '/providers',
    },
    {
      done: state.connection,
      label: 'Connect a database',
      hint: 'Read-only credentials. DataMind proves the role cannot write before it uses it.',
      action: 'Data sources',
      to: '/sources',
    },
    {
      done: state.synced,
      label: 'Sync its schema',
      hint: 'Not optional: every table and column in a generated query is resolved against this snapshot.',
      action: 'Schema',
      to: first ? `/sources/${first.id}/schema` : '/sources',
    },
    {
      done: false,
      optional: true,
      label: 'Describe what it means',
      hint: 'A semantic layer — grain, metrics, time conventions. Better answers, not a requirement.',
      action: 'Semantic layer',
      to: first ? `/sources/${first.id}/semantic` : '/sources',
    },
  ]

  return (
    <div
      style={{
        width: '100%',
        maxWidth: 520,
        marginTop: 6,
        padding: '14px 16px 12px',
        textAlign: 'left',
        background: 'var(--panel)',
        border: '1px solid var(--border)',
        borderRadius: 12,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-strong)' }}>
          Four steps to your first answer
        </span>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss the setup checklist"
          title="Dismiss"
          className="rm-icon-btn"
          style={{
            marginLeft: 'auto',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 24,
            height: 24,
            borderRadius: 6,
            border: 'none',
            background: 'transparent',
            color: 'var(--text-faint)',
            cursor: 'pointer',
            ['--rm-hover-bg' as string]: 'var(--panel-alt)',
          }}
        >
          <Icon.Close size={13} />
        </button>
      </div>

      <ol style={{ display: 'flex', flexDirection: 'column', gap: 9, margin: 0, padding: 0, listStyle: 'none' }}>
        {steps.map((step, index) => (
          <li key={step.label} style={{ display: 'flex', alignItems: 'flex-start', gap: 9 }}>
            {/* Done is a tick; not-done is its number, so the list reads as an
                order of work rather than a set of empty boxes. */}
            <span
              aria-hidden
              style={{
                display: 'grid',
                placeItems: 'center',
                width: 18,
                height: 18,
                flexShrink: 0,
                marginTop: 1,
                borderRadius: 999,
                fontSize: 10.5,
                fontWeight: 700,
                background: step.done ? 'var(--green-bg)' : 'var(--panel-alt)',
                border: `1px solid ${step.done ? 'var(--green-border)' : 'var(--border)'}`,
                color: step.done ? 'var(--green)' : 'var(--text-faint)',
              }}
            >
              {step.done ? <Icon.Check size={11} /> : index + 1}
            </span>
            <span style={{ flex: 1, minWidth: 0 }}>
              <span
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  fontSize: 12.5,
                  fontWeight: 600,
                  color: step.done ? 'var(--text-dim)' : 'var(--text-strong)',
                }}
              >
                {step.label}
                {step.optional && (
                  <span style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--text-faint)' }}>
                    optional
                  </span>
                )}
                <span className="rm-sr">{step.done ? ' — done' : ' — still to do'}</span>
              </span>
              <span style={{ display: 'block', fontSize: 11.5, color: 'var(--text-faint)', lineHeight: 1.5 }}>
                {step.hint}
              </span>
            </span>
            {/* Drawn as a link rather than a quiet action: the finding this
                list answers is that the empty pickers said "None configured"
                and *were not links*, and an action faint until hovered would
                repeat it for anyone who never hovers. */}
            {!step.done && (
              <button
                type="button"
                onClick={() => navigate(step.to)}
                className="rm-step-go"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 3,
                  flexShrink: 0,
                  padding: '2px 0 2px 8px',
                  fontSize: 12,
                  fontWeight: 600,
                  fontFamily: 'inherit',
                  color: 'var(--accent)',
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                }}
              >
                {step.action}
                <Icon.ArrowRight size={12} />
              </button>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}

/**
 * The opening screen. An empty transcript is the worst place to be told only
 * what the product does, so it also offers questions that are safe to ask of
 * any schema — the first one routes as METADATA and never touches SQL.
 */
const STARTERS = [
  'What tables do I have?',
  'Which tables can I join together?',
  'How many records are in each table?',
  'Show me a sample of rows',
]

function Welcome({
  ready, setup, connections, dismissedSetup, onDismissSetup, onPick,
}: {
  ready: boolean
  setup: SetupState
  connections: Connection[]
  dismissedSetup: boolean
  onDismissSetup: () => void
  onPick: (text: string) => void
}) {
  // The checklist replaces the starters while the product cannot answer
  // anything, and gives them back the moment it can.
  const showChecklist = !setup.usable && !dismissedSetup
  return (
    <div
      className="rm-welcome"
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 14,
        padding: '56px 16px 24px',
        textAlign: 'center',
      }}
    >
      <span
        className="rm-welcome-badge"
        style={{
          width: 46,
          height: 46,
          borderRadius: 13,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--accent-bg)',
          border: '1px solid var(--accent-border)',
        }}
      >
        <Icon.Sparkle size={22} stroke="var(--accent)" />
      </span>

      <div style={{ fontSize: 19, fontWeight: 700, color: 'var(--text-strong)' }}>
        Ask a question about your data
      </div>
      <p
        style={{
          fontSize: 13.5,
          color: 'var(--text-dim)',
          maxWidth: 440,
          lineHeight: 1.6,
          margin: 0,
        }}
      >
        DataMind writes the SQL, checks it against your schema, runs it on a
        read-only connection, and shows you exactly what it did.
      </p>

      {showChecklist ? (
        <SetupChecklist
          state={setup}
          connections={connections}
          onDismiss={onDismissSetup}
        />
      ) : (
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 8,
            justifyContent: 'center',
            marginTop: 6,
          }}
        >
          {STARTERS.map((text) => (
            <StarterChip
              key={text}
              text={text}
              disabled={!ready}
              onClick={() => onPick(text)}
            />
          ))}
        </div>
      )}

      {!ready && !showChecklist && (
        <div style={{ fontSize: 12, color: 'var(--text-faint)', marginTop: 2 }}>
          Choose a database and model in the header to begin.
        </div>
      )}
    </div>
  )
}

function StarterChip({
  text, onClick, disabled = false,
}: {
  text: string
  onClick: () => void
  disabled?: boolean
}) {
  const [hover, setHover] = useState(false)
  const lit = hover && !disabled
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        fontSize: 12.5,
        fontWeight: 500,
        color: lit ? 'var(--text-strong)' : 'var(--text-dim)',
        background: lit ? 'var(--panel-hover)' : 'var(--panel)',
        border: `1px solid ${lit ? 'var(--accent-border)' : 'var(--border)'}`,
        padding: '8px 13px',
        borderRadius: 20,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transition: 'background .12s ease, border-color .12s ease, color .12s ease',
      }}
    >
      {text}
    </button>
  )
}

/**
 * Model-proposed next questions, shown under a finished answer. They line up
 * with the assistant's content column (past the avatar gutter) so they read as
 * a continuation of the thread rather than a new element. Each chip sends its
 * question directly, reusing the starter-chip affordance for consistency.
 */
function SuggestedFollowups({
  items, pending, onPick,
}: {
  items: string[]
  /** The request is out. It is a full completion on the connection's model and
   *  runs long enough that an empty row would read as "there are none". */
  pending: boolean
  onPick: (text: string) => void
}) {
  return (
    <div
      className="rm-enter"
      style={{
        marginLeft: 43,
        maxWidth: 737,
        display: 'flex',
        flexDirection: 'column',
        gap: 9,
      }}
    >
      <div
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 11,
          fontWeight: 600,
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
          color: 'var(--text-faint)',
        }}
      >
        <Icon.Sparkle size={12} stroke="var(--accent)" />
        Suggested follow-ups
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {items.length > 0
          ? items.map((text) => (
              <StarterChip key={text} text={text} onClick={() => onPick(text)} />
            ))
          : pending && <SuggestionSkeleton />}
      </div>
    </div>
  )
}

/** Three chip-shaped placeholders, sized so the row does not jump when the
 *  real questions replace them.
 *
 *  They are painted in `--panel-alt` over `--border`, which are tokens that
 *  exist. The first version asked for `--surface-2` and `--border-subtle`,
 *  which do not: three fully transparent boxes under a heading that says
 *  "Suggested follow-ups", for the twelve to eighteen seconds that call takes.
 *  The row read as empty at exactly the moment it was there to say "not yet". */
function SuggestionSkeleton() {
  return (
    <>
      {[168, 208, 144].map((width) => (
        <div
          key={width}
          className="rm-pulse"
          style={{
            width,
            height: 32,
            borderRadius: 16,
            border: '1px solid var(--border)',
            background: 'var(--panel-alt)',
          }}
        />
      ))}
    </>
  )
}

/**
 * The conversation title in the chat header, editable in place. A pencil
 * appears on hover and a double-click on the title opens the same editor, so
 * a chat can be renamed from where the eye already is rather than only from
 * the sidebar. Non-editable before the first conversation exists.
 *
 * It is also Chat's one <h1>: the thread on screen is what this page is about.
 * The heading survives the rename editor — while the field carries the draft
 * the heading goes visually hidden rather than away, because a page that has
 * no heading for as long as a field has focus is a page with no heading, and
 * an <h1> wrapped around a text field is a heading with no text.
 */
function HeaderTitle({
  title, editable, onRename,
}: {
  title: string
  editable: boolean
  onRename: (title: string) => void
}) {
  const [hover, setHover] = useState(false)
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(title)
  const inputRef = useRef<HTMLInputElement>(null)

  function startEdit() {
    if (!editable) return
    setValue(title)
    setEditing(true)
  }

  function commit() {
    setEditing(false)
    onRename(value)
  }

  useEffect(() => {
    if (editing) inputRef.current?.select()
  }, [editing])

  if (editing) {
    return (
      <>
        <h1 className="rm-sr">{title}</h1>
        <input
          ref={inputRef}
          value={value}
          dir={dirOf(value)}
          onChange={(e) => setValue(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              commit()
            } else if (e.key === 'Escape') {
              e.preventDefault()
              setEditing(false)
            }
          }}
          style={{
            minWidth: 0,
            maxWidth: 360,
            padding: '5px 9px',
            fontSize: 14,
            fontWeight: 600,
            color: 'var(--text-strong)',
            background: 'var(--input-bg)',
            border: '1px solid var(--accent)',
            borderRadius: 7,
            outline: 'none',
          }}
        />
      </>
    )
  }

  return (
    <h1
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}
    >
      <div
        dir={dirOf(title)}
        onDoubleClick={startEdit}
        title={editable ? 'Double-click to rename' : undefined}
        style={{
          fontSize: 14,
          fontWeight: 600,
          color: 'var(--text-strong)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          minWidth: 0,
          cursor: editable ? 'text' : 'default',
        }}
      >
        {title}
      </div>
      {editable && (
        <button
          className="rm-icon-btn"
          onClick={startEdit}
          title="Rename conversation"
          aria-label="Rename conversation"
          style={{
            ...iconBtnStyle('var(--text-faint)', 'var(--panel-alt)'),
            visibility: hover ? 'visible' : 'hidden',
          }}
        >
          <Icon.Pencil size={13} stroke="var(--text-faint)" />
        </button>
      )}
    </h1>
  )
}

// ── the conversation list ───────────────────────────────────────────────────
/**
 * The threads, newest first, cut into the periods people actually think in.
 *
 * A chat list is unlike the other indexes in the product in one way that
 * decides its shape: nobody remembers what they called a thread, they remember
 * *when* they had it. So the ordering is recency and the grouping is recency.
 * The rest is the furniture every other index here has and this one lacked: a
 * heading with a count, a filter once the list is long enough to need one, and
 * something to read when it is empty.
 *
 * A row is one line and that line is the question — see `ConversationItem`.
 */
const DAY = 86_400_000

function bucketOf(iso: string, now: number): string {
  const age = now - new Date(iso).getTime()
  if (age < DAY) return 'Today'
  if (age < 7 * DAY) return 'Previous 7 days'
  if (age < 30 * DAY) return 'Previous 30 days'
  return 'Older'
}

const BUCKETS = ['Today', 'Previous 7 days', 'Previous 30 days', 'Older']

function ConversationSidebar({
  conversations: list, connections, activeId, open, onSelect, onNew, onDelete,
  onRename,
}: {
  conversations: ConversationSummary[]
  /** To name the data source each thread is bound to, on its row. */
  connections: Connection[]
  activeId: string | null
  /** Below 700px this list is an overlay — see `list-drawer.tsx`. */
  open?: boolean
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
  onRename: (id: string, title: string) => void
}) {
  const [query, setQuery] = useState('')

  const byId = useMemo(
    () => new Map(connections.map((connection) => [connection.id, connection])),
    [connections],
  )

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase()
    // Over what the rows actually show: the title and the source name. It used
    // to search the preview line too, and once that line stopped being drawn a
    // match on it would have been a row that appeared for no visible reason.
    const matched = list.filter((conversation) => {
      if (!needle) return true
      if (conversation.title.toLowerCase().includes(needle)) return true
      const source = byId.get(conversation.default_connection_id ?? '')
      return source !== undefined && source.name.toLowerCase().includes(needle)
    })
    const now = Date.now()
    const sorted = [...matched].sort(
      (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    )
    return BUCKETS.map((label) => ({
      label,
      items: sorted.filter((conversation) => bucketOf(conversation.updated_at, now) === label),
    })).filter((group) => group.items.length > 0)
  }, [list, byId, query])

  return (
    <aside
      id={LIST_DRAWER_ID}
      className={`rm-chats${open ? ' is-open' : ''}`}
      style={{
        width: 252,
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        background: 'var(--sidebar-bg)',
        borderRight: '1px solid var(--border)',
      }}
    >
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
          padding: '16px 12px 12px',
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '0 2px' }}>
          <span style={{ fontSize: 14.5, fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--text-strong)' }}>
            Chats
          </span>
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: 'var(--text-faint)',
              background: 'var(--panel-alt)',
              padding: '2px 7px',
              borderRadius: 20,
            }}
          >
            {list.length}
          </span>
        </div>

        <ListNewButton label="New chat" onClick={onNew} />

        {/* Offered once the list outgrows a glance — the same rule the
            Dashboards toolbar follows for its archived filter. */}
        {list.length > 7 && (
          <div className="rm-chats-search">
            <SearchField
              value={query}
              onChange={setQuery}
              ariaLabel="Search chats"
              placeholder="Search chats…"
            />
          </div>
        )}
      </div>

      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '0 10px 16px',
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
        }}
      >
        {list.length === 0 ? (
          <p style={{ fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-dim)', padding: '4px 6px', margin: 0 }}>
            No chats yet. Ask a question in plain language and DataMind writes
            the SQL, runs it read-only, and shows you both.
          </p>
        ) : groups.length === 0 ? (
          <p style={{ fontSize: 12.5, color: 'var(--text-dim)', padding: '4px 6px', margin: 0 }}>
            Nothing matches “{query.trim()}”.
          </p>
        ) : (
          groups.map((group) => (
            <div key={group.label} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span className="rm-chats-caption">{group.label}</span>
              {group.items.map((conversation) => (
                <ConversationItem
                  key={conversation.id}
                  conversation={conversation}
                  connection={byId.get(conversation.default_connection_id ?? '') ?? null}
                  active={conversation.id === activeId}
                  onSelect={() => onSelect(conversation.id)}
                  onDelete={() => onDelete(conversation.id)}
                  onRename={(title) => onRename(conversation.id, title)}
                />
              ))}
            </div>
          ))
        )}
      </div>
    </aside>
  )
}

function ConversationItem({
  conversation, connection, active, onSelect, onDelete, onRename,
}: {
  conversation: ConversationSummary
  /** The data source the thread is bound to, resolved by the list. */
  connection: Connection | null
  active: boolean
  onSelect: () => void
  onDelete: () => void
  onRename: (title: string) => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(conversation.title)
  const inputRef = useRef<HTMLInputElement>(null)

  function startEdit() {
    setValue(conversation.title)
    setEditing(true)
  }

  function commit() {
    setEditing(false)
    onRename(value)
  }

  // Select the whole title on entry, so a rename can start by just typing.
  useEffect(() => {
    if (editing) inputRef.current?.select()
  }, [editing])

  return (
    <div
      onMouseLeave={() => setConfirming(false)}
      className={`rm-chat-item${active ? ' is-on' : ''}`}
    >
      {editing ? (
        <input
          ref={inputRef}
          value={value}
          dir={dirOf(value)}
          onChange={(e) => setValue(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              commit()
            } else if (e.key === 'Escape') {
              e.preventDefault()
              setEditing(false)
            }
          }}
          style={{
            flex: 1,
            minWidth: 0,
            padding: '3px 7px',
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--text-strong)',
            background: 'var(--input-bg)',
            border: '1px solid var(--accent)',
            borderRadius: 6,
            outline: 'none',
          }}
        />
      ) : (
        // The question, and the database it was asked of.
        //
        // The row used to carry an initial badge and a line of the answer's
        // opening words. Both are gone: the badge repeated the letter the
        // title started with two characters to its right, and the preview was
        // a sentence fragment cut mid-word, so twenty of them stacked read as
        // noise. Title alone was worse in the other direction — a column of
        // bare sentences reads as prose, not as a list of things you can open.
        //
        // So the second line is an *attribute* rather than a sentence: which
        // data source the thread is bound to. A thread is pinned to one
        // connection for its whole life (`_bind_connection`), so it is a fact
        // about the conversation and not a detail of its last turn — and it is
        // the thing you actually need when two threads ask the same question
        // of staging and of production. The engine-tinted glyph says the same
        // thing at a glance, in the colour the Data sources index uses.
        <button
          onClick={onSelect}
          onDoubleClick={startEdit}
          title={conversation.title}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 9,
            flex: 1,
            minWidth: 0,
            padding: 0,
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          <GlyphBadge
            hue={connection ? engineHue(connection.database_type) : undefined}
            size={26}
            radius={8}
          >
            <Icon.Database size={13} />
          </GlyphBadge>
          <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, gap: 1 }}>
            <span
              dir={dirOf(conversation.title)}
              style={{
                fontSize: 12.5,
                fontWeight: active ? 600 : 500,
                lineHeight: 1.3,
                color: active ? 'var(--text-strong)' : 'var(--text)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {conversation.title}
            </span>
            <span
              style={{
                fontSize: 10.5,
                lineHeight: 1.3,
                color: 'var(--text-faint)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {connection?.name ?? 'No data source'}
            </span>
          </span>
        </button>
      )}

      {editing ? null : confirming ? (
        // Same floating cluster, pinned open: a confirmation that moved the
        // title out from under the pointer would be answering a different
        // question than the one it asked.
        <span
          className="rm-chat-actions is-open"
          style={{ display: 'flex', alignItems: 'center', gap: 2, flexShrink: 0 }}
        >
          <button
            className="rm-icon-btn"
            onClick={onDelete}
            title="Confirm delete"
            aria-label="Confirm delete"
            style={iconBtnStyle('var(--red)', 'var(--red-bg)')}
          >
            <Icon.Check size={13} stroke="var(--red)" />
          </button>
          <button
            className="rm-icon-btn"
            onClick={() => setConfirming(false)}
            title="Cancel"
            aria-label="Cancel delete"
            style={iconBtnStyle('var(--text-dim)', 'var(--panel-alt)')}
          >
            <Icon.Close size={12} stroke="var(--text-dim)" />
          </button>
        </span>
      ) : (
        // Revealed on approach — and kept for the keyboard, which never
        // produces a hover, by `:focus-within` in the stylesheet.
        <span
          className="rm-chat-actions"
          style={{ display: 'flex', alignItems: 'center', gap: 2, flexShrink: 0 }}
        >
          <button
            className="rm-icon-btn"
            onClick={startEdit}
            title="Rename conversation"
            aria-label="Rename conversation"
            style={iconBtnStyle('var(--text-faint)', 'var(--panel-alt)')}
          >
            <Icon.Pencil size={13} stroke="var(--text-faint)" />
          </button>
          <button
            className="rm-icon-btn"
            onClick={() => setConfirming(true)}
            title="Delete conversation"
            aria-label="Delete conversation"
            style={iconBtnStyle('var(--text-faint)', 'var(--panel-alt)')}
          >
            <Icon.Trash size={13} stroke="var(--text-faint)" />
          </button>
        </span>
      )}
    </div>
  )
}

// `--rm-hover-bg` is picked up by the `.rm-icon-btn:hover` rule in styles.css.
function iconBtnStyle(color: string, hoverBg: string): React.CSSProperties {
  return {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 24,
    height: 24,
    borderRadius: 6,
    border: 'none',
    background: 'transparent',
    color,
    cursor: 'pointer',
    flexShrink: 0,
    transition: 'background .1s ease',
    ...({ '--rm-hover-bg': hoverBg } as React.CSSProperties),
  }
}

function Composer({
  value, onChange, onSubmit, onStop, busy, stopping, ready,
}: {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  /** Cancel the run in flight. The same button, while there is one. */
  onStop: () => void
  busy: boolean
  /** A stop already asked for and not yet landed. */
  stopping: boolean
  /** Both a database and a model are chosen — required before a first send. */
  ready: boolean
}) {
  const [focus, setFocus] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)

  // Grow the textarea to fit its content, up to a cap, then scroll.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [value])

  const canSend = value.trim().length > 0 && !busy && ready
  const active = focus || value.trim().length > 0
  // One control, two jobs — send while there is nothing running, stop while
  // there is. It is the same button because it is the same question ("what do
  // I do about this answer?"), and because a second button that is disabled
  // half the time is a second thing to look at every time it is not.
  const canStop = busy && !stopping

  return (
    <div style={{ padding: '10px 28px 20px', flexShrink: 0 }}>
      <div
        className={`rm-composer${active ? ' is-active' : ''}${busy ? ' is-busy' : ''}`}
        style={{ maxWidth: 780, margin: '0 auto' }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-end',
            gap: 10,
            background: 'var(--panel)',
            border: `1px solid ${focus ? 'var(--accent)' : 'var(--border-strong)'}`,
            borderRadius: 22,
            padding: '10px 10px 10px 18px',
            boxShadow: focus
              ? '0 0 0 4px var(--accent-bg), 0 12px 34px -12px rgba(0,0,0,0.28)'
              : '0 2px 12px -4px rgba(0,0,0,0.14)',
            transition: 'border-color .18s ease, box-shadow .18s ease, transform .18s ease',
            transform: focus ? 'translateY(-1px)' : 'none',
          }}
        >
          <textarea
            ref={ref}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onFocus={() => setFocus(true)}
            onBlur={() => setFocus(false)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                if (canSend) onSubmit()
              }
            }}
            rows={1}
            dir={dirOf(value)}
            placeholder="Ask anything about your data…"
            aria-label="Ask about your data"
            style={{
              flex: 1,
              resize: 'none',
              maxHeight: 160,
              background: 'transparent',
              border: 'none',
              outline: 'none',
              color: 'var(--text)',
              fontSize: 14.5,
              lineHeight: 1.6,
              padding: '5px 0',
            }}
          />
          {/*
            One slot, two controls, one disc — the arrow becomes a square while
            a run is in flight, which is the convention every chat product has
            settled on and therefore the one people arrive already knowing.
            It was a labelled pill for a while, on the worry that a 13px glyph
            swap in the same circle is too quiet to notice. Three things carry
            that signal without a word: the square is now a third of the disc
            rather than a detail in the middle of it, the accent ring around
            the button breathes for as long as the run lasts, and the hint
            line directly underneath says `Esc` to stop. The word inside the
            button was the fourth telling of the same thing — and the only one
            that cost a control its shape.
          */}
          <button
            className={`rm-send-btn${canStop ? ' is-running' : ''}`}
            onClick={() => (busy ? canStop && onStop() : canSend && onSubmit())}
            disabled={busy ? !canStop : !canSend}
            aria-label={busy ? 'Stop generating' : 'Send'}
            title={busy ? 'Stop generating  ·  Esc' : undefined}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              height: 38,
              width: 38,
              padding: 0,
              borderRadius: 999,
              border: busy ? '1px solid var(--border-strong)' : 'none',
              flexShrink: 0,
              // Stopping is not the accent action — it undoes one — so the
              // disc goes neutral and lets the answer above it keep the only
              // accent on the screen. The ring around it stays accent: that
              // is the run, not the button.
              background: busy
                ? 'var(--panel-alt)'
                : canSend
                  ? 'linear-gradient(150deg, color-mix(in oklch, var(--accent) 88%, white), var(--accent))'
                  : 'var(--panel-alt)',
              color: busy
                ? stopping ? 'var(--text-dim)' : 'var(--text-strong)'
                : canSend ? 'var(--on-accent)' : 'var(--text-faint)',
              cursor: busy
                ? canStop ? 'pointer' : 'default'
                : canSend ? 'pointer' : 'not-allowed',
              transition: 'background .15s ease, color .15s ease',
            }}
          >
            {busy ? (
              // 24 draws a 12px square inside a 38px disc — the same third of
              // the button the products this borrows from use, and enough to
              // read as a shape rather than as a dot.
              stopping ? <Spinner size={15} /> : <Icon.Stop size={24} />
            ) : (
              <Icon.Send size={16} />
            )}
          </button>
        </div>

        <div
          className="rm-composer-hint"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
            marginTop: 8,
            fontSize: 11,
            color: 'var(--text-faint)',
          }}
        >
          {busy ? (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              {stopping ? (
                'Stopping…'
              ) : (
                <>
                  <span className="rm-kbd">Esc</span> to stop
                </>
              )}
            </span>
          ) : ready ? (
            <>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                <span className="rm-kbd">Enter</span> to send
              </span>
              <span style={{ opacity: 0.5 }}>·</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                <span className="rm-kbd">Shift</span>
                <span className="rm-kbd">Enter</span> for a new line
              </span>
            </>
          ) : (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              <Icon.Sparkle size={12} stroke="var(--text-faint)" />
              Choose a database and model above to start
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * A themed dropdown for the header's Database and Model pickers.
 *
 * The native <select> it replaces sized itself to its content, so the two
 * boxes jumped width on every change, and its popup ignored the app's dark
 * theme. This keeps a fixed trigger width, a chevron affordance, a menu that
 * follows the tokens, and a check on the current choice. Closes on an outside
 * click or Escape.
 */
function HeaderSelect({
  icon, label, value, onChange, options, badge, width = 190, disabled = false,
  emptyLabel, onEmpty,
}: {
  icon: React.ReactNode
  label: string
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
  /** Optional status pill fused into the trigger, e.g. the disclosure badge. */
  badge?: React.ReactNode
  width?: number
  /** Read-only: once a thread has started, its database/model can't change. */
  disabled?: boolean
  /** What to offer when there is nothing to choose — "Add a database…". */
  emptyLabel?: string
  onEmpty?: () => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  const selected = options.find((o) => o.value === value)
  // Distinguish "nothing chosen yet" (there are options, pick one) from
  // "nothing to choose" (none configured on the settings page).
  const placeholder =
    options.length === 0 ? 'None configured' : `Choose a ${label.toLowerCase()}`
  const display = selected?.label ?? placeholder

  useEffect(() => {
    if (!open) return
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    // `rm-picker`: below 700px the header wraps onto its own line and the two
    // pickers share it, so the fixed trigger width — which exists to stop them
    // jumping on every change — has to give way to an equal share.
    <div ref={ref} className="rm-picker" style={{ position: 'relative', flexShrink: 0 }}>
      <button
        onClick={() => !disabled && setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={
          disabled
            ? `${label} is fixed for this conversation: ${display}`
            : `${label}: ${display}`
        }
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          width,
          background: 'var(--panel)',
          border: `1px solid ${open ? 'var(--accent)' : 'var(--border-strong)'}`,
          borderRadius: 9,
          padding: '6px 10px',
          cursor: disabled ? 'default' : 'pointer',
          opacity: disabled ? 0.7 : 1,
          textAlign: 'left',
          transition: 'border-color .12s ease, opacity .12s ease',
        }}
      >
        {icon}
        <span
          style={{
            display: 'flex',
            flexDirection: 'column',
            lineHeight: 1.15,
            minWidth: 0,
            flex: 1,
          }}
        >
          <span
            style={{
              fontSize: 9.5,
              color: 'var(--text-faint)',
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.04em',
            }}
          >
            {label}
          </span>
          <span
            style={{
              fontSize: 12.5,
              fontWeight: 600,
              color: selected ? 'var(--text-strong)' : 'var(--text-faint)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {display}
          </span>
        </span>
        {badge}
        {disabled ? (
          <Icon.Lock size={12} stroke="var(--text-faint)" />
        ) : (
          <Icon.Chevron open={open} size={13} stroke="var(--text-faint)" />
        )}
      </button>

      {open && !disabled && (
        <div
          role="listbox"
          style={{
            position: 'absolute',
            top: 'calc(100% + 6px)',
            right: 0,
            minWidth: '100%',
            maxWidth: 280,
            maxHeight: 320,
            overflowY: 'auto',
            background: 'var(--panel)',
            border: '1px solid var(--border-strong)',
            borderRadius: 10,
            padding: 5,
            boxShadow: '0 10px 30px rgba(0,0,0,0.22)',
            zIndex: 50,
          }}
        >
          {options.length === 0 ? (
            /* An empty menu used to say "None configured" and stop there — a
               dead end at the exact moment a new user needs a door. The row
               that says nothing exists is now the row that goes and makes
               one. */
            onEmpty ? (
              <button
                type="button"
                className="rm-menu-item"
                onClick={() => {
                  setOpen(false)
                  onEmpty()
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  width: '100%',
                  padding: '9px 10px',
                  borderRadius: 7,
                  border: 'none',
                  background: 'transparent',
                  color: 'var(--accent)',
                  fontSize: 12.5,
                  fontWeight: 600,
                  cursor: 'pointer',
                  textAlign: 'left',
                }}
              >
                <Icon.Plus size={13} />
                {emptyLabel ?? 'Set one up'}
              </button>
            ) : (
              <div
                style={{
                  fontSize: 12.5,
                  color: 'var(--text-faint)',
                  padding: '9px 10px',
                }}
              >
                None configured
              </div>
            )
          ) : (
            options.map((option) => {
              const active = option.value === value
              return (
                <button
                  key={option.value}
                  role="option"
                  aria-selected={active}
                  className={active ? undefined : 'rm-menu-item'}
                  onClick={() => {
                    onChange(option.value)
                    setOpen(false)
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    width: '100%',
                    padding: '8px 10px',
                    borderRadius: 7,
                    border: 'none',
                    background: active ? 'var(--accent-bg)' : 'transparent',
                    color: active ? 'var(--text-strong)' : 'var(--text)',
                    fontSize: 12.5,
                    fontWeight: active ? 600 : 500,
                    cursor: 'pointer',
                    textAlign: 'left',
                  }}
                >
                  <span
                    style={{
                      flex: 1,
                      minWidth: 0,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {option.label}
                  </span>
                  {active && <Icon.Check size={14} stroke="var(--accent)" />}
                </button>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}

/**
 * If result rows leave the customer's database for a third-party API, the
 * person asking should see that at the moment they ask, not by reading docs.
 *
 * The policy is a property of the *connection*, so this renders as a compact
 * pill fused into the Database picker (see HeaderSelect's `badge` prop) rather
 * than floating beside the model. Dot + one word carries the state at a glance;
 * the full sentence lives in the tooltip.
 */
/** Terminal states that owe the reader an explanation. */
function isFailure(status: string): boolean {
  return ['FAILED', 'TIMED_OUT'].includes(status)
}

/** The one terminal state the reader chose. It owes them nothing but the
 *  trail of what it had managed before they said stop. */
function isStopped(status: string): boolean {
  return status === 'CANCELLED'
}
