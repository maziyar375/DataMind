"""Sections: a database too large to send whole, divided into named parts.

A section is **a name, a sentence and a list of tables — nothing else**
(`docs/plans/retrieval-sections.md` D1). It carries no semantic layer, no
templates, no disclosure policy and no access rule of its own; all of those
stay on the connection.

This module is the deterministic half: *propose* a division from what the
snapshot already says (schemas, the foreign-key graph, how tables are named,
the semantic layer), and *size* a section in the same units `retrieve`
decides with. No model call and no I/O — the service reads the rows and hands
them in, as `metadata.py` does.

**A proposal is never derived from row values.** It reads names, catalog
comments and the semantic layer's own words, and nothing that came out of a
`SELECT` — section text is sent to a model on every question, under every
disclosure policy, the same rung as a catalog comment
(`docs/reference/security.md`).
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from app.pipeline.metadata import table_chars

#: A foreign-key component smaller than this is not a section on its own — two
#: tables that join are a pair, not a domain — and joins the pool that is
#: grouped by name instead. The same floor applies to a name-prefix group.
MIN_SECTION_TABLES = 3
#: A component larger than this is split by name prefix even if it fits the
#: budget: forty tables is past the point where one sentence can say what a
#: section answers, and the sentence is what the router reads.
MAX_SECTION_TABLES = 40
#: Write limits, checked on save. A name is the token the router replies with,
#: so it is short and has no comma in it; a description is prompt text sent on
#: every question, so it is bounded like every other curated text.
MAX_NAME_CHARS = 60
MAX_DESCRIPTION_CHARS = 1_000
#: How many sections a connection may hold. Every one of them is a line in the
#: routing prompt, so this bounds that prompt the way `_RETRIEVE_BUDGET_CHARS`
#: bounds the schema block.
MAX_SECTIONS = 60
#: A proposed description is kept well under the write limit: it is one or two
#: sentences a router can read at a glance, not an inventory.
_PROPOSED_DESCRIPTION_CHARS = 400
_ITEM_CHARS = 140

#: The bucket every table in no section is shown in. Not a section — never
#: routed to — so a person may not call a section by its name.
UNASSIGNED = "Unassigned"
#: Names a section may not take: the bucket above, and the word the router
#: replies with when no section fits.
RESERVED_NAMES = frozenset({UNASSIGNED.lower(), "none"})

#: Schema names that say nothing about what is in them.
_GENERIC_SCHEMAS = frozenset({"public", "dbo", "main", "default", ""})

Fit = Literal["FITS", "TOO_LARGE", "EMPTY"]


@dataclass(frozen=True, slots=True)
class SectionSpec:
    """A saved section as the ask path reads it — what `NodeDeps.sections`
    carries, and all the `scope` node needs: the name the router replies with,
    the sentence it chooses by, and the tables a pick narrows retrieval to."""

    name: str
    description: str
    tables: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProposedSection:
    name: str
    description: str
    tables: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Proposal:
    sections: tuple[ProposedSection, ...]
    #: Every table in no section, in snapshot order. Together with the
    #: sections' members this is every table in the snapshot exactly once.
    unassigned: tuple[str, ...]


def key(table: Mapping[str, Any]) -> str:
    """`schema.name`, spelled as the snapshot spells it — the key `retrieve`,
    the relationships and the guard all use."""
    return f"{table.get('schema', '')}.{table.get('name', '')}"


# ── sizing ───────────────────────────────────────────────────────────────
def section_chars(members: Iterable[str], by_key: Mapping[str, Mapping[str, Any]]) -> int:
    """A section's weight in the schema block, in `table_chars` terms.

    The estimator `retrieve` decides with, so the badge on the Sections screen
    and the branch a question takes at runtime can never disagree. A member no
    longer in the snapshot weighs nothing: it cannot be rendered.
    """
    return sum(table_chars(by_key[m]) for m in members if m in by_key)


def fit(
    members: Iterable[str], by_key: Mapping[str, Mapping[str, Any]], budget_chars: int
) -> Fit:
    """FITS, TOO_LARGE, or EMPTY — the three states the screen shows."""
    present = [m for m in members if m in by_key]
    if not present:
        return "EMPTY"
    return "FITS" if section_chars(present, by_key) <= budget_chars else "TOO_LARGE"


# ── names ────────────────────────────────────────────────────────────────
_SPLIT = re.compile(r"[_\-\s.]+|(?<=[a-z0-9])(?=[A-Z])")


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("sses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if word.endswith(("ss", "us", "is")):
        return word
    if len(word) > 3 and word.endswith("s"):
        return word[:-1]
    return word


def prefix(name: str) -> str:
    """The leading word of a table name, singular and lower-case.

    `order_items`, `orders` and `OrderNotes` all lead with *order*: whoever
    named them was already thinking in sections, and this reads it back.
    """
    first = next((part for part in _SPLIT.split(name) if part), name)
    return _singular(first.lower())


def humanize(name: str) -> str:
    """`order_items` → "Order items"; `OrderItems` → "Order items"."""
    words = [w for w in _SPLIT.split(name) if w]
    text = " ".join(w.lower() for w in words) or name
    return text[:1].upper() + text[1:]


def valid_name(name: str) -> str | None:
    """Why a section may not be called this, or None when it may.

    The name is the token the router replies with, split on commas — so a
    comma or a line break in one is an unparseable reply, and two sections
    whose names differ only in case are one reply naming both.
    """
    stripped = name.strip()
    if not stripped:
        return "A section needs a name."
    if len(stripped) > MAX_NAME_CHARS:
        return f"A section name is at most {MAX_NAME_CHARS} characters."
    if "," in stripped or "\n" in stripped or "\r" in stripped:
        return "A section name cannot contain a comma or a line break."
    if stripped.lower() in RESERVED_NAMES:
        return f'"{stripped}" is reserved — choose another name.'
    return None


# ── the proposal ─────────────────────────────────────────────────────────
def _components(
    members: list[str], edges: list[tuple[str, str]]
) -> list[list[str]]:
    """Connected components of the FK graph over `members`, in snapshot order.

    `members` is already in snapshot order; each component lists its tables in
    that order and components are ordered by their first table, so the same
    snapshot always yields the same components in the same order.
    """
    parent = {m: m for m in members}
    position = {m: i for i, m in enumerate(members)}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                # The earlier table is the root, so roots are stable.
                first, second = (ra, rb) if position[ra] < position[rb] else (rb, ra)
                parent[second] = first

    groups: dict[str, list[str]] = {}
    for m in members:
        groups.setdefault(find(m), []).append(m)
    return list(groups.values())


def _by_prefix(members: list[str], names: Mapping[str, str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for m in members:
        groups.setdefault(prefix(names[m]), []).append(m)
    return groups


def _split(
    component: list[str],
    *,
    names: Mapping[str, str],
    edges: list[tuple[str, str]],
    by_key: Mapping[str, Mapping[str, Any]],
    budget_chars: int,
) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Split an oversized component by leading name token.

    A hub table joins everything to everything, so a real warehouse produces
    one component of three hundred tables — but its tables were named
    `order_*`, `billing_*`, `inventory_*` by someone who was already thinking
    in sections. Each prefix with enough tables becomes a group; a table whose
    prefix is too rare to stand alone joins the group it has the most foreign
    keys to, if that group still fits the budget. Returns `(groups, left)`,
    each group labelled with its prefix; `left` goes back to the pool.

    A prefix group is **never split further**, even over budget: a prefix is a
    naming decision somebody made, and the screen flags the section as too
    large rather than inventing a finer division nobody asked for.
    """
    groups = _by_prefix(component, names)
    big = {p: list(ts) for p, ts in groups.items() if len(ts) >= MIN_SECTION_TABLES}
    if not big:
        # Nothing to split on. Kept whole, and flagged too large on the screen,
        # where a person can do what the names could not.
        return [("", component)], []

    home = {t: p for p, ts in big.items() for t in ts}
    left: list[str] = []
    for table in component:
        if table in home:
            continue
        links: dict[str, int] = {}
        for a, b in edges:
            other = b if a == table else a if b == table else None
            if other is not None and other in home:
                links[home[other]] = links.get(home[other], 0) + 1
        placed = False
        for p in sorted(links, key=lambda p: (-links[p], -len(big[p]), p)):
            if section_chars([*big[p], table], by_key) <= budget_chars:
                big[p].append(table)
                home[table] = p
                placed = True
                break
        if not placed:
            left.append(table)

    order = {t: i for i, t in enumerate(component)}
    return [
        (p, sorted(ts, key=order.__getitem__)) for p, ts in big.items()
    ], left


def propose(
    snapshot: Mapping[str, Any],
    *,
    semantic: Mapping[str, Any] | None = None,
    budget_chars: int,
    only: Iterable[str] | None = None,
) -> Proposal:
    """A complete, deterministic division of the snapshot into sections.

    In order (`docs/plans/retrieval-sections.md` §1.2):

    1. **Split by schema** — tables in different schemas are different
       domains far more often than not.
    2. **Within a schema, connected components of the FK graph.** A component
       of `MIN_SECTION_TABLES`–`MAX_SECTION_TABLES` tables that fits the budget
       is a section: tables that join are about the same thing.
    3. **Split an oversized component by leading name token** (`_split`).
    4. **Pool what is left** — tables in components too small to stand alone,
       and tables a split could not place — and group the pool by name
       prefix; a prefix with enough tables is a section.
    5. **Everything still left is Unassigned**: never routed to, always shown.
       Every table in the snapshot is in exactly one place.
    6. **Name** from the schema, the common prefix, or the largest table, and
       **describe** from the semantic layer where there is one, else from table
       names and catalog comments.

    `only` restricts the proposal to those tables — how *Split this section*
    asks for a division of one section's members without touching the rest.
    """
    tables = [t for t in snapshot.get("tables") or [] if t.get("name")]
    if only is not None:
        wanted = set(only)
        tables = [t for t in tables if key(t) in wanted]
    by_key = {key(t): t for t in tables}
    names = {key(t): str(t.get("name", "")) for t in tables}
    edges = [
        (str(r.get("from_table", "")), str(r.get("to_table", "")))
        for r in snapshot.get("relationships") or []
        if r.get("from_table") in by_key and r.get("to_table") in by_key
    ]

    schemas: dict[str, list[str]] = {}
    for t in tables:
        schemas.setdefault(str(t.get("schema", "")), []).append(key(t))

    # (schema, prefix-or-"", members) — prefix is "" for an unsplit component.
    groups: list[tuple[str, str, list[str]]] = []
    unassigned: list[str] = []
    for schema in sorted(schemas):
        members = schemas[schema]
        pool: list[str] = []
        for component in _components(members, edges):
            if len(component) < MIN_SECTION_TABLES:
                pool += component
            elif (
                len(component) <= MAX_SECTION_TABLES
                and section_chars(component, by_key) <= budget_chars
            ):
                groups.append((schema, "", component))
            else:
                split, left = _split(
                    component, names=names, edges=edges, by_key=by_key,
                    budget_chars=budget_chars,
                )
                groups += [(schema, p, ts) for p, ts in split]
                pool += left
        order = {t: i for i, t in enumerate(members)}
        pool.sort(key=order.__getitem__)
        for p, ts in _by_prefix(pool, names).items():
            if len(ts) >= MIN_SECTION_TABLES:
                groups.append((schema, p, ts))
            else:
                unassigned += ts

    per_schema: dict[str, int] = {}
    for schema, _p, _ts in groups:
        per_schema[schema] = per_schema.get(schema, 0) + 1

    entities = _entities(semantic)
    taken: set[str] = set()
    sections: list[ProposedSection] = []
    for schema, p, members in groups:
        base = _name(
            schema, p, members, by_key=by_key, names=names,
            alone=per_schema[schema] == 1,
        )
        name = _unique(base, schema, taken)
        taken.add(name.lower())
        sections.append(ProposedSection(
            name=name,
            description=describe(members, by_key=by_key, entities=entities),
            tables=tuple(members),
        ))

    position = {key(t): i for i, t in enumerate(tables)}
    return Proposal(
        sections=tuple(sections),
        unassigned=tuple(sorted(unassigned, key=position.__getitem__)),
    )


def _largest(members: list[str], by_key: Mapping[str, Mapping[str, Any]]) -> str:
    order = {m: i for i, m in enumerate(members)}
    return min(
        members,
        key=lambda m: (
            -(by_key[m].get("approx_row_count") or 0),
            -len(by_key[m].get("columns") or []),
            order[m],
        ),
    )


def _name(
    schema: str,
    group_prefix: str,
    members: list[str],
    *,
    by_key: Mapping[str, Mapping[str, Any]],
    names: Mapping[str, str],
    alone: bool,
) -> str:
    if group_prefix:
        return humanize(group_prefix)
    if alone and schema.lower() not in _GENERIC_SCHEMAS:
        return humanize(schema)
    counts: dict[str, int] = {}
    for m in members:
        p = prefix(names[m])
        counts[p] = counts.get(p, 0) + 1
    common = max(counts, key=lambda p: (counts[p], -list(counts).index(p)))
    if counts[common] * 2 >= len(members):
        return humanize(common)
    return humanize(names[_largest(members, by_key)])


def _unique(base: str, schema: str, taken: set[str]) -> str:
    """A name no other proposed section has, case-insensitively — the router's
    reply cannot tell *Sales* from *sales*."""
    base = base[:MAX_NAME_CHARS].strip() or "Section"
    if base.lower() in RESERVED_NAMES:
        base = f"{base} tables"
    if base.lower() not in taken:
        return base
    if schema and f"{base} ({schema})".lower() not in taken:
        return f"{base} ({schema})"[:MAX_NAME_CHARS]
    n = 2
    while f"{base} {n}".lower() in taken:
        n += 1
    return f"{base} {n}"


# ── descriptions ─────────────────────────────────────────────────────────
_WHITESPACE = re.compile(r"\s+")


def _one_line(text: Any, limit: int) -> str:
    text = _WHITESPACE.sub(" ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + "…"


def _entities(semantic: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    """The layer's entities that describe a table, keyed by lower-cased table.

    Only the valid, included ones — an entity the binder marked invalid is
    about a table that is not there, and an excluded one was excluded on
    purpose."""
    out: dict[str, Mapping[str, Any]] = {}
    for entity in (semantic or {}).get("entities") or []:
        if entity.get("exclude") or entity.get("valid") is False:
            continue
        table = str(entity.get("table") or "").lower()
        if table:
            out[table] = entity
    return out


def describe(
    members: list[str],
    *,
    by_key: Mapping[str, Mapping[str, Any]],
    entities: Mapping[str, Mapping[str, Any]],
) -> str:
    """One or two sentences a router can read: what this section answers.

    Written from the semantic layer where it speaks — each entity's `label`,
    its `grain`, its `description` and its `synonyms`, content that already
    exists and until now reached only the generate prompt — and from the table
    name and its catalog comment where it does not. Largest tables first,
    whole items only, cut at a length a router reads at a glance.
    """
    present = [m for m in members if m in by_key]
    if not present:
        return ""
    ranked = sorted(present, key=lambda m: (
        -(by_key[m].get("approx_row_count") or 0), present.index(m),
    ))

    items: list[str] = []
    synonyms: list[str] = []
    for m in ranked:
        entity = entities.get(m.lower())
        table = by_key[m]
        if entity is not None:
            label = _one_line(entity.get("label"), 60) or humanize(str(table["name"]))
            grain = _one_line(entity.get("grain"), 60)
            about = _one_line(entity.get("description"), _ITEM_CHARS)
            item = label + (f" ({grain})" if grain else "") + (f": {about}" if about else "")
            for word in entity.get("synonyms") or []:
                word = _one_line(word, 30)
                if word and word.lower() not in {s.lower() for s in synonyms}:
                    synonyms.append(word)
        else:
            comment = _one_line(table.get("comment"), _ITEM_CHARS)
            item = humanize(str(table["name"])) + (f": {comment}" if comment else "")
        items.append(_one_line(item, _ITEM_CHARS).rstrip(". "))

    tail = ""
    if synonyms:
        tail = " Also called: " + ", ".join(synonyms[:6]) + "."

    # Whole items only, and room is kept for the count of what did not fit.
    room = _PROPOSED_DESCRIPTION_CHARS - len(tail) - len("; and 999 more tables.")
    kept: list[str] = []
    for item in items:
        if kept and len("; ".join([*kept, item])) > room:
            break
        kept.append(item)
    rest = len(items) - len(kept)
    body = "; ".join(kept) + (
        f"; and {rest} more table{'' if rest == 1 else 's'}" if rest else ""
    )
    return (body + "." + tail).strip()
