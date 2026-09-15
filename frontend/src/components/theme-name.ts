/**
 * Which theme is in force, as a value a chart can paint from — and a re-render
 * when the reader flips it.
 *
 * Charts cannot read the oklch CSS variables (Vega and D3 would fall back to
 * black), so they pick a `palette.ts` palette by name instead, and that name
 * comes from `data-theme` on the root element. Its own module so a chart that
 * does not use Vega can follow the theme without importing it.
 */
import { useEffect, useState } from 'react'
import type { ThemeName } from './palette.ts'

export function currentTheme(): ThemeName {
  return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'
}

export function useThemeName(): ThemeName {
  const [name, setName] = useState<ThemeName>(currentTheme)
  useEffect(() => {
    const root = document.documentElement
    const observer = new MutationObserver(() => setName(currentTheme()))
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])
  return name
}
