"""Semantic layer versions: every document that reached the model is kept.

Phase 1 of `docs/plans/semantic-layer-model.md`, which numbered this `0030`;
`0030` and `0031` went to the usage screen while the plan was being written.

`semantic_layers` held one row per connection and `PUT` overwrote it in place:
no version, no author, no note, no restore, and a `REPLACE` generation or a
wrong save could not be undone. Four changes:

* **`semantic_layers` gains `revision` and `published_version`.** `revision`
  increases on every write to the row and is the concurrency token a writer
  must present; `published_version` names which version `document` is a copy
  of. `document` keeps its meaning — *what the model reads* — so none of its
  readers change (D3).
* **`semantic_layer_versions`** — immutable, numbered per connection, linear.
  A version is a document that reached the model (D2). `document_sha256` is
  over the canonical JSON, and a test asserts it equals the hash of the head
  row's `document` after every write path.
* **`semantic_layer_changes`** — each version's typed changes against its
  parent, one row per entry. Keys, not content: the before and after are
  recomputed from two immutable versions when somebody asks, and the history
  query only needs the index.
* **`runs` and `benchmark_runs` gain `semantic_layer_version`.** Which version
  answered. `0` means no layer reached the prompt; `NULL` means the row
  predates this migration and nothing was recorded.

**The backfill.** Every row with a non-empty document becomes version 1 with
no author (`published_by` NULL), `origin {"migrated": true}` and a note saying
nothing before it was kept. No author is invented — the rule that left the
`prompt_version` rows from before 2026-08-31 unrewritten. No change rows are
written for it: there is no parent to diff against, and inventing "added" rows
for work done before anybody recorded it would be history nobody saw happen.

**Versions are never pruned here**, because runs point at them (§11 has the
trigger for retention). They go only with their connection — `CASCADE`.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

MIGRATED_NOTE = (
    "Recorded when versioning was introduced. Nothing before this point was kept."
)


def canonical_sha256(document: dict) -> str:
    """The hash `semantic_service.document_sha256` computes, written out again.

    Duplicated rather than imported: a migration that imports application code
    stops running the day that code moves. `test_semantic_versions.py` asserts
    the two agree.
    """
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column(
        "semantic_layers",
        sa.Column("revision", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column("semantic_layers", sa.Column("published_version", sa.Integer))

    op.create_table(
        "semantic_layer_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("database_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("parent_version", sa.Integer),
        sa.Column("document", postgresql.JSONB, nullable=False),
        sa.Column("document_sha256", sa.CHAR(64), nullable=False),
        sa.Column("schema_version", sa.Integer, nullable=False),
        # SET NULL: who published is history, and a version must not vanish
        # because its author left. That is when somebody reads the history.
        sa.Column(
            "published_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "origin", postgresql.JSONB, nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("entity_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("metric_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reviewed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("issue_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "connection_id", "version", name="uq_semantic_layer_versions_number"
        ),
    )

    op.create_table(
        "semantic_layer_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "version_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("semantic_layer_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Denormalised for the history query, which filters on it first.
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("entity_key", sa.Text, nullable=False, server_default=""),
        sa.Column("item_key", sa.Text, nullable=False, server_default=""),
        sa.Column("affects_sql", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_semantic_changes_entry",
        "semantic_layer_changes",
        ["connection_id", "entity_key", "item_key"],
    )
    op.create_index(
        "ix_semantic_changes_version", "semantic_layer_changes", ["version_id"]
    )

    op.add_column("runs", sa.Column("semantic_layer_version", sa.Integer))
    op.add_column("benchmark_runs", sa.Column("semantic_layer_version", sa.Integer))

    _backfill()


def _backfill() -> None:
    bind = op.get_bind()
    layers = bind.execute(
        sa.text(
            "SELECT connection_id, document, schema_version, entity_count, "
            "metric_count, reviewed_count, issue_count FROM semantic_layers "
            "WHERE document IS NOT NULL AND document <> '{}'::jsonb"
        )
    ).mappings().all()

    insert = sa.text(
        "INSERT INTO semantic_layer_versions (id, connection_id, version, "
        "parent_version, document, document_sha256, schema_version, published_by, "
        "note, origin, entity_count, metric_count, reviewed_count, issue_count) "
        "VALUES (:id, :connection_id, 1, NULL, CAST(:document AS jsonb), :sha, "
        ":schema_version, NULL, :note, CAST(:origin AS jsonb), :entity_count, "
        ":metric_count, :reviewed_count, :issue_count)"
    )
    for layer in layers:
        document = layer["document"]
        if isinstance(document, str):  # a driver that hands JSONB back as text
            document = json.loads(document)
        bind.execute(insert, {
            "id": uuid.uuid4(),
            "connection_id": layer["connection_id"],
            "document": json.dumps(document),
            "sha": canonical_sha256(document),
            "schema_version": layer["schema_version"] or 0,
            "note": MIGRATED_NOTE,
            "origin": json.dumps({"migrated": True}),
            "entity_count": layer["entity_count"] or 0,
            "metric_count": layer["metric_count"] or 0,
            "reviewed_count": layer["reviewed_count"] or 0,
            "issue_count": layer["issue_count"] or 0,
        })
        bind.execute(
            sa.text(
                "UPDATE semantic_layers SET published_version = 1, revision = 1 "
                "WHERE connection_id = :connection_id"
            ),
            {"connection_id": layer["connection_id"]},
        )


def downgrade() -> None:
    op.drop_column("benchmark_runs", "semantic_layer_version")
    op.drop_column("runs", "semantic_layer_version")
    op.drop_index("ix_semantic_changes_version", table_name="semantic_layer_changes")
    op.drop_index("ix_semantic_changes_entry", table_name="semantic_layer_changes")
    op.drop_table("semantic_layer_changes")
    op.drop_table("semantic_layer_versions")
    op.drop_column("semantic_layers", "published_version")
    op.drop_column("semantic_layers", "revision")
