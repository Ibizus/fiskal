"""profile settings.

Revision ID: d5e9a2c7f418
Revises: b7d4e1f20a93
Create Date: 2026-11-28 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d5e9a2c7f418"
down_revision: Union[str, None] = "b7d4e1f20a93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "profile_settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.String(length=500), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_profile_settings")),
    )


def downgrade() -> None:
    # Refuse to discard saved settings: they cannot be reconstructed.
    if not context.is_offline_mode():
        bind = op.get_bind()
        if bind.exec_driver_sql("SELECT 1 FROM profile_settings LIMIT 1").first():
            raise RuntimeError(
                "Downgrading would discard profile settings; it must be done "
                "on a database with no saved settings."
            )

    op.drop_table("profile_settings")
