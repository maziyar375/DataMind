"""The arithmetic a *why* question needs, computed here instead of by a model.

`docs/plans/deep-analysis-mode.md` Phase 2. A sibling of `app/reports/` and
under the same charter: pure functions over rows that have **already been
executed**, with no session, no settings, no provider and no tokens — and, by
the ninth import-linter contract, no way to acquire any of them. That is the
whole reason it can be trusted: it is not that it does not reach a database, it
is that it *cannot*.

    from app.analysis import Column, contribution, compare, distribution

## What is in here

| | |
|---|---|
| `measures.py` | which of three classes a measure is, and why the class picks the method |
| `contribution.py` | the three attribution algorithms, one per class |
| `periods.py` | two periods made comparable — or declared not to be |
| `outliers.py` | z-scores at a threshold chosen by cardinality |

## Every refusal is a value, never an exception

A *why* question that the data cannot answer is the ordinary case, not the
error case, and it is the case this package exists for. Asked why revenue fell,
a language model will find a reason; this returns `Refusal(NO_CHANGE, …)` when
revenue did not fall, and the caller renders that sentence. Exceptions would
make the honest answer look like a malfunction and invite a `try` that swallows
it.

`Refusal` is **falsey** and every result type is truthy, so the caller reads:

```python
result = contribution(columns=cols, before_rows=before, after_rows=after)
if not result:
    return result.reason          # a sentence a business reader can act on
for driver in result.top(3):
    ...
```

The refusals, and where each comes from:

SpotIQ's three, copied rather than rediscovered — a product that has shipped
this for years has already found out where it breaks:

* `COMPARATIVE_MEASURE` — it refuses "growth of" / "versus" phrasings, and so
  does this. A measure that is *already* a change cannot be decomposed into a
  change.
* `GROUP_FORMULA` — a complex `group_*` formula is already aggregated across a
  grouping this module cannot see.
* `MIXED_ATTRIBUTE_TYPES` — `"42"` and `42` in one column are two segments a
  reader will read as one.

And the ones found by writing the arithmetic down:

* `NO_CHANGE` — nothing moved. **The commonest true answer to a *why*
  question**, and the one no model will give.
* `RATIO_NEEDS_WEIGHT` — an average needs the count it was taken over. Assuming
  equal weights is the confident nonsense this phase's risk names.
* `ZERO_BASELINE` — a percentage change from zero is undefined.
* `UNCOMPARABLE_PERIODS` — two spans of different lengths, asked to be one
  comparison.
* `NO_MEASURE`, `NO_DIMENSION`, `NOT_ENOUGH_ROWS`, `TOO_MANY_ROWS`,
  `NOT_ENOUGH_VALUES` — the shapes a result has to have.

The source for the first three is
[research §3.4](../../../docs/research/deep-analysis-mode.md).

## What this package deliberately does not do

**No model call, ever.** The split is mvp2 Part 5 §4's — the model picks which
dimensions to test, this computes what actually moved. A function here that
called a model would make every number in it unverifiable, which is the one
property the package is for.

**No database.** It reads rows that were already fetched through the guarded
path, so it is not a new execution surface and does not replay the hostile
corpus. `docs/plans/deep-analysis-mode.md` D4 is the decision and its reason.

**No trading calendar, no seasonality, no forecast.** `periods.py` normalises
by calendar day, which is computable from the rows. A trading calendar is a
fact about a business that is not in them, and a seasonal adjustment is a model
of one. Both belong in the semantic layer if they belong anywhere.

**No multi-attribute root cause.** Each call attributes one measure by one
dimension. R-Adtributor's recursive form is the extension, and it is deferred
until a question in `deep_v1` demonstrably needs it.
"""
from __future__ import annotations

from app.analysis.contribution import Contribution, Driver, contribution
from app.analysis.measures import (
    Column,
    MeasureClass,
    Refusal,
    RefusalCode,
    classify,
    dimensions,
    measures,
    weight_for,
)
from app.analysis.outliers import Distribution, Outlier, distribution, threshold_for
from app.analysis.periods import Period, PeriodPair, compare

__all__ = [
    "Column",
    "Contribution",
    "Distribution",
    "Driver",
    "MeasureClass",
    "Outlier",
    "Period",
    "PeriodPair",
    "Refusal",
    "RefusalCode",
    "classify",
    "compare",
    "contribution",
    "dimensions",
    "distribution",
    "measures",
    "threshold_for",
    "weight_for",
]
