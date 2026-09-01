/**
 * The model's reasoning channel, as the chat holds it.
 *
 * A reasoning model does not begin by writing; it thinks first, for as long as
 * it wants to, on a stream that carries no answer. The chat used to render
 * nothing at all for that — a step chip on `describe` and an empty paragraph,
 * for what has been measured in this codebase at seven minutes — which is
 * indistinguishable from a crash. `REASONING_DELTA` is what arrives instead,
 * and this is the state it accumulates into.
 *
 * Kept out of `chat.tsx` because that file is JSX and this is arithmetic:
 * `npm run test:thinking` runs the cases below it directly.
 */

/** What the panel renders: the thought so far, how long, and whether it ended. */
export type ThinkingState = {
  /** The tail of the reasoning — see `REASONING_TAIL_CHARS`. */
  text: string
  /** Milliseconds from the first thought to the most recent one. */
  ms: number
  /** The answer has started; the clock stops and the label changes tense. */
  done: boolean
}

/**
 * How much reasoning is kept in memory.
 *
 * The panel shows a scrolling tail, and a model that thinks for two minutes
 * produces far more than anyone scrolls back through — so the head is dropped
 * rather than held. Nothing readable is lost that was not already going to be:
 * the server does not store this channel either, by design.
 */
export const REASONING_TAIL_CHARS = 6000

/** `47s`, then `1m 04s`. Seconds are padded so a ticking clock does not jitter. */
export function thoughtTime(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000))
  if (total < 60) return `${total}s`
  return `${Math.floor(total / 60)}m ${String(total % 60).padStart(2, '0')}s`
}

/**
 * Fold one `REASONING_DELTA` into the state.
 *
 * `elapsedMs` is the server's measurement and wins over the previous one even
 * when it is smaller — it is the authority on how long the model has been at
 * it, and the panel's own clock is only there to move the number between
 * events.
 */
export function absorbThought(
  prev: ThinkingState | null,
  delta: string,
  elapsedMs: number | undefined,
): ThinkingState {
  const joined = (prev?.text ?? '') + delta
  return {
    text: joined.length > REASONING_TAIL_CHARS
      ? joined.slice(-REASONING_TAIL_CHARS)
      : joined,
    ms: elapsedMs ?? prev?.ms ?? 0,
    done: false,
  }
}

/**
 * The first word of the answer has arrived: stop the clock, keep the panel.
 *
 * How long an answer took to start is part of what happened, so the line stays
 * — collapsed, reading "Thought for 47s" — rather than disappearing at the one
 * moment the reader learns what the wait was for.
 */
export function endThought(prev: ThinkingState | null): ThinkingState | null {
  return prev ? { ...prev, done: true } : prev
}
