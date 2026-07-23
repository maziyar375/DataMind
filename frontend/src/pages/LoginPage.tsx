import { useState } from 'react'
import { ApiError, auth } from '../api/client'
import type { User } from '../api/types'
import { ErrorNote, Logo, Spinner, TextInput } from '../components/ui'

export default function LoginPage({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      onSignedIn(await auth.login(email.trim(), password))
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Could not reach the server. Check that the API is running.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="rm-auth"
      style={{
        minHeight: '100vh',
        width: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg)',
        padding: 24,
      }}
    >
      <div
        className="rm-enter"
        style={{
          width: '100%',
          maxWidth: 392,
          display: 'flex',
          flexDirection: 'column',
          gap: 26,
        }}
      >
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 18,
          }}
        >
          <div className="rm-auth-logo">
            <Logo size={84} />
          </div>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 27, fontWeight: 700, letterSpacing: '-0.02em' }}>
              Welcome to DataMind
            </div>
            <div style={{ fontSize: 14, color: 'var(--text-dim)', marginTop: 6 }}>
              Sign in to your conversational BI workspace
            </div>
          </div>
        </div>

        <form
          onSubmit={submit}
          className="rm-auth-card"
          style={{
            padding: 26,
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
          }}
        >
          {error && <ErrorNote>{error}</ErrorNote>}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            <label
              htmlFor="email"
              style={{ fontSize: 12, color: 'var(--text-dim)', fontWeight: 500 }}
            >
              Email
            </label>
            <TextInput
              id="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              style={{ borderRadius: 9, padding: '11px 13px' }}
            />
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            <label
              htmlFor="password"
              style={{ fontSize: 12, color: 'var(--text-dim)', fontWeight: 500 }}
            >
              Password
            </label>
            <TextInput
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              style={{ borderRadius: 9, padding: '11px 13px' }}
            />
          </div>

          <button
            type="submit"
            disabled={busy}
            className="rm-auth-submit"
            style={{
              marginTop: 4,
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 8,
              fontSize: 14,
              fontWeight: 600,
              color: 'var(--on-accent)',
              background: 'var(--accent)',
              border: 'none',
              borderRadius: 10,
              padding: 12,
              cursor: busy ? 'wait' : 'pointer',
              opacity: busy ? 0.7 : 1,
            }}
          >
            {busy && <Spinner />}
            {busy ? 'Signing in' : 'Sign in'}
          </button>
        </form>

        <div
          style={{
            textAlign: 'center',
            fontSize: 12,
            color: 'var(--text-faint)',
            letterSpacing: '0.01em',
          }}
        >
          Ask in plain language — get an answer, a table, and auditable SQL.
        </div>
      </div>
    </div>
  )
}
