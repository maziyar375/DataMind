"""Feedback, the review queue, and the loop closing.

Three claims, in the order they would hurt if they broke:

* **Feedback is open to any signed-in user.** The person best placed to notice
  a wrong answer is the person who asked the question, and they are usually not
  the person allowed to fix it. Gating the *report* on the right to *repair*
  would lose exactly the reports worth having — so `POST /runs/{id}/feedback`
  does not ask `can_curate`, while resolving a flag does.
* **`became_template` reaches the person who flagged it.** A feedback control
  with no visible payoff is worse than none: people learn their thumbs-down
  goes nowhere and stop pressing it. Ship this phase without that link and it
  has shipped a suggestion box.
* **A dismissal takes a reason.** A dismissal with no note is
  indistinguishable, from the flagger's side, from being ignored.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.clock import utcnow
from app.core.config import Settings, get_settings
from app.core.context import RequestContext
from app.infra.db.models import (
    AnswerFeedback,
    Artifact,
    Conversation,
    DashboardTile,
    DatabaseConnection,
    GeneratedQuery,
    KnowledgeTemplateHit,
    KnowledgeTemplateRow,
    Message,
    ReportBlock,
    Run,
    RunStep,
    SchemaSnapshotRow,
    SemanticLayerRow,
    User,
)
from app.main import create_app

USER = uuid4()
OTHER = uuid4()
CONNECTION_ID = uuid4()
RUN_ID = uuid4()
MESSAGE_ID = uuid4()

KNOWLEDGE = f"/api/v1/connections/{CONNECTION_ID}/knowledge"
RUN = f"/api/v1/runs/{RUN_ID}"


def _connection() -> DatabaseConnection:
    return DatabaseConnection(
        id=CONNECTION_ID, owner_id=USER, name="aurora", database_type="postgres",
        host="h", port=5432, database_name="aurora", username="ro",
        encrypted_password="x", max_rows=1000, statement_timeout_ms=30_000,
        conflict_checks_enabled=True,
    )


def _run() -> Run:
    # `model_snapshot` and the timestamps are spelled out because a real row
    # gets them from the database on insert, and this one is never inserted.
    return Run(
        id=RUN_ID, conversation_id=uuid4(), user_message_id=MESSAGE_ID,
        owner_id=USER, connection_id=CONNECTION_ID, status="SUCCEEDED",
        prompt_version="v8", repair_count=0, attempt_count=1,
        model_snapshot={"provider": "test", "model": "test"},
        skip_templates=False, cancel_requested=False,
        created_at=utcnow(), updated_at=utcnow(),
    )


class FakeDb:
    """Enough of an `AsyncSession` for the capture routes."""

    def __init__(self) -> None:
        self.connection = _connection()
        self.run = _run()
        self.message = Message(
            id=MESSAGE_ID, conversation_id=self.run.conversation_id, seq=1,
            role="USER", content="total revenue last month",
        )
        self.user = User(
            id=USER, email="sara@test.local", display_name="Sara A.",
            password_hash="x", role="MEMBER", status="ACTIVE",
        )
        self.feedback: list[AnswerFeedback] = []
        self.templates: list[KnowledgeTemplateRow] = []
        self.queries: list[GeneratedQuery] = [
            GeneratedQuery(
                id=uuid4(), run_id=RUN_ID, attempt_no=1,
                raw_sql="SELECT SUM(amount) FROM sales_daily_rollup",
                rewritten_sql="SELECT SUM(amount) FROM sales_daily_rollup LIMIT 1000",
                dialect="postgres", validation_status="VALID",
                validation_report={}, referenced_tables=["public.sales_daily_rollup"],
                referenced_columns=[],
            )
        ]

    async def execute(self, statement: Any) -> Any:
        selected = statement.column_descriptions[0]
        entity, name = selected.get("entity"), selected.get("name")
        if entity is DatabaseConnection:
            owner = statement.compile().params.get("owner_id_1")
            match = (
                self.connection
                if owner is None or self.connection.owner_id == owner
                else None
            )
            return _Result(match)
        if entity is Run:
            # The owner predicate is honoured rather than ignored: scoping is a
            # property this file asserts, and a fake that answered every lookup
            # with the same row would prove it for nobody.
            owner = statement.compile().params.get("owner_id_1")
            scoped = self.run if owner is None or self.run.owner_id == owner else None
            return _Result(scoped, rows=[(self.run, self.message.content)])
        if entity in (DashboardTile, ReportBlock, RunStep, Artifact):
            return _Result(None, rows=[])
        if entity is AnswerFeedback:
            return _Result(self._one_feedback(statement), rows=self._feedback(statement))
        if entity is GeneratedQuery:
            return _Result(self.queries[-1] if self.queries else None,
                           rows=self.queries)
        if entity is KnowledgeTemplateRow:
            return _Result(None, rows=self.templates)
        if entity is KnowledgeTemplateHit:
            return _Result(None, rows=[])
        if entity is SchemaSnapshotRow:
            return _Result(None)
        if entity is SemanticLayerRow:
            return _Result(None)
        if name in ("version", "question_normalized", "run_id"):
            return _Result(None, rows=[])
        # Deliberately short: an unhandled query used to raise with the whole
        # SQLAlchemy statement in the frame, and the error logger's rich
        # traceback spent minutes rendering it.
        raise AssertionError(f"unexpected query: entity={entity} name={name}")

    def _feedback(self, statement: Any) -> list[AnswerFeedback]:
        params = statement.compile().params
        rows = list(self.feedback)
        if (user := params.get("user_id_1")) is not None:
            rows = [r for r in rows if r.user_id == user]
        if (state := params.get("state_1")) is not None:
            rows = [r for r in rows if r.state == state]
        if (verdict := params.get("verdict_1")) is not None:
            rows = [r for r in rows if r.verdict != verdict]
        return rows

    def _one_feedback(self, statement: Any) -> AnswerFeedback | None:
        rows = self._feedback(statement)
        return rows[0] if rows else None

    def add(self, obj: Any) -> None:
        obj.created_at = getattr(obj, "created_at", None) or utcnow()
        if isinstance(obj, AnswerFeedback):
            self.feedback.append(obj)
        elif isinstance(obj, KnowledgeTemplateRow):
            obj.updated_at = obj.updated_at or utcnow()
            self.templates.append(obj)

    async def flush(self) -> None:
        pass

    async def get(self, model: Any, key: UUID) -> Any:
        if model is Run:
            return self.run if key == RUN_ID else None
        if model is Message:
            return self.message if key == MESSAGE_ID else None
        if model is User:
            return self.user if key == USER else None
        if model is AnswerFeedback:
            return next((f for f in self.feedback if f.id == key), None)
        if model is KnowledgeTemplateRow:
            return next((t for t in self.templates if t.id == key), None)
        if model is Conversation:
            return None
        return None


class _Result:
    def __init__(self, value: Any, rows: list[Any] | None = None) -> None:
        self._value = value
        self._rows = rows if rows is not None else []

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalars(self) -> Any:
        return _Scalars(self._rows)

    def all(self) -> list[Any]:
        return self._rows


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


def _client(db: FakeDb, *, user: UUID = USER, admin_only: bool = False) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=user, email="u@test.local", role="MEMBER", correlation_id="t"
    )
    settings = Settings(curation_admin_only=admin_only)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[deps.get_settings] = lambda: settings
    client = TestClient(app)
    client.db = db  # type: ignore[attr-defined]
    return client


@pytest.fixture
def client() -> Any:
    return _client(FakeDb())


# ── leaving feedback ─────────────────────────────────────────────────────
def test_a_wrong_verdict_opens_a_flag(client: Any) -> None:
    response = client.post(
        f"{RUN}/feedback",
        json={"verdict": "WRONG", "comment": "this double-counts refunds"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "WRONG" and body["state"] == "OPEN"
    assert body["comment"] == "this double-counts refunds"


def test_a_correct_verdict_arrives_already_resolved(client: Any) -> None:
    """Confirming an answer *is* a resolution, by the person who confirmed it.

    Treating a ✓ as open work would put a permanent number on the tab that no
    curator could ever clear, which is how a badge stops being a signal.
    """
    body = client.post(f"{RUN}/feedback", json={"verdict": "CORRECT"}).json()
    assert body["state"] == "RESOLVED" and body["resolved_at"] is not None


def test_ask_for_review_is_its_own_verdict(client: Any) -> None:
    # "This is wrong" and "please look at this" are different asks: one is a
    # correction the flagger could make, the other is a question they cannot
    # answer. Collapsing them loses the second.
    body = client.post(f"{RUN}/feedback", json={"verdict": "NEEDS_REVIEW"}).json()
    assert body["verdict"] == "NEEDS_REVIEW" and body["state"] == "OPEN"


def test_pressing_again_is_a_change_of_mind_not_a_second_vote(client: Any) -> None:
    first = client.post(f"{RUN}/feedback", json={"verdict": "CORRECT"}).json()
    second = client.post(
        f"{RUN}/feedback", json={"verdict": "WRONG", "comment": "no, look again"}
    ).json()

    assert second["id"] == first["id"]
    assert len(client.db.feedback) == 1
    # And it reopens: the curator's earlier "resolved" answered a different flag.
    assert second["state"] == "OPEN" and second["resolved_at"] is None


def test_a_verdict_this_system_does_not_record_is_refused(client: Any) -> None:
    assert client.post(f"{RUN}/feedback", json={"verdict": "MAYBE"}).status_code == 422


def test_feedback_is_open_to_any_signed_in_user() -> None:
    """The headline claim of this phase's permission model.

    Not gated by `can_curate`, deliberately — even with `curation_admin_only`
    on, which Phase 8 made the default. Reporting a wrong answer and being
    allowed to repair it are different rights, and gating the first on the
    second loses exactly the reports worth having.
    """
    db = FakeDb()
    member = _client(db, admin_only=True)
    assert member.post(f"{RUN}/feedback", json={"verdict": "WRONG"}).status_code == 200

    # …while *resolving* a flag stays a curator's act. A stranger does not get
    # 403 but 404: the queue is routed to the connection's owner, so somebody
    # else's queue is not a thing they are refused — it is a thing they are
    # never told about.
    flag = db.feedback[0]
    stranger = _client(db, user=OTHER, admin_only=True)
    assert stranger.post(
        f"{KNOWLEDGE}/reviews/{flag.id}/resolve", json={"dismiss": True, "note": "no"}
    ).status_code == 404


def test_another_users_run_cannot_be_flagged() -> None:
    stranger = _client(FakeDb(), user=OTHER)
    assert stranger.post(f"{RUN}/feedback", json={"verdict": "WRONG"}).status_code == 404


# ── the queue ────────────────────────────────────────────────────────────
def _flag(client: Any, comment: str = "this double-counts refunds") -> dict[str, Any]:
    return client.post(
        f"{RUN}/feedback", json={"verdict": "WRONG", "comment": comment}
    ).json()


def test_the_queue_carries_the_evidence_beside_the_flag(client: Any) -> None:
    # The curator's actual job here is comparing two statements, and a queue
    # that made them click through to find the first one would not get used.
    _flag(client)
    rows = client.get(f"{KNOWLEDGE}/reviews").json()

    assert len(rows) == 1
    assert rows[0]["question"] == "total revenue last month"
    assert "sales_daily_rollup" in rows[0]["sql"]
    assert rows[0]["comment"] == "this double-counts refunds"


def test_the_queue_names_a_person_and_never_an_address(client: Any) -> None:
    # The header says who to go and ask. An email there would put a personal
    # identifier on a screen with no need for one.
    _flag(client)
    row = client.get(f"{KNOWLEDGE}/reviews").json()[0]
    assert row["flagged_by"] == "Sara A."
    assert "@" not in str(row)


def test_a_confirmed_answer_is_not_work(client: Any) -> None:
    client.post(f"{RUN}/feedback", json={"verdict": "CORRECT"})
    assert client.get(f"{KNOWLEDGE}/reviews").json() == []


# ── the loop closing ─────────────────────────────────────────────────────
def test_a_flag_that_became_a_template_says_so(client: Any) -> None:
    """`became_template` is the loop closing, as one nullable FK.

    Without it this phase has shipped a suggestion box.
    """
    flag = _flag(client)
    template = KnowledgeTemplateRow(
        id=uuid4(), connection_id=CONNECTION_ID, question="total revenue",
        question_normalized="total revenue", sql="SELECT 1", params=[], note="",
        source="CHAT_CORRECTED", literal_provenance="HUMAN_AUTHORED",
        role="RETRIEVABLE", status="ACTIVE", status_reason="", schema_version=1,
        referenced_tables=[], conflicts_with=[], hit_count=0,
    )
    client.db.add(template)

    resolved = client.post(
        f"{KNOWLEDGE}/reviews/{flag['id']}/resolve",
        json={"template_id": str(template.id)},
    )
    assert resolved.status_code == 200
    assert resolved.json()["became_template"] == str(template.id)
    assert resolved.json()["state"] == "RESOLVED"

    # And the asker sees it on their own answer.
    knowledge = client.get(f"{RUN}").json()["knowledge"]
    assert knowledge["feedback"]["became_template"] == str(template.id)


def test_a_resolution_cannot_point_at_another_connections_template(
    client: Any,
) -> None:
    # Telling the flagger their flag became knowledge that is not there would
    # be a lie in the one place the product is asking to be believed.
    flag = _flag(client)
    assert client.post(
        f"{KNOWLEDGE}/reviews/{flag['id']}/resolve",
        json={"template_id": str(uuid4())},
    ).status_code == 404


def test_a_dismissal_without_a_reason_is_refused(client: Any) -> None:
    # A dismissal with no note is indistinguishable, from the flagger's side,
    # from being ignored.
    flag = _flag(client)
    assert client.post(
        f"{KNOWLEDGE}/reviews/{flag['id']}/resolve", json={"dismiss": True}
    ).status_code == 422


def test_a_dismissal_with_a_reason_carries_it_back(client: Any) -> None:
    flag = _flag(client)
    body = client.post(
        f"{KNOWLEDGE}/reviews/{flag['id']}/resolve",
        json={"dismiss": True, "note": "The rollup is correct; refunds net out."},
    ).json()
    assert body["state"] == "DISMISSED"
    assert body["resolution_note"] == "The rollup is correct; refunds net out."


def test_the_answer_carries_this_readers_own_verdict_and_nobody_elses(
    client: Any,
) -> None:
    assert client.get(RUN).json()["knowledge"]["feedback"] is None
    _flag(client)
    assert client.get(RUN).json()["knowledge"]["feedback"]["verdict"] == "WRONG"


# ── the backlog ──────────────────────────────────────────────────────────
def test_a_flag_becomes_the_first_row_of_the_backlog(client: Any) -> None:
    _flag(client)
    rows = client.get(f"{KNOWLEDGE}/suggestions").json()

    flagged = [r for r in rows if r["kind"] == "FLAGGED"]
    assert len(flagged) == 1
    assert flagged[0]["question"] == "total revenue last month"
    assert "sales_daily_rollup" in flagged[0]["sql"]
    # A generated statement's literals were the model's choice, so a template
    # confirmed from it is gated like a sample value. `docs/security.md`.
    assert flagged[0]["model_derived"] is True


def test_the_backlog_is_readable_by_anyone_who_can_read_the_connection() -> None:
    assert _client(FakeDb(), admin_only=True).get(
        f"{KNOWLEDGE}/suggestions"
    ).status_code == 200


def test_another_users_connection_has_no_queue_and_no_backlog() -> None:
    stranger = _client(FakeDb(), user=OTHER)
    assert stranger.get(f"{KNOWLEDGE}/reviews").status_code == 404
    assert stranger.get(f"{KNOWLEDGE}/suggestions").status_code == 404
