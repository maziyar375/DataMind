/**
 * Publishing a semantic layer draft.
 *
 * Phase 2 of `docs/plans/semantic-layer-model.md`: an edit, a generation and a
 * restore land in a draft that no question reads, and this dialog is the act
 * that makes the draft what the model reads. Three parts, in the order a person
 * needs them:
 *
 *  - **What changes**, grouped by table and put into words by
 *    `semantic-changes.ts` — the server's own change list, never a comparison
 *    made here. A change that moves a number sits first and says so.
 *  - **Why**, a note. Required when anything changes numbers: a dashboard's SQL
 *    does not show that `revenue` now excludes refunds, and the note is the only
 *    record of the reason. The server refuses the publish without one
 *    (`E_SEMANTIC_NOTE_REQUIRED`); the button is disabled before it gets there.
 *  - **Score this draft**, when the connection has a benchmark set: one run of
 *    the set against the draft, its held-out accuracy beside the latest run of
 *    the published layer. Advisory — a set is only as representative as its
 *    curator made it — and a delta only when the two runs are the same
 *    measurement (`semantic-score.ts`), otherwise the reason there is none.
 *    The cost is stated before it is spent.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError, knowledge as knowledgeApi, llmConfigs as llmApi, semantic as api } from '../api/client'
import type {
  BenchmarkSet, LlmConfig, ProblemDetail, SemanticChange, SemanticLayer,
} from '../api/types'
import {
  ErrorNote, Field, GhostButton, Icon, Modal, PrimaryButton, Select, Spinner, TextArea,
} from './ui'
import { groupChanges } from './semantic-changes'
import { ChangeWords, NumbersChip } from './semantic-history'
import { deltaWords, draftScore, modelName } from './semantic-score'

export function PublishDialog({
  connectionId, layer, onClose, onPublished, onConflict,
}: {
  connectionId: string
  layer: SemanticLayer
  onClose: () => void
  onPublished: (next: SemanticLayer) => void
  /** Somebody wrote the layer after this dialog's layer was read. */
  onConflict: (detail: ProblemDetail) => void
}) {
  const changes = layer.unpublished_changes
  const numbers = changes.some((c) => c.affects_sql)
  const next = (layer.published_version ?? 0) + 1
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const blocked = numbers && note.trim() === ''

  async function publish() {
    setBusy(true)
    setError(null)
    try {
      onPublished(await api.publish(connectionId, { baseRevision: layer.revision, note: note.trim() }))
    } catch (err) {
      if (err instanceof ApiError && err.code === 'E_SEMANTIC_CONFLICT') {
        onConflict(err.detail ?? {})
      } else {
        setError(err instanceof Error ? err.message : 'Could not publish this draft.')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={`Publish v${next}`}
      subtitle="What the model reads from the next question on. Nothing in the draft reaches an answer until then."
      onClose={onClose}
      width={600}
      footer={
        <>
          <GhostButton onClick={onClose} disabled={busy}>Cancel</GhostButton>
          <PrimaryButton
            onClick={publish}
            disabled={busy || blocked || changes.length === 0}
            title={blocked ? 'Say why these numbers change first' : undefined}
          >
            {busy && <Spinner />}
            Publish v{next}
          </PrimaryButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-strong)', flex: 1 }}>
              {changes.length} {changes.length === 1 ? 'change' : 'changes'} against
              {layer.published_version ? ` v${layer.published_version}` : ' an empty layer'}
            </span>
            {numbers && <NumbersChip />}
          </div>
          <div
            style={{
              maxHeight: 240, overflowY: 'auto', border: '1px solid var(--border)',
              borderRadius: 10, padding: '10px 12px', background: 'var(--panel-alt)',
            }}
          >
            {changes.length === 0 ? (
              <span style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
                The draft says the same thing to the model as the published layer.
              </span>
            ) : (
              <ChangeList changes={changes} />
            )}
          </div>
        </section>

        <Field
          label={numbers ? 'Why these numbers change' : 'Note'}
          hint={
            numbers
              ? 'Required. A dashboard’s SQL will not show this change — the note is the only record of the reason.'
              : 'Optional. The next person to read this history will want to know.'
          }
        >
          <TextArea
            autoFocus
            value={note}
            maxLength={2000}
            aria-required={numbers}
            placeholder={numbers ? 'e.g. Finance counts refunds as negative revenue from Q3.' : 'e.g. Clearer names for the order tables.'}
            onChange={(e) => setNote(e.target.value)}
            style={{ minHeight: 64 }}
          />
        </Field>

        <ScoreSection connectionId={connectionId} revision={layer.revision} />

        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Modal>
  )
}

/** A grouped change list, compact: a heading per table, a line per change,
 *  a glyph and a word — never colour alone — on each line that moves a number. */
export function ChangeList({ changes }: { changes: SemanticChange[] }) {
  const groups = useMemo(() => groupChanges(changes), [changes])
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {groups.map((group) => (
        <div key={group.key} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <span
            className={group.titleIsCode ? 'mono' : undefined}
            dir={group.titleIsCode ? 'ltr' : undefined}
            style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--text-dim)' }}
          >
            {group.title}
          </span>
          {group.lines.map((line, index) => (
            <span
              key={`${line.kind}:${line.itemKey}:${index}`}
              style={{ display: 'flex', gap: 7, fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-strong)' }}
            >
              <span aria-hidden style={{ color: line.affectsSql ? 'var(--amber)' : 'var(--text-faint)' }}>
                {line.affectsSql ? '◆' : '·'}
              </span>
              <span style={{ minWidth: 0 }}>
                <ChangeWords segments={line.segments} />
                {line.affectsSql && (
                  <span style={{ color: 'var(--amber)', fontSize: 11 }}> — changes numbers</span>
                )}
              </span>
            </span>
          ))}
        </div>
      ))}
    </div>
  )
}

// ── scoring the draft ──────────────────────────────────────────────────────
/**
 * *Score this draft*, when the connection has a benchmark set.
 *
 * Silent when there is nothing to offer: no set, or a reader who may not see
 * the knowledge store (the benchmark list is its own resource type, and a 403
 * or 404 here is not this dialog's error to show).
 */
function ScoreSection({ connectionId, revision }: { connectionId: string; revision: number }) {
  const [sets, setSets] = useState<BenchmarkSet[] | null>(null)
  const [setId, setSetId] = useState('')
  const [configs, setConfigs] = useState<LlmConfig[]>([])
  const [configId, setConfigId] = useState('')
  const [queuing, setQueuing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const overview = await knowledgeApi.benchmarks(connectionId)
      setSets(overview.sets)
      setSetId((current) => current || overview.sets[0]?.id || '')
    } catch {
      setSets([])
    }
  }, [connectionId])

  useEffect(() => {
    void refresh()
    llmApi
      .list('chat')
      .then((items) => {
        setConfigs(items)
        const reachable = items.find((c) => c.status === 'OK')
        setConfigId((reachable ?? items[0])?.id ?? '')
      })
      .catch(() => setConfigs([]))
  }, [refresh])

  const set = sets?.find((s) => s.id === setId) ?? null
  const score = set ? draftScore(set, revision) : null
  const running = score?.state === 'running'

  // While the run is in flight, ask again every few seconds. A benchmark is
  // minutes; the dialog may be closed long before it ends, and nothing is lost
  // — the run is stored and read back the next time the dialog opens.
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => void refresh(), 4000)
    return () => clearInterval(timer)
  }, [running, refresh])

  if (sets === null || sets.length === 0 || !set) return null

  const questions = set.template_ids.length
  const config = configs.find((c) => c.id === configId)

  async function score_() {
    setQueuing(true)
    setError(null)
    try {
      await knowledgeApi.runBenchmark(connectionId, setId, {
        semanticSource: 'DRAFT', llmConfigId: configId || undefined,
      })
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start scoring this draft.')
    } finally {
      setQueuing(false)
    }
  }

  return (
    <section
      style={{
        border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px',
        display: 'flex', flexDirection: 'column', gap: 10,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ color: 'var(--accent)', display: 'flex' }}><Icon.Sparkle size={14} /></span>
        <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-strong)', flex: 1 }}>
          Score this draft
        </span>
        <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>advisory</span>
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        {sets.length > 1 && (
          <Select
            value={setId}
            onChange={(e) => setSetId(e.target.value)}
            aria-label="Benchmark set"
            style={{ width: 'auto', padding: '5px 8px', fontSize: 12 }}
          >
            {sets.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </Select>
        )}
        {configs.length > 1 && (
          <Select
            value={configId}
            onChange={(e) => setConfigId(e.target.value)}
            aria-label="Model to score with"
            style={{ width: 'auto', padding: '5px 8px', fontSize: 12 }}
          >
            {configs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
        )}
        <span style={{ flex: 1, minWidth: 160, fontSize: 12, color: 'var(--text-dim)' }}>
          {questions} {questions === 1 ? 'question' : 'questions'}
          {config ? ` on ${config.name}` : ''} — one model call or more each, billed by your provider.
        </span>
        <GhostButton
          onClick={score_}
          disabled={queuing || running || !configId}
          style={{ padding: '6px 11px', fontSize: 12 }}
        >
          {(queuing || running) && <Spinner size={12} />}
          {running ? 'Scoring…' : score?.state === 'scored' ? 'Score again' : 'Score'}
        </GhostButton>
      </div>

      {score?.state === 'none' && score.earlier && (
        <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
          The last score was of an earlier draft. This one has not been scored.
        </span>
      )}
      {score?.state === 'failed' && <ErrorNote>{score.message}</ErrorNote>}
      {score?.state === 'scored' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 13, color: 'var(--text-strong)', fontVariantNumeric: 'tabular-nums' }}>
            Held-out <strong>{score.draft === null ? '—' : `${score.draft}%`}</strong> (draft)
            {' · '}
            {score.published === null ? '— (published)' : `${score.published}% (published)`}
            {score.delta !== null && (
              <span style={{ color: 'var(--text-dim)' }}>{` · ${deltaWords(score.delta)}`}</span>
            )}
          </span>
          {score.reason && (
            <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
              Not compared: {score.reason}
            </span>
          )}
          {set.draft_run && (
            <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
              {modelName(set.draft_run.model_snapshot)} · prompts {set.draft_run.prompt_version}
            </span>
          )}
        </div>
      )}
      {error && <ErrorNote>{error}</ErrorNote>}
    </section>
  )
}
