/**
 * Renders a backend-produced Vega-Lite spec with vega-embed.
 *
 * The spec's data and encodings are chosen by the agent (the `chart` pipeline
 * node); this component only paints it. Colours come from a small hex palette
 * keyed on the active `data-theme` rather than the app's oklch CSS variables,
 * because Vega/D3 cannot parse `oklch()` and would fall back to black. A
 * MutationObserver re-renders the chart when the user toggles the theme.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import embed, { type VisualizationSpec } from 'vega-embed'

type ThemeName = 'dark' | 'light'

const PALETTES: Record<ThemeName, {
  text: string; dim: string; grid: string; category: string[]
}> = {
  dark: {
    text: '#e6e9ef',
    dim: '#9aa4b2',
    grid: 'rgba(255,255,255,0.09)',
    category: ['#5b9bf3', '#5fd0a6', '#e6b34d', '#e2724f', '#b48be6', '#4fc4e2', '#e06f9c', '#8bd45f'],
  },
  light: {
    text: '#1f2733',
    dim: '#5b6675',
    grid: 'rgba(0,0,0,0.09)',
    category: ['#2f6fdb', '#1f9e73', '#c9871f', '#c9512a', '#7d4fc4', '#1f93c9', '#c93f74', '#4f9e1f'],
  },
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

export function VegaChart({ spec }: { spec: Record<string, unknown> }) {
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
    const isHorizontalBar = mark === 'bar' && encoding.y?.type === 'nominal'
    const PER_BAR = 22
    const height = isHorizontalBar
      ? Math.min(60_000, Math.max(200, rowCount * PER_BAR))
      : mark === 'arc'
        ? 260
        : 300
    const xIsCategorical = encoding.x?.type === 'nominal' || encoding.x?.type === 'ordinal'
    return { encoding, isHorizontalBar, height, xIsCategorical }
  }, [spec])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const p = PALETTES[theme]
    const { encoding, height, xIsCategorical } = layout

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
      range: { category: p.category },
      bar: { cornerRadiusEnd: 3 },
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

    const full = {
      ...spec,
      encoding: encodingOverride,
      width: 'container',
      height,
      autosize: { type: 'fit-x', contains: 'padding' },
      background: 'transparent',
      config,
    }

    let cancelled = false
    let result: Awaited<ReturnType<typeof embed>> | null = null
    setFailed(false)
    embed(el, full as unknown as VisualizationSpec, { actions: false, renderer: 'svg' })
      .then((r) => {
        if (cancelled) r.finalize()
        else result = r
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })

    return () => {
      cancelled = true
      result?.finalize()
    }
  }, [spec, theme, layout])

  // A chart failure must never blank the answer or the table above it.
  if (failed) return null

  return (
    <div
      style={{
        width: '100%',
        marginTop: 6,
        padding: '8px 10px',
        border: '1px solid var(--border)',
        borderRadius: 10,
        background: 'var(--panel)',
        overflowX: 'auto',
        // A tall, many-bar horizontal chart scrolls inside a bounded box rather
        // than stretching the whole conversation down the page.
        overflowY: layout.isHorizontalBar ? 'auto' : 'visible',
        maxHeight: layout.isHorizontalBar ? 'min(70vh, 640px)' : undefined,
      }}
    >
      <div ref={ref} style={{ width: '100%', minHeight: 40 }} />
    </div>
  )
}
