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
 * A *subset* of the backend's eighteen on purpose — these are the ones a
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
