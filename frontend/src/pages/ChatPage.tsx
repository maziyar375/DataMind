import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { conversations, connections as connectionsApi, llmConfigs, streamRun } from '../api/client'
import type {
  Connection, ConversationSummary, LlmConfig, MessageWithRun, RunStep,
} from '../api/types'
import {
  AssistantTurn, RunErrorCard, ThinkingCard, UserBubble,
} from '../components/chat'
import {
  ErrorNote, Icon, PrimaryButton, Spinner, dirOf, initialOf,
} from '../components/ui'

export default function ChatPage() {
  const [conversationList, setConversationList] = useState<ConversationSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<MessageWithRun[]>([])
  const [connections, setConnections] = useState<Connection[]>([])
  const [models, setModels] = useState<LlmConfig[]>([])
  const [connectionId, setConnectionId] = useState<string>('')
  const [modelId, setModelId] = useState<string>('')
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Model-proposed follow-ups, refreshed after each answered turn.
  const [suggestions, setSuggestions] = useState<string[]>([])

  // Live run state, kept separate from persisted messages so a refresh
  // mid-run recovers from the server rather than from this component.
  const [liveSteps, setLiveSteps] = useState<RunStep[]>([])
  const [liveText, setLiveText] = useState('')
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const stopStreamRef = useRef<(() => void) | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const followRef = useRef(true)
  // Id of a conversation `send` just created and is already populating, so the
  // load effect below doesn't re-fetch it out from under the optimistic turn.
  const justCreatedRef = useRef<string | null>(null)
  const [showJump, setShowJump] = useState(false)

  // ── bootstrap ───────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [convs, conns, llms] = await Promise.all([
          conversations.list(),
          connectionsApi.list(),
          llmConfigs.list(),
        ])
        if (cancelled) return
        setConversationList(convs)
        setConnections(conns)
        setModels(llms)
        // No preselection: the reader picks a database and a model for each
        // conversation from the header. Selecting a saved conversation below
        // restores whatever it was started with.
        if (convs.length > 0) setActiveId(convs[0].id)
      } catch {
        if (!cancelled) setError('Could not load your workspace.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
      stopStreamRef.current?.()
    }
  }, [])

  // ── load a conversation ─────────────────────────────────────────────────
  const loadMessages = useCallback(async (conversationId: string) => {
    const loaded = await conversations.messages(conversationId)
    setMessages(loaded)

    // If the newest run is still in flight, reattach to its stream instead of
    // showing a conversation that looks frozen.
    const lastRun = loaded.at(-1)?.run
    if (lastRun && !isTerminal(lastRun.status)) {
      attachStream(lastRun.id, conversationId)
    }
  }, [])

  // Fetch follow-up suggestions for a thread. Best-effort — a failure just
  // leaves the row empty and never surfaces an error to the reader.
  const refreshSuggestions = useCallback(async (conversationId: string) => {
    try {
      const { suggestions: next } = await conversations.suggestions(conversationId)
      setSuggestions(next)
    } catch {
      setSuggestions([])
    }
  }, [])

  useEffect(() => {
    if (!activeId) {
      setMessages([])
      setSuggestions([])
      return
    }
    // A conversation `send` just created already holds the optimistic turn and
    // owns its stream; re-loading it here would race that POST and could blank
    // the question the reader just asked.
    if (justCreatedRef.current === activeId) {
      justCreatedRef.current = null
      return
    }
    setSuggestions([])
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
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }

  useEffect(() => {
    if (!followRef.current) return
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [messages, liveText, liveSteps])

  // ── streaming ───────────────────────────────────────────────────────────
  function attachStream(runId: string, conversationId: string) {
    stopStreamRef.current?.()
    setActiveRunId(runId)
    setLiveSteps([])
    setLiveText('')

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
            setLiveText((prev) => prev + (event.data.text ?? ''))
            break
          default:
            break
        }
      },
      onDone: async () => {
        setActiveRunId(null)
        setLiveSteps([])
        setLiveText('')
        try {
          await loadMessages(conversationId)
          setConversationList(await conversations.list())
        } catch {
          /* the run finished; a list refresh failure is not worth an error */
        }
        // After the answer lands, offer where the reader might go next.
        void refreshSuggestions(conversationId)
      },
      onError: () => {
        /* the client falls back to polling internally */
      },
    })
  }

  // ── send ────────────────────────────────────────────────────────────────
  /** `override` lets a suggestion chip send without waiting for a state tick. */
  async function send(override?: string) {
    const content = (override ?? draft).trim()
    if (!content || activeRunId) return

    if (!connectionId || !modelId) {
      setError('Add a data source and a model before asking a question.')
      return
    }

    setError(null)
    setDraft('')
    setSuggestions([])  // the prior turn's follow-ups no longer apply

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
      })
      attachStream(accepted.run_id, conversationId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send that message.')
      setDraft(content)
    }
  }

  // A new chat starts empty and unbound: no database, no model, nothing
  // persisted. The conversation row is created lazily on the first send (see
  // `send`), stored with exactly the database/model pair chosen there — the
  // pair the thread then stays locked to.
  function newChat() {
    stopStreamRef.current?.()
    setActiveRunId(null)
    setLiveSteps([])
    setLiveText('')
    setActiveId(null)
    setMessages([])
    setSuggestions([])
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
      setActiveRunId(null)
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

  return (
    <div style={{ display: 'flex', height: '100%', width: '100%', minWidth: 0 }}>
      {/* conversation list */}
      <aside
        style={{
          width: 244,
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
          padding: '20px 14px',
          borderRight: '1px solid var(--border)',
          overflowY: 'auto',
        }}
      >
        <PrimaryButton onClick={newChat} style={{ width: '100%', borderRadius: 9 }}>
          <Icon.Plus size={15} />
          New chat
        </PrimaryButton>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {conversationList.map((conversation) => (
            <ConversationItem
              key={conversation.id}
              conversation={conversation}
              active={conversation.id === activeId}
              onSelect={() => setActiveId(conversation.id)}
              onDelete={() => deleteConversation(conversation.id)}
              onRename={(title) => renameConversation(conversation.id, title)}
            />
          ))}
        </div>
      </aside>

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
                  />
                )
              })}

              {activeRunId &&
                (liveText ? (
                  <AssistantTurn text={liveText} run={null} streaming />
                ) : (
                  <ThinkingCard steps={liveSteps} />
                ))}

              {!activeRunId && suggestions.length > 0 && (
                <SuggestedFollowups
                  items={suggestions}
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
  items, onPick,
}: {
  items: string[]
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
        {items.map((text) => (
          <StarterChip key={text} text={text} onClick={() => onPick(text)} />
        ))}
      </div>
    </div>
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

function ConversationItem({
  conversation, active, onSelect, onDelete, onRename,
}: {
  conversation: ConversationSummary
  active: boolean
  onSelect: () => void
  onDelete: () => void
  onRename: (title: string) => void
}) {
  const [hover, setHover] = useState(false)
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
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => {
        setHover(false)
        setConfirming(false)
      }}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        width: '100%',
        padding: '9px 8px 9px 10px',
        borderRadius: 8,
        background: active || hover ? 'var(--panel-hover)' : 'transparent',
        transition: 'background .12s ease',
      }}
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
            padding: '5px 8px',
            fontSize: 12.5,
            fontWeight: 600,
            color: 'var(--text-strong)',
            background: 'var(--input-bg)',
            border: '1px solid var(--accent)',
            borderRadius: 6,
            outline: 'none',
          }}
        />
      ) : (
        <button
          onClick={onSelect}
          onDoubleClick={startEdit}
          title={conversation.title}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            flex: 1,
            minWidth: 0,
            padding: 0,
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          <span
            style={{
              width: 26,
              height: 26,
              borderRadius: 7,
              background: 'var(--panel-alt)',
              color: 'var(--text-dim)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 11,
              fontWeight: 700,
              flexShrink: 0,
            }}
          >
            {initialOf(conversation.title)}
          </span>
          <span
            style={{
              display: 'flex',
              flexDirection: 'column',
              minWidth: 0,
              lineHeight: 1.25,
            }}
          >
            <span
              dir={dirOf(conversation.title)}
              style={{
                fontSize: 12.5,
                fontWeight: 600,
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
                fontSize: 11,
                color: 'var(--text-faint)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {conversation.preview ?? `${conversation.message_count} messages`}
            </span>
          </span>
        </button>
      )}

      {editing ? null : confirming ? (
        <span style={{ display: 'flex', alignItems: 'center', gap: 2, flexShrink: 0 }}>
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
        <span
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 2,
            flexShrink: 0,
            visibility: hover ? 'visible' : 'hidden',
          }}
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
function DisclosureBadge({ policy }: { policy?: string }) {
  if (!policy) return null

  // `tone` names a token trio (--green / --green-bg / --green-border, likewise
  // amber) redefined per theme. The label uses the neutral --text2 (not the
  // tone) so it flips near-white in dark / near-black in light and reads as
  // native to the active palette; a saturated tone label looked like a stray
  // light-mode accent on the dark UI. The dot alone carries the policy colour.
  const copy: Record<string, { short: string; full: string; tone: 'green' | 'amber' }> = {
    NONE: { short: 'Private', full: 'No rows shared with the model provider', tone: 'green' },
    AGGREGATE: { short: 'Totals', full: 'Only aggregate totals shared with the model provider', tone: 'green' },
    SAMPLE: { short: 'Sample', full: 'Sample rows shared with the model provider', tone: 'amber' },
    FULL: { short: 'All rows', full: 'All rows shared with the model provider', tone: 'amber' },
  }
  const entry = copy[policy]
  if (!entry) return null

  return (
    <span
      title={`${entry.full} — controls how much of a query result is sent to the model provider.`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        flexShrink: 0,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: '0.01em',
        // Neutral, theme-aware label on a native surface — near-white in dark,
        // near-black in light — so the chip belongs to whichever palette is
        // active. The dot alone carries the amber/green policy signal.
        color: 'var(--text2)',
        background: `var(--${entry.tone}-bg)`,
        border: `1px solid var(--${entry.tone}-border)`,
        padding: '2px 7px 2px 6px',
        borderRadius: 999,
        whiteSpace: 'nowrap',
      }}
    >
      <span
        style={{
          width: 5,
          height: 5,
          borderRadius: '50%',
          background: `var(--${entry.tone})`,
        }}
      />
      {entry.short}
    </span>
  )
}

function isTerminal(status: string): boolean {
  return ['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT'].includes(status)
}

/** Terminal states that owe the reader an explanation. */
function isFailure(status: string): boolean {
  return ['FAILED', 'CANCELLED', 'TIMED_OUT'].includes(status)
}
