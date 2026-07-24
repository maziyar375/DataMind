/**
 * Renders a backend-produced Vega-Lite spec with vega-embed.
 *
 * The spec's data and encodings are chosen by the agent (the `chart` pipeline
 * node); this component only paints it. Colours come from a small hex palette
 * keyed on the active `data-theme` rather than the app's oklch CSS variables,
 * because Vega/D3 cannot parse `oklch()` and would fall back to black. A
 * MutationObserver re-renders the chart when the user toggles the theme.
 */
import { useEffect, useRef, useState } from 'react'
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

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const p = PALETTES[theme]

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
      },
      legend: { labelColor: p.dim, titleColor: p.text, labelFontSize: 11, titleFontSize: 11 },
      range: { category: p.category },
      mark: { color: p.category[0] },
      arc: { innerRadius: 0 },
    }

    const full = {
      ...spec,
      width: 'container',
      autosize: { type: 'fit', contains: 'padding' },
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
  }, [spec, theme])

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
      }}
    >
      <div ref={ref} style={{ width: '100%', minHeight: 40 }} />
    </div>
  )
}
