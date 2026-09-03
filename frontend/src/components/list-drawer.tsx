/**
 * The second column, on a phone.
 *
 * Every working page in this product is two columns inside the rail: a list
 * of things on the left, the thing itself on the right. Below 860px the rail
 * already collapses to a 66px icon strip, but the second column did not give
 * ground with it — 208px of chat list, 274px of settings index — so on a
 * 375px screen roughly a hundred pixels were left for the transcript, the
 * form, or the report. Not cramped: unusable.
 *
 * Below 700px that column becomes an overlay instead, in the same treatment
 * `.rm-drawer` already gives the dashboard's settings panel: fixed beside the
 * rail, over the content rather than beside it, with a scrim behind it. The
 * page reclaims its full width and the list is one tap away.
 *
 * Three rules, and they are why this is a shared module rather than four
 * implementations:
 *
 *  - **It closes when you pick something.** Every list here navigates, so the
 *    close is keyed on the path changing rather than on each page remembering
 *    to call back. A drawer left open over the thing you just chose is the
 *    single most common way this pattern is got wrong.
 *  - **Escape closes it**, like every other layer in the app.
 *  - **The toggle only exists where the drawer does.** Above the breakpoint
 *    the column is simply a column, and a button that opens what is already
 *    open is furniture — so the toggle is hidden in CSS at exactly the width
 *    the drawer stops being one, and the two rules live next to each other in
 *    `styles.css`.
 */
import { useCallback, useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { Icon } from './ui'

/** The id the toggle points `aria-controls` at. One list column per page. */
export const LIST_DRAWER_ID = 'list-column'

export function useListDrawer(): {
  open: boolean
  toggle: () => void
  close: () => void
} {
  const [open, setOpen] = useState(false)
  const { pathname } = useLocation()

  // Picking anything in these lists is a navigation, so this is "close on
  // select" for every page at once — including the ones whose lists grow a
  // new kind of row later.
  useEffect(() => setOpen(false), [pathname])

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  return {
    open,
    toggle: useCallback(() => setOpen((current) => !current), []),
    close: useCallback(() => setOpen(false), []),
  }
}

/**
 * The button that opens it, for a page header.
 *
 * `label` names what is inside — "Chats", "Data sources" — because on the
 * screen where this appears the column's own heading is off-canvas, and
 * "Menu" beside a rail that is already a menu says nothing.
 */
export function ListToggle({
  open, label, onClick,
}: {
  open: boolean
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rm-list-toggle"
      aria-expanded={open}
      aria-controls={LIST_DRAWER_ID}
      aria-label={open ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
      title={label}
    >
      <Icon.List size={15} />
    </button>
  )
}

/**
 * The dimmed page behind an open drawer.
 *
 * Rendered by the page rather than by the column so it sits *under* the
 * column in the stacking order without either of them having to declare a
 * z-index relative to the other. `aria-hidden`: it is a target, not
 * information, and the Escape key does the same job for the keyboard.
 */
export function ListScrim({ open, onClick }: { open: boolean; onClick: () => void }) {
  if (!open) return null
  return <div aria-hidden className="rm-list-scrim" onClick={onClick} />
}
