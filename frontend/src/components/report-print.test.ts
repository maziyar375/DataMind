/**
 * The print geometry, and the one number it shares with a stylesheet.
 *
 * `npm run test:print` — Node runs this file directly (type stripping, no
 * bundler, no test framework, no new dependency), the same arrangement
 * `report-document.test.ts` and `dashboard-schedule.test.ts` use.
 *
 * There is a real drift hazard here and it is the reason this file exists.
 * `.rm-report` is given its page width in `styles.css`, in millimetres, and
 * `report-print.ts` redraws every chart against that same width converted to
 * pixels. The two live in different languages and nothing at build time makes
 * them agree — so the last check below reads the stylesheet and asserts they
 * still do. A margin changed in one place and not the other prints charts that
 * overhang the page, which is exactly the failure nobody sees until a PDF
 * reaches somebody else's desk.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import {
  MIN_PRINT_CHART_WIDTH_PX, PRINT_CONTENT_WIDTH_MM, PRINT_CONTENT_WIDTH_PX,
  printChartWidth,
} from './report-print.ts'

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

// ── the page ─────────────────────────────────────────────────────────────
check('A4 less the 14mm margins on both sides', PRINT_CONTENT_WIDTH_MM, 182)
// 182mm at 96 CSS px to the inch. Print px are defined by the inch, not by the
// screen, so this is a conversion rather than an estimate.
check('and the same measure in print pixels', PRINT_CONTENT_WIDTH_PX, 688)

// ── the inset ────────────────────────────────────────────────────────────
// A chart sits inside a figure that has padding and a border, inside a chart
// frame that has its own. Together that is a fixed number of pixels at any
// article width, which is what makes measuring it on screen legitimate.
check(
  'a chart 52px narrower than its article keeps that inset on paper',
  printChartWidth(860, 808),
  688 - 52,
)
check(
  'and the same chart in a narrow window yields the same page width',
  printChartWidth(520, 468),
  688 - 52,
)
check(
  'a full-bleed chart gets the whole measure',
  printChartWidth(860, 860),
  688,
)

// An unmeasurable article — a print fired before layout, a detached node —
// must not turn into a negative inset that widens the chart past the page.
check('an unmeasured article falls back to the full measure', printChartWidth(0, 0), 688)
check('so does a chart wider than its own article', printChartWidth(600, 900), 688)

// The floor. Reachable only if a chart is nested far enough that its insets
// exceed the page, but a negative width renders as nothing at all.
check('an absurd inset stops at the floor', printChartWidth(2000, 100), MIN_PRINT_CHART_WIDTH_PX)

// ── the number the stylesheet also holds ─────────────────────────────────
const css = readFileSync(fileURLToPath(new URL('../styles.css', import.meta.url)), 'utf8')
const declared = /\.rm-report\s*\{[^}]*max-width:\s*(\d+(?:\.\d+)?)mm/.exec(css)
check('styles.css gives .rm-report a page width in mm', declared !== null, true)
check(
  'and it is the width the charts are redrawn against',
  declared === null ? null : Number(declared[1]),
  PRINT_CONTENT_WIDTH_MM,
)

// The font this whole phase exists for has to be served by us, not fetched.
check(
  'Vazirmatn is self-hosted rather than requested from a CDN',
  /@font-face[^}]*vazirmatn-arabic\.woff2/.test(css),
  true,
)
const html = readFileSync(fileURLToPath(new URL('../../index.html', import.meta.url)), 'utf8')
check(
  'and index.html no longer asks Google Fonts for it',
  /fonts\.googleapis\.com\/css2[^"]*Vazirmatn/.test(html),
  false,
)

if (failures > 0) {
  // A thrown error is the non-zero exit; `process` would need Node's types.
  throw new Error(`${failures} print checks failed`)
}
console.log('\nall passed')
