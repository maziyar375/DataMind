/**
 * `<AccessPanel>` — who can reach this, and one way to change it.
 *
 * **One component for every resource type**, mounted with the resource's own
 * API path: `connections/{id}`, `connections/{id}/knowledge`,
 * `connections/{id}/semantic`, and from Phase 8 reports, dashboards, model
 * configurations and conversations. Eight copies of a share dialog is eight
 * places for the privilege list to drift from what the server will accept, and
 * the only way anybody finds out is a Save that 403s after a form was filled
 * in.
 *
 * Four things here are deliberate, and each is the plan's §21.4 as an
 * interaction rather than a paragraph:
 *
 * * **The privilege radio is rendered from `GET …/actions`, not from a list in
 *   this file.** The server sends the five privileges *and the sentence each
 *   means on this resource type*, from the same `PRIVILEGE_MEANINGS` table a
 *   403 quotes. So the label beside a radio button and the refusal somebody
 *   would get are the same words, and adding a resource type needs no change
 *   here.
 * * **Every row shows the path.** *"Sara — select"* is a fact nobody can act on
 *   or verify. *"Sara — select · via the Finance team"* tells them the revoke
 *   they want is on the team, and that clicking revoke on this row would do
 *   nothing. Ownership is shown as a path too, without a revoke control,
 *   because ownership is not a grant — it is moved by transferring.
 * * **Teams are offered first and said to be the better answer.** A permission
 *   attached to a job survives the person leaving it; one attached to a person
 *   becomes a row nobody can attribute and nobody dares revoke. That is the
 *   whole reason teams shipped a phase before grants.
 * * **The panel renders nothing but a sentence when the viewer lacks `manage`.**
 *   Not a disabled form: a control somebody cannot use teaches them less than
 *   a sentence saying who can.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { access, teams as teamsApi, users as usersApi } from '../api/client'
import type { Actions, Grant, Team, User } from '../api/types'
import {
  Chip, DangerButton, ErrorNote, GhostButton, Icon, Modal, PrimaryButton,
  SearchField, Select, Spinner, initialOf,
} from './ui'

/** The five, in lattice order — the order the radio renders them in. */
const ORDER = ['describe', 'select', 'modify', 'delete', 'manage']

/** How a path reads on a row. `owner` is not a grant and says so. */
const PATH_LABEL: Record<string, string> = {
  owner: 'owner',
  direct: 'directly',
  team: 'via this team',
}

interface Principal {
  id: string
  name: string
  kind: 'HUMAN' | 'SERVICE' | 'TEAM'
  hint?: string
}

export function AccessPanel({
  base, title, description, onChanged,
}: {
  /**
   * The resource's path, **without** a leading slash and without `/api/v1`:
   * `connections/{id}`, `connections/{id}/knowledge`. The same string the
   * backend mounts these routes under, which is what makes one panel serve
   * every type.
   */
  base: string
  title?: string
  description?: string
  /** Called after any change, so a parent showing a share count can re-read. */
  onChanged?: () => void
}) {
  const [grants, setGrants] = useState<Grant[] | null>(null)
  const [actions, setActions] = useState<Actions | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sharing, setSharing] = useState(false)
  const [revoking, setRevoking] = useState<Grant | null>(null)

  const reload = useCallback(async () => {
    const next = await access.grants(base)
    setGrants(next)
    onChanged?.()
    return next
  }, [base, onChanged])

  useEffect(() => {
    let cancelled = false
    access.actions(base)
      .then((next) => !cancelled && setActions(next))
      .catch(() => !cancelled && setActions(null))
    return () => {
      cancelled = true
    }
  }, [base])

  const mayShare = actions?.can?.share ?? false

  useEffect(() => {
    if (!mayShare) {
      setGrants([])
      return
    }
    let cancelled = false
    access.grants(base)
      .then((next) => !cancelled && setGrants(next))
      .catch(() => {
        if (cancelled) return
        setError('Could not load who has access.')
        setGrants([])
      })
    return () => {
      cancelled = true
    }
  }, [base, mayShare])

  if (actions === null) {
    return <Spinner />
  }

  if (!mayShare) {
    // Not a disabled form. Somebody who cannot share this can still be told
    // who to ask, and a greyed-out picker tells them nothing.
    const owner = grants?.find((g) => g.path === 'owner')
    return (
      <p style={{ margin: 0, fontSize: 12.5, color: 'var(--text-dim)', lineHeight: 1.6 }}>
        You can see this, and you cannot change who else can — that needs the{' '}
        <strong>manage</strong> permission.{' '}
        {owner ? `Ask ${owner.principal_name}, who owns it.` : 'Ask its owner.'}
      </p>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {error && <ErrorNote>{error}</ErrorNote>}
      {description && (
        <p style={{ margin: 0, fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.55 }}>
          {description}
        </p>
      )}

      {grants === null ? (
        <Spinner />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {grants.map((grant) => (
            <GrantRow
              key={grant.id ?? `owner-${grant.principal_id}`}
              grant={grant}
              onRevoke={() => setRevoking(grant)}
            />
          ))}
        </div>
      )}

      <div>
        <PrimaryButton onClick={() => setSharing(true)}>
          <Icon.Plus size={14} />
          Give access
        </PrimaryButton>
      </div>

      {sharing && actions && (
        <ShareModal
          base={base}
          title={title}
          meanings={actions.meanings}
          existing={grants ?? []}
          onClose={() => setSharing(false)}
          onShared={async () => {
            setSharing(false)
            setError(null)
            await reload()
          }}
        />
      )}

      {revoking && (
        <RevokeModal
          base={base}
          grant={revoking}
          onClose={() => setRevoking(null)}
          onRevoked={async () => {
            setRevoking(null)
            setError(null)
            await reload()
          }}
        />
      )}
    </div>
  )
}

function GrantRow({ grant, onRevoke }: { grant: Grant; onRevoke: () => void }) {
  const isOwner = grant.path === 'owner'
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '8px 12px',
        borderRadius: 10,
        border: '1px solid var(--border)',
        background: 'var(--panel)',
      }}
    >
      <span
        aria-hidden
        style={{
          width: 24,
          height: 24,
          flexShrink: 0,
          borderRadius: grant.principal_kind === 'TEAM' ? 7 : 20,
          display: 'grid',
          placeItems: 'center',
          background: 'var(--panel-alt)',
          color: 'var(--text-dim)',
          fontSize: 10.5,
          fontWeight: 700,
        }}
      >
        {grant.principal_kind === 'TEAM' ? (
          <Icon.Users size={12} />
        ) : (
          initialOf(grant.principal_name)
        )}
      </span>

      <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)' }}>
          {grant.principal_name}
        </span>
        {/* The path. Without it a row is a fact nobody can act on: revoking
            here would not touch a permission that arrives through a team. */}
        <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
          {grant.privilege} · {PATH_LABEL[grant.path] ?? grant.path}
        </span>
      </span>

      {grant.principal_kind === 'SERVICE' && <Chip tone="accent">Service</Chip>}
      {grant.principal_kind === 'TEAM' && <Chip tone="accent">Team</Chip>}

      {isOwner ? (
        // Ownership is not a grant and cannot be revoked — only transferred.
        // Saying so beats a disabled button with no explanation.
        <Chip tone="green">Owner</Chip>
      ) : (
        <GhostButton onClick={onRevoke} title="Remove this access">
          <Icon.Close size={13} />
          Revoke
        </GhostButton>
      )}
    </div>
  )
}

function ShareModal({
  base, title, meanings, existing, onClose, onShared,
}: {
  base: string
  title?: string
  meanings: Record<string, string>
  existing: Grant[]
  onClose: () => void
  onShared: () => void
}) {
  const [principals, setPrincipals] = useState<Principal[] | null>(null)
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<Principal | null>(null)
  const [privilege, setPrivilege] = useState('select')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      usersApi.list().catch(() => [] as User[]),
      teamsApi.list().catch(() => [] as Team[]),
    ]).then(([people, teams]) => {
      if (cancelled) return
      setPrincipals([
        // Teams first, and the panel says why: a permission attached to a job
        // survives the person leaving it.
        ...teams.map<Principal>((team) => ({
          id: team.id,
          name: team.name,
          kind: 'TEAM',
          hint: `${team.members} ${team.members === 1 ? 'person' : 'people'}`,
        })),
        ...people.map<Principal>((person) => ({
          id: person.id,
          name: person.display_name || person.email,
          kind: person.kind === 'SERVICE' ? 'SERVICE' : 'HUMAN',
          hint: person.kind === 'SERVICE' ? 'service account' : person.email,
        })),
      ])
    })
    return () => {
      cancelled = true
    }
  }, [])

  const already = useMemo(
    () => new Set(existing.map((grant) => grant.principal_id)),
    [existing],
  )

  const candidates = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const pool = (principals ?? []).filter((p) => !already.has(p.id))
    if (!needle) return pool
    return pool.filter((p) => p.name.toLowerCase().includes(needle))
  }, [principals, query, already])

  /**
   * Which privileges this type may be given.
   *
   * Straight from `meanings`, whose keys are the server's own enum — so a type
   * whose share surface should be narrower (a model configuration, where
   * `modify` is equivalent to disclosing the API key) narrows on the server and
   * this follows without a second rule here.
   */
  const offered = ORDER.filter((name) => name in meanings)

  async function submit() {
    if (!picked) return
    setBusy(true)
    setError(null)
    try {
      await access.grant(base, {
        privilege,
        ...(picked.kind === 'TEAM' ? { team_id: picked.id } : { user_id: picked.id }),
      })
      onShared()
    } catch (err) {
      // The server's refusals are the sentences worth showing: the
      // key-equivalent privilege on a model config, the wildcard needing
      // `role.manage`, and the 403 naming the privilege.
      setError(err instanceof Error ? err.message : 'Could not share this.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title={title ? `Give access to “${title}”` : 'Give access'}
      onClose={onClose}
      width={520}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton disabled={!picked || busy} onClick={submit}>
            {busy ? <Spinner /> : <Icon.Check size={15} />}
            Give access
          </PrimaryButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {error && <ErrorNote>{error}</ErrorNote>}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <SearchField
            value={query}
            onChange={setQuery}
            ariaLabel="Search people and teams"
            placeholder="Search people and teams…"
          />
          {principals === null ? (
            <Spinner />
          ) : (
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
                maxHeight: 200,
                overflowY: 'auto',
              }}
            >
              {candidates.map((principal) => (
                <button
                  key={`${principal.kind}-${principal.id}`}
                  type="button"
                  onClick={() => setPicked(principal)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    padding: '7px 10px',
                    borderRadius: 9,
                    textAlign: 'left',
                    border: '1px solid var(--border)',
                    background:
                      picked?.id === principal.id ? 'var(--accent-bg)' : 'var(--panel)',
                    color: 'var(--text-strong)',
                    cursor: 'pointer',
                  }}
                >
                  <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
                    <span style={{ fontSize: 12.5, fontWeight: 600 }}>
                      {principal.name}
                    </span>
                    <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
                      {principal.hint}
                    </span>
                  </span>
                  {principal.kind === 'TEAM' && <Chip tone="accent">Team</Chip>}
                  {principal.kind === 'SERVICE' && <Chip tone="accent">Service</Chip>}
                </button>
              ))}
              {candidates.length === 0 && (
                <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>
                  Nobody left to give access to.
                </span>
              )}
            </div>
          )}
          {/* Said once, where the decision is being made. */}
          <p style={{ margin: 0, fontSize: 11, color: 'var(--text-faint)', lineHeight: 1.5 }}>
            <strong style={{ color: 'var(--text-dim)' }}>Prefer a team.</strong>{' '}
            A permission given to a job survives the person leaving it; one given
            to a person becomes a row nobody can attribute a year from now.
          </p>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--text-dim)' }}>
            What they may do
          </span>
          {/* Rendered from the server's own table, so the label here and the
              sentence in a 403 are the same words. */}
          {offered.map((name) => (
            <label
              key={name}
              style={{
                display: 'flex',
                gap: 9,
                alignItems: 'flex-start',
                padding: '7px 10px',
                borderRadius: 9,
                border: '1px solid var(--border)',
                background: privilege === name ? 'var(--accent-bg)' : 'var(--panel)',
                cursor: 'pointer',
              }}
            >
              <input
                type="radio"
                name="privilege"
                checked={privilege === name}
                onChange={() => setPrivilege(name)}
                style={{ accentColor: 'var(--accent)', marginTop: 2 }}
              />
              <span style={{ minWidth: 0 }}>
                <span
                  className="mono"
                  style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-strong)' }}
                >
                  {name}
                </span>
                <span
                  style={{
                    display: 'block',
                    fontSize: 11,
                    color: 'var(--text-faint)',
                    lineHeight: 1.45,
                  }}
                >
                  {meanings[name]}
                </span>
              </span>
            </label>
          ))}
        </div>
      </div>
    </Modal>
  )
}

function RevokeModal({
  base, grant, onClose, onRevoked,
}: {
  base: string
  grant: Grant
  onClose: () => void
  onRevoked: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    if (!grant.id) return
    setBusy(true)
    setError(null)
    try {
      await access.revoke(base, grant.id)
      onRevoked()
    } catch (err) {
      // The last-manager refusal lands here, and it names the two ways out.
      setError(err instanceof Error ? err.message : 'Could not revoke this.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title={`Remove ${grant.principal_name}’s access?`}
      onClose={onClose}
      width={440}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <DangerButton disabled={busy} onClick={submit}>
            {busy ? <Spinner /> : <Icon.Close size={14} />}
            Revoke
          </DangerButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {error && <ErrorNote>{error}</ErrorNote>}
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)', lineHeight: 1.55 }}>
          {grant.principal_kind === 'TEAM'
            ? `Everybody in ${grant.principal_name} loses this on their next request.`
            : `${grant.principal_name} loses this on their next request — not in fifteen minutes.`}{' '}
          Anything they built with it stays theirs.
        </p>
      </div>
    </Modal>
  )
}

/**
 * The transfer control. Separate from the panel because it is a different act.
 *
 * Sharing adds somebody; transferring hands the thing over and the previous
 * owner keeps nothing. Putting them side by side in one dialog would make the
 * second look like a stronger version of the first.
 */
/**
 * The one-line answer a header shows without opening the panel.
 *
 * "Only you" / "Shared with 3 people and 1 team". A header that said nothing
 * would put *"is this shared?"* one click away from every screen, and that is
 * the question somebody asks before they paste a number into an email.
 *
 * Counts **principals**, not rows: somebody granted both `select` and `manage`
 * is one person who can reach this, and saying "2 people" would be a number
 * nobody could reconcile with the list underneath it.
 */
export function accessSummary(grants: Grant[] | null): string {
  if (!grants) return ''
  const shares = grants.filter((g) => g.path !== 'owner')
  if (shares.length === 0) return 'Only you'
  const people = new Set(
    shares.filter((g) => g.path !== 'team').map((g) => g.principal_id),
  ).size
  const teams = new Set(
    shares.filter((g) => g.path === 'team').map((g) => g.principal_id),
  ).size
  const parts: string[] = []
  if (people) parts.push(`${people} ${people === 1 ? 'person' : 'people'}`)
  if (teams) parts.push(`${teams} ${teams === 1 ? 'team' : 'teams'}`)
  return `Shared with ${parts.join(' and ')}`
}


export function TransferControl({
  base, title, onTransferred,
}: {
  base: string
  title: string
  onTransferred: () => void
}) {
  const [open, setOpen] = useState(false)
  const [people, setPeople] = useState<User[]>([])
  const [to, setTo] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    usersApi.list().then(setPeople).catch(() => setPeople([]))
  }, [open])

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      await access.transfer(base, to)
      setOpen(false)
      onTransferred()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not transfer this.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <GhostButton onClick={() => setOpen(true)}>
        <Icon.ArrowRight size={14} />
        Transfer ownership
      </GhostButton>
      {open && (
        <Modal
          title={`Transfer “${title}”`}
          onClose={() => setOpen(false)}
          width={460}
          footer={
            <>
              <GhostButton onClick={() => setOpen(false)}>Cancel</GhostButton>
              <PrimaryButton disabled={!to || busy} onClick={submit}>
                {busy ? <Spinner /> : <Icon.ArrowRight size={15} />}
                Transfer
              </PrimaryButton>
            </>
          }
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {error && <ErrorNote>{error}</ErrorNote>}
            <Select
              aria-label="New owner"
              value={to}
              onChange={(event) => setTo(event.target.value)}
            >
              <option value="">Choose the new owner…</option>
              {people
                .filter((person) => person.status !== 'DISABLED')
                .map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.display_name || person.email}
                    {person.kind === 'SERVICE' ? ' (service account)' : ''}
                  </option>
                ))}
            </Select>
            <p style={{ margin: 0, fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.55 }}>
              You keep <strong>nothing</strong>. If you should still be able to
              reach this afterwards, give yourself access first — one row
              somebody can see, rather than a residue of a transfer nobody
              remembers.
            </p>
          </div>
        </Modal>
      )}
    </>
  )
}


/**
 * The Access control for a header that has no room for a panel.
 *
 * The Knowledge console and the Semantic tab live *inside* a connection's
 * detail screen, so neither can afford a fifth tab of its own — but both are
 * separately grantable resources, and that is the whole of requirement 2: a
 * Knowledge Manager may be given `(knowledge, manage)` on a connection whose
 * data they cannot read. A button that opens the same `<AccessPanel>` in a
 * modal is how those two surfaces get a share control without a second
 * navigation level.
 *
 * It **renders nothing when the viewer cannot share**, rather than rendering a
 * disabled button: on these headers a greyed control would sit beside a screen
 * the person can otherwise use fully, and it would read as "this is broken"
 * rather than "this is not yours". The panel behind it is refused by the
 * server regardless — `manage` on all five calls — so hiding it is an
 * affordance, never the boundary.
 */
export function AccessPopover({
  base, resourceLabel,
}: {
  base: string
  /** What to call this thing in the button's title and the modal's heading. */
  resourceLabel: string
}) {
  const [open, setOpen] = useState(false)
  const [actions, setActions] = useState<Actions | null>(null)
  const [grants, setGrants] = useState<Grant[] | null>(null)

  // `…/actions` decides whether the button exists at all, and it is the same
  // answer the API will check on the next request. The summary beside it is a
  // second call, made only when the button is going to be shown.
  useEffect(() => {
    let cancelled = false
    access.actions(base)
      .then((next) => !cancelled && setActions(next))
      .catch(() => !cancelled && setActions(null))
    return () => {
      cancelled = true
    }
  }, [base])

  useEffect(() => {
    if (!actions?.can.share) return
    let cancelled = false
    access.grants(base)
      .then((rows) => !cancelled && setGrants(rows))
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [base, actions])

  if (!actions?.can.share) return null

  return (
    <>
      <GhostButton onClick={() => setOpen(true)} title={`Who can reach ${resourceLabel}`}>
        <Icon.Users size={14} />
        {accessSummary(grants) || 'Access'}
      </GhostButton>
      {open && (
        <Modal
          title={`Access to ${resourceLabel}`}
          onClose={() => setOpen(false)}
          width={620}
          footer={<GhostButton onClick={() => setOpen(false)}>Done</GhostButton>}
        >
          <AccessPanel base={base} />
        </Modal>
      )}
    </>
  )
}
