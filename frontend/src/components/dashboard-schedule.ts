/**
 * Which tiles are due — the whole of the refresh policy, as one pure function.
 *
 * It lives in a file of its own, with no React and no imports, because it is
 * the rule the feature turns on: get it wrong in the "too eager" direction and
 * a forgotten browser tab becomes a load generator pointed at the customer's
 * database; get it wrong the other way and a 30-second tile silently shows
 * ten-minute-old numbers. Separated out, it can be read — and exercised —
 * without a DOM.
 */

export interface SchedulableTile {
  id: string
  tile_type: string
  /** The rate the backend already resolved: tile's own, else the dashboard's. */
  effective_refresh_interval_seconds: number
}

export interface SchedulableResult {
  computed_at: string
}

/**
 * The ids of the tiles whose interval has elapsed since the result they are
 * currently showing.
 *
 * Three rules, all of them deliberate:
 *
 * * A **TEXT** tile computes nothing, ever.
 * * An interval of **0 is manual** — never due on a tick, only on a request.
 *   This is why `NULL` (inherit) and `0` are different values in the database:
 *   the backend resolves the inheritance, and a resolved `0` means "only when
 *   asked".
 * * A tile with **no result yet** is due immediately, which is what makes the
 *   first paint fetch everything without a second code path.
 */
export function dueTileIds(
  tiles: SchedulableTile[],
  results: Record<string, SchedulableResult | undefined>,
  now: number = Date.now(),
): string[] {
  const due: string[] = []
  for (const tile of tiles) {
    if (tile.tile_type === 'TEXT') continue
    const interval = tile.effective_refresh_interval_seconds
    if (interval <= 0) continue

    const result = results[tile.id]
    if (!result) {
      due.push(tile.id)
      continue
    }
    const computedAt = new Date(result.computed_at).getTime()
    // An unparseable stamp is treated as "due" rather than "never": a tile
    // that can never refresh again is the worse failure of the two.
    if (Number.isNaN(computedAt) || now - computedAt >= interval * 1000) {
      due.push(tile.id)
    }
  }
  return due
}
