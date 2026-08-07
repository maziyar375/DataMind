/**
 * The merge that makes a run render as a document.
 *
 * `npm run test:report` — Node runs this file directly (type stripping, no
 * bundler, no test framework, no new dependency), the same arrangement
 * `dashboard-schedule.test.ts` uses and for the same reason: one pure function
 * whose failure modes are invisible until a real generation is half finished.
 *
 * The cases below are the states a reader actually watches go by — numbers with
 * no paragraph yet, an executive summary that arrives last but belongs first, a
 * section deleted from the outline since the run — not invented ones.
 */
import { assembleDocument, chartTypeOf, isEdited, proseOf, renderKindOf } from './report-document.ts'
import type { ReportBlockResult, ReportRunDetail, ReportSectionResult } from '../api/types.ts'

const AT = '2026-08-01T12:00:00Z'

function block(
  id: string,
  sectionId: string | null,
  position: number,
  heading: string,
  extra: Partial<ReportBlockResult> = {},
): ReportBlockResult {
  return {
    id,
    block_id: `b-${id}`,
    section_id: sectionId,
    position,
    heading_snapshot: heading,
    question_snapshot: 'revenue by month',
    sql_text: 'SELECT 1',
    sql_hash: 'abc',
    columns: [],
    rows: [],
    row_count: 0,
    truncated: false,
    vega_spec: null,
    chart_source: null,
    chart_note: null,
    kpi: null,
    computed_at: AT,
    duration_ms: 4,
    status: 'OK',
    error_code: null,
    error_message: null,
    ...extra,
  }
}

function prose(
  id: string,
  sectionId: string | null,
  position: number,
  heading: string,
  extra: Partial<ReportSectionResult> = {},
): ReportSectionResult {
  return {
    id,
    section_id: sectionId,
    position,
    heading_snapshot: heading,
    prose: 'The model wrote this.',
    edited_prose: null,
    numeric_check: null,
    status: 'OK',
    error_message: null,
    created_at: AT,
    ...extra,
  }
}

function run(
  blocks: ReportBlockResult[],
  sections: ReportSectionResult[],
  extra: Partial<ReportRunDetail> = {},
): ReportRunDetail {
  return {
    id: 'run-1',
    report_id: 'rep-1',
    status: 'RUNNING',
    phase: 'Writing روند درآمد',
    progress_current: 2,
    progress_total: 7,
    llm_config_id: null,
    model_snapshot: {},
    prompt_version: 'r1',
    language: 'en',
    error_message: null,
    started_at: AT,
    finished_at: null,
    created_at: AT,
    blocks,
    sections,
    ...extra,
  }
}

let failures = 0
function check(name: string, actual: unknown, expected: unknown): void {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures += 1
  console.log(
    ok
      ? `ok    ${name}`
      : `FAIL  ${name}\n        got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`,
  )
}

const headings = (r: ReportRunDetail) => assembleDocument(r).map((s) => s.heading)

// ── the order ────────────────────────────────────────────────────────────
check(
  'sections come out in outline order, from the blocks',
  headings(run(
    [block('1', 's1', 0, 'Trend'), block('2', 's1', 1, 'Trend'), block('3', 's2', 2, 'Products')],
    [],
  )),
  ['Trend', 'Products'],
)

check(
  'the block list is sorted, not trusted',
  headings(run([block('3', 's2', 2, 'Products'), block('1', 's1', 0, 'Trend')], [])),
  ['Trend', 'Products'],
)

// The one that motivated this file: the summary has no blocks, so it exists
// only once its paragraph lands — and it lands last, while belonging first.
check(
  'an executive summary written last still comes out first',
  headings(run(
    [block('1', 's1', 0, 'Trend'), block('2', 's2', 1, 'Products')],
    [prose('p1', 's1', 1, 'Trend'), prose('p2', 's2', 2, 'Products'), prose('p0', 'sum', 0, 'Summary')],
  )),
  ['Summary', 'Trend', 'Products'],
)

check(
  'a block-less section in the middle lands in the middle',
  headings(run(
    [block('1', 's1', 0, 'One'), block('2', 's3', 1, 'Three')],
    [prose('p2', 's2', 1, 'Two')],
  )),
  ['One', 'Two', 'Three'],
)

// ── the half-written state the reader watches ────────────────────────────
const midRun = assembleDocument(run(
  [block('1', 's1', 0, 'Trend'), block('2', 's2', 1, 'Products')],
  [prose('p1', 's1', 0, 'Trend')],
))
check('numbers render before their paragraph exists', midRun.map((s) => s.prose !== null), [true, false])
check('every block lands under its own section', midRun.map((s) => s.blocks.length), [1, 1])

// ── rows a changed outline left behind ───────────────────────────────────
check(
  'a result whose section was deleted still gets a heading of its own',
  headings(run([block('1', null, 0, 'Deleted but readable')], [])),
  ['Deleted but readable'],
)
check(
  'and it is marked unretryable rather than dropped',
  assembleDocument(run([block('1', null, 0, 'Gone')], [])).map((s) => s.sectionId),
  [null],
)
check(
  'two orphaned results do not collapse into one section',
  headings(run([block('1', null, 0, 'A'), block('2', null, 1, 'B')], [])),
  ['A', 'B'],
)

// ── the paragraph itself ─────────────────────────────────────────────────
check('an unedited paragraph reads as the model wrote it', proseOf(prose('p', 's', 0, 'H')), 'The model wrote this.')
check(
  'an edited one reads as the user wrote it',
  proseOf(prose('p', 's', 0, 'H', { edited_prose: 'I wrote this.' })),
  'I wrote this.',
)
check('null means not edited, not edited to nothing', isEdited(prose('p', 's', 0, 'H')), false)
check(
  'an empty edit is still an edit',
  isEdited(prose('p', 's', 0, 'H', { edited_prose: '' })),
  true,
)

// ── what to draw ─────────────────────────────────────────────────────────
check('a failed block is an error, whatever else it carries', renderKindOf(block('1', 's', 0, 'H', { status: 'FAILED', kpi: { value: '1', raw: 1, label: 'x', caption: null, delta: null, sparkline: [] } })), 'error')
check('a kpi outranks a chart', renderKindOf(block('1', 's', 0, 'H', { kpi: { value: '1', raw: 1, label: 'x', caption: null, delta: null, sparkline: [] }, vega_spec: { mark: 'bar' } })), 'kpi')
check('a spec is a chart', renderKindOf(block('1', 's', 0, 'H', { vega_spec: { mark: 'bar' } })), 'chart')
check('everything else falls back to the table', renderKindOf(block('1', 's', 0, 'H')), 'table')

check('the chart type is read off the compiled spec', chartTypeOf({ usermeta: { datamind: { chart_type: 'line' } } }), 'line')
check('a spec without one says nothing rather than guessing', chartTypeOf({ mark: 'bar' }), '')
check('no spec at all is no type', chartTypeOf(null), '')

if (failures > 0) {
  // A thrown error is the non-zero exit; `process` would need Node's types.
  throw new Error(`${failures} document checks failed`)
}
console.log('\nall passed')
