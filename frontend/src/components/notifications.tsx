/**
 * One place the app says that something finished.
 *
 * The product has several operations that are minutes long and deliberately
 * queued — writing a semantic layer, scoring a benchmark set, sweeping a
 * knowledge store — and until now their completion was visible only if you
 * were still on the tab that started them. Start a four-minute generation,
 * go and read a dashboard, and nothing ever told you it was done.
 *
 * **What does not belong here.** In-page errors. A failed save says so beside
 * the button that failed, in an `ErrorNote`, and always will: a corner of the
 * screen is a worse place to explain a form than the form is. This surface is
 * for events that outlive the screen that produced them, which is a much
 * smaller set than "things worth saying".
 *
 * It is a live region, so a screen reader hears the title without moving
 * focus — `polite`, because none of this is urgent enough to interrupt a
 * sentence being read. Focus is never taken: an announcement that stole the
 * caret from a form would be a worse bug than the silence it replaced.
 */
import { useEffect } from 'react'
import type { Notice } from '../shell'
import { GhostButton, Icon } from './ui'

/** Long enough to notice and read, short enough not to become furniture. */
const DISMISS_AFTER_MS = 9000

export type ShownNotice = Notice & { id: number }

export function Notifications({
  notices, onDismiss, onGo,
}: {
  notices: ShownNotice[]
  onDismiss: (id: number) => void
  onGo: (to: string, id: number) => void
}) {
  return (
    <div
      // `role="status"` carries an implicit `aria-live="polite"`; both are
      // written out because the implicit one is not honoured everywhere.
      role="status"
      aria-live="polite"
      aria-label="Background activity"
      className="rm-notices"
    >
      {notices.map((notice) => (
        <NoticeCard
          key={notice.id}
          notice={notice}
          onDismiss={() => onDismiss(notice.id)}
          onGo={notice.to ? () => onGo(notice.to!, notice.id) : undefined}
        />
      ))}
    </div>
  )
}

function NoticeCard({
  notice, onDismiss, onGo,
}: {
  notice: ShownNotice
  onDismiss: () => void
  onGo?: () => void
}) {
  // A notice with somewhere to go waits to be dealt with; one that is only
  // information gets out of the way on its own. Dismissing something a
  // reader might still want to click is how a "go to it" link becomes a
  // race against a timer.
  useEffect(() => {
    if (onGo) return
    const timer = setTimeout(onDismiss, DISMISS_AFTER_MS)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notice.id])

  const colour =
    notice.tone === 'error' ? 'var(--red)'
      : notice.tone === 'warn' ? 'var(--amber)'
        : 'var(--green)'

  return (
    <div className="rm-notice" style={{ ['--rm-notice-tone' as string]: colour }}>
      <span aria-hidden style={{ color: colour, display: 'flex', paddingTop: 1 }}>
        {notice.tone === 'ok'
          ? <Icon.Check size={15} stroke={colour} />
          : <Icon.Alert size={15} stroke={colour} />}
      </span>
      <div style={{ minWidth: 0, flex: 1, display: 'flex', flexDirection: 'column', gap: 3 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-strong)' }}>
          {notice.title}
        </span>
        {notice.body && (
          <span style={{ fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.5 }}>
            {notice.body}
          </span>
        )}
        {onGo && (
          <span style={{ marginTop: 4 }}>
            <GhostButton onClick={onGo} style={{ padding: '5px 10px', fontSize: 12 }}>
              {notice.toLabel ?? 'Open'}
            </GhostButton>
          </span>
        )}
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss"
        className="rm-icon-btn"
        style={{
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 24,
          height: 24,
          borderRadius: 7,
          border: 'none',
          background: 'transparent',
          color: 'var(--text-faint)',
          cursor: 'pointer',
          ['--rm-hover-bg' as string]: 'var(--panel-hover)',
        }}
      >
        <Icon.Close size={12} />
      </button>
    </div>
  )
}
