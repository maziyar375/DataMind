/**
 * What a page may ask of the shell around it.
 *
 * Two things live here, and both exist because a page was reaching past its
 * own boundary to get them: the theme, which a dashboard may override while it
 * is open, and the fact that a form has unsaved work in it, which is the only
 * thing that may stop a navigation.
 *
 * Both are *requests*, not writes. `App` stays the single owner — the one
 * caller of `applyTheme`, and the one place the confirm dialog is rendered —
 * so two components can never disagree about what the theme is or about
 * whether it is safe to leave.
 *
 * It is its own module rather than an export of `App.tsx` for the ordinary
 * reason: `App` imports every page, so a page importing back from `App` would
 * close a cycle around the app's entry point.
 */
import { createContext, useCallback, useContext, useEffect } from 'react'

import type { ThemeName } from './theme/tokens'

export type Shell = {
  /**
   * Force a theme for as long as a surface is open, or hand it back with
   * `null`. The shell resolves `override ?? the user's own choice`, so a rail
   * toggle made during an override takes effect the moment it clears.
   */
  requestThemeOverride: (theme: ThemeName | null) => void
  /**
   * Say whether this surface holds unsaved work. `key` identifies the
   * registrant so two dirty forms cannot clear each other's flag; `reason` is
   * shown in the confirm dialog, so it should name what would be lost.
   */
  setUnsaved: (key: string, reason: string | null) => void
}

const ShellContext = createContext<Shell | null>(null)

export const ShellProvider = ShellContext.Provider

export function useShell(): Shell {
  const shell = useContext(ShellContext)
  if (!shell) throw new Error('useShell must be used inside the app shell')
  return shell
}

/**
 * Hold the app at `theme` while this component is mounted.
 *
 * Written as a hook so the release on unmount cannot be forgotten — the bug
 * this replaces was a component restoring a theme it had captured at mount,
 * which was already stale if the user had toggled the rail since.
 */
export function useThemeOverride(theme: ThemeName | null): void {
  const { requestThemeOverride } = useShell()
  useEffect(() => {
    requestThemeOverride(theme)
    return () => requestThemeOverride(null)
  }, [theme, requestThemeOverride])
}

/**
 * Register unsaved work under `key`, and clear it on unmount.
 *
 * `reason` is the sentence the confirm dialog shows; passing `null` means
 * there is nothing to lose right now. A page calls this on every render with
 * its current dirty state, which is why the registration is keyed: the
 * connection form and the provider form can both be open in a session, and
 * the last one to go clean must not speak for the other.
 *
 * Returns a release: a form that has just *saved* itself and is navigating as
 * part of that save must let go before it goes, or the guard stops the page
 * from leaving a form that no longer has anything to lose. The registration
 * has to be gone by the time `navigate` is called, which an effect running
 * after the render cannot promise.
 */
export function useUnsavedWork(key: string, reason: string | null): () => void {
  const { setUnsaved } = useShell()
  useEffect(() => {
    setUnsaved(key, reason)
  }, [key, reason, setUnsaved])
  useEffect(() => () => setUnsaved(key, null), [key, setUnsaved])
  return useCallback(() => setUnsaved(key, null), [key, setUnsaved])
}
