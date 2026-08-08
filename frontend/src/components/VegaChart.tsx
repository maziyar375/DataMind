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
import { PALETTES, type ThemeName } from './palette.ts'
import { registerPrintableChart, type DrawTarget } from './report-print.ts'

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
  const theme = useThemeName()

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
        : mark === 'arc'
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
    return { encoding, growsDown, height, width, xIsCategorical, signedMeasure: meta?.signed_measure }
  }, [spec, fill])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const { encoding, height, width, xIsCategorical, signedMeasure } = layout

    /**
     * The spec as it goes to Vega, for a given palette and a given width.
     *
     * Both are parameters rather than closure constants because printing needs
     * this same chart drawn differently: at the width of the printed page
     * instead of the container's, and in the light palette whatever theme the
     * reader is running. See `report-print.ts` for why neither can be done in
     * a stylesheet.
     */
    const buildSpec = (themeName: ThemeName, widthOverride: number | null) => {
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

      // On paper there is no container to fit to and no observer to re-measure,
      // so both axes need a definite length. The width is the page's; the height
      // is whatever the chart already occupies on screen, which is the size the
      // reader chose to look at it in.
      const printing = widthOverride !== null
      const drawWidth = printing && width === 'container' ? widthOverride : width
      const drawHeight =
        printing && height === 'container' ? Math.max(200, el.clientHeight || 300) : height

      return {
        ...spec,
        encoding: encodingOverride,
        width: drawWidth,
        height: drawHeight,
        autosize: {
          type:
            drawWidth === 'container' && drawHeight === 'container' ? 'fit'
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
        target.kind === 'print' ? target.widthPx : null,
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
        if (target.kind === 'screen') watch(r)
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

  return (
    <div
      style={{
        width: '100%',
        // In fill mode the box's height comes from the parent, and the inner
        // div must inherit it as a definite length: Vega measures the
        // container to size the plot, and measuring an auto-height element
        // would read back the plot's own height — a feedback loop.
        height: fill ? '100%' : undefined,
        marginTop: frameless ? 0 : 6,
        padding: frameless ? 0 : '8px 10px',
        border: frameless ? 'none' : '1px solid var(--border)',
        borderRadius: frameless ? 0 : 10,
        background: frameless ? 'transparent' : 'var(--panel)',
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
      <div
        ref={ref}
        style={{
          width: '100%',
          minHeight: 40,
          height: fill && !layout.growsDown ? '100%' : undefined,
        }}
      />
    </div>
  )
}
