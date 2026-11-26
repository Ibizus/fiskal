"""guided history actions.

Revision ID: e1a7c4b9d302
Revises: d5e9a2c7f418
Create Date: 2026-11-28 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1a7c4b9d302"
down_revision: Union[str, None] = "d5e9a2c7f418"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "guided_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("normalized_description", sa.String(length=500), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=True),
        sa.Column("undone_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "direction IN ('income', 'expense')",
            name="ck_guided_actions_direction",
        ),
        sa.CheckConstraint(
            "row_count >= 0",
            name="ck_guided_actions_row_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("guided_action_id", sa.Uuid(), nullable=True))
        batch_op.create_index(
            "ix_transactions_guided_action_id", ["guided_action_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_transactions_guided_action_id",
            "guided_actions",
            ["guided_action_id"],
            ["id"],
        )


def downgrade() -> None:
    # Refuse to discard the audit link: without it, rows validated in blocks
    # could no longer be undone and the action history would be lost.
    if not context.is_offline_mode():
        bind = op.get_bind()
        if bind.exec_driver_sql("SELECT 1 FROM guided_actions LIMIT 1").first():
            raise RuntimeError(
                "Downgrading would discard the guided action history; it must "
                "be done on a database with no guided actions."
            )

    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_constraint("fk_transactions_guided_action_id", type_="foreignkey")
        batch_op.drop_index("ix_transactions_guided_action_id")
        batch_op.drop_column("guided_action_id")
    op.drop_table("guided_actions")
