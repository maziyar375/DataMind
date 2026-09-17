/**
 * A semantic layer as a file: the export dialog and the import dialog.
 *
 * Phase 4 of `docs/plans/semantic-layer-model.md`, on the rules the dashboards'
 * `dashboard-transfer.tsx` set: a file carries no ids, no hosts and nothing a
 * connection would call a secret. Two rules are this layer's own:
 *
 *  - **Value meanings stay home unless the exporter says otherwise.** They are
 *    codes drawn from the database's data (`'C'` → cancelled), and the checkbox
 *    that includes them says so beside the count of columns that carry them.
 *  - **An import lands in the draft.** It is typing by another route: no
 *    question reads it until somebody publishes it, and the report says — before
 *    anything else — which tables the file names that this schema lacks.
 *
 * What a file holds is read by `semantic-file.ts` before anything is sent; the
 * server checks all of it again.
 */
import { useEffect, useRef, useState } from 'react'
import { ApiError, semantic as api } from '../api/client'
import type { SemanticImportReport, SemanticLayer } from '../api/types'
import {
  ErrorNote, GhostButton, Icon, Modal, PrimaryButton, Spinner, Toggle,
} from './ui'
import {
  fileContents, importSummary, layerFileName, parseLayerFile, valueMeaningColumns,
} from './semantic-file'
import type { LayerFileLike } from './semantic-file'

// ── export ─────────────────────────────────────────────────────────────────
export function ExportDialog({
  connectionId, connectionName, layer, onClose,
}: {
  connectionId: string
  connectionName: string
  layer: SemanticLayer
  onClose: () => void
}) {
  const version = layer.published_version
  const [withMeanings, setWithMeanings] = useState(false)
  // Counted from the published version, which is what leaves — not from the
  // draft the editor may be showing.
  const [columns, setColumns] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (version === null) return
    let live = true
    api.version(connectionId, version)
      .then((row) => {
        const entities = (row.document.entities ?? []) as { columns: { value_meanings?: Record<string, string> }[] }[]
        if (live) setColumns(valueMeaningColumns(entities))
      })
      .catch(() => live && setColumns(null))
    return () => {
      live = false
    }
  }, [connectionId, version])

  async function download() {
    setBusy(true)
    setError(null)
    try {
      const file = await api.exportFile(connectionId, { valueMeanings: withMeanings })
      // Fetched, not linked: a plain link sends no Authorization header.
      const blob = new Blob([JSON.stringify(file, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = layerFileName(connectionName, file.source.version)
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      setTimeout(() => URL.revokeObjectURL(url), 0)
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not export this layer.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title="Export the semantic layer"
      subtitle={
        version !== null
          ? `Published v${version} as a JSON file — the layer questions read, not the draft.`
          : 'Nothing has been published yet.'
      }
      onClose={onClose}
      width={480}
      footer={
        <>
          <GhostButton onClick={onClose} disabled={busy}>Cancel</GhostButton>
          <PrimaryButton onClick={download} disabled={busy || version === null}>
            {busy ? <Spinner /> : <Icon.ArrowDown size={13} />}
            Download
          </PrimaryButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: 'var(--text-dim)' }}>
          The file names this connection and its engine, and nothing else about
          it — no host, no user, no password. Joins and validity are left out:
          whoever imports it re-reads them against their own schema.
        </p>
        <Toggle
          checked={withMeanings}
          onChange={setWithMeanings}
          disabled={columns === 0}
          label={
            columns === null
              ? 'Include value meanings'
              : `Include value meanings (${columns} ${columns === 1 ? 'column' : 'columns'})`
          }
          hint="Codes and what they mean — ‘C’ is cancelled — drawn from this database’s data. Off, the file carries no value from the data."
        />
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Modal>
  )
}

// ── import ─────────────────────────────────────────────────────────────────
type Phase = 'choose' | 'preview' | 'done'

export function ImportDialog({
  connectionId, layer, dirty, onClose, onImported, onPublish,
}: {
  connectionId: string
  layer: SemanticLayer | null
  /** Unsaved edits in the editor. An import replaces the draft, so it waits. */
  dirty: boolean
  onClose: () => void
  onImported: (next: SemanticLayer) => void
  onPublish: () => void
}) {
  const [phase, setPhase] = useState<Phase>('choose')
  const [file, setFile] = useState<{ parsed: LayerFileLike; raw: unknown; name: string } | null>(null)
  const [report, setReport] = useState<SemanticImportReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const fileInput = useRef<HTMLInputElement | null>(null)

  async function read(chosen: File) {
    setError(null)
    const parsed = parseLayerFile(await chosen.text())
    if (!parsed.ok) {
      setError(parsed.error)
      return
    }
    setFile({ parsed: parsed.file, raw: parsed.raw, name: chosen.name })
    setPhase('preview')
  }

  async function run() {
    if (!file || !layer) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.importFile(connectionId, file.raw, { baseRevision: layer.revision })
      setReport(result.report)
      onImported(result.layer)
      setPhase('done')
    } catch (err) {
      if (err instanceof ApiError && err.code === 'E_SEMANTIC_CONFLICT') {
        const who = err.detail?.updated_by_name || 'Someone'
        setError(`${who} changed this layer since it was opened. Close this, reload the layer, and import again.`)
      } else if (err instanceof ApiError && err.code === 'E_SEMANTIC_NO_CHANGES') {
        setError('That file says exactly what this layer already says — there is nothing to import.')
      } else {
        setError(err instanceof Error ? err.message : 'Could not import that file.')
      }
    } finally {
      setBusy(false)
    }
  }

  const contents = file ? fileContents(file.parsed) : null
  const summary = report ? importSummary(report) : null
  const source = file?.parsed.source

  return (
    <Modal
      title="Import a semantic layer"
      subtitle="Into your draft. No question reads it until you publish."
      onClose={onClose}
      width={520}
      footer={
        phase === 'done' ? (
          <>
            <GhostButton onClick={onClose}>Close</GhostButton>
            <PrimaryButton onClick={onPublish}>Review and publish</PrimaryButton>
          </>
        ) : phase === 'preview' ? (
          <>
            <GhostButton
              onClick={() => {
                setPhase('choose')
                setFile(null)
                setError(null)
              }}
              disabled={busy}
            >
              Back
            </GhostButton>
            <PrimaryButton onClick={run} disabled={busy || dirty || !layer}>
              {busy && <Spinner />}
              Import into draft
            </PrimaryButton>
          </>
        ) : (
          <GhostButton onClick={onClose}>Cancel</GhostButton>
        )
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {phase === 'choose' && (
          <>
            <div
              className={`rm-dropzone${dragging ? ' is-over' : ''}`}
              onDragOver={(event) => {
                event.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault()
                setDragging(false)
                const chosen = event.dataTransfer.files[0]
                if (chosen) void read(chosen)
              }}
              onClick={() => fileInput.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') fileInput.current?.click()
              }}
            >
              <span aria-hidden style={{ display: 'flex', color: 'var(--text-dim)' }}>
                <Icon.ArrowDown size={18} />
              </span>
              <span style={{ fontSize: 13, color: 'var(--text)' }}>
                Drop a semantic layer export here, or <u>choose a file</u>
              </span>
              <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
                From this connection, another one, or another DataMind.
              </span>
            </div>
            <input
              ref={fileInput}
              type="file"
              accept="application/json,.json"
              hidden
              aria-label="Semantic layer file"
              onChange={(event) => {
                const chosen = event.target.files?.[0]
                if (chosen) void read(chosen)
                event.target.value = ''
              }}
            />
          </>
        )}

        {phase === 'preview' && file && contents && (
          <>
            <div
              style={{
                border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px',
                display: 'flex', flexDirection: 'column', gap: 6, background: 'var(--panel-alt)',
              }}
            >
              <span className="mono" style={{ fontSize: 12, color: 'var(--text-dim)', wordBreak: 'break-all' }}>
                {file.name}
              </span>
              {source?.connection && (
                <span style={{ fontSize: 13, color: 'var(--text-strong)' }}>
                  From <strong dir="auto">{source.connection}</strong>
                  {source.engine ? ` (${source.engine})` : ''}
                  {source.version ? `, published v${source.version}` : ''}
                </span>
              )}
              <span style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
                {contents.tables} {contents.tables === 1 ? 'table' : 'tables'} · {contents.metrics}{' '}
                {contents.metrics === 1 ? 'metric' : 'metrics'} · {contents.terms}{' '}
                {contents.terms === 1 ? 'glossary term' : 'glossary terms'}
              </span>
              {contents.valueMeaningColumns > 0 && (
                <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                  Carries value meanings on {contents.valueMeaningColumns}{' '}
                  {contents.valueMeaningColumns === 1 ? 'column' : 'columns'} — codes from the source’s data.
                </span>
              )}
            </div>
            <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-dim)' }}>
              Every table and metric is checked against this connection’s schema.
              Anything that does not resolve comes in flagged, not dropped, and
              stays out of the prompt.
              {layer?.has_draft && ' It replaces the unpublished changes in your current draft.'}
            </p>
            {dirty && (
              <ErrorNote>You have unsaved edits in the editor. Save or discard them first, so nothing is replaced without you seeing it.</ErrorNote>
            )}
          </>
        )}

        {phase === 'done' && summary && (
          <div
            role="status"
            style={{
              display: 'flex', flexDirection: 'column', gap: 6, padding: '12px 14px', borderRadius: 10,
              border: `1px solid ${summary.tone === 'warn' ? 'var(--amber-border)' : 'var(--green-border)'}`,
              background: summary.tone === 'warn' ? 'var(--amber-bg)' : 'var(--green-bg)',
            }}
          >
            {summary.lines.map((line, index) => (
              <span
                key={index}
                style={{ display: 'flex', gap: 7, fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-strong)' }}
              >
                <span aria-hidden style={{ color: line.warn ? 'var(--amber)' : 'var(--green)' }}>
                  {line.warn ? '◆' : '✓'}
                </span>
                {line.text}
              </span>
            ))}
          </div>
        )}

        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Modal>
  )
}
