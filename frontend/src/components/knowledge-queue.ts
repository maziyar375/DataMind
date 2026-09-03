/**
 * How much curation work is waiting, across every connection.
 *
 * The learning loop's console — flags people raised, questions nothing
 * answers, templates that stopped working — lived three clicks deep inside
 * one connection's fourth tab. Its queue was therefore invisible unless
 * somebody went looking, which is the wrong way round: a queue is only worth
 * keeping if it can ask for attention.
 *
 * This file is the arithmetic behind that badge, kept DOM-free and tested
 * because every one of its failure modes is quiet. A badge that shows a
 * number the screen behind it does not show is worse than no badge — the
 * reader clicks, finds nothing, and stops believing the next one. So the
 * counting rules here mirror exactly what `KnowledgeTab` renders, and the
 * two must move together.
 */

/** One connection's share of the queue. */
export interface QueueRow {
  connectionId: string
  name: string
  /** Flags a human raised on a wrong answer. Unambiguous work. */
  reviews: number
  /**
   * The ranked backlog — questions asked that nothing here answers.
   *
   * Counted **excluding** `FLAGGED`, because the tab renders it that way: a
   * flag is already in `reviews`, and counting it twice would show a badge of
   * four over a list of two.
   */
  suggestions: number
}

/** What one connection is asking of a curator. */
export function waiting(row: QueueRow): number {
  return row.reviews + row.suggestions
}

export function totalWaiting(rows: QueueRow[]): number {
  return rows.reduce((sum, row) => sum + waiting(row), 0)
}

/**
 * The badge's text, or nothing at all.
 *
 * `undefined` at zero rather than `"0"`: a badge that is always there is
 * decoration, and one that appears when there is work is a signal. Past 99 it
 * stops counting — the difference between 100 and 137 changes no decision,
 * and a four-character badge changes the width of the rail.
 */
export function badge(total: number): string | undefined {
  if (total <= 0) return undefined
  return total > 99 ? '99+' : String(total)
}

/** One connection's count, for the tab that shows only that connection. */
export function forConnection(
  rows: QueueRow[], connectionId: string,
): number | undefined {
  const row = rows.find((r) => r.connectionId === connectionId)
  if (!row) return undefined
  const total = waiting(row)
  return total > 0 ? total : undefined
}

/**
 * The connections with work first, then the rest, each half alphabetical.
 *
 * The console's whole promise is "you can see where the work is without
 * opening anything", and a list in creation order breaks it the moment there
 * are more connections than fit on a screen. Alphabetical *within* each half
 * so the order is stable between visits — sorting purely by count makes rows
 * jump under the cursor every time a flag is resolved.
 */
export function byUrgency(rows: QueueRow[]): QueueRow[] {
  return [...rows].sort((a, b) => {
    const busy = Number(waiting(b) > 0) - Number(waiting(a) > 0)
    return busy !== 0 ? busy : a.name.localeCompare(b.name)
  })
}

/**
 * What the console's header says about the whole queue.
 *
 * Names the connections rather than only totalling them, up to two of them:
 * "6 waiting" tells a curator to start looking, "4 on sales, 2 on Aurora
 * Coffee" tells them where.
 *
 * Sorted by count here, unlike `byUrgency` — and the difference is the point.
 * A *list* must hold still between visits or rows jump under the cursor, so
 * it orders alphabetically within the busy half; a sentence that names two of
 * twenty connections has to name the two worth opening first.
 */
export function queueSentence(rows: QueueRow[]): string {
  const busy = rows
    .filter((row) => waiting(row) > 0)
    .sort((a, b) => waiting(b) - waiting(a) || a.name.localeCompare(b.name))
  const total = totalWaiting(rows)
  if (total === 0) {
    return rows.length === 0
      ? 'Nothing connected yet.'
      : 'Nothing is waiting for you.'
  }
  // Trimmed for the sentence and only for the sentence: a name stored with a
  // stray space is still that record's name and the list shows it as stored,
  // but "22 on Aurora Coffee , 20 on sales" reads as a typo in the product.
  const named = busy.slice(0, 2).map((row) => `${waiting(row)} on ${row.name.trim()}`)
  const rest = busy.length - named.length
  if (rest > 0) named.push(`${rest} more ${rest === 1 ? 'connection' : 'connections'}`)
  return `${total} waiting — ${named.join(', ')}.`
}
