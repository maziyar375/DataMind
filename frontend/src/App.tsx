import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Navigate, Route, Routes, useBlocker, useLocation, useNavigate,
  type BlockerFunction,
} from 'react-router-dom'
import { auth, connections, getAccessToken, knowledge, onAuthChange } from './api/client'
import type { User } from './api/types'
import { DangerButton, GhostButton, Icon, Logo, Modal, initialOf } from './components/ui'
import AboutPage from './pages/AboutPage'
import AccountPage from './pages/AccountPage'
import KnowledgePage from './pages/KnowledgePage'
import ChatPage from './pages/ChatPage'
import DashboardsPage from './pages/DashboardsPage'
import DataSourcesPage from './pages/DataSourcesPage'
import LlmProvidersPage from './pages/LlmProvidersPage'
import LoginPage from './pages/LoginPage'
import ReportsPage from './pages/ReportsPage'
import UsersPage from './pages/UsersPage'
import { badge, queueTone, totalWaiting } from './components/knowledge-queue'
import type { QueueRow } from './components/knowledge-queue'
import { Notifications, type ShownNotice } from './components/notifications'
import { ShellProvider, isWithin, type BackgroundTask, type Shell } from './shell'
import { applyTheme, type ThemeName } from './theme/tokens'

/**
 * The rail's destinations, ordered by how often a row is opened — which is
 * also the order the product is used, and the order in which its rows stop
 * being about today's work and start being about keeping it running. The list
 * stays flat and uncaptioned (see `docs/frontend.md`); the ordering is what
 * carries the grouping, so it must not zig-zag across that boundary.
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
  // Last of the four you *work* in rather than first of the three you keep,
  // because that is what it is: Chat raises the flags, and teaching one
  // changes what Chat answers next. It closes the loop the three rows above
  // it open. It sat under Data sources for a while on the grounds that a
  // console belongs beside what it curates, but that is object-adjacency,
  // and it is the weaker claim — the way in from one connection is the
  // Knowledge tab, which navigates here, so no discovery ever depended on
  // the rail. What the old position cost was an order that fell out of daily
  // use into a settings page and climbed back out, with the product's only
  // badge stranded below the line where keeping-it-running begins. A count
  // down there reads as a setting needing attention, which is the wrong kind
  // of urgency for somebody's answer having been wrong. It is *in* the rail
  // at all because the finding it answers is that its queue was invisible: a
  // console three clicks inside one connection's fourth tab cannot ask for
  // anything. The count is the argument for the row — if it is permanently
  // empty on a real install, this row has not earned its place.
  { path: '/knowledge', label: 'Knowledge', icon: <Icon.Book /> },
  // Then the three you configure, in the order their frequency falls away: a
  // connection is edited occasionally — a policy, a schema sync after a
  // migration — a provider key almost never once it works, and Users is
  // admin-only and rarer still.
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
  const unsaved = useRef(new Map<string, { reason: string; within?: string }>())

  // What finished while the reader was elsewhere, and the long jobs still
  // being waited on. The tasks are a ref for the same reason `unsaved` is:
  // one interval reads them, and registering a job is not a reason to
  // re-render the app around it.
  const [notices, setNotices] = useState<ShownNotice[]>([])
  const nextNoticeId = useRef(1)
  const tasks = useRef(new Map<string, BackgroundTask>())

  // The curation queue, counted here so the rail can show it from any page.
  //
  // A fan-out — two reads per connection — because there is no cross-
  // connection endpoint for either feed, and adding one would have been the
  // expensive way to answer a question the client can already ask. It is
  // taken once at sign-in and again after anything that changes the queue,
  // never on a timer: work arrives at the speed people flag answers, and a
  // badge that polls every connection every minute would cost more than it
  // tells anyone. If this list grows past a handful of connections, one
  // `GET /knowledge/queue` replaces the whole thing.
  const [queue, setQueue] = useState<QueueRow[]>([])
  /**
   * One connection's count, as reported by the console that just read it.
   *
   * Two things here are load-bearing, and both are about not chasing your own
   * tail. The callback is stable (`[]`), because the console derives its data
   * loader from it — a `noteQueueFor` that changed identity whenever the
   * queue changed would make the loader change, re-run the effect that calls
   * it, and fetch forever. And an unchanged count returns the *same array*,
   * so a screen that re-reads its store and finds nothing new re-renders
   * nothing above it.
   */
  const noteQueueFor = useCallback<Shell['noteQueueFor']>(
    (connectionId, counts) =>
      setQueue((current) => {
        const known = current.find((row) => row.connectionId === connectionId)
        if (
          known
          && known.name === counts.name
          && known.reviews === counts.reviews
          && known.suggestions === counts.suggestions
        ) {
          return current
        }
        const next = { connectionId, ...counts }
        return known
          ? current.map((row) => (row.connectionId === connectionId ? next : row))
          : [...current, next]
      }),
    [],
  )

  const refreshQueue = useCallback(async () => {
    try {
      const rows = await connections.list()
      setQueue(
        await Promise.all(
          rows.map(async (row) => {
            const [reviews, suggestions] = await Promise.all([
              knowledge.reviews(row.id).catch(() => []),
              knowledge.suggestions(row.id).catch(() => []),
            ])
            return {
              connectionId: row.id,
              name: row.name,
              reviews: reviews.length,
              // FLAGGED entries are the same items as `reviews`; the tab
              // filters them out of its backlog list and so does this, or
              // the badge would show four over a list of two.
              suggestions: suggestions.filter((s) => s.kind !== 'FLAGGED').length,
            }
          }),
        ),
      )
    } catch {
      // A badge that cannot be counted is absent, never guessed.
      setQueue([])
    }
  }, [])

  const shell = useMemo<Shell>(
    () => ({
      requestThemeOverride: setThemeOverride,
      setUnsaved: (key, reason, within) => {
        if (reason) unsaved.current.set(key, { reason, within })
        else unsaved.current.delete(key)
      },
      notify: (notice) =>
        setNotices((current) => [
          // Newest last, so the stack grows downward and an arriving notice
          // never pushes the one being read out from under the cursor.
          ...current.slice(-2),
          { ...notice, id: nextNoticeId.current++ },
        ]),
      watch: (task) => {
        if (!tasks.current.has(task.key)) tasks.current.set(task.key, task)
      },
      queue,
      refreshQueue,
      noteQueueFor,
    }),
    [queue, refreshQueue, noteQueueFor],
  )

  // One timer for every background job, rather than one per job: the page
  // that started a job usually polls it too (that is what draws its progress
  // bar), and this exists only so the *ending* survives that page being
  // closed. Five seconds because nothing here is a progress bar.
  useEffect(() => {
    const timer = setInterval(async () => {
      for (const [key, task] of [...tasks.current]) {
        try {
          const notice = await task.poll()
          if (notice) {
            tasks.current.delete(key)
            shell.notify(notice)
          }
        } catch {
          // A watcher that reports network weather is worse than one that
          // gives up: the page that owns this job will say so if it is open.
          tasks.current.delete(key)
        }
      }
    }, 5000)
    return () => clearInterval(timer)
  }, [shell])

  // One guard for the whole app rather than a check per page: every way out of
  // a dirty form — the rail, a row in the master column, browser Back — is a
  // navigation, and this is where they all pass through.
  //
  // A registration can name the address its work survives inside (`within`),
  // and then it is not a reason to stop: the tabs of one connection are
  // separate routes, and asking someone to confirm the loss of edits that a
  // tab switch does not touch is how a guard becomes a thing people click
  // through without reading.
  const stoppers = useCallback(
    (nextPath: string) =>
      [...unsaved.current.values()].filter(
        (work) => !work.within || !isWithin(nextPath, work.within),
      ),
    [],
  )
  const shouldBlock = useCallback<BlockerFunction>(
    ({ currentLocation, nextLocation }) =>
      currentLocation.pathname !== nextLocation.pathname
      && stoppers(nextLocation.pathname).length > 0,
    [stoppers],
  )
  const blocker = useBlocker(shouldBlock)
  // The reason the *pending* navigation was stopped, not merely the first
  // dirty thing on the app: with two forms registered, one of which permits
  // this move, naming the wrong one would explain a dialog by pointing at
  // something the reader is not leaving.
  const blockedReason = blocker.state === 'blocked'
    ? stoppers(blocker.location.pathname)[0]?.reason
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

  // Once, when there is someone to count for. Not in the `restore` effect:
  // that one also runs for a visitor who turns out not to be signed in.
  useEffect(() => {
    if (user) refreshQueue()
    else setQueue([])
  }, [user, refreshQueue])

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
            the rail is seven destinations plus a footer group, and a keyboard user
            should not have to walk them to reach the page they opened. */}
        <a className="rm-skip" href="#main">Skip to content</a>
        {/* The two shell boxes carry classes so the print stylesheet can reach
            them: both are viewport-sized scroll containers, and on paper there
            is no viewport to be sized to — see `@media print`. The inner one is
            also the document's <main>, which is what the skip link targets. */}
        <div className="rm-app-row" style={{ display: 'flex', height: '100vh', width: '100%' }}>
          <Sidebar
            user={user}
            queueBadge={badge(totalWaiting(queue))}
            queueTone={queueTone(queue)}
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
              <Route path="/knowledge/*" element={<KnowledgePage />} />
              <Route path="/providers/*" element={<LlmProvidersPage />} />
              {/* Not a hidden rail item: a member who types the path lands on
                  Chat like any other unknown address. */}
              {user.role === 'ADMIN' && (
                <Route path="/users" element={<UsersPage currentUser={user} />} />
              )}
              {/* `/settings` at last means the account, which is what the
                  word says. It held the LLM providers until routing moved
                  them to `/providers`. */}
              <Route
                path="/settings"
                element={<AccountPage user={user} onUserChange={setUser} />}
              />
              <Route path="/about" element={<AboutPage />} />
              <Route path="*" element={<Navigate to="/chat" replace />} />
            </Routes>
          </main>
        </div>

        <Notifications
          notices={notices}
          onDismiss={(id) => setNotices((current) => current.filter((n) => n.id !== id))}
          onGo={(to, id) => {
            setNotices((current) => current.filter((n) => n.id !== id))
            navigate(to)
          }}
        />

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
 * captions over seven items are furniture, and the split invited a decision
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
  user, queueBadge, queueTone, pathname, onNavigate, theme, onToggleTheme, onLogout,
}: {
  user: User
  /** The curation queue's size, or nothing when there is nothing waiting. */
  queueBadge?: string
  /** Red for a flag somebody raised, amber for a backlog of questions. */
  queueTone?: 'red' | 'amber' | 'neutral'
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
            badge={item.path === '/knowledge' ? queueBadge : undefined}
            badgeTone={queueTone}
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
          {/* The name and the avatar are now the door to the account screen,
              rather than a label beside a sign-out icon. It is where the
              display name is already shown, which makes it the place someone
              looks when they want to change it — and until F6 there was
              nowhere to go at all: every route under `/users` is admin-only,
              so an invited member was stuck on the password an administrator
              generated for them. Sign out keeps its own button: leaving and
              editing are not the same intention and must not share a target. */}
          <button
            type="button"
            onClick={() => onNavigate('/settings')}
            aria-current={pathname === '/settings' ? 'page' : undefined}
            title="Your account"
            className={`rm-sidebar-me${pathname === '/settings' ? ' is-on' : ''}`}
          >
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
          <div className="rm-sidebar-text" style={{ display: 'flex', flexDirection: 'column', minWidth: 0, lineHeight: 1.25, textAlign: 'start' }}>
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
          </button>
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
  active, icon, label, badge, badgeTone, onClick,
}: {
  active: boolean
  icon: React.ReactNode
  label: string
  /** A count worth interrupting for. Absent, never "0". */
  badge?: string
  /**
   * How loudly to say it. Red is reserved for a defect somebody reported;
   * a backlog of unanswered questions is amber, because a mark that cries
   * wolf about a backlog is one people stop looking at — which is the exact
   * failure this badge was added to fix.
   */
  badgeTone?: 'red' | 'amber' | 'neutral'
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={`rm-nav-btn${active ? ' is-on' : ''}`}
      // The count is in the tooltip too: on the 66px rail the label is hidden
      // and the badge is a number floating beside a glyph, which says how
      // many of *what* only to someone who already knows.
      title={badge ? `${label} — ${badge} waiting` : label}
    >
      {icon}
      <span className="rm-sidebar-text">{label}</span>
      {badge && (
        <>
          <span
            aria-hidden
            className={`rm-nav-badge${badgeTone === 'amber' ? ' is-amber' : ''}`}
          >
            {badge}
          </span>
          {/* The number means nothing read aloud on its own. */}
          <span className="rm-sr">{badge} waiting</span>
        </>
      )}
    </button>
  )
}
