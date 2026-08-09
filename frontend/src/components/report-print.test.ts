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

// The classes the scale is hung on. Every one of them is on an element styled
// inline, so the selector is the only handle print has on it; renaming one in
// the component without renaming it here reverts that element to screen size
// on paper and nowhere else, which is invisible until a PDF is sent out.
for (const [cls, where] of [
  ['rm-report-title', reportTsx],
  ['rm-report-heading', reportTsx],
  ['rm-report-prose', reportTsx],
  ['rm-report-caption', reportTsx],
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
