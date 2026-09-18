/**
 * What the signed-in person may do, and the one hook that asks.
 *
 * **The interface renders every affordance from the backend's answer, never
 * from a role string.** Before Phase 3 of the access-control plan the SPA
 * decided what to show by comparing `user.role` to `'ADMIN'` in four places;
 * that is a second, quieter copy of the permission model, and the day the two
 * disagree the product either hides a button somebody is allowed to press or
 * — worse — shows one they are not, so the refusal arrives as a 403 after
 * they have filled in a form.
 *
 * `useCan()` closes that: it reads the capability list `/auth/me` returned,
 * which is the *same set* `deps.needs(...)` will check on the server for the
 * same request. A capability the backend has and the SPA has never heard of
 * simply is not asked about; a capability the SPA asks about and the backend
 * does not grant is `false`. Neither case can invent access.
 *
 * It is deliberately **not** a store with its own fetch. The user object is
 * already owned by `App`, already refreshed on sign-in and after a profile
 * edit, and adding a second source of truth for the same bytes is how a screen
 * ends up rendering permissions from before somebody's role changed.
 */
import { createContext, useContext, useMemo, type ReactNode } from 'react'
import type { User } from './api/types'

/**
 * The capability names this SPA knows how to ask about.
 *
 * A *subset* of the backend's nineteen on purpose — these are the ones a
 * screen here actually branches on. Typing them buys a compile error for a
 * misspelling, which is the failure mode that matters: `can('role.mange')`
 * silently returns false and hides a tab forever.
 */
export type Capability =
  | 'user.read'
  | 'user.manage'
  | 'service_user.manage'
  | 'team.read'
  | 'team.manage'
  | 'role.read'
  | 'role.manage'
  | 'audit.read'
  | 'access.review'
  | 'usage.read'
  | 'connection.create'
  | 'llm_config.create'
  | 'dashboard.create'
  | 'report.create'
  | 'conversation.create'
  | 'settings.manage'
  | 'benchmark.manage'
  | 'eval.run'
  | 'system.maintenance'

interface Permissions {
  can: (capability: Capability) => boolean
  /** True when **any** of these is held. The rail's gate is this shape. */
  canAny: (...capabilities: Capability[]) => boolean
  /** The raw set, for a screen that needs to show it rather than branch on it. */
  capabilities: ReadonlySet<string>
  roles: string[]
}

const NOTHING: Permissions = {
  can: () => false,
  canAny: () => false,
  capabilities: new Set(),
  roles: [],
}

const PermissionsContext = createContext<Permissions>(NOTHING)

/**
 * The default is **nothing**, not everything.
 *
 * A component rendered outside the provider — a test, a story, a mistake —
 * shows the surface of somebody with no permissions. That is the boring
 * outcome; the alternative default renders an administration screen to
 * whoever managed to render it.
 */
export function PermissionsProvider({
  user, children,
}: {
  user: User | null
  children: ReactNode
}) {
  const value = useMemo<Permissions>(() => {
    if (!user) return NOTHING
    const held = new Set(user.capabilities ?? [])
    return {
      can: (capability) => held.has(capability),
      canAny: (...capabilities) => capabilities.some((c) => held.has(c)),
      capabilities: held,
      roles: user.roles ?? [],
    }
  }, [user])

  return (
    <PermissionsContext.Provider value={value}>
      {children}
    </PermissionsContext.Provider>
  )
}

/** `const can = useCan(); can('role.manage')`. */
export function useCan(): Permissions['can'] {
  return useContext(PermissionsContext).can
}

export function usePermissions(): Permissions {
  return useContext(PermissionsContext)
}

/**
 * The capabilities that put the **Administration** row in the rail.
 *
 * Any one of them is enough, and the tabs inside decide the rest — an Auditor
 * gets the row, sees People and Audit, and can change nothing; a DataMind
 * Maintainer gets the row and does not see People at all. A rail item gated on
 * "is an administrator" could express neither.
 */
export const ADMIN_SECTION: Capability[] = [
  'user.read',
  'user.manage',
  'role.read',
  'role.manage',
  'team.read',
  'team.manage',
  'service_user.manage',
  'audit.read',
]

/**
 * What the signed-in person may do to **one** thing, from the privileges the
 * server sent with it.
 *
 * The four verbs are the backend's own `CAN` map (`api/v1/access.py`) — view
 * is `select`, edit is `modify`, delete is `delete`, share is `manage` — and
 * the list arrives already expanded through the lattice, so this is a
 * membership test and not a second copy of the lattice. Every edit control on
 * a dashboard, report, data source, model, knowledge store and semantic layer
 * renders from this; before it existed most of them rendered for everybody and
 * the refusal arrived as a 403 after the click.
 */
export interface Access {
  view: boolean
  edit: boolean
  delete: boolean
  share: boolean
}

export const FULL_ACCESS: Access = { view: true, edit: true, delete: true, share: true }
export const NO_ACCESS: Access = { view: false, edit: false, delete: false, share: false }

export function accessOf(privileges: readonly string[] | null | undefined): Access {
  if (!privileges) return NO_ACCESS
  const held = new Set(privileges)
  return {
    view: held.has('select'),
    edit: held.has('modify'),
    delete: held.has('delete'),
    share: held.has('manage'),
  }
}

/**
 * Whether a data source can be **asked** — `select` on it. The pickers in
 * Chat, the tile editor and the new-report form offer only these: the list
 * endpoint also returns sources a person may merely know exist, and offering
 * one of those is offering a refusal.
 */
export function queryable(row: { privileges?: string[] }): boolean {
  return (row.privileges ?? []).includes('select')
}
