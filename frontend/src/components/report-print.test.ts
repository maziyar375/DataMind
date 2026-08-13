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
  MAX_PRINT_CHART_HEIGHT_PX, MIN_PRINT_CHART_HEIGHT_PX, MIN_PRINT_CHART_WIDTH_PX,
  PRINT_CONTENT_WIDTH_MM, PRINT_CONTENT_WIDTH_PX, printChartHeight, printChartWidth,
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

// ── the height, which is the page's and not the screen's ─────────────────
// A screen plot is 300px tall whatever its width, because a screen scrolls for
// free. A page is a fixed rectangle, and at 300px a figure plus its caption
// and source line is a third of A4 — so `break-inside: avoid` gives each one a
// page with the bottom third blank. The page sets the height from the width
// instead, at the proportion a printed exhibit is drawn in.
check('a full-measure chart is drawn wider than tall', printChartHeight(688), 206)
check('a half-measure one keeps the same proportion', printChartHeight(500), 150)
check('and a narrow one stops at the floor', printChartHeight(240), MIN_PRINT_CHART_HEIGHT_PX)
check(
  'a chart on a wider page does not eat it',
  printChartHeight(1200),
  MAX_PRINT_CHART_HEIGHT_PX,
)
// The whole point of the exercise: a figure has to be short enough that more
// than one of them fits on a page. A4 less the 16mm top and bottom margins is
// 265mm of column, and the plot is the tall part of a figure.
check(
  'so two figures at full measure fit a page with room for their captions',
  2 * printChartHeight(PRINT_CONTENT_WIDTH_PX) < Math.round(265 * (96 / 25.4)),
  true,
)

// ── the number the stylesheet also holds ─────────────────────────────────
const css = readFileSync(fileURLToPath(new URL('../styles.css', import.meta.url)), 'utf8')
const declared = /\.rm-report\s*\{[^}]*max-width:\s*(\d+(?:\.\d+)?)mm/.exec(css)
check('styles.css gives .rm-report a page width in mm', declared !== null, true)
check(
  'and it is the width the charts are redrawn against',
  declared === null ? null : Number(declared[1]),
  PRINT_CONTENT_WIDTH_MM,
)

// ── the shell, which on paper must stop being a window ───────────────────
// The application is a box one viewport tall that clips its overflow, and it
// says so *inline* in App.tsx — so a print stylesheet that does not name every
// layer of it, with `!important`, prints page one and silently discards the
// rest of the document. That was the bug. These assert the release survives,
// and that the classes the selectors depend on are still on the elements.
const print = /@media print \{([\s\S]*)\n\}/.exec(css)
check('styles.css has a print block', print !== null, true)
const printCss = print?.[1] ?? ''

/** The selectors of every rule in the print block that both un-clips and
 *  un-sizes what it names. Declaration order is not asserted — only that one
 *  rule does both to a given selector, since either alone still loses pages. */
const released = new Set<string>()
const printRules = printCss.replace(/\/\*[\s\S]*?\*\//g, '')
for (const [, selectors, body] of printRules.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
  if (!/overflow:\s*visible\s*!important/.test(body)) continue
  if (!/height:\s*auto\s*!important/.test(body)) continue
  for (const one of selectors.split(',')) released.add(one.trim())
}
for (const selector of ['html', 'body', '#root', '.rm-app', '.rm-app-row',
  '.rm-app-view', '.rm-report-view', '.rm-report-scroll']) {
  check(
    `print releases ${selector} from its height and its clipping`,
    released.has(selector),
    true,
  )
}

const appTsx = readFileSync(fileURLToPath(new URL('../App.tsx', import.meta.url)), 'utf8')
check('the shell row still carries the class the rule names', /"rm-app-row"/.test(appTsx), true)
check('and so does the view box', /"rm-app-view"/.test(appTsx), true)
const reportTsx = readFileSync(fileURLToPath(new URL('./report.tsx', import.meta.url)), 'utf8')
check('and the run viewer', /"rm-report-view"/.test(reportTsx), true)

// ── the order the handoff hangs on ───────────────────────────────────────
// `window.print()` blocks in Chrome and Firefox and `afterprint` fires before
// it returns, so a watch armed *after* the call is armed for an event that has
// already happened: the promise then runs to its 60s timeout, the spinner
// turns, and the re-entrancy guard keeps the button shut the whole time. That
// was the bug. Source order is what fixes it, so source order is what is
// asserted — there is no DOM here to observe the event in.
const printTs = readFileSync(fileURLToPath(new URL('./report-print.ts', import.meta.url)), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/\/\/.*$/gm, '')
const armed = printTs.indexOf('= watchForPrintEnd()')
const printed = printTs.indexOf('window.print()')
check('the print-end watch is armed before window.print() blocks',
  armed > 0 && printed > 0 && armed < printed, true)

// ── the page margin, which is taken rather than cleared ──────────────────
// The browser prints its own date, title and URL into the margin boxes the
// document leaves undeclared. Declaring one takes it: the footer went away the
// moment `@bottom-center` was claimed, which is what these five empty boxes
// are for. An empty `content` here is load-bearing, so a tidying pass that
// deletes them as no-ops brings the header back.
for (const box of ['top-left', 'top-center', 'top-right',
  'bottom-left', 'bottom-right']) {
  check(
    `@${box} is claimed, so the browser cannot print into it`,
    new RegExp(`@${box}\\s*\\{\\s*content:\\s*""\\s*;?\\s*\\}`).test(printRules),
    true,
  )
}
check(
  'and @bottom-center carries the page number',
  /@bottom-center\s*\{[^}]*content:\s*counter\(page\)/.test(printRules),
  true,
)

// ── the light theme's wash, which is a screen affordance ─────────────────
// Light mode paints `.rm-app` with four radial gradients so the app reads as
// paper; on actual paper that is a yellow cast over every page. It is scoped
// to `:root[data-theme='light'] .rm-app`, which out-specifies a bare `.rm-app`
// even when both are `!important` — so the print block has to answer it with
// the same selector, not a broader one. Dark mode never showed the bug, which
// is what made it look like a theme problem rather than a specificity one.
check(
  'print flattens the light wash with a selector that can actually win',
  /:root\[data-theme='light'\]\s+\.rm-app[^{]*\{[^}]*background:\s*#ffffff\s*!important/
    .test(printRules),
  true,
)

// A flex container is the one layout browsers fragment worst, and these two
// are the ones that have to cross a page break.
check(
  'the article and its sections print as block flow',
  /\.rm-report,\s*\.rm-report-section\s*\{\s*display:\s*block\s*!important/.test(printCss),
  true,
)

// ── the page's own type scale ────────────────────────────────────────────
// The components set their sizes inline for a screen, in px. Print px are
// 1/96in by definition, so the screen's 14.5px body is a hard 10.9pt on paper —
// a large-print edition of a document whose peers set 9.5–10pt. The print
// block restates the scale in points, and it can only win over an inline style
// with `!important`, so both facts are asserted: a rule that loses its
// `!important` in a tidy-up silently reverts the whole document to screen size.
const printedBody = /\.rm-report-prose p,\s*\.rm-report-findings li\s*\{([^}]*)\}/.exec(printCss)
check('the printed body has a size of its own', printedBody !== null, true)
check(
  'set in points, the unit the page is measured in',
  /font-size:\s*\d+(\.\d+)?pt\s*!important/.test(printedBody?.[1] ?? ''),
  true,
)
// Ragged-right is right on screen, where the measure moves with the window.
// The page's measure is fixed and known, which is the condition justification
// needs — and the reason a printout otherwise reads as a pasted-in web page.
check(
  'and it is justified, which a fixed measure is what allows',
  /text-align:\s*justify/.test(printedBody?.[1] ?? ''),
  true,
)
check(
  'with hyphenation, which is what keeps justification from opening rivers',
  /hyphens:\s*auto/.test(printedBody?.[1] ?? ''),
  true,
)
// `hyphens: auto` breaks words using the language in force. Inherited from
// `<html lang="en">` a Persian report would be hyphenated against English
// patterns, so the article declares its own.
check(
  'the article declares the language the hyphenator reads',
  /lang=\{language\}/.test(reportTsx),
  true,
)

// ── the imprint on the cover ─────────────────────────────────────────────
// A printed report leaves the tool, and the app chrome that says where it came
// from is the first thing the print block removes — so the mark has to be
// inside the article. Sized in millimetres rather than px: the source is a
// 1024px raster, and asking for 8mm of it is what keeps it a print resolution
// instead of a screen one.
check('the cover carries the mark', /<Brandmark \/>/.test(reportTsx), true)
check(
  'and print gives the lockup a size of its own',
  /\.rm-report-brand[^{]*\{[^}]*\}/.test(printCss),
  true,
)
check(
  'in millimetres, so the raster prints at print resolution',
  /\.rm-report-brand img[\s\S]{0,120}?width:\s*\d+(\.\d+)?mm/.test(printCss),
  true,
)

// The classes the scale is hung on. Every one of them is on an element styled
// inline, so the selector is the only handle print has on it; renaming one in
// the component without renaming it here reverts that element to screen size
// on paper and nowhere else, which is invisible until a PDF is sent out.
for (const [cls, where] of [
  ['rm-report-title', reportTsx],
  ['rm-report-heading', reportTsx],
  ['rm-report-prose', reportTsx],
  ['rm-report-caption', reportTsx],
  // A callout is the one block that is deliberately *not* a figure, so it is
  // the one whose print size no `.rm-report-figure` rule would cover for it.
  ['rm-report-callout', reportTsx],
  ['rm-report-source', reportTsx],
  ['rm-report-sql', reportTsx],
  ['rm-kpi-value', readFileSync(fileURLToPath(new URL('./ui.tsx', import.meta.url)), 'utf8')],
  ['rm-table', readFileSync(fileURLToPath(new URL('./ui.tsx', import.meta.url)), 'utf8')],
] as const) {
  check(
    `print sizes .${cls}, and a component still carries it`,
    printCss.includes(`.${cls}`) && where.includes(cls),
    true,
  )
}

// ── the type ─────────────────────────────────────────────────────────────
// Every face this app prints in has to be served by us, not fetched. A report
// is the deliverable, and a firewalled install that cannot reach a CDN must
// still put the right glyphs on the page.
for (const file of [
  'vazirmatn-arabic', 'vazirmatn-latin', 'vazirmatn-latin-ext',
  'inter-latin', 'inter-latin-ext',
  'jetbrains-mono-latin', 'jetbrains-mono-latin-ext',
] as const) {
  check(
    `${file} is self-hosted rather than requested from a CDN`,
    new RegExp(`@font-face[^}]*${file}\\.woff2`).test(css),
    true,
  )
}
const html = readFileSync(fileURLToPath(new URL('../../index.html', import.meta.url)), 'utf8')
check(
  'and index.html asks Google Fonts for nothing at all',
  // Comments stripped first: the note in that file explains why the CDN is
  // gone, and naming a host is not requesting from it.
  /fonts\.(googleapis|gstatic)\.com/.test(html.replace(/<!--[\s\S]*?-->/g, '')),
  false,
)

// One line box for both scripts, or a row changes height with its language and
// Persian descenders clip. Every face has to land on the same effective
// ascent/descent — and Vazirmatn gets there via different numbers, because
// `size-adjust` scales the overrides too (CSS Fonts 5 §4.4). That is the drift
// hazard: raise size-adjust, forget to re-divide, and the box silently splits
// in two. These read the declared values back and multiply them out.
const faces = [...css.matchAll(/@font-face\s*\{([^}]*)\}/g)].map((m) => m[1])
check('every declared face is accounted for', faces.length, 7)
const box = (face: string): [number, number] => {
  const num = (prop: string, fallback: number): number => {
    const m = face.match(new RegExp(`${prop}:\\s*([\\d.]+)%`))
    return m ? Number(m[1]) : fallback
  }
  const scale = num('size-adjust', 100) / 100
  // Rounded because the declared percentages are themselves rounded to 2dp.
  return [
    Math.round(num('ascent-override', -1) * scale * 100) / 100,
    Math.round(num('descent-override', -1) * scale * 100) / 100,
  ]
}
check(
  'and all seven resolve to the one shared box',
  [...new Set(faces.map((f) => JSON.stringify(box(f))))],
  [JSON.stringify([100, 38])],
)
// The box has to clear the deepest glyph in either script. Persian ج reaches
// −0.3374em and is the binding constraint at Vazirmatn's 105%; Inter's Å is
// the tallest thing above the baseline at +0.9971em. Both measured from the
// woff2 files themselves.
check('the box clears Persian ج below the baseline', 0.3374 * 1.05 < 0.38, true)
check('and Inter Å above it', 0.9971 < 1.0, true)

// Persian digits ship proportional and only become tabular under `tnum`, so
// the class that numeric cells and KPI values wear has to ask for it.
check(
  '.mono asks for tabular figures and can render Persian ones',
  /code, pre, \.mono \{[^}]*Vazirmatn[^}]*font-variant-numeric:\s*tabular-nums/s.test(css),
  true,
)

if (failures > 0) {
  // A thrown error is the non-zero exit; `process` would need Node's types.
  throw new Error(`${failures} print checks failed`)
}
console.log('\nall passed')
