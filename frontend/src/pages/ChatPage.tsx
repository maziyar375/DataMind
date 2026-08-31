import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  conversations, connections as connectionsApi, llmConfigs,
  isRunInFlight, runs, streamRun,
} from '../api/client'
import type {
  Connection, ConversationSummary, LlmConfig, MessageWithRun, RunDetail, RunStep,
} from '../api/types'
import {
  AssistantTurn, RunErrorCard, UserBubble,
} from '../components/chat'
import {
  DisclosureBadge, ErrorNote, GlyphBadge, Icon, PrimaryButton, SearchField, Spinner,
  dirOf, engineHue,
} from '../components/ui'

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

export default function ChatPage() {
  const [conversationList, setConversationList] = useState<ConversationSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<MessageWithRun[]>([])
  // Read by `regenerate`, which is given a stable identity so a transcript of
  // memoised turns does not re-render on every streamed token. It needs the
  // *current* transcript to find the question behind an answer, and a
  // dependency array would defeat the point.
  const messagesRef = useRef<MessageWithRun[]>([])
  useEffect(() => {
    messagesRef.current = messages
  }, [messages])
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
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
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
          llmConfigs.list(),
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
    setLiveSteps([])
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
  }, [liveText, liveSteps])

  // ── streaming ───────────────────────────────────────────────────────────
  function attachStream(runId: string, conversationId: string) {
    stopStreamRef.current?.()
    const token = (streamToken.current += 1)
    setActiveRunId(runId)
    setLiveSteps([])
    clearText()

    stopStreamRef.current = streamRun(runId, {
      onEvent: (event) => {
        switch (event.type) {
          case 'STEP_STARTED':
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
          case 'TEXT_DELTA':
            appendText(event.data.text ?? '')
            break
          // Narration failed part-way through: the deltas already rendered are
          // half a sentence, and the fallback that follows replaces them
          // rather than continuing them. Same path on replay, since polling
          // and SSE both land here.
          case 'TEXT_RESET':
            clearText()
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
        setLiveSteps([])
        clearText()
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
        setActiveId(created.id)
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
  useEffect(() => {
    sendRef.current = send
  })
  const pickOption = useCallback((text: string) => {
    void sendRef.current(text)
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
  const regenerate = useCallback((run: RunDetail) => {
    const asked = messagesRef.current.find(
      (m) => m.role === 'ASSISTANT' && m.run?.id === run.id,
    )
    const index = asked ? messagesRef.current.indexOf(asked) : -1
    const question = index > 0 ? messagesRef.current[index - 1].content : null

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
  }, [])

  // A new chat starts empty and unbound: no database, no model, nothing
  // persisted. The conversation row is created lazily on the first send (see
  // `send`), stored with exactly the database/model pair chosen there — the
  // pair the thread then stays locked to.
  function newChat() {
    stopStreamRef.current?.()
    streamToken.current += 1
    setActiveRunId(null)
    setLiveSteps([])
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
      setActiveId(remaining[0]?.id ?? null)
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
        onDelete={deleteConversation}
        onRename={renameConversation}
      />

      {/* main column */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <header
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            padding: '14px 28px',
            borderBottom: '1px solid var(--border)',
            flexShrink: 0,
          }}
        >
          <HeaderTitle
            key={activeId ?? 'none'}
            title={activeTitle}
            editable={!!activeId}
            onRename={(title) => activeId && renameConversation(activeId, title)}
          />

          <div
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
                <Welcome ready={ready} onPick={(text) => void send(text)} />
              )}

              {messages.map((message) => {
                if (message.role === 'USER') {
                  // A run that died before writing an answer has no assistant
                  // message to hang off, so the server attaches it here.
                  // Dropping it was what made a failed turn look like the
                  // question had simply vanished.
                  return (
                    <Fragment key={message.id}>
                      <UserBubble text={message.content ?? ''} />
                      {message.run && isFailure(message.run.status) && (
                        <RunErrorCard run={message.run} />
                      )}
                    </Fragment>
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
          busy={!!activeRunId}
          ready={ready}
        />
      </div>
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

function Welcome({ ready, onPick }: { ready: boolean; onPick: (text: string) => void }) {
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

      {!ready && (
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
    )
  }

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}
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
    </div>
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
  conversations: list, connections, activeId, onSelect, onNew, onDelete, onRename,
}: {
  conversations: ConversationSummary[]
  /** To name the data source each thread is bound to, on its row. */
  connections: Connection[]
  activeId: string | null
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
      className="rm-chats"
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

        <PrimaryButton onClick={onNew} style={{ width: '100%', borderRadius: 9 }}>
          <Icon.Plus size={15} />
          New chat
        </PrimaryButton>

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
  value, onChange, onSubmit, busy, ready,
}: {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  busy: boolean
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

  return (
    <div style={{ padding: '10px 28px 20px', flexShrink: 0 }}>
      <div
        className={`rm-composer${active ? ' is-active' : ''}`}
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
          <button
            className="rm-send-btn"
            onClick={() => canSend && onSubmit()}
            disabled={!canSend}
            aria-label="Send"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 38,
              height: 38,
              borderRadius: '50%',
              border: 'none',
              flexShrink: 0,
              background: canSend
                ? 'linear-gradient(150deg, color-mix(in oklch, var(--accent) 88%, white), var(--accent))'
                : 'var(--panel-alt)',
              color: canSend ? 'var(--on-accent)' : 'var(--text-faint)',
              cursor: canSend ? 'pointer' : 'not-allowed',
            }}
          >
            {busy ? <Spinner size={15} /> : <Icon.Send size={16} />}
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
          {ready ? (
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
    <div ref={ref} style={{ position: 'relative', flexShrink: 0 }}>
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
            <div
              style={{
                fontSize: 12.5,
                color: 'var(--text-faint)',
                padding: '9px 10px',
              }}
            >
              None configured
            </div>
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
  return ['FAILED', 'CANCELLED', 'TIMED_OUT'].includes(status)
}
