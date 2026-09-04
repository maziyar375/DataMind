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
 * **The advanced-parameter fields are generated, not written.** The server
 * serves what each provider documents (`GET /llm-configs/parameters`, from
 * `app/domain/value_objects/llm_params.py`) and this page renders an input per
 * entry, choosing the input from the entry's own type. Nothing below names a
 * parameter: adding `prompt_cache_key` to that catalog puts it on this screen
 * with no change here, and a parameter the selected provider does not document
 * cannot be typed at all — which is the half a free JSON box would give away.
 * The translation between what is typed and what is sent lives in
 * `components/provider-params.ts`, tested apart from React.
 *
 * **A row is one thing: a model that answers, or an embedder.** The two are
 * different jobs — one writes an answer, one turns a question into a vector —
 * and a row that quietly did both was the screen's most confusing shape: the
 * Embeddings section sat under every model whether or not that endpoint had
 * anything to do with vectors. So the list is two groups, creating asks which
 * kind, and the form shows that kind's fields and no others. An embedder has
 * no temperature; a model has no embedding section.
 *
 * **They still share this screen**, and that is an architectural decision
 * rather than a placement: an embedding endpoint needs exactly what this form
 * already stores — a provider kind, a base URL and an encrypted key — and
 * `LLMGateway.embed` already takes a resolved provider plus a model *name*. A
 * separate screen would duplicate this form, the key handling and the probe to
 * hold one extra string. What is separated is the *record*, which is what was
 * confusing, not the credential form, which was not.
 *
 * Rows written before this — one row declaring both — still work and are shown
 * in both groups. Nothing rewrites them: `can_chat` and `can_embed` are
 * derived from the two fields, so "what is this for?" stays a question asked
 * of the row rather than a column that can disagree with it.
 *
 * The one departure from the master–detail pages' visual rules is
 * `PROVIDER_HUES` below: the colour is keyed on the provider rather than on
 * the record, because three models behind one endpoint are a family and should
 * look like one.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMatch, useNavigate } from 'react-router-dom'
import { llmConfigs as api } from '../api/client'
import type { LlmConfig, ParameterCatalog, TestResult } from '../api/types'
import {
  Chip, DangerButton, EmptyState, ErrorNote, Field, GhostButton, GlyphBadge, Icon,
  PrimaryButton, Segmented, Select, Spinner, TextArea, TextInput, identityHue,
  relativeTime,
} from '../components/ui'
import {
  addable, collectParams, configuredCount, draftsFrom, fieldKind,
  parameterVerdict, sameParams, selectOptions, shown,
} from '../components/provider-params'
import type { ParamDrafts, ParamSpec } from '../components/provider-params'
import {
  DetailBody, DetailHeader, FieldRow, MasterColumn, MasterItem, Section,
  StatusLine, UnsavedNote,
} from '../components/settings'
import { ListScrim, ListToggle, useListDrawer } from '../components/list-drawer'
import { PROVIDER_URLS } from '../theme/tokens'
import { useUnsavedWork } from '../shell'

/** What a row is for. Derived from the two model fields, never stored:
 *  `can_chat` and `can_embed` ask the row the same way on the server, and a
 *  `kind` column would be a third answer able to disagree with both.
 *
 *  `both` is not creatable. It is the shape rows had before the two were
 *  separated, and it is kept readable rather than migrated — a row that
 *  answers *and* embeds is working, and rewriting somebody's provider without
 *  being asked is worse than showing it in two groups. */
type Kind = 'chat' | 'embedding'

function kindOf(config: LlmConfig): Kind | 'both' {
  if (config.model && config.embedding_model) return 'both'
  return config.model ? 'chat' : 'embedding'
}

/** What a new row of each kind starts as.
 *
 *  Both carry a plausible model name for the same reason `gpt-4o-mini` was
 *  always there: the field's *shape* is the hint that matters, and a blank one
 *  makes the Test button the only way to find out what belongs in it.
 *
 *  The embedding model is a plain field on the draft like any other scalar.
 *  The two parameter *maps* are not: they live in their own state, because
 *  what the form holds is text per field and what the API takes is a typed
 *  JSON object, and `provider-params.ts` is the translation. */
function blankDraft(kind: Kind) {
  return {
    name: kind === 'chat' ? 'New model' : 'New embedder',
    provider: 'OpenAI-compatible',
    base_url: PROVIDER_URLS['OpenAI-compatible'],
    model: kind === 'chat' ? 'gpt-4o-mini' : '',
    temperature: 0,
    max_tokens: 2048,
    embedding_model: kind === 'chat' ? '' : 'text-embedding-3-small',
  }
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


/** Faint explanatory prose, as the "How testing works" section already uses. */
const faintNote: React.CSSProperties = {
  fontSize: 12.5, color: 'var(--text-dim)', margin: 0, lineHeight: 1.6,
}

/** A heading over one half of the list, and the button that adds to it.
 *
 *  The list stopped being one thing when a row did: reading forty subtitles to
 *  find which endpoint makes vectors is the work this removes. `count` is on
 *  the heading rather than only on the column, because the number that matters
 *  here is per group — one embedder and nine models is the shape a healthy
 *  deployment has, and a single `10` says none of that.
 */
function GroupHeading({
  label, count, onAdd, addLabel,
}: {
  label: string
  count: number
  onAdd?: () => void
  addLabel?: string
}) {
  return (
    <div
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '12px 8px 4px',
      }}
    >
      <span
        style={{
          fontSize: 10.5, fontWeight: 700, letterSpacing: '0.06em',
          textTransform: 'uppercase', color: 'var(--text-faint)',
        }}
      >
        {label}
      </span>
      <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-faint)' }}>
        {count}
      </span>
      <span style={{ flex: 1, height: 1, background: 'var(--border-subtle)' }} />
      {onAdd && (
        <button
          type="button"
          onClick={onAdd}
          aria-label={addLabel}
          title={addLabel}
          className="rm-icon-btn"
          style={{
            display: 'grid',
            placeItems: 'center',
            width: 22,
            height: 22,
            border: 'none',
            borderRadius: 7,
            background: 'transparent',
            color: 'var(--text-faint)',
            cursor: 'pointer',
            ['--rm-hover-bg' as string]: 'var(--panel-alt)',
          }}
        >
          <Icon.Plus size={13} />
        </button>
      )}
    </div>
  )
}

/** One row in either group. The subtitle is passed in rather than derived,
 *  because a row that answers *and* embeds appears in both and must say the
 *  relevant half in each — the same record read two ways. */
function ProviderItem({
  config, subtitle, active, onClick,
}: {
  config: LlmConfig
  subtitle: string
  active: boolean
  onClick: () => void
}) {
  const state = reachability(config.status)
  return (
    <MasterItem
      title={config.name}
      subtitle={subtitle}
      active={active}
      tone={state.tone}
      toneLabel={state.label}
      glyph={
        <GlyphBadge size={30} hue={providerHue(config.provider)}>
          <Icon.Sparkle size={15} />
        </GlyphBadge>
      }
      onClick={onClick}
    />
  )
}

/** Whether a provider has an embedding endpoint, per the served catalog.
 *
 *  Read from the catalog rather than from a list here, so the answer moves
 *  with the backend — Anthropic's "no" is one fact stated in one place. While
 *  the catalog is unknown the answer is *yes*, because hiding a field on a
 *  loading state would look like the feature was removed. */
function providerEmbeds(
  catalogs: ParameterCatalog[] | null, provider: string,
): boolean {
  const entry = catalogs?.find((c) => c.provider === provider)
  return entry ? entry.embedding_supported : true
}

/**
 * One input per documented parameter, and not one line that names any of them.
 *
 * The input is chosen by `fieldKind` from the spec's own type, the hint is the
 * provider's own description of the parameter, and the placeholder is a valid
 * value — so a catalog entry is enough to produce a usable field. A parameter
 * added to `llm_params.py` appears here on the next deploy with no change to
 * this file, which is the property the whole design is for.
 *
 * Blank is *unset* everywhere, including on the pickers, or a parameter could
 * be switched on and never off again.
 */
function ParameterFields({
  specs, drafts, errors, onChange, onRemove, onAdd, empty,
}: {
  specs: ParamSpec[]
  drafts: ParamDrafts
  errors: Record<string, string>
  onChange: (name: string, value: string) => void
  onRemove: (name: string) => void
  onAdd: (name: string) => void
  empty: string
}) {
  if (specs.length === 0) {
    return <p style={faintNote}>{empty}</p>
  }
  const rows = shown(specs, drafts)
  const offer = addable(specs, drafts)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {rows.map((spec) => {
        const value = drafts[spec.name] ?? ''
        const error = errors[spec.name]
        const kind = fieldKind(spec)
        return (
          <div key={spec.name} style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <Field
                label={spec.name}
                // The provider's own sentence, plus this page's own error
                // under it — an in-page failure stays beside the field that
                // caused it, which is the shell's rule for anything that is
                // not a background job.
                hint={error ? `${spec.summary} · ${error}` : spec.summary}
              >
                {kind === 'select' ? (
                  <Select
                    value={value}
                    onChange={(e) => onChange(spec.name, e.target.value)}
                  >
                    {selectOptions(spec).map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </Select>
                ) : kind === 'textarea' ? (
                  <TextArea
                    rows={2}
                    value={value}
                    placeholder={spec.example}
                    spellCheck={false}
                    // A JSON value is code, and code is always left-to-right —
                    // the same rule SQL follows everywhere in this product.
                    dir="ltr"
                    style={{
                      fontFamily: 'var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)',
                      fontSize: 12,
                      borderColor: error ? 'var(--danger)' : undefined,
                    }}
                    onChange={(e) => onChange(spec.name, e.target.value)}
                  />
                ) : (
                  <TextInput
                    type={kind === 'number' ? 'number' : 'text'}
                    value={value}
                    placeholder={spec.example}
                    min={spec.minimum}
                    max={spec.maximum}
                    step={spec.kind === 'integer' ? 1 : 'any'}
                    dir="ltr"
                    style={{ borderColor: error ? 'var(--danger)' : undefined }}
                    onChange={(e) => onChange(spec.name, e.target.value)}
                  />
                )}
              </Field>
            </div>
            <GhostButton
              onClick={() => onRemove(spec.name)}
              aria-label={`Remove ${spec.name}`}
              title={`Remove ${spec.name}`}
              style={{ marginTop: 20 }}
            >
              <Icon.Trash />
            </GhostButton>
          </div>
        )
      })}

      {offer.length > 0 ? (
        <div style={{ maxWidth: 320 }}>
          <Field
            label={rows.length === 0 ? 'Add a parameter' : 'Add another'}
            hint={
              rows.length === 0
                ? 'Everything this provider documents, and nothing it does not.'
                : undefined
            }
          >
            {/* A picker rather than fourteen always-visible inputs. The
                catalog supplies the names *and* the summaries, so this stays
                generated: a parameter added to `llm_params.py` appears in
                this list on the next deploy. Value resets to '' every time so
                the same parameter can be re-added after a removal. */}
            <Select
              value=""
              onChange={(e) => {
                if (e.target.value) onAdd(e.target.value)
              }}
            >
              <option value="">Choose a parameter…</option>
              {offer.map((spec) => (
                <option key={spec.name} value={spec.name}>
                  {spec.name}
                  {spec.choices?.length
                    ? ` — ${spec.choices.join(' | ')}`
                    : spec.minimum !== undefined || spec.maximum !== undefined
                      ? ` — ${spec.minimum ?? '−∞'} to ${spec.maximum ?? '∞'}`
                      : ` — ${spec.kind.replace('_', ' ')}`}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      ) : (
        <p style={faintNote}>
          Every parameter this provider documents is already on the form.
        </p>
      )}
    </div>
  )
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
  const [draft, setDraft] = useState<Record<string, any>>(blankDraft('chat'))
  // What the open row is for. Set from the row when one is opened and from the
  // button that started a create — the one place it is a choice rather than an
  // observation, because a row that declares nothing yet cannot be asked.
  const [kind, setKind] = useState<Kind | 'both'>('chat')
  const [apiKey, setApiKey] = useState('')
  // What each provider documents, fetched once. `null` means "not known yet",
  // which is **not** the same as "this provider takes nothing": while it is
  // null the two parameter maps are left out of every payload entirely, so a
  // catalog that failed to load cannot silently wipe a saved configuration.
  const [catalogs, setCatalogs] = useState<ParameterCatalog[] | null>(null)
  const [paramDrafts, setParamDrafts] = useState<ParamDrafts>({})
  const [embeddingDrafts, setEmbeddingDrafts] = useState<ParamDrafts>({})
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<TestResult | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const selected = useMemo(
    () => list.find((c) => c.id === selectedId) ?? null,
    [list, selectedId],
  )

  const catalog = useMemo(
    () => catalogs?.find((entry) => entry.provider === draft.provider) ?? null,
    [catalogs, draft.provider],
  )
  const completionSpecs: ParamSpec[] = catalog?.completion ?? []
  const embeddingSpecs: ParamSpec[] = catalog?.embedding ?? []

  // Collected on every render rather than on submit, because the per-field
  // errors are shown under the fields while they are being typed — the same
  // bargain the semantic layer's metric editor makes with its live check.
  const completion = useMemo(
    () => collectParams(completionSpecs, paramDrafts),
    [completionSpecs, paramDrafts],
  )
  const embedding = useMemo(
    () => collectParams(embeddingSpecs, embeddingDrafts),
    [embeddingSpecs, embeddingDrafts],
  )
  const paramErrors
    = Object.keys(completion.errors).length + Object.keys(embedding.errors).length

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
    // The two parameter maps compare by *value*: the drafts are text and the
    // saved row is JSON, so the only honest comparison is of what would
    // actually be sent. While the catalog is unknown they cannot have been
    // edited — there are no fields — so they cannot make a form dirty either.
    if (catalog) {
      if (!sameParams(completion.params, selected.params)) return true
      if (!sameParams(embedding.params, selected.embedding_params)) return true
    }
    const saved = selected as unknown as Record<string, unknown>
    return Object.keys(draft).some((key) => {
      // `base_url` hydrates a null as '' (the input has no null state), so
      // compare through the same lens or a just-opened form reads as edited.
      const a = key === 'base_url' ? (draft[key] || '') : draft[key]
      const b = key === 'base_url' ? (saved[key] ?? '') : saved[key]
      if (typeof b === 'number') return Number(a) !== b
      return (a ?? null) !== (b ?? null)
    })
  }, [selected, draft, apiKey, catalog, completion.params, embedding.params])

  // The blank *this* form started from. Kind-dependent, so switching what a
  // new row is for does not read as unsaved work in every field.
  const blank = useMemo(() => blankDraft(kind === 'both' ? 'chat' : kind), [kind])

  // The same rule Data sources follows: a create form counts as unsaved work
  // only once something has been typed into it.
  const touchedNew = useMemo(
    () =>
      creating
      && (apiKey !== ''
        || configuredCount(paramDrafts) > 0
        || configuredCount(embeddingDrafts) > 0
        || Object.keys(blank).some(
          (key) => draft[key] !== (blank as Record<string, unknown>)[key],
        )),
    [creating, draft, apiKey, paramDrafts, embeddingDrafts, blank],
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
    // Beside the list, not after it: they are two readings of one screen, and
    // a catalog that arrived second would make the parameter fields appear
    // under the reader's cursor. A failure is silent by design — the rest of
    // the form works, and the section simply says the catalog is unavailable.
    api.parameters().then(setCatalogs).catch(() => setCatalogs(null))
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
      embedding_model: selected.embedding_model ?? '',
    })
    // Asked of the row, not remembered from the last one opened.
    setKind(kindOf(selected))
    // Hydrated against the **row's** provider, not the draft's: the draft is
    // being set in this same pass and the specs decide which stored keys have
    // a field to appear in.
    const entry = catalogs?.find((c) => c.provider === selected.provider)
    setParamDrafts(draftsFrom(entry?.completion ?? [], selected.params))
    setEmbeddingDrafts(draftsFrom(entry?.embedding ?? [], selected.embedding_params))
    // Keyed on the **loaded row's** id, not on the id in the URL.
    //
    // Those differ for exactly one arrival, and it is the one routing made
    // possible: a deep link. Typing, bookmarking or refreshing
    // `/{route}/:id` sets the id from the URL at mount, while the list it
    // has to be looked up in is still in flight — so an effect keyed on the
    // URL fired once against a `selected` of `null`, bailed, and never ran
    // again. The form stayed on `BLANK`, showing a blank record, reporting
    // unsaved changes in every field, and arming the navigation guard against
    // edits nobody made. Keyed on the row, it hydrates when the row arrives.
    //
    // Still the *id* rather than the row itself: `selected` is a fresh object
    // on every list refresh, and depending on it would re-hydrate the form —
    // discarding what the user had typed — every time a save reloaded the list.
    // `catalogs` is deliberately in the dependency list beside the row's id:
    // on a deep link the catalog is usually still in flight when the row
    // arrives, and without this the fields would hydrate blank once and never
    // again — the same failure the id-versus-row note above describes.
  }, [selected?.id, catalogs])

  function startCreate(next: Kind = 'chat') {
    setKind(next)
    setDraft(blankDraft(next))
    setParamDrafts({})
    setEmbeddingDrafts({})
    setApiKey('')
    setTestResult(null)
    setError(null)
    navigate('/providers/new')
  }

  /** Switch what a *new* row is for, keeping what has been typed that both
   *  kinds share — the name, the endpoint, the key.
   *
   *  Offered while creating only. Turning a saved model into an embedder means
   *  clearing the model every run of every question points at, and a segmented
   *  control is far too quiet a place for that: the row is deleted and
   *  replaced instead, which is one confirmation and no ambiguity. */
  function changeKind(next: Kind) {
    const fresh = blankDraft(next)
    setDraft({
      ...draft,
      // The name follows the kind only while it is still the default one.
      name: draft.name === blank.name ? fresh.name : draft.name,
      model: fresh.model,
      embedding_model: fresh.embedding_model,
    })
    setParamDrafts({})
    setEmbeddingDrafts({})
    setKind(next)
  }

  function changeProvider(provider: string) {
    setDraft({
      ...draft,
      provider,
      base_url: PROVIDER_URLS[provider] ?? draft.base_url,
      // Anthropic serves no embedding endpoint, and the server refuses a row
      // that claims otherwise. Clearing it here means the refusal is never
      // seen rather than being explained.
      embedding_model: providerEmbeds(catalogs, provider) ? draft.embedding_model : '',
    })
    // The parameters are the *other* provider's vocabulary — `seed` means
    // nothing to Anthropic and `top_k` nothing to OpenAI — so switching drops
    // them rather than carrying them to a save the server will refuse. The
    // fields under the picker change with it, which is the whole point of a
    // generated form.
    setParamDrafts({})
    setEmbeddingDrafts({})
  }

  /** The two maps, or nothing at all while the catalog is unknown.
   *
   *  Omitted rather than sent empty: a PATCH that leaves them out keeps what
   *  is stored, and a PATCH carrying `{}` clears it. Those are different
   *  intentions and only one of them is ever meant here. */
  function parameterPayload(): Record<string, unknown> {
    if (!catalog) return {}
    return { params: completion.params, embedding_params: embedding.params }
  }

  /** The half of the row this kind does not use, cleared on the way out.
   *
   *  The separation has to reach the wire or it is only a hidden field: a form
   *  that stops *showing* the embedding model while still posting the one it
   *  was hydrated with produces exactly the dual row this screen no longer
   *  offers to make. A legacy `both` row sends neither and stays as it is. */
  function kindPayload(): Record<string, unknown> {
    if (kind === 'chat') return { embedding_model: '', embedding_params: {} }
    if (kind === 'embedding') return { model: '', params: {} }
    return {}
  }

  async function save() {
    if (paramErrors > 0) {
      setError('Some parameters are not valid yet — see the fields below.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      if (creating) {
        const created = await api.create({
          ...draft, ...parameterPayload(), ...kindPayload(),
          api_key: apiKey || undefined,
        })
        await refresh()
        // Let go before navigating, or the guard stops a saved form leaving
        // itself; replace, because `/providers/new` no longer describes it.
        releaseUnsaved()
        setSelectedId(created.id, { replace: true })
      } else if (selected) {
        const payload: Record<string, unknown> = {
          ...draft, ...parameterPayload(), ...kindPayload(),
        }
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
            // The probe tests the form, parameters included — otherwise a
            // configuration could pass its test and fail on the first real
            // question because of a value nobody probed.
            ...parameterPayload(),
            embedding_model: draft.embedding_model || '',
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

  // A row declares the model its kind is *for*, and a probe asks for whichever
  // that is: an embedder is tested by asking it for one vector, which is the
  // whole of what it claims to do. A legacy `both` row needs either.
  const declares =
    kind === 'chat' ? Boolean(draft.model)
      : kind === 'embedding' ? Boolean(draft.embedding_model)
        : Boolean(draft.model || draft.embedding_model)
  const canTest = creating || isDirty ? declares : true
  const embeds = kind !== 'chat'
  const answers = kind !== 'embedding'

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return list
    return list.filter(
      (config) =>
        config.name.toLowerCase().includes(needle)
        || config.model.toLowerCase().includes(needle)
        || config.embedding_model.toLowerCase().includes(needle)
        || config.provider.toLowerCase().includes(needle),
    )
  }, [list, query])

  // The two groups the list is read in. A legacy row that does both appears in
  // both, which is the honest place for it: it really is the answer to both
  // questions, and hiding it under one would make that list look wrong.
  const models = useMemo(() => visible.filter((c) => c.model), [visible])
  const embedders = useMemo(() => visible.filter((c) => c.embedding_model), [visible])

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
        onNew={() => startCreate('chat')}
        newLabel="Add a model"
        // "No models configured yet" implied a workspace someone else had
        // configured. They are per-account: a second person's first visit is
        // an empty list because it is theirs, not because something is
        // missing from it.
        empty="You have not added a model yet. Providers belong to the account that made them, so a colleague's will not show up here."
      >
        {/* Two groups, because they are two jobs. The heading is what makes
            "which of these makes vectors?" a glance rather than a reading of
            every subtitle — and the empty Embedder group is the screen's
            clearest statement that word matching is what Knowledge is doing. */}
        {visible.length > 0 && (
          <>
            <GroupHeading label="Models" count={models.length} />
            {models.map((config) => (
              <ProviderItem
                key={`chat-${config.id}`}
                config={config}
                subtitle={config.model}
                active={config.id === selectedId}
                onClick={() => setSelectedId(config.id)}
              />
            ))}
            <GroupHeading
              label="Embedder"
              count={embedders.length}
              onAdd={() => startCreate('embedding')}
              addLabel="Add an embedder"
            />
            {embedders.map((config) => (
              <ProviderItem
                key={`embed-${config.id}`}
                config={config}
                subtitle={`${config.embedding_model} · embeddings`}
                active={config.id === selectedId}
                onClick={() => setSelectedId(config.id)}
              />
            ))}
            {embedders.length === 0 && (
              <p style={{ ...faintNote, fontSize: 11.5, padding: '2px 8px 4px' }}>
                None yet. Knowledge matches questions on the words they share
                until one is added.
              </p>
            )}
          </>
        )}
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
              body="DataMind works with any OpenAI-compatible endpoint (including local servers like Ollama) or Anthropic. A model answers questions; an embedder makes the vectors Knowledge matches with. They are separate rows, and one embedder serves the whole app. The key you add is yours — each person adds their own."
              action={
                <>
                  <PrimaryButton onClick={() => startCreate('chat')}>
                    Add a model
                  </PrimaryButton>
                  <GhostButton onClick={() => startCreate('embedding')}>
                    Add an embedder
                  </GhostButton>
                </>
              }
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
              title={
                creating
                  ? (kind === 'embedding' ? 'New embedder' : 'New model')
                  : selected!.name
              }
              subtitle={`${draft.provider} · ${
                draft.model || `${draft.embedding_model} (embeddings)` || '—'
              }`}
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
                    title={
                      canTest
                        ? undefined
                        : embeds && !answers
                          ? 'Enter an embedding model first.'
                          : 'Enter a model name first.'
                    }
                  >
                    {testing ? <Spinner /> : <Icon.Zap size={14} />}
                    {embeds && !answers ? 'Test embedder' : 'Test model'}
                  </GhostButton>
                  <PrimaryButton
                    onClick={save}
                    disabled={saving || !(creating || isDirty) || !declares}
                    title={
                      !declares
                        ? embeds && !answers
                          ? 'Give this embedder a model to make vectors with.'
                          : 'Give this provider a model to answer with.'
                        : creating || isDirty ? undefined : 'No changes to save.'
                    }
                  >
                    {saving && <Spinner />}
                    {creating
                      ? (kind === 'embedding' ? 'Add embedder' : 'Add model')
                      : 'Save changes'}
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
              {testResult?.ok
                && parameterVerdict(
                  draft.model, testResult.applied_params, testResult.dropped_params,
                ) && (
                // Separate from the reachability line because it answers a
                // different question, and it is the question the form cannot
                // answer on its own: an unsupported parameter is dropped in
                // silence on a real request, so this is the only place a
                // configuration is told it is claiming something untrue.
                <StatusLine ok={(testResult.dropped_params ?? []).length === 0}>
                  {parameterVerdict(
                    draft.model, testResult.applied_params, testResult.dropped_params,
                  )}
                </StatusLine>
              )}
              {testResult?.embedding && (
                <StatusLine ok={testResult.embedding.ok}>
                  {testResult.embedding.ok
                    ? `Embeddings: ${testResult.embedding.model} answered at `
                      + `${testResult.embedding.dimension} dimensions.`
                    : testResult.embedding.message}
                </StatusLine>
              )}

              {creating && (
                // Asked once, at the top, before anything below it makes
                // sense: the fields under this differ by kind, and a form that
                // reshapes itself after the endpoint has been typed is a form
                // that made the reader guess.
                <Section
                  title="What is this for?"
                  description="A row does one job. Answering is what runs a question; embedding is what lets Knowledge match questions that mean the same thing in different words."
                  icon={<Icon.Sparkle size={14} />}
                >
                  <Segmented
                    ariaLabel="What this provider is for"
                    value={kind === 'both' ? 'chat' : kind}
                    onChange={changeKind}
                    options={[
                      { value: 'chat', label: 'Answering questions' },
                      { value: 'embedding', label: 'Making vectors' },
                    ]}
                  />
                </Section>
              )}

              {!creating && kind === 'both' && (
                // The shape rows had before the two were separated. Named
                // rather than migrated: it works, and rewriting somebody's
                // provider unasked is worse than one sentence.
                <Section
                  title="This row does both"
                  description="It answers questions and makes vectors — the shape rows had before the two were separated. Nothing is wrong with it. To split them, add an embedder in the list and then clear the embedding model here; the key is stored encrypted per row, so it has to be typed once on the new one."
                  icon={<Icon.Sparkle size={14} />}
                >
                  <p style={faintNote}>
                    Clearing the embedding model here while a store is indexed
                    with it leaves that store on word matching until embedding
                    search is switched on again in Knowledge.
                  </p>
                </Section>
              )}

              <Section
                title="Endpoint"
                description={
                  answers
                    ? 'Where DataMind sends completion requests.'
                    : 'Where DataMind asks for vectors.'
                }
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
                      {/* An embedder can only be a provider that embeds, so
                          Anthropic is not offered at all rather than offered
                          and then refused on save. */}
                      {Object.keys(PROVIDER_URLS)
                        .filter((provider) =>
                          answers || providerEmbeds(catalogs, provider))
                        .map((provider) => (
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

                {answers && (
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
                )}
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

              {/* Both of these shape a *completion*, so an embedder has
                  nothing to apply them to. Gated on the row's kind rather than
                  on whether the model field happens to be filled in, or the
                  form would rearrange itself under the cursor of anyone
                  clearing that field to retype it. */}
              {answers ? (
                <>
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

              <Section
                title={
                  configuredCount(paramDrafts) > 0
                    ? `Advanced parameters · ${configuredCount(paramDrafts)} set`
                    : 'Advanced parameters'
                }
                description={
                  catalog
                    ? `Sent on every request this model serves, exactly as ${draft.provider} documents them. Nothing is set unless you add it.`
                    : 'The parameter list is loading. Nothing here is changed while it is unavailable.'
                }
                icon={<Icon.Sliders size={14} />}
              >
                <ParameterFields
                  specs={completionSpecs}
                  drafts={paramDrafts}
                  errors={completion.errors}
                  onChange={(name, value) =>
                    setParamDrafts({ ...paramDrafts, [name]: value })}
                  onAdd={(name) => setParamDrafts({ ...paramDrafts, [name]: '' })}
                  onRemove={(name) => {
                    const { [name]: _removed, ...rest } = paramDrafts
                    setParamDrafts(rest)
                  }}
                  empty={
                    catalog
                      ? `${draft.provider} takes no further request parameters here.`
                      : 'Could not load what this provider documents.'
                  }
                />
              </Section>
                </>
              ) : null}

              {embeds && (
              <Section
                title="Embeddings"
                description="One embedder serves the whole app: Knowledge uses it to match questions that mean the same thing in different words, and each store records the model and width it was indexed with."
                icon={<Icon.Sparkle size={14} />}
              >
                {catalog && !catalog.embedding_supported ? (
                  <p style={faintNote}>
                    {draft.provider} has no embedding endpoint, so it cannot serve
                    vectors. Point an OpenAI-compatible provider at one — Knowledge
                    falls back to word matching either way, which needs no provider
                    at all.
                  </p>
                ) : (
                  <>
                    <Field
                      label="Embedding model"
                      hint="Asked of the same endpoint and the same key. The width is measured from the reply, never typed."
                    >
                      <TextInput
                        value={draft.embedding_model ?? ''}
                        placeholder="e.g. text-embedding-3-small"
                        onChange={(e) =>
                          setDraft({ ...draft, embedding_model: e.target.value })}
                      />
                    </Field>
                    {draft.embedding_model ? (
                      <ParameterFields
                        specs={embeddingSpecs}
                        drafts={embeddingDrafts}
                        errors={embedding.errors}
                        onChange={(name, value) =>
                          setEmbeddingDrafts({ ...embeddingDrafts, [name]: value })}
                        onAdd={(name) =>
                          setEmbeddingDrafts({ ...embeddingDrafts, [name]: '' })}
                        onRemove={(name) => {
                          const { [name]: _removed, ...rest } = embeddingDrafts
                          setEmbeddingDrafts(rest)
                        }}
                        empty="This provider takes no further embedding parameters."
                      />
                    ) : null}
                  </>
                )}
              </Section>
              )}

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
