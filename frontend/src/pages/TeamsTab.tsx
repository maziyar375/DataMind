/**
 * Teams: who is in one, what it carries, and where its members inherit it.
 *
 * The research behind this plan is unanimous on one point, and it is why this
 * screen ships before any resource can be shared: **per-user grants do not
 * survive contact with staff turnover.** A workspace where every share was made
 * to a person accumulates permissions nobody can attribute and nobody dares
 * revoke — forty rows, forty names, and no way to tell which five describe a
 * job that still exists. So the first share anybody makes can already be made
 * to a team.
 *
 * Three things on this screen are deliberate:
 *
 * * **The members picker sends the whole intended membership**, not a list of
 *   changes. What somebody is looking at *is* the answer; a client that had to
 *   diff two lists is a client that can re-add the person another
 *   administrator just removed. The server takes the difference and writes one
 *   audit row per person who actually moved.
 * * **Roles a team holds are shown beside its members**, because that pairing
 *   is the feature: assigning the BI Engineer role here gives every member
 *   `dashboard.create` on their next request, with no sign-out.
 * * **The external binding has its own control and its own explanation.**
 *   Rebinding redirects which people flow into a set of permissions; putting
 *   it in the name-and-description form would make it look like a rename.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { roles as rolesApi, teams as api, users as usersApi } from '../api/client'
import type { Role, Team, User } from '../api/types'
import {
  Chip, DangerButton, ErrorNote, Field, GhostButton, GlyphBadge, Icon, Modal,
  PrimaryButton, SearchField, Select, Spinner, TextArea, TextInput, initialOf,
} from '../components/ui'
import {
  DetailBody, DetailHeader, MasterColumn, MasterItem, Section,
} from '../components/settings'
import { useCan } from '../permissions'

export default function TeamsTab() {
  const can = useCan()
  const mayManage = can('team.manage')

  const [list, setList] = useState<Team[] | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [removing, setRemoving] = useState<Team | null>(null)

  const refresh = useCallback(async () => {
    const rows = await api.list()
    setList(rows)
    return rows
  }, [])

  useEffect(() => {
    refresh().catch(() => {
      setError('Could not load teams.')
      setList([])
    })
  }, [refresh])

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const rows = list ?? []
    if (!needle) return rows
    return rows.filter(
      (team) =>
        team.name.toLowerCase().includes(needle)
        || team.description.toLowerCase().includes(needle),
    )
  }, [list, query])

  const selected = useMemo(
    () => (list ?? []).find((team) => team.id === selectedId) ?? null,
    [list, selectedId],
  )

  useEffect(() => {
    if (selectedId === null && list && list.length > 0) setSelectedId(list[0].id)
  }, [list, selectedId])

  return (
    <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
      <MasterColumn
        title="Teams"
        icon={<Icon.Users size={15} />}
        note="A team is how a permission survives somebody changing jobs. Grant to the team, not to the person."
        count={(list ?? []).length}
        onNew={mayManage ? () => setCreating(true) : undefined}
        newLabel="New team"
        empty="No teams yet"
        query={query}
        onQuery={setQuery}
        loading={list === null}
      >
        {visible.map((team) => (
          <MasterItem
            key={team.id}
            title={team.name}
            subtitle={`${team.members} ${team.members === 1 ? 'person' : 'people'}${
              team.provider_id ? ` · ${team.provider_id}` : ''
            }`}
            active={team.id === selectedId}
            tone={team.members > 0 ? 'green' : 'neutral'}
            toneLabel={team.members > 0 ? 'Has members' : 'Empty'}
            glyph={
              <GlyphBadge size={26}>
                <Icon.Users size={13} />
              </GlyphBadge>
            }
            onClick={() => setSelectedId(team.id)}
          />
        ))}
      </MasterColumn>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        {error && (
          <div style={{ padding: '16px 28px 0' }}>
            <ErrorNote>{error}</ErrorNote>
          </div>
        )}
        {list === null ? (
          <div style={{ display: 'grid', placeItems: 'center', flex: 1 }}>
            <Spinner size={18} />
          </div>
        ) : list.length === 0 ? (
          <div style={{ padding: '40px 28px', maxWidth: 560 }}>
            <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)', lineHeight: 1.6 }}>
              A team is a named set of people that can hold roles — and, once
              resources can be shared, the thing to share them with. Making one
              now means the first share does not have to be made to a person
              who might change jobs.
            </p>
          </div>
        ) : selected === null ? null : (
          <TeamDetail
            key={selected.id}
            team={selected}
            editable={mayManage}
            onChanged={(next) =>
              setList((current) =>
                (current ?? []).map((row) => (row.id === next.id ? next : row)),
              )
            }
            onRemove={() => setRemoving(selected)}
          />
        )}
      </div>

      {creating && (
        <NewTeamModal
          onClose={() => setCreating(false)}
          onCreated={async (created) => {
            setCreating(false)
            await refresh()
            setSelectedId(created.id)
          }}
        />
      )}

      {removing && (
        <RemoveTeamModal
          team={removing}
          onClose={() => setRemoving(null)}
          onRemoved={async () => {
            setRemoving(null)
            setSelectedId(null)
            await refresh()
          }}
        />
      )}
    </div>
  )
}

// ── one team ──────────────────────────────────────────────────────────────
function TeamDetail({
  team, editable, onChanged, onRemove,
}: {
  team: Team
  editable: boolean
  onChanged: (team: Team) => void
  onRemove: () => void
}) {
  const [name, setName] = useState(team.name)
  const [description, setDescription] = useState(team.description)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [members, setMembers] = useState<User[] | null>(null)
  const [everyone, setEveryone] = useState<User[]>([])
  const [heldRoles, setHeldRoles] = useState<string[]>(team.roles)
  const [allRoles, setAllRoles] = useState<Role[]>([])
  const [teamRoles, setTeamRoles] = useState<Role[] | null>(null)
  const [picked, setPicked] = useState('')
  const [busy, setBusy] = useState(false)

  const can = useCan()
  const dirty = name !== team.name || description !== team.description

  const reload = useCallback(async () => {
    const [fresh, people] = await Promise.all([api.get(team.id), api.members(team.id)])
    setMembers(people)
    setHeldRoles(fresh.roles)
    onChanged(fresh)
  }, [team.id, onChanged])

  useEffect(() => {
    let cancelled = false
    // Re-read the team as well as its members. The list already carries the
    // roles, so this is belt and braces rather than the only source — but a
    // detail pane that trusted a row somebody else had just changed is how a
    // screen shows a permission that is no longer there.
    api.get(team.id)
      .then((fresh) => !cancelled && setHeldRoles(fresh.roles))
      .catch(() => undefined)
    api.members(team.id)
      .then((rows) => !cancelled && setMembers(rows))
      .catch(() => !cancelled && setMembers([]))
    if (can('user.read')) {
      usersApi.list().then((rows) => !cancelled && setEveryone(rows)).catch(() => undefined)
    }
    if (can('role.read')) {
      rolesApi.list().then((rows) => !cancelled && setAllRoles(rows)).catch(() => undefined)
    }
    return () => {
      cancelled = true
    }
  }, [team.id, can])

  // The team's own roles, resolved from the names the API returned against the
  // full list — so removing one has an id to send without a second endpoint.
  useEffect(() => {
    setTeamRoles(allRoles.filter((role) => heldRoles.includes(role.name)))
  }, [allRoles, heldRoles])

  async function save() {
    setSaving(true)
    setError(null)
    try {
      onChanged(await api.update(team.id, { name, description }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save this team.')
    } finally {
      setSaving(false)
    }
  }

  async function addRole(roleId: string) {
    setBusy(true)
    setError(null)
    try {
      const updated = await api.assignRole(team.id, roleId)
      setHeldRoles(updated.roles)
      onChanged(updated)
      setPicked('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not assign that role.')
    } finally {
      setBusy(false)
    }
  }

  async function dropRole(roleId: string) {
    setBusy(true)
    setError(null)
    try {
      await api.unassignRole(team.id, roleId)
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove that role.')
    } finally {
      setBusy(false)
    }
  }

  const available = allRoles.filter((role) => !heldRoles.includes(role.name))

  return (
    <>
      <DetailHeader
        glyph={
          <GlyphBadge size={40}>
            <Icon.Users size={19} />
          </GlyphBadge>
        }
        title={team.name}
        subtitle={`${team.members} ${team.members === 1 ? 'person' : 'people'}${
          heldRoles.length ? ` · ${heldRoles.join(', ')}` : ' · no roles yet'
        }`}
        chips={
          team.provider_id ? (
            <Chip tone="accent">{`${team.provider_id} · ${team.source_id}`}</Chip>
          ) : undefined
        }
        actions={
          <>
            {editable && (
              <PrimaryButton disabled={!dirty || saving} onClick={save}>
                {saving ? <Spinner /> : <Icon.Check size={15} />}
                Save
              </PrimaryButton>
            )}
            {editable && (
              <GhostButton
                onClick={onRemove}
                disabled={heldRoles.length > 0}
                title={
                  heldRoles.length > 0
                    ? 'This team still holds a role'
                    : 'Delete this team'
                }
              >
                <Icon.Trash size={14} />
                Delete
              </GhostButton>
            )}
          </>
        }
      />

      <DetailBody>
        {error && <ErrorNote>{error}</ErrorNote>}

        <Section
          title="Identity"
          description="What this team is called, and what it is for. Say which job it describes — that is what makes a permission attributable a year from now."
          icon={<Icon.Key size={13} />}
        >
          <Field label="Name">
            <TextInput
              value={name}
              disabled={!editable}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field label="Description">
            <TextArea
              rows={2}
              value={description}
              disabled={!editable}
              onChange={(event) => setDescription(event.target.value)}
            />
          </Field>
        </Section>

        <Section
          title="Roles"
          description="Every member holds these, from their next request — no sign-out. This is how a permission attaches to a job rather than to a person."
          icon={<Icon.Shield size={13} />}
        >
          {heldRoles.length === 0 ? (
            <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>
              This team carries no roles, so being in it grants nothing yet.
            </span>
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {(teamRoles ?? []).map((role) => (
                <span
                  key={role.id}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    padding: '4px 6px 4px 10px',
                    borderRadius: 20,
                    fontSize: 11.5,
                    fontWeight: 600,
                    color: 'var(--text-strong)',
                    background: 'var(--accent-bg)',
                    border: '1px solid var(--border)',
                  }}
                >
                  {role.name}
                  {editable && (
                    <button
                      type="button"
                      onClick={() => dropRole(role.id)}
                      disabled={busy}
                      aria-label={`Remove ${role.name}`}
                      className="rm-icon-btn"
                      style={{
                        display: 'flex',
                        padding: 2,
                        border: 'none',
                        borderRadius: 20,
                        background: 'transparent',
                        color: 'var(--text-faint)',
                        cursor: 'pointer',
                      }}
                    >
                      <Icon.Close size={11} />
                    </button>
                  )}
                </span>
              ))}
            </div>
          )}
          {editable && available.length > 0 && (
            <div style={{ display: 'flex', gap: 8 }}>
              <Select
                aria-label="Add a role to this team"
                value={picked}
                disabled={busy}
                onChange={(event) => setPicked(event.target.value)}
                style={{ flex: 1 }}
              >
                <option value="">Add a role…</option>
                {available.map((role) => (
                  <option key={role.id} value={role.id}>
                    {role.name}
                  </option>
                ))}
              </Select>
              <PrimaryButton disabled={!picked || busy} onClick={() => addRole(picked)}>
                {busy ? <Spinner /> : <Icon.Plus size={14} />}
                Add
              </PrimaryButton>
            </div>
          )}
        </Section>

        <MembersSection
          teamId={team.id}
          members={members}
          everyone={everyone}
          editable={editable}
          onSaved={reload}
        />

        {editable && (
          <SourceSection
            team={team}
            onBound={(next) => onChanged(next)}
          />
        )}
      </DetailBody>
    </>
  )
}

/**
 * The membership picker: a search, a checklist, and one Save.
 *
 * Deliberately not a row of add/remove buttons that each fire a request. The
 * intended membership is a *set*, somebody edits it as one, and sending it as
 * one is what makes "I meant to swap Ali for Sara" a single decision instead
 * of two moments where the team is briefly wrong.
 */
function MembersSection({
  teamId, members, everyone, editable, onSaved,
}: {
  teamId: string
  members: User[] | null
  everyone: User[]
  editable: boolean
  onSaved: () => Promise<void>
}) {
  const [chosen, setChosen] = useState<Set<string> | null>(null)
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (members) setChosen(new Set(members.map((member) => member.id)))
  }, [members])

  const original = useMemo(
    () => new Set((members ?? []).map((member) => member.id)),
    [members],
  )
  const dirty =
    chosen !== null
    && (chosen.size !== original.size || [...chosen].some((id) => !original.has(id)))

  const candidates = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const pool = everyone.length > 0 ? everyone : (members ?? [])
    if (!needle) return pool
    return pool.filter(
      (person) =>
        person.display_name.toLowerCase().includes(needle)
        || person.email.toLowerCase().includes(needle),
    )
  }, [everyone, members, query])

  async function save() {
    if (!chosen) return
    setBusy(true)
    setError(null)
    try {
      await api.setMembers(teamId, [...chosen])
      await onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the membership.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section
      title="Members"
      description="Who is in this team. Everything the team holds reaches these people on their next request, and stops reaching them the moment they leave."
      icon={<Icon.Users size={13} />}
    >
      {error && <ErrorNote>{error}</ErrorNote>}
      {members === null ? (
        <Spinner />
      ) : (
        <>
          {everyone.length > 6 && (
            <SearchField
              value={query}
              onChange={setQuery}
              ariaLabel="Search people"
              placeholder="Search people…"
            />
          )}
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
              maxHeight: 280,
              overflowY: 'auto',
            }}
          >
            {candidates.map((person) => {
              const inTeam = chosen?.has(person.id) ?? false
              return (
                <label
                  key={person.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    padding: '7px 10px',
                    borderRadius: 9,
                    border: '1px solid var(--border)',
                    background: inTeam ? 'var(--accent-bg)' : 'var(--panel)',
                    cursor: editable ? 'pointer' : 'default',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={inTeam}
                    disabled={!editable}
                    onChange={() =>
                      setChosen((current) => {
                        const next = new Set(current ?? [])
                        if (next.has(person.id)) next.delete(person.id)
                        else next.add(person.id)
                        return next
                      })
                    }
                    style={{ accentColor: 'var(--accent)' }}
                  />
                  <span
                    aria-hidden
                    style={{
                      width: 24,
                      height: 24,
                      borderRadius: 7,
                      display: 'grid',
                      placeItems: 'center',
                      background: 'var(--panel-alt)',
                      color: 'var(--text-dim)',
                      fontSize: 10.5,
                      fontWeight: 700,
                      flexShrink: 0,
                    }}
                  >
                    {initialOf(person.display_name || person.email)}
                  </span>
                  <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                    <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)' }}>
                      {person.display_name || person.email}
                    </span>
                    <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
                      {person.email}
                    </span>
                  </span>
                </label>
              )
            })}
            {candidates.length === 0 && (
              <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>
                Nobody matches.
              </span>
            )}
          </div>
          {editable && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <PrimaryButton disabled={!dirty || busy} onClick={save}>
                {busy ? <Spinner /> : <Icon.Check size={15} />}
                Save membership
              </PrimaryButton>
              {dirty && (
                <span style={{ fontSize: 11.5, color: 'var(--text-dim)' }}>
                  {chosen?.size ?? 0} selected — was {original.size}
                </span>
              )}
            </div>
          )}
        </>
      )}
    </Section>
  )
}

/**
 * The external binding, with the sentence that makes it not a rename.
 *
 * Inert today — nothing reads these columns until an OIDC adapter exists — and
 * the section says so rather than implying a feature. What it buys is that
 * binding an existing team later is two column updates instead of a namespace
 * retrofitted onto identifiers every grant already points at.
 */
function SourceSection({
  team, onBound,
}: {
  team: Team
  onBound: (team: Team) => void
}) {
  const [provider, setProvider] = useState(team.provider_id ?? '')
  const [source, setSource] = useState(team.source_id ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const dirty =
    provider !== (team.provider_id ?? '') || source !== (team.source_id ?? '')

  async function save() {
    setBusy(true)
    setError(null)
    try {
      onBound(await api.bindSource(team.id, provider || null, source || null))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change the binding.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section
      title="External group"
      description="Mirror this team from an identity provider. Both fields or neither. Nothing reads them yet — there is no directory connected — and recording the pairing now is what makes connecting one later a two-line change rather than a migration."
      icon={<Icon.Link size={13} />}
    >
      {error && <ErrorNote>{error}</ErrorNote>}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 12 }}>
        <Field label="Provider">
          <TextInput
            value={provider}
            placeholder="oidc"
            onChange={(event) => setProvider(event.target.value)}
          />
        </Field>
        <Field label="Group">
          <TextInput
            value={source}
            placeholder="/analytics"
            onChange={(event) => setSource(event.target.value)}
          />
        </Field>
      </div>
      <div>
        <PrimaryButton disabled={!dirty || busy} onClick={save}>
          {busy ? <Spinner /> : <Icon.Link size={14} />}
          {provider || source ? 'Bind to this group' : 'Remove the binding'}
        </PrimaryButton>
      </div>
    </Section>
  )
}

// ── create and delete ─────────────────────────────────────────────────────
function NewTeamModal({
  onClose, onCreated,
}: {
  onClose: () => void
  onCreated: (team: Team) => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      onCreated(await api.create({ name: name.trim(), description: description.trim() }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create this team.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title="New team"
      onClose={onClose}
      width={460}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton disabled={!name.trim() || busy} onClick={submit}>
            {busy ? <Spinner /> : <Icon.Plus size={15} />}
            Create team
          </PrimaryButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {error && <ErrorNote>{error}</ErrorNote>}
        <Field label="Name">
          <TextInput
            autoFocus
            value={name}
            placeholder="BI Engineers"
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field
          label="Description"
          hint="Name the job, not the people — that is what keeps a permission attributable."
        >
          <TextArea
            rows={2}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
      </div>
    </Modal>
  )
}

function RemoveTeamModal({
  team, onClose, onRemoved,
}: {
  team: Team
  onClose: () => void
  onRemoved: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      await api.remove(team.id)
      onRemoved()
    } catch (err) {
      // Refused while the team holds a role, naming which. Shown verbatim —
      // it is the only sentence here somebody can act on.
      setError(err instanceof Error ? err.message : 'Could not delete this team.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title={`Delete “${team.name}”?`}
      onClose={onClose}
      width={440}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <DangerButton disabled={busy} onClick={submit}>
            {busy ? <Spinner /> : <Icon.Trash size={14} />}
            Delete team
          </DangerButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {error && <ErrorNote>{error}</ErrorNote>}
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)', lineHeight: 1.55 }}>
          {team.members > 0
            ? `${team.members} ${team.members === 1 ? 'person is' : 'people are'} in this team. Their accounts stay; they simply stop being members.`
            : 'Nobody is in this team.'}{' '}
          Nothing is reassigned to anybody else — this cannot be undone.
        </p>
      </div>
    </Modal>
  )
}
