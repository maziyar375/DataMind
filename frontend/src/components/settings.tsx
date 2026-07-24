/**
 * Master–detail furniture for the settings pages.
 *
 * Data sources and LLM providers are the same shape of screen: pick a record
 * on the left, edit it on the right. Sharing the frame here keeps the two
 * pages visually identical and leaves each page file holding only its own
 * fields.
 */
import React, { useState } from 'react'
import { Icon } from './ui'

// ── left column ───────────────────────────────────────────────────────────
export function MasterColumn({
  title, count, onNew, newLabel, empty, children,
}: {
  title: string
  count: number
  onNew: () => void
  newLabel: string
  empty: string
  children: React.ReactNode
}) {
  return (
    <div
      style={{
        width: 268,
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
        borderRight: '1px solid var(--border)',
        background: 'var(--sidebar-bg)',
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '18px 16px 12px',
        }}
      >
        <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-strong)' }}>
          {title}
        </span>
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: 'var(--text-faint)',
            background: 'var(--panel-alt)',
            padding: '2px 7px',
            borderRadius: 20,
          }}
        >
          {count}
        </span>
        <button
          onClick={onNew}
          title={newLabel}
          aria-label={newLabel}
          style={{
            marginLeft: 'auto',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 5,
            fontSize: 12,
            fontWeight: 600,
            color: 'var(--accent)',
            background: 'var(--accent-bg)',
            border: '1px solid var(--accent-border)',
            padding: '5px 10px',
            borderRadius: 7,
            cursor: 'pointer',
          }}
        >
          <Icon.Plus size={13} stroke="var(--accent)" />
          New
        </button>
      </div>

      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '0 10px 16px',
          display: 'flex',
          flexDirection: 'column',
          gap: 3,
        }}
      >
        {count === 0 ? (
          <p
            style={{
              fontSize: 12.5,
              color: 'var(--text-dim)',
              lineHeight: 1.5,
              padding: '4px 6px',
              margin: 0,
            }}
          >
            {empty}
          </p>
        ) : (
          children
        )}
      </div>
    </div>
  )
}

export function MasterItem({
  title, subtitle, active, tone, onClick,
}: {
  title: string
  subtitle: string
  active: boolean
  tone: 'green' | 'red' | 'neutral'
  onClick: () => void
}) {
  const [hover, setHover] = useState(false)
  const dotColor =
    tone === 'green' ? 'var(--green)' : tone === 'red' ? 'var(--red)' : 'var(--text-faint)'

  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        width: '100%',
        padding: '9px 10px',
        borderRadius: 8,
        cursor: 'pointer',
        textAlign: 'left',
        border: 'none',
        background: active
          ? 'var(--accent-bg)'
          : hover
            ? 'var(--panel-hover)'
            : 'transparent',
        boxShadow: active ? 'inset 2px 0 0 var(--accent)' : 'none',
        transition: 'background .12s ease',
      }}
    >
      <span
        style={{
          width: 7,
          height: 7,
          borderRadius: '50%',
          background: dotColor,
          flexShrink: 0,
        }}
      />
      <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, gap: 1 }}>
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: active ? 'var(--text-strong)' : 'var(--text)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {title}
        </span>
        <span
          className="mono"
          style={{
            fontSize: 10.5,
            color: 'var(--text-faint)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {subtitle}
        </span>
      </span>
    </button>
  )
}

// ── detail pane ───────────────────────────────────────────────────────────
export function DetailHeader({
  title, subtitle, chips, actions,
}: {
  title: string
  subtitle: React.ReactNode
  chips?: React.ReactNode
  actions: React.ReactNode
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 16,
        padding: '20px 28px 16px',
        borderBottom: '1px solid var(--border)',
        flexShrink: 0,
      }}
    >
      <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div
          style={{
            fontSize: 17,
            fontWeight: 700,
            color: 'var(--text-strong)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {title}
        </div>
        <div
          className="mono"
          style={{ fontSize: 12, color: 'var(--text-dim)' }}
        >
          {subtitle}
        </div>
        {chips && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 2 }}>
            {chips}
          </div>
        )}
      </div>
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
    </div>
  )
}

export function Tabs({
  value, onChange, items,
}: {
  value: string
  onChange: (value: string) => void
  items: { value: string; label: string; count?: number }[]
}) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 2,
        padding: '0 28px',
        borderBottom: '1px solid var(--border)',
        flexShrink: 0,
      }}
    >
      {items.map((item) => {
        const active = item.value === value
        return (
          <button
            key={item.value}
            onClick={() => onChange(item.value)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 13,
              fontWeight: 600,
              padding: '11px 14px',
              background: 'transparent',
              border: 'none',
              borderBottom: `2px solid ${active ? 'var(--accent)' : 'transparent'}`,
              color: active ? 'var(--text-strong)' : 'var(--text-dim)',
              cursor: 'pointer',
              marginBottom: -1,
            }}
          >
            {item.label}
            {item.count != null && (
              <span
                style={{
                  fontSize: 10.5,
                  fontWeight: 600,
                  color: 'var(--text-faint)',
                  background: 'var(--panel-alt)',
                  padding: '1px 6px',
                  borderRadius: 20,
                }}
              >
                {item.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

/** A titled card. Groups related fields so a long form reads as a few parts. */
export function Section({
  title, description, danger, children,
}: {
  title: string
  description?: string
  danger?: boolean
  children: React.ReactNode
}) {
  return (
    <section
      style={{
        border: `1px solid ${danger ? 'var(--red-border)' : 'var(--border)'}`,
        borderRadius: 12,
        background: 'var(--panel)',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          padding: '13px 18px',
          borderBottom: '1px solid var(--border)',
          background: danger ? 'var(--red-bg)' : 'transparent',
        }}
      >
        <div
          style={{
            fontSize: 12.5,
            fontWeight: 700,
            color: danger ? 'var(--red)' : 'var(--text-strong)',
          }}
        >
          {title}
        </div>
        {description && (
          <div style={{ fontSize: 11.5, color: 'var(--text-dim)', marginTop: 3 }}>
            {description}
          </div>
        )}
      </div>
      <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
        {children}
      </div>
    </section>
  )
}

/** Lays fields out in equal columns, collapsing to one on a narrow pane. */
export function FieldRow({
  columns = 2, children,
}: {
  columns?: number
  children: React.ReactNode
}) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
        gap: 12,
      }}
    >
      {children}
    </div>
  )
}

/** The scrollable body of a detail tab, with a comfortable reading width. */
export function DetailBody({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 28, minHeight: 0 }}>
      <div
        style={{
          maxWidth: 720,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}
      >
        {children}
      </div>
    </div>
  )
}

export function StatusLine({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 12.5,
        color: ok ? 'var(--green)' : 'var(--red)',
        background: ok ? 'var(--green-bg)' : 'var(--red-bg)',
        border: `1px solid ${ok ? 'transparent' : 'var(--red-border)'}`,
        borderRadius: 8,
        padding: '9px 12px',
      }}
    >
      {ok ? <Icon.Check size={14} stroke="var(--green)" /> : <Icon.Alert size={14} stroke="var(--red)" />}
      <span>{children}</span>
    </div>
  )
}
