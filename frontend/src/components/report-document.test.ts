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
import {
  assembleDocument, chartTypeOf, figureNumbers, isEdited, keyFigures, proseOf,
  renderKindOf, summaryParts,
} from './report-document.ts'
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

// ── the summary, and the numbering that flows from it ────────────────────
// A section with no blocks *is* the executive summary — there is no `kind` on a
// result row and none is needed, because it is the one section written from
// other sections' prose rather than from queries, and `outline.py` refuses to
// keep a proposed section with no blocks.
{
  const doc = assembleDocument(
    run(
      [block('1', 's1', 0, 'Revenue'), block('2', 's2', 1, 'Products')],
      [prose('p0', 's0', 0, 'Executive summary')],
    ),
  )
  check('the block-less section is read as the summary', doc.map((s) => s.isSummary), [
    true, false, false,
  ])
  check('and it is numbered outside the sequence', doc.map((s) => s.number), [0, 1, 2])
}

check(
  'a section still being written is numbered anyway',
  assembleDocument(run([block('1', 's1', 0, 'Revenue')], [])).map((s) => s.number),
  [1],
)

// ── the findings a summary states ────────────────────────────────────────
check(
  'a lead paragraph and its findings come apart',
  summaryParts('Revenue grew.\n\n- Up 12% to 1.4M.\n- North leads at 31%.'),
  { lead: 'Revenue grew.', findings: ['Up 12% to 1.4M.', 'North leads at 31%.'] },
)
check(
  'a model that reached for a different bullet is not punished for it',
  summaryParts('Lead.\n• One.\n– Two.\n* Three.').findings,
  ['One.', 'Two.', 'Three.'],
)
check(
  'a summary with no findings is all lead',
  summaryParts('Just a paragraph.\nAnd another line.'),
  { lead: 'Just a paragraph. And another line.', findings: [] },
)
check(
  'findings with no lead still render',
  summaryParts('- Only this.'),
  { lead: '', findings: ['Only this.'] },
)
check(
  'a wrapped finding stays with the finding above it',
  summaryParts('- Up 12%,\ncontinued here.').findings,
  ['Up 12%, continued here.'],
)
check('an empty summary comes apart into nothing', summaryParts(''), { lead: '', findings: [] })

// ── figures, numbered across the whole document ──────────────────────────
{
  const doc = assembleDocument(
    run(
      [
        block('a', 's1', 0, 'Revenue'),
        block('b', 's1', 1, 'Revenue'),
        block('c', 's2', 2, 'Products'),
      ],
      [prose('p0', 's0', 0, 'Executive summary')],
    ),
  )
  check(
    'figures are numbered in reading order, across sections',
    [...figureNumbers(doc).entries()],
    [['a', 1], ['b', 2], ['c', 3]],
  )
}

// ── the headline band ────────────────────────────────────────────────────
{
  const kpi = { value: '1.4M', raw: 1.4, label: 'Revenue', caption: null, delta: null, sparkline: [] }
  const doc = assembleDocument(
    run(
      [
        block('a', 's1', 0, 'Revenue', { kpi }),
        block('b', 's1', 1, 'Revenue'),
        block('c', 's2', 2, 'Products', { kpi, status: 'FAILED' }),
      ],
      [],
    ),
  )
  check(
    'only computed figures that actually landed reach the band',
    keyFigures(doc).map((f) => f.block.id),
    ['a'],
  )
  check('and each carries the heading it was computed under', keyFigures(doc)[0].heading, 'Revenue')
}

check(
  'the band is capped rather than becoming a wall',
  keyFigures(
    assembleDocument(
      run(
        [0, 1, 2, 3, 4, 5].map((i) =>
          block(`k${i}`, `s${i}`, i, `H${i}`, {
            kpi: { value: '1', raw: 1, label: 'x', caption: null, delta: null, sparkline: [] },
          }),
        ),
        [],
      ),
    ),
  ).length,
  4,
)

if (failures > 0) {
  // A thrown error is the non-zero exit; `process` would need Node's types.
  throw new Error(`${failures} document checks failed`)
}
console.log('\nall passed')
