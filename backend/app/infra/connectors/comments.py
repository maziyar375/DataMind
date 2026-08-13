"""Catalog descriptions, shared across every engine.

A sibling of `hints.py`, and for the same reason: four engines produce four
kinds of dirt, and everything above the connector layer must see one clean
thing. What a `COMMENT ON` says has to mean the same whether it came from
`pg_description`, `information_schema.COLUMN_COMMENT`, an `MS_Description`
extended property or `ALL_COL_COMMENTS`.

**This is free documentation.** A DBA wrote `COMMENT ON COLUMN orders.status IS
'fulfilment state; C = cancelled by the customer'` years ago, and until now no
connector read a single one. It is business-accurate, human-authored and one
catalog query away from the exact place the pipeline needs it.

Three properties are load-bearing:

* **Cleaning happens at capture, never at render.** A bad comment is never
  written into a snapshot, so every consumer downstream — the schema block, the
  semantic generator, the `describe` node, the UI — inherits the same hygiene
  for free and none of them has to remember to ask.
* **Whitespace is collapsed, including newlines.** A comment is untrusted text
  written by whoever owns the target database, and after this feature it lands
  inside a system prompt. One line inside quotes cannot forge a prompt section
  header, close a block, or open a fake `Tables:` list. (The guard is what makes
  an injection *harmless* — AST validation fails closed, so the worst outcome is
  a wrong query — but there is no reason to make it easy. See §3.2 of
  `docs/catalog-metadata-plan.md`.)
* **A comment that teaches nothing is dropped, not stored.** `"orders"` on
  `orders`, `InnoDB free: 4096 kB`, `TODO` — each costs tokens on every question
  and buys nothing.

A comment is **not** derived from the data: it does not change when a row
changes, and it is exactly as much "customer data" as a column name. So it
travels with structure and is captured regardless of `HintBudget` — which gates
counts, ranges and value lists, all of which *are* read out of the data.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Any

# Stored caps. The render caps (plan §4.4) are tighter still and belong to the
# renderer: what is worth keeping in a snapshot and what is worth spending
# tokens on are two different questions, and only the second one has a budget.
COMMENT_MAX_CHARS_TABLE = 400
COMMENT_MAX_CHARS_COLUMN = 240

# Comments that are structurally worthless whatever they are attached to.
_NOISE_EXACT = frozenset({
    "n/a", "na", "none", "null", "todo", "tbd", "tba", "test", "temp", "tmp",
    "view", "table", "column", "no comment", "no description", "-", "--", "?",
    "xxx", "x", "placeholder", "deprecated?",
})

# MySQL's `TABLE_COMMENT` is not always a comment: InnoDB has historically
# appended storage chatter to it, and the column reads `VIEW` for a view. It is
# dirty input on every version, so it gets its own rule rather than a hope.
_INNODB_CHATTER = re.compile(r"^innodb\s+free:?\s*\d+", re.IGNORECASE)

# Everything that is not a letter or a digit, for the "is this just the object's
# own name?" test. `order_items`, `Order Items` and `ORDER-ITEMS` are one name.
_NON_ALNUM = re.compile(r"[^\w]+", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

# The two format characters that are *orthography*, not formatting.
#
# Unicode files ZWNJ and ZWJ under `Cf`, alongside the bidi overrides and the
# zero-width space — and stripping the category wholesale silently rewrites
# Persian: `سفارش‌ها` ("orders") becomes `سفارش ها`, two words that are not the
# word. Half this product's users write Persian, and a comment mangled at
# capture is mangled in the prompt, in the semantic layer and in the UI forever
# after. Everything else in `Cf` still goes, bidi overrides included: those are
# a spoofing vector and carry no meaning inside a one-line description.
_KEPT_FORMAT_CHARS = frozenset({"‌", "‍"})


# ── the system schemas nobody asked about ────────────────────────────────
# Point a broad allowlist — or a privileged account — at any of these engines
# and the snapshot fills with the engine's own dictionary: forty tables about
# Oracle, or `pg_catalog` next to `sales`. Every downstream cost is per table
# (a retrieve budget, one model call per table in the semantic generator), so
# this is the cheapest filter in the product and it belongs at the bottom.
#
# Oracle is the reason this exists — a schema there is a *user*, and a
# production instance carries dozens of them — but every engine benefits.
SYSTEM_SCHEMAS: dict[str, frozenset[str]] = {
    "postgres": frozenset({"pg_catalog", "information_schema", "pg_toast"}),
    "mysql": frozenset({
        "mysql", "information_schema", "performance_schema", "sys",
    }),
    "mssql": frozenset({
        "sys", "information_schema", "guest", "db_owner", "db_accessadmin",
        "db_securityadmin", "db_ddladmin", "db_backupoperator",
        "db_datareader", "db_datawriter", "db_denydatareader",
        "db_denydatawriter",
    }),
    "oracle": frozenset({
        "sys", "system", "sysaux", "xdb", "mdsys", "ctxsys", "wmsys", "outln",
        "dbsnmp", "audsys", "lbacsys", "olapsys", "dvsys", "dvf", "orddata",
        "ordsys", "ordplugins", "ojvmsys", "gsmadmin_internal", "appqossys",
        "sysbackup", "sysdg", "syskm", "sysrac", "si_informtn_schema",
        "oracle_ocm", "remote_scheduler_agent", "dbsfwuser", "ggsys",
        "anonymous", "pdbadmin", "public",
    }),
}

# Prefixes, because these families are unbounded: Postgres numbers its temp
# schemas per backend, Oracle's APEX and ORDS ship versioned schema names.
_SYSTEM_PREFIXES: dict[str, tuple[str, ...]] = {
    "postgres": ("pg_temp_", "pg_toast_temp_", "pg_"),
    "mysql": (),
    "mssql": (),
    # `C##…` — an Oracle common user — is deliberately *not* here: in a
    # multitenant setup a common user can legitimately own business tables, and
    # dropping a schema somebody allowlisted on purpose is worse than carrying
    # one they did not.
    "oracle": ("apex_", "ords_", "flows_"),
}


def is_system_schema(dialect: str, name: str) -> bool:
    """Whether this schema is the engine talking about itself."""
    lowered = (name or "").strip().casefold()
    if not lowered:
        return False
    if lowered in SYSTEM_SCHEMAS.get(dialect, frozenset()):
        return True
    return lowered.startswith(_SYSTEM_PREFIXES.get(dialect, ()))


def business_schemas(dialect: str, names: Sequence[str]) -> list[str]:
    """The caller's schema list with the engine's own dictionaries removed.

    **Unless that would leave nothing.** An empty allowlist means an empty
    snapshot, and an empty snapshot is a connection that can answer no question
    at all — the guard resolves every name against it and rejects what it cannot
    find. Somebody who deliberately pointed DataMind at `SYS`, or who connects
    to Oracle *as* `SYSTEM` (where the allowlist defaults to the connecting
    user's own schema), is better served by what they asked for than by silence
    they have no way to diagnose.
    """
    kept = [name for name in names if not is_system_schema(dialect, name)]
    return kept or list(names)


# ── cleaning one comment ─────────────────────────────────────────────────
def clean_comment(
    raw: Any, *, limit: int = COMMENT_MAX_CHARS_TABLE, name: str | None = None
) -> str | None:
    """One catalog value in; a single clean line, or `None`, out.

    `None` means "there is nothing here worth storing" — for an empty comment,
    for one that is only the object's own name, and for a value that is really
    the engine's storage chatter. The caller stores nothing rather than storing
    a blank, which is what keeps a snapshot from a database with no comments
    byte-identical to one taken before this feature existed.
    """
    text = _as_text(raw)
    if text is None:
        return None

    # Control characters go first: a comment can legally contain a newline, a
    # tab, or a stray NUL from a bad import, and every one of them is a way to
    # forge structure in a prompt or to break a JSON round trip.
    text = "".join(
        ch
        if ch in _KEPT_FORMAT_CHARS
        or unicodedata.category(ch) not in ("Cc", "Cf", "Zl", "Zp")
        else " "
        for ch in text
    )
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        return None

    text = _truncate(text, limit)
    if is_noise(text, name=name):
        return None
    return text


def is_noise(text: str, *, name: str | None = None) -> bool:
    """Whether a comment says less than the thing it is attached to already does.

    Not a judgement about quality — a terse comment is still a comment. It
    rejects only the four things that are reliably worthless: an engine's own
    storage chatter, a marker like `TODO`, a string with no letters or digits in
    it at all, and the object's own name spelled differently.
    """
    stripped = text.strip()
    if not stripped:
        return True
    if _INNODB_CHATTER.match(stripped):
        return True
    if stripped.casefold() in _NOISE_EXACT:
        return True
    # Punctuation only: "---", "***", ".". Anything with a letter or a digit in
    # it survives, including a comment in a non-Latin script.
    if not any(ch.isalnum() for ch in stripped):
        return True
    return bool(name and _slug(stripped) == _slug(name))


def _as_text(raw: Any) -> str | None:
    """Decode whatever the driver handed back.

    SQL Server returns `sql_variant` (cast to `nvarchar` in the query, but a
    `bytes` on some collations), MySQL hands back `bytes` when no charset is
    negotiated, and Oracle can return a LOB handle for a long comment. A value
    that cannot be read is not an error — it is a comment we do not have.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw or None
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return bytes(raw).decode("utf-8", errors="replace") or None
    read = getattr(raw, "read", None)
    if callable(read):
        try:
            value = read()
        except Exception:  # noqa: BLE001 — a LOB that will not read is not a comment
            return None
        return _as_text(value)
    return str(raw) or None


def _truncate(text: str, limit: int) -> str:
    """Cut on a word boundary and mark it, so nothing reads as a whole sentence.

    A comment cut mid-word looks like corruption; a comment cut mid-sentence
    with no mark reads as the DBA's complete thought, which is worse — the
    second half is where "…except for refunds" lives.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    window = text[: limit - 1]
    cut = window.rfind(" ")
    if cut > limit // 2:
        window = window[:cut]
    return window.rstrip(" ,;:-") + "…"


# ── folding an engine's rows into engine-neutral maps ────────────────────
# Every engine's comment query is written to return the same column order, so
# one fold serves all four and each connector supplies only the SQL. Rows are
# indexed positionally: asyncpg hands back `Record`, aiomysql and pymssql plain
# tuples, oracledb tuples — position is the one thing they all agree on.
def fold_table_comments(
    rows: Iterable[Sequence[Any]],
) -> dict[tuple[str, str], str]:
    """`(schema, table, comment)` rows -> `{(schema, table): comment}`."""
    folded: dict[tuple[str, str], str] = {}
    for row in rows:
        if len(row) < 3:
            continue
        schema, table = _text(row[0]), _text(row[1])
        if not schema or not table:
            continue
        comment = clean_comment(
            row[2], limit=COMMENT_MAX_CHARS_TABLE, name=table
        )
        if comment:
            folded[(schema, table)] = comment
    return folded


def fold_column_comments(
    rows: Iterable[Sequence[Any]],
) -> dict[tuple[str, str, str], str]:
    """`(schema, table, column, comment)` rows -> `{(s, t, c): comment}`."""
    folded: dict[tuple[str, str, str], str] = {}
    for row in rows:
        if len(row) < 4:
            continue
        schema, table, column = _text(row[0]), _text(row[1]), _text(row[2])
        if not schema or not table or not column:
            continue
        comment = clean_comment(
            row[3], limit=COMMENT_MAX_CHARS_COLUMN, name=column
        )
        if comment:
            folded[(schema, table, column)] = comment
    return folded


def fold_schema_comments(rows: Iterable[Sequence[Any]]) -> dict[str, str]:
    """`(schema, comment)` rows -> `{schema: comment}`."""
    folded: dict[str, str] = {}
    for row in rows:
        if len(row) < 2:
            continue
        schema = _text(row[0])
        if not schema:
            continue
        comment = clean_comment(
            row[1], limit=COMMENT_MAX_CHARS_TABLE, name=schema
        )
        if comment:
            folded[schema] = comment
    return folded


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _slug(value: str) -> str:
    """`Order Items` and `order_items` are the same name."""
    return _NON_ALNUM.sub("", value.replace("_", "")).casefold()


__all__ = [
    "COMMENT_MAX_CHARS_COLUMN",
    "COMMENT_MAX_CHARS_TABLE",
    "SYSTEM_SCHEMAS",
    "business_schemas",
    "clean_comment",
    "fold_column_comments",
    "fold_schema_comments",
    "fold_table_comments",
    "is_noise",
    "is_system_schema",
]
