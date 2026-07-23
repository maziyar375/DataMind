/**
 * UI primitives.
 *
 * Every dimension, radius, and font size here is lifted from the design
 * concept rather than invented. The mock is the specification.
 */
import React, { useState } from 'react'

// ── logo ──────────────────────────────────────────────────────────────────
export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="4 12 92 78" style={{ flexShrink: 0 }}>
      <path
        d="M22 16 H78 A14 14 0 0 1 92 30 V58 A14 14 0 0 1 78 72 H42 L26 86 V72 H22 A14 14 0 0 1 8 58 V30 A14 14 0 0 1 22 16 Z"
        fill="#5C8AE6"
      />
      <rect x="30" y="48" width="9" height="14" rx="2" fill="#ffffff" />
      <rect x="44" y="40" width="9" height="22" rx="2" fill="#ffffff" />
      <rect x="58" y="32" width="9" height="30" rx="2" fill="#ffffff" />
      <path
        d="M74 20 C75.4 27 76.6 28.2 84 30 C76.6 31.8 75.4 33 74 40 C72.6 33 71.4 31.8 64 30 C71.4 28.2 72.6 27 74 20 Z"
        fill="#3FC79E"
      />
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
  Alert: ({ size = 14, stroke = 'currentColor', strokeWidth = 2 }: IconProps) => (
    <svg {...iconBase(size, stroke, strokeWidth)}>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 8v4M12 16h.01" />
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
export function Chip({
  children, tone = 'neutral',
}: {
  children: React.ReactNode
  tone?: 'neutral' | 'green' | 'amber' | 'red' | 'accent'
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

export function initialOf(value: string): string {
  return (value.trim()[0] ?? '?').toUpperCase()
}
