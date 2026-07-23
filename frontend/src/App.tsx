import { useCallback, useEffect, useMemo, useState } from 'react'
import { auth } from './api/client'
import type { User } from './api/types'
import { Icon, Logo, initialOf } from './components/ui'
import ChatPage from './pages/ChatPage'
import DataSourcesPage from './pages/DataSourcesPage'
import LlmProvidersPage from './pages/LlmProvidersPage'
import LoginPage from './pages/LoginPage'
import UsersPage from './pages/UsersPage'
import { applyTheme, type ThemeName } from './theme/tokens'

export type View = 'chat' | 'connections' | 'settings' | 'users'

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [booting, setBooting] = useState(true)
  const [view, setView] = useState<View>('chat')
  const [theme, setTheme] = useState<ThemeName>(
    () => (localStorage.getItem('raymand.theme') as ThemeName) || 'dark',
  )

  useEffect(() => {
    applyTheme(theme)
    localStorage.setItem('raymand.theme', theme)
  }, [theme])

  // A live refresh cookie means the user is still signed in across reloads.
  useEffect(() => {
    let cancelled = false
    auth
      .restore()
      .then((restored) => {
        if (!cancelled) setUser(restored)
      })
      .finally(() => {
        if (!cancelled) setBooting(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const handleLogout = useCallback(async () => {
    await auth.logout()
    setUser(null)
    setView('chat')
  }, [])

  if (booting) {
    return (
      <div
        style={{
          height: '100vh',
          display: 'grid',
          placeItems: 'center',
          background: 'var(--bg)',
        }}
      >
        <div className="rm-pulse" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Logo size={30} />
          <span style={{ fontWeight: 700, fontSize: 15, color: 'var(--text)' }}>
            DataMind
          </span>
        </div>
      </div>
    )
  }

  if (!user) {
    return <LoginPage onSignedIn={setUser} />
  }

  return (
    <div
      style={{
        position: 'relative',
        height: '100vh',
        width: '100%',
        background: 'var(--bg)',
        color: 'var(--text)',
        fontFamily: 'Inter, system-ui, sans-serif',
        overflow: 'hidden',
      }}
    >
      <div style={{ display: 'flex', height: '100vh', width: '100%' }}>
        <Sidebar
          user={user}
          view={view}
          onNavigate={setView}
          theme={theme}
          onToggleTheme={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          onLogout={handleLogout}
        />
        <div style={{ flex: 1, display: 'flex', minWidth: 0 }}>
          {view === 'chat' && <ChatPage />}
          {view === 'connections' && <DataSourcesPage />}
          {view === 'settings' && <LlmProvidersPage />}
          {view === 'users' && <UsersPage currentUser={user} />}
        </div>
      </div>
    </div>
  )
}

function Sidebar({
  user, view, onNavigate, theme, onToggleTheme, onLogout,
}: {
  user: User
  view: View
  onNavigate: (view: View) => void
  theme: ThemeName
  onToggleTheme: () => void
  onLogout: () => void
}) {
  const items = useMemo(
    () =>
      [
        { key: 'chat' as const, label: 'Chat', icon: <Icon.Chat /> },
        { key: 'connections' as const, label: 'Data sources', icon: <Icon.Database /> },
        { key: 'settings' as const, label: 'LLM providers', icon: <Icon.Sparkle /> },
        ...(user.role === 'ADMIN'
          ? [{ key: 'users' as const, label: 'Users', icon: <Icon.Users /> }]
          : []),
      ],
    [user.role],
  )

  return (
    <nav
      aria-label="Main"
      style={{
        width: 224,
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--sidebar-bg)',
        borderRight: '1px solid var(--border)',
        padding: '20px 14px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '0 8px 20px' }}>
        <Logo />
        <div style={{ fontWeight: 700, fontSize: 15, letterSpacing: '-0.01em' }}>
          DataMind
        </div>
        <div
          style={{
            marginLeft: 'auto',
            fontSize: 10,
            fontWeight: 600,
            color: 'var(--text-dim)',
            background: 'var(--panel-alt)',
            padding: '2px 6px',
            borderRadius: 4,
          }}
        >
          v0
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {items.map((item) => (
          <NavButton
            key={item.key}
            active={view === item.key}
            icon={item.icon}
            label={item.label}
            onClick={() => onNavigate(item.key)}
          />
        ))}
      </div>

      <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <button
          onClick={onToggleTheme}
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 9,
            padding: '8px',
            background: 'transparent',
            border: 'none',
            color: 'var(--text-dim)',
            fontSize: 12.5,
            cursor: 'pointer',
          }}
        >
          <span
            style={{
              width: 34,
              height: 18,
              borderRadius: 9,
              background: 'var(--panel-alt)',
              border: '1px solid var(--border-strong)',
              position: 'relative',
              flexShrink: 0,
            }}
          >
            <span
              style={{
                position: 'absolute',
                top: 1,
                left: theme === 'dark' ? 1 : 16,
                width: 14,
                height: 14,
                borderRadius: '50%',
                background: 'var(--accent)',
                transition: 'left .15s ease',
              }}
            />
          </span>
          {theme === 'dark' ? 'Dark' : 'Light'}
        </button>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 9,
            padding: '8px',
            borderTop: '1px solid var(--border)',
            paddingTop: 14,
          }}
        >
          <span
            style={{
              width: 28,
              height: 28,
              borderRadius: 8,
              background: 'var(--accent)',
              color: 'var(--on-accent)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 12,
              fontWeight: 700,
              flexShrink: 0,
            }}
          >
            {initialOf(user.display_name || user.email)}
          </span>
          <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, lineHeight: 1.2 }}>
            <span
              style={{
                fontSize: 12.5,
                fontWeight: 600,
                color: 'var(--text-strong)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {user.display_name || user.email}
            </span>
            <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
              {user.role === 'ADMIN' ? 'Admin' : 'Member'}
            </span>
          </div>
          <button
            onClick={onLogout}
            title="Sign out"
            aria-label="Sign out"
            style={{
              marginLeft: 'auto',
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 28,
              height: 28,
              borderRadius: 7,
              background: 'transparent',
              border: '1px solid var(--border-strong)',
              color: 'var(--text-dim)',
              cursor: 'pointer',
            }}
          >
            <Icon.Logout size={14} />
          </button>
        </div>
      </div>
    </nav>
  )
}

function NavButton({
  active, icon, label, onClick,
}: {
  active: boolean
  icon: React.ReactNode
  label: string
  onClick: () => void
}) {
  const [hover, setHover] = useState(false)
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      aria-current={active ? 'page' : undefined}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        width: '100%',
        padding: '9px 10px',
        borderRadius: 8,
        border: 'none',
        cursor: 'pointer',
        fontSize: 13.5,
        fontWeight: active ? 600 : 500,
        textAlign: 'left',
        color: active ? 'var(--text-strong)' : 'var(--text-dim)',
        background: active
          ? 'var(--accent-bg)'
          : hover
            ? 'var(--panel-hover)'
            : 'transparent',
      }}
    >
      {icon}
      {label}
    </button>
  )
}
