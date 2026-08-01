/**
 * UI primitives.
 *
 * Every dimension, radius, and font size here is lifted from the design
 * concept rather than invented. The mock is the specification.
 */
import React, { useEffect, useMemo, useState } from 'react'

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
  padding: '9px 11px',
  color: 'var(--text)',
  fontSize: 13.5,
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

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} style={{ ...inputStyle, ...props.style }} />
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} style={{ ...inputStyle, ...props.style }} />
}

export function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      style={{
        ...inputStyle,
        minHeight: 68,
        lineHeight: 1.5,
        resize: 'vertical',
        fontFamily: 'inherit',
        ...props.style,
      }}
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
  children, tone = 'neutral',
}: {
  children: React.ReactNode
  tone?: ChipTone
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
        fontSize: 11,
        padding: '4px 9px',
        borderRadius: 5,
        whiteSpace: 'nowrap',
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
  title, body, action,
}: {
  title: string
  body: string
  action?: React.ReactNode
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
 * or an empty result looks like. Moved, not copied (docs/dashboards.md §2).
 *
 * The shape is the artifact spec, which is also the shape a tile result
 * carries — columns, rows, and the semantic type that decides which cells
 * are right-aligned.
 */
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
        style={{
          border: '1px solid var(--border)',
          borderRadius: 10,
          overflow: 'auto',
          maxHeight: expanded ? maxHeight : 'none',
        }}
      >
        <table
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
