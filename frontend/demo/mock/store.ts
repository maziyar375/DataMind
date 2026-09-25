/**
 * The demo's session: conversations and their runs, in memory, kept in
 * `sessionStorage` so a refresh — on a deep link, or in the middle of a run —
 * comes back to the same place.
 *
 * A run is stored as *which answer* and *when it started*; its state at any
 * moment is read off its timeline (`stream.ts`). So a run that was streaming
 * when the tab reloaded is still streaming afterwards, from where the clock
 * says it is, the way a real run survives the page that started it.
 */
import type {
  AnswerFeedback, Artifact, ConversationSummary, GeneratedQuery, MessageWithRun, RunDetail,
  RunKnowledge,
} from '../../src/api/types'
import { demoIso, demoNow, daysAgo } from './clock'
import { ANSWERS, BUILT_FOR, HISTORY } from './fixtures/answers.generated'
import { DEEP } from './fixtures/deep.generated'
import { VERIFIED } from './fixtures/knowledge.generated'
import { DEMO_TODAY } from './fixtures/today'
import { CONNECTIONS, IDS, LLM_CONFIGS } from './fixtures/world'
import type { ScriptedAnswer, ScriptedChart, ScriptedDeep } from './script-types'
import {
  scriptDeep, scriptFallback, scriptRun, stepsAt, type DeepTimeline, type Timeline, type VerifiedAnswer,
} from './stream'

export interface StoredTurn {
  messageId: string
  question: string
  askedAt: number
  runIds: string[]
}

export interface StoredConversation {
  id: string
  title: string
  connectionId: string
  llmConfigId: string
  createdAt: number
  updatedAt: number
  turns: StoredTurn[]
}

export interface StoredRun {
  id: string
  conversationId: string
  userMessageId: string
  assistantMessageId: string
  /** QUICK or DEEP — what the composer was set to when this was asked. */
  depth: 'QUICK' | 'DEEP'
  /** The scripted answer, or null for the demo's own "I can't answer that". */
  answerId: string | null
  /** The scripted deep analysis, on a DEEP run that has one. */
  deepId: string | null
  /** When *Answer now* was pressed on a deep run, on the demo clock. */
  answerNowAt: number | null
  fallback: string | null
  connectionId: string
  llmConfigId: string
  startedAt: number
  cancelledAt: number | null
  feedback: AnswerFeedback | null
  overridden: boolean
}

interface State {
  builtFor: string
  conversations: StoredConversation[]
  runs: StoredRun[]
}

const KEY = 'datamind-demo:session:v1'

/** Every recorded quick answer, including the two the knowledge store gives. */
export const ANSWERS_BY_ID = new Map<string, ScriptedAnswer>([...ANSWERS, ...VERIFIED].map((a) => [a.id, a]))
export const DEEP_BY_ID = new Map(DEEP.map((d) => [d.id, d]))

/** Deep analyses already in the sidebar: (id, days before DEMO_TODAY, hour). */
const DEEP_HISTORY: [string, number, number][] = [['deep-june', 2, 15.3]]

if (BUILT_FOR !== DEMO_TODAY) {
  console.warn(
    `[demo] fixtures were built for ${BUILT_FOR} but DEMO_TODAY is ${DEMO_TODAY} — `
    + 'run demo/scripts/build-fixtures.sh so the data and the SQL agree.',
  )
}

export function newId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx'.replace(/x/g, () => Math.floor(Math.random() * 16).toString(16))
}

/** The sidebar's conversations: asked on earlier days, finished long ago. */
function seed(): State {
  const conversations: StoredConversation[] = []
  const runs: StoredRun[] = []
  HISTORY.forEach(([answerId, days, hour], i) => {
    const answer = ANSWERS_BY_ID.get(answerId)!
    const connection = CONNECTIONS.find((c) => c.id === IDS.connections[answer.connection])!
    const model = LLM_CONFIGS[i % 3 === 2 ? 1 : 0]
    const askedAt = Date.parse(daysAgo(days, hour))
    const conversationId = `0000000a-0000-4000-8000-${(i + 1).toString(16).padStart(12, '0')}`
    const turn: StoredTurn = {
      messageId: `0000000b-0000-4000-8000-${(i + 1).toString(16).padStart(12, '0')}`,
      question: answer.question,
      askedAt,
      runIds: [`0000000c-0000-4000-8000-${(i + 1).toString(16).padStart(12, '0')}`],
    }
    runs.push({
      id: turn.runIds[0],
      conversationId,
      userMessageId: turn.messageId,
      assistantMessageId: `0000000d-0000-4000-8000-${(i + 1).toString(16).padStart(12, '0')}`,
      depth: 'QUICK',
      answerId,
      deepId: null,
      answerNowAt: null,
      fallback: null,
      connectionId: connection.id,
      llmConfigId: model.id,
      startedAt: askedAt + 400,
      cancelledAt: null,
      feedback: null,
      overridden: false,
    })
    conversations.push({
      id: conversationId,
      title: titleOf(answer.question),
      connectionId: connection.id,
      llmConfigId: model.id,
      createdAt: askedAt - 5000,
      updatedAt: askedAt + 9000,
      turns: [turn],
    })
  })
  DEEP_HISTORY.forEach(([deepId, days, hour], j) => {
    const deep = DEEP_BY_ID.get(deepId)!
    const connection = CONNECTIONS.find((c) => c.id === IDS.connections[deep.connection])!
    const askedAt = Date.parse(daysAgo(days, hour))
    const n = (HISTORY.length + j + 1).toString(16).padStart(12, '0')
    const turn: StoredTurn = {
      messageId: `0000000b-0000-4000-8000-${n}`, question: deep.question, askedAt,
      runIds: [`0000000c-0000-4000-8000-${n}`],
    }
    runs.push({
      id: turn.runIds[0], conversationId: `0000000a-0000-4000-8000-${n}`, userMessageId: turn.messageId,
      assistantMessageId: `0000000d-0000-4000-8000-${n}`, depth: 'DEEP', answerId: null, deepId,
      answerNowAt: null, fallback: null, connectionId: connection.id, llmConfigId: LLM_CONFIGS[0].id,
      startedAt: askedAt + 400, cancelledAt: null, feedback: null, overridden: false,
    })
    conversations.push({
      id: `0000000a-0000-4000-8000-${n}`, title: titleOf(deep.question), connectionId: connection.id,
      llmConfigId: LLM_CONFIGS[0].id, createdAt: askedAt - 5000, updatedAt: askedAt + 40000, turns: [turn],
    })
  })
  return { builtFor: BUILT_FOR, conversations, runs }
}

/** What the backend does to a new thread's title: the first question, cut. */
export function titleOf(question: string): string {
  return question.slice(0, 80)
}

function load(): State {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as State
      // A session from before deep runs existed: its runs are all quick ones.
      if (parsed.builtFor === BUILT_FOR && parsed.runs.every((r) => 'depth' in r)) return parsed
    }
  } catch {
    /* private mode, blocked storage: a fresh session is the right answer */
  }
  return seed()
}

export const state: State = load()

export function save(): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(state))
  } catch {
    /* the session simply does not survive a reload */
  }
}

// ── reading a run ────────────────────────────────────────────────────────────
const timelines = new Map<string, Timeline>()

export function answerOf(run: StoredRun): ScriptedAnswer | null {
  return run.answerId ? ANSWERS_BY_ID.get(run.answerId) ?? null : null
}

export function deepOf(run: StoredRun): ScriptedDeep | null {
  return run.deepId ? DEEP_BY_ID.get(run.deepId) ?? null : null
}

/**
 * A run's timeline. Keyed by *Answer now* as well as the run, because pressing
 * it rewrites the run's ending: the steps after the one in flight never start,
 * and `synthesize` writes from what was found. Everything up to that moment is
 * the same in both, so a stream already playing carries on seamlessly.
 */
export function timelineOf(run: StoredRun): Timeline {
  const key = `${run.id}:${run.answerNowAt ?? ''}`
  let timeline = timelines.get(key)
  if (!timeline) {
    const answer = answerOf(run)
    const deep = deepOf(run)
    const connection = CONNECTIONS.find((c) => c.id === run.connectionId)!
    const model = LLM_CONFIGS.find((m) => m.id === run.llmConfigId) ?? LLM_CONFIGS[0]
    if (deep) {
      timeline = scriptDeep(deep, connection, model, run.id)
      if (run.answerNowAt !== null) {
        // The loop reads the request on the edge before each step, so the
        // steps that had started by then are the steps that run.
        const pressed = run.answerNowAt - run.startedAt
        const ran = (timeline as DeepTimeline).stepStarts.filter((at) => at <= pressed).length
        if (ran < deep.steps.length) timeline = scriptDeep(deep, connection, model, run.id, ran)
      }
    } else {
      timeline = answer ? scriptRun(answer, connection, model, run.id) : scriptFallback(run.fallback ?? '')
    }
    timelines.set(key, timeline)
  }
  return timeline
}

function deepTimelineOf(run: StoredRun): DeepTimeline | null {
  return run.deepId ? (timelineOf(run) as DeepTimeline) : null
}

/**
 * The result the finished turn shows, and its chart: the answer's own, or on
 * a deep run the last step that ran — which is what `synthesize` hands the
 * `chart` node.
 */
export function chartedOf(run: StoredRun): (ScriptedChart & { result: ScriptedAnswer['result'] }) | null {
  const deep = deepTimelineOf(run)
  if (deep) return deep.ran > 0 ? deepOf(run)!.steps[deep.ran - 1] : null
  return answerOf(run)
}

/** The statement a curator would open: the answer's last draft, or the charted step's. */
export function lastSqlOf(run: StoredRun): string {
  const deep = deepTimelineOf(run)
  if (deep) return deep.ran > 0 ? deepOf(run)!.steps[deep.ran - 1].attempt.raw_sql : ''
  return answerOf(run)?.attempts.at(-1)?.raw_sql ?? ''
}

export type RunState = 'RUNNING' | 'SUCCEEDED' | 'CANCELLED'

export function stateOf(run: StoredRun, now = demoNow()): RunState {
  const end = run.startedAt + timelineOf(run).total
  if (run.cancelledAt !== null && run.cancelledAt < end) return 'CANCELLED'
  return now >= end ? 'SUCCEEDED' : 'RUNNING'
}

function queriesUpTo(attempts: ScriptedAnswer['attempts'], timeline: Timeline, elapsed: number): GeneratedQuery[] {
  const judged = new Set(
    timeline.events
      .filter((e) => e.at <= elapsed && (e.type === 'SQL_VALIDATED' || e.type === 'SQL_REJECTED'))
      .map((e) => e.data.attempt_no as number),
  )
  return attempts
    .filter((a) => judged.has(a.attempt_no))
    .map((a) => ({
      attempt_no: a.attempt_no,
      raw_sql: a.raw_sql,
      rewritten_sql: a.rewritten_sql,
      validation_status: a.validation_status,
      // As served: the API sends `hint: null` where the SPA's type says absent,
      // and the SPA reads it as falsy either way.
      validation_report: a.validation_report as GeneratedQuery['validation_report'],
      referenced_tables: a.referenced_tables,
    }))
}

function knowledgeOf(run: StoredRun): RunKnowledge {
  const answer = answerOf(run)
  if (answer && 'verified' in answer) {
    // Answered from a saved question: the badge names it, with what was bound.
    const { verified } = answer as VerifiedAnswer
    return {
      tier: 'VERIFIED',
      template_id: verified.template_id,
      question: verified.question,
      bound_params: verified.bound_params,
      score: verified.score,
      matcher: 'LEXICAL',
      overridden: run.overridden,
      feedback: run.feedback,
      metrics_used: [],
      metrics_version: null,
    }
  }
  return {
    tier: 'GENERATED',
    template_id: null,
    question: '',
    bound_params: {},
    score: 0,
    matcher: 'LEXICAL',
    overridden: run.overridden,
    feedback: run.feedback,
    metrics_used: [],
    metrics_version: null,
  }
}

export function artifactsOf(run: StoredRun): Artifact[] {
  const charted = chartedOf(run)
  const out: Artifact[] = []
  if (charted?.result) {
    const { columns, rows, row_count, truncated } = charted.result
    out.push({
      id: `${run.id.slice(0, 24)}000000000001`,
      kind: 'TABLE',
      spec: { columns, rows, row_count, truncated },
    })
    if (charted.chart) {
      out.push({ id: `${run.id.slice(0, 24)}000000000002`, kind: 'CHART', spec: charted.chart as Artifact['spec'] })
    }
    if (charted.kpi) {
      out.push({ id: `${run.id.slice(0, 24)}000000000003`, kind: 'KPI', spec: charted.kpi as unknown as Artifact['spec'] })
    }
  }
  const deep = deepTimelineOf(run)
  if (deep) {
    out.push({ id: `${run.id.slice(0, 24)}000000000004`, kind: 'ANALYSIS', spec: deep.analysis as Artifact['spec'] })
  }
  return out
}

/** Every statement a run judged, in order: the answer's drafts, or one per deep step that ran. */
function attemptsOf(run: StoredRun): ScriptedAnswer['attempts'] {
  const deep = deepTimelineOf(run)
  if (deep) return deepOf(run)!.steps.slice(0, deep.ran).map((s) => s.attempt)
  return answerOf(run)?.attempts ?? []
}

export function detailOf(run: StoredRun, now = demoNow()): RunDetail {
  const charted = chartedOf(run)
  const timeline = timelineOf(run)
  const status = stateOf(run, now)
  const connection = CONNECTIONS.find((c) => c.id === run.connectionId)!
  const model = LLM_CONFIGS.find((m) => m.id === run.llmConfigId) ?? LLM_CONFIGS[0]
  const cut = status === 'CANCELLED' ? run.cancelledAt! - run.startedAt : now - run.startedAt
  const done = status === 'SUCCEEDED'
  return {
    id: run.id,
    conversation_id: run.conversationId,
    status,
    depth: run.depth,
    error_code: null,
    error_message: null,
    repair_count: done ? timeline.repairs : 0,
    total_latency_ms: done ? timeline.total : status === 'CANCELLED' ? Math.round(cut) : null,
    db_latency_ms: done && charted?.result ? charted.result.duration_ms : null,
    model_snapshot: {
      provider: model.provider, model: model.model, temperature: model.temperature,
      max_tokens: model.max_tokens, connection_name: connection.name, prompt_version: 'v12',
    },
    connection_id: connection.id,
    // No semantic layer reached any prompt: none is written in the demo.
    semantic_layer_version: 0,
    prompt_tokens: done ? timeline.promptTokens : null,
    completion_tokens: done ? timeline.completionTokens : null,
    retrieval_sections: [],
    steps: done ? timeline.steps : stepsAt(timeline, cut),
    artifacts: done ? artifactsOf(run) : [],
    queries: queriesUpTo(attemptsOf(run), timeline, done ? Infinity : cut),
    knowledge: knowledgeOf(run),
    restricted: false,
    restricted_reason: null,
  }
}

// ── reading a conversation ────────────────────────────────────────────────────
export function runOf(id: string): StoredRun | undefined {
  return state.runs.find((r) => r.id === id)
}

export function conversationOf(id: string): StoredConversation | undefined {
  return state.conversations.find((c) => c.id === id)
}

function latestRun(turn: StoredTurn): StoredRun | undefined {
  return runOf(turn.runIds[turn.runIds.length - 1])
}

export function messagesOf(conversation: StoredConversation, now = demoNow()): MessageWithRun[] {
  const out: MessageWithRun[] = []
  let seq = 0
  for (const turn of conversation.turns) {
    seq += 1
    const run = latestRun(turn)
    const user: MessageWithRun = {
      id: turn.messageId, seq, role: 'USER', content: turn.question, created_at: demoIso(turn.askedAt), run: null,
    }
    out.push(user)
    if (!run) continue
    const status = stateOf(run, now)
    if (status !== 'SUCCEEDED') {
      // In flight or stopped: no answer was written, so the run hangs off the
      // question — which is where the real API attaches it too.
      user.run = detailOf(run, now)
      continue
    }
    const answer = answerOf(run)
    const deep = deepTimelineOf(run)
    seq += 1
    out.push({
      id: run.assistantMessageId,
      seq,
      role: 'ASSISTANT',
      content: deep ? deep.answer : answer ? answer.answer : run.fallback,
      created_at: demoIso(run.startedAt + timelineOf(run).total),
      run: answer || deep ? detailOf(run, now) : null,
    })
  }
  return out
}

export function summaryOf(conversation: StoredConversation): ConversationSummary {
  const messages = messagesOf(conversation)
  const last = [...messages].reverse().find((m) => m.content)
  return {
    id: conversation.id,
    title: conversation.title,
    status: 'ACTIVE',
    default_connection_id: conversation.connectionId,
    default_llm_config_id: conversation.llmConfigId,
    created_at: demoIso(conversation.createdAt),
    updated_at: demoIso(conversation.updatedAt),
    message_count: messages.length,
    preview: last?.content ? last.content.replace(/\*\*/g, '').slice(0, 140) : null,
  }
}

/** The questions already asked in a conversation, by answer id. */
export function askedIn(conversation: StoredConversation): Set<string> {
  const asked = new Set<string>()
  for (const turn of conversation.turns) {
    const run = latestRun(turn)
    if (run?.answerId) asked.add(run.answerId)
    if (run?.deepId) asked.add(run.deepId)
  }
  return asked
}

export function lastRunIn(conversation: StoredConversation): StoredRun | undefined {
  const turn = conversation.turns[conversation.turns.length - 1]
  return turn ? latestRun(turn) : undefined
}
