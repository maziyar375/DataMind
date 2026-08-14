/**
 * What an outline will produce if it is generated now.
 *
 * `npm run test:readiness` — DOM-free and framework-free, the arrangement
 * `report-document.ts` uses and for the same reason: the answer is worth
 * getting right and its failure mode is invisible until a generation has
 * already been spent.
 *
 * Two questions, kept apart because they are asked at different moments:
 *
 *  * `readinessOf` counts the outline — how many questions are ready, empty,
 *    refused, unchecked. It is what the status panel reads while the user
 *    works, and it says nothing about what to do.
 *  * `preflightOf` answers the one question the Generate button asks: *what
 *    will be wrong with the document this produces?* Every problem it names is
 *    a state the run really does treat that way, read off `workers/report.py`
 *    rather than guessed:
 *
 *      - a block with **no statement** is skipped and its figure is replaced by
 *        "this block has not been checked" in the finished document
 *        (`_NO_SQL`), and `create_run` refuses only when *every* block is in
 *        that state — so eleven good questions and one unchecked one generate
 *        happily, with a hole in the middle;
 *      - a block whose question was **reworded after somebody wrote its SQL by
 *        hand** keeps that statement (`update_block` drops only a model draft),
 *        so it executes and returns the previous question's numbers under the
 *        new heading — the one failure here that is silent rather than visible;
 *      - a **section with no questions** narrates as `SKIPPED_NO_DATA`: it
 *        appears, with a line saying there were no results;
 *      - an **empty** result is not a failure at all, and the section says so
 *        plainly instead of inventing numbers.
 *
 * The categories are disjoint per block, in that order of severity, because a
 * dialog that counts one question twice is one the reader stops trusting.
 *
 * ## Why `sweepable` is not "everything unchecked"
 *
 * Checking a block that already carries a hand-written statement **replaces
 * it** — the row's own button says "Rewrite" for exactly that reason. So the
 * bulk sweep is offered only over blocks a check would *fill*, and a reworded
 * hand-written block is named in the preflight instead, where the decision to
 * throw away that SQL stays the user's to make one block at a time.
 */
import type { ReportBlock, ReportFeasibility, ReportSection } from '../api/types'

// ── counting the outline ──────────────────────────────────────────────────
export interface ReadinessState {
  blocks: number
  ready: number
  empty: number
  infeasible: number
  unchecked: number
  /** Why the API would refuse to start a run, or null when it would accept. */
  blocked: string | null
  runnable: boolean
}

export function readinessOf(sections: ReportSection[]): ReadinessState {
  const blocks = sections.flatMap((section) => section.blocks)
  const count = (status: ReportFeasibility) =>
    blocks.filter((block) => block.feasibility_status === status).length
  const infeasible = blocks.filter((block) => block.feasibility_status === 'INFEASIBLE')
  const withSql = blocks.filter((block) => hasQuery(block)).length

  let blocked: string | null = null
  if (withSql === 0) {
    blocked = 'No question has a query yet — check them first.'
  }

  return {
    blocks: blocks.length,
    ready: count('FEASIBLE'),
    empty: count('EMPTY'),
    infeasible: infeasible.length,
    unchecked: count('UNCHECKED'),
    blocked,
    runnable: blocked === null,
  }
}

// ── what generating now would produce ─────────────────────────────────────
export type PreflightKind =
  | 'no-questions'
  | 'infeasible'
  | 'no-query'
  | 'stale-sql'
  | 'sectionless'
  | 'empty'

export interface PreflightProblem {
  kind: PreflightKind
  /** Red is a hole in the document; amber is a document that reads oddly. */
  tone: 'red' | 'amber'
  count: number
  /** What is wrong, counted. */
  title: string
  /** What it does to the finished document. One sentence, no jargon. */
  detail: string
  /** The guard's own words, where there are any. Never re-worded. */
  hint?: string
}

export interface Preflight {
  /** Most severe first. Empty when the outline is clean. */
  problems: PreflightProblem[]
  /** Nothing to say: Generate should just generate. */
  clean: boolean
  /** Whether `POST .../runs` would accept this — the API's own rule, mirrored. */
  canGenerate: boolean
  /**
   * Blocks a bulk check would **fill** rather than overwrite, in outline order.
   * The list the "Check them and generate" button walks.
   */
  sweepable: ReportBlock[]
}

export function preflightOf(sections: ReportSection[]): Preflight {
  const blocks = sections.flatMap((section) => section.blocks)

  const infeasible = blocks.filter((block) => block.feasibility_status === 'INFEASIBLE')
  // Disjoint from the refusals above: rewording a refused block clears both its
  // status and the guard's sentence, so nothing is counted twice.
  const unchecked = blocks.filter((block) => block.feasibility_status === 'UNCHECKED')
  const noQuery = unchecked.filter((block) => !hasQuery(block))
  const staleSql = unchecked.filter((block) => hasQuery(block))
  const empty = blocks.filter((block) => block.feasibility_status === 'EMPTY')
  const sectionless = sections.filter(
    (section) => section.kind !== 'EXECUTIVE_SUMMARY' && section.blocks.length === 0,
  )

  const problems: PreflightProblem[] = []

  if (blocks.length === 0) {
    problems.push({
      kind: 'no-questions',
      tone: 'red',
      count: sections.length,
      title: 'This outline has no questions.',
      detail:
        'Every paragraph is written from the results of a question, and there are none '
        + 'to run — so there would be nothing to write from.',
    })
  }

  if (noQuery.length > 0) {
    problems.push({
      kind: 'no-query',
      tone: 'red',
      count: noQuery.length,
      title: `${quantify(noQuery.length, 'question has', 'questions have')} never been checked.`,
      detail:
        'A question that has never been near the guard carries no query, so it arrives in '
        + 'the finished document as an error message where its figure should be.',
    })
  }

  if (staleSql.length > 0) {
    problems.push({
      kind: 'stale-sql',
      tone: 'red',
      count: staleSql.length,
      title:
        staleSql.length === 1
          ? 'A question was reworded after its SQL was written by hand.'
          : `${staleSql.length} questions were reworded after their SQL was written by hand.`,
      detail:
        'The statement is kept — losing hand-written SQL to a typo fix would be worse — but '
        + 'it answers the previous wording, so it would run and put those numbers under the '
        + 'new heading. Re-check it from its row, where replacing your SQL is your call.',
    })
  }

  if (infeasible.length > 0) {
    problems.push({
      kind: 'infeasible',
      tone: 'red',
      count: infeasible.length,
      title: `${quantify(infeasible.length, 'question cannot', 'questions cannot')} be produced.`,
      detail:
        'Re-checking will refuse again — this is answered by rewording the question, or by '
        + 'writing the statement yourself.',
      hint: infeasible[0].feasibility_reason ?? undefined,
    })
  }

  if (sectionless.length > 0) {
    problems.push({
      kind: 'sectionless',
      tone: 'amber',
      count: sectionless.length,
      title: `${quantify(sectionless.length, 'section has', 'sections have')} no questions.`,
      detail:
        'A section with nothing under it has no results to be written from, so it appears '
        + 'in the document as a heading and a line saying there were none.',
    })
  }

  if (empty.length > 0) {
    problems.push({
      kind: 'empty',
      tone: 'amber',
      count: empty.length,
      title: `${quantify(empty.length, 'question returns', 'questions return')} no rows.`,
      detail:
        'Not a failure: the query works and nothing falls in the window. The section says so '
        + 'plainly rather than showing an empty chart.',
    })
  }

  return {
    problems,
    clean: problems.length === 0,
    canGenerate: blocks.some((block) => hasQuery(block)),
    sweepable: noQuery,
  }
}

/**
 * Why nothing can be generated from this report at all, or `null`.
 *
 * Distinct from everything above, and the distinction is the whole reason this
 * is a separate function. `preflightOf` describes an outline that is *fixable*
 * — check these questions, fill that section — which is why the Generate button
 * stays live and opens a dialog that offers the fix. This describes a report
 * that cannot be generated no matter what the user does in the editor, because
 * the database it was built against no longer exists. There is no fix to offer,
 * so the controls are disabled rather than lying about what a click will do —
 * and the reason is rendered in the page, not only in a `title`, because a
 * greyed control whose explanation lives in a tooltip is unreadable on a
 * touchscreen and unreachable from a keyboard.
 *
 * Every past run stays readable; only new ones are refused. That is the same
 * answer `report_service` gives on all four of its write paths, and saying it
 * here first turns a 422 into a sentence the user reads before clicking.
 */
export function generationBlockedBy(report: {
  connection_id: string | null
  connection_name: string | null
}): string | null {
  if (report.connection_id !== null) return null
  const which = report.connection_name ? ` (${report.connection_name})` : ''
  return (
    `The database this report was built against${which} has been deleted, so ` +
    'it cannot be generated or checked again. Every run it already produced ' +
    'stays readable.'
  )
}

/**
 * Whether this block would be executed by a run.
 *
 * The worker's own test, character for character: `if block.sql.strip()`. A
 * verdict is not consulted there, which is why an unchecked block with a
 * statement still runs — and why this asks about the statement, not the chip.
 */
function hasQuery(block: ReportBlock): boolean {
  return block.sql.trim() !== ''
}

/** "1 question has", "3 questions have" — the count, and the verb it governs. */
function quantify(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}
