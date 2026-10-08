/**
 * What the knowledge console and the template editor both use: the SQL text
 * style, the field label and hint, and the one way an error becomes a sentence.
 */

/** SQL is **always** `dir="ltr"`, in both themes and both directions.
 *  A bidi-reordered statement is unreadable and, worse, ambiguous. */
export const CODE: React.CSSProperties = {
  fontFamily: 'var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)',
  fontSize: 12,
  lineHeight: 1.6,
  background: 'var(--code-bg)',
  color: 'var(--code-text)',
  direction: 'ltr',
  textAlign: 'left',
  whiteSpace: 'pre',
  overflowX: 'auto',
}

export function Label({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 11, fontWeight: 700, letterSpacing: 0.5,
        textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 4,
      }}
    >
      {children}
    </div>
  )
}

export function hint(): React.CSSProperties {
  return {
    fontSize: 12, color: 'var(--text-dim)', padding: '8px 12px',
    border: '1px solid var(--border)', borderRadius: 8,
    background: 'var(--panel-alt)',
  }
}

export function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : 'Something went wrong.'
}
