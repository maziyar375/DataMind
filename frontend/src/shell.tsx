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

import type { QueueRow } from './components/knowledge-queue'
import type { ThemeName } from './theme/tokens'

/**
 * Something finished while you were somewhere else.
 *
 * Deliberately not an error channel. In-page errors stay where they are — an
 * `ErrorNote` beside the thing that failed is the better pattern and moving
 * them into a corner of the screen would make every failure less legible, not
 * more. This is for work that outlives the screen that started it: a semantic
 * layer that took four minutes to write, a benchmark run, a sweep that found
 * two templates disagreeing.
 */
export type Notice = {
  tone: 'ok' | 'warn' | 'error'
  /** One line. It is read aloud, so it must make sense without the body. */
  title: string
  body?: string
  /** Where the finished work lives, so the notice is also the way back. */
  to?: string
  toLabel?: string
}

/**
 * A job to keep watching after the page that started it has gone.
 *
 * The shell knows nothing about semantic layers or benchmark runs: a page
 * hands over a `poll` that answers *"is it done, and what should I say?"* —
 * `null` while it continues, a `Notice` when it is finished. A poll that
 * throws drops the task silently, because a background watcher that starts
 * reporting network weather is worse than one that gives up.
 *
 * `key` de-duplicates: registering the same job twice (a page remounting on
 * the way back to it) watches it once.
 */
export type BackgroundTask = {
  key: string
  poll: () => Promise<Notice | null>
}

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
   *
   * `within` is the address the work survives inside — a record's own path,
   * usually. Moving between the tabs of one connection is a navigation, and
   * without this the guard stopped a form from reaching the tab beside it to
   * protect edits that a tab switch does not touch. A confirm dialog people
   * learn to click through is worse than no dialog at all.
   */
  setUnsaved: (key: string, reason: string | null, within?: string) => void
  /** Say that something finished. See `Notice` for what belongs here. */
  notify: (notice: Notice) => void
  /** Keep polling this after the page that started it has unmounted. */
  watch: (task: BackgroundTask) => void
  /**
   * How much curation work is waiting, per connection — the rail's badge.
   *
   * Held by the shell rather than by the console, because its whole purpose
   * is to be visible from everywhere *except* the console. Empty until the
   * first count lands, and empty again if the count cannot be taken: a badge
   * that guesses is worse than one that is absent.
   */
  queue: QueueRow[]
  /** Re-count every connection. Taken once at sign-in. */
  refreshQueue: () => void
  /**
   * One connection's count, reported by the console that just read it.
   *
   * The knowledge screen loads both feeds anyway — that is what draws its
   * two sections — so telling the shell what it found keeps the badge exact
   * without a single extra request. Resolving a flag updates the rail before
   * the reader has looked away from the row they resolved.
   */
  noteQueueFor: (
    connectionId: string,
    counts: { name: string; reviews: number; suggestions: number },
  ) => void
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
 * `within` names the address the work is safe inside, so a page whose tabs
 * are routes is not stopped from moving between them. Everything outside it
 * still asks.
 *
 * Returns a release: a form that has just *saved* itself and is navigating as
 * part of that save must let go before it goes, or the guard stops the page
 * from leaving a form that no longer has anything to lose. The registration
 * has to be gone by the time `navigate` is called, which an effect running
 * after the render cannot promise.
 */
export function useUnsavedWork(
  key: string, reason: string | null, within?: string,
): () => void {
  const { setUnsaved } = useShell()
  useEffect(() => {
    setUnsaved(key, reason, within)
  }, [key, reason, within, setUnsaved])
  useEffect(() => () => setUnsaved(key, null), [key, setUnsaved])
  return useCallback(() => setUnsaved(key, null), [key, setUnsaved])
}

/**
 * Is `pathname` at, or inside, `root`?
 *
 * Segment-aware on purpose: a plain `startsWith` makes `/sources/abc` a
 * parent of `/sources/abcdef`, and two connections whose ids share a prefix
 * would hand each other's unsaved work a free pass.
 */
export function isWithin(pathname: string, root: string): boolean {
  return pathname === root || pathname.startsWith(`${root}/`)
}

/**
 * Announce something that finished, from anywhere.
 *
 * A page that is still open should keep showing its own result inline as
 * well — this is the copy for whoever has already moved on.
 */
export function useNotify(): (notice: Notice) => void {
  return useShell().notify
}

/** Hand a long-running job to the shell, so its ending survives this page. */
export function useBackgroundWatch(): (task: BackgroundTask) => void {
  return useShell().watch
}

/** The curation queue, for the rail badge and the tab count. */
export function useQueue(): {
  rows: QueueRow[]
  refresh: () => void
  noteFor: Shell['noteQueueFor']
} {
  const { queue, refreshQueue, noteQueueFor } = useShell()
  return { rows: queue, refresh: refreshQueue, noteFor: noteQueueFor }
}
