# LLM providers, models and parameters

How a provider row is configured, what it may carry, and the three rules that
decide what reaches the wire. This is the **configuration** side; what is
actually *sent* on each call is [llm-calls.md](llm-calls.md).

Code: [`backend/app/infra/llm/litellm_gateway.py`](../../backend/app/infra/llm/litellm_gateway.py)
(the one module allowed to import litellm),
[`backend/app/domain/value_objects/llm_params.py`](../../backend/app/domain/value_objects/llm_params.py)
(the parameter catalog), `services/llm_service.py` and `api/v1/llm_configs.py`.
The form is [`frontend/src/pages/LlmProvidersPage.tsx`](../../frontend/src/pages/LlmProvidersPage.tsx),
generated from `GET /llm-configs/parameters`.

Companion to [security.md](security.md) §6 (how the key is encrypted and why it
is never returned), [knowledge-templates.md](knowledge-templates.md) §7 (which
row embeds) and [../plans/token-accounting.md](../plans/token-accounting.md)
(what a call is billed at).

---

## 1. Two layers, and neither replaced the other

**These two are different layers and neither replaced the other.** LiteLLM is
the *provider adapter* — it turns `ChatMessage[]` into a call to OpenAI,
Anthropic, Ollama or vLLM, and that is all it does. LangGraph is the
*orchestrator* — it decides which node runs next. LangGraph was adopted in the
migration ([../plans/langgraph-migration.md](../plans/langgraph-migration.md));
LiteLLM was never touched by it and is still the only way a prompt leaves the
process.

## 2. The two creatable provider kinds

**The UI offers exactly two creatable provider kinds**, and they are the keys
of `PROVIDER_URLS` in `frontend/src/theme/tokens.ts`: `OpenAI-compatible`
(which covers everything speaking that API — OpenRouter, Ollama, vLLM, a local
gateway — and gets an `openai/` model prefix added in the gateway when the
model name carries no `/`) and `Anthropic`. A legacy `"Custom"` value is still
handled in `litellm_gateway.py` for rows created before it was dropped, but
nothing creates one now. Removing a key from that map removes the choice.

## 3. Parameters are a catalog, and the catalog is data

**What each of those two accepts beyond `temperature` and `max_tokens` is a
catalog, and the catalog is data**: `app/domain/value_objects/llm_params.py`,
one entry per parameter the provider's own API reference documents, under the
provider's own name for it — `stop_sequences` and `thinking` for Anthropic,
`stop` and `seed` for OpenAI-compatible. Stored in `llm_configs.params`
(JSONB), validated against that catalog **before** the row is written, merged
into the request by `_kwargs`, and served to the SPA over
`GET /llm-configs/parameters` so the form is *generated* rather than written.
Adding a parameter is one line there: **no DTO field, no form field, no
request-shaping branch.** `{}` — every row before migration `0022` — builds
byte-identically to before the column existed.

## 4. Three rules that are not style

**nothing the gateway owns is configurable** (`RESERVED`: `model`, `messages`,
`stream`, `response_format`, `temperature`, `max_tokens`, `api_key`,
`api_base`, …, refused inside `extra_body` too);
**a parameter the *selected* provider does not document is refused on save**,
because `litellm.drop_params` is on and a silently-dropped parameter is a row
describing a behaviour that never happens;
and **nothing may be catalogued that cannot be shown to reach the wire** —
`tests/unit/test_provider_params.py` drives every entry through litellm's own
parameter mapping and fails if one is misnamed or unsupported. The gateway
renames exactly one thing (`_ANTHROPIC_TO_LITELLM`: Anthropic's documented
`metadata.user_id` is litellm's `user`), and a test pins that it is the only
one. `extra_body` is the one open door and it is the OpenAI *client's* own
documented passthrough, for what an endpoint defines for itself (vLLM's
`top_k`, OpenRouter's provider routing).

## 5. A row declares what it is *for*, and there is no `kind` column

**A provider row declares what it is *for*, and there is no `kind` column.**
`model` and `embedding_model` are both optional and a row must have at least
one; `query_service.can_chat` / `can_embed` derive the rest, the way vector
staleness is derived rather than tracked. That is what lets an endpoint which
serves **only** vectors — a self-hosted TEI or Infinity server, an Ollama with
one embedding model pulled — be configured at all: it used to need an invented
chat model, whose Test button could only fail against something that does not
exist. Consequences, all enforced in one place each:
`resolve_llm(purpose=...)` refuses at the **funnel** every chat call site goes
through (a run, a draft, a semantic layer, a report outline, a report section,
a benchmark), so there is no eleventh site to forget;
`GET /llm-configs?purpose=chat|embedding` filters with those same two
predicates, and **every picker that chooses a model to answer with passes
`purpose=chat`**;
and `create_run` refuses an embeddings-only row *before* a run row exists —
`resolve_llm`'s refusal escapes the executor's failure handling, so a run made
that way sat `RUNNING` until the reconciler killed it as `E_ORPHANED`, which
tells the reader nothing. Same posture as `_bind_connection` beside it.

## 6. Embedding models live on the same table and the same screen

**Embedding models live on the same table and the same screen** —
`llm_configs.embedding_model` / `.embedding_params`, in the form's
*Embeddings* section — because an embedding endpoint needs exactly what a
provider row already holds (kind, base URL, encrypted key) and
`LLMGateway.embed` already took a resolved provider plus a model *name*. A
second screen would duplicate the credential form, the `llm_config:{id}` AAD
scheme, the probe and the delete guard to hold one string.
**But a row is one job**, and the screen says so: `/providers` lists *Models*
and *Embedder* as two groups, creating asks which kind, and the form shows
that kind's fields only — an embedder has no temperature or advanced
completion parameters, a model has no Embeddings section, and the kind is
cleared out of the payload on save (`kindPayload`) so the separation reaches
the wire instead of being a hidden field. The kind is **derived**
(`kindOf` on the frontend, `can_chat` / `can_embed` on the server), never
stored: a `kind` column would be a third answer able to disagree with the two
fields it describes. Rows written before this that declare both still work,
appear in both groups, and are told what they are rather than migrated. Anthropic is
refused an embedding model at save time, for the same reason
`probe_embedding` refuses it with no network call.
Do **not** confuse this with `database_connections.embedding_model` /
`.embedding_dimension`: those are a record of *an index* — what the vectors in
a knowledge store were actually made with, measured from a real reply — and
`.embedding_llm_config_id` (migration `0022`) names the provider that made
them. **LangChain is not a dependency**: the one `langchain_core` import is
`RunnableConfig`, a type LangGraph pulls in, used in `pipeline/graph.py` and
