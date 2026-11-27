"""optional description on cost centers and categories.

Revision ID: f2b8d6a4c1e7
Revises: e1a7c4b9d302
Create Date: 2026-11-29 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f2b8d6a4c1e7"
down_revision: Union[str, None] = "e1a7c4b9d302"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("cost_centers", "categories"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("description", sa.String(length=1000), nullable=True)
            )


def downgrade() -> None:
    # Refuse to discard descriptions the owner wrote.
    if not context.is_offline_mode():
        bind = op.get_bind()
        for table in ("cost_centers", "categories"):
            if bind.exec_driver_sql(
                f"SELECT 1 FROM {table} WHERE description IS NOT NULL LIMIT 1"
            ).first():
                raise RuntimeError(
                    "Downgrading would discard the descriptions written on "
                    "cost centers or categories; clear them first."
                )

    for table in ("categories", "cost_centers"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("description")
