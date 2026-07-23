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
      style={{
        height: '100vh',
        width: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg)',
        padding: 24,
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: 380,
          display: 'flex',
          flexDirection: 'column',
          gap: 24,
        }}
      >
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 14,
          }}
        >
          <Logo size={56} />
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.02em' }}>
              Welcome to DataMind
            </div>
            <div style={{ fontSize: 13.5, color: 'var(--text-dim)', marginTop: 4 }}>
              Sign in to your conversational BI workspace
            </div>
          </div>
        </div>

        <form
          onSubmit={submit}
          style={{
            background: 'var(--panel)',
            border: '1px solid var(--border)',
            borderRadius: 16,
            padding: 24,
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
            boxShadow: '0 8px 30px rgba(0,0,0,0.12)',
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
              boxShadow: '0 1px 2px rgba(0,0,0,0.15)',
            }}
          >
            {busy && <Spinner />}
            {busy ? 'Signing in' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
