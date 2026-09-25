/**
 * Who is in the demo, and what they have: people, roles, teams, a service
 * account, two data sources and two model providers.
 *
 * The company is **Lumen Supply Co.**, the electronics retailer whose
 * warehouse `scripts/warehouse.py` builds; the people here are its analytics
 * team. The roles are the eight the product seeds (migration `0024`, with
 * `0030` and `0042` on top), word for word — a demo that invented its own
 * role names would be showing a permission model the product does not have.
 */
import type {
  Connection, LlmConfig, Role, ServiceAccount, ServiceKey, Team, User,
} from '../../../src/api/types'
import { daysAgo } from '../clock'
import { SNAPSHOTS } from './schema.generated'

// ── ids ─────────────────────────────────────────────────────────────────────
// UUID-shaped, readable in a network tab, and stable across reloads so a deep
// link to a conversation keeps working.
const id = (group: number, n: number) =>
  `${group.toString(16).padStart(8, '0')}-0000-4000-8000-${n.toString(16).padStart(12, '0')}`

export const IDS = {
  users: {
    sam: id(1, 1), priya: id(1, 2), tomas: id(1, 3), leila: id(1, 4),
    chen: id(1, 5), olivia: id(1, 6), marcus: id(1, 7), hannah: id(1, 8), jonas: id(1, 9),
  },
  service: { digest: id(2, 1) },
  roles: {
    administrator: id(3, 1), normal: id(3, 2), viewer: id(3, 3), dataEngineer: id(3, 4),
    biEngineer: id(3, 5), knowledgeManager: id(3, 6), maintainer: id(3, 7), auditor: id(3, 8),
  },
  teams: { analytics: id(4, 1), finance: id(4, 2), revops: id(4, 3) },
  connections: { sales: id(5, 1), sakila: id(5, 2) },
  llm: { sonnet: id(6, 1), gpt: id(6, 2) },
  keys: { digest: id(7, 1), digestOld: id(7, 2) },
} as const

/** What the login page is pre-filled with. Any input signs in. */
export const DEMO_CREDENTIALS = { email: 'sam@lumen-supply.example', password: 'datamind-demo' }

// ── roles, as seeded ─────────────────────────────────────────────────────────
const ALL_ADMIN = [
  'user.read', 'user.manage', 'service_user.manage', 'team.read', 'team.manage', 'role.read',
  'role.manage', 'audit.read', 'access.review', 'connection.create', 'llm_config.create',
  'dashboard.create', 'report.create', 'conversation.create', 'settings.manage',
  'benchmark.manage', 'eval.run', 'system.maintenance',
  // 0030 and 0042.
  'usage.read', 'deep.run',
]

interface SeededRole {
  key: keyof typeof IDS.roles
  name: string
  description: string
  capabilities: string[]
  scoped: [string, string][]
}

const SEEDED: SeededRole[] = [
  {
    key: 'administrator', name: 'Administrator',
    description: 'Manages people, teams, roles and the system. Sees no data they have not been granted or granted themselves.',
    capabilities: ALL_ADMIN, scoped: [],
  },
  {
    key: 'normal', name: 'Normal User',
    description: 'Asks questions, builds their own dashboards and reports, and sees exactly what they own or have been given.',
    capabilities: ['dashboard.create', 'report.create', 'conversation.create', 'team.read'], scoped: [],
  },
  {
    key: 'viewer', name: 'Viewer',
    description: 'Consumes. Creates nothing. Every surface they see is a grant.',
    capabilities: ['team.read'], scoped: [],
  },
  {
    key: 'dataEngineer', name: 'Data Engineer',
    description: 'Owns how DataMind understands the schema. Can see that every connection exists and curate meaning on all of them — and still needs select to read any data.',
    capabilities: ['connection.create', 'conversation.create', 'team.read'],
    scoped: [['semantic_layer', 'manage'], ['connection', 'describe']],
  },
  {
    key: 'biEngineer', name: 'BI Engineer',
    description: 'Builds the artifacts. Their data reach comes from the team they are in, not from this role.',
    capabilities: ['dashboard.create', 'report.create', 'conversation.create', 'team.read'],
    scoped: [['dashboard', 'describe'], ['report', 'describe']],
  },
  {
    key: 'knowledgeManager', name: 'Knowledge Manager',
    description: 'Owns what the system has been taught, across every connection, without being able to edit a credential, change a disclosure policy, or read data they were not granted.',
    capabilities: ['conversation.create', 'benchmark.manage', 'team.read'],
    scoped: [['knowledge', 'manage'], ['connection', 'describe']],
  },
  {
    key: 'maintainer', name: 'DataMind Maintainer',
    description: 'Keeps the installation running. Deliberately not user.manage: administering people and administering the system are different jobs.',
    capabilities: ['llm_config.create', 'service_user.manage', 'settings.manage', 'eval.run', 'system.maintenance', 'benchmark.manage'],
    scoped: [['llm_config', 'describe']],
  },
  {
    key: 'auditor', name: 'Auditor',
    description: 'Can answer who can reach what, and who did what, for every resource — and can read none of the data in any of them.',
    capabilities: ['user.read', 'team.read', 'role.read', 'audit.read', 'access.review', 'usage.read'],
    scoped: [['connection', 'describe'], ['dashboard', 'describe'], ['report', 'describe']],
  },
]

// ── people ───────────────────────────────────────────────────────────────────
interface Person {
  key: keyof typeof IDS.users
  name: string
  email: string
  status: 'ACTIVE' | 'INVITED' | 'DISABLED'
  roles: (keyof typeof IDS.roles)[]
  teams: (keyof typeof IDS.teams)[]
  joined: number
}

const PEOPLE: Person[] = [
  { key: 'sam', name: 'Sam Rivera', email: DEMO_CREDENTIALS.email, status: 'ACTIVE', roles: ['administrator'], teams: ['analytics'], joined: 410 },
  { key: 'priya', name: 'Priya Nair', email: 'priya.nair@lumen-supply.example', status: 'ACTIVE', roles: ['dataEngineer'], teams: ['analytics'], joined: 388 },
  { key: 'tomas', name: 'Tomás Álvarez', email: 'tomas.alvarez@lumen-supply.example', status: 'ACTIVE', roles: ['biEngineer'], teams: ['analytics', 'revops'], joined: 301 },
  { key: 'leila', name: 'Leila Karimi', email: 'leila.karimi@lumen-supply.example', status: 'ACTIVE', roles: ['knowledgeManager', 'normal'], teams: ['analytics'], joined: 244 },
  { key: 'chen', name: 'Chen Wei', email: 'chen.wei@lumen-supply.example', status: 'ACTIVE', roles: ['normal'], teams: ['finance'], joined: 198 },
  { key: 'olivia', name: 'Olivia Bennett', email: 'olivia.bennett@lumen-supply.example', status: 'ACTIVE', roles: ['viewer'], teams: ['finance'], joined: 120 },
  { key: 'marcus', name: 'Marcus Johnson', email: 'marcus.johnson@lumen-supply.example', status: 'ACTIVE', roles: ['auditor'], teams: [], joined: 96 },
  { key: 'hannah', name: 'Hannah Schmidt', email: 'hannah.schmidt@lumen-supply.example', status: 'DISABLED', roles: ['normal'], teams: ['revops'], joined: 350 },
  { key: 'jonas', name: 'Jonas Berg', email: 'jonas.berg@lumen-supply.example', status: 'INVITED', roles: ['normal'], teams: [], joined: 2 },
]

const TEAM_DEFS: { key: keyof typeof IDS.teams; name: string; description: string; roles: (keyof typeof IDS.roles)[] }[] = [
  { key: 'analytics', name: 'Analytics', description: 'The data team: owns the connections and what they mean.', roles: [] },
  { key: 'finance', name: 'Finance', description: 'Month-end close and revenue reporting.', roles: [] },
  { key: 'revops', name: 'Revenue Operations', description: 'Pipeline, territories and quota.', roles: [] },
]

function capabilitiesOf(roleKeys: (keyof typeof IDS.roles)[]): string[] {
  const held = new Set<string>()
  for (const key of roleKeys) for (const c of SEEDED.find((r) => r.key === key)!.capabilities) held.add(c)
  return [...held].sort()
}

function rolesReaching(person: Person): (keyof typeof IDS.roles)[] {
  const viaTeams = person.teams.flatMap((t) => TEAM_DEFS.find((d) => d.key === t)!.roles)
  return [...new Set([...person.roles, ...viaTeams])]
}

export function userOf(person: Person, withPermissions = false): User {
  const reaching = rolesReaching(person)
  return {
    id: IDS.users[person.key],
    email: person.email,
    display_name: person.name,
    status: person.status,
    created_at: daysAgo(person.joined, 10),
    kind: 'HUMAN',
    roles: reaching.map((k) => SEEDED.find((r) => r.key === k)!.name),
    teams: person.teams.map((t) => TEAM_DEFS.find((d) => d.key === t)!.name),
    ...(withPermissions
      ? {
          capabilities: capabilitiesOf(reaching),
          // Deep analysis is built and shipped off: the installation does not
          // offer it, whatever a role holds. See docs/status.md §3.
          features: [],
        }
      : {}),
  }
}

export const DEMO_PERSON = PEOPLE[0]
export const USERS: User[] = PEOPLE.map((p) => userOf(p))
export const PEOPLE_BY_ID = new Map(PEOPLE.map((p) => [IDS.users[p.key], p]))

export function rolesForUser(userId: string): Role[] {
  const person = PEOPLE_BY_ID.get(userId)
  return person ? person.roles.map((k) => ROLES.find((r) => r.id === IDS.roles[k])!) : []
}

export function teamsForUser(userId: string): Team[] {
  const person = PEOPLE_BY_ID.get(userId)
  return person ? person.teams.map((k) => TEAMS.find((t) => t.id === IDS.teams[k])!) : []
}

export const ROLES: Role[] = SEEDED.map((role) => ({
  id: IDS.roles[role.key],
  name: role.name,
  description: role.description,
  is_system: true,
  capabilities: role.capabilities,
  scoped_privileges: role.scoped.map(([resource_type, privilege]) => ({ resource_type, privilege })),
  holders:
    PEOPLE.filter((p) => rolesReaching(p).includes(role.key)).length
    + (role.key === 'viewer' ? 1 : 0),
  created_at: daysAgo(420, 9),
}))

export const TEAMS: Team[] = TEAM_DEFS.map((team) => ({
  id: IDS.teams[team.key],
  name: team.name,
  description: team.description,
  members: PEOPLE.filter((p) => p.teams.includes(team.key)).length,
  roles: team.roles.map((k) => SEEDED.find((r) => r.key === k)!.name),
  provider_id: null,
  source_id: null,
  created_at: daysAgo(400, 11),
}))

export function teamMembers(teamId: string): User[] {
  const key = (Object.keys(IDS.teams) as (keyof typeof IDS.teams)[]).find((k) => IDS.teams[k] === teamId)
  return PEOPLE.filter((p) => key && p.teams.includes(key)).map((p) => userOf(p))
}

export const SERVICE_ACCOUNTS: ServiceAccount[] = [
  {
    id: IDS.service.digest,
    display_name: 'Revenue digest bot',
    description: 'Reads the Monday revenue figures for the leadership email. Viewer only.',
    status: 'ACTIVE',
    kind: 'SERVICE',
    email: 'svc-revenue-digest-7f3a@service.datamind.local',
    roles: ['Viewer'],
    teams: [],
    active_keys: 1,
    created_at: daysAgo(150, 14),
  },
]

export const SERVICE_KEYS: ServiceKey[] = [
  {
    id: IDS.keys.digest, name: 'production', prefix: 'dm_svc_7f3a',
    expires_at: daysAgo(-215, 0), last_used_at: daysAgo(4, 7), revoked_at: null, created_at: daysAgo(40, 15),
  },
  {
    id: IDS.keys.digestOld, name: 'first key', prefix: 'dm_svc_1c9e',
    expires_at: daysAgo(-150, 0), last_used_at: daysAgo(44, 7), revoked_at: daysAgo(40, 15), created_at: daysAgo(150, 14),
  },
]

// ── data sources ─────────────────────────────────────────────────────────────
const OWNER_PRIVILEGES = ['describe', 'select', 'modify', 'delete', 'manage']

export const CONNECTION_KEY: Record<string, 'sales' | 'sakila'> = {
  [IDS.connections.sales]: 'sales',
  [IDS.connections.sakila]: 'sakila',
}

/**
 * Two engines, two disclosure policies — so the header's badge differs between
 * a sales conversation and a Sakila one, and so does what the answer says:
 * under SAMPLE the model saw the rows and quotes them; under AGGREGATE it saw
 * only their shape, and the narrative says so instead of inventing figures.
 */
export const CONNECTIONS: Connection[] = [
  {
    id: IDS.connections.sales,
    name: 'Sales warehouse',
    database_type: 'postgres',
    host: 'pg-analytics.lumen.internal',
    port: 5432,
    database_name: 'sales',
    username: 'analytics_ro',
    ssl_mode: 'require',
    schema_allowlist: ['public'],
    max_rows: 1000,
    statement_timeout_ms: 30000,
    disclosure_policy: 'SAMPLE',
    semantic_layer_enabled: true,
    clarify_enabled: true,
    include_db_comments: true,
    conflict_checks_enabled: false,
    knowledge_examples_enabled: false,
    status: 'OK',
    readonly_confirmed: true,
    server_version: SNAPSHOTS.sales.server_version,
    last_tested_at: daysAgo(0, 9.2),
    last_synced_at: daysAgo(0, 9.25),
    privileges: OWNER_PRIVILEGES,
    owner: 'Sam Rivera',
  },
  {
    id: IDS.connections.sakila,
    name: 'Sakila DVD rental',
    database_type: 'mysql',
    host: 'mysql-legacy.lumen.internal',
    port: 3306,
    database_name: 'sakila',
    username: 'analytics_ro',
    ssl_mode: 'require',
    schema_allowlist: [],
    max_rows: 1000,
    statement_timeout_ms: 30000,
    disclosure_policy: 'AGGREGATE',
    semantic_layer_enabled: true,
    clarify_enabled: true,
    include_db_comments: true,
    conflict_checks_enabled: false,
    knowledge_examples_enabled: false,
    status: 'OK',
    readonly_confirmed: true,
    server_version: SNAPSHOTS.sakila.server_version,
    last_tested_at: daysAgo(2, 14.1),
    last_synced_at: daysAgo(2, 14.15),
    privileges: OWNER_PRIVILEGES,
    owner: 'Priya Nair',
  },
]

// ── model providers ──────────────────────────────────────────────────────────
/**
 * Two providers, keys stored and never sent: the API returns `has_api_key`
 * and nothing else, which is what the screen masks. Neither is called — every
 * answer in the demo is a recorded run.
 */
export const LLM_CONFIGS: LlmConfig[] = [
  {
    id: IDS.llm.sonnet,
    name: 'Claude Sonnet 5',
    provider: 'Anthropic',
    base_url: null,
    model: 'claude-sonnet-5',
    temperature: 0,
    max_tokens: 8192,
    params: {},
    embedding_model: '',
    embedding_params: {},
    status: 'OK',
    has_api_key: true,
    last_tested_at: daysAgo(1, 9),
    privileges: OWNER_PRIVILEGES,
    shared: false,
    owner_name: null,
  },
  {
    id: IDS.llm.gpt,
    name: 'GPT-4.1 mini (gateway)',
    provider: 'OpenAI-compatible',
    base_url: 'https://llm-gateway.lumen.internal/v1',
    model: 'gpt-4.1-mini',
    temperature: 0,
    max_tokens: 4096,
    params: { seed: 7 },
    embedding_model: '',
    embedding_params: {},
    status: 'OK',
    has_api_key: true,
    last_tested_at: daysAgo(6, 16),
    privileges: ['describe', 'select'],
    shared: true,
    owner_name: 'Priya Nair',
  },
]
