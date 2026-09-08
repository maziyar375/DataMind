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
import AccessReviewTab from './AccessReviewTab'
import AuditTab from './AuditTab'
import RolesTab from './RolesTab'
import ServiceAccountsTab from './ServiceAccountsTab'
import TeamsTab from './TeamsTab'
import UsersPage from './UsersPage'

interface TabSpec {
  value: string
  label: string
  /** The one capability that puts this tab on the screen. */
  needs: Capability
  /**
   * The line under the section title while this tab is open.
   *
   * It lives here rather than in the tab, because the section already carries
   * the title and the strip — the same bargain `UsersPage`'s `embedded` flag
   * struck, extended to the two tabs that arrived later and kept a standalone
   * page's chrome. The Audit log and Access review each drew a second <h1> of
   * the same size directly under "Administration", which reads as two pages
   * stacked rather than one section with a tab open. Their own sentences are
   * the ones below, verbatim: the heading was the duplicate, not the prose.
   */
  blurb: string
}

/**
 * The section's tabs, in the order the plan introduces them.
 *
 * Access review arrived in Phase 9 as one more entry here — which was the
 * point of building the shell rather than a second standalone page each time.
 */
const TABS: TabSpec[] = [
  {
    value: 'people',
    label: 'People',
    needs: 'user.read',
    blurb: 'Who can sign in, what each of them may do, and how they get a password.',
  },
  {
    value: 'roles',
    label: 'Roles',
    needs: 'role.read',
    blurb: 'Every set of permissions this installation defines, and who holds each one.',
  },
  // `team.read` is held by five of the eight seed roles, so this tab is the
  // one most people in the installation will see — which is deliberate: from
  // Phase 6 a team is how somebody will have been given access to anything,
  // and "which teams am I in" stops being a curiosity.
  {
    value: 'teams',
    label: 'Teams',
    needs: 'team.read',
    blurb: 'Named groups a permission can be granted to, so access outlives whoever set it up.',
  },
  // The one tab a **DataMind Maintainer** sees. That pairing is the point
  // rather than an accident of the seed: running the installation and
  // administering people are different jobs, and minting an agent belongs to
  // the first — so this role gets the Administration row and no People list.
  {
    value: 'service-accounts',
    label: 'Service accounts',
    needs: 'service_user.manage',
    blurb: 'Machine identities and their keys — what each may do, and when it last did it.',
  },
  // Last, because it is the one somebody arrives at with a question rather
  // than a task. `audit.read` is Administrator's and **Auditor's** — the role
  // that exists to read this and change nothing anywhere, which is the whole
  // reason it is a capability rather than an administrator flag.
  // Beside the audit log, and second-to-last for the same reason: both are
  // arrived at with a question rather than a task, and they answer the two
  // halves of one — the log says what happened, the review says what is
  // possible. `access.review` is Administrator's and **Auditor's**.
  {
    value: 'access',
    label: 'Access review',
    needs: 'access.review',
    blurb:
      'What one person, machine or team can reach — or everyone who can reach one '
      + 'thing. Every row says how, so you know what to revoke.',
  },
  {
    value: 'audit',
    label: 'Audit log',
    needs: 'audit.read',
    blurb:
      'Who did what, and what was refused. Every permission change, every share, '
      + 'and every denial.',
  },
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
    // `rm-section` is the surface, and it is the whole point of the wrapper:
    // one accent wash, thrown from above the title so it runs behind the
    // header and the strip and fades out under whichever tab is open. Before
    // it, three of these six tabs painted their own `rm-index` wash *below*
    // the strip and three painted none, so the section's background changed
    // as you moved along its own tabs — and where it was painted, it began
    // with a hard horizontal edge at the tab border.
    <div
      className="rm-section"
      style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 }}
    >
      <div className="rm-section-head">
        <div className="rm-section-title">
          <PageHeader title="Administration" subtitle={active.blurb} />
        </div>

        {/* 18, not the default 28: a tab keeps 14px for its own hover pill, so
            this is what puts the first label on the same 32px edge as the
            title above it and the tab's content below. */}
        <Tabs
          value={active.value}
          gutter={18}
          onChange={(next) => navigate(`/admin/${next}`)}
          items={visible.map((entry) => ({ value: entry.value, label: entry.label }))}
        />
      </div>

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
      ) : active.value === 'service-accounts' ? (
        <ServiceAccountsTab />
      ) : active.value === 'access' ? (
        <AccessReviewTab />
      ) : (
        <AuditTab />
      )}
    </div>
  )
}
