/**
 * Service accounts: a machine identity, its roles, and its keys.
 *
 * A service account is a principal like any other — it holds roles, joins
 * teams, owns what it builds and appears in the audit log by name. What it
 * does not have is a way in: no password, no session, no profile. It
 * authenticates with an API key on every request.
 *
 * Four things on this screen are deliberate, and each is a decision the
 * backend also enforces — this is the affordance, never the boundary:
 *
 * * **Creating an identity and issuing a key are two acts.** The create form
 *   has no key field and the create response carries no key. Handing out a
 *   secret happens from the detail page, once at a time, so a provisioning
 *   script that makes an account does not end up with a credential in a file.
 * * **A key is shown exactly once**, in the same panel the one-time password
 *   uses on People — the same situation, so the same furniture.
 * * **Every key ever issued is listed**, revoked and expired ones included and
 *   struck through. *"This key existed, was last used in March, and was
 *   revoked on the 4th"* is the sentence an incident needs; a list that hid
 *   them answers none of it.
 * * **The role picker hides the four privileged capabilities and says why.**
 *   A leaked key must not be able to mint an administrator. The server refuses
 *   them regardless of what this screen offers, which is the rule: a hidden
 *   control always has a refusal behind it.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { roles as rolesApi, serviceAccounts as api } from '../api/client'
import type { Role, ServiceAccount, ServiceKey } from '../api/types'
import {
  Chip, DangerButton, ErrorNote, Field, GhostButton, GlyphBadge, Icon, Modal,
  PrimaryButton, SecretOncePanel, Select, Spinner, TextArea, TextInput,
  relativeTime,
} from '../components/ui'
import {
  DetailBody, DetailHeader, MasterColumn, MasterItem, Section,
} from '../components/settings'
import { useCan } from '../permissions'

/**
 * The four capabilities a leaked key must not reach.
 *
 * A copy of the backend's `PRIVILEGED_CAPABILITIES`, and the one place in this
 * SPA where a permission list is duplicated rather than fetched. It is a copy
 * because it decides only what a *picker* offers: the server refuses these
 * whatever this array says, so the worst a drift can do is show a role whose
 * assignment is then refused with a sentence naming the capability.
 */
const PRIVILEGED = [
  'user.manage',
  'role.manage',
  'service_user.manage',
  'settings.manage',
]

function isPrivileged(role: Role): boolean {
  return role.capabilities.some((capability) => PRIVILEGED.includes(capability))
}

/** Not revoked, not past its expiry — the only keys that can authenticate. */
function isLive(key: ServiceKey): boolean {
  if (key.revoked_at) return false
  return !key.expires_at || new Date(key.expires_at) > new Date()
}

export default function ServiceAccountsTab() {
  const can = useCan()
  const mayManage = can('service_user.manage')

  const [list, setList] = useState<ServiceAccount[] | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [removing, setRemoving] = useState<ServiceAccount | null>(null)

  const refresh = useCallback(async () => {
    const rows = await api.list()
    setList(rows)
    return rows
  }, [])

  useEffect(() => {
    refresh().catch(() => {
      setError('Could not load service accounts.')
      setList([])
    })
  }, [refresh])

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const rows = list ?? []
    if (!needle) return rows
    return rows.filter(
      (account) =>
        account.display_name.toLowerCase().includes(needle)
        || (account.description ?? '').toLowerCase().includes(needle),
    )
  }, [list, query])

  const selected = useMemo(
    () => (list ?? []).find((account) => account.id === selectedId) ?? null,
    [list, selectedId],
  )

  useEffect(() => {
    if (selectedId === null && list && list.length > 0) setSelectedId(list[0].id)
  }, [list, selectedId])

  return (
    <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
      <MasterColumn
        title="Service accounts"
        icon={<Icon.Server size={15} />}
        note="A machine identity: its own roles, its own keys, and no way to sign in."
        count={(list ?? []).length}
        onNew={mayManage ? () => setCreating(true) : undefined}
        newLabel="New service account"
        empty="No service accounts yet"
        query={query}
        onQuery={setQuery}
        loading={list === null}
      >
        {visible.map((account) => (
          <MasterItem
            key={account.id}
            title={account.display_name}
            subtitle={
              account.status === 'DISABLED'
                ? 'Disabled'
                : account.active_keys === 0
                  ? 'No live keys'
                  : `${account.active_keys} live ${account.active_keys === 1 ? 'key' : 'keys'}`
            }
            active={account.id === selectedId}
            // Green means "something is authenticating as this right now",
            // which is the fact somebody scanning this list is looking for.
            tone={
              account.status === 'DISABLED'
                ? 'red'
                : account.active_keys > 0
                  ? 'green'
                  : 'neutral'
            }
            toneLabel={
              account.status === 'DISABLED'
                ? 'Disabled'
                : account.active_keys > 0
                  ? 'Has a live key'
                  : 'No live key'
            }
            glyph={
              <GlyphBadge size={26}>
                <Icon.Server size={13} />
              </GlyphBadge>
            }
            onClick={() => setSelectedId(account.id)}
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
          <div style={{ padding: '40px 28px', maxWidth: 620 }}>
            <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)', lineHeight: 1.6 }}>
              A service account is how a script, a scheduler or an agent talks
              to DataMind. It is a principal in its own right: it holds roles,
              can be put in teams, owns what it creates, and shows up in the
              audit log by name rather than as somebody&rsquo;s borrowed
              password. Give it exactly the roles the integration needs — its
              reach is those roles, not the name you give it.
            </p>
          </div>
        ) : selected === null ? null : (
          <ServiceAccountDetail
            key={selected.id}
            account={selected}
            editable={mayManage}
            onChanged={(next) =>
              setList((current) =>
                (current ?? []).map((row) => (row.id === next.id ? next : row)),
              )
            }
            onRefresh={refresh}
            onRemove={() => setRemoving(selected)}
          />
        )}
      </div>

      {creating && (
        <NewServiceAccountModal
          onClose={() => setCreating(false)}
          onCreated={async (created) => {
            setCreating(false)
            await refresh()
            setSelectedId(created.id)
          }}
        />
      )}

      {removing && (
        <RemoveServiceAccountModal
          account={removing}
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

// ── one account ───────────────────────────────────────────────────────────
function ServiceAccountDetail({
  account, editable, onChanged, onRefresh, onRemove,
}: {
  account: ServiceAccount
  editable: boolean
  onChanged: (account: ServiceAccount) => void
  onRefresh: () => Promise<ServiceAccount[]>
  onRemove: () => void
}) {
  const can = useCan()
  const [name, setName] = useState(account.display_name)
  const [description, setDescription] = useState(account.description ?? '')
  const [saving, setSaving] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [allRoles, setAllRoles] = useState<Role[]>([])
  const [held, setHeld] = useState<string[]>(account.roles)
  const [picked, setPicked] = useState('')

  const dirty =
    name !== account.display_name || description !== (account.description ?? '')

  useEffect(() => {
    if (!can('role.read')) return
    let cancelled = false
    rolesApi.list().then((rows) => !cancelled && setAllRoles(rows)).catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [can])

  useEffect(() => {
    setHeld(account.roles)
  }, [account.roles])

  async function save() {
    setSaving(true)
    setError(null)
    try {
      onChanged(await api.update(account.id, { display_name: name, description }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save this account.')
    } finally {
      setSaving(false)
    }
  }

  async function setStatus(status: string) {
    setBusy(true)
    setError(null)
    try {
      onChanged(await api.update(account.id, { status }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change the status.')
    } finally {
      setBusy(false)
    }
  }

  async function addRole(roleId: string) {
    setBusy(true)
    setError(null)
    try {
      const updated = await api.assignRole(account.id, roleId)
      setHeld(updated.roles)
      onChanged(updated)
      setPicked('')
    } catch (err) {
      // The privileged-capability refusal lands here, naming the capability
      // and the setting that would allow it. Shown verbatim: it is the only
      // sentence on this screen somebody can act on.
      setError(err instanceof Error ? err.message : 'Could not assign that role.')
    } finally {
      setBusy(false)
    }
  }

  async function dropRole(roleId: string) {
    setBusy(true)
    setError(null)
    try {
      await api.unassignRole(account.id, roleId)
      onChanged(await api.get(account.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove that role.')
    } finally {
      setBusy(false)
    }
  }

  const heldRoles = allRoles.filter((role) => held.includes(role.name))
  const offerable = allRoles.filter(
    (role) => !held.includes(role.name) && !isPrivileged(role),
  )
  const hidden = allRoles.filter(
    (role) => !held.includes(role.name) && isPrivileged(role),
  )
  const disabled = account.status === 'DISABLED'

  return (
    <>
      <DetailHeader
        glyph={
          <GlyphBadge size={40}>
            <Icon.Server size={19} />
          </GlyphBadge>
        }
        title={account.display_name}
        subtitle={
          held.length ? held.join(', ') : 'No roles yet — this account can do nothing'
        }
        chips={
          <>
            <Chip tone="accent">Service</Chip>
            {disabled && <Chip tone="red">Disabled</Chip>}
          </>
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
                disabled={busy}
                onClick={() => setStatus(disabled ? 'ACTIVE' : 'DISABLED')}
                title={
                  disabled
                    ? 'Re-enable this account. Its keys start working again.'
                    : 'Stop every key working, without deleting anything'
                }
              >
                <Icon.Lock size={14} />
                {disabled ? 'Re-enable' : 'Disable'}
              </GhostButton>
            )}
            {editable && (
              <GhostButton onClick={onRemove} title="Delete this account and its keys">
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
          description="What this account is called, and what it is for. The purpose is required, because an undocumented machine identity is the one nobody is ever willing to delete."
          icon={<Icon.Key size={13} />}
        >
          <Field label="Name">
            <TextInput
              value={name}
              disabled={!editable}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field label="What is this for?">
            <TextArea
              rows={2}
              value={description}
              disabled={!editable}
              onChange={(event) => setDescription(event.target.value)}
            />
          </Field>
          <Field
            label="Address"
            hint="Generated and not deliverable. It exists so an audit row has something to join on; nothing sends mail here."
          >
            <TextInput value={account.email} disabled readOnly />
          </Field>
        </Section>

        <Section
          title="Roles"
          description="This account's entire reach. A service account with the BI Engineer role can do exactly what a person with it can do — the permissions are resolved by the same query from the same tables."
          icon={<Icon.Shield size={13} />}
        >
          {held.length === 0 ? (
            <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>
              No roles, so a key issued to this account can reach nothing.
            </span>
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {heldRoles.map((role) => (
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
          {editable && offerable.length > 0 && (
            <div style={{ display: 'flex', gap: 8 }}>
              <Select
                aria-label="Add a role to this service account"
                value={picked}
                disabled={busy}
                onChange={(event) => setPicked(event.target.value)}
                style={{ flex: 1 }}
              >
                <option value="">Add a role…</option>
                {offerable.map((role) => (
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
          {editable && hidden.length > 0 && <PrivilegedNote roles={hidden} />}
        </Section>

        <KeysSection
          account={account}
          editable={editable}
          onChanged={() => void onRefresh()}
        />
      </DetailBody>
    </>
  )
}

/**
 * Why four roles are missing from the picker — said, not silently omitted.
 *
 * A control that is simply absent teaches somebody the product is broken. A
 * control that is absent *with a reason* teaches them the rule, and the rule
 * here is one worth knowing: the blast radius of a leaked key must not include
 * changing who can sign in.
 */
function PrivilegedNote({ roles }: { roles: Role[] }) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 9,
        padding: '10px 12px',
        borderRadius: 10,
        border: '1px solid var(--border)',
        background: 'var(--panel)',
        fontSize: 11.5,
        lineHeight: 1.55,
        color: 'var(--text-dim)',
      }}
    >
      <span aria-hidden style={{ color: 'var(--text-faint)', flexShrink: 0 }}>
        <Icon.Lock size={13} />
      </span>
      <span>
        <strong style={{ color: 'var(--text-strong)' }}>
          {roles.map((role) => role.name).join(', ')}
        </strong>{' '}
        {roles.length === 1 ? 'is' : 'are'} not offered here. {roles.length === 1 ? 'It carries' : 'They carry'}{' '}
        at least one of <code className="mono">user.manage</code>,{' '}
        <code className="mono">role.manage</code>,{' '}
        <code className="mono">service_user.manage</code> or{' '}
        <code className="mono">settings.manage</code> — a leaked API key must
        not be able to change who can sign in. Set{' '}
        <code className="mono">ALLOW_PRIVILEGED_SERVICE_USERS</code> if an agent
        genuinely provisions accounts; the server refuses these until you do,
        and records it when you use it.
      </span>
    </div>
  )
}

// ── keys ──────────────────────────────────────────────────────────────────
/** A key unused for this long is flagged: it is how "can I delete this?" gets
 *  an answer that is not a guess. */
const STALE_DAYS = 90

function KeysSection({
  account, editable, onChanged,
}: {
  account: ServiceAccount
  editable: boolean
  onChanged: () => void
}) {
  const [keys, setKeys] = useState<ServiceKey[] | null>(null)
  const [issued, setIssued] = useState<{ token: string; name: string } | null>(null)
  const [issuing, setIssuing] = useState(false)
  const [revoking, setRevoking] = useState<ServiceKey | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    setKeys(await api.keys(account.id))
    onChanged()
  }, [account.id, onChanged])

  useEffect(() => {
    let cancelled = false
    api.keys(account.id)
      .then((rows) => !cancelled && setKeys(rows))
      .catch(() => !cancelled && setKeys([]))
    return () => {
      cancelled = true
    }
  }, [account.id])

  return (
    <Section
      title="Keys"
      description="How this account authenticates. Every key ever issued is listed, revoked ones included — “this key existed and was revoked on the 4th” is the sentence an incident needs."
      icon={<Icon.Key size={13} />}
    >
      {error && <ErrorNote>{error}</ErrorNote>}

      {issued && (
        <SecretOncePanel
          title={`API key for ${account.display_name} · ${issued.name}`}
          secret={issued.token}
          note="Copy this now — it is shown once and cannot be retrieved later. Send it with every request as an Authorization: Bearer header."
          onDismiss={() => setIssued(null)}
        />
      )}

      {keys === null ? (
        <Spinner />
      ) : keys.length === 0 ? (
        <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>
          No keys yet. Nothing can authenticate as this account.
        </span>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {keys.map((key) => (
            <KeyRow
              key={key.id}
              row={key}
              editable={editable}
              onRevoke={() => setRevoking(key)}
            />
          ))}
        </div>
      )}

      {editable && (
        <div>
          <PrimaryButton
            disabled={account.status === 'DISABLED'}
            title={
              account.status === 'DISABLED'
                ? 'Re-enable this account first — a key issued to a disabled account cannot be used'
                : 'Issue a new key'
            }
            onClick={() => setIssuing(true)}
          >
            <Icon.Plus size={14} />
            Issue a key
          </PrimaryButton>
        </div>
      )}

      {issuing && (
        <IssueKeyModal
          account={account}
          onClose={() => setIssuing(false)}
          onIssued={async (token, name) => {
            setIssuing(false)
            setIssued({ token, name })
            await reload()
          }}
        />
      )}

      {revoking && (
        <RevokeKeyModal
          account={account}
          row={revoking}
          onClose={() => setRevoking(null)}
          onRevoked={async () => {
            setRevoking(null)
            setError(null)
            await reload()
          }}
        />
      )}
    </Section>
  )
}

function KeyRow({
  row, editable, onRevoke,
}: {
  row: ServiceKey
  editable: boolean
  onRevoke: () => void
}) {
  const live = isLive(row)
  const expired = !row.revoked_at && !!row.expires_at
    && new Date(row.expires_at) <= new Date()
  const stale =
    live
    && (!row.last_used_at
      || Date.now() - new Date(row.last_used_at).getTime()
        > STALE_DAYS * 86_400_000)

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '9px 12px',
        borderRadius: 10,
        border: '1px solid var(--border)',
        background: live ? 'var(--panel)' : 'transparent',
        opacity: live ? 1 : 0.62,
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0, flex: 1 }}>
        <div
          style={{
            fontSize: 12.5,
            fontWeight: 600,
            color: 'var(--text-strong)',
            // Struck through rather than hidden: a revoked key is history
            // somebody may need to read, not clutter.
            textDecoration: live ? 'none' : 'line-through',
          }}
        >
          {row.name}
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', fontSize: 11, color: 'var(--text-faint)' }}>
          <code className="mono" title="The key's public half — paste it here to identify a key you found in a log">
            dm_sk_{row.prefix}…
          </code>
          <span>
            {row.last_used_at ? `used ${relativeTime(row.last_used_at)}` : 'never used'}
          </span>
          {row.expires_at && (
            <span>
              {expired ? 'expired' : 'expires'}{' '}
              {new Date(row.expires_at).toLocaleDateString()}
            </span>
          )}
          {!row.expires_at && <span>no expiry</span>}
        </div>
      </div>

      {row.revoked_at ? (
        <Chip tone="red">Revoked</Chip>
      ) : expired ? (
        <Chip tone="neutral">Expired</Chip>
      ) : stale ? (
        <Chip tone="amber">
          {row.last_used_at ? `Unused ${STALE_DAYS}d` : 'Never used'}
        </Chip>
      ) : (
        <Chip tone="green">Live</Chip>
      )}

      {editable && live && (
        <GhostButton onClick={onRevoke} title="Revoke this key">
          <Icon.Close size={13} />
          Revoke
        </GhostButton>
      )}
    </div>
  )
}

function IssueKeyModal({
  account, onClose, onIssued,
}: {
  account: ServiceAccount
  onClose: () => void
  onIssued: (token: string, name: string) => void
}) {
  const [name, setName] = useState('')
  const [neverExpires, setNeverExpires] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      const result = await api.issueKey(account.id, {
        name: name.trim(),
        never_expires: neverExpires,
      })
      onIssued(result.token, result.credential.name)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not issue a key.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title={`Issue a key for “${account.display_name}”`}
      onClose={onClose}
      width={460}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton disabled={!name.trim() || busy} onClick={submit}>
            {busy ? <Spinner /> : <Icon.Key size={15} />}
            Issue key
          </PrimaryButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {error && <ErrorNote>{error}</ErrorNote>}
        <Field
          label="Name"
          hint="Where this key will live — “CI”, “Airflow”, “Ali's laptop”. It is what you will revoke by."
        >
          <TextInput
            autoFocus
            value={name}
            placeholder="nightly-report-runner"
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <label
          style={{
            display: 'flex',
            gap: 9,
            alignItems: 'flex-start',
            fontSize: 12,
            color: 'var(--text-dim)',
            lineHeight: 1.5,
            cursor: 'pointer',
          }}
        >
          <input
            type="checkbox"
            checked={neverExpires}
            onChange={(event) => setNeverExpires(event.target.checked)}
            style={{ accentColor: 'var(--accent)', marginTop: 2 }}
          />
          <span>
            <strong style={{ color: 'var(--text-strong)' }}>Never expires.</strong>{' '}
            Otherwise this key expires a year from now. An unexpiring machine
            credential is the one thing every published key-leak incident has in
            common — tick this only for an integration that genuinely cannot
            rotate.
          </span>
        </label>
        <p style={{ margin: 0, fontSize: 11.5, color: 'var(--text-faint)', lineHeight: 1.5 }}>
          The key is shown once, on the next screen, and cannot be retrieved
          afterwards — only a hash of it is stored.
        </p>
      </div>
    </Modal>
  )
}

function RevokeKeyModal({
  account, row, onClose, onRevoked,
}: {
  account: ServiceAccount
  row: ServiceKey
  onClose: () => void
  onRevoked: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      await api.revokeKey(account.id, row.id)
      onRevoked()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not revoke this key.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title={`Revoke “${row.name}”?`}
      onClose={onClose}
      width={440}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <DangerButton disabled={busy} onClick={submit}>
            {busy ? <Spinner /> : <Icon.Close size={14} />}
            Revoke key
          </DangerButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {error && <ErrorNote>{error}</ErrorNote>}
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)', lineHeight: 1.55 }}>
          Anything using this key stops working on its <strong>next request</strong>
          {' '}— not in fifteen minutes. The row stays in the list, struck through,
          so the history remains readable. This cannot be undone; issue a new key
          instead.
        </p>
      </div>
    </Modal>
  )
}

// ── create and delete ─────────────────────────────────────────────────────
function NewServiceAccountModal({
  onClose, onCreated,
}: {
  onClose: () => void
  onCreated: (account: ServiceAccount) => void
}) {
  const can = useCan()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [allRoles, setAllRoles] = useState<Role[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!can('role.read')) return
    let cancelled = false
    rolesApi.list().then((rows) => !cancelled && setAllRoles(rows)).catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [can])

  const offerable = allRoles.filter((role) => !isPrivileged(role))
  const hidden = allRoles.filter(isPrivileged)

  /**
   * What this account will be able to do, before Save.
   *
   * The union of the chosen roles' capabilities, shown as the count and the
   * words themselves. A form that showed only role *names* asks somebody to
   * remember what eight roles mean; this answers the question they actually
   * have, which is "what am I about to hand out?".
   */
  const effective = useMemo(() => {
    const words = new Set<string>()
    for (const role of allRoles) {
      if (chosen.has(role.id)) role.capabilities.forEach((c) => words.add(c))
    }
    return [...words].sort()
  }, [allRoles, chosen])

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      onCreated(
        await api.create({
          display_name: name.trim(),
          description: description.trim(),
          role_ids: [...chosen],
        }),
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create this account.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title="New service account"
      onClose={onClose}
      width={560}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton
            disabled={!name.trim() || !description.trim() || busy}
            onClick={submit}
          >
            {busy ? <Spinner /> : <Icon.Plus size={15} />}
            Create account
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
            placeholder="Nightly report runner"
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field
          label="What is this for?"
          hint="Required. An undocumented machine identity is the one nobody is willing to delete a year from now."
        >
          <TextArea
            rows={2}
            value={description}
            placeholder="Runs the 03:00 finance report and posts it to Slack."
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>

        {offerable.length > 0 && (
          <Field
            label="Roles"
            hint="Its entire reach. The same roles a person holds mean the same thing here."
          >
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
                maxHeight: 210,
                overflowY: 'auto',
              }}
            >
              {offerable.map((role) => {
                const picked = chosen.has(role.id)
                return (
                  <label
                    key={role.id}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 10,
                      padding: '7px 10px',
                      borderRadius: 9,
                      border: '1px solid var(--border)',
                      background: picked ? 'var(--accent-bg)' : 'var(--panel)',
                      cursor: 'pointer',
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={picked}
                      onChange={() =>
                        setChosen((current) => {
                          const next = new Set(current)
                          if (next.has(role.id)) next.delete(role.id)
                          else next.add(role.id)
                          return next
                        })
                      }
                      style={{ accentColor: 'var(--accent)', marginTop: 2 }}
                    />
                    <span style={{ minWidth: 0 }}>
                      <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)' }}>
                        {role.name}
                      </span>
                      <span style={{ display: 'block', fontSize: 11, color: 'var(--text-faint)', lineHeight: 1.45 }}>
                        {role.description}
                      </span>
                    </span>
                  </label>
                )
              })}
            </div>
          </Field>
        )}

        {/* The effective permission, before Save — the question somebody
            actually has when they tick a role is "what am I handing out?". */}
        <div
          style={{
            padding: '10px 12px',
            borderRadius: 10,
            border: '1px solid var(--border)',
            background: 'var(--panel)',
            fontSize: 11.5,
            lineHeight: 1.55,
            color: 'var(--text-dim)',
          }}
        >
          {effective.length === 0 ? (
            <>
              <strong style={{ color: 'var(--text-strong)' }}>No permissions yet.</strong>{' '}
              A key issued to this account would authenticate and be able to
              reach nothing. You can add roles afterwards.
            </>
          ) : (
            <>
              <strong style={{ color: 'var(--text-strong)' }}>
                This account will hold {effective.length}{' '}
                {effective.length === 1 ? 'permission' : 'permissions'}:
              </strong>{' '}
              <span className="mono" style={{ wordBreak: 'break-word' }}>
                {effective.join(' · ')}
              </span>
            </>
          )}
        </div>

        {hidden.length > 0 && <PrivilegedNote roles={hidden} />}

        <p style={{ margin: 0, fontSize: 11.5, color: 'var(--text-faint)', lineHeight: 1.5 }}>
          No key is issued here. Making an identity and handing out a secret are
          two deliberate acts — issue a key from the account&rsquo;s page once it
          exists.
        </p>
      </div>
    </Modal>
  )
}

function RemoveServiceAccountModal({
  account, onClose, onRemoved,
}: {
  account: ServiceAccount
  onClose: () => void
  onRemoved: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      await api.remove(account.id)
      onRemoved()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete this account.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title={`Delete “${account.display_name}”?`}
      onClose={onClose}
      width={460}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <DangerButton disabled={busy} onClick={submit}>
            {busy ? <Spinner /> : <Icon.Trash size={14} />}
            Delete account
          </DangerButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {error && <ErrorNote>{error}</ErrorNote>}
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)', lineHeight: 1.55 }}>
          {account.active_keys > 0
            ? `${account.active_keys} live ${account.active_keys === 1 ? 'key stops' : 'keys stop'} working immediately, and whatever is using ${account.active_keys === 1 ? 'it' : 'them'} starts failing.`
            : 'No live keys, so nothing is authenticating as this account right now.'}{' '}
          Every key it was ever issued is deleted with it, and its history in the
          audit log stays.
        </p>
        <p style={{ margin: 0, fontSize: 12, color: 'var(--text-faint)', lineHeight: 1.5 }}>
          If you only want to stop it working for now,{' '}
          <strong>disable it instead</strong> — that keeps its roles and keys, so
          re-enabling is one click rather than a re-provision.
        </p>
      </div>
    </Modal>
  )
}
