import { useCallback, useEffect, useMemo, useState } from 'react'
import { auth, getAccessToken, onAuthChange } from './api/client'
import type { User } from './api/types'
import { Icon, Logo, initialOf } from './components/ui'
import AboutPage from './pages/AboutPage'
import ChatPage from './pages/ChatPage'
import DashboardsPage from './pages/DashboardsPage'
import DataSourcesPage from './pages/DataSourcesPage'
import LlmProvidersPage from './pages/LlmProvidersPage'
import LoginPage from './pages/LoginPage'
import ReportsPage from './pages/ReportsPage'
import UsersPage from './pages/UsersPage'
import { applyTheme, type ThemeName } from './theme/tokens'

export type View =
  | 'chat' | 'dashboards' | 'reports' | 'connections' | 'settings' | 'users' | 'about'

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [booting, setBooting] = useState(true)
  const [view, setView] = useState<View>('chat')
  // The one signed-out destination other than the form itself. It is local
  // state rather than a route because the signed-out shell has no router: the
  // whole product is one view swap deep, and About is not worth being the
  // exception that introduces a second mechanism.
  const [aboutSignedOut, setAboutSignedOut] = useState(false)
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

  // A 401 the refresh cookie cannot rescue clears the token deep inside the
  // client. Nothing was listening for that, so the signed-in shell stayed
  // mounted around a session that no longer existed and every request behind
  // it failed — the app looked emptied out instead of signed out. Dropping the
  // user here shows the login screen, which is the truth.
  useEffect(
    () => onAuthChange(() => {
      if (getAccessToken() === null) setUser((current) => (current ? null : current))
    }),
    [],
  )

  const handleLogout = useCallback(async () => {
    await auth.logout()
    setUser(null)
    setView('chat')
    setAboutSignedOut(false)
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
          <Logo />
          <span style={{ fontWeight: 700, fontSize: 15, color: 'var(--text)' }}>
            DataMind
          </span>
        </div>
      </div>
    )
  }

  if (!user) {
    return aboutSignedOut
      ? <AboutPage onBack={() => setAboutSignedOut(false)} />
      : <LoginPage onSignedIn={setUser} onAbout={() => setAboutSignedOut(true)} />
  }

  return (
    <div
      className="rm-app"
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
      {/* The first thing in the tab order, and invisible until it has focus:
          the rail is six destinations plus a footer group, and a keyboard user
          should not have to walk them to reach the page they opened. */}
      <a className="rm-skip" href="#main">Skip to content</a>
      {/* The two shell boxes carry classes so the print stylesheet can reach
          them: both are viewport-sized scroll containers, and on paper there
          is no viewport to be sized to — see `@media print`. The inner one is
          also the document's <main>, which is what the skip link targets. */}
      <div className="rm-app-row" style={{ display: 'flex', height: '100vh', width: '100%' }}>
        <Sidebar
          user={user}
          view={view}
          onNavigate={setView}
          theme={theme}
          onToggleTheme={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          onLogout={handleLogout}
        />
        <main
          id="main"
          // Not focusable in the tab order — only as the skip link's target,
          // which is how the focus actually lands inside the page rather than
          // merely scrolling it there.
          tabIndex={-1}
          className="rm-app-view"
          style={{ flex: 1, display: 'flex', minWidth: 0 }}
        >
          {view === 'chat' && <ChatPage />}
          {view === 'dashboards' && <DashboardsPage />}
          {view === 'reports' && <ReportsPage />}
          {view === 'connections' && <DataSourcesPage />}
          {view === 'settings' && <LlmProvidersPage />}
          {view === 'users' && <UsersPage currentUser={user} />}
          {view === 'about' && <AboutPage />}
        </main>
      </div>
    </div>
  )
}

/**
 * The application's rail.
 *
 * One flat list of destinations, in the order the product is used. It was
 * briefly cut into "Workspace" and "Configure" groups and that was worse:
 * captions over six items are furniture, and the split invited a decision
 * ("which half is this in?") on every glance at a list short enough to read
 * whole.
 *
 * Creators is the one destination deliberately *not* in that list, and not
 * drawn like one either. The list is the work, in the order it is done; who
 * built the thing is a credit, and a credit given the same weight as Chat
 * reads as a sixth place to work. So it is the last line in the rail, in the
 * footer group's own quieter register — the theme, the account, the way out —
 * and it marks itself when open with colour alone rather than the accent rail
 * a nav row gets.
 *
 * What the rail does carry is state made visible — the open page keeps an
 * accent rail and an accent glyph, matching the selected row of the settings
 * index one column to the right, so "where am I" is answered the same way
 * everywhere in the product. Hover and selection live in the stylesheet
 * (`.rm-nav-btn`), so the rail holds no React state per button.
 */
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
        { key: 'dashboards' as const, label: 'Dashboards', icon: <Icon.Grid /> },
        { key: 'reports' as const, label: 'Reports', icon: <Icon.Doc /> },
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
      className="rm-sidebar"
      style={{
        width: 232,
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--sidebar-bg)',
        borderRight: '1px solid var(--border)',
        padding: '18px 12px 14px',
      }}
    >
      <div className="rm-brand">
        <Logo size={34} />
        <span className="rm-sidebar-text rm-brand-name">DataMind</span>
        <span className="rm-sidebar-text rm-brand-tag">v0.1</span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 3, marginTop: 18 }}>
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

      <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 10 }}>
        {/* Drawn as the two things you can pick rather than as a track with a
            knob: a switch labelled "Dark" never says whether that is the state
            or the offer. Collapsed to the icon of the *other* theme on the
            narrow rail, where there is no room for both. */}
        <div className="rm-theme" role="group" aria-label="Theme">
          <button
            type="button"
            className={theme === 'dark' ? 'is-on' : undefined}
            aria-pressed={theme === 'dark'}
            title="Dark theme"
            onClick={() => theme !== 'dark' && onToggleTheme()}
          >
            <Icon.Moon size={13} />
            <span className="rm-sidebar-text">Dark</span>
          </button>
          <button
            type="button"
            className={theme === 'light' ? 'is-on' : undefined}
            aria-pressed={theme === 'light'}
            title="Light theme"
            onClick={() => theme !== 'light' && onToggleTheme()}
          >
            <Icon.Sun size={13} />
            <span className="rm-sidebar-text">Light</span>
          </button>
        </div>

        <div className="rm-sidebar-user">
          <span
            style={{
              width: 30,
              height: 30,
              borderRadius: 9,
              background: 'var(--accent)',
              color: 'var(--on-accent)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 12.5,
              fontWeight: 700,
              flexShrink: 0,
            }}
          >
            {initialOf(user.display_name || user.email)}
          </span>
          <div className="rm-sidebar-text" style={{ display: 'flex', flexDirection: 'column', minWidth: 0, lineHeight: 1.25 }}>
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
            className="rm-sidebar-logout rm-icon-btn"
            style={{
              marginLeft: 'auto',
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 28,
              height: 28,
              borderRadius: 8,
              background: 'transparent',
              border: 'none',
              color: 'var(--text-faint)',
              cursor: 'pointer',
              ['--rm-hover-bg' as string]: 'var(--panel-hover)',
            }}
          >
            <Icon.Logout size={14} />
          </button>
        </div>

        {/* The last line in the rail, and deliberately the quietest thing in
            it: a colophon, not a destination. It was briefly a full nav row
            up in the list, which read as a sixth place to work — the credit
            for who wrote the thing should be findable, not offered. On the
            66px rail the word is hidden with every other label and the glyph
            stands in for it, which is why both are rendered. */}
        <button
          type="button"
          onClick={() => onNavigate('about')}
          aria-current={view === 'about' ? 'page' : undefined}
          aria-label="Creators"
          title="Creators"
          className={`rm-sidebar-about${view === 'about' ? ' is-on' : ''}`}
        >
          <span className="rm-sidebar-text">Creators</span>
          <span className="rm-sidebar-about-icon" aria-hidden>
            <Icon.Info size={14} />
          </span>
        </button>
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
  return (
    <button
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={`rm-nav-btn${active ? ' is-on' : ''}`}
      title={label}
    >
      {icon}
      <span className="rm-sidebar-text">{label}</span>
    </button>
  )
}
