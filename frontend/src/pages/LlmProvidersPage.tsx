import { useCallback, useEffect, useMemo, useState } from 'react'
import { llmConfigs as api } from '../api/client'
import type { LlmConfig, TestResult } from '../api/types'
import {
  Chip, DangerButton, Dot, EmptyState, ErrorNote, Field, GhostButton, Icon,
  PrimaryButton, Select, Spinner, TextInput,
} from '../components/ui'
import { PROVIDER_URLS } from '../theme/tokens'

const BLANK = {
  name: 'New model',
  provider: 'OpenAI-compatible',
  base_url: PROVIDER_URLS['OpenAI-compatible'],
  model: 'gpt-4o-mini',
  temperature: 0.2,
  max_tokens: 2048,
}

export default function LlmProvidersPage() {
  const [list, setList] = useState<LlmConfig[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<Record<string, any>>(BLANK)
  const [apiKey, setApiKey] = useState('')
  const [creating, setCreating] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<TestResult | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const selected = useMemo(
    () => list.find((c) => c.id === selectedId) ?? null,
    [list, selectedId],
  )

  const refresh = useCallback(async () => {
    const items = await api.list()
    setList(items)
    return items
  }, [])

  useEffect(() => {
    refresh()
      .then((items) => {
        if (items.length > 0) setSelectedId(items[0].id)
      })
      .catch(() => setError('Could not load your model configurations.'))
  }, [refresh])

  useEffect(() => {
    if (!selected) return
    setCreating(false)
    setApiKey('')
    setTestResult(null)
    setDraft({
      name: selected.name,
      provider: selected.provider,
      base_url: selected.base_url ?? '',
      model: selected.model,
      temperature: selected.temperature,
      max_tokens: selected.max_tokens,
    })
  }, [selectedId])

  function startCreate() {
    setCreating(true)
    setSelectedId(null)
    setDraft(BLANK)
    setApiKey('')
    setTestResult(null)
  }

  function changeProvider(provider: string) {
    setDraft({
      ...draft,
      provider,
      base_url: PROVIDER_URLS[provider] ?? draft.base_url,
    })
  }

  async function save() {
    setSaving(true)
    setError(null)
    try {
      if (creating) {
        const created = await api.create({
          ...draft,
          api_key: apiKey || undefined,
        })
        await refresh()
        setSelectedId(created.id)
        setCreating(false)
      } else if (selected) {
        const payload: Record<string, unknown> = { ...draft }
        if (apiKey) payload.api_key = apiKey
        await api.update(selected.id, payload)
        await refresh()
        setApiKey('')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save this model.')
    } finally {
      setSaving(false)
    }
  }

  async function test() {
    if (!selected) return
    setTesting(true)
    setTestResult(null)
    try {
      setTestResult(await api.test(selected.id))
      await refresh()
    } catch (err) {
      setTestResult({
        ok: false,
        latency_ms: 0,
        message: err instanceof Error ? err.message : 'Test failed.',
      })
    } finally {
      setTesting(false)
    }
  }

  async function remove() {
    if (!selected) return
    await api.remove(selected.id)
    const items = await refresh()
    setSelectedId(items[0]?.id ?? null)
  }

  return (
    <div style={{ display: 'flex', height: '100%', width: '100%', minWidth: 0 }}>
      <div
        style={{
          width: 380,
          flexShrink: 0,
          overflowY: 'auto',
          padding: 28,
          borderRight: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
          gap: 18,
        }}
      >
        <div
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
        >
          <div style={{ fontSize: 16, fontWeight: 700 }}>LLM providers</div>
          <button
            onClick={startCreate}
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: 'var(--accent)',
              background: 'var(--accent-bg)',
              border: '1px solid var(--accent-border)',
              padding: '5px 10px',
              borderRadius: 6,
              cursor: 'pointer',
            }}
          >
            + New
          </button>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {list.map((config) => {
            const active = config.id === selectedId
            return (
              <button
                key={config.id}
                onClick={() => setSelectedId(config.id)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '10px 12px',
                  borderRadius: 9,
                  cursor: 'pointer',
                  textAlign: 'left',
                  background: active ? 'var(--panel-hover)' : 'var(--panel)',
                  border: `1px solid ${active ? 'var(--accent-border)' : 'var(--border)'}`,
                }}
              >
                <span style={{ fontSize: 13, fontWeight: 600 }}>{config.name}</span>
                <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
                  {config.model}
                </span>
                {config.is_default && (
                  <span style={{ marginLeft: 'auto' }}>
                    <Chip tone="green">Default</Chip>
                  </span>
                )}
              </button>
            )
          })}
          {list.length === 0 && !creating && (
            <div style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
              No models configured yet.
            </div>
          )}
        </div>

        {(selected || creating) && (
          <div
            style={{
              borderTop: '1px solid var(--border)',
              paddingTop: 16,
              display: 'flex',
              flexDirection: 'column',
              gap: 14,
            }}
          >
            {error && <ErrorNote>{error}</ErrorNote>}

            <Field label="Name">
              <TextInput
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </Field>

            <Field label="Provider">
              <Select
                value={draft.provider}
                onChange={(e) => changeProvider(e.target.value)}
              >
                {Object.keys(PROVIDER_URLS).map((provider) => (
                  <option key={provider} value={provider}>
                    {provider}
                  </option>
                ))}
              </Select>
            </Field>

            <Field label="Base URL">
              <TextInput
                value={draft.base_url ?? ''}
                onChange={(e) => setDraft({ ...draft, base_url: e.target.value })}
              />
            </Field>

            <Field label="Model">
              <TextInput
                value={draft.model}
                onChange={(e) => setDraft({ ...draft, model: e.target.value })}
              />
            </Field>

            <Field
              label="API key"
              hint={
                creating
                  ? 'Stored encrypted. It is never returned by the API.'
                  : selected?.has_api_key
                    ? 'A key is stored. Leave blank to keep it.'
                    : 'No key stored yet.'
              }
            >
              <TextInput
                type="password"
                autoComplete="new-password"
                placeholder={selected?.has_api_key ? '••••••••' : 'sk-…'}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </Field>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <Field label="Temperature">
                <TextInput
                  type="number"
                  step="0.1"
                  min="0"
                  max="2"
                  value={draft.temperature}
                  onChange={(e) =>
                    setDraft({ ...draft, temperature: Number(e.target.value) })
                  }
                />
              </Field>
              <Field label="Max tokens">
                <TextInput
                  type="number"
                  value={draft.max_tokens}
                  onChange={(e) =>
                    setDraft({ ...draft, max_tokens: Number(e.target.value) })
                  }
                />
              </Field>
            </div>

            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <PrimaryButton onClick={save} disabled={saving}>
                {saving && <Spinner />}
                {creating ? 'Add model' : 'Save changes'}
              </PrimaryButton>
              {!creating && (
                <GhostButton onClick={test} disabled={testing}>
                  {testing && <Spinner />}
                  Test model
                </GhostButton>
              )}
            </div>

            {testResult && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  fontSize: 12.5,
                  color: testResult.ok ? 'var(--green)' : 'var(--red)',
                }}
              >
                <Dot color={testResult.ok ? 'var(--green)' : 'var(--red)'} />
                {testResult.ok
                  ? `${testResult.message} · ${testResult.latency_ms}ms`
                  : testResult.message}
              </div>
            )}

            {!creating && selected && !selected.is_default && (
              <GhostButton
                onClick={async () => {
                  await api.update(selected.id, { is_default: true })
                  await refresh()
                }}
                style={{
                  alignSelf: 'flex-start',
                  color: 'var(--accent)',
                  borderColor: 'var(--accent-border)',
                }}
              >
                Set as default
              </GhostButton>
            )}

            {!creating && selected && (
              <DangerButton onClick={remove} style={{ alignSelf: 'flex-start' }}>
                <Icon.Trash />
                Delete model
              </DangerButton>
            )}
          </div>
        )}
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: 28, minWidth: 0 }}>
        {!selected && (
          <EmptyState
            title="Connect a model"
            body="Raymand works with any OpenAI-compatible endpoint, Anthropic, or a local Ollama server. Testing a model records what it can actually do."
          />
        )}

        {selected && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18, maxWidth: 640 }}>
            <div>
              <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>
                {selected.name}
              </div>
              <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>
                {selected.provider} · {selected.model}
              </div>
            </div>

            <div
              style={{
                border: '1px solid var(--border)',
                borderRadius: 12,
                background: 'var(--panel)',
                padding: 18,
                display: 'flex',
                flexDirection: 'column',
                gap: 12,
              }}
            >
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: 'var(--text-faint)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                }}
              >
                Detected capabilities
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <Chip tone={selected.status === 'OK' ? 'green' : 'neutral'}>
                  {selected.status === 'OK'
                    ? 'reachable'
                    : selected.status === 'ERROR'
                      ? 'unreachable'
                      : 'untested'}
                </Chip>
                <Chip tone={selected.has_api_key ? 'green' : 'amber'}>
                  {selected.has_api_key ? 'key stored' : 'no key'}
                </Chip>
                {selected.last_tested_at && (
                  <Chip>tested {new Date(selected.last_tested_at).toLocaleString()}</Chip>
                )}
              </div>
              <p style={{ fontSize: 12.5, color: 'var(--text-dim)', margin: 0, lineHeight: 1.5 }}>
                Testing sends one short prompt and checks whether the provider
                accepts a structured-output request. Raymand validates model
                output on its own side regardless of what a provider claims to
                support.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
