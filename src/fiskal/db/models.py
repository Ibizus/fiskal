"""SQLAlchemy ORM models persisted in the database."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from fiskal.db.base import Base


class Transaction(Base):
    """A single movement of money.

    This is a minimal placeholder entity for phase 0 so that Alembic has a
    model to track. It stores no personal or bank-specific data.
    """

    __tablename__ = "transactions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    currency: Mapped[str] = mapped_column(String(3))
    description: Mapped[str] = mapped_column(String(255))
