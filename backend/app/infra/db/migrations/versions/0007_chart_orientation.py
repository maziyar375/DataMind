"""Tiles: fold `horizontal_bar` into bar + orientation.

A data migration, not a schema one — `chart_config` is JSONB and its *shape*
does not change. What changes is a value inside it: the pre-consolidation
`chart_type: "horizontal_bar"` becomes `chart_type: "bar"` with
`orientation: "horizontal"`, because a vertical and a horizontal bar chart were
never two chart types. They are one mark, one comparison, one reading, and
splitting them meant `_fit`'s cardinality rule silently replaced whatever the
user had picked.

Two properties worth stating, since both are easy to get wrong here:

* **It is not the only defence.** `ChartIntent` also reads the old spelling
  directly (`_accept_legacy_horizontal_bar`), so a row this migration does not
  reach — an older API instance writing mid-rollout, a restored backup — still
  renders as the user intended rather than silently falling back to Auto. The
  migration exists to make the stored data say what it means; the validator
  exists because migrations do not run on rows that arrive afterwards.
* **It is reversible without loss.** `downgrade` folds the pair back into the
  single value, so a rollback leaves rows an older API can read. Rows written
  as vertical bars carry `orientation: "vertical"`, which the old code ignores
  as an unknown key... except that `ChartIntent` is `extra="forbid"`, which
  would make it land as Auto. So the downgrade strips the key rather than
  leaving it behind — losing a default is survivable, losing a chart is not.

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `||` on two jsonb objects is a right-biased merge, so this sets both keys
    # in one pass and touches no other key in the config.
    op.execute(
        """
        UPDATE dashboard_tiles
           SET chart_config =
               chart_config || '{"chart_type":"bar","orientation":"horizontal"}'::jsonb
         WHERE chart_config ->> 'chart_type' = 'horizontal_bar'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE dashboard_tiles
           SET chart_config =
               (chart_config || '{"chart_type":"horizontal_bar"}'::jsonb) - 'orientation'
         WHERE chart_config ->> 'chart_type' = 'bar'
           AND chart_config ->> 'orientation' = 'horizontal'
        """
    )
    # Vertical and auto were never a separate type; the key is simply dropped.
    op.execute(
        """
        UPDATE dashboard_tiles
           SET chart_config = chart_config - 'orientation'
         WHERE chart_config ? 'orientation'
        """
    )
