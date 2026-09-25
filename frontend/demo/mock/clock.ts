/**
 * The demo's clock: `DEMO_TODAY` in the afternoon, running from the moment the
 * session started.
 *
 * Everything with a timestamp — the sidebar's conversations, the audit log,
 * the usage chart, a run started a minute ago — lives on this clock, so the
 * app never shows a conversation from "yesterday" whose SQL answered for a
 * different today.
 *
 * The offset from the real clock is kept for the session, not recomputed per
 * page load. A run is stored with the demo time it started at; a clock that
 * restarted from its anchor on every reload would put that start in the
 * future, and a finished answer would come back as a run still in flight.
 */
import { DEMO_TODAY } from './fixtures/today'

/** When the demo's day "is" at the start of a session. */
const ANCHOR = Date.parse(`${DEMO_TODAY}T15:40:00Z`)
const KEY = 'datamind-demo:clock:v1'

function sessionOffset(): number {
  try {
    const stored = sessionStorage.getItem(KEY)
    if (stored !== null && Number.isFinite(Number(stored))) return Number(stored)
  } catch {
    /* no storage: the clock starts at the anchor on every load */
  }
  const offset = ANCHOR - Date.now()
  try {
    sessionStorage.setItem(KEY, String(offset))
  } catch {
    /* as above */
  }
  return offset
}

const OFFSET = sessionOffset()

export function demoNow(): number {
  return Date.now() + OFFSET
}

export function demoIso(ms: number = demoNow()): string {
  return new Date(ms).toISOString()
}

/** `days` before DEMO_TODAY, at `hour` UTC. */
export function daysAgo(days: number, hour = 12): string {
  const day = Date.parse(`${DEMO_TODAY}T00:00:00Z`) - days * 86_400_000
  return new Date(day + hour * 3_600_000).toISOString()
}
