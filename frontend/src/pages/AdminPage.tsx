/**
 * Administration: people, roles, and — as later phases land — teams, service
 * accounts, access review and the audit log.
 *
 * One section rather than five rail rows, and the reason is the thing this
 * whole plan is about. Before Phase 3 the rail had **Users**, gated on
 * `role === 'ADMIN'` (authz-ok: prose), and that gate could express exactly
 * one idea: you are an
 * administrator or you are nobody. The product now has eight roles, and two of
 * them exist precisely to sit between those: an **Auditor** reads people and
 * the audit log and changes nothing anywhere; a **DataMind Maintainer** keeps
 * the installation running and cannot see the People list at all.
 *
 * So the row is gated on *holding any administration capability*, and each tab
 * appears only when the capability that tab needs is held. Both facts come
 * from `/auth/me` — the same capability set the API will check on the next
 * request — which is what makes "the interface shows exactly what the backend
 * would allow" a property rather than an aspiration.
 *
 * **The URL is the tab.** `/admin/people` and `/admin/roles` are addresses
 * somebody can link to, and the section redirects to the first tab its viewer
 * actually has. A person with no administration capability at all never
 * reaches this component: the route is not registered for them, and they land
 * on Chat like any other unknown address.
 */
import { useMemo } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import type { User } from '../api/types'
import { PageHeader } from '../components/ui'
import { Tabs } from '../components/settings'
import { useCan, type Capability } from '../permissions'
import RolesTab from './RolesTab'
import ServiceAccountsTab from './ServiceAccountsTab'
import TeamsTab from './TeamsTab'
import UsersPage from './UsersPage'

interface TabSpec {
  value: string
  label: string
  /** The one capability that puts this tab on the screen. */
  needs: Capability
}

/**
 * The section's tabs, in the order the plan introduces them.
 *
 * Access review and Audit arrive in Phases 9 and 7 as more entries here —
 * which is the point of building the shell now rather than a second standalone
 * page each time.
 */
const TABS: TabSpec[] = [
  { value: 'people', label: 'People', needs: 'user.read' },
  { value: 'roles', label: 'Roles', needs: 'role.read' },
  // `team.read` is held by five of the eight seed roles, so this tab is the
  // one most people in the installation will see — which is deliberate: from
  // Phase 6 a team is how somebody will have been given access to anything,
  // and "which teams am I in" stops being a curiosity.
  { value: 'teams', label: 'Teams', needs: 'team.read' },
  // The one tab a **DataMind Maintainer** sees. That pairing is the point
  // rather than an accident of the seed: running the installation and
  // administering people are different jobs, and minting an agent belongs to
  // the first — so this role gets the Administration row and no People list.
  { value: 'service-accounts', label: 'Service accounts', needs: 'service_user.manage' },
]

export default function AdminPage({ user }: { user: User }) {
  const can = useCan()
  const navigate = useNavigate()
  const { tab } = useParams<{ tab?: string }>()

  const visible = useMemo(() => TABS.filter((entry) => can(entry.needs)), [can])

  // No tab at all is still a real state — a custom role could carry one of the
  // rail's capabilities and none of the tabs' — and saying so is better than
  // an empty frame.
  if (visible.length === 0) {
    return (
      <div className="rm-index rm-page-pad" style={{ flex: 1, overflowY: 'auto' }}>
        <PageHeader
          title="Administration"
          subtitle="Nothing here needs your attention — your roles carry no permission that this section manages."
        />
      </div>
    )
  }

  const active = visible.find((entry) => entry.value === tab)
  if (!active) return <Navigate to={`/admin/${visible[0].value}`} replace />

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 }}>
      <div style={{ padding: '20px 28px 0' }}>
        <PageHeader
          title="Administration"
          subtitle="Who can sign in, what each of them may do, and how those permissions are defined."
        />
      </div>

      <Tabs
        value={active.value}
        onChange={(next) => navigate(`/admin/${next}`)}
        items={visible.map((entry) => ({ value: entry.value, label: entry.label }))}
      />

      {/* Each tab owns its own scrolling: People is an index that scrolls as a
          page, Roles is a master–detail whose two columns scroll apart. A
          wrapper that scrolled for both would give the roles list a second
          scrollbar inside the one it already has. */}
      {active.value === 'people' ? (
        <UsersPage currentUser={user} embedded />
      ) : active.value === 'roles' ? (
        <RolesTab />
      ) : active.value === 'teams' ? (
        <TeamsTab />
      ) : (
        <ServiceAccountsTab />
      )}
    </div>
  )
}
