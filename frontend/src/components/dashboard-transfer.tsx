/**
 * Moving a dashboard between accounts and installations.
 *
 * Export is one call and a saved file. Import is a conversation, and it exists
 * because of one thing the file cannot answer: **which database is this?** A
 * document names the connections its tiles were written against, and the person
 * importing it is the only one who can say which of *their* connections each of
 * those is. Sending the file blind and letting the guard sort it out would fail
 * a dozen tiles for one wrong pick and explain none of it.
 *
 * So the browser reads the file first (`dashboard-document.ts` — the rules,
 * DOM-free and tested), shows what is in it, asks the mapping question, and only
 * then posts. The backend re-validates every byte of this and runs the guard
 * over every statement: nothing here is trusted, and nothing here is a check the
 * server does not repeat.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { ApiError, connections as connectionsApi, dashboards as api } from '../api/client'
import type {
  Connection, DashboardDocument, DashboardImportResult, ImportSkip,
} from '../api/types'
import {
  engineMismatches, exportFileName, matchConnections, orphanTiles, parseDocument, unmappedRefs,
} from './dashboard-document'
import {
  ErrorNote, Field, GhostButton, Icon, Modal, PrimaryButton, Select, Spinner, TextInput,
} from './ui'

/**
 * Fetch a dashboard's document and hand it to the browser as a file.
 *
 * Fetched rather than linked: `<a href="/api/v1/…" download>` sends no
 * Authorization header, so a plain link would download a 401. The object URL is
 * revoked on the next tick — the click has already been dispatched by then, and
 * leaving it alive pins the blob for the life of the page.
 */
export async function exportDashboard(id: string, name: string): Promise<void> {
  const document_ = await api.exportDocument(id)
  const blob = new Blob([JSON.stringify(document_, null, 2)], {
    type: 'application/json',
  })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = exportFileName(name)
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

type Phase = 'choose' | 'map' | 'done'

export function ImportDialog({
  onClose, onImported,
}: {
  onClose: () => void
  /** The dashboard that was created, so the page can open or list it. */
  onImported: (result: DashboardImportResult) => void
}) {
  const [phase, setPhase] = useState<Phase>('choose')
  const [document_, setDocument] = useState<DashboardDocument | null>(null)
  const [name, setName] = useState('')
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [connections, setConnections] = useState<Connection[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  /** The tiles a refusal named, so "import the rest" can say what it drops. */
  const [refused, setRefused] = useState<string[] | null>(null)
  const [result, setResult] = useState<DashboardImportResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    connectionsApi
      .list()
      .then(setConnections)
      .catch(() => setConnections([]))
  }, [])

  const read = useCallback(
    async (file: File) => {
      setError(null)
      setRefused(null)
      const parsed = parseDocument(await file.text())
      if (!parsed.ok) return setError(parsed.error)
      setDocument(parsed.document)
      setName(parsed.document.dashboard.name)
      setPhase('map')
    },
    [],
  )

  // The mapping is proposed once the file and the connection list are both in.
  // Only names match, and only exactly — see `matchConnections` for why nothing
  // softer is offered.
  useEffect(() => {
    if (!document_ || !connections) return
    setMapping(matchConnections(document_.connections, connections))
  }, [connections, document_])

  const needed = useMemo(
    () => (document_ ? unmappedRefs(document_, mapping) : []),
    [document_, mapping],
  )
  const mismatched = useMemo(
    () =>
      document_ && connections
        ? engineMismatches(document_.connections, mapping, connections)
        : [],
    [connections, document_, mapping],
  )

  const submit = useCallback(
    async (skipInvalid: boolean) => {
      if (!document_) return
      setBusy(true)
      setError(null)
      try {
        const imported = await api.importDocument({
          document: document_,
          name: name.trim() || null,
          connection_map: Object.fromEntries(
            Object.entries(mapping).filter(([, id]) => id),
          ),
          skip_invalid: skipInvalid,
        })
        // Tiles were dropped, so the dialog stays up to say which. Closing onto
        // a dashboard that is quietly missing four tiles is the one outcome
        // this whole flow exists to prevent.
        if (imported.skipped.length > 0) {
          setResult(imported)
          setPhase('done')
        } else {
          onImported(imported)
        }
      } catch (err) {
        if (err instanceof ApiError) {
          setError(err.message)
          setRefused(err.detail?.tiles ?? null)
        } else {
          setError('That import did not work.')
        }
      } finally {
        setBusy(false)
      }
    },
    [document_, mapping, name, onImported],
  )

  const tileCount = document_?.tiles.length ?? 0
  const orphans = document_ ? orphanTiles(document_) : 0

  return (
    <Modal
      title={phase === 'done' ? 'Imported, with tiles dropped' : 'Import a dashboard'}
      subtitle={
        phase === 'choose'
          ? 'A .json file exported from DataMind. It holds the layout and the SQL — never any data.'
          : phase === 'map'
            ? 'Say which of your connections each of its databases is.'
            : undefined
      }
      width={560}
      onClose={onClose}
      footer={
        phase === 'done' ? (
          <PrimaryButton onClick={() => result && onImported(result)}>
            Open dashboard
          </PrimaryButton>
        ) : phase === 'map' ? (
          <>
            <GhostButton onClick={onClose}>Cancel</GhostButton>
            {/* Offered only once the server has said what it would refuse.
                "Import anyway" before that would be a checkbox asking the user
                to accept losses nobody has counted yet. */}
            {refused && (
              <GhostButton onClick={() => void submit(true)} disabled={busy}>
                Import the other {Math.max(tileCount - refused.length, 0)}
              </GhostButton>
            )}
            <PrimaryButton
              onClick={() => void submit(false)}
              disabled={busy || needed.length > 0}
            >
              {busy ? <Spinner size={13} /> : <Icon.Check size={13} />}
              {busy ? 'Importing…' : `Import ${tileCount} ${tileCount === 1 ? 'tile' : 'tiles'}`}
            </PrimaryButton>
          </>
        ) : (
          <GhostButton onClick={onClose}>Cancel</GhostButton>
        )
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {error && (
          <ErrorNote>
            {error}
            {refused && refused.length > 0 && (
              <div style={{ marginTop: 6, fontSize: 11.5, opacity: 0.9 }}>
                {refused.join(' · ')}
              </div>
            )}
          </ErrorNote>
        )}

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
                const file = event.dataTransfer.files[0]
                if (file) void read(file)
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
                Drop an export here, or <u>choose a file</u>
              </span>
              <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
                Exported from any DataMind — your own account or someone else's.
              </span>
            </div>
            <input
              ref={fileInput}
              type="file"
              accept="application/json,.json"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) void read(file)
                // Cleared so choosing the same file twice fires again — after a
                // failed parse, re-picking it is the obvious second attempt.
                event.target.value = ''
              }}
            />
          </>
        )}

        {phase === 'map' && document_ && (
          <>
            <Field
              label="Name"
              hint="A name you already use gets a number — importing your own export is not a collision worth refusing."
            >
              <TextInput value={name} onChange={(event) => setName(event.target.value)} />
            </Field>

            <div className="rm-import-facts">
              <span>
                <strong>{tileCount}</strong> {tileCount === 1 ? 'tile' : 'tiles'}
              </span>
              <span aria-hidden style={{ opacity: 0.4 }}>·</span>
              <span>
                <strong>{document_.connections.length}</strong>
                {document_.connections.length === 1 ? ' database' : ' databases'}
              </span>
              {document_.exported_at && (
                <>
                  <span aria-hidden style={{ opacity: 0.4 }}>·</span>
                  <span>exported {document_.exported_at.slice(0, 10)}</span>
                </>
              )}
            </div>

            {document_.connections.length === 0 ? (
              <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>
                No tile in this file queries a database, so there is nothing to map.
              </span>
            ) : (
              document_.connections.map((ref) => (
                <Field
                  key={ref.ref}
                  label={`“${ref.name}”${ref.database_type ? ` · ${ref.database_type}` : ''}`}
                >
                  <Select
                    value={mapping[ref.ref] ?? ''}
                    onChange={(event) => {
                      setMapping((current) => ({ ...current, [ref.ref]: event.target.value }))
                      // A different database is a different verdict. Leaving the
                      // last refusal on screen would offer to skip tiles that
                      // this connection may well accept.
                      setRefused(null)
                      setError(null)
                    }}
                  >
                    <option value="">Choose a connection…</option>
                    {(connections ?? []).map((connection) => (
                      <option key={connection.id} value={connection.id}>
                        {connection.name} ({connection.database_type})
                      </option>
                    ))}
                  </Select>
                </Field>
              ))
            )}

            {connections !== null && connections.length === 0 && (
              <ErrorNote>
                You have no data sources yet. Add one under Data sources, sync its
                schema, then import this file.
              </ErrorNote>
            )}

            {/* Both notes are warnings, not walls: each names a reason the guard
                is about to refuse tiles, said once here instead of a dozen times
                after the round trip. */}
            {mismatched.length > 0 && (
              <Note>
                {mismatched
                  .map((item) => `“${item.ref.name}” was ${item.ref.database_type}; `
                    + `${item.chosen.name} is ${item.chosen.database_type}`)
                  .join('. ')}
                . SQL written for one engine often will not parse on another.
              </Note>
            )}
            {orphans > 0 && (
              <Note>
                {orphans} {orphans === 1 ? 'tile names' : 'tiles name'} no database — its
                connection was already gone when the file was written. Import the rest,
                then point {orphans === 1 ? 'it' : 'them'} at one here.
              </Note>
            )}
            {needed.length > 0 && (
              <Note>
                {needed.map((ref) => `“${ref.name}”`).join(', ')} still needs a connection.
              </Note>
            )}
          </>
        )}

        {phase === 'done' && result && (
          <>
            <span style={{ fontSize: 13, color: 'var(--text)' }}>
              <strong>{result.imported_tiles}</strong>
              {result.imported_tiles === 1 ? ' tile' : ' tiles'} imported into
              {' '}
              <strong>{result.dashboard.name}</strong>. These were refused by the guard
              against the connection you chose, and were not saved:
            </span>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {result.skipped.map((skip: ImportSkip, index: number) => (
                <div key={`${skip.title}-${index}`} className="rm-import-skip">
                  <span style={{ fontSize: 12.5, color: 'var(--text-strong)' }}>
                    {skip.title}
                  </span>
                  <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
                    {skip.reason}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </Modal>
  )
}

/** A quiet caution — something to know before pressing Import, not an error. */
function Note({ children }: { children: React.ReactNode }) {
  return (
    <div className="rm-attention" style={{ margin: 0 }}>
      <span aria-hidden style={{ display: 'flex', color: 'var(--amber)' }}>
        <Icon.Alert size={14} />
      </span>
      <span>{children}</span>
    </div>
  )
}
