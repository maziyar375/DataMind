/**
 * LLM providers: the models DataMind may call, and the keys it calls them with.
 *
 * Deliberately the same screen as Data sources — the same `MasterColumn` /
 * `DetailHeader` / `Section` / `FieldRow` furniture from
 * `components/settings.tsx`, the same test-before-save bargain, the same
 * status vocabulary. Two pages that configure a credential and probe it should
 * not each invent their own shape; where this one parts company is stated
 * below and nowhere else.
 *
 * **Test probes the form, not the saved row**, exactly as connections do:
 * while creating, or while the form is dirty, `testDraft` takes the typed
 * values and persists nothing, so a key can be checked before a broken row
 * exists. A clean row's test hits the stored config and records what it found
 * — the probe is a real completion, so what comes back is the model's actual
 * capabilities rather than a reachability ping.
 *
 * **The provider list is not open.** The picker maps over `PROVIDER_URLS` in
 * `theme/tokens.ts`, which holds exactly two keys — `OpenAI-compatible` (which
 * covers OpenAI itself and anything speaking its API: OpenRouter, Ollama,
 * vLLM, a local gateway) and `Anthropic`. Removing a key removes the choice;
 * adding one requires the gateway to know what to do with it. A legacy
 * `"Custom"` value still exists on rows created before it was dropped and is
 * handled in `litellm_gateway.py`, but nothing creates one now.
 *
 * The one departure from the master–detail pages' visual rules is
 * `PROVIDER_HUES` below: the colour is keyed on the provider rather than on
 * the record, because three models behind one endpoint are a family and should
 * look like one.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMatch, useNavigate } from 'react-router-dom'
import { llmConfigs as api } from '../api/client'
import type { LlmConfig, TestResult } from '../api/types'
import {
  Chip, DangerButton, EmptyState, ErrorNote, Field, GhostButton, GlyphBadge, Icon,
  PrimaryButton, Select, Spinner, TextInput, identityHue, relativeTime,
} from '../components/ui'
import {
  DetailBody, DetailHeader, FieldRow, MasterColumn, MasterItem, Section,
  StatusLine, UnsavedNote,
} from '../components/settings'
import { ListScrim, ListToggle, useListDrawer } from '../components/list-drawer'
import { PROVIDER_URLS } from '../theme/tokens'
import { useUnsavedWork } from '../shell'

const BLANK = {
  name: 'New model',
  provider: 'OpenAI-compatible',
  base_url: PROVIDER_URLS['OpenAI-compatible'],
  model: 'gpt-4o-mini',
  temperature: 0,
  max_tokens: 2048,
}

/**
 * A hue per *provider*, not per row.
 *
 * Three models behind one endpoint are a family and should look like one, so
 * the colour is keyed on the provider rather than on the config id — which is
 * the one place this page departs from the dashboard cards' per-record hue.
 * Anything not in the map falls back to a stable hue for its name.
 */
const PROVIDER_HUES: Record<string, number> = {
  'OpenAI-compatible': 160,
  Anthropic: 25,
}

function providerHue(provider: string): number {
  return PROVIDER_HUES[provider] ?? identityHue(provider)
}

/** What a stored row's `status` says, in the words the chips and dots use. */
function reachability(status: string): { tone: 'green' | 'red' | 'neutral'; label: string } {
  if (status === 'OK') return { tone: 'green', label: 'Reachable' }
  if (status === 'ERROR') return { tone: 'red', label: 'Unreachable' }
  return { tone: 'neutral', label: 'Untested' }
}

export default function LlmProvidersPage() {
  const [list, setList] = useState<LlmConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  // The open provider is the URL, on the same terms as Data sources — the two
  // pages are meant to be one screen with different fields, and that includes
  // how they are addressed. `/providers/new` is the create form.
  const navigate = useNavigate()
  // Below 700px the index is an overlay; above it this does nothing.
  const listDrawer = useListDrawer()
  const routeId = useMatch('/providers/:id')?.params.id ?? null
  const creating = routeId === 'new'
  const selectedId = creating ? null : routeId
  const setSelectedId = useCallback(
    (id: string | null, { replace = false } = {}) =>
      navigate(id ? `/providers/${id}` : '/providers', { replace }),
    [navigate],
  )
  const [draft, setDraft] = useState<Record<string, any>>(BLANK)
  const [apiKey, setApiKey] = useState('')
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
  //
  // One check serves both questions here — "is there anything to save?" and
  // "must a probe use the form rather than the stored row?" — because every
  // field on this form is sent to the probe. (Data sources needs two: its row
  // cap and disclosure policy change nothing about a probe.)
  //
  // Derived from the draft's own keys rather than a written-out list, because
  // a hand-maintained one silently omits every field added after it.
  const isDirty = useMemo(() => {
    if (!selected) return false
    if (apiKey !== '') return true
    const saved = selected as unknown as Record<string, unknown>
    return Object.keys(draft).some((key) => {
      // `base_url` hydrates a null as '' (the input has no null state), so
      // compare through the same lens or a just-opened form reads as edited.
      const a = key === 'base_url' ? (draft[key] || '') : draft[key]
      const b = key === 'base_url' ? (saved[key] ?? '') : saved[key]
      if (typeof b === 'number') return Number(a) !== b
      return (a ?? null) !== (b ?? null)
    })
  }, [selected, draft, apiKey])

  // The same rule Data sources follows: a create form counts as unsaved work
  // only once something has been typed into it.
  const touchedNew = useMemo(
    () =>
      creating
      && (apiKey !== ''
        || Object.keys(BLANK).some(
          (key) => draft[key] !== (BLANK as Record<string, unknown>)[key],
        )),
    [creating, draft, apiKey],
  )
  const releaseUnsaved = useUnsavedWork(
    'provider-form',
    creating
      ? (touchedNew ? 'This new model has not been saved yet.' : null)
      : (isDirty
        ? `Your changes to “${selected?.name ?? 'this model'}” have not been saved.`
        : null),
  )

  const refresh = useCallback(async () => {
    const items = await api.list()
    setList(items)
    return items
  }, [])

  useEffect(() => {
    refresh()
      .then((items) => {
        // Landing on `/providers` opens the first one, as this screen has
        // always done — as a redirect, so the address says which.
        if (!routeId && items.length > 0) setSelectedId(items[0].id, { replace: true })
      })
      .catch(() => setError('Could not load your model configurations.'))
      .finally(() => setLoading(false))
    // Once, on mount: this is the landing redirect, not a reaction to the URL.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!selected) return
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
    setDraft(BLANK)
    setApiKey('')
    setTestResult(null)
    setError(null)
    navigate('/providers/new')
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
        // Let go before navigating, or the guard stops a saved form leaving
        // itself; replace, because `/providers/new` no longer describes it.
        releaseUnsaved()
        setSelectedId(created.id, { replace: true })
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
    // Same reason as the data-source delete: a rejection here reached nobody,
    // so a refused delete was indistinguishable from one that did nothing.
    try {
      await api.remove(selected.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete this configuration.')
      return
    }
    // Only once the row is actually gone: a refused delete leaves the form,
    // and its edits, where they were.
    releaseUnsaved()
    setError(null)
    const items = await refresh()
    setSelectedId(items[0]?.id ?? null, { replace: true })
  }

  const editing = creating || !!selected

  // Local/custom endpoints need no key, so only the model is required
  // before a draft probe can say anything useful. Editing an unsaved
  // change takes the same draft path, so it needs a model name too.
  const canTest = creating || isDirty ? Boolean(draft.model) : true

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return list
    return list.filter(
      (config) =>
        config.name.toLowerCase().includes(needle)
        || config.model.toLowerCase().includes(needle)
        || config.provider.toLowerCase().includes(needle),
    )
  }, [list, query])

  return (
    <div style={{ display: 'flex', height: '100%', width: '100%', minWidth: 0 }}>
      <ListScrim open={listDrawer.open} onClick={listDrawer.close} />
      <MasterColumn
        title="LLM providers"
        open={listDrawer.open}
        icon={<Icon.Sparkle size={15} />}
        note="Yours only — models are not shared with your team."
        count={list.length}
        loading={loading}
        query={query}
        onQuery={setQuery}
        onNew={startCreate}
        newLabel="Add a model"
        // "No models configured yet" implied a workspace someone else had
        // configured. They are per-account: a second person's first visit is
        // an empty list because it is theirs, not because something is
        // missing from it.
        empty="You have not added a model yet. Providers belong to the account that made them, so a colleague's will not show up here."
      >
        {visible.map((config) => {
          const state = reachability(config.status)
          return (
            <MasterItem
              key={config.id}
              title={config.name}
              subtitle={config.model}
              active={config.id === selectedId}
              tone={state.tone}
              toneLabel={state.label}
              glyph={
                <GlyphBadge size={30} hue={providerHue(config.provider)}>
                  <Icon.Sparkle size={15} />
                </GlyphBadge>
              }
              onClick={() => setSelectedId(config.id)}
            />
          )
        })}
        {visible.length === 0 && list.length > 0 && (
          <p style={{ fontSize: 12.5, color: 'var(--text-dim)', padding: '4px 6px', margin: 0 }}>
            Nothing matches “{query.trim()}”.
          </p>
        )}
      </MasterColumn>

      <div
        className="rm-detail-pane"
        style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}
      >
        {!editing ? (
          // Centred in the pane rather than pinned to the top of it: with no
          // record open the pane has nothing else in it, and an invitation
          // hanging from the ceiling of an empty room reads as a page that
          // failed to load.
          <div
            className="rm-emptyfield"
            style={{ flex: 1, display: 'grid', placeItems: 'center' }}
          >
            <EmptyState
              icon={<Icon.Sparkle size={20} />}
              title="Connect a model"
              body="DataMind works with any OpenAI-compatible endpoint (including local servers like Ollama) or Anthropic. Testing a model records what it can actually do. The key you add is yours — each person adds their own."
              action={<PrimaryButton onClick={startCreate}>Add a model</PrimaryButton>}
            />
          </div>
        ) : (
          <>
            <DetailHeader
              leading={
                <ListToggle open={listDrawer.open} label="LLM providers" onClick={listDrawer.toggle} />
              }
              glyph={
                <GlyphBadge size={40} hue={providerHue(draft.provider)}>
                  <Icon.Sparkle size={19} />
                </GlyphBadge>
              }
              title={creating ? 'New model' : selected!.name}
              subtitle={`${draft.provider} · ${draft.model}`}
              chips={
                creating ? undefined : (
                  <>
                    <Chip tone={reachability(selected!.status).tone}>
                      {reachability(selected!.status).label}
                    </Chip>
                    <Chip tone={selected!.has_api_key ? 'green' : 'amber'}>
                      {selected!.has_api_key ? 'Key stored' : 'No key'}
                    </Chip>
                    {selected!.last_tested_at && (
                      <Chip>Tested {relativeTime(selected!.last_tested_at)}</Chip>
                    )}
                  </>
                )
              }
              actions={
                <>
                  {!creating && isDirty && <UnsavedNote />}
                  <GhostButton
                    onClick={test}
                    disabled={testing || !canTest}
                    title={canTest ? undefined : 'Enter a model name first.'}
                  >
                    {testing ? <Spinner /> : <Icon.Zap size={14} />}
                    Test model
                  </GhostButton>
                  <PrimaryButton
                    onClick={save}
                    disabled={saving || !(creating || isDirty)}
                    title={creating || isDirty ? undefined : 'No changes to save.'}
                  >
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
                icon={<Icon.Link size={14} />}
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
                    draft.provider === 'OpenAI-compatible'
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
                icon={<Icon.Key size={14} />}
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
                    placeholder={selected?.has_api_key ? '••••••••' : 'e.g. sk-…'}
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                  />
                </Field>
              </Section>

              <Section
                title="Generation"
                description="Applied to every request this model serves."
                icon={<Icon.Sliders size={14} />}
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
                  <Section title="How testing works" icon={<Icon.Zap size={14} />}>
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
                    icon={<Icon.Alert size={14} />}
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
