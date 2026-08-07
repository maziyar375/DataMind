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
    }
    byKey.set(key, created)
    sections.splice(Math.min(paragraph.position, sections.length), 0, created)
  }

  return sections
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
