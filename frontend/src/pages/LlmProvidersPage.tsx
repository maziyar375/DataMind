import { useCallback, useEffect, useMemo, useState } from 'react'
import { llmConfigs as api } from '../api/client'
import type { LlmConfig, TestResult } from '../api/types'
import {
  Chip, DangerButton, EmptyState, ErrorNote, Field, GhostButton, Icon,
  PrimaryButton, Select, Spinner, TextInput,
} from '../components/ui'
import {
  DetailBody, DetailHeader, FieldRow, MasterColumn, MasterItem, Section,
  StatusLine,
} from '../components/settings'
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

  // True when the form holds edits not yet saved to `selected`. A new key
  // counts, since a blank key field means "keep the stored one" — see test().
  const isDirty = useMemo(() => {
    if (!selected) return false
    return (
      apiKey !== '' ||
      draft.name !== selected.name ||
      draft.provider !== selected.provider ||
      (draft.base_url || '') !== (selected.base_url ?? '') ||
      draft.model !== selected.model ||
      draft.temperature !== selected.temperature ||
      draft.max_tokens !== selected.max_tokens
    )
  }, [selected, draft, apiKey])

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
    setError(null)
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
    setError(null)
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
        const created = await api.create({ ...draft, api_key: apiKey || undefined })
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
    setTesting(true)
    setTestResult(null)
    try {
      if (creating || (selected && isDirty)) {
        // Probe the form values, not the saved row. `config_id` (absent while
        // creating) lets the backend reuse the stored key if none was typed.
        // This never persists, since the form may differ from what is saved.
        setTestResult(
          await api.testDraft({
            config_id: selected?.id,
            provider: draft.provider,
            base_url: draft.base_url || undefined,
            model: draft.model,
            api_key: apiKey || undefined,
            temperature: draft.temperature,
            max_tokens: draft.max_tokens,
          }),
        )
      } else if (selected) {
        // No unsaved edits: test the stored row, which records its capabilities.
        setTestResult(await api.test(selected.id))
        await refresh()
      }
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

  const editing = creating || !!selected

  // Ollama and other local endpoints need no key, so only the model is
  // required before a draft probe can say anything useful. Editing an unsaved
  // change takes the same draft path, so it needs a model name too.
  const canTest = creating || isDirty ? Boolean(draft.model) : true

  return (
    <div style={{ display: 'flex', height: '100%', width: '100%', minWidth: 0 }}>
      <MasterColumn
        title="LLM providers"
        count={list.length}
        onNew={startCreate}
        newLabel="Add a model"
        empty="No models configured yet. Add one to start asking questions."
      >
        {list.map((config) => (
          <MasterItem
            key={config.id}
            title={config.name}
            subtitle={config.model}
            active={config.id === selectedId}
            tone={
              config.status === 'OK' ? 'green' : config.status === 'ERROR' ? 'red' : 'neutral'
            }
            onClick={() => setSelectedId(config.id)}
          />
        ))}
      </MasterColumn>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        {!editing ? (
          <EmptyState
            title="Connect a model"
            body="DataMind works with any OpenAI-compatible endpoint, Anthropic, or a local Ollama server. Testing a model records what it can actually do."
            action={<PrimaryButton onClick={startCreate}>Add a model</PrimaryButton>}
          />
        ) : (
          <>
            <DetailHeader
              title={creating ? 'New model' : selected!.name}
              subtitle={`${draft.provider} · ${draft.model}`}
              chips={
                creating ? undefined : (
                  <>
                    <Chip tone={selected!.status === 'OK' ? 'green' : selected!.status === 'ERROR' ? 'red' : 'neutral'}>
                      {selected!.status === 'OK'
                        ? 'reachable'
                        : selected!.status === 'ERROR'
                          ? 'unreachable'
                          : 'untested'}
                    </Chip>
                    <Chip tone={selected!.has_api_key ? 'green' : 'amber'}>
                      {selected!.has_api_key ? 'key stored' : 'no key'}
                    </Chip>
                    {selected!.last_tested_at && (
                      <Chip>tested {new Date(selected!.last_tested_at).toLocaleString()}</Chip>
                    )}
                  </>
                )
              }
              actions={
                <>
                  <GhostButton
                    onClick={test}
                    disabled={testing || !canTest}
                    title={canTest ? undefined : 'Enter a model name first.'}
                  >
                    {testing && <Spinner />}
                    Test model
                  </GhostButton>
                  <PrimaryButton onClick={save} disabled={saving}>
                    {saving && <Spinner />}
                    {creating ? 'Add model' : 'Save changes'}
                  </PrimaryButton>
                </>
              }
            />

            <DetailBody>
              {error && <ErrorNote>{error}</ErrorNote>}
              {testResult && (
                <StatusLine ok={testResult.ok}>
                  {testResult.ok
                    ? `${testResult.message} · ${testResult.latency_ms}ms`
                    : testResult.message}
                </StatusLine>
              )}

              <Section
                title="Endpoint"
                description="Where DataMind sends completion requests."
              >
                <FieldRow>
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
                </FieldRow>

                <Field label="Base URL">
                  <TextInput
                    value={draft.base_url ?? ''}
                    onChange={(e) => setDraft({ ...draft, base_url: e.target.value })}
                  />
                </Field>

                <Field
                  label="Model"
                  hint={
                    draft.provider === 'OpenAI-compatible' || draft.provider === 'Custom'
                      ? 'If the model name contains a slash (e.g. lightning-ai/gemma-4-31B-it), prefix it with openai/ — openai/lightning-ai/gemma-4-31B-it — or it will not route correctly.'
                      : undefined
                  }
                >
                  <TextInput
                    value={draft.model}
                    onChange={(e) => setDraft({ ...draft, model: e.target.value })}
                  />
                </Field>
              </Section>

              <Section
                title="Credentials"
                description="Stored encrypted with the server's secret box. The API never returns it."
              >
                <Field
                  label="API key"
                  hint={
                    creating
                      ? undefined
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
              </Section>

              <Section
                title="Generation"
                description="Applied to every request this model serves."
              >
                <FieldRow>
                  <Field label="Temperature" hint="0 is deterministic, 2 is wildest.">
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
                  <Field label="Max tokens" hint="Ceiling on a single completion.">
                    <TextInput
                      type="number"
                      value={draft.max_tokens}
                      onChange={(e) =>
                        setDraft({ ...draft, max_tokens: Number(e.target.value) })
                      }
                    />
                  </Field>
                </FieldRow>
              </Section>

              {!creating && (
                <>
                  <Section title="How testing works">
                    <p
                      style={{
                        fontSize: 12.5,
                        color: 'var(--text-dim)',
                        margin: 0,
                        lineHeight: 1.6,
                      }}
                    >
                      Testing sends one short prompt and checks whether the provider
                      accepts a structured-output request. DataMind validates model
                      output on its own side regardless of what a provider claims to
                      support.
                    </p>
                  </Section>

                  <Section
                    title="Danger zone"
                    description="Conversations that already ran on this model keep their recorded snapshot."
                    danger
                  >
                    <DangerButton onClick={remove} style={{ alignSelf: 'flex-start' }}>
                      <Icon.Trash />
                      Delete model
                    </DangerButton>
                  </Section>
                </>
              )}
            </DetailBody>
          </>
        )}
      </div>
    </div>
  )
}
