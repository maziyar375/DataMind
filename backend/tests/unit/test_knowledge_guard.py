"""The guard's **fifth entry point**, and it gets no exemption.

DataMind has four guarded doors, and each replays the hostile corpus in a test
of its own — `test_sqlguard_hostile.py` (the pipeline), `test_query_service.py`
(a dashboard tile), `test_report_guard.py` (a report block),
`test_dashboard_transfer.py` (an imported dashboard). None of the four is
privileged, because *the moment one door is special, the guarantee is gone.*

A knowledge template is the fifth: a person types SQL into a textarea, it is
stored, and it is executed again months later against a schema that has moved.
So the whole corpus is replayed through **both** halves of the template path —
the save-time gate and the on-every-use re-validation — and this file lands in
Phase 1, before anything in Phase 2 reads from the store.

Three further claims are proven here because they are the ways this particular
door could be made special by accident:

* a **parameterized** statement is guarded, not waved through — the corpus is
  replayed a third time with a `:slot` spliced into it;
* the template policy builder agrees with `query_service`'s, so the fifth door
  cannot come to allow a table the other four reject;
* validation returns a verdict and never returns executable SQL, so nothing
  downstream can run a statement whose placeholders were never bound.
"""
from __future__ import annotations

import pytest

from app.domain.value_objects import DatabaseKind
from app.infra.db.models import DatabaseConnection
from app.knowledge import (
    KnowledgeTemplate,
    TemplateParam,
    policy_from_tables,
    validate_sql,
    validate_template,
)
from app.services.query_service import policy_from_snapshot
from app.sqlguard import GuardPolicy
from tests.unit.test_sqlguard_hostile import HOSTILE, LEGITIMATE

SNAPSHOT_TABLES = [
    {
        "schema": "public",
        "name": "orders",
        "columns": [
            {"name": "id", "data_type": "bigint", "is_primary_key": True},
            {"name": "customer_id", "data_type": "bigint"},
            {"name": "order_date", "data_type": "date"},
            {"name": "status", "data_type": "text"},
            {"name": "total_amount", "data_type": "numeric"},
        ],
    },
    {
        "schema": "public",
        "name": "order_items",
        "columns": [
            {"name": "id", "data_type": "bigint"},
            {"name": "order_id", "data_type": "bigint"},
            {"name": "product_id", "data_type": "bigint"},
            {"name": "quantity", "data_type": "integer"},
            {"name": "unit_price", "data_type": "numeric"},
        ],
    },
    {
        "schema": "public",
        "name": "products",
        "columns": [
            {"name": "id", "data_type": "bigint"},
            {"name": "name", "data_type": "text"},
            {"name": "category", "data_type": "text"},
            {"name": "price", "data_type": "numeric"},
        ],
    },
    {
        "schema": "public",
        "name": "customers",
        "columns": [
            {"name": "id", "data_type": "bigint"},
            {"name": "name", "data_type": "text"},
            {"name": "region_id", "data_type": "bigint"},
            {"name": "signed_up_at", "data_type": "timestamp"},
        ],
    },
    {
        "schema": "public",
        "name": "regions",
        "columns": [
            {"name": "id", "data_type": "bigint"},
            {"name": "name", "data_type": "text"},
        ],
    },
]

POLICY = policy_from_tables(SNAPSHOT_TABLES, dialect="postgres", max_rows=1000)


# ── door five: save time ─────────────────────────────────────────────────
@pytest.mark.parametrize("sql,expected_code", HOSTILE)
def test_hostile_sql_cannot_be_saved_as_a_template(
    sql: str, expected_code: str | None
) -> None:
    verdict = validate_sql(sql, POLICY)

    assert not verdict.valid, f"BYPASS — a template was accepted for: {sql!r}"
    assert verdict.report.status == "REJECTED"
    assert verdict.report.errors, f"Rejected without a reason: {sql!r}"

    if expected_code is not None:
        codes = {issue.rule_id for issue in verdict.report.errors}
        assert expected_code in codes, (
            f"Expected {expected_code} for {sql!r}, got {sorted(codes)}"
        )


# ── door five: every use ─────────────────────────────────────────────────
@pytest.mark.parametrize("sql,_expected_code", HOSTILE)
def test_hostile_sql_is_rejected_again_on_every_use(
    sql: str, _expected_code: str | None
) -> None:
    """A stored row is re-guarded against the schema as it is *now*.

    The point of the second pass is a template that was legal when it was
    written: the schema moves and it stops being legal. But the pass has to be
    the *same* pass, so the corpus goes through it too — a re-validation that
    trusted the store would be the exemption this file exists to deny.
    """
    stored = KnowledgeTemplate(question="taught", sql=sql)
    assert not validate_template(stored, POLICY).valid


@pytest.mark.parametrize("sql", LEGITIMATE)
def test_legitimate_analytics_sql_can_be_taught(sql: str) -> None:
    verdict = validate_sql(sql, POLICY)
    assert verdict.valid, (
        f"False rejection of teachable SQL {sql!r}: "
        f"{[(i.rule_id, i.message) for i in verdict.report.errors]}"
    )
    assert verdict.referenced_tables


# ── door five, parameterized ─────────────────────────────────────────────
@pytest.mark.parametrize("sql,_expected_code", HOSTILE)
def test_hostile_sql_is_still_rejected_when_it_carries_a_slot(
    sql: str, _expected_code: str | None
) -> None:
    """Splicing a slot into a hostile statement buys it nothing.

    This is the shape of attack the fifth door uniquely invites — *"the guard
    has seen my SQL, but not with the parameter in it"* — so the corpus is
    replayed a third time with a placeholder where a literal was.
    """
    template = KnowledgeTemplate(
        question="taught {x}",
        sql=sql.replace("1=1", "1=:x") if "1=1" in sql else sql,
        params=[TemplateParam(name="x")] if "1=1" in sql else [],
    )
    assert not validate_template(template, POLICY).valid


def test_a_parameterized_statement_is_parsed_and_guarded_not_waved_through() -> None:
    ok = validate_sql(
        "SELECT SUM(total_amount) FROM orders WHERE status = :status", POLICY
    )
    assert ok.valid and ok.placeholders == ["status"]

    # The same shape against a table this connection does not have. A slot in
    # the statement must not buy it a pass.
    blocked = validate_sql("SELECT * FROM users WHERE id = :id", POLICY)
    assert not blocked.valid
    assert "E_TABLE_NOT_ALLOWED" in {i.rule_id for i in blocked.report.errors}


def test_a_slot_cannot_smuggle_sql_because_it_is_not_text_substitution() -> None:
    # `:region` is an AST node, not a hole in a string. There is no rendering
    # of the stored statement in which a *value* becomes SQL, which is why
    # binding in Phase 2 replaces the node rather than formatting a string.
    verdict = validate_sql(
        "SELECT id FROM orders WHERE status = :status", POLICY
    )
    assert verdict.valid
    assert verdict.placeholders == ["status"]


def test_a_placeholder_inside_a_string_literal_is_text_not_a_slot() -> None:
    # A regex over the statement would declare `:status` here and produce a
    # parameter that can never bind. Reading it off the parse cannot.
    verdict = validate_sql(
        "SELECT id FROM orders WHERE status = ':status'", POLICY
    )
    assert verdict.valid and verdict.placeholders == []


# ── the two halves must not drift apart ──────────────────────────────────
def test_the_template_policy_agrees_with_the_one_every_other_door_uses() -> None:
    """Two builders is how one of them silently stops matching the guard.

    `app.knowledge` may not import `app.services`, so it has a policy builder
    of its own. This is the test that keeps the copy honest: same snapshot,
    same connection, same allowlist — and therefore the same verdict on the
    whole corpus.
    """
    connection = DatabaseConnection(
        name="sales", database_type="postgres", host="h", port=5432,
        database_name="sales", username="ro", encrypted_password="x",
        max_rows=1000, statement_timeout_ms=30_000,
    )
    theirs = policy_from_snapshot(
        {"tables": SNAPSHOT_TABLES, "dialect": "postgres"}, connection
    )
    ours = policy_from_tables(
        SNAPSHOT_TABLES,
        dialect=DatabaseKind(connection.database_type).sqlglot_dialect,
        max_rows=connection.max_rows,
    )
    assert ours.allowed_tables == theirs.allowed_tables
    assert ours.allowed_columns == theirs.allowed_columns
    assert ours.dialect == theirs.dialect and ours.max_rows == theirs.max_rows


def test_an_unsynced_connection_can_teach_nothing() -> None:
    # The same property `test_sqlguard_hostile` asserts for the pipeline: an
    # empty allowlist rejects everything, so a connection nobody has synced
    # cannot have a template authored against it.
    empty = policy_from_tables([], dialect="postgres")
    assert not validate_sql("SELECT id FROM orders", empty).valid


# ── the verdict carries no executable statement ──────────────────────────
def test_validation_never_hands_back_something_to_run() -> None:
    """`guard()` rewrites; this door deliberately throws the rewrite away.

    A parameterized statement rendered for Postgres spells its placeholders
    `%(name)s` — a driver's binding syntax. Handing that to a caller as
    "executable" is the one way this path could produce a statement nobody
    meant to run, so the verdict has no field to put it in.
    """
    verdict = validate_sql(
        "SELECT SUM(total_amount) FROM orders WHERE status = :status", POLICY
    )
    assert not hasattr(verdict, "executable")
    assert "sql" not in verdict.model_dump()


# ── the slots and the parameters have to agree ───────────────────────────
def test_a_declared_parameter_the_sql_never_uses_is_rejected() -> None:
    template = KnowledgeTemplate(
        question="revenue for {region}",
        sql="SELECT SUM(total_amount) FROM orders",
        params=[TemplateParam(name="region")],
    )
    verdict = validate_template(template, POLICY)
    assert not verdict.valid
    assert "E_PARAM_MISMATCH" in {i.rule_id for i in verdict.report.errors}


def test_a_slot_in_the_sql_that_is_not_declared_is_rejected() -> None:
    template = KnowledgeTemplate(
        question="revenue",
        sql="SELECT SUM(total_amount) FROM orders WHERE status = :status",
    )
    verdict = validate_template(template, POLICY)
    assert not verdict.valid
    assert "E_PARAM_MISMATCH" in {i.rule_id for i in verdict.report.errors}


def test_a_template_whose_slots_agree_is_accepted() -> None:
    template = KnowledgeTemplate(
        question="revenue for {status}",
        sql="SELECT SUM(total_amount) FROM orders WHERE status = :status",
        params=[TemplateParam(name="status")],
    )
    assert validate_template(template, POLICY).valid


# ── drift is told apart from illegality ──────────────────────────────────
def test_a_missing_column_reads_as_drift_not_as_an_illegal_query() -> None:
    """The fix differs, so the report must too.

    "Re-sync the connection, then edit the SQL" and "this query is not allowed"
    are different sentences, and a UI that cannot tell them apart shows the
    wrong one. Same rule set `query_service` uses for a tile.
    """
    verdict = validate_sql("SELECT o.gone FROM orders o", POLICY)
    assert not verdict.valid and verdict.drifted

    illegal = validate_sql("SELECT pg_sleep(1)", GuardPolicy(dialect="postgres"))
    assert not illegal.valid and not illegal.drifted
