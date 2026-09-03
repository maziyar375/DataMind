import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Navigate, Route, Routes, useBlocker, useLocation, useNavigate,
  type BlockerFunction,
} from 'react-router-dom'
import { auth, getAccessToken, onAuthChange } from './api/client'
import type { User } from './api/types'
import { DangerButton, GhostButton, Icon, Logo, Modal, initialOf } from './components/ui'
import AboutPage from './pages/AboutPage'
import ChatPage from './pages/ChatPage'
import DashboardsPage from './pages/DashboardsPage'
import DataSourcesPage from './pages/DataSourcesPage'
import LlmProvidersPage from './pages/LlmProvidersPage'
import LoginPage from './pages/LoginPage'
import ReportsPage from './pages/ReportsPage'
import UsersPage from './pages/UsersPage'
import { ShellProvider, type Shell } from './shell'
import { applyTheme, type ThemeName } from './theme/tokens'

/**
 * The rail's destinations, in the order the product is used.
 *
 * The paths are the product's vocabulary now that there is a router, so they
 * are written once, here, and both the rail and the route table read them.
 * `/providers` rather than `/settings` for the model providers: it says what
 * the page is, and it leaves `/settings` free for the account screen.
 */
const NAV = [
  { path: '/chat', label: 'Chat', icon: <Icon.Chat /> },
  { path: '/dashboards', label: 'Dashboards', icon: <Icon.Grid /> },
  { path: '/reports', label: 'Reports', icon: <Icon.Doc /> },
  { path: '/sources', label: 'Data sources', icon: <Icon.Database /> },
  { path: '/providers', label: 'LLM providers', icon: <Icon.Sparkle /> },
  { path: '/users', label: 'Users', icon: <Icon.Users />, adminOnly: true },
]

/** Does `pathname` sit inside the section rooted at `path`? */
function isInSection(pathname: string, path: string): boolean {
  return pathname === path || pathname.startsWith(`${path}/`)
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [booting, setBooting] = useState(true)
  const location = useLocation()
  const navigate = useNavigate()
  const [theme, setTheme] = useState<ThemeName>(
    () => (localStorage.getItem('raymand.theme') as ThemeName) || 'dark',
  )
  // A surface — today only a dashboard pinned to DARK or LIGHT — holding the
  // theme while it is open. The shell resolves `override ?? theme` and is the
  // only caller of `applyTheme`, so nothing can restore a stale value it
  // captured at mount while the rail was toggled behind its back.
  const [themeOverride, setThemeOverride] = useState<ThemeName | null>(null)

  useEffect(() => {
    applyTheme(themeOverride ?? theme)
  }, [theme, themeOverride])

  // The user's own choice is what persists. An override belongs to the
  // surface that asked for it and dies with it.
  useEffect(() => {
    localStorage.setItem('raymand.theme', theme)
  }, [theme])

  // Which surfaces are holding unsaved work, and what would be lost. A ref
  // rather than state: the blocker reads it at navigation time, and a form
  // going dirty is not a reason to re-render the shell around it.
  const unsaved = useRef(new Map<string, string>())
  const shell = useMemo<Shell>(
    () => ({
      requestThemeOverride: setThemeOverride,
      setUnsaved: (key, reason) => {
        if (reason) unsaved.current.set(key, reason)
        else unsaved.current.delete(key)
      },
    }),
    [],
  )

  // One guard for the whole app rather than a check per page: every way out of
  // a dirty form — the rail, a row in the master column, browser Back — is a
  // navigation, and this is where they all pass through.
  const shouldBlock = useCallback<BlockerFunction>(
    ({ currentLocation, nextLocation }) =>
      unsaved.current.size > 0 && currentLocation.pathname !== nextLocation.pathname,
    [],
  )
  const blocker = useBlocker(shouldBlock)
  const blockedReason = blocker.state === 'blocked'
    ? [...unsaved.current.values()][0]
    : null

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
    navigate('/chat')
  }, [navigate])

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

  // The signed-out side has one destination other than the form itself, and
  // it is the same URL it has when signed in — the credits page is reachable
  // from both sides of the wall and should be linkable from either.
  if (!user) {
    return location.pathname === '/about'
      ? <AboutPage onBack={() => navigate('/')} />
      : <LoginPage onSignedIn={setUser} onAbout={() => navigate('/about')} />
  }

  return (
    <ShellProvider value={shell}>
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
            pathname={location.pathname}
            onNavigate={navigate}
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
            {/* Each section owns the routes under it and stays mounted across
                them (`/chat` → `/chat/:id` is one screen deciding what to show,
                not two). That is why the paths end in `*` and the switch lives
                inside the page: remounting a section on every open and close
                would drop a chat's live stream and re-read a list the reader
                was just looking at. Anything unrecognised lands on Chat, which
                is where the app used to open. */}
            <Routes>
              <Route path="/chat/*" element={<ChatPage />} />
              <Route path="/dashboards/*" element={<DashboardsPage />} />
              <Route path="/reports/*" element={<ReportsPage />} />
              <Route path="/sources/*" element={<DataSourcesPage />} />
              <Route path="/providers/*" element={<LlmProvidersPage />} />
              {/* Not a hidden rail item: a member who types the path lands on
                  Chat like any other unknown address. */}
              {user.role === 'ADMIN' && (
                <Route path="/users" element={<UsersPage currentUser={user} />} />
              )}
              <Route path="/about" element={<AboutPage />} />
              <Route path="*" element={<Navigate to="/chat" replace />} />
            </Routes>
          </main>
        </div>

        {/* The app knew the work was unsaved and used to discard it without a
            word. Rendered here rather than in the page that is dirty, because
            the navigation it interrupts has already left that page's hands. */}
        {blocker.state === 'blocked' && (
          <Modal
            title="You have unsaved changes"
            onClose={() => blocker.reset?.()}
            width={420}
            footer={
              <>
                <GhostButton onClick={() => blocker.reset?.()}>Keep editing</GhostButton>
                <DangerButton onClick={() => blocker.proceed?.()}>
                  Discard and leave
                </DangerButton>
              </>
            }
          >
            <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)', lineHeight: 1.55 }}>
              {blockedReason ?? 'Your edits have not been saved.'}
            </p>
          </Modal>
        )}
      </div>
    </ShellProvider>
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
  user, pathname, onNavigate, theme, onToggleTheme, onLogout,
}: {
  user: User
  pathname: string
  onNavigate: (path: string) => void
  theme: ThemeName
  onToggleTheme: () => void
  onLogout: () => void
}) {
  const items = useMemo(
    () => NAV.filter((item) => !item.adminOnly || user.role === 'ADMIN'),
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
            key={item.path}
            active={isInSection(pathname, item.path)}
            icon={item.icon}
            label={item.label}
            onClick={() => onNavigate(item.path)}
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
          onClick={() => onNavigate('/about')}
          aria-current={pathname === '/about' ? 'page' : undefined}
          aria-label="Creators"
          title="Creators"
          className={`rm-sidebar-about${pathname === '/about' ? ' is-on' : ''}`}
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
