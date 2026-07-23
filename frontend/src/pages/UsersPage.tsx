import { Fragment, useCallback, useEffect, useState } from 'react'
import { users as api } from '../api/client'
import type { User } from '../api/types'
import {
  Chip, ErrorNote, GhostButton, Icon, PrimaryButton, Spinner, TextInput,
  initialOf,
} from '../components/ui'

export default function UsersPage({ currentUser }: { currentUser: User }) {
  const [list, setList] = useState<User[]>([])
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [invite, setInvite] = useState<{ email: string; password: string } | null>(null)

  // Which user's password form is open, and the value being typed into it.
  const [pwFor, setPwFor] = useState<string | null>(null)
  const [pwValue, setPwValue] = useState('')
  const [pwBusy, setPwBusy] = useState(false)
  const [pwDone, setPwDone] = useState<string | null>(null)

  function openPassword(userId: string) {
    setPwFor(userId)
    setPwValue('')
    setPwDone(null)
    setError(null)
  }

  async function savePassword(user: User) {
    if (pwValue.length < 8) return
    setPwBusy(true)
    setError(null)
    try {
      await api.setPassword(user.id, pwValue)
      setPwFor(null)
      setPwValue('')
      setPwDone(user.email)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not set that password.')
    } finally {
      setPwBusy(false)
    }
  }

  const refresh = useCallback(async () => {
    setList(await api.list())
  }, [])

  useEffect(() => {
    refresh().catch(() => setError('Could not load the user list.'))
  }, [refresh])

  async function addUser() {
    if (!name.trim() || !email.trim()) return
    setBusy(true)
    setError(null)
    try {
      const created = await api.create({
        display_name: name.trim(),
        email: email.trim(),
        role: 'MEMBER',
      })
      setInvite({ email: created.user.email, password: created.temporary_password })
      setName('')
      setEmail('')
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add that user.')
    } finally {
      setBusy(false)
    }
  }

  async function toggleRole(user: User) {
    setError(null)
    try {
      await api.update(user.id, { role: user.role === 'ADMIN' ? 'MEMBER' : 'ADMIN' })
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change that role.')
    }
  }

  async function removeUser(user: User) {
    setError(null)
    try {
      await api.remove(user.id)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove that user.')
    }
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          padding: '16px 28px',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        <div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>User management</div>
          <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>
            Admins can add or remove users, set passwords, and grant admin access
          </div>
        </div>
      </header>

      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '24px 28px',
          maxWidth: 820,
          display: 'flex',
          flexDirection: 'column',
          gap: 20,
        }}
      >
        {error && <ErrorNote>{error}</ErrorNote>}

        {pwDone && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              fontSize: 12.5,
              color: 'var(--green)',
              background: 'var(--green-bg)',
              border: '1px solid transparent',
              borderRadius: 8,
              padding: '9px 12px',
            }}
          >
            <Icon.Check size={14} stroke="var(--green)" />
            <span>
              Password updated for {pwDone}. Any existing sessions were signed
              out.
            </span>
          </div>
        )}

        {invite && (
          <div
            style={{
              border: '1px solid var(--amber-border)',
              background: 'var(--amber-bg)',
              borderRadius: 10,
              padding: '14px 16px',
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--amber)' }}>
              Temporary password for {invite.email}
            </div>
            <code
              className="mono"
              style={{
                fontSize: 13.5,
                padding: '8px 12px',
                background: 'var(--code-bg)',
                borderRadius: 7,
                color: 'var(--code-text)',
                userSelect: 'all',
              }}
            >
              {invite.password}
            </code>
            <div style={{ fontSize: 11.5, color: 'var(--text-dim)' }}>
              Copy this now — it is shown once and cannot be retrieved later.
              They will be asked to change it on first sign-in.
            </div>
            <button
              onClick={() => setInvite(null)}
              style={{
                alignSelf: 'flex-start',
                fontSize: 12,
                color: 'var(--text-dim)',
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                padding: 0,
                textDecoration: 'underline',
              }}
            >
              Dismiss
            </button>
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div
            style={{
              fontSize: 11,
              fontWeight: 700,
              color: 'var(--text-faint)',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
            }}
          >
            Add a user
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <TextInput
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Full name"
              aria-label="Full name"
              style={{ flex: 1, minWidth: 150, borderRadius: 8, padding: '9px 12px' }}
            />
            <TextInput
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="email@company.com"
              aria-label="Email address"
              style={{ flex: 1, minWidth: 180, borderRadius: 8, padding: '9px 12px' }}
            />
            <PrimaryButton onClick={addUser} disabled={busy || !name.trim() || !email.trim()}>
              {busy ? <Spinner /> : <Icon.Plus size={15} />}
              Add user
            </PrimaryButton>
          </div>
        </div>

        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: 12,
            overflow: 'hidden',
            background: 'var(--panel)',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              padding: '10px 16px',
              borderBottom: '1px solid var(--border)',
              fontSize: 11,
              fontWeight: 700,
              color: 'var(--text-faint)',
              textTransform: 'uppercase',
              letterSpacing: '0.04em',
            }}
          >
            <span style={{ flex: 1 }}>User</span>
            <span style={{ width: 110 }}>Role</span>
            <span style={{ width: 240, textAlign: 'right' }}>Actions</span>
          </div>

          {list.map((user) => {
            const isSelf = user.id === currentUser.id
            const isAdmin = user.role === 'ADMIN'
            const editingPw = pwFor === user.id
            return (
              <Fragment key={user.id}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  padding: '12px 16px',
                  borderTop: '1px solid var(--border)',
                }}
              >
                <div
                  style={{
                    flex: 1,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 11,
                    minWidth: 0,
                  }}
                >
                  <span
                    style={{
                      width: 34,
                      height: 34,
                      borderRadius: 9,
                      background: 'var(--panel-alt)',
                      color: 'var(--text-dim)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 13,
                      fontWeight: 700,
                      flexShrink: 0,
                    }}
                  >
                    {initialOf(user.display_name || user.email)}
                  </span>
                  <div
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      lineHeight: 1.2,
                      minWidth: 0,
                    }}
                  >
                    <span
                      style={{
                        fontSize: 13.5,
                        fontWeight: 600,
                        color: 'var(--text-strong)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {user.display_name || user.email}
                    </span>
                    <span
                      style={{
                        fontSize: 11.5,
                        color: 'var(--text-faint)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {user.email}
                      {user.status === 'INVITED' && ' · invited'}
                    </span>
                  </div>
                </div>

                <span style={{ width: 110 }}>
                  <Chip tone={isAdmin ? 'accent' : 'neutral'}>
                    {isAdmin ? 'Admin' : 'Member'}
                  </Chip>
                </span>

                <div
                  style={{
                    width: 240,
                    display: 'flex',
                    gap: 8,
                    justifyContent: 'flex-end',
                    alignItems: 'center',
                  }}
                >
                  <button
                    onClick={() => toggleRole(user)}
                    disabled={isSelf}
                    style={{
                      fontSize: 12,
                      fontWeight: 500,
                      background: 'transparent',
                      color: isSelf ? 'var(--text-faint)' : 'var(--accent)',
                      border: `1px solid ${isSelf ? 'var(--border)' : 'var(--accent-border)'}`,
                      padding: '6px 11px',
                      borderRadius: 7,
                      cursor: isSelf ? 'not-allowed' : 'pointer',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {isAdmin ? 'Revoke admin' : 'Make admin'}
                  </button>

                  {isSelf ? (
                    <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>You</span>
                  ) : (
                    <>
                      <button
                        onClick={() => (editingPw ? setPwFor(null) : openPassword(user.id))}
                        title="Set password"
                        aria-label={`Set password for ${user.display_name || user.email}`}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          width: 32,
                          height: 32,
                          background: editingPw ? 'var(--accent-bg)' : 'transparent',
                          color: 'var(--accent)',
                          border: `1px solid ${editingPw ? 'var(--accent)' : 'var(--accent-border)'}`,
                          borderRadius: 7,
                          cursor: 'pointer',
                        }}
                      >
                        <Icon.Key size={14} />
                      </button>
                      <button
                        onClick={() => removeUser(user)}
                        title="Remove user"
                        aria-label={`Remove ${user.display_name || user.email}`}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          width: 32,
                          height: 32,
                          background: 'transparent',
                          color: 'var(--red)',
                          border: '1px solid var(--red-border)',
                          borderRadius: 7,
                          cursor: 'pointer',
                        }}
                      >
                        <Icon.Trash size={14} />
                      </button>
                    </>
                  )}
                </div>
              </div>

              {editingPw && (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    padding: '12px 16px 14px 61px',
                    borderTop: '1px solid var(--border)',
                    background: 'var(--panel-alt)',
                  }}
                >
                  <TextInput
                    type="password"
                    autoFocus
                    autoComplete="new-password"
                    value={pwValue}
                    onChange={(e) => setPwValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && pwValue.length >= 8) savePassword(user)
                      if (e.key === 'Escape') setPwFor(null)
                    }}
                    placeholder="New password — at least 8 characters"
                    aria-label={`New password for ${user.email}`}
                    style={{ flex: 1, maxWidth: 320, borderRadius: 8, padding: '9px 12px' }}
                  />
                  <PrimaryButton
                    onClick={() => savePassword(user)}
                    disabled={pwBusy || pwValue.length < 8}
                  >
                    {pwBusy ? <Spinner /> : <Icon.Key size={14} />}
                    Set password
                  </PrimaryButton>
                  <GhostButton onClick={() => setPwFor(null)}>Cancel</GhostButton>
                </div>
              )}
              </Fragment>
            )
          })}
        </div>
      </div>
    </div>
  )
}
