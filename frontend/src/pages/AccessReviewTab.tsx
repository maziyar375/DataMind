/**
 * The access review: *"what can Ali reach?"* and *"who can reach this?"*
 *
 * Two lenses over one set of facts, and a switch between them — not two
 * screens, because they answer one question from two directions and an
 * administrator moves between them mid-thought. §15.2's five facts arrive as
 * five path kinds, and **the path is the answer**: *"Reza — modify"* is
 * something nobody can act on, *"Reza — modify · role: BI Engineer"* tells
 * them the revoke they want is on the role and that removing his direct grant
 * would change nothing.
 *
 * Four decisions here, each one a way this screen could have been less useful:
 *
 * * **The path is a chip, and it is the leftmost thing after the name.** It is
 *   what the reader came for. A table that put the privilege first would sort
 *   by the thing everybody already knows.
 * * **Grouping is by resource, not by row.** Somebody can reach one dashboard
 *   three ways — owning it, a team grant, a role — and three flat rows make
 *   that look like three dashboards. Grouped, it reads as one thing with
 *   three reasons, which is what it is.
 * * **The CSV comes from the server.** The escaping rule that stops Excel
 *   *running* a display name beginning `=` lives in one place with a test on
 *   it; a second copy here would be a second chance to get it wrong.
 * * **Nothing on this screen is data.** A row names a resource and a
 *   privilege — never a host, a username or a stored statement. The endpoint
 *   enforces that; this file simply has nothing else to render.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { access, connections as connectionsApi, dashboards as dashboardsApi,
  reports as reportsApi, teams as teamsApi, users as usersApi } from '../api/client'
import type { Connection, DashboardSummary, Reach, ReportSummary, Team, User } from '../api/types'
import {
  Chip, EmptyState, ErrorNote, Field, GhostButton, Icon, Select,
  Spinner, initialOf, saveCsv,
} from '../components/ui'

type Lens = 'principal' | 'resource'

/** How each path reads, and what it means the reader should do about it. */
const PATH: Record<string, { label: string; tone: 'green' | 'accent' | 'neutral' }> = {
  owner: { label: 'owner', tone: 'green' },
  direct: { label: 'shared directly', tone: 'accent' },
  team: { label: 'via team', tone: 'accent' },
  role: { label: 'via role', tone: 'neutral' },
  wildcard: { label: 'wildcard grant', tone: 'neutral' },
}

/** The types a resource can be picked from, and what each is called. */
const TYPES: { value: string; label: string }[] = [
  { value: 'connection', label: 'Data sources' },
  { value: 'dashboard', label: 'Dashboards' },
  { value: 'report', label: 'Reports' },
]

export default function AccessReviewTab() {
  const [lens, setLens] = useState<Lens>('principal')
  const [rows, setRows] = useState<Reach[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const [principalId, setPrincipalId] = useState('')
  const [resourceType, setResourceType] = useState('connection')
  const [resourceId, setResourceId] = useState('')
  const [privilege, setPrivilege] = useState('')

  const [people, setPeople] = useState<User[]>([])
  const [teams, setTeams] = useState<Team[]>([])
  const [resources, setResources] = useState<{ id: string; name: string }[]>([])

  // Principals once; resources per type, because the three lists are three
  // endpoints and loading all of them to fill one dropdown would be three
  // requests for two answers nobody asked for.
  useEffect(() => {
    let cancelled = false
    usersApi.list().then((next) => !cancelled && setPeople(next)).catch(() => undefined)
    teamsApi.list().then((next) => !cancelled && setTeams(next)).catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    const load =
      resourceType === 'connection'
        ? connectionsApi.list().then((rows: Connection[]) =>
            rows.map((row) => ({ id: row.id, name: row.name })))
        : resourceType === 'dashboard'
        ? dashboardsApi.list().then((rows: DashboardSummary[]) =>
            rows.map((row) => ({ id: row.id, name: row.name })))
        : reportsApi.list().then((rows: ReportSummary[]) =>
            rows.map((row) => ({ id: row.id, name: row.name })))
    load
      .then((next) => {
        if (cancelled) return
        setResources(next)
        setResourceId((current) =>
          next.some((r) => r.id === current) ? current : next[0]?.id ?? '')
      })
      .catch(() => !cancelled && setResources([]))
    return () => {
      cancelled = true
    }
  }, [resourceType])

  const query = useMemo(
    () =>
      lens === 'principal'
        ? { principal_id: principalId, privilege: privilege || undefined }
        : {
            resource_type: resourceType,
            resource_id: resourceId,
            privilege: privilege || undefined,
          },
    [lens, principalId, privilege, resourceId, resourceType],
  )

  const ready = lens === 'principal' ? !!principalId : !!resourceId

  const load = useCallback(async () => {
    if (!ready) {
      setRows(null)
      return
    }
    setLoading(true)
    setError(null)
    try {
      setRows(await access.review(query))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not read the review.')
      setRows([])
    } finally {
      setLoading(false)
    }
  }, [query, ready])

  useEffect(() => {
    void load()
  }, [load])

  // Grouped by resource, so one thing reachable three ways reads as one thing.
  const groups = useMemo(() => {
    const out = new Map<string, { name: string; type: string; rows: Reach[] }>()
    for (const row of rows ?? []) {
      const key = `${row.resource_type}:${row.resource_id ?? '*'}`
      const group = out.get(key) ?? {
        name: row.resource_name, type: row.resource_type, rows: [],
      }
      group.rows.push(row)
      out.set(key, group)
    }
    return [...out.values()]
  }, [rows])

  const subject =
    lens === 'principal'
      ? [...teams, ...people].find((p) => p.id === principalId)
      : resources.find((r) => r.id === resourceId)

  async function download() {
    try {
      const text = await access.reviewCsv(query as Record<string, string | undefined>)
      const name = (subject as { name?: string; display_name?: string } | undefined)
      saveCsv(`access — ${name?.name ?? name?.display_name ?? 'review'}`, text)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That export did not work.')
    }
  }

  return (
    // See the note in `AuditTab`: the section owns the wash and the heading,
    // and this tab's own copy of each was drawing a second one of both.
    <div className="rm-page-pad" style={{ flex: 1, overflowY: 'auto' }}>
      {error && <ErrorNote>{error}</ErrorNote>}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
          gap: 12,
          marginBottom: 16,
        }}
      >
        <Field label="Look at">
          <Select value={lens} onChange={(e) => setLens(e.target.value as Lens)}>
            <option value="principal">A person, machine or team</option>
            <option value="resource">One thing</option>
          </Select>
        </Field>

        {lens === 'principal' ? (
          <Field label="Who">
            <Select
              value={principalId}
              onChange={(e) => setPrincipalId(e.target.value)}
            >
              <option value="">Choose…</option>
              {/* Teams first, and said to be the better answer everywhere
                  else in this product: a team is the principal a permission
                  should be attached to. */}
              <optgroup label="Teams">
                {teams.map((team) => (
                  <option key={team.id} value={team.id}>{team.name}</option>
                ))}
              </optgroup>
              <optgroup label="People and machines">
                {people.map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.display_name || person.email}
                  </option>
                ))}
              </optgroup>
            </Select>
          </Field>
        ) : (
          <>
            <Field label="Kind">
              <Select
                value={resourceType}
                onChange={(e) => setResourceType(e.target.value)}
              >
                {TYPES.map((type) => (
                  <option key={type.value} value={type.value}>{type.label}</option>
                ))}
              </Select>
            </Field>
            <Field label="Which">
              <Select value={resourceId} onChange={(e) => setResourceId(e.target.value)}>
                {resources.map((resource) => (
                  <option key={resource.id} value={resource.id}>{resource.name}</option>
                ))}
                {resources.length === 0 && <option value="">Nothing to review</option>}
              </Select>
            </Field>
          </>
        )}

        <Field label="Privilege">
          <Select value={privilege} onChange={(e) => setPrivilege(e.target.value)}>
            <option value="">Any</option>
            {['describe', 'select', 'modify', 'delete', 'manage'].map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </Select>
        </Field>
      </div>

      {!ready ? (
        <EmptyState
          icon={<Icon.Users size={20} />}
          title="Pick somebody, or something"
          body="This screen answers two questions: what one principal can reach, and who can reach one thing. Both are the same rows, read from different ends."
        />
      ) : loading && rows === null ? (
        <div style={{ display: 'grid', placeItems: 'center', padding: 40 }}>
          <Spinner size={18} />
        </div>
      ) : groups.length === 0 ? (
        <EmptyState
          icon={<Icon.Lock size={20} />}
          title="Nothing reaches"
          body={
            lens === 'principal'
              ? 'This principal owns nothing and has been granted nothing. They can still see anything a wildcard or a role reaches, and there is none of either.'
              : 'Nobody but its owner reaches this — and it has no owner, which is a state worth fixing.'
          }
        />
      ) : (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {groups.map((group) => (
              <div
                key={`${group.type}-${group.name}`}
                style={{
                  border: '1px solid var(--border)',
                  borderRadius: 12,
                  background: 'var(--panel)',
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '9px 13px',
                    borderBottom: '1px solid var(--border)',
                    background: 'var(--panel-alt)',
                  }}
                >
                  <span
                    style={{ fontSize: 12.5, fontWeight: 650, color: 'var(--text-strong)' }}
                  >
                    {group.name}
                  </span>
                  <Chip tone="neutral">{group.type.replace('_', ' ')}</Chip>
                  <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-faint)' }}>
                    {group.rows.length} {group.rows.length === 1 ? 'reason' : 'reasons'}
                  </span>
                </div>
                {group.rows.map((row, index) => (
                  <Row key={index} row={row} lens={lens} />
                ))}
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 14 }}>
            <GhostButton onClick={download}>
              <Icon.ArrowDown size={13} /> Export CSV
            </GhostButton>
            <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
              {(rows ?? []).length} {(rows ?? []).length === 1 ? 'row' : 'rows'} · reach
              only, never data
            </span>
          </div>
        </>
      )}
    </div>
  )
}

function Row({ row, lens }: { row: Reach; lens: Lens }) {
  const path = PATH[row.path] ?? { label: row.path, tone: 'neutral' as const }
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '8px 13px',
        borderTop: '1px solid var(--border)',
      }}
    >
      {/* In the by-resource lens the principal is the news; in the
          by-principal lens it is the same person on every row, so the path
          leads instead. */}
      {lens === 'resource' && (
        <>
          <span
            aria-hidden
            style={{
              width: 22,
              height: 22,
              borderRadius: row.principal_kind === 'TEAM' ? 7 : 20,
              display: 'grid',
              placeItems: 'center',
              background: 'var(--panel-alt)',
              color: 'var(--text-dim)',
              fontSize: 10,
              fontWeight: 700,
              flexShrink: 0,
            }}
          >
            {row.principal_kind === 'TEAM'
              ? <Icon.Users size={11} />
              : initialOf(row.principal_name)}
          </span>
          <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)' }}>
            {row.principal_name}
          </span>
        </>
      )}
      <Chip tone={path.tone}>
        {path.label}
        {row.via ? `: ${row.via}` : ''}
      </Chip>
      <code className="mono" style={{ fontSize: 11.5, color: 'var(--text-dim)' }}>
        {row.privilege}
      </code>
      {lens === 'principal' && row.principal_kind === 'TEAM' && (
        <Chip tone="accent">through a team you are in</Chip>
      )}
    </div>
  )
}
