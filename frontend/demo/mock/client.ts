/**
 * The API client, for the demo: every export of `src/api/client.ts`, answering
 * from fixtures instead of the network.
 *
 * `demo/plugins/demo-substitute.ts` resolves every import of the real client
 * to this module, so the app is unchanged and no request ever leaves the page.
 * Each namespace is declared as `typeof Real.<namespace>`, so a method the real
 * client grows and this one lacks is a compile error in `demo/tsconfig.json`
 * (`surface.ts` checks the export list itself). Only the implementation is
 * fake: the error class, the token plumbing and the run-status helpers are the
 * real module's own, re-exported.
 *
 * Reads answer from fixtures. Writes that the demo can honour without
 * pretending — renaming or deleting a conversation, asking a question, saying
 * whether an answer was right — are kept for the session. Everything else is
 * refused with one sentence saying why: nothing here reaches a database or a
 * model, and a demo that appeared to save a connection or rotate a key would be
 * claiming something it did not do.
 */
import type * as Real from '../../src/api/client'
import {
  ApiError, getAccessToken, isReportRunInFlight, isRunInFlight, onAuthChange, setAccessToken,
} from '../../src/api/client'
import type {
  Actions, AnswerFeedback, ChartRedraw, Connection, ConversationSummary, Directory, Grant,
  KnowledgeHealth, Permissions, Reach, Review, RunEvent, SchemaSnapshot, TemplateCheckResult, User,
} from '../../src/api/types'
import { demoNow } from './clock'
import { AUDIT, recordOperation, usageByPerson, usageMine, usageTotal } from './fixtures/activity'
import { CATALOG } from './fixtures/catalog.generated'
import { SECTIONS, SNAPSHOTS, SNAPSHOT_VERSION } from './fixtures/schema.generated'
import {
  CONNECTION_KEY, CONNECTIONS, DEEP_LIMITS, DEMO_PERSON, IDS, LLM_CONFIGS, PEOPLE_BY_ID, ROLES, SERVICE_ACCOUNTS,
  SERVICE_KEYS, TEAMS, USERS, rolesForUser, teamMembers, teamsForUser, userOf,
} from './fixtures/world'
import {
  ANSWERS_BY_ID, answerOf, askedIn, chartedOf, conversationOf, deepOf, detailOf, lastRunIn,
  lastSqlOf, messagesOf, newId, runOf, save, state, stateOf, summaryOf, timelineOf, titleOf, type StoredRun,
} from './store'
import { ANSWERS } from './fixtures/answers.generated'
import { DEEP } from './fixtures/deep.generated'
import { play, type DeepTimeline } from './stream'
import { REQUEST_MS, SUGGESTIONS_MS } from './timing'

export { ApiError, getAccessToken, isReportRunInFlight, isRunInFlight, onAuthChange, setAccessToken }
export type { UsageRange } from '../../src/api/client'

/**
 * Whether the Semantic tab is offered. Off: the demo writes no semantic layer,
 * and the tab's empty state would only offer a generation that cannot run.
 * The tab is hidden the way the product hides it for a reader with no grant on
 * the layer — `GET …/semantic/actions` answers 404.
 */
const SHOW_SEMANTIC_TAB = false

// ── plumbing ─────────────────────────────────────────────────────────────────
function respond<T>(value: T | (() => T), ms: number = REQUEST_MS): Promise<T> {
  return new Promise((resolve, reject) => {
    setTimeout(() => {
      try {
        const out = typeof value === 'function' ? (value as () => T)() : value
        // A copy, so a screen that edits what it was given cannot edit the fixture.
        resolve(out === undefined ? out : structuredClone(out))
      } catch (err) {
        reject(err)
      }
    }, ms)
  })
}

const DEMO_NOTE = 'This is a read-only demo: nothing is saved, and nothing reaches a database or a model.'

function refuse(what: string): Promise<never> {
  return new Promise((_, reject) =>
    setTimeout(() => reject(new ApiError(`${what} ${DEMO_NOTE}`, 'E_DEMO_READ_ONLY', 409, {
      title: 'Not available in the demo', status: 409, code: 'E_DEMO_READ_ONLY', detail: `${what} ${DEMO_NOTE}`,
    })), REQUEST_MS),
  )
}

function notFound(noun: string): ApiError {
  return new ApiError(`${noun} not found.`, 'E_NOT_FOUND', 404, { title: 'Not found', status: 404, code: 'E_NOT_FOUND' })
}

const me = () => IDS.users[DEMO_PERSON.key]
let displayName = DEMO_PERSON.name

function currentUser(): User {
  return { ...userOf(DEMO_PERSON, true), display_name: displayName }
}

function connectionOr404(id: string): Connection {
  const found = CONNECTIONS.find((c) => c.id === id)
  if (!found) throw notFound('Connection')
  return found
}

// ── auth ─────────────────────────────────────────────────────────────────────
/**
 * The demo opens on the sign-in screen, pre-filled, one click from the app.
 * Signing in is remembered for the tab, the way the real refresh cookie is
 * remembered for the browser: a reload on a deep link comes back signed in,
 * and a new visit starts at the door. `#/login` always shows the screen.
 */
const SIGNED_IN_KEY = 'datamind-demo:signed-in'

function wantsLoginScreen(): boolean {
  return typeof location !== 'undefined' && location.hash.replace(/^#/, '').startsWith('/login')
}

function signedInThisTab(): boolean {
  try {
    return sessionStorage.getItem(SIGNED_IN_KEY) === '1'
  } catch {
    return false
  }
}

function rememberSignedIn(on: boolean): void {
  try {
    if (on) sessionStorage.setItem(SIGNED_IN_KEY, '1')
    else sessionStorage.removeItem(SIGNED_IN_KEY)
  } catch {
    /* blocked storage: the next reload simply starts at the door */
  }
}

export const auth: typeof Real.auth = {
  async login() {
    setAccessToken('demo')
    rememberSignedIn(true)
    // Off the login address, so the router lands on the app rather than on a
    // route the signed-in shell does not have.
    if (wantsLoginScreen()) history.replaceState(null, '', `${location.pathname}#/chat`)
    return respond(currentUser, 450)
  },
  async restore() {
    if (wantsLoginScreen() || !signedInThisTab()) return respond(null, 250)
    setAccessToken('demo')
    return respond(currentUser, 250)
  },
  async logout() {
    rememberSignedIn(false)
    setAccessToken(null)
  },
  me: () => respond(currentUser),
  permissions: () => respond((): Permissions => ({
    capabilities: currentUser().capabilities ?? [],
    roles: currentUser().roles ?? [],
    teams: currentUser().teams ?? [],
    reach: reachOf({ principal_id: me() }),
  })),
  async updateProfile(name: string) {
    displayName = name.trim() || displayName
    return respond(currentUser)
  },
  changePassword: () => refuse('Passwords cannot be changed here.'),
}

// ── people, roles, teams, service accounts, audit ────────────────────────────
export const users: typeof Real.users = {
  list: () => respond(USERS),
  roles: (id) => respond(() => rolesForUser(id)),
  assignRole: () => refuse('Roles cannot be assigned here.'),
  unassignRole: () => refuse('Roles cannot be removed here.'),
  teams: (id) => respond(() => teamsForUser(id)),
  create: () => refuse('Nobody can be invited here.'),
  update: () => refuse('Accounts cannot be edited here.'),
  setPassword: () => refuse('Passwords cannot be set here.'),
  remove: () => refuse('Accounts cannot be deleted here.'),
}

export const roles: typeof Real.roles = {
  list: () => respond(ROLES),
  get: (id) => respond(() => {
    const role = ROLES.find((r) => r.id === id)
    if (!role) throw notFound('Role')
    return role
  }),
  create: () => refuse('Roles cannot be created here.'),
  update: () => refuse('Roles cannot be edited here.'),
  remove: () => refuse('Roles cannot be deleted here.'),
  capabilities: () => respond(CATALOG.capabilities),
  privileges: () => respond(CATALOG.privileges),
}

export const teams: typeof Real.teams = {
  list: () => respond(TEAMS),
  get: (id) => respond(() => {
    const team = TEAMS.find((t) => t.id === id)
    if (!team) throw notFound('Team')
    return team
  }),
  members: (id) => respond(() => teamMembers(id)),
  create: () => refuse('Teams cannot be created here.'),
  update: () => refuse('Teams cannot be edited here.'),
  setMembers: () => refuse('Team membership cannot be changed here.'),
  assignRole: () => refuse('Roles cannot be assigned here.'),
  unassignRole: () => refuse('Roles cannot be removed here.'),
  bindSource: () => refuse('Teams cannot be bound to a directory here.'),
  remove: () => refuse('Teams cannot be deleted here.'),
}

export const audit: typeof Real.audit = {
  list: (params = {}) => respond(() => AUDIT.filter((entry) => (
    (!params.action || entry.action === params.action)
    && (!params.outcome || entry.outcome === params.outcome)
    && (!params.resource_type || entry.resource_type === params.resource_type)
    && (!params.actor || entry.actor.toLowerCase().includes(params.actor.toLowerCase()))
    && (!params.since || entry.at >= params.since)
    && (!params.until || entry.at < params.until)
    && (!params.before || entry.at < params.before)
  )).slice(0, params.limit ?? 50)),
  actions: () => respond(() => [...new Set(AUDIT.map((e) => e.action))].sort()),
}

export const usage: typeof Real.usage = {
  mine: (params = {}) => respond(() => usageMine(me(), displayName, params)),
  byPerson: (params = {}) => respond(() => usageByPerson(params)),
  total: (params = {}) => respond(() => usageTotal(params)),
}

export const serviceAccounts: typeof Real.serviceAccounts = {
  list: () => respond(SERVICE_ACCOUNTS),
  get: (id) => respond(() => {
    const account = SERVICE_ACCOUNTS.find((a) => a.id === id)
    if (!account) throw notFound('Service account')
    return account
  }),
  create: () => refuse('Service accounts cannot be created here.'),
  update: () => refuse('Service accounts cannot be edited here.'),
  remove: () => refuse('Service accounts cannot be deleted here.'),
  assignRole: () => refuse('Roles cannot be assigned here.'),
  unassignRole: () => refuse('Roles cannot be removed here.'),
  keys: (id) => respond(() => (id === IDS.service.digest ? SERVICE_KEYS : [])),
  issueKey: () => refuse('No key is issued in the demo.'),
  revokeKey: () => refuse('Keys cannot be revoked here.'),
}

// ── access ───────────────────────────────────────────────────────────────────
const TYPE_OF_SEGMENT: Record<string, string> = {
  connections: 'connection', knowledge: 'knowledge', semantic: 'semantic_layer',
  dashboards: 'dashboard', reports: 'report', 'llm-configs': 'llm_config', conversations: 'conversation',
}

/** `connections/{id}/knowledge` → the resource type and id the backend would see. */
function parseBase(base: string): { type: string; id: string } {
  const parts = base.split('/')
  const derived = parts[2] ? TYPE_OF_SEGMENT[parts[2]] : undefined
  return { type: derived ?? TYPE_OF_SEGMENT[parts[0]] ?? parts[0], id: parts[1] }
}

const ALL_PRIVILEGES = ['describe', 'select', 'modify', 'delete', 'manage']
const CAN: Record<string, string> = { view: 'select', edit: 'modify', delete: 'delete', share: 'manage', transfer: 'manage' }

function ownerOf(type: string, id: string): string {
  if ((type === 'connection' || type === 'knowledge' || type === 'semantic_layer') && id === IDS.connections.sakila) return 'Priya Nair'
  if (type === 'llm_config' && id === IDS.llm.gpt) return 'Priya Nair'
  return DEMO_PERSON.name
}

function actionsFor(type: string, id: string, held: string[] = ALL_PRIVILEGES): Actions {
  const owner = ownerOf(type, id)
  return {
    privileges: held,
    can: Object.fromEntries(Object.entries(CAN).map(([name, privilege]) => [name, held.includes(privilege)])),
    meanings: CATALOG.privileges[type] ?? {},
    labels: CATALOG.labels[type] ?? {},
    levels: CATALOG.levels[type] ?? [],
    owner_name: owner,
    is_owner: owner === DEMO_PERSON.name,
  }
}

/** Who holds what on each thing, beyond its owner. */
const GRANTS: Record<string, Grant[]> = {
  [`connection:${IDS.connections.sales}`]: [
    { id: 'g-1', principal_id: IDS.teams.analytics, principal_name: 'Analytics', principal_kind: 'TEAM', privilege: 'select', path: 'direct' },
    { id: 'g-2', principal_id: IDS.teams.finance, principal_name: 'Finance', principal_kind: 'TEAM', privilege: 'select', path: 'direct' },
    { id: 'g-3', principal_id: IDS.users.tomas, principal_name: 'Tomás Álvarez', principal_kind: 'HUMAN', privilege: 'modify', path: 'direct' },
  ],
  [`connection:${IDS.connections.sakila}`]: [
    { id: 'g-4', principal_id: IDS.users.mazbar, principal_name: 'Mazbar Azami', principal_kind: 'HUMAN', privilege: 'manage', path: 'direct' },
    { id: 'g-5', principal_id: IDS.teams.analytics, principal_name: 'Analytics', principal_kind: 'TEAM', privilege: 'select', path: 'direct' },
  ],
  [`llm_config:${IDS.llm.sonnet}`]: [
    { id: 'g-6', principal_id: IDS.teams.analytics, principal_name: 'Analytics', principal_kind: 'TEAM', privilege: 'select', path: 'direct' },
    { id: 'g-7', principal_id: IDS.teams.finance, principal_name: 'Finance', principal_kind: 'TEAM', privilege: 'select', path: 'direct' },
  ],
  [`llm_config:${IDS.llm.gpt}`]: [
    { id: 'g-8', principal_id: IDS.users.mazbar, principal_name: 'Mazbar Azami', principal_kind: 'HUMAN', privilege: 'select', path: 'direct' },
  ],
}

function grantsOn(type: string, id: string): Grant[] {
  const ownerName = ownerOf(type === 'knowledge' || type === 'semantic_layer' ? 'connection' : type, id)
  const owner = [...PEOPLE_BY_ID.entries()].find(([, p]) => p.name === ownerName)
  const key = type === 'knowledge' || type === 'semantic_layer' ? `connection:${id}` : `${type}:${id}`
  return [
    { id: null, principal_id: owner?.[0] ?? me(), principal_name: ownerName, principal_kind: 'HUMAN', privilege: 'manage', path: 'owner' },
    ...(GRANTS[key] ?? []),
  ]
}

function resourceName(type: string, id: string): string {
  if (type === 'connection' || type === 'knowledge' || type === 'semantic_layer') return CONNECTIONS.find((c) => c.id === id)?.name ?? id
  if (type === 'llm_config') return LLM_CONFIGS.find((m) => m.id === id)?.name ?? id
  return id
}

function reachOf(query: { principal_id?: string; resource_type?: string; resource_id?: string; privilege?: string }): Reach[] {
  const rows: Reach[] = []
  const things: [string, string][] = [
    ...CONNECTIONS.map((c): [string, string] => ['connection', c.id]),
    ...LLM_CONFIGS.map((m): [string, string] => ['llm_config', m.id]),
  ]
  for (const [type, id] of things) {
    for (const grant of grantsOn(type, id)) {
      const members = grant.principal_kind === 'TEAM'
        ? teamMembers(grant.principal_id).map((u) => ({ id: u.id, name: u.display_name, via: grant.principal_name }))
        : [{ id: grant.principal_id, name: grant.principal_name, via: '' }]
      for (const m of members) {
        rows.push({
          principal_id: m.id, principal_name: m.name, principal_kind: 'HUMAN', resource_type: type,
          resource_id: id, resource_name: resourceName(type, id), privilege: grant.privilege,
          path: grant.path === 'owner' ? 'owner' : m.via ? 'team' : 'direct', via: m.via,
        })
      }
    }
  }
  return rows.filter((r) => (
    (!query.principal_id || r.principal_id === query.principal_id)
    && (!query.resource_type || r.resource_type === query.resource_type)
    && (!query.resource_id || r.resource_id === query.resource_id)
    && (!query.privilege || r.privilege === query.privilege)
  ))
}

export const access: typeof Real.access = {
  grants: (base) => respond(() => {
    const { type, id } = parseBase(base)
    return grantsOn(type, id)
  }),
  grant: () => refuse('Nothing can be shared here.'),
  revoke: () => refuse('Access cannot be revoked here.'),
  actions: (base) => {
    const { type, id } = parseBase(base)
    if (type === 'semantic_layer' && !SHOW_SEMANTIC_TAB) return Promise.reject(notFound('Semantic layer'))
    if (type === 'llm_config' && id === IDS.llm.gpt) return respond(actionsFor(type, id, ['describe', 'select']))
    return respond(actionsFor(type, id))
  },
  directory: () => respond((): Directory => ({
    people: [
      ...USERS.filter((u) => u.status !== 'DISABLED').map((u) => ({
        id: u.id, name: u.display_name, kind: 'HUMAN' as const, members: null, is_you: u.id === me(),
      })),
      ...SERVICE_ACCOUNTS.map((a) => ({ id: a.id, name: a.display_name, kind: 'SERVICE' as const, members: null, is_you: false })),
    ],
    teams: TEAMS.map((t) => ({ id: t.id, name: t.name, kind: 'TEAM' as const, members: t.members, is_you: false })),
  })),
  transfer: () => refuse('Ownership cannot be transferred here.'),
  review: (query) => respond(() => reachOf(query)),
  reviewCsv: (query) => respond(() => {
    const rows = reachOf(query)
    const head = 'principal,kind,resource_type,resource,privilege,path,via'
    const quote = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v)
    return [head, ...rows.map((r) => [r.principal_name, r.principal_kind, r.resource_type, r.resource_name, r.privilege, r.path, r.via].map(quote).join(','))].join('\n')
  }),
  shareCheck: () => respond({ total_connections: 0, unreadable: [] }),
  reportShareCheck: () => respond({ total_connections: 1, unreadable: [] }),
}

// ── data sources ─────────────────────────────────────────────────────────────
const NO_DATABASE = 'There is no live database behind the demo — the schema shown was synced when the demo was built.'

export const connections: typeof Real.connections = {
  list: () => respond(CONNECTIONS),
  setDisclosure: () => refuse('The disclosure policy cannot be changed here.'),
  deepBudget: () => respond({
    effective: DEEP_LIMITS,
    ceiling: DEEP_LIMITS,
    is_default: true,
    refused: null,
  }),
  setDeepBudget: () => refuse('Budgets cannot be changed here.'),
  create: () => refuse('Connections cannot be added here.'),
  update: () => refuse('Connections cannot be edited here.'),
  remove: () => refuse('Connections cannot be deleted here.'),
  test: () => refuse(NO_DATABASE),
  testDraft: () => refuse(NO_DATABASE),
  syncSchema: () => refuse(NO_DATABASE),
  schema: (id) => respond((): SchemaSnapshot => {
    const connection = connectionOr404(id)
    const snap = SNAPSHOTS[CONNECTION_KEY[id]]
    return {
      dialect: snap.dialect,
      version: SNAPSHOT_VERSION,
      synced_at: connection.last_synced_at,
      tables: snap.tables,
      relationships: snap.relationships,
      catalog_meta: snap.catalog_meta,
    }
  }),
}

export const sections: typeof Real.sections = {
  get: (connectionId) => respond(() => {
    const connection = connectionOr404(connectionId)
    return { ...SECTIONS[CONNECTION_KEY[connectionId]].read, synced_at: connection.last_synced_at }
  }),
  propose: (connectionId, tables) => {
    if (tables) return refuse('Splitting a section is not available here.')
    return respond(() => {
      const connection = connectionOr404(connectionId)
      return { ...SECTIONS[CONNECTION_KEY[connectionId]].proposal, synced_at: connection.last_synced_at }
    })
  },
  save: () => refuse('Sections cannot be saved here.'),
  remove: () => refuse('Sections cannot be deleted here.'),
  clear: () => refuse('Sections cannot be cleared here.'),
}

/** The layer is not part of the demo; its tab is hidden (`SHOW_SEMANTIC_TAB`). */
const noLayer = () => refuse('The semantic layer is not part of this demo.')
export const semantic: typeof Real.semantic = {
  get: noLayer, saveDraft: noLayer, discardDraft: noLayer, publish: noLayer, save: noLayer, diff: noLayer,
  versions: noLayer, changes: noLayer, restore: noLayer, history: noLayer, remove: noLayer, generate: noLayer,
  job: noLayer, cancelJob: noLayer, version: noLayer, exportFile: noLayer, importFile: noLayer,
  metricUse: noLayer, attention: noLayer, check: noLayer,
}

// ── knowledge ────────────────────────────────────────────────────────────────
function health(): KnowledgeHealth {
  return { total: 0, stale: [], conflicted: [], unused: [], conflict_checks_enabled: false, unused_after_days: 60 }
}

/** Flags raised in this session, in the curator's queue — the loop, closing. */
function reviewsFor(connectionId: string): Review[] {
  return state.runs
    .filter((run) => run.connectionId === connectionId && run.feedback && run.feedback.verdict !== 'CORRECT')
    .map((run) => {
      const turn = conversationOf(run.conversationId)?.turns.find((t) => t.messageId === run.userMessageId)
      return {
        id: run.feedback!.id,
        run_id: run.id,
        verdict: run.feedback!.verdict as Review['verdict'],
        comment: run.feedback!.comment,
        state: 'OPEN',
        created_at: run.feedback!.created_at,
        question: turn?.question ?? '',
        sql: lastSqlOf(run),
        flagged_by: displayName,
      }
    })
}

function templateCheck(sql: string, accept: string[] | undefined): TemplateCheckResult {
  const recorded = ANSWERS.find((a) => a.template_check && a.template_check.sql.trim() === sql.trim())
  const key = [...(accept ?? [])].sort().join(',')
  const answer = recorded?.template_check?.answers[key]
  if (answer) return answer
  // An edited statement: say so rather than pretend to have parsed it.
  return {
    valid: false,
    issue: 'The demo can only check the statements it recorded — edits are not parsed here.',
    issues: [],
    referenced_tables: [],
    proposals: recorded?.template_check?.answers['']?.proposals ?? [],
    sql,
    params: [],
    question_slots: [],
  }
}

export const knowledge: typeof Real.knowledge = {
  reviews: (connectionId, reviewState = 'OPEN') => respond(() => (reviewState === 'OPEN' ? reviewsFor(connectionId) : [])),
  resolve: () => refuse('Flags cannot be resolved here.'),
  suggestions: () => respond([]),
  list: () => respond({
    templates: [], schema_version: SNAPSHOT_VERSION, schema_synced: true, can_curate: true, stale_ids: [], health: health(),
  }),
  capabilities: () => respond({ can_curate: true }),
  health: () => respond(health),
  revalidate: () => refuse('The store cannot be swept here.'),
  embeddings: (connectionId) => respond(() => ({
    enabled: false, model: '', dimension: 0, templates: 0, indexed: 0,
    schema_tables: SNAPSHOTS[CONNECTION_KEY[connectionId] ?? 'sales'].tables.length, schema_tables_indexed: 0,
    message: '', embedder: null, pin: 'NO_EMBEDDER' as const, serves_model: '',
  })),
  setEmbeddings: () => refuse('Embedding search is not available here.'),
  check: (_connectionId, payload) => respond(() => templateCheck(payload.sql, payload.accept), 260),
  create: () => refuse('Templates cannot be saved here.'),
  update: () => refuse('Templates cannot be edited here.'),
  benchmarks: () => respond({ sets: [], can_curate: true, candidates: 0, min_set_size: 5 }),
  benchmarkCandidates: () => respond([]),
  createBenchmark: () => refuse('Benchmark sets cannot be created here.'),
  deleteBenchmark: () => refuse('Benchmark sets cannot be deleted here.'),
  runBenchmark: () => refuse('Benchmarks cannot be run here.'),
  benchmarkResults: () => respond([]),
  archive: () => refuse('Templates cannot be archived here.'),
  restore: () => refuse('Templates cannot be restored here.'),
}

// ── model providers ──────────────────────────────────────────────────────────
export const llmConfigs: typeof Real.llmConfigs = {
  list: (purpose) => respond(() => (purpose === 'embedding' ? LLM_CONFIGS.filter((m) => m.embedding_model) : LLM_CONFIGS)),
  create: () => refuse('Providers cannot be added here.'),
  update: () => refuse('Providers cannot be edited here.'),
  remove: () => refuse('Providers cannot be deleted here.'),
  test: () => refuse('No model is called in the demo.'),
  testDraft: () => refuse('No model is called in the demo.'),
  parameters: () => respond(CATALOG.parameters),
}

// ── conversations ────────────────────────────────────────────────────────────
function normalise(text: string): string {
  return text
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[‌‍‎‏]/g, ' ')
    .replace(/[?؟!.,،;:'"`’“”()]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

const SUGGESTED: Record<'sales' | 'sakila', string[]> = {
  sales: ['revenue-trend', 'top-products', 'region-revenue', 'category-fa', 'sales-tables', 'sales-joins'],
  sakila: ['sakila-categories', 'sakila-actors', 'sakila-tables', 'sakila-joins'],
}

function findAnswer(content: string, connectionKey: 'sales' | 'sakila') {
  const wanted = normalise(content)
  const matches = ANSWERS.filter((a) => [a.question, ...a.aliases].some((q) => normalise(q) === wanted))
  return {
    here: matches.find((a) => a.connection === connectionKey) ?? null,
    elsewhere: matches.find((a) => a.connection !== connectionKey) ?? null,
  }
}

function findDeep(content: string, connectionKey: 'sales' | 'sakila') {
  const wanted = normalise(content)
  return DEEP.find((d) => d.connection === connectionKey
    && [d.question, ...d.aliases].some((q) => normalise(q) === wanted)) ?? null
}

/** The deep analyses recorded on one connection, as a list. */
function deepList(key: 'sales' | 'sakila'): string {
  return DEEP.filter((d) => d.connection === key).map((d) => `• ${d.question}`).join('\n')
}

const CONNECTION_NAME: Record<'sales' | 'sakila', string> = { sales: 'Sales warehouse', sakila: 'Sakila DVD rental' }

/** The demo's own answer to a question it has no recording of. */
function fallbackText(key: 'sales' | 'sakila', elsewhere: boolean): string {
  const list = SUGGESTED[key].map((id) => `• ${ANSWERS_BY_ID.get(id)!.question}`).join('\n')
  const other = key === 'sales' ? 'sakila' : 'sales'
  if (elsewhere) {
    return `**Demo mode** — that question is recorded on **${CONNECTION_NAME[other]}**, not on this database. `
      + `Start a new chat and choose ${CONNECTION_NAME[other]} to ask it.\n\nOn **${CONNECTION_NAME[key]}** you can ask:\n${list}`
  }
  return '**Demo mode** — this demo has no model or live database behind it, so it can only answer the '
    + 'questions it was recorded with, and that is not one of them.\n\n'
    + `On **${CONNECTION_NAME[key]}** you can ask:\n${list}\n\n`
    + 'The four starters on a new chat work too, and so does '
    + `**${CONNECTION_NAME[other]}** with its own questions. The suggestions below ask one for you.`
}

/**
 * The demo's own answer in **Deep**, to a question with no recorded analysis.
 * A deep run is a plan of several real queries, so only the questions it was
 * recorded with can be shown; the rest are one switch away, in Quick.
 */
function deepFallbackText(key: 'sales' | 'sakila', quick: boolean): string {
  const recorded = deepList(key)
  const where = recorded
    ? `On **${CONNECTION_NAME[key]}** a deep analysis is recorded for:\n${recorded}`
    : `No deep analysis is recorded on **${CONNECTION_NAME[key]}**. Start a new chat on `
      + `**${CONNECTION_NAME[key === 'sales' ? 'sakila' : 'sales']}** to watch one:\n`
      + deepList(key === 'sales' ? 'sakila' : 'sales')
  const lead = quick
    ? '**Demo mode** — that question is recorded as a quick answer. Switch the composer to **Quick** to see it.'
    : '**Demo mode** — this demo has no model or live database behind it, so a deep analysis can only be '
      + 'shown for the questions it was recorded with, and that is not one of them.'
  return `${lead}\n\n${where}\n\nEverything else this demo can answer is in **Quick**.`
}

/** The demo's own answer in **Quick**, to a question recorded only as a deep analysis. */
function quickFallbackText(): string {
  return '**Demo mode** — that is a *why* question, and it is recorded as a deep analysis: a plan of '
    + 'several queries, each checked and computed, and an answer that cites the step behind every sentence. '
    + 'Switch the composer to **Deep** and ask it again to watch it work.'
}

export const conversations: typeof Real.conversations = {
  list: () => respond(() => [...state.conversations]
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .map(summaryOf)),
  create: (payload) => respond((): ConversationSummary => {
    const now = demoNow()
    const conversation = {
      id: newId(),
      title: payload.title || 'New chat',
      connectionId: payload.connection_id ?? CONNECTIONS[0].id,
      llmConfigId: payload.llm_config_id ?? LLM_CONFIGS[0].id,
      createdAt: now,
      updatedAt: now,
      turns: [],
    }
    state.conversations.push(conversation)
    save()
    return summaryOf(conversation)
  }),
  update: (id, payload) => respond(() => {
    const conversation = conversationOf(id)
    if (!conversation) throw notFound('Conversation')
    if (typeof payload.title === 'string' && payload.title.trim()) conversation.title = payload.title.trim()
    save()
    return summaryOf(conversation)
  }),
  remove: (id) => respond(() => {
    state.conversations = state.conversations.filter((c) => c.id !== id)
    state.runs = state.runs.filter((r) => r.conversationId !== id)
    save()
    return undefined
  }),
  messages: (id) => respond(() => {
    const conversation = conversationOf(id)
    if (!conversation) throw notFound('Conversation')
    return messagesOf(conversation)
  }),
  send: (id, payload) => respond(() => {
    const conversation = conversationOf(id)
    if (!conversation) throw notFound('Conversation')
    const connectionId = payload.connection_id ?? conversation.connectionId
    const key = CONNECTION_KEY[connectionId] ?? 'sales'
    const depth = payload.depth === 'DEEP' ? 'DEEP' : 'QUICK'
    const { here, elsewhere } = findAnswer(payload.content, key)
    const deep = findDeep(payload.content, key)
    // Each mode answers only what it was recorded with; the other mode's
    // recording is named rather than played under the wrong label.
    const answer = depth === 'QUICK' ? here : null
    const analysis = depth === 'DEEP' ? deep : null
    const fallback = answer || analysis ? null
      : depth === 'DEEP' ? deepFallbackText(key, Boolean(here))
        : deep ? quickFallbackText() : fallbackText(key, Boolean(elsewhere))
    const now = demoNow()
    const turn = { messageId: newId(), question: payload.content, askedAt: now, runIds: [] as string[] }
    const run: StoredRun = {
      id: newId(),
      conversationId: id,
      userMessageId: turn.messageId,
      assistantMessageId: newId(),
      depth,
      answerId: answer?.id ?? null,
      deepId: analysis?.id ?? null,
      answerNowAt: null,
      fallback,
      connectionId,
      llmConfigId: payload.llm_config_id ?? conversation.llmConfigId,
      startedAt: now,
      cancelledAt: null,
      feedback: null,
      overridden: false,
    }
    turn.runIds.push(run.id)
    conversation.turns.push(turn)
    if (conversation.title === 'New chat' || !conversation.title) conversation.title = titleOf(payload.content)
    conversation.updatedAt = now
    state.runs.push(run)
    save()
    const timeline = timelineOf(run)
    if ((answer || analysis) && timeline.promptTokens !== null) {
      const model = LLM_CONFIGS.find((m) => m.id === run.llmConfigId) ?? LLM_CONFIGS[0]
      recordOperation({
        at: now, actorId: me(), actor: displayName, model: model.model,
        prompt: timeline.promptTokens, completion: timeline.completionTokens ?? 0,
      })
    }
    return { run_id: run.id, message_id: turn.messageId }
  }),
  suggestions: (id) => respond(() => {
    const conversation = conversationOf(id)
    if (!conversation) return { suggestions: [] }
    const key = CONNECTION_KEY[conversation.connectionId] ?? 'sales'
    const asked = askedIn(conversation)
    const last = lastRunIn(conversation)
    // A thread in Deep is offered what Deep can answer: the other recorded
    // analyses. A quick chip there would only be answered with a note.
    if (last?.depth === 'DEEP') {
      const deeper = DEEP.filter((d) => d.connection === key && !asked.has(d.id))
      return { suggestions: deeper.slice(0, 4).map((d) => d.question) }
    }
    const answer = last ? answerOf(last) : null
    const pool = answer ? [...answer.followups, ...SUGGESTED[key]] : SUGGESTED[key]
    const ids = [...new Set(pool)].filter((q) => !asked.has(q) && ANSWERS_BY_ID.get(q)?.connection === key)
    return { suggestions: ids.slice(0, 4).map((q) => ANSWERS_BY_ID.get(q)!.question) }
  }, SUGGESTIONS_MS),
}

// ── SQL drafts, dashboards, reports ───────────────────────────────────────────
export const sqlDrafts: typeof Real.sqlDrafts = {
  draft: () => refuse('No model is called in the demo, so no SQL can be drafted.'),
  validate: () => refuse('Statements cannot be checked against a live database here.'),
}

export const dashboards: typeof Real.dashboards = {
  list: () => respond([]),
  create: () => refuse('Dashboards cannot be created here.'),
  get: () => Promise.reject(notFound('Dashboard')),
  update: () => refuse('Dashboards cannot be edited here.'),
  remove: () => refuse('Dashboards cannot be deleted here.'),
  addTile: () => refuse('Tiles cannot be added here.'),
  updateTile: () => refuse('Tiles cannot be edited here.'),
  removeTile: () => refuse('Tiles cannot be deleted here.'),
  duplicateTile: () => refuse('Tiles cannot be duplicated here.'),
  setLayout: () => refuse('Layouts cannot be saved here.'),
  exportDocument: () => Promise.reject(notFound('Dashboard')),
  importDocument: () => refuse('Dashboards cannot be imported here.'),
  data: () => respond({ results: {} }),
  tileData: () => Promise.reject(notFound('Tile')),
}

export const reports: typeof Real.reports = {
  list: () => respond([]),
  create: () => refuse('Reports cannot be created here.'),
  get: () => Promise.reject(notFound('Report')),
  update: () => refuse('Reports cannot be edited here.'),
  remove: () => refuse('Reports cannot be deleted here.'),
  proposeOutline: () => refuse('No model is called in the demo.'),
  addSection: () => refuse('Reports cannot be edited here.'),
  updateSection: () => refuse('Reports cannot be edited here.'),
  removeSection: () => refuse('Reports cannot be edited here.'),
  addBlock: () => refuse('Reports cannot be edited here.'),
  updateBlock: () => refuse('Reports cannot be edited here.'),
  removeBlock: () => refuse('Reports cannot be edited here.'),
  checkBlock: () => refuse('Statements cannot be checked against a live database here.'),
  editBlockSql: () => refuse('Reports cannot be edited here.'),
  startRun: () => refuse('Reports cannot be generated here.'),
  runs: () => respond([]),
  run: () => Promise.reject(notFound('Report run')),
  cancelRun: () => refuse('Reports cannot be generated here.'),
  retrySection: () => refuse('Reports cannot be generated here.'),
  editProse: () => refuse('Reports cannot be edited here.'),
  redrawBlockChart: () => refuse('Reports cannot be edited here.'),
}

// ── runs ─────────────────────────────────────────────────────────────────────
function runOr404(id: string): StoredRun {
  const run = runOf(id)
  if (!run) throw notFound('Run')
  return run
}

/** Events already due, numbered the way the stream numbers them. */
function eventsDue(run: StoredRun): RunEvent[] {
  const timeline = timelineOf(run)
  const elapsed = (run.cancelledAt ?? demoNow()) - run.startedAt
  const out: RunEvent[] = []
  timeline.events.forEach((event, i) => {
    if (event.at <= elapsed) out.push({ seq: i + 1, type: event.type, data: event.data })
  })
  if (run.cancelledAt !== null && run.cancelledAt - run.startedAt < timeline.total) {
    out.push({ seq: timeline.events.length + 1, type: 'RUN_FINISHED', data: { status: 'CANCELLED' } })
  }
  return out
}

export const runs: typeof Real.runs = {
  get: (id) => respond(() => detailOf(runOr404(id))),
  cancel: (id) => respond(() => {
    const run = runOr404(id)
    const running = detailOf(run).status === 'RUNNING'
    if (running) {
      run.cancelledAt = demoNow()
      save()
    }
    return { cancelled: running }
  }),
  answerNow: (id) => respond(() => {
    const run = runOr404(id)
    // Honoured on the loop's next edge; a run that is not a deep one in
    // flight has no edge left to honour it on, as the real 202 would find.
    if (!deepOf(run) || stateOf(run) !== 'RUNNING' || run.answerNowAt !== null) return { requested: false }
    run.answerNowAt = demoNow()
    save()
    return { requested: true }
  }),
  plan: (id) => respond(() => {
    const run = runOr404(id)
    const detail = detailOf(run)
    const finished = detail.status !== 'RUNNING'
    if (!deepOf(run)) return { depth: run.depth, status: detail.status, finished, restricted: false, plan: null }
    const analysis = (timelineOf(run) as DeepTimeline).analysis
    if (finished) return { depth: 'DEEP', status: detail.status, finished, restricted: false, ...analysis }
    // In flight: what the events so far say, as `services/deep_plan.py` folds them.
    const due = eventsDue(run)
    const proposed = due.find((e) => e.type === 'PLAN_PROPOSED')?.data ?? null
    const revisions = due.filter((e) => e.type === 'PLAN_REVISED').map((e) => e.data)
    return {
      depth: 'DEEP', status: detail.status, finished, restricted: false,
      plan: proposed && {
        restatement: proposed.restatement, stop_when: proposed.stop_when,
        steps: (proposed.steps as unknown[]).map((step, i) => [...revisions].reverse().find((r) => r.index === i)?.by ?? step),
      },
      revisions,
      steps: due.filter((e) => e.type === 'STEP_EVIDENCE').map((e) => e.data),
      stop_reason: '',
      claims: [],
      traceable: null,
      budget: [...due].reverse().find((e) => e.type === 'BUDGET_SPENT')?.data ?? null,
    }
  }),
  retry: (id) => respond(() => {
    const previous = runOr404(id)
    const conversation = conversationOf(previous.conversationId)!
    const turn = conversation.turns.find((t) => t.messageId === previous.userMessageId)!
    const run: StoredRun = {
      ...previous, id: newId(), assistantMessageId: newId(), startedAt: demoNow(), cancelledAt: null,
      answerNowAt: null, feedback: null,
    }
    turn.runIds.push(run.id)
    conversation.updatedAt = run.startedAt
    state.runs.push(run)
    save()
    return { run_id: run.id, message_id: turn.messageId }
  }),
  override: (id) => respond(() => {
    const run = runOr404(id)
    run.overridden = true
    save()
    return detailOf(run).knowledge
  }),
  feedback: (id, payload) => respond((): AnswerFeedback => {
    const run = runOr404(id)
    const connection = CONNECTIONS.find((c) => c.id === run.connectionId)
    run.feedback = {
      id: newId(),
      run_id: run.id,
      verdict: payload.verdict as AnswerFeedback['verdict'],
      comment: payload.comment ?? '',
      state: payload.verdict === 'CORRECT' ? 'RESOLVED' : 'OPEN',
      resolution_note: '',
      became_template: null,
      resolved_at: null,
      created_at: new Date(demoNow()).toISOString(),
      routed_to: connection?.owner ?? '',
    }
    save()
    return run.feedback
  }),
  artifact: (id) => respond(() => {
    for (const run of state.runs) {
      const artifact = detailOf(run).artifacts.find((a) => a.id === id)
      if (artifact) return { id: artifact.id, kind: artifact.kind, spec: artifact.spec }
    }
    throw notFound('Artifact')
  }),
  poll: (id, after) => respond(() => eventsDue(runOr404(id)).filter((e) => e.seq > after)),
  redrawChart: (id, chartType) => respond((): ChartRedraw => {
    const charted = chartedOf(runOr404(id))
    if (!charted?.result) throw notFound('Result')
    const drawn = charted.redraws[chartType]
    return {
      spec: drawn?.spec ?? null,
      chart_type: drawn?.chart_type ?? 'none',
      reason: drawn ? drawn.reason : 'This result cannot be drawn that way.',
      options: charted.options,
    }
  }, 220),
}

/**
 * Stream a run's events — played from its script rather than read from a
 * socket. The same signature and the same ending as the real one: every event
 * through `onEvent`, then `onDone` once, and a function that stops it.
 */
export function streamRun(
  runId: string,
  handlers: { onEvent: (event: RunEvent) => void; onDone: () => void; onError?: (error: Error) => void },
): () => void {
  const run = runOf(runId)
  if (!run) {
    const timer = window.setTimeout(() => handlers.onDone(), REQUEST_MS)
    return () => window.clearTimeout(timer)
  }
  return play(timelineOf(run), run.startedAt, demoNow, handlers, () => runOf(runId)?.cancelledAt ?? null)
}
