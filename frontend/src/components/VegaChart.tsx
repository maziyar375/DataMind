/**
 * Renders a backend-produced Vega-Lite spec with vega-embed.
 *
 * The spec's data and encodings are chosen by the agent (the `chart` pipeline
 * node); this component only paints it. A MutationObserver re-renders the
 * chart when the theme is toggled.
 *
 * The colours themselves, and the reasoning behind every one of them, live in
 * `palette.ts` — apart from here so that `npm run test:palette` can measure
 * them without loading React. Read that file before changing a value.
 *
 * Every instance also offers itself to `report-print.ts`, which redraws the
 * ones inside a printed report at page width in the light palette. Neither is
 * expressible in `@media print`: a Vega plot is sized in the spec, not by CSS,
 * and its colours come from `data-theme` rather than from the tokens.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import embed, { type VisualizationSpec } from 'vega-embed'
import { CATEGORY_INK, PALETTES, type ThemeName } from './palette.ts'
import { registerPrintableChart, type DrawTarget } from './report-print.ts'

/**
 * Widen a printed chart's `viewBox` to whatever it actually drew.
 *
 * Vega gives the SVG a `viewBox` equal to the width and height it computed,
 * which is what lets the print stylesheet's `max-width: 100%` scale an
 * over-wide chart down rather than crop it. The catch is that a chart can draw
 * outside that box: a legend's width is computed from its labels' *measured*
 * text, and the glyphs are then rendered to wherever they end — a couple of
 * pixels past the box, reliably enough that a printed pie chart's longest
 * legend label loses its last letter. An SVG clips at its viewBox, so those
 * pixels are simply gone, and no amount of CSS around it brings them back.
 *
 * So after a print draw the box is re-measured against the drawing and
 * enlarged where the drawing won. Only the origin is fixed: extending right
 * and down keeps the chart's own padding and its position in the frame.
 *
 * Print only. On screen the frame scrolls, so an overflowing legend is reachable
 * rather than lost, and this would be a per-redraw reflow for nothing.
 */
function makeScalable(el: HTMLElement): void {
  const svg = el.querySelector('svg')
  if (!svg) return
  const w = Number(svg.getAttribute('width'))
  const h = Number(svg.getAttribute('height'))
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return

  let right = w
  let bottom = h
  try {
    const box = svg.getBBox()
    right = Math.max(right, Math.ceil(box.x + box.width))
    bottom = Math.max(bottom, Math.ceil(box.y + box.height))
  } catch {
    /* getBBox throws on an unrendered SVG; the declared size is the fallback */
  }

  // And a hair beyond even that. This is measured as the chart is drawn, while
  // the cut happens later on the page, and the two are not quite the same
  // layout — a fraction of a pixel of glyph advance is all it takes. Two
  // pixels in seven hundred is a 0.3% reduction nobody can see, against a
  // legend label missing its last letter, which everybody does.
  const BLEED = 2

  svg.setAttribute('viewBox', `0 0 ${right + BLEED} ${bottom + BLEED}`)
  svg.setAttribute('preserveAspectRatio', 'xMinYMin meet')
}

function currentTheme(): ThemeName {
  return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'
}

function useThemeName(): ThemeName {
  const [name, setName] = useState<ThemeName>(currentTheme)
  useEffect(() => {
    const root = document.documentElement
    const observer = new MutationObserver(() => setName(currentTheme()))
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])
  return name
}

export function VegaChart({ spec, frameless = false, fill = false }: {
  spec: Record<string, unknown>
  /**
   * Drop the border, padding and panel background. A dashboard tile already
   * is a bordered panel, and nesting a second one inside it reads as a chart
   * that failed to fill its box.
   */
  frameless?: boolean
  /**
   * Fill the parent's height too, not only its width. In a dashboard tile the
   * box is the tile the user sized, and a fixed-height plot floating in a
   * taller tile reads as wasted space. The parent must give this component a
   * definite height (flex: 1 / height: 100%). The charts that grow downward
   * are exempt — a horizontal bar's height must follow its category count and
   * a heatmap's its second dimension, so labels and cells stay legible; those
   * scroll instead.
   */
  fill?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState(false)
  // Vega draws asynchronously, so the box exists a beat before the plot does.
  // The first drawing therefore lands in one frame, at full height, under
  // whatever is already on screen — the answer and the table shift down as it
  // arrives. That is the "sudden" part, and a fade alone does not cover it: an
  // opacity ramp over a box that grew 40px → 340px instantly is a chart
  // materialising in a hole that opened for it.
  //
  // So the box grows into the drawing instead (`rm-chart-frame`, below): while
  // it is empty it is collapsed and colourless, and the drawing's arrival
  // unfolds it to the height the plot turned out to need while the plot fades
  // in. One motion, in the same 6px/240ms register as `rm-artifact-in` in
  // `styles.css` — a chart is a bigger surface than a chip, so it is given a
  // little longer, and no more.
  //
  // Once it is up it stays up: a redraw (theme, picker, print) replaces the SVG
  // in place rather than blinking the frame.
  const [drawn, setDrawn] = useState(false)
  // The entrance is a first-drawing affordance and nothing else. Once it has
  // played, every style it introduces is dropped and the box is exactly the box
  // it was before this existed — which matters for the one case the collapsing
  // wrapper has to clip while it plays: a categorical chart drawn wider than
  // its container, whose sideways scroll is the whole point of that width.
  const [settled, setSettled] = useState(false)
  const theme = useThemeName()

  useEffect(() => {
    if (!drawn || settled) return
    // Comfortably past the longest transition below. A timer rather than
    // `transitionend`, which never fires where `grid-template-rows` is not
    // animatable (Safari < 16) — and that would leave a wide chart clipped for
    // good, trading a 300ms entrance for a permanent bug.
    const timer = window.setTimeout(() => setSettled(true), 460)
    return () => window.clearTimeout(timer)
  }, [drawn, settled])

  // Layout depends only on the spec's shape, so compute it once and share it
  // between the render (container sizing) and the embed effect.
  const layout = useMemo(() => {
    const encoding = (spec.encoding ?? {}) as Record<string, { type?: string }>
    const mark = typeof spec.mark === 'object' ? (spec.mark as { type?: string }).type : spec.mark
    const rowCount = Array.isArray((spec.data as { values?: unknown[] })?.values)
      ? (spec.data as { values: unknown[] }).values.length
      : 0

    // A horizontal bar's height must grow with its category count — ~22px per
    // bar — so every label stays legible; the container then scrolls when the
    // result is large. Capping the height (as an earlier version did) crushes
    // hundreds of bars into a few pixels. Every other chart gets a fixed plot
    // height so the marks, not the labels, own the box.
    //
    // Which chart this *is* comes from `usermeta`, where the backend states its
    // decision, rather than from re-deriving it out of the encoding. The old
    // test — bar mark plus a nominal y — is a guess that a stacked bar or a
    // future `rect` mark can satisfy by accident. It survives only as the
    // fallback for specs compiled before `usermeta` existed: chat artifacts
    // persist their spec, so those are still on screen, and the guess is the
    // same one that was in force when they were written.
    const meta = (spec.usermeta as
      { datamind?: {
        chart_type?: string; orientation?: string; stack?: string
        categories?: number; bands?: number; signed_measure?: string
      } } | undefined)?.datamind
    const isHorizontalBar = meta
      ? meta.chart_type === 'bar' && meta.orientation === 'horizontal'
      : mark === 'bar' && encoding.y?.type === 'nominal'
    // A heatmap has the same problem for the same reason — rows of cells
    // stacked down the page — but its own row count, and it needs a taller
    // band than a bar because a cell is read as an area, not a length.
    const isHeatmap = meta?.chart_type === 'heatmap'
    // A pie. Read off `usermeta` rather than the mark, because a pie is a
    // *layer* — the arcs, then the numbers written across them — and so has no
    // top-level mark to test. The mark is still the fallback for the specs
    // compiled before the labels existed, which are single-mark arcs and are
    // still on screen wherever a chat artifact persisted one.
    const isArc = meta ? meta.chart_type === 'pie' : mark === 'arc'
    const PER_BAND = isHeatmap ? 26 : 22
    // Marks drawn along the vertical category axis. For a heatmap that is the
    // second dimension; for bars, a stacked split puts its parts on one bar so
    // the count is the categories, while a grouped one gives each part its own
    // bar and it is back to the row count.
    const bandCount = isHeatmap
      ? meta?.bands ?? 0
      : meta?.stack === 'grouped' ? rowCount : meta?.categories ?? rowCount
    // Past a dozen bands the fixed plot height starts crushing them; below it,
    // filling the tile looks better than a short chart floating in a tall box.
    const growsDown = isHorizontalBar || (isHeatmap && bandCount > 12)
    const height: number | 'container' = growsDown
      ? Math.min(60_000, Math.max(200, bandCount * PER_BAND))
      : fill
        ? 'container'
        : isArc
          ? 260
          : 300
    const xIsCategorical = encoding.x?.type === 'nominal' || encoding.x?.type === 'ordinal'
    // The same rule on the other axis: a vertical bar chart squeezed to the
    // container turns its categories into a smear once there are more of them
    // than the width has pixels for. Past a dozen, give each mark a minimum
    // width and let the box scroll sideways instead. The backend caps category
    // charts long before this matters — this is the floor that holds if that
    // cap ever changes, not the primary defence.
    //
    // What is counted is *columns*, not rows, and once a chart is split by a
    // series those stop being the same number: eight regions over twelve
    // months is 96 rows and twelve columns. The backend states the column
    // count in `usermeta`; the row count is only the fallback for specs
    // compiled before it did, where no split was expressible anyway.
    const columnCount = meta?.categories ?? rowCount
    const PER_COLUMN = 34
    const width: number | 'container' =
      xIsCategorical && columnCount > 12 ? columnCount * PER_COLUMN : 'container'
    return {
      encoding, growsDown, height, width, xIsCategorical,
      // A pie's radius is derived from the plot box, which is why Vega's `fit`
      // autosize does not apply to it — the print path has to size it the
      // other way round. See the autosize note in `buildSpec`.
      isArc,
      // The column its slices are coloured by, which is what the labels drawn
      // over them need: the ink is chosen per slice from the fill under it.
      categoryField:
        isArc && typeof spec.encoding === 'object'
          ? (spec.encoding as { color?: { field?: string } }).color?.field
          : undefined,
      signedMeasure: meta?.signed_measure,
    }
  }, [spec, fill])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const { encoding, height, width, xIsCategorical, signedMeasure, categoryField } = layout

    /**
     * The spec as it goes to Vega, for a given palette and a given width.
     *
     * Both are parameters rather than closure constants because printing needs
     * this same chart drawn differently: at the width of the printed page
     * instead of the container's, and in the light palette whatever theme the
     * reader is running. See `report-print.ts` for why neither can be done in
     * a stylesheet.
     */
    const buildSpec = (
      themeName: ThemeName,
      page: { widthPx: number; heightPx: number } | null,
    ) => {
      const p = PALETTES[themeName]

      const config = {
        background: 'transparent',
        view: { stroke: 'transparent' },
        font: 'inherit',
        title: { color: p.text, fontSize: 13, fontWeight: 600, anchor: 'start' as const },
        axis: {
          labelColor: p.dim,
          titleColor: p.dim,
          gridColor: p.grid,
          domainColor: p.grid,
          tickColor: p.grid,
          labelFontSize: 11,
          titleFontSize: 11,
          labelLimit: 140,
          labelPadding: 4,
        },
        legend: { labelColor: p.dim, titleColor: p.text, labelFontSize: 11, titleFontSize: 11 },
        // Vega picks the scale *family* from the encoding type, and each family
        // has its own default range: `category` for discrete colour, `ramp` for
        // continuous, `ordinal` for ordered, `heatmap` for a rect mark's
        // quantitative colour, and `diverging` once a scale has a `domainMid`.
        // Every family left unset falls back to a Vega built-in, which is how
        // one product ends up with two palettes — `ramp` and `ordinal` were once
        // on `blues` beside a plum chart, and `heatmap` was still on
        // yellow-green-blue until this line named it. Setting all five is the
        // only version of this that stays fixed.
        range: {
          category: p.category,
          ramp: p.ramp,
          ordinal: p.ramp,
          heatmap: p.ramp,
          diverging: p.diverging,
        },
        // Rounded only at the data end, anchored to the baseline, so the mark
        // still reads as a measurement rather than a lozenge.
        bar: { cornerRadiusEnd: 4 },
        scale: { bandPaddingInner: 0.25 },
        mark: { color: p.category[0] },
        arc: { innerRadius: 0 },
        // The pie's slice numbers — the only text mark this product draws.
        // Bolder and a shade smaller than an axis label, because it is read
        // against a colour rather than against the page.
        text: { fontSize: 10.5, fontWeight: 600 },
        point: { size: 60, filled: true },
        line: { strokeWidth: 2 },
      }

      // Angle long category labels instead of standing them fully vertical, so
      // they stop eating half the chart. Set on the x-encoding directly (config
      // .axisX does not reliably carry labelAngle) and only when x is the
      // categorical axis — never for numeric axes (scatter, a horizontal bar's
      // measure axis).
      let encodingOverride = spec.encoding
      if (xIsCategorical && encoding.x && typeof encoding.x === 'object') {
        encodingOverride = {
          ...encoding,
          x: { ...encoding.x, axis: { labelAngle: -35, labelLimit: 110, labelPadding: 4 } },
        }
      }

      // A measure that crosses zero: paint the bars below the line in the
      // polarity colour. The compiler names the field rather than the colour,
      // because the pair is a theme value and this same spec is repainted when
      // the reader flips the theme — see `palette.ts`.
      //
      // The sign is already visible in which way the bar points, so this is
      // reinforcement, not the encoding; it is applied only where colour is
      // otherwise unused (no series), so nothing that carried identity is
      // overwritten to carry sign instead.
      if (signedMeasure) {
        encodingOverride = {
          ...(encodingOverride as Record<string, unknown>),
          color: {
            condition: {
              test: `datum[${JSON.stringify(signedMeasure)}] < 0`,
              value: p.polarity.negative,
            },
            value: p.category[0],
          },
        }
      }

      // The pie's slice numbers. The compiler places them and leaves them
      // unpainted, because ink on a slice is a question about the palette and
      // the palette is this file's — the same division `signed_measure` is on.
      //
      // Per slice, not per chart: the categorical slots deliberately vary in
      // lightness (that is the CVD mechanism, see `palette.ts`), so no single
      // ink clears 4.5:1 against all eight. `contrast()` and `scale()` are
      // Vega expression functions, so the decision is made from the *resolved*
      // fill at draw time — which also means it survives a theme flip without
      // anything here knowing which slice got which hue.
      let layerOverride = spec.layer
      if (Array.isArray(layerOverride) && categoryField) {
        const fillOf = `scale('color', datum[${JSON.stringify(categoryField)}])`
        const ink = {
          expr:
            `contrast(${fillOf}, '${CATEGORY_INK.light}') > ` +
            `contrast(${fillOf}, '${CATEGORY_INK.dark}') ` +
            `? '${CATEGORY_INK.light}' : '${CATEGORY_INK.dark}'`,
        }
        layerOverride = (layerOverride as { mark?: { type?: string } }[]).map((child) =>
          child.mark?.type === 'text' ? { ...child, mark: { ...child.mark, fill: ink } } : child,
        )
      }

      // On paper there is no container to fit to and no observer to re-measure,
      // so both axes need a definite length. The width is the page's, and so is
      // the height: a screen plot is 300px tall whatever its width, which on a
      // fixed rectangle turns every figure into a third of a page and leaves the
      // rest of each one blank (`printChartHeight`).
      //
      // The charts that grow downward with their rows keep their own height —
      // a hundred horizontal bars flattened into the height a page can spare
      // is a smear of labels, and the stylesheet already scales an over-tall
      // one down as vector art rather than clipping it.
      const drawWidth = page !== null && width === 'container' ? page.widthPx : width
      const drawHeight =
        page === null || layout.growsDown ? height : page.heightPx

      // `pad` means the numbers above size the *plot* and the axes and legend
      // are added outside it, which is right on screen — the box scrolls, and
      // a chart that overflows it by twenty pixels is a chart the reader can
      // still reach. A page has nowhere to scroll to: the same overflow is a
      // legend printed with its last letter cut off at the figure's edge. So
      // on paper the figure's box is the constraint and the plot gives way to
      // it, which is what `fit` means.
      //
      // Two exceptions. A chart that grows downward with its rows has a height
      // that *is* the count of them, and fitting that to a box is the crushing
      // this component exists to prevent. And Vega cannot fit an arc: a pie's
      // radius is computed *from* the plot box, so the box cannot be computed
      // back from the drawing — asked to fit, it leaves its legend outside the
      // viewport and the last letter of a label is cut off. Those two stay on
      // `pad` and are scaled down instead, as vector art, by the print
      // stylesheet's `max-width` on the embed and its SVG.
      const fitToPage = page !== null && !layout.growsDown && !layout.isArc
      return {
        ...spec,
        // Only when there is one: `layer: undefined` still reads as a layered
        // spec to Vega-Lite — it tests for the key, not for a value — and
        // every single-mark chart would compile to nothing.
        ...(layerOverride ? { layer: layerOverride } : {}),
        encoding: encodingOverride,
        width: drawWidth,
        height: drawHeight,
        autosize: {
          type:
            fitToPage ? 'fit'
            : drawWidth === 'container' && drawHeight === 'container' ? 'fit'
            : drawWidth === 'container' ? 'fit-x'
            : drawHeight === 'container' ? 'fit-y'
            : 'pad',
          contains: 'padding',
        },
        background: 'transparent',
        config,
      }
    }

    let cancelled = false
    let result: Awaited<ReturnType<typeof embed>> | null = null
    let observer: ResizeObserver | null = null
    let resizeTimer: number | null = null

    // A 'container' size only re-measures on window:resize — that is the
    // sole trigger Vega-Lite compiles into the width/height signals. A
    // dashboard tile resizes its element without one (react-grid-layout,
    // the settings drawer opening), leaving the chart at its stale size
    // until the next refresh re-embeds it. So watch the element and hand
    // the new size to the view ourselves, debounced so a resize drag
    // re-lays-out once at rest, not sixty times a second.
    const watch = (r: Awaited<ReturnType<typeof embed>>) => {
      if (width !== 'container' && height !== 'container') return
      // Sub-2px changes are ignored: fit autosize can overshoot the box by
      // a pixel, and reacting to our own overshoot would loop forever.
      let lastW = -1
      let lastH = -1
      observer = new ResizeObserver(() => {
        if (resizeTimer !== null) window.clearTimeout(resizeTimer)
        resizeTimer = window.setTimeout(() => {
          const w = el.clientWidth
          const h = el.clientHeight
          if (Math.abs(w - lastW) < 2 && Math.abs(h - lastH) < 2) return
          lastW = w
          lastH = h
          let view = r.view
          if (width === 'container' && w > 0) view = view.width(w)
          if (height === 'container' && h > 0) view = view.height(h)
          void view.resize().runAsync()
        }, 60)
      })
      observer.observe(el)
    }

    /**
     * Draw, or redraw, into the element. Awaited by the printer, which needs
     * the page-width chart to exist before `window.print()` is called — hence
     * a real re-embed rather than a `view.width()` nudge: the palette changes
     * too, and that is compiled into the spec.
     */
    const draw = async (target: DrawTarget) => {
      if (cancelled) return
      // The observer measures the *container*, which does not change when the
      // page does, so leaving it attached would only let a stray resize undo
      // the print width.
      observer?.disconnect()
      observer = null
      if (resizeTimer !== null) {
        window.clearTimeout(resizeTimer)
        resizeTimer = null
      }

      const full = buildSpec(
        target.kind === 'print' ? 'light' : theme,
        target.kind === 'print'
          ? { widthPx: target.widthPx, heightPx: target.heightPx }
          : null,
      )
      const previous = result
      result = null
      previous?.finalize()

      setFailed(false)
      try {
        const r = await embed(el, full as unknown as VisualizationSpec, {
          actions: false,
          renderer: 'svg',
        })
        if (cancelled) {
          r.finalize()
          return
        }
        result = r
        setDrawn(true)
        if (target.kind === 'screen') watch(r)
        else makeScalable(el)
      } catch {
        if (!cancelled) setFailed(true)
      }
    }

    void draw({ kind: 'screen' })
    // Offered to the printer whatever page it is on: `printReport` redraws
    // only the charts inside the document it was handed.
    const unregister = registerPrintableChart({ el, draw })

    return () => {
      cancelled = true
      unregister()
      observer?.disconnect()
      if (resizeTimer !== null) window.clearTimeout(resizeTimer)
      result?.finalize()
    }
  }, [spec, theme, layout])

  // A chart failure must never blank the answer or the table above it.
  if (failed) return null

  // `rm-chart-frame` is named so the print stylesheet can reach this box.
  // Inside a report the chart already sits in a bordered figure, and a second
  // rule around the plot reads as a box in a box on paper — so print keeps the
  // box, whose padding and border the printer measured to work out the chart's
  // page width, and takes only its line.

  // Fill mode is exempt from the unfold: the box there is the dashboard tile
  // the user sized, so nothing jumps when the plot lands, and the height *is*
  // the measurement Vega takes off the container — collapsing it would have it
  // draw a chart zero pixels tall.
  const entering = !fill && !settled
  // Before the first drawing the frame is present but not visible. An empty
  // bordered strip announcing a chart that has not arrived is the same pop one
  // box further out.
  const veiled = entering && !drawn

  return (
    <div
      className={frameless ? undefined : 'rm-chart-frame'}
      style={{
        width: '100%',
        // In fill mode the box's height comes from the parent, and the inner
        // div must inherit it as a definite length: Vega measures the
        // container to size the plot, and measuring an auto-height element
        // would read back the plot's own height — a feedback loop.
        height: fill ? '100%' : undefined,
        marginTop: frameless ? 0 : 6,
        padding: frameless ? 0 : '8px 10px',
        border: frameless
          ? 'none'
          : `1px solid ${veiled ? 'transparent' : 'var(--border)'}`,
        borderRadius: frameless ? 0 : 10,
        background: frameless || veiled ? 'transparent' : 'var(--panel)',
        // The frame's own half of the entrance: its line and panel arrive with
        // the drawing rather than ahead of it. Dropped once settled, so a theme
        // flip — which changes both of these tokens — repaints instantly, the
        // way every other panel in the product does.
        transition: entering
          ? 'border-color .26s ease, background-color .26s ease'
          : undefined,
        // Fill mode clips instead of scrolling: the chart is fitted to this
        // box, and a scrollbar triggered by a pixel of overshoot would change
        // the box's size and start a fit/unfit oscillation. Two exemptions
        // scroll on purpose, and are stable because their content size is
        // fixed rather than fitted: the charts that grow downward with their
        // rows (horizontal bars, tall heatmaps) and wide categorical charts
        // (per-column width).
        overflowX:
          fill && !layout.growsDown && layout.width === 'container' ? 'hidden' : 'auto',
        // A tall, many-bar horizontal chart scrolls inside a bounded box rather
        // than stretching the whole conversation down the page.
        overflowY: layout.growsDown ? 'auto' : fill ? 'hidden' : 'visible',
        maxHeight: layout.growsDown ? 'min(70vh, 640px)' : undefined,
      }}
    >
      {/* The unfold. A grid row animated `0fr → 1fr` is the one way to grow a
          box to a height nobody knows in advance — and nobody here does: it is
          whatever Vega worked out from the spec, the axes and the legend.

          It sits *inside* the frame rather than on it because the frame is the
          scroll container: a `max-height` on a grid container makes an `fr` row
          resolve against that instead of against its content, which would
          flatten a hundred-bar horizontal chart into 640px rather than letting
          it scroll. Once settled the wrapper leaves the layout entirely
          (`display: contents`), so the box model is the one the rest of this
          file's sizing was written against. */}
      <div
        style={
          entering
            ? {
                display: 'grid',
                gridTemplateRows: drawn ? '1fr' : '0fr',
                transition: 'grid-template-rows .32s cubic-bezier(0.22, 0.68, 0.32, 1)',
              }
            : { display: 'contents' }
        }
      >
        <div
          ref={ref}
          style={{
            width: '100%',
            // A grid item has to be allowed to be shorter than its content, and
            // to hide the part of it the row has not revealed yet.
            minHeight: entering ? 0 : 40,
            overflow: entering ? 'hidden' : undefined,
            height: fill && !layout.growsDown ? '100%' : undefined,
            opacity: drawn ? 1 : 0,
            transition: 'opacity .3s ease',
          }}
        />
      </div>
    </div>
  )
}
