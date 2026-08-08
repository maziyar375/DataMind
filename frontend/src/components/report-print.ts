/**
 * The print handoff.
 *
 * `@media print` in styles.css does the half of printing that is a stylesheet:
 * the app chrome goes, the light token set is forced over the user's theme, a
 * figure is kept whole across a page break, and the article is given a definite
 * page width. This file does the half that a stylesheet cannot reach, because
 * a Vega chart is not laid out by CSS:
 *
 * 1. **Width.** `VegaChart` sizes on `width: 'container'` through a
 *    `ResizeObserver`, and print does not resize the live DOM — no observer
 *    fires, so a chart would print at whatever width it last had on screen. On
 *    a wide monitor that is a chart clipped at the page margin; on a narrow
 *    window it is a chart occupying a third of the page. So every chart is
 *    redrawn at the width the printed page will actually give it.
 * 2. **Theme.** A chart's colours come from `data-theme`, not from CSS
 *    variables, so the token override that turns the *page* light leaves a
 *    dark-theme chart with near-white axis labels on white paper. The print
 *    redraw pins the light palette.
 * 3. **Fonts.** `document.fonts.ready` before `window.print()`, or the first
 *    print of a Persian report fires against fallback metrics — the exact
 *    failure self-hosting Vazirmatn exists to prevent.
 *
 * The registry is how this module reaches the charts without owning them.
 * `VegaChart` registers a redraw callback for its own element; this module
 * decides which of them are inside the document being printed, what width each
 * one gets, and puts them all back afterwards.
 *
 * What it deliberately does not do is change any state: the run on screen is
 * the saved run, and printing must not write to it.
 */

/** Where a chart is being drawn — the screen it lives on, or the page. */
export type DrawTarget =
  | { kind: 'screen' }
  /**
   * Print. `widthPx` is the printed content width available to that chart's
   * container; a chart with a width of its own (a wide categorical chart,
   * which is sized per column) keeps it and is scaled down by the stylesheet
   * instead — squeezing its columns into the page would smear the labels.
   */
  | { kind: 'print'; widthPx: number }

type PrintableChart = {
  el: HTMLElement
  draw: (target: DrawTarget) => Promise<void>
}

const charts = new Set<PrintableChart>()

/**
 * Offer a chart to the printer. Returns the unregister function, which the
 * caller must run on unmount — a stale entry would be a detached element the
 * printer tries to redraw.
 */
export function registerPrintableChart(chart: PrintableChart): () => void {
  charts.add(chart)
  return () => {
    charts.delete(chart)
  }
}

/* The page. A4 minus the horizontal margin `@page` declares, which is the
   assumption the whole print stylesheet already makes. US Letter is wider, so
   a report printed on it gets slightly more margin rather than a broken
   layout; anything narrower is caught by the `max-width: 100%` on the printed
   SVG, which scales a too-wide chart down as vector art. */
export const PRINT_PAGE_MARGIN_MM = 14
export const PRINT_CONTENT_WIDTH_MM = 210 - 2 * PRINT_PAGE_MARGIN_MM

/** CSS px are 1/96 inch in print, by definition — so this conversion is exact. */
const PX_PER_MM = 96 / 25.4
export const PRINT_CONTENT_WIDTH_PX = Math.round(PRINT_CONTENT_WIDTH_MM * PX_PER_MM)

/**
 * A floor, for the pathological case: a chart nested deeply enough that the
 * inset eats the page. Better a chart narrower than its box than one with a
 * negative width, which Vega renders as nothing at all.
 */
export const MIN_PRINT_CHART_WIDTH_PX = 240

/**
 * How wide a chart should be drawn on paper, given the widths it and its
 * article have on screen.
 *
 * Everything between the article's edge and the chart's box — the figure's
 * padding and border, the chart frame's — is a fixed number of pixels whatever
 * the article is wide. So the inset can be measured on screen, where the
 * layout exists, and subtracted from the page, where it does not yet.
 *
 * Pure, and separate from `printReport`, because it is the one piece of this
 * that is arithmetic rather than choreography — and because it has to keep
 * agreeing with a number in a stylesheet. `npm run test:print`.
 */
export function printChartWidth(articleWidthPx: number, chartWidthPx: number): number {
  const inset = articleWidthPx > 0 ? Math.max(0, articleWidthPx - chartWidthPx) : 0
  return Math.max(MIN_PRINT_CHART_WIDTH_PX, PRINT_CONTENT_WIDTH_PX - inset)
}

/**
 * Arabic and Persian, their supplements, and the presentation forms — written
 * as escapes because a range of RTL characters inside an LTR source file is
 * unreadable in every editor and reorders itself in a diff.
 */
const RTL_TEXT = /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]/

/**
 * Watch for the print dialog closing. **Call this before `window.print()`.**
 *
 * That order is the whole point, and getting it wrong is a bug this file
 * already had: `window.print()` *blocks* in Chrome and Firefox and `afterprint`
 * fires before it returns, so a listener attached afterwards is attached to an
 * event that has already happened. It then never resolves, the spinner turns
 * until the timeout, and the re-entrancy guard holds the button shut for a
 * minute — for a print that finished immediately. So this returns the promise
 * without awaiting it, and the caller starts watching first and prints second.
 *
 * Two signals, because neither is universal. `afterprint` is the direct one.
 * `matchMedia('print')` going false is the same fact observed from the other
 * side, and covers an engine that rasterises without firing the event. The
 * timeout is the last resort: whatever happens, the charts must not stay stuck
 * at page width on a screen.
 */
function watchForPrintEnd(): Promise<void> {
  return new Promise((resolve) => {
    const media = window.matchMedia('print')
    let timer = 0
    const done = () => {
      window.clearTimeout(timer)
      window.removeEventListener('afterprint', done)
      media.removeEventListener('change', onMedia)
      resolve()
    }
    // Only the *leaving* edge. The entering one fires as the dialog opens.
    const onMedia = (event: MediaQueryListEvent) => {
      if (!event.matches) done()
    }
    timer = window.setTimeout(done, 60_000)
    window.addEventListener('afterprint', done)
    media.addEventListener('change', onMedia)
  })
}

let printing = false

/**
 * Print the report document rooted at `root`.
 *
 * Redraws every chart inside it at page width in the light palette, waits for
 * the fonts, prints, and restores. Never rejects: a chart that fails to redraw
 * is one chart, and the rest of the document is still worth printing.
 */
export async function printReport(root: HTMLElement | null): Promise<void> {
  // Re-entrancy would restore the first call's charts while the second is
  // still showing them to the printer.
  if (printing) return
  printing = true

  // The inset between the article and a chart's box — the figure's padding and
  // border, the chart's own — is a fixed number of pixels whatever the article
  // is wide, so it can be measured on screen and subtracted from the page.
  const articleWidth = root?.clientWidth ?? 0
  const targets = [...charts].filter((c) => root !== null && root.contains(c.el))

  const plan = targets.map((chart) => ({
    chart,
    widthPx: printChartWidth(articleWidth, chart.el.clientWidth),
  }))

  try {
    await Promise.all(
      plan.map(({ chart, widthPx }) =>
        chart.draw({ kind: 'print', widthPx }).catch(() => undefined),
      ),
    )

    // A document with Persian in it needs the Arabic subset actually resident,
    // not merely declared: `unicode-range` makes the browser fetch it lazily,
    // and `fonts.ready` only promises that loading is *idle*. Asked for by the
    // text on the page, so an English report pays for nothing.
    if (root && RTL_TEXT.test(root.textContent ?? '')) {
      await document.fonts.load('600 16px Vazirmatn', 'گزارش').catch(() => undefined)
    }
    await document.fonts.ready

    // Watch first, print second — `window.print()` blocks and the event fires
    // inside it. See `watchForPrintEnd`.
    const ended = watchForPrintEnd()
    window.print()
    await ended
  } finally {
    await Promise.all(
      plan.map(({ chart }) => chart.draw({ kind: 'screen' }).catch(() => undefined)),
    )
    printing = false
  }
}
