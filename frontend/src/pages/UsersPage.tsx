import { useCallback, useEffect, useState } from 'react'
import { users as api } from '../api/client'
import type { User } from '../api/types'
import {
  Chip, ErrorNote, Icon, PrimaryButton, Spinner, TextInput, initialOf,
} from '../components/ui'

export default function UsersPage({ currentUser }: { currentUser: User }) {
  const [list, setList] = useState<User[]>([])
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [invite, setInvite] = useState<{ email: string; password: string } | null>(null)

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
            Admins can add or remove users and grant admin access
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
            <span style={{ width: 200, textAlign: 'right' }}>Actions</span>
          </div>

          {list.map((user) => {
            const isSelf = user.id === currentUser.id
            const isAdmin = user.role === 'ADMIN'
            return (
              <div
                key={user.id}
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
                    width: 200,
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
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
