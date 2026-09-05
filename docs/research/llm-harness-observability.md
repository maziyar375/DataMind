# LLM Harness Observability Options

## Technical Discussion and Architectural Analysis

### 1. Context

The goal is to evaluate different approaches for adding observability to an LLM harness platform.

The current architecture has an `LLMGateway` component implemented in `litellm_gateway.py`. The gateway is intended to be the centralized point through which the application makes calls to external LLM providers.

The observability approaches under consideration are:

1. Build lightweight observability internally using existing logging and database infrastructure.
2. Use LiteLLM's native instrumentation and spend tracking.
3. Use Langfuse as a dedicated LLM observability platform.
4. Use a general APM solution such as OpenTelemetry, Sentry, or Datadog.

The discussion so far has focused primarily on understanding Options 1–3 and, especially, the architectural meaning of the `LLMGateway` choke point.

---

# 2. Current Architecture

The important architectural assumption is:

```text
Application / Harness
        │
        ├── complete()
        ├── stream()
        └── structured()
                │
                ▼
          LLMGateway
                │
                ▼
             LiteLLM
                │
                ▼
       LLM Provider API
                │
        ┌───────┼────────┐
        ▼       ▼        ▼
      OpenAI Anthropic  Other
```

The key point is that `LLMGateway` is the **single provider-call choke point**.

This does NOT mean that it is the only possible place to intercept information in the entire application.

Rather, it means:

> Every actual call to an external LLM provider is expected to pass through `LLMGateway`.

Therefore, instrumenting `LLMGateway` once can capture provider-level LLM activity from all parts of the harness that use the gateway.

---

# 3. Choke Point vs. Interception Point

These concepts should not be confused.

There can technically be multiple places where information could be intercepted:

```text
Application
    │
    ├── Agent
    ├── Pipeline
    ├── Evaluator
    ├── LLMGateway
    ├── LiteLLM
    ├── HTTP client
    └── Network proxy
              │
              ▼
           Provider
```

Each layer has different visibility.

For example, an application-level component may know:

```text
Agent = ResearchAgent
Task = summarize_document
Step = 4
```

while `LLMGateway` may know:

```text
model
provider
messages
temperature
input tokens
output tokens
latency
cost
status
```

A network-level proxy may see the HTTP request and response, but may not understand the semantic context of the harness execution.

Therefore:

> `LLMGateway` is the centralized LLM-call interception/choke point, not the only possible interception point in the whole workflow.

---

# 4. Option 1 — Close the Gap Ourselves

## Summary

The first proposal is to avoid introducing a new observability platform.

Instead:

* fix the existing LLM usage reporting;
* use `structlog` for structured events;
* continue storing usage information in the existing `runs` table.

The basic architecture remains:

```text
Harness
   │
   ▼
LLMGateway
   │
   ▼
LiteLLM
   │
   ▼
Provider

LLMGateway
   │
   ├── structured logs
   │
   └── runs table
```

## Existing problem

The report identifies a bug:

`complete()` already returns usage information, but `structured()` and `stream()` do not properly propagate that information.

As a result, many LLM calls currently appear to have zero token usage.

For example, a provider may return:

```text
input_tokens  = 1500
output_tokens = 400
total_tokens  = 1900
```

but the gateway may discard the usage metadata on some execution paths.

The `runs` table consequently receives:

```text
input_tokens  = 0
output_tokens = 0
```

even though the provider actually consumed tokens.

## Proposed fix

Make all gateway methods return consistent usage information:

```text
complete()
stream()
structured()
```

Conceptually, the result would contain both:

```text
output
usage
```

where usage contains information such as:

```text
input_tokens
output_tokens
total_tokens
cost
latency
```

The usage data can then be stored in the existing `runs` table.

---

# 5. Structured Logging

The proposal also suggests using `structlog`.

Instead of a plain message:

```text
LLM call completed
```

the application can emit a structured event:

```json
{
  "event": "llm_call",
  "model": "claude-sonnet",
  "input_tokens": 1500,
  "output_tokens": 400,
  "total_tokens": 1900,
  "latency_ms": 2340,
  "status": "success"
}
```

This makes logs machine-readable and easier to filter, aggregate, and analyze.

---

# 6. What Option 1 Provides

Option 1 can answer questions such as:

* How many tokens are being consumed?
* How much are we spending?
* Which models consume the most tokens?
* Which providers are being used?
* Which runs are expensive?
* How many calls fail?
* What is the average latency?
* How does usage change over time?

For example:

```sql
SELECT
    model,
    SUM(input_tokens),
    SUM(output_tokens),
    SUM(cost)
FROM runs
GROUP BY model;
```

This is enough for basic cost and volume analytics.

---

# 7. Limitation of Option 1

The main limitation is that this is primarily **metrics/telemetry**, not full LLM tracing.

For example, the database might tell us:

```text
Run 781
Model: Claude
Input tokens: 12,000
Output tokens: 700
Cost: $0.08
```

But it may not explain why the answer was poor.

A richer trace could show:

```text
Run 781
│
├── Retrieval
│   └── 5 documents retrieved
│
├── Prompt construction
│   └── Prompt version 23
│
├── LLM call
│   ├── Prompt
│   ├── Response
│   ├── Tokens
│   └── Cost
│
└── Final answer
```

Therefore, the report describes Option 1 as having an **analytics ceiling based on SQL queries over `runs`**.

---

# 8. Option 2 — LiteLLM Instrumentation

The second proposal is to use LiteLLM's own instrumentation rather than manually implementing all LLM usage tracking.

There are two distinct approaches.

## Option 2A — LiteLLM Callbacks

Architecture:

```text
Harness
   │
   ▼
LLMGateway
   │
   ▼
LiteLLM library
   │
   ▼
Provider
```

LiteLLM provides callbacks that can expose information after an LLM call.

Potential telemetry includes:

```text
model
provider
input tokens
output tokens
total tokens
cost
latency
success/failure
request/response metadata
```

The application can use these callbacks to record the information.

The key benefit is that LiteLLM handles more of the provider-specific accounting.

Instead of manually extracting usage after every call, LiteLLM can provide the telemetry through its instrumentation mechanisms.

---

# 9. Option 2B — LiteLLM Proxy

The second LiteLLM approach is to run the LiteLLM Proxy as a separate service.

Architecture:

```text
Harness
   │
   ▼
LLMGateway
   │
   ▼
LiteLLM Proxy
   │
   ▼
Provider
```

The LiteLLM Proxy can provide centralized capabilities such as:

* usage tracking;
* spend tracking;
* centralized logging;
* dashboards;
* API keys / virtual keys;
* routing;
* load balancing;
* rate limiting;
* centralized provider/model management;
* aggregation across multiple clients.

However, this introduces a new service.

The architecture becomes:

```text
Application
     │
     ▼
LLMGateway
     │
     ▼
LiteLLM Proxy
     │
     ▼
Provider
```

The additional service must be deployed, secured, monitored, and maintained.

---

# 10. Does LiteLLM Proxy See More Information?

An important conclusion from the discussion is:

> LiteLLM Proxy does not necessarily provide significantly more fundamental LLM information than LiteLLM callbacks.

Both can observe the underlying LLM request and response.

For example, both can potentially obtain:

```text
model
provider
input tokens
output tokens
cost
latency
errors
request/response information
```

The main difference is therefore **not necessarily information depth**.

It is primarily **centralization and infrastructure capabilities**.

A useful comparison is:

| Capability                                | LiteLLM Callbacks | LiteLLM Proxy |
| ----------------------------------------- | ----------------: | ------------: |
| Token usage                               |               Yes |           Yes |
| Cost                                      |               Yes |           Yes |
| Latency                                   |               Yes |           Yes |
| Model/provider                            |               Yes |           Yes |
| LLM request/response                      |        Can access |    Can access |
| Centralized dashboard                     |  Limited / custom |           Yes |
| Centralized LLM gateway                   |                No |           Yes |
| Multi-application aggregation             |            Custom |      Built in |
| Separate service                          |                No |           Yes |
| Deeper understanding of harness semantics |                No |            No |

Therefore, adding LiteLLM Proxy does not automatically make the system more semantically aware of the harness.

It mainly gives LiteLLM a centralized service role.

---

# 11. Option 3 — Langfuse

The third proposal is to use Langfuse as a dedicated LLM observability platform.

The conceptual architecture becomes:

```text
Harness
   │
   ▼
LLMGateway ─────────────► Langfuse
   │                       │
   ▼                       ▼
LiteLLM                 Trace DB
   │                       │
   ▼                       ▼
Provider                Langfuse UI
```

Langfuse is specifically designed for LLM observability and tracing.

Its main advantage is that it can represent an LLM application execution as a **trace**, rather than simply a collection of usage metrics.

---

# 12. LLM Trace Concept

Consider an agent workflow:

```text
User request
     │
     ▼
Agent
     │
     ├── LLM call #1
     │
     ├── Tool call
     │
     ├── LLM call #2
     │
     └── LLM call #3
             │
             ▼
         Final answer
```

A simple usage database might record:

```text
LLM call #1 → 2,000 tokens
LLM call #2 → 5,000 tokens
LLM call #3 → 1,500 tokens
```

Langfuse can represent the relationships:

```text
Run #123
│
├── Agent
│   │
│   ├── LLM call #1
│   │   ├── Prompt
│   │   ├── Response
│   │   ├── Model
│   │   ├── Tokens
│   │   └── Cost
│   │
│   ├── Tool call
│   │
│   └── LLM call #2
│       ├── Prompt
│       ├── Response
│       ├── Model
│       ├── Tokens
│       └── Cost
│
└── Final output
```

The hierarchy is one of the key reasons to choose an LLM-specific tracing system.

---

# 13. Why Langfuse Is Different

The fundamental distinction is:

### Option 1

Primarily answers:

> How much did this run consume?

### Option 2

Primarily answers:

> What did this LLM call consume and cost?

### Option 3

Can answer:

> What happened throughout this LLM application run, and why did it behave this way?

For example, a poor answer could potentially be traced back to:

* incorrect retrieval;
* irrelevant documents;
* a prompt change;
* excessive context;
* an unexpected tool result;
* a particular model;
* a particular prompt version.

This is substantially deeper than token/cost monitoring.

---

# 14. Prompt and Response Inspection

Langfuse can capture information such as:

```text
Prompt
Response
Model
Tokens
Cost
Latency
Trace relationships
Metadata
Evaluation scores
```

This enables inspection of individual LLM conversations/runs.

For example:

```text
Run #781

Prompt version: v23

Retrieved documents:
    Document A
    Document B
    Document C

LLM input:
    ...

LLM response:
    ...

Evaluation:
    correctness = 0.72
    relevance = 0.91
```

This allows engineers to investigate the behavior of individual runs rather than only looking at aggregate metrics.

---

# 15. Evaluation Capabilities

Another important Langfuse capability is associating evaluation scores with traces.

Conceptually:

```text
Run #781
│
├── LLM call
│
├── Output
│
└── Evaluation
     ├── correctness
     ├── relevance
     └── hallucination
```

This enables questions such as:

> Did prompt version 23 improve answer quality?

rather than only:

> Did prompt version 23 change token consumption?

This makes Langfuse more suitable for debugging and improving LLM application behavior.

---

# 16. Important Limitation: Langfuse Does Not Automatically Understand Everything

Integrating Langfuse at `LLMGateway` does not mean Langfuse automatically knows the complete semantic execution of the harness.

For example, if only the gateway is instrumented, Langfuse may know:

```text
LLM call
├── prompt
├── response
├── model
├── tokens
└── cost
```

But it does not automatically know that:

```text
this LLM call
    ↓
was step 4 of ResearchAgent
    ↓
which retrieved documents A/B/C
    ↓
which came from a particular pipeline branch
```

unless the application sends that context as trace/span metadata or explicitly creates corresponding observations/spans.

Therefore:

> Langfuse provides the tracing infrastructure, but the application still needs to instrument meaningful execution boundaries if it wants rich application-level traces.

---

# 17. Langfuse Deployment and Security

The discussion identified an important security distinction between Langfuse Cloud and self-hosting.

## Langfuse Cloud

Conceptually:

```text
Your Harness
     │
     ├──────────────► Langfuse Cloud
     │                   │
     │                   ▼
     │              External storage
     │
     ▼
LLM Provider
```

If prompts and responses are sent to the cloud service, the organization must consider:

* data residency;
* data retention;
* privacy;
* access control;
* security policies;
* whether sensitive prompt/response data can leave the organization's infrastructure.

## Self-hosted Langfuse

Conceptually:

```text
             Your infrastructure
        ┌─────────────────────────┐
        │                         │
        │ Harness                 │
        │    │                    │
        │    ▼                    │
        │ LLMGateway ──────┐      │
        │    │             │      │
        │    ▼             ▼      │
        │ LiteLLM       Langfuse  │
        │                  │      │
        │                  ▼      │
        │             Langfuse DB│
        │                         │
        └─────────────────────────┘
```

Self-hosting provides control over where the observability data is stored.

However:

> Self-hosting does not automatically make the system secure.

Security still depends on deployment configuration, including:

* authentication;
* authorization;
* network isolation;
* TLS;
* database access restrictions;
* secret management;
* retention policies;
* backup security;
* access controls.

---

# 18. Why Self-Hosted Langfuse Means Additional Infrastructure

If the goal is to prioritize keeping LLM data inside the organization's infrastructure, self-hosting Langfuse generally means running additional infrastructure.

The exact deployment topology can vary, but conceptually:

```text
Existing application
       +
Langfuse service(s)
       +
Langfuse database
```

This introduces additional operational responsibilities.

By comparison, Option 1 can remain entirely inside the existing application and database infrastructure.

---

# 19. Sensitive Data Consideration

Langfuse's richer observability is also its main security concern.

Token metrics are relatively low sensitivity:

```text
input_tokens = 1500
output_tokens = 400
cost = $0.012
```

Full traces can contain:

```text
user prompts
documents
retrieved context
model responses
tool outputs
application metadata
```

Therefore, the decision to use Langfuse should consider not only:

> "How much observability do we want?"

but also:

> "What LLM data are we comfortable storing in an observability system?"

This is particularly important for production deployments.

---

# 20. Option 4 — General APM

The fourth option includes:

* OpenTelemetry;
* Sentry;
* Datadog.

These tools are general application-performance/observability systems rather than LLM-specific platforms.

They are better suited to answering questions such as:

```text
API request
   │
   ▼
Application
   │
   ├── database
   ├── external API
   ├── LLM
   └── other services
```

They can provide broad application tracing.

However, they are not necessarily the best choice if the immediate requirement is specifically:

> "We need to see LLM token usage, cost, prompts, responses, and LLM-specific behavior."

General APM becomes more attractive if the actual requirement is:

> "We have no application-wide observability at all."

---

# 21. Overall Comparison

| Option                     | Primary purpose                 |               New service? | Token/cost | Prompt/response tracing | Application-level tracing |
| -------------------------- | -------------------------------- | --------------------------: | ----------: | ------------------------: | --------------------------: |
| 1. Existing DB + structlog | Lightweight internal telemetry  |                         No |        Yes |          Limited/custom |                   Limited |
| 2A. LiteLLM callbacks      | LLM usage instrumentation       |                         No |        Yes |             Some access |                   Limited |
| 2B. LiteLLM Proxy          | Centralized LLM gateway + usage |                        Yes |        Yes |             Some access |                   Limited |
| 3. Langfuse                | LLM observability/tracing       | Usually yes if self-hosted |        Yes |                     Yes |      Yes, if instrumented |
| 4. General APM             | Whole-system observability      |                    Usually |   Possible |         Not LLM-focused |                       Yes |

---

# 22. Main Architectural Insight

The options should not necessarily be treated as mutually exclusive.

They operate at different levels.

A possible layered approach is:

```text
                Application / Harness
                        │
                        ▼
                   LLMGateway
                  /          \
                 /            \
                ▼              ▼
           LiteLLM          Observability
                │              │
                ▼              ▼
             Provider       DB / Langfuse
```

Option 1 can coexist with Option 3.

For example:

```text
LLMGateway
    │
    ├── record essential usage → runs table
    │
    └── emit detailed trace → Langfuse
```

This provides a local source of truth for basic operational metrics while using Langfuse for deeper investigation.

---

# 23. Recommended Decision Framework

The report's recommendation can be understood as a progression.

## Requirement 1: "How much are we spending?"

Use Option 1.

Fix the existing usage-reporting bug and populate the existing `runs` table.

There is little reason to introduce a new observability service solely for this requirement.

## Requirement 2: "We want centralized LLM usage/cost infrastructure."

Consider Option 2.

LiteLLM callbacks are the lower-complexity option.

LiteLLM Proxy becomes attractive if centralized gateway functionality, routing, authentication, rate limiting, dashboards, or multi-application aggregation are also needed.

## Requirement 3: "We need to understand why an LLM run behaved badly."

Consider Option 3.

Langfuse becomes valuable when the requirement changes from:

```text
How much did the LLM cost?
```

to:

```text
What happened during this run?
Why did the model produce this answer?
Which prompt/version/context/tool caused the problem?
```

---

# 24. Key Conclusions From the Discussion

### Conclusion 1

`LLMGateway` is the centralized **provider-call choke point**.

It is therefore a natural location for LLM-level instrumentation.

### Conclusion 2

It is incorrect to say that `LLMGateway` is the only possible interception point.

Application code, LiteLLM, HTTP clients, and network proxies can all technically be instrumented.

They simply provide different levels of information.

### Conclusion 3

Option 1 is essentially a **lightweight internal LLM telemetry system**.

It is inexpensive and fits the existing architecture well.

### Conclusion 4

Option 2 does not necessarily provide dramatically more fundamental LLM information than Option 1.

Its main advantage is leveraging LiteLLM's existing instrumentation and, in Proxy mode, gaining centralized LLM infrastructure.

### Conclusion 5

LiteLLM Proxy is primarily an **infrastructure/management improvement**, not necessarily an observability-depth improvement.

### Conclusion 6

Langfuse is fundamentally different because it is designed around **LLM traces and individual run inspection**, rather than only aggregate token/cost metrics.

### Conclusion 7

Langfuse does not automatically understand the semantic structure of the harness.

The application must provide the relevant context and trace relationships.

### Conclusion 8

If security/data residency is a major concern, self-hosting Langfuse can keep observability data inside the organization's infrastructure, but it introduces additional infrastructure and operational responsibilities.

### Conclusion 9

The richer the observability system, the more sensitive data it may store.

There is a meaningful difference between storing:

```text
tokens = 1900
cost = $0.012
```

and storing:

```text
full prompt
retrieved documents
tool outputs
full response
```

### Conclusion 10

The strongest initial strategy is to **fix the existing usage-reporting problem first**.

That gives the platform reliable token/cost data regardless of whether Langfuse or another observability system is introduced later.

---

# 25. Central Question for Further Architecture Work

The most important unresolved question is:

> **What observability information does the LLM harness actually need?**

Specifically, determine whether the platform needs:

1. Token usage
2. Cost
3. Latency
4. Model/provider information
5. Error information
6. Exact prompts
7. Exact completions
8. Conversation/session grouping
9. Agent/workflow traces
10. Tool-call traces
11. Retrieval traces
12. Prompt versioning
13. Evaluation scores
14. Whole-application distributed tracing
15. Centralized dashboards
16. Multi-tenant usage aggregation

The answer to this list should drive the architecture rather than selecting an observability product first.

The fundamental tradeoff is:

```text
More observability
        ↓
More information
        ↓
More debugging capability
        ↓
More infrastructure
        +
More sensitive data stored
        +
More operational complexity
```

The objective should therefore be to choose the **minimum observability architecture that satisfies the actual requirements**, while leaving a clean extension point for deeper tracing later.
