"""transaction validated by rule marker.

Revision ID: b7d4e1f20a93
Revises: a3f8c2e6b1d4
Create Date: 2026-11-27 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7d4e1f20a93"
down_revision: Union[str, None] = "a3f8c2e6b1d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "validated_by_rule",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )


def downgrade() -> None:
    # Refuse to discard the marker: without it, rows validated automatically by
    # a rule would look validated by hand and lose their rule ownership.
    if not context.is_offline_mode():
        bind = op.get_bind()
        if bind.exec_driver_sql(
            "SELECT 1 FROM transactions WHERE validated_by_rule = 1 LIMIT 1"
        ).first():
            raise RuntimeError(
                "Downgrading would discard the validated-by-rule marker; it "
                "must be done on a database with no rule-validated "
                "transactions."
            )

    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_column("validated_by_rule")
