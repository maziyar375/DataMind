/**
 * UI primitives.
 *
 * Every dimension, radius, and font size here is lifted from the design
 * concept rather than invented. The mock is the specification.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react'

import {
  formatCell, resolveColumns, sortRows,
  type ResultTableConfig,
} from './table-format'

// ── logo ──────────────────────────────────────────────────────────────────
//
// A head in profile, split into four interlocking puzzle pieces — the mind as
// something assembled from parts. Rebuilt as vector art rather than the source
// raster so it stays crisp at 26px and needs no dark glow behind it; the four
// piece colours are lifted from that image (violet, amber, blue, red).
//
// The four pieces tile a rectangle and are clipped to the head silhouette, so
// the profile is authored once (as the clip and the outline) while the interior
// seams — each with one puzzle knob — do the rest.
const LOGO_HEAD =
  'M32,20 C34,13 46,10 55,14 C64,18 68,26 68,34 L70,42 L78,50 L69,55 ' +
  'L71,61 C71,66 66,67 64,68 L63,76 L66,86 L34,86 L33,58 C27,54 27,30 32,20 Z'

// Palette taken from the source artwork: violet, magenta, blue, rose — no
// amber (that hue only appears as a background glow orb, not in the head).
const LOGO_PIECES: { d: string; fill: string }[] = [
  { d: 'M6,2 L48,2 L48,30 C59,30 59,42 48,42 L48,52 L6,52 Z', fill: '#7C3AED' },
  {
    d: 'M48,2 L94,2 L94,52 L74,52 C74,63 60,63 60,52 L48,52 L48,42 C59,42 59,30 48,30 L48,2 Z',
    fill: '#EC2E8A',
  },
  {
    d: 'M48,52 L60,52 C60,63 74,63 74,52 L94,52 L94,98 L48,98 L48,52 Z',
    fill: '#F43F7A',
  },
  { d: 'M6,52 L48,52 L48,98 L6,98 L6,52 Z', fill: '#2563EB' },
]

const LOGO_SEAM_V = 'M48,2 L48,30 C59,30 59,42 48,42 L48,98'
const LOGO_SEAM_H = 'M6,52 L60,52 C60,63 74,63 74,52 L94,52'

// The neon look from the source artwork: gradient-filled puzzle pieces, a
// white glow rim over a deep indigo-navy line, soft colored orbs behind the
// head, all on a dark app-icon tile.
const LOGO_INK = '#161038'

// The logo prefers the real brand image at /brand.png (drop your exact file
// there and it is used verbatim, everywhere). Until that file exists the
// drawn mark below is shown as an automatic fallback — no broken image.
//
// Sizes here run larger than a logo normally would, because the artwork is
// mostly negative space: the opaque bbox leaves ~9% margin on three sides and
// ~17% at the bottom, the circle cluster covers ~68% of the box, and the
// identifying glyph — the speech bubble and its three bars — is only ~31% of
// it. A 40px box therefore reads as a ~12px mark, about the optical weight of
// the 15px wordmark beside it. Shrink these and the bars stop resolving.
export function Logo({ size = 40 }: { size?: number }) {
  const [failed, setFailed] = React.useState(false)
  if (failed) return <LogoMark size={size} />
  return (
    <img
      src="/brand.png"
      width={size}
      height={size}
      alt="DataMind"
      onError={() => setFailed(true)}
      style={{
        flexShrink: 0,
        display: 'block',
        borderRadius: Math.round(size * 0.22),
        objectFit: 'cover',
      }}
    />
  )
}

function LogoMark({ size = 26 }: { size?: number }) {
  const uid = 'lg' + React.useId().replace(/[^a-zA-Z0-9]/g, '')
  const tile = `${uid}t`, head = `${uid}h`
  const bg = `${uid}bg`, gV = `${uid}v`, gM = `${uid}m`, gB = `${uid}b`, gF = `${uid}f`
  const blur = `${uid}blur`, glow = `${uid}glow`
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" style={{ flexShrink: 0 }} role="img" aria-label="DataMind">
      <defs>
        <clipPath id={tile}><rect x="0" y="0" width="100" height="100" rx="22" /></clipPath>
        <clipPath id={head}><path d={LOGO_HEAD} /></clipPath>
        <radialGradient id={bg} cx="50%" cy="36%" r="78%">
          <stop offset="0%" stopColor="#241636" />
          <stop offset="58%" stopColor="#0c0714" />
          <stop offset="100%" stopColor="#050207" />
        </radialGradient>
        <linearGradient id={gV} x1="0" y1="0" x2="0.4" y2="1">
          <stop offset="0%" stopColor="#8B3DF5" /><stop offset="100%" stopColor="#4F46E5" />
        </linearGradient>
        <linearGradient id={gM} x1="0" y1="0" x2="0.3" y2="1">
          <stop offset="0%" stopColor="#F0369A" /><stop offset="100%" stopColor="#E11D74" />
        </linearGradient>
        <linearGradient id={gB} x1="0" y1="0" x2="0.4" y2="1">
          <stop offset="0%" stopColor="#3B7BF6" /><stop offset="100%" stopColor="#2563EB" />
        </linearGradient>
        <linearGradient id={gF} x1="0.1" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#F5327D" /><stop offset="100%" stopColor="#A93AE0" />
        </linearGradient>
        <filter id={blur} x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="5" /></filter>
        <filter id={glow} x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="1.6" /></filter>
      </defs>

      <g clipPath={`url(#${tile})`}>
        <rect x="0" y="0" width="100" height="100" fill={`url(#${bg})`} />
        {/* soft background orbs */}
        <g filter={`url(#${blur})`} opacity="0.5">
          <circle cx="30" cy="30" r="24" fill="#7C3AED" />
          <circle cx="68" cy="28" r="20" fill="#F5A623" />
          <circle cx="30" cy="68" r="24" fill="#2563EB" />
          <circle cx="70" cy="70" r="24" fill="#F0369A" />
        </g>
        {/* little floating dots */}
        <g opacity="0.9">
          <circle cx="55" cy="11" r="3" fill="#F43F7A" />
          <circle cx="90" cy="45" r="3" fill="#2B7BFF" />
          <circle cx="10" cy="49" r="2.4" fill="#8B5CF6" />
          <circle cx="15" cy="77" r="2.4" fill="#F5A623" />
          <circle cx="88" cy="63" r="2.4" fill="#F43F7A" />
        </g>

        {/* the head — gradient pieces + neon seams */}
        <g clipPath={`url(#${head})`}>
          <path d={LOGO_PIECES[0].d} fill={`url(#${gV})`} />
          <path d={LOGO_PIECES[1].d} fill={`url(#${gM})`} />
          <path d={LOGO_PIECES[2].d} fill={`url(#${gF})`} />
          <path d={LOGO_PIECES[3].d} fill={`url(#${gB})`} />
          <g fill="none" strokeLinejoin="round" strokeLinecap="round">
            <path d={LOGO_SEAM_V} stroke="#fff" strokeWidth="4.6" opacity="0.4" filter={`url(#${glow})`} />
            <path d={LOGO_SEAM_H} stroke="#fff" strokeWidth="4.6" opacity="0.4" filter={`url(#${glow})`} />
            <path d={LOGO_SEAM_V} stroke="#fff" strokeWidth="3.3" opacity="0.9" />
            <path d={LOGO_SEAM_H} stroke="#fff" strokeWidth="3.3" opacity="0.9" />
            <path d={LOGO_SEAM_V} stroke={LOGO_INK} strokeWidth="2.1" />
            <path d={LOGO_SEAM_H} stroke={LOGO_INK} strokeWidth="2.1" />
          </g>
        </g>

        {/* head silhouette — glow, white rim, navy line */}
        <path d={LOGO_HEAD} fill="none" stroke="#fff" strokeWidth="6.5" opacity="0.5" strokeLinejoin="round" filter={`url(#${glow})`} />
        <path d={LOGO_HEAD} fill="none" stroke="#fff" strokeWidth="4.4" opacity="0.95" strokeLinejoin="round" />
        <path d={LOGO_HEAD} fill="none" stroke={LOGO_INK} strokeWidth="2.8" strokeLinejoin="round" />
      </g>
    </svg>
  )
}

// ── icons ─────────────────────────────────────────────────────────────────
type IconProps = { size?: number; stroke?: string; strokeWidth?: number }

const iconBase = (size: number, stroke: string, sw: number) => ({
  width: size,
  height: size,
  viewBox: '0 0 24 24',
  fill: 'none' as const,
  stroke,
  strokeWidth: sw,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  style: { flexShrink: 0 },
})

export const Icon = {
  Chat: ({ size = 17, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
    </svg>
  ),
  Database: ({ size = 17, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M3 5v6c0 1.66 4.03 3 9 3s9-1.34 9-3V5" />
      <path d="M3 11v6c0 1.66 4.03 3 9 3s9-1.34 9-3v-6" />
    </svg>
  ),
  Sparkle: ({ size = 17, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M12 3l1.9 4.6L18.5 9l-4.6 1.9L12 15.5l-1.9-4.6L5.5 9l4.6-1.4L12 3z" />
      <path d="M18 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8.8-2z" />
    </svg>
  ),
  Users: ({ size = 17, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  ),
  Trash: ({ size = 13, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  ),
  Plus: ({ size = 15, stroke = 'currentColor', strokeWidth = 2.2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  ),
  Minus: ({ size = 15, stroke = 'currentColor', strokeWidth = 2.2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M5 12h14" />
    </svg>
  ),
  Logout: ({ size = 15, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5M21 12H9" />
    </svg>
  ),
  Send: ({ size = 17, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
    </svg>
  ),
  Chevron: ({ size = 14, stroke = 'currentColor', strokeWidth = 2, open = false }: IconProps & { open?: boolean }) => (
    <svg
      {...iconBase(size, stroke, strokeWidth)}
      style={{
        flexShrink: 0,
        transform: open ? 'rotate(90deg)' : 'none',
        transition: 'transform .15s ease',
      }}
    >
      <path d="M9 18l6-6-6-6" />
    </svg>
  ),
  Check: ({ size = 14, stroke = 'currentColor', strokeWidth = 2.4 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M20 6L9 17l-5-5" />
    </svg>
  ),
  Close: ({ size = 14, stroke = 'currentColor', strokeWidth = 2.2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M18 6L6 18M6 6l12 12" />
    </svg>
  ),
  Key: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <circle cx="7.5" cy="15.5" r="4.5" />
      <path d="M10.7 12.3 21 2m-4 4 3 3m-6-1 3 3" />
    </svg>
  ),
  Pencil: ({ size = 13, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
    </svg>
  ),
  Copy: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  ),
  ArrowDown: ({ size = 14, stroke = 'currentColor', strokeWidth = 2.2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M12 5v14M19 12l-7 7-7-7" />
    </svg>
  ),
  Alert: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 8v4M12 16h.01" />
    </svg>
  ),
  Lock: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <rect x="4" y="11" width="16" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  ),
  ArrowLeft: ({ size = 15, stroke = 'currentColor', strokeWidth = 2.2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M19 12H5M12 19l-7-7 7-7" />
    </svg>
  ),
  More: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <circle cx="12" cy="5" r="1.4" />
      <circle cx="12" cy="12" r="1.4" />
      <circle cx="12" cy="19" r="1.4" />
    </svg>
  ),
  Refresh: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M21 12a9 9 0 1 1-2.6-6.4" />
      <path d="M21 3v6h-6" />
    </svg>
  ),
  Grid: ({ size = 17, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </svg>
  ),
  Search: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </svg>
  ),
  Gear: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  ),
  Expand: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
    </svg>
  ),
  Collapse: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" />
    </svg>
  ),
  Inbox: ({ size = 16, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M22 12h-6l-2 3h-4l-2-3H2" />
      <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
    </svg>
  ),
  List: ({ size = 15, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
    </svg>
  ),
  // A page with a heading and two lines of prose under it — a *document*,
  // which is what separates a report from the grid of tiles next to it in the
  // sidebar. `List` is already the list-layout toggle and would read as one.
  Doc: ({ size = 17, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
      <path d="M9 13h6M9 17h4" />
    </svg>
  ),
  Play: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M6 4.5v15l12-7.5-12-7.5z" />
    </svg>
  ),
  Grip: ({ size = 13, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <circle cx="9" cy="6" r="1.2" /><circle cx="15" cy="6" r="1.2" />
      <circle cx="9" cy="12" r="1.2" /><circle cx="15" cy="12" r="1.2" />
      <circle cx="9" cy="18" r="1.2" /><circle cx="15" cy="18" r="1.2" />
    </svg>
  ),
  // The settings screens group a long form into a few titled cards, and a
  // glyph beside each title is what lets the eye find "the credentials one"
  // without reading three headings. One icon per section, no decoration.
  Link: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" />
      <path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" />
    </svg>
  ),
  Sliders: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3" />
      <path d="M1 14h6M9 8h6M17 16h6" />
    </svg>
  ),
  Shield: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      <path d="M9.5 12l1.8 1.8 3.4-3.6" />
    </svg>
  ),
  Server: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <rect x="3" y="4" width="18" height="7" rx="2" />
      <rect x="3" y="13" width="18" height="7" rx="2" />
      <path d="M7 7.5h.01M7 16.5h.01" />
    </svg>
  ),
  Mail: ({ size = 13, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <rect x="2.5" y="5" width="19" height="14" rx="2.5" />
      <path d="M3 7.5l8.2 5.4a1.5 1.5 0 0 0 1.6 0L21 7.5" />
    </svg>
  ),
  Calendar: ({ size = 13, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <rect x="3" y="5" width="18" height="16" rx="2.5" />
      <path d="M3 10h18M8 3v4M16 3v4" />
    </svg>
  ),
  Zap: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M13 2L4.5 13.5H11l-1 8.5 8.5-11.5H12l1-8.5z" />
    </svg>
  ),
  // The theme switch says which theme it *gives* you, so it is drawn as the
  // two things you can pick rather than as an abstract on/off track.
  Sun: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 2v2.4M12 19.6V22M4.2 4.2l1.7 1.7M18.1 18.1l1.7 1.7M2 12h2.4M19.6 12H22M4.2 19.8l1.7-1.7M18.1 5.9l1.7-1.7" />
    </svg>
  ),
  Moon: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <path d="M20.5 14.3A8.5 8.5 0 1 1 9.7 3.5a6.8 6.8 0 0 0 10.8 10.8z" />
    </svg>
  ),
}

// ── hoverable button ──────────────────────────────────────────────────────
type HoverButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  baseStyle: React.CSSProperties
  hoverStyle?: React.CSSProperties
}

export function HoverButton({
  baseStyle, hoverStyle, children, ...rest
}: HoverButtonProps) {
  const [hover, setHover] = useState(false)
  return (
    <button
      {...rest}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{ ...baseStyle, ...(hover && !rest.disabled ? hoverStyle : {}) }}
    >
      {children}
    </button>
  )
}

// ── form controls ─────────────────────────────────────────────────────────
export const inputStyle: React.CSSProperties = {
  background: 'var(--input-bg)',
  border: '1px solid var(--border-strong)',
  borderRadius: 7,
  padding: '7px 11px',
  color: 'var(--text)',
  fontSize: 13.5,
  // An explicit line box, not the browser's `normal`. `normal` is derived from
  // the *primary* font's metrics — a Latin stack, about 1.2 — and an input
  // clips whatever overflows it. Scripts whose glyphs hang below the Latin
  // baseline are drawn by a fallback font with taller metrics, so Persian and
  // Arabic descenders (ی ج چ ح خ ر ز و) lost their tails, and so did the
  // occasional Latin one. 1.6 is the same ratio `TextArea` already used, and
  // the padding drops 2px to keep the control the height it was.
  lineHeight: 1.6,
  outline: 'none',
  width: '100%',
}

export function Field({
  label, children, hint,
}: {
  label: string
  children: React.ReactNode
  hint?: string
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <label style={{ fontSize: 12, color: 'var(--text-dim)' }}>{label}</label>
      {children}
      {hint && (
        <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>{hint}</span>
      )}
    </div>
  )
}

/**
 * `dir="auto"` by default, on every free-text control.
 *
 * The browser reads the first strong directional character and lays the field
 * out from it: type Persian and the caret, the alignment and the punctuation
 * go right-to-left; type English and nothing changes. An English word inside a
 * Persian sentence then falls out of the Unicode bidi algorithm correctly,
 * which a fixed `dir="ltr"` cannot do — under LTR the *sentence* is what gets
 * reordered, so a mixed title reads with its clauses in the wrong order.
 *
 * It is a default, not a rule: `{...props}` spreads after it, so a field whose
 * content is never prose — SQL, a number — passes `dir="ltr"` and wins.
 */
export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input dir="auto" {...props} style={{ ...inputStyle, ...props.style }} />
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} style={{ ...inputStyle, ...props.style }} />
}

export function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      dir="auto"
      {...props}
      style={{
        ...inputStyle,
        minHeight: 68,
        resize: 'vertical',
        fontFamily: 'inherit',
        ...props.style,
      }}
    />
  )
}

/**
 * A field that is invisible until approached: edited where it is read.
 *
 * A heading, a name, a question — text the user is *looking at* — is a longer
 * road through a dialog than it is worth. This reads as the text it holds until
 * hovered, commits on blur or Enter, and reverts on Escape.
 *
 * `multiline` is not cosmetic: a report block's question is a sentence, and an
 * `<input>` scrolls it sideways out of sight while a growing textarea keeps the
 * whole of it readable. Enter still commits there — Shift+Enter is the newline.
 */
export function InlineEdit({
  value, onCommit, ariaLabel, placeholder, required = false, multiline = false, style,
}: {
  value: string
  onCommit: (value: string) => void
  ariaLabel: string
  placeholder?: string
  /** An empty commit reverts instead — for fields that must not be blank. */
  required?: boolean
  multiline?: boolean
  style?: React.CSSProperties
}) {
  const [draft, setDraft] = useState(value)
  const box = useRef<HTMLTextAreaElement | null>(null)
  useEffect(() => setDraft(value), [value])

  // Height follows content, so the field is exactly as tall as what is in it.
  // Reset to `auto` first or `scrollHeight` only ever reports the height it
  // already has, and the box grows but never shrinks.
  useEffect(() => {
    const el = box.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [draft])

  function commit() {
    const next = draft.trim()
    if (required && !next) return setDraft(value)
    if (next !== value) onCommit(next)
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      event.currentTarget.blur()
    }
    if (event.key === 'Escape') {
      setDraft(value)
      event.currentTarget.blur()
    }
  }

  const shared = {
    className: 'rm-inline-edit',
    'aria-label': ariaLabel,
    // Prose follows whatever script it was written in — see `TextInput`.
    dir: 'auto' as const,
    value: draft,
    placeholder,
    onBlur: commit,
    onKeyDown,
  }

  return multiline ? (
    <textarea
      {...shared}
      ref={box}
      rows={1}
      onChange={(event) => setDraft(event.target.value)}
      style={{ resize: 'none', overflow: 'hidden', ...style }}
    />
  ) : (
    <input
      {...shared}
      onChange={(event) => setDraft(event.target.value)}
      style={style}
    />
  )
}

/** A labelled on/off switch, for settings a checkbox would under-sell. */
export function Toggle({
  checked, onChange, label, hint, disabled,
}: {
  checked: boolean
  onChange: (next: boolean) => void
  label: string
  hint?: string
  disabled?: boolean
}) {
  return (
    <label
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 10,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.55 : 1,
      }}
    >
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        style={{
          width: 34,
          height: 20,
          flexShrink: 0,
          marginTop: 1,
          borderRadius: 999,
          border: '1px solid var(--border-strong)',
          background: checked ? 'var(--accent)' : 'var(--panel-alt)',
          position: 'relative',
          cursor: 'inherit',
          transition: 'background 120ms ease',
          padding: 0,
        }}
      >
        <span
          style={{
            position: 'absolute',
            top: 2,
            left: checked ? 16 : 2,
            width: 14,
            height: 14,
            borderRadius: '50%',
            background: checked ? 'var(--on-accent)' : 'var(--text-faint)',
            transition: 'left 120ms ease',
          }}
        />
      </button>
      <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ fontSize: 13, color: 'var(--text-strong)' }}>{label}</span>
        {hint && (
          <span style={{ fontSize: 11.5, color: 'var(--text-dim)' }}>{hint}</span>
        )}
      </span>
    </label>
  )
}

/**
 * Determinate progress. Deliberately not a spinner: a generation takes minutes
 * and "n of m tables" is the difference between waiting and wondering.
 */
export function ProgressBar({
  current, total, label,
}: {
  current: number
  total: number
  label?: React.ReactNode
}) {
  const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, width: '100%' }}>
      {label && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            gap: 12,
            fontSize: 12.5,
            color: 'var(--text-dim)',
          }}
        >
          <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {label}
          </span>
          <span className="mono" style={{ flexShrink: 0 }}>
            {current}/{total}
          </span>
        </div>
      )}
      <div
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        style={{
          height: 6,
          borderRadius: 999,
          background: 'var(--panel-alt)',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: '100%',
            background: 'var(--accent)',
            borderRadius: 999,
            transition: 'width 240ms ease',
          }}
        />
      </div>
    </div>
  )
}

// ── buttons ───────────────────────────────────────────────────────────────
export function PrimaryButton({
  children, style, ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <HoverButton
      {...rest}
      baseStyle={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 6,
        fontSize: 13,
        fontWeight: 600,
        background: 'var(--accent)',
        color: 'var(--on-accent)',
        border: 'none',
        padding: '9px 16px',
        borderRadius: 8,
        cursor: rest.disabled ? 'not-allowed' : 'pointer',
        opacity: rest.disabled ? 0.55 : 1,
        ...style,
      }}
      hoverStyle={{ filter: 'brightness(1.08)' }}
    >
      {children}
    </HoverButton>
  )
}

export function GhostButton({
  children, style, ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <HoverButton
      {...rest}
      baseStyle={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 13,
        fontWeight: 500,
        background: 'transparent',
        color: 'var(--text-strong)',
        border: '1px solid var(--border-strong)',
        padding: '8px 14px',
        borderRadius: 7,
        cursor: 'pointer',
        ...style,
      }}
      hoverStyle={{ borderColor: 'var(--accent)' }}
    >
      {children}
    </HoverButton>
  )
}

export function DangerButton({
  children, style, ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <HoverButton
      {...rest}
      baseStyle={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 12.5,
        fontWeight: 500,
        background: 'transparent',
        color: 'var(--red)',
        border: '1px solid var(--red-border)',
        padding: '6px 12px',
        borderRadius: 6,
        cursor: 'pointer',
        ...style,
      }}
      hoverStyle={{ background: 'var(--red-bg)' }}
    >
      {children}
    </HoverButton>
  )
}

// ── chips and badges ──────────────────────────────────────────────────────
export type ChipTone = 'neutral' | 'green' | 'amber' | 'red' | 'accent'

export function Chip({
  children, tone = 'neutral', small = false, wrap = false,
}: {
  children: React.ReactNode
  tone?: ChipTone
  /** A quieter pill for dense chrome (dashboard tile headers). */
  small?: boolean
  /**
   * Let the label run onto several lines instead of one clipped one.
   *
   * A chip is a nowrap pill because its label is normally a few words. The
   * exception is a chip whose content is unbounded — the list of tables a run
   * referenced, which for a metadata question is the whole schema — and there
   * clipping hides the very thing being shown. Wrapping also blockifies the
   * span so `max-width` actually binds; a nowrap inline chip that is not a
   * flex item cannot be constrained, which is how one long label used to push
   * the whole transcript sideways.
   */
  wrap?: boolean
}) {
  const tones: Record<string, React.CSSProperties> = {
    neutral: { color: 'var(--text-dim)', background: 'var(--panel-alt)' },
    green: { color: 'var(--green)', background: 'var(--green-bg)' },
    amber: { color: 'var(--amber)', background: 'var(--amber-bg)' },
    red: { color: 'var(--red)', background: 'var(--red-bg)' },
    accent: { color: 'var(--accent)', background: 'var(--accent-bg)' },
  }
  return (
    <span
      style={{
        fontSize: small ? 10 : 11,
        fontWeight: small ? 500 : undefined,
        padding: small ? '2.5px 8px' : '4px 9px',
        borderRadius: small ? 999 : 5,
        display: wrap ? 'inline-block' : undefined,
        whiteSpace: wrap ? 'normal' : 'nowrap',
        wordBreak: wrap ? 'break-word' : undefined,
        lineHeight: wrap ? 1.6 : undefined,
        minWidth: 0,
        maxWidth: small ? 120 : '100%',
        overflow: small ? 'hidden' : undefined,
        textOverflow: small ? 'ellipsis' : undefined,
        ...tones[tone],
      }}
    >
      {children}
    </span>
  )
}

export function Dot({ color = 'var(--green)' }: { color?: string }) {
  return (
    <span
      style={{
        width: 6,
        height: 6,
        borderRadius: '50%',
        background: color,
        flexShrink: 0,
      }}
    />
  )
}

export function Spinner({ size = 14 }: { size?: number }) {
  return (
    <svg
      className="rm-spin"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.4}
      strokeLinecap="round"
      style={{ flexShrink: 0 }}
    >
      <path d="M21 12a9 9 0 1 1-6.2-8.6" />
    </svg>
  )
}

// ── feedback ──────────────────────────────────────────────────────────────
export function ErrorNote({ children }: { children: React.ReactNode }) {
  return (
    <div
      role="alert"
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 8,
        fontSize: 12.5,
        color: 'var(--red)',
        background: 'var(--red-bg)',
        border: '1px solid var(--red-border)',
        borderRadius: 8,
        padding: '9px 12px',
      }}
    >
      <Icon.Alert size={15} />
      <span>{children}</span>
    </div>
  )
}

export function EmptyState({
  title, body, action, icon,
}: {
  title: string
  body: string
  action?: React.ReactNode
  /** An optional glyph drawn in an accent-tinted badge above the title. */
  icon?: React.ReactNode
}) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 10,
        padding: '48px 24px',
        textAlign: 'center',
      }}
    >
      {icon && (
        <div
          aria-hidden
          style={{
            display: 'grid',
            placeItems: 'center',
            width: 44,
            height: 44,
            borderRadius: 12,
            background: 'var(--accent-bg)',
            border: '1px solid var(--accent-border)',
            color: 'var(--accent)',
            marginBottom: 2,
          }}
        >
          {icon}
        </div>
      )}
      <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-strong)' }}>
        {title}
      </div>
      <div style={{ fontSize: 13, color: 'var(--text-dim)', maxWidth: 380 }}>
        {body}
      </div>
      {action}
    </div>
  )
}

/**
 * A centered modal dialog over a scrim. Closes on Escape or a click on the
 * backdrop; locks body scroll while open. The header, scrollable body, and
 * optional footer are laid out so long forms scroll without the chrome moving.
 */
export function Modal({
  title, subtitle, onClose, children, footer, width = 460,
}: {
  title: React.ReactNode
  subtitle?: React.ReactNode
  onClose: () => void
  children: React.ReactNode
  footer?: React.ReactNode
  width?: number
}) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [onClose])

  return (
    <div
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 100,
        background: 'rgba(0,0,0,0.5)',
        backdropFilter: 'blur(2px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 20,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="rm-enter"
        style={{
          width: '100%',
          maxWidth: width,
          maxHeight: 'calc(100vh - 40px)',
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--panel)',
          border: '1px solid var(--border-strong)',
          borderRadius: 14,
          boxShadow: '0 24px 64px -20px rgba(0,0,0,0.55)',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 12,
            padding: '16px 18px',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-strong)' }}>
              {title}
            </div>
            {subtitle && (
              <div style={{ fontSize: 12, color: 'var(--text-faint)', marginTop: 2 }}>
                {subtitle}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 30,
              height: 30,
              flexShrink: 0,
              background: 'transparent',
              color: 'var(--text-dim)',
              border: '1px solid var(--border)',
              borderRadius: 7,
              cursor: 'pointer',
            }}
          >
            <Icon.Close size={15} />
          </button>
        </div>

        <div
          style={{
            padding: 18,
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 14,
          }}
        >
          {children}
        </div>

        {footer && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'flex-end',
              gap: 10,
              padding: '14px 18px',
              borderTop: '1px solid var(--border)',
            }}
          >
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}

/** Copies text and confirms it in place, so the click has visible feedback. */
export function CopyButton({
  text, label = 'Copy',
}: {
  text: string
  label?: string
}) {
  const [copied, setCopied] = useState(false)

  return (
    <button
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true)
          setTimeout(() => setCopied(false), 1400)
        })
      }}
      title={label}
      aria-label={label}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        fontSize: 11.5,
        fontWeight: 500,
        color: copied ? 'var(--green)' : 'var(--text-faint)',
        background: 'transparent',
        border: 'none',
        padding: '4px 6px',
        borderRadius: 6,
        cursor: 'pointer',
      }}
    >
      {copied ? <Icon.Check size={13} stroke="var(--green)" /> : <Icon.Copy size={13} />}
      {copied ? 'Copied' : label}
    </button>
  )
}

/** "just now" / "4m ago" / "3d ago" — shared by every settings surface. */
export function relativeTime(iso: string): string {
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

export function initialOf(value: string): string {
  return (value.trim()[0] ?? '?').toUpperCase()
}

// ── identity glyph ────────────────────────────────────────────────────────
/**
 * A record's mark: an icon or an initial in a tinted rounded tile.
 *
 * The same idea as the dashboard card's glyph, and deliberately the same six
 * hues: a colour derived from the id is *stable* for a given record without
 * pretending to mean anything, so a connection, a model, and a teammate are
 * each recognisable at a glance in a list and again in the header of the pane
 * that edits them. Anything that carries real meaning — reachable, admin,
 * archived — stays a chip or a dot, because a hue nobody can decode is not a
 * status.
 *
 * `hue` is optional: pass one from `identityHue` for per-record colour, or
 * leave it out for the neutral panel tone (used where the tile sits beside a
 * heading that already names the thing).
 */
export const IDENTITY_HUES = [250, 300, 340, 25, 80, 160]

export function identityHue(seed: string): number {
  let hash = 0
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 31 + seed.charCodeAt(index)) >>> 0
  }
  return IDENTITY_HUES[hash % IDENTITY_HUES.length]
}

/**
 * A hue per database *engine*, so every Postgres connection is the same colour
 * and a list can be read by shape as well as by name. Keyed on the engine
 * rather than on the row id because these are families, and members of one
 * should look related.
 *
 * It lives here rather than in the Data sources page because the chat list
 * names a connection too: the same source has to be the same colour in both,
 * or the colour means nothing.
 */
const ENGINE_HUES: Record<string, number> = {
  postgres: 250,
  mysql: 80,
  mssql: 25,
  oracle: 340,
}

export function engineHue(kind: string): number {
  return ENGINE_HUES[kind] ?? identityHue(kind)
}

/**
 * The three colours a hue-tinted glyph is drawn from, resolved per theme.
 *
 * The lightness is a token rather than a constant because the same hue has to
 * behave differently on the two grounds: 0.65 lightness is a bright mark on a
 * 0.16 background and a washed-out one on 0.96 paper. The hue is passed
 * through untouched — that part identifies the record, and a connection that
 * changed colour with the theme would be identifying nothing.
 *
 * The dashboard tile, the report card and this badge all draw from here, so
 * the same record looks the same in all three.
 */
export function glyphTint(hue: number): { background: string; border: string; color: string } {
  const tint = `var(--glyph-tint-l, 0.7) var(--glyph-tint-c, 0.16) ${hue}`
  return {
    background: `oklch(${tint} / 0.16)`,
    border: `1px solid oklch(${tint} / 0.3)`,
    color: `oklch(var(--glyph-ink-l, 0.65) var(--glyph-ink-c, 0.17) ${hue})`,
  }
}

export function GlyphBadge({
  children, hue, size = 34, radius,
}: {
  children: React.ReactNode
  hue?: number
  size?: number
  radius?: number
}) {
  const tinted = hue != null
  const tint = tinted ? glyphTint(hue) : null
  return (
    <span
      aria-hidden
      style={{
        display: 'grid',
        placeItems: 'center',
        width: size,
        height: size,
        flexShrink: 0,
        borderRadius: radius ?? Math.round(size * 0.28),
        fontSize: Math.round(size * 0.4),
        fontWeight: 700,
        background: tint?.background ?? 'var(--panel-alt)',
        border: tint?.border ?? '1px solid var(--border)',
        color: tint?.color ?? 'var(--text-dim)',
      }}
    >
      {children}
    </span>
  )
}

// Persian, Arabic, and related scripts. If the first strong character of a
// string is one of these, the text should be laid out right-to-left.
const RTL_CHARS = /[֐-׿؀-ۿ܀-߿ࢠ-ࣿיִ-﷿ﹰ-﻿]/

/** 'rtl' when the text leads with a right-to-left script, else 'ltr'. */
export function dirOf(value: string): 'rtl' | 'ltr' {
  for (const char of value) {
    if (RTL_CHARS.test(char)) return 'rtl'
    if (/[A-Za-z]/.test(char)) return 'ltr'
  }
  return 'ltr'
}

// ── result table ────────────────────────────────────────────
/**
 * A query result as a table. It lived in `chat.tsx` until dashboards
 * needed it: a TABLE tile and a chat answer render the same thing, and a
 * second copy is how the two quietly stop agreeing about what a null cell
 * or an empty result looks like. Moved, not copied (docs/dashboards.md §6).
 *
 * The shape is the artifact spec, which is also the shape a tile result
 * carries — columns, rows, and the semantic type that decides which cells
 * are right-aligned.
 */
// ── the big number ────────────────────────────────────────────────────────
/**
 * One number drawn large, with whatever context the result supported.
 *
 * Lives here rather than beside either caller because it has two: a dashboard
 * METRIC tile and a chat turn whose result was a single row. Those used to be
 * different things — the tile had a bespoke renderer that picked its own
 * column and called `toLocaleString`, and chat had nothing at all.
 *
 * It renders what it is given and decides nothing. Which column, how the
 * figure is written, what the comparison is against — all of that is
 * `plan_kpi` on the backend, which is what stops the tile and the chat turn
 * from disagreeing about the same number.
 */
export function Kpi({ spec, compact = false }: {
  spec: KpiSpecView
  /** Tighter type for a dashboard tile, which is a smaller box than a turn. */
  compact?: boolean
}) {
  /* Nothing abbreviates the figure — `_fmt_number` writes it in full, so
     `12,345,678,901.23` is an ordinary value — and a grouped number has no
     space to break at. Drawn at a fixed 40px in a quarter-width report tile it
     runs straight through the border. So the size comes down with the length,
     which keeps the whole number readable rather than clipping it, and the
     wrap below catches whatever is longer than any size can save. */
  const scale = kpiScale(spec.value, compact)
  return (
    /* The classes carry no screen styling: they are how the report's print
       stylesheet reaches a number whose size is set inline here. A headline
       figure drawn at 40px is right on a dashboard and is a third of a
       printed page. */
    <div
      className="rm-kpi"
      style={{
        height: '100%',
        maxWidth: '100%',
        minWidth: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 4,
        /* Print sets its own base size with `!important` and would drop the
           correction with it, so the scale travels as a variable the print
           stylesheet multiplies back in — styles.css, `.rm-kpi-value`. */
        ['--kpi-scale' as string]: String(scale),
      }}
    >
      <span
        title={spec.value}
        className="mono rm-kpi-value"
        style={{
          fontSize: (compact ? 34 : 40) * scale,
          fontWeight: 700,
          color: 'var(--text-strong)',
          lineHeight: 1.1,
          letterSpacing: '-0.01em',
          fontVariantNumeric: 'tabular-nums',
          maxWidth: '100%',
          textAlign: 'center',
          /* The floor on the scale means a long enough value still will not
             fit. Wrapping mid-number is ugly; a number crossing the border of
             the box it labels is worse. */
          overflowWrap: 'anywhere',
        }}
      >
        {spec.value}
      </span>
      <span
        className="rm-kpi-label"
        style={{
          fontSize: 11,
          color: 'var(--text-dim)',
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
          textAlign: 'center',
          maxWidth: '100%',
          /* A column name is one token — `total_revenue_amount` has nowhere to
             break either. */
          overflowWrap: 'anywhere',
        }}
      >
        {spec.label}
      </span>
      {spec.delta && (
        <span
          className="rm-kpi-delta"
          style={{
            fontSize: 11.5,
            color: 'var(--text-dim)',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexWrap: 'wrap',
            gap: 4,
            maxWidth: '100%',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {/* Direction is drawn, never coloured. Green-for-up is a judgement
              the data does not carry: a rising refund rate is not good news,
              and the backend cannot know which metric this is. A semantic
              pair is deferred until it can be measured against the palette
              rather than guessed at here — docs/charts.md §7. */}
          <span aria-hidden style={{ opacity: 0.8 }}>
            {spec.delta.direction === 'up' ? '▲'
              : spec.delta.direction === 'down' ? '▼' : '—'}
          </span>
          {spec.delta.text}
          <span style={{ color: 'var(--text-faint)' }}>{spec.delta.caption}</span>
        </span>
      )}
      {spec.sparkline.length > 1 && <Sparkline points={spec.sparkline} />}
      {spec.caption && (
        <span
          className="rm-kpi-caption"
          style={{
            fontSize: 10.5,
            color: 'var(--text-faint)',
            maxWidth: '100%',
            textAlign: 'center',
          }}
        >
          {spec.caption}
        </span>
      )}
    </div>
  )
}

/**
 * How much to shrink a figure so it stays inside the box that holds it.
 *
 * Counted, not measured. A measured fit means a layout read per tile and a
 * ResizeObserver to keep it honest, for a component that renders eight to a
 * report and a dozen to a dashboard — and it would still leave print, which
 * has no layout to read at the time the number is drawn, unsolved. The value
 * is monospaced, so its width *is* its length: at JetBrains Mono's 0.6em
 * advance, `budget` characters at the base size is about the inner width of
 * the narrowest box each variant lives in — a quarter-width report tile for
 * the compact one, a full-width figure for the other.
 *
 * The floor stops a pathological value from shrinking to nothing; past it the
 * value wraps instead.
 */
function kpiScale(value: string, compact: boolean): number {
  const budget = compact ? 8 : 20
  const fit = Math.min(1, Math.max(0.45, budget / value.length))
  return Math.round(fit * 100) / 100
}

export interface KpiSpecView {
  value: string
  label: string
  caption: string | null
  delta: { text: string; direction: 'up' | 'down' | 'flat'; caption: string } | null
  sparkline: number[]
}

/**
 * The series behind the number, as a bare polyline.
 *
 * Hand-drawn rather than a Vega view, and that is the deliberate exception to
 * "use the stable component". A sparkline has no axes, no ticks, no legend and
 * no scale to read — it is a shape, not a chart — while a Vega view brings a
 * dataflow runtime, a ResizeObserver and a finalize() per tile for a strip 60
 * pixels wide. Twelve lines of SVG is the smaller commitment.
 */
function Sparkline({ points }: { points: number[] }) {
  const width = 64
  const height = 18
  const low = Math.min(...points)
  const high = Math.max(...points)
  // A flat series would divide by zero; drawn down the middle it still says
  // the true thing, which is that nothing moved.
  const span = high - low || 1
  const path = points
    .map((value, i) => {
      const x = (i / (points.length - 1)) * width
      const y = height - ((value - low) / span) * height
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden
      style={{ overflow: 'visible', marginTop: 2 }}
    >
      <polyline
        points={path}
        fill="none"
        stroke="var(--text-faint)"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  )
}

// Re-exported so a caller needs one import for the table and its settings.
export type {
  ResultTableColumnConfig, ResultTableConfig,
} from './table-format'

export interface ResultTableSpec {
  columns: { name: string; db_type?: string; semantic_type: string }[]
  rows: unknown[][]
}

export function ResultTable({ spec, previewRows = 5, maxHeight = 420, config }: {
  spec: ResultTableSpec
  /** How many rows before "show all". */
  previewRows?: number
  maxHeight?: number | 'none'
  /** Omitted: every column, in query order, unsorted — as the query returned it. */
  config?: ResultTableConfig | null
}) {
  const [expanded, setExpanded] = useState(false)
  const columns = useMemo(() => resolveColumns(spec, config), [spec, config])
  const sorted = useMemo(() => sortRows(spec.rows, spec, config), [spec, config])
  const rows = expanded ? sorted : sorted.slice(0, previewRows)

  if (spec.columns.length === 0) {
    return (
      <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>
        The query ran successfully but returned no rows.
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div
        className="rm-table-wrap"
        style={{
          border: '1px solid var(--border)',
          borderRadius: 10,
          overflow: 'auto',
          maxHeight: expanded ? maxHeight : 'none',
        }}
      >
        <table
          className="rm-table"
          style={{
            width: '100%',
            borderCollapse: 'collapse',
            fontSize: 12.5,
          }}
        >
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column.name}
                  style={{
                    position: 'sticky',
                    top: 0,
                    textAlign: column.align,
                    padding: '9px 12px',
                    background: 'var(--panel-alt)',
                    color: 'var(--text-dim)',
                    fontWeight: 600,
                    fontSize: 11,
                    textTransform: 'uppercase',
                    letterSpacing: '0.04em',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {column.heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex} style={{ borderTop: '1px solid var(--border)' }}>
                {columns.map((column) => (
                  <td
                    key={column.name}
                    className={column.numeric ? 'mono' : undefined}
                    style={{
                      padding: '8px 12px',
                      textAlign: column.align,
                      color: 'var(--text2)',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {formatCell(row[column.index], column.format)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {spec.rows.length > previewRows && (
        <button
          onClick={() => setExpanded(!expanded)}
          style={{
            alignSelf: 'flex-start',
            fontSize: 12,
            color: 'var(--accent)',
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            padding: 0,
          }}
        >
          {expanded
            ? 'Show fewer rows'
            : `Show all ${spec.rows.length.toLocaleString()} rows`}
        </button>
      )}
    </div>
  )
}


// ── index chrome ──────────────────────────────────────────────────────────
/**
 * The three controls every index page needs, kept here rather than in either
 * page that uses them.
 *
 * All three began life inside `DashboardsPage`; Reports needs the same search
 * field, the same segmented filter, and — in its create dialog — the same
 * disclosure badge the chat header shows. Copying them is how the two indexes
 * quietly stop agreeing about what a filter looks like, which is the reason
 * `ResultTable` was moved here rather than duplicated (docs/dashboards.md §6).
 */
/**
 * The title block every index page opens with: a name, one line of facts about
 * the collection, and the action that adds to it.
 *
 * Dashboards and Reports each wrote this header by hand and agree on it to the
 * pixel — 25px, -0.022em, a 13.5px subtitle, the primary action pushed right.
 * Users is the third page of that kind, and a third hand-written copy is how
 * three headers quietly stop agreeing. Extracted here rather than left in
 * either page, on the same reasoning as `SearchField` and `Segmented` above.
 */
export function PageHeader({
  title, subtitle, actions,
}: {
  title: string
  /** One line under the title: what this collection currently contains. */
  subtitle: React.ReactNode
  actions?: React.ReactNode
}) {
  return (
    <header
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'flex-start',
        gap: 12,
        marginBottom: 18,
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 0 }}>
        <h1
          style={{
            margin: 0,
            fontSize: 25,
            fontWeight: 700,
            letterSpacing: '-0.022em',
            color: 'var(--text-strong)',
          }}
        >
          {title}
        </h1>
        <span style={{ fontSize: 13.5, color: 'var(--text-dim)' }}>{subtitle}</span>
      </div>
      {actions && (
        <div
          style={{
            marginLeft: 'auto',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            flexShrink: 0,
          }}
        >
          {actions}
        </div>
      )}
    </header>
  )
}

/** A dot between two facts on one line — the index pages' own punctuation. */
export function MetaDot() {
  return <span aria-hidden style={{ opacity: 0.4 }}> · </span>
}

export function DisclosureBadge({ policy }: { policy?: string }) {
  if (!policy) return null

  // `tone` names a token trio (--green / --green-bg / --green-border, likewise
  // amber) redefined per theme. The label uses the neutral --text2 (not the
  // tone) so it flips near-white in dark / near-black in light and reads as
  // native to the active palette; a saturated tone label looked like a stray
  // light-mode accent on the dark UI. The dot alone carries the policy colour.
  const copy: Record<string, { short: string; full: string; tone: 'green' | 'amber' }> = {
    NONE: { short: 'Private', full: 'No rows shared with the model provider', tone: 'green' },
    AGGREGATE: { short: 'Totals', full: 'Only aggregate totals shared with the model provider', tone: 'green' },
    SAMPLE: { short: 'Sample', full: 'Sample rows shared with the model provider', tone: 'amber' },
    FULL: { short: 'All rows', full: 'All rows shared with the model provider', tone: 'amber' },
  }
  const entry = copy[policy]
  if (!entry) return null

  return (
    <span
      title={`${entry.full} — controls how much of a query result is sent to the model provider.`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        flexShrink: 0,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: '0.01em',
        // Neutral, theme-aware label on a native surface — near-white in dark,
        // near-black in light — so the chip belongs to whichever palette is
        // active. The dot alone carries the amber/green policy signal.
        color: 'var(--text2)',
        background: `var(--${entry.tone}-bg)`,
        border: `1px solid var(--${entry.tone}-border)`,
        padding: '2px 7px 2px 6px',
        borderRadius: 999,
        whiteSpace: 'nowrap',
      }}
    >
      <span
        style={{
          width: 5,
          height: 5,
          borderRadius: '50%',
          background: `var(--${entry.tone})`,
        }}
      />
      {entry.short}
    </span>
  )
}


/** Search with the glyph inside the field, and a clear button once typed in. */
export function SearchField({
  value, onChange, placeholder, ariaLabel = 'Search',
}: {
  value: string
  onChange: (next: string) => void
  placeholder: string
  ariaLabel?: string
}) {
  return (
    <div className="rm-search">
      <span aria-hidden className="rm-search-icon"><Icon.Search size={14} /></span>
      <input
        type="search"
        aria-label={ariaLabel}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onChange('')
        }}
      />
      {value && (
        <button
          type="button"
          aria-label="Clear search"
          className="rm-search-clear rm-icon-btn"
          onClick={() => onChange('')}
          style={{ ['--rm-hover-bg' as string]: 'var(--panel-alt)' }}
        >
          <Icon.Close size={12} />
        </button>
      )}
    </div>
  )
}

/**
 * One control, several mutually exclusive states.
 *
 * Three ghost buttons in a row look like three unrelated actions; a segmented
 * control looks like one choice, which is what a filter and a layout switch
 * both are.
 */
/**
 * A small whole number, chosen by nudging it.
 *
 * A `<input type="number">` for a value with four usable settings is a text
 * field that accepts "0", "-3" and "seven" and then argues about them. Two
 * buttons and a figure cannot express a value outside the range, so the range
 * needs no error message — and the number stays readable, which is the point
 * of showing it rather than a slider.
 *
 * Typing still works for anyone who prefers it: the figure is a real input,
 * clamped on blur rather than on keystroke so a half-typed number is not
 * rewritten under the cursor.
 */
export function NumberStepper({
  value, onChange, min, max, ariaLabel, disabled = false, suffix,
}: {
  value: number
  onChange: (next: number) => void
  min: number
  max: number
  ariaLabel: string
  disabled?: boolean
  suffix?: React.ReactNode
}) {
  const [draft, setDraft] = useState(String(value))
  useEffect(() => setDraft(String(value)), [value])

  const clamp = (next: number) => Math.max(min, Math.min(max, next))
  const step = (delta: number) => onChange(clamp(value + delta))

  return (
    <div
      className="rm-stepper"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 2,
        padding: 3,
        background: 'var(--panel-alt)',
        border: '1px solid var(--border)',
        borderRadius: 9,
      }}
    >
      <StepperButton
        label={`One fewer — ${ariaLabel}`}
        disabled={disabled || value <= min}
        onClick={() => step(-1)}
      >
        <Icon.Minus size={13} />
      </StepperButton>
      <input
        aria-label={ariaLabel}
        inputMode="numeric"
        value={draft}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value.replace(/[^\d]/g, ''))}
        onBlur={() => {
          const parsed = Number.parseInt(draft, 10)
          onChange(Number.isNaN(parsed) ? value : clamp(parsed))
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.currentTarget.blur()
          if (event.key === 'ArrowUp') step(1)
          if (event.key === 'ArrowDown') step(-1)
        }}
        style={{
          width: 30,
          padding: 0,
          textAlign: 'center',
          background: 'transparent',
          border: 'none',
          outline: 'none',
          color: 'var(--text-strong)',
          fontSize: 13.5,
          fontWeight: 650,
          fontVariantNumeric: 'tabular-nums',
        }}
      />
      {suffix && (
        <span style={{ paddingRight: 5, fontSize: 12, color: 'var(--text-faint)' }}>{suffix}</span>
      )}
      <StepperButton
        label={`One more — ${ariaLabel}`}
        disabled={disabled || value >= max}
        onClick={() => step(1)}
      >
        <Icon.Plus size={13} />
      </StepperButton>
    </div>
  )
}

function StepperButton({
  label, disabled, onClick, children,
}: {
  label: string
  disabled: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="rm-icon-btn"
      style={{
        display: 'flex',
        width: 24,
        height: 24,
        alignItems: 'center',
        justifyContent: 'center',
        borderRadius: 7,
        border: 'none',
        background: 'transparent',
        color: disabled ? 'var(--text-faint)' : 'var(--text2)',
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled ? 0.45 : 1,
        ['--rm-hover-bg' as string]: 'var(--panel)',
      }}
    >
      {children}
    </button>
  )
}

export function Segmented<T extends string>({
  value, onChange, options, ariaLabel,
}: {
  value: T
  onChange: (next: T) => void
  options: { value: T; label: React.ReactNode; title?: string }[]
  ariaLabel: string
}) {
  return (
    <div className="rm-seg" role="group" aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          title={option.title}
          aria-pressed={value === option.value}
          className={value === option.value ? 'is-on' : undefined}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
