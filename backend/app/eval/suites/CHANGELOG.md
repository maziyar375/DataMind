# Golden-set changelog

The golden set is **frozen**. Questions are never edited to make a score go up.
Gold SQL is corrected **only when demonstrably wrong** (it does not answer the
question the English asks, or it errors against the fixture), and every such
correction is logged here with the reason and the evidence.

Retrieval context and prompts may be tuned freely; the gold answers may not.

Format per entry:

```
## <date> — <suite> <record-id>
- **What changed:** old → new (one line)
- **Why:** the demonstrable defect (not "to pass")
- **Evidence:** the query/output that proves the old gold was wrong
```

---

<!-- No corrections. The set as authored in Task 2 stands. -->

Evaluated against gemma-4-31B and DeepSeek V4 Pro. Every mismatch investigated
traced to a model choice, a service/harness defect (guard CTE handling, an
over-strict rounding tolerance), or genuine question ambiguity (relative time
windows, long-vs-wide comparison shape) — never a demonstrably wrong gold. No
gold SQL was changed.
