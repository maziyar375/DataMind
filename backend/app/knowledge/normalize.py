"""question → `question_normalized`, the match key.

The store's unique constraint and (from Phase 2) the lexical matcher both read
this string, so the function has exactly one job: make two askings of the same
question produce the same key, and two different questions produce different
ones. Everything it does is reversible in the reader's head, which is the
property that makes a bad match explainable.

What it does, in order, and why each step is here:

1. **NFKC + casefold.** `Ｒevenue` and `REVENUE` are the same word; a Persian
   or Arabic question keeps its letters, since casefold is a no-op on scripts
   without case.
2. **`{slot}` → `*`.** A curator writes `revenue for {region}`; the store must
   match `revenue for EMEA`. The brace is the one piece of syntax this feature
   asks a person to learn, and it exists so a template reads as a *family*.
3. **Quoted strings and bare numerals → `*`.** `top 10 stores` and `top 25
   stores` are one question. A four-digit year, an ISO date and a decimal all
   land here.
4. **Punctuation → space.** A trailing `?` must not make a new key.
5. **Collapse whitespace.**

What it deliberately does **not** do: strip stopwords, stem, or reorder. Each
would raise the hit rate on paper and make a false match impossible to explain
— *"revenue by store"* and *"revenue by month"* differ in one stopword-adjacent
token, and the cost of confusing them is a confident wrong answer.
"""
from __future__ import annotations

import re
import unicodedata

#: The token every masked literal collapses to. A character no natural
#: question contains, so it cannot be forged by typing.
MASK = "*"

_SLOT = re.compile(r"\{[^{}]*\}")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"|«[^»]*»|“[^”]*”")
#: A run of digits, optionally with separators — `2026`, `2026-01-01`,
#: `1,000.50`, `۱۴۰۴`. Bounded by non-alphanumerics so `q3` and `p90` survive
#: as words: they are part of the question, not a value in it.
_NUMERIC = re.compile(
    r"(?<![^\W\d_])[\d٠-٩۰-۹]+"
    r"(?:[.,:/\-][\d٠-٩۰-۹]+)*"
    r"(?![^\W\d_])"
)
#: Everything that is not a letter, a digit, a mask or whitespace.
_PUNCTUATION = re.compile(r"[^\w\s" + re.escape(MASK) + r"]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")
#: Two masks with only punctuation between them are one slot, not two.
_MASK_RUN = re.compile(re.escape(MASK) + r"(?:\s*" + re.escape(MASK) + r")+")


def normalize_question(question: str) -> str:
    """The match key for a question or a question pattern.

    Total: any string produces a string, and the empty question produces `""`
    rather than an exception. The store's unique constraint is on this value,
    so a caller that skipped it would create a duplicate nothing could find.
    """
    text = unicodedata.normalize("NFKC", question or "").casefold()
    text = _SLOT.sub(f" {MASK} ", text)
    text = _QUOTED.sub(f" {MASK} ", text)
    text = _NUMERIC.sub(f" {MASK} ", text)
    text = _PUNCTUATION.sub(" ", text)
    text = _MASK_RUN.sub(MASK, text)
    return _WHITESPACE.sub(" ", text).strip()


def slots(question: str) -> list[str]:
    """The `{names}` a question pattern declares, in the order they appear.

    Used by the editor to show which declared parameters the question actually
    mentions — a template whose SQL takes `:region` while its question never
    says `{region}` will never bind, and saying so at authoring time is much
    cheaper than discovering it as a silent miss.
    """
    seen: list[str] = []
    for match in _SLOT.finditer(question or ""):
        name = match.group(0)[1:-1].strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def example_questions(question: str, params: list[tuple[str, str]]) -> str:
    """The editor's live preview: the pattern with real values substituted.

    Nobody understands `{region}` from the brace; everybody understands
    *"revenue by month for EMEA in 2026"*. Kept here rather than in the UI so
    the preview and the match key are computed from the same reading of the
    braces.
    """
    filled = question or ""
    for name, value in params:
        filled = filled.replace(f"{{{name}}}", value)
    return filled
