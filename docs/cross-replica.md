# Running more than one API replica

What changes when the process serving a request is not the process doing the
work, what was done about each, and how to prove it on your own machine.

This is Phase 6 of [langgraph-migration.md](langgraph-migration.md), and the
phase record there says it should be folded into production-readiness work
rather than done as a LangGraph phase. It was — nothing here is a graph change.
Companion to [architecture.md](architecture.md) §17 (which deferred the shared
queue and named the trigger) and [pipeline.md](pipeline.md) (the run itself).

---

## 1. The seven things that were single-process

Each of these worked because a dict, a task handle or an `asyncio.Event` was
reachable from wherever the request happened to land. **None of them were bugs
at one replica.** All of them were at two.

| | before | at two replicas | now |
|---|---|---|---|
| SSE fan-out | in-process bus, one asyncio queue per subscriber | browser on B, run on A → subscribes to a bus that never publishes; the stream hangs until the client gives up | `LISTEN`/`NOTIFY` over the `run_events` log both replicas already write |
| chat cancel | `executor.cancel` over a local `_tasks` dict | B writes `CANCELLED`, A never hears, and A's `_finalise` overwrites it with `SUCCEEDED` | `runs.cancel_requested`, read on the owner's heartbeat; `_finalise` refuses to overwrite a terminal status |
| report cancel | `ReportRunExecutor._flags` | no effect at all — minutes of model calls spent on a document the user closed | `report_runs.cancel_requested`, read by `_watch` |
| starting a run | the handler that accepted the POST submits to its own executor | a process that dies between the commit and the submit loses the run silently | `RunService.claim` (`FOR UPDATE SKIP LOCKED`), plus a claim poller for the unowned |
| stranded report resume | startup resumes every `QUEUED`/`RUNNING` report run | B's startup resumes a run A is generating → **two processes narrating the same sections into the same run** | `stranded_runs` filters on `heartbeat_at`, which `report_runs` now has |
| the reconciler | every replica sweeps on its own timer | N replicas × the judgement "this heartbeat is late enough to call it dead", against live users' runs | `pg_try_advisory_xact_lock` |
| `event_bus.forget()` | never called by anything | — | called on finish and on cancel |

The last one is not a replica problem at all. `forget()` existed from the start
and nothing invoked it, so `_history`, `_seq` and `_closed` held every event of
every run since boot — behind a durable copy of the same events in
`run_events`. It is in this table because the fix belongs to the same design:
the in-memory buffer is safe to drop precisely because the log is authoritative.

---

## 2. Why Postgres and not Redis

The phase checklist said "Redis-backed `EventPublisher` adapter". It is
Postgres `LISTEN`/`NOTIFY` instead, for the reason Phase 4 declined a
checkpointer: **the rows already exist.**

`run_events` is a durable, ordered log with `UNIQUE(run_id, seq)`, written on
every emit inside `RunService._emit`, and the SPA already had a polling
fallback reading it. A broker would be a second copy of that fact, in a second
deployment unit, with its own delivery semantics to reason about — against
[CLAUDE.md](../CLAUDE.md)'s "no microservices, no broker, no vector DB", which
is a standing commitment rather than an accident.

**The notification carries `run_id:seq` and nothing else.** Three things fall
out of that, and they are why this is not a compromise:

- `NOTIFY` has an 8000-byte payload ceiling. A compiled Vega-Lite spec does not
  fit; an identifier cannot fail to.
- Postgres delivers a notification **at commit** and drops it on rollback. The
  row is written in that same transaction, so a listener that wakes and reads
  can never find the row missing. Visibility and ordering are the database's
  problem and it already solved them. This is why `notify_run_event` must be
  called on the session that inserts the row — it is the guarantee, not a
  convention.
- A dropped notification costs latency, not events. `_drain` reads
  `seq > watermark` rather than the one seq it was told about, so the next
  notification catches up everything missed in a reconnect window.

Cost: one dedicated connection per process, outside the pool, because `LISTEN`
is session-scoped. A replica whose listener drops still serves *correct* SSE —
the endpoint backfills from the log and the SPA falls back to polling — it just
stops being live until the listener reconnects, which it retries every 2s.

---

## 3. The one that bit during verification

The reconciler's lock was written as `pg_try_advisory_lock` … `pg_advisory_unlock`
in a `try/finally`. It passed its unit test. Against two live replicas,
`pg_locks` showed the lock still held after the sweep, and every subsequent
sweep logged `reconciler_skipped_locked` — **the reconciler was silently
disabled**.

A session-level advisory lock is released by an unlock *on the connection that
took it*. `reconcile_stale` commits, and SQLAlchemy may hand the connection back
to the pool at that point and take another for the next statement, so the
unlock lands on a different backend and quietly fails. The lock then survives on
a pooled connection for as long as that connection lives.

`pg_try_advisory_xact_lock`, taken in the same transaction as the sweep, cannot
leak: Postgres releases it at commit, at rollback, and on a crash. There is
nothing to pair, so there is nothing to mispair.

It is worth naming the failure mode, because it is the one thing here that
nothing would have reported: **a reconciler that never runs looks exactly like
one that keeps finding nothing.**

---

## 4. Proving it

```bash
docker compose -f docker-compose.yml -f docker-compose.replicas.yml up -d
```

Two API replicas behind nginx on the usual port 8000. The balancer config
(`scripts/nginx-replicas.conf`) turns off `proxy_buffering`, without which SSE
arrives in one lump at the end of the run and the live step trail silently
becomes a summary.

Both replicas should report a listener:

```bash
docker compose logs api | grep run_event_listener_ready   # expect one per replica
```

**Fan-out across processes.** With an SSE stream attached through the balancer,
write an event from a connection that is neither replica:

```sql
BEGIN;
INSERT INTO run_events (run_id, seq, type, data, at)
VALUES ('<run>', 1, 'STEP_STARTED', '{"seq":1,"name":"route"}', now());
SELECT pg_notify('run_events', '<run>:1');
COMMIT;
```

The stream delivers it. That is the whole cross-replica path — nothing in the
producing process was involved.

**Exactly one claimer.** Two concurrent `RunService.claim` calls for one queued
run: one returns `True`, the other `False` immediately rather than blocking.
Without `SKIP LOCKED` the loser waits for the winner's transaction, which turns
a claim into a queue.

**No leaked locks.** After any number of sweeps:

```sql
SELECT count(*) FROM pg_locks WHERE locktype = 'advisory';  -- 0
```

---

## 5. What is still single-replica

- **A chat run does not survive the death of its process.** It is claimable
  again once its heartbeat lapses, but its partial work is gone — chat runs are
  not checkpointed, which [Phase 4](langgraph-migration.md) decided on
  measurement (88 KB per node, 97% of it the schema block, for a run of 5–60
  seconds). Report runs *do* survive, from their result rows.
- **Cross-replica cancel is not instant.** The owner learns on its next
  heartbeat, so worst case is `run_heartbeat_seconds` (10s). Same-replica
  cancel is still immediate — the local task handle is tried first.
- **`MAX_CONCURRENT_RUNS` is per replica, not per cluster.** Three replicas at
  8 is 24 concurrent runs against the provider and the customer's database.
  Size it accordingly.
- **Sticky sessions are not required and not helpful.** Every path that used to
  need them is now durable. Do not add them to work around something here;
  whatever it is, that is a bug in this design and not a deployment fix.
