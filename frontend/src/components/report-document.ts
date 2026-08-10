/**
 * Turning one poll response into a document.
 *
 * `GET /reports/{id}/runs/{rid}` returns two flat lists — every block result
 * written so far, and every paragraph written so far — and the viewer has to
 * render them as sections in outline order *while the run is still writing
 * them*. That merge is the whole progressive render, and it is fiddly enough to
 * be worth having on its own, DOM-free and tested: `npm run test:report`.
 *
 * Three facts about the two lists make it fiddly, and all three come from
 * `workers/report.py`:
 *
 *  1. **The numbers arrive before the prose.** Every block is executed first,
 *     then the sections are narrated one at a time. So a section exists on
 *     screen — heading, charts, tables — for a while before it has a paragraph,
 *     and that half-drawn state is exactly what the reader is watching.
 *  2. **The two lists count in different spaces.** A section result's
 *     `position` is its index in the *outline*. A block result's `position` is
 *     its index among *all the blocks of the run*. Sorting one by the other's
 *     numbers puts section seven above section two.
 *  3. **A section may have no blocks at all.** The executive summary is
 *     written last, from the other sections' prose, and carries no questions of
 *     its own — so it appears only when its paragraph lands, and it belongs at
 *     whatever position the user put it, which is normally first.
 *
 * The order therefore comes from the blocks (their list is already in outline
 * order, because the worker enumerates sections then blocks) and any section
 * that has no blocks is inserted at the index its own `position` claims.
 */
import type { ReportBlockResult, ReportRunDetail, ReportSectionResult } from '../api/types'

export interface DocumentSection {
  /**
   * The section's id, or a synthetic key.
   *
   * `section_id` is SET NULL when the section is deleted from the outline,
   * because a run must stay readable after the structure it came from changes.
   * Such rows still have their heading and their numbers, so they still render
   * — they just cannot be retried or edited, which is what `section_id: null`
   * tells the viewer.
   */
  key: string
  sectionId: string | null
  heading: string
  /** The paragraph, once it has been written. Null while the run is on its way. */
  prose: ReportSectionResult | null
  blocks: ReportBlockResult[]
  /**
   * The executive summary, which a document leads with rather than numbers.
   *
   * Read off the blocks rather than a field, because there is no field: a
   * `report_section_results` row carries no `kind`. It needs none — the summary
   * is the one section written from other sections' prose instead of from
   * queries, so it is the one section with no blocks, and `outline.py` refuses
   * to keep a proposed section that has none. What the viewer needs from this
   * is real: the summary is numbered differently, styled differently, and its
   * "- " lines are findings rather than prose.
   */
  isSummary: boolean
  /**
   * Its place in the reader's numbering — 1, 2, 3 — or 0 for the summary, which
   * sits outside the sequence the way an abstract does.
   */
  number: number
}

/** What the reader sees in a paragraph: their own words if they wrote any. */
export function proseOf(section: ReportSectionResult | null): string {
  if (!section) return ''
  return section.edited_prose ?? section.prose
}

/** Whether this paragraph has been written over. NULL means *not edited*. */
export function isEdited(section: ReportSectionResult | null): boolean {
  return section?.edited_prose != null
}

/**
 * The document, in outline order, from whatever the run has written so far.
 *
 * Never throws and never drops a row: a result whose section is unknown still
 * gets a heading of its own rather than vanishing, because a document that
 * silently loses a section is not a document.
 */
export function assembleDocument(run: ReportRunDetail): DocumentSection[] {
  const sections: DocumentSection[] = []
  const byKey = new Map<string, DocumentSection>()

  // The blocks carry the order. They are written in outline order — sections
  // enumerated, then each section's blocks — so first appearance *is* rank.
  for (const block of [...run.blocks].sort((a, b) => a.position - b.position)) {
    const key = block.section_id ?? `orphan:${block.id}`
    let entry = byKey.get(key)
    if (!entry) {
      entry = {
        key,
        sectionId: block.section_id,
        heading: block.heading_snapshot,
        prose: null,
        blocks: [],
        isSummary: false,
        number: 0,
      }
      byKey.set(key, entry)
      sections.push(entry)
    }
    entry.blocks.push(block)
  }

  // Then the paragraphs. One that belongs to a section already on screen fills
  // it in; one that belongs to a section with no blocks — the executive
  // summary, normally — is inserted where its own position claims, which is a
  // *section* index and so is meaningful against this list.
  const paragraphs = [...run.sections].sort((a, b) => a.position - b.position)
  for (const paragraph of paragraphs) {
    const key = paragraph.section_id ?? `prose:${paragraph.id}`
    const entry = byKey.get(key)
    if (entry) {
      entry.prose = paragraph
      // The paragraph's heading is the later snapshot of the two, and the two
      // are equal in every ordinary case.
      if (paragraph.heading_snapshot) entry.heading = paragraph.heading_snapshot
      continue
    }
    const created: DocumentSection = {
      key,
      sectionId: paragraph.section_id,
      heading: paragraph.heading_snapshot,
      prose: paragraph,
      blocks: [],
      isSummary: true,
      number: 0,
    }
    byKey.set(key, created)
    sections.splice(Math.min(paragraph.position, sections.length), 0, created)
  }

  // Numbered last, over the assembled order, so the reader's "3" is the third
  // heading they can see and not the third row the worker happened to write.
  // The summary is skipped rather than numbered 1: it is the page you read
  // instead of the report, not its first chapter.
  let counter = 0
  for (const section of sections) {
    section.number = section.isSummary ? 0 : ++counter
  }

  return sections
}

/**
 * The findings a summary states, split from the paragraph that opens it.
 *
 * `REPORT_SUMMARY_SYSTEM` asks for a lead paragraph, a blank line, then 3 to 5
 * lines beginning with "- ". That is the shape of every executive summary a
 * business reader has ever been handed, and rendering it as one undifferentiated
 * block would throw away the only structure in the document the model is asked
 * to produce.
 *
 * Tolerant on purpose, because a model that ignores the format must not cost the
 * reader the text: a summary with no bullets comes back as a lead and no
 * findings, and bullets with no lead come back as findings and an empty lead.
 * Both render.
 */
export function summaryParts(prose: string): { lead: string; findings: string[] } {
  const lead: string[] = []
  const findings: string[] = []

  for (const raw of prose.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    // A model asked for "- " will sometimes give "•", "–" or "*" instead, and
    // the reader should not pay for that.
    const bullet = /^[-–—*•]\s+(.*)$/.exec(line)
    if (bullet) {
      findings.push(bullet[1].trim())
    } else if (findings.length === 0) {
      lead.push(line)
    } else {
      // Prose after the findings began: a wrapped bullet, so it belongs to the
      // one above rather than to a lead that is already closed.
      findings[findings.length - 1] += ` ${line}`
    }
  }

  return { lead: lead.join(' '), findings }
}

/**
 * What a block is captioned with: a statement, falling back to its question.
 *
 * A caption is the one line of a figure every reader reads, and a document
 * whose captions are questions reads as a transcript of the session that
 * produced it. `title_snapshot` is that statement, written by the outline
 * prompt and editable in the outline; the question stays with the query, in
 * the appendix and the query panel, where provenance belongs.
 *
 * Empty is the ordinary case for a run generated before blocks had titles, and
 * for one whose author cleared the field — so the fallback is not a defensive
 * `??`, it is the documented meaning of an empty title.
 */
export function captionOf(block: ReportBlockResult): string {
  return (block.title_snapshot || '').trim() || block.question_snapshot
}

/**
 * Whether this block is a callout rather than an exhibit.
 *
 * One number is not a figure. An exhibit is something a reader studies —
 * structure over time, across categories, against a total — and giving a
 * headline number its own numbered slot fills a quarter of a page with
 * information the sentence beside it already carried, while training the
 * reader that a figure number is not worth turning to. Every reporting
 * convention treats a single value the same way: a scorecard at the top, a
 * callout in the flow, never Figure 4.
 *
 * Read off what was *produced*, like `renderKindOf` — a METRIC block whose
 * query came back with a hundred rows is a table, and is drawn and numbered as
 * one.
 */
export function isCallout(block: ReportBlockResult): boolean {
  return renderKindOf(block) === 'kpi'
}

/**
 * The figures of the document, numbered in reading order.
 *
 * A professional report cites "Figure 4", and it can only do that if the number
 * is a property of the document rather than of the section — so this is computed
 * once over the assembled sections and read by both the body and the appendix.
 * A block that is still being written has no number yet and simply is not in the
 * map, which is what makes it safe to call mid-run.
 *
 * Callouts are skipped rather than numbered, so the numbering counts what a
 * reader can actually be pointed at. A block that is *not* in the map is
 * therefore two different things — a callout, or a figure the run has not
 * written yet — and only the caller with the block in hand can tell them
 * apart, which is what `isCallout` is for.
 */
export function figureNumbers(sections: DocumentSection[]): Map<string, number> {
  const numbers = new Map<string, number>()
  let counter = 0
  for (const section of sections) {
    for (const block of section.blocks) {
      if (isCallout(block)) continue
      numbers.set(block.id, ++counter)
    }
  }
  return numbers
}

/** One headline figure, with what the document calls it. */
export interface KeyFigure {
  block: ReportBlockResult
  /**
   * The block's caption, not its section's heading.
   *
   * The band is read before the document, so each tile has to say what its
   * number *is* — "Monthly revenue, last twelve months" — where a heading says
   * only which part of the report it came from. It is also what the number is
   * labelled with in the section below, so a reader meets the same figure
   * under the same words twice.
   */
  caption: string
}

/**
 * The figures the document opens on, gathered from wherever they were computed.
 *
 * Every one of these is a `plan_kpi` result — computed from result rows by the
 * same planner chat and dashboard tiles use, never written by a model. That is
 * what makes a band of them safe to put above the prose: it is the one part of
 * the document that cannot be wrong about a number.
 *
 * Capped, because a strip of nine stops being a summary and becomes a wall.
 */
export function keyFigures(sections: DocumentSection[], limit = 4): KeyFigure[] {
  const figures: KeyFigure[] = []
  for (const section of sections) {
    for (const block of section.blocks) {
      if (block.status === 'OK' && block.kpi) {
        figures.push({ block, caption: captionOf(block) })
      }
    }
  }
  return figures.slice(0, limit)
}

/** How far along a run is, as the header renders it. */
export interface RunProgress {
  /** 0–100, and never runs backwards past the total the run declared. */
  percent: number
  current: number
  total: number
  phase: string
}

export function progressOf(run: ReportRunDetail): RunProgress {
  const total = Math.max(run.progress_total, 0)
  const current = Math.min(Math.max(run.progress_current, 0), total || run.progress_current)
  return {
    percent: total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0,
    current,
    total,
    phase: run.phase,
  }
}

/**
 * The chart type a stored spec was drawn as.
 *
 * The compiler stamps it into `usermeta`, which is how the picker can show what
 * is currently on screen without a round trip — the same read `chat.tsx` makes
 * of a run's chart artifact.
 */
export function chartTypeOf(spec: Record<string, unknown> | null): string {
  if (!spec) return ''
  const meta = spec.usermeta as { datamind?: { chart_type?: string } } | undefined
  return meta?.datamind?.chart_type ?? ''
}

/**
 * How one block result should be drawn.
 *
 * A block result carries no `block_type` — it carries what was *produced*, and
 * that is the honest thing to render from: a METRIC block whose query stopped
 * returning a single number has no KPI to draw, and a CHART block the planner
 * vetoed has no spec. Each falls back to the table, because the numbers are
 * correct whatever happened to the picture.
 */
export function renderKindOf(block: ReportBlockResult): 'error' | 'kpi' | 'chart' | 'table' {
  if (block.status !== 'OK') return 'error'
  if (block.kpi) return 'kpi'
  if (block.vega_spec) return 'chart'
  return 'table'
}
