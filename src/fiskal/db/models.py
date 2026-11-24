"""SQLAlchemy ORM models persisted in the database.

Imported bank fields (dates, amounts, descriptions, balance) are intentionally
separated from editable classification fields (cost center, category, origin,
status, proration period, note). Imported fields are immutable by design; only
the classification fields and the note may be edited after import.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from fiskal.db.base import Base
from fiskal.domain.enums import (
    CategoryKind,
    CategoryNature,
    ClassificationOrigin,
    TransactionStatus,
    TransactionType,
)


def _transaction_type_values() -> tuple[str, ...]:
    return tuple(member.value for member in TransactionType)


def _transaction_status_values() -> tuple[str, ...]:
    return tuple(member.value for member in TransactionStatus)


def _category_kind_values() -> tuple[str, ...]:
    return tuple(member.value for member in CategoryKind)


def _category_nature_values() -> tuple[str, ...]:
    return tuple(member.value for member in CategoryNature)


def _classification_origin_values() -> tuple[str, ...]:
    return tuple(member.value for member in ClassificationOrigin)


class Account(Base):
    """A bank or cash account where money moves."""

    __tablename__ = "accounts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255))
    currency_code: Mapped[str] = mapped_column(String(3))
    default_cost_center_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cost_centers.id"),
    )
    account_hint: Mapped[str | None] = mapped_column(String(4))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        CheckConstraint(
            "length(account_hint) <= 4",
            name="ck_accounts_account_hint_length",
        ),
    )


class CostCenter(Base):
    """A grouping that answers "who or what does this belong to?"."""

    __tablename__ = "cost_centers"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name_key: Mapped[str | None] = mapped_column(String(255))
    custom_name: Mapped[str | None] = mapped_column(String(255))
    group_label: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    attributes: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)

    __table_args__ = (
        CheckConstraint(
            "name_key IS NOT NULL OR custom_name IS NOT NULL",
            name="ck_cost_centers_name_present",
        ),
    )


class Category(Base):
    """A grouping that answers "what is this?"."""

    __tablename__ = "categories"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name_key: Mapped[str | None] = mapped_column(String(255))
    custom_name: Mapped[str | None] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(10))
    nature: Mapped[str] = mapped_column(String(10))
    default_proration_months: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        CheckConstraint(
            "name_key IS NOT NULL OR custom_name IS NOT NULL",
            name="ck_categories_name_present",
        ),
        CheckConstraint(
            f"kind IN {_category_kind_values()}",
            name="ck_categories_kind",
        ),
        CheckConstraint(
            f"nature IN {_category_nature_values()}",
            name="ck_categories_nature",
        ),
        CheckConstraint(
            "default_proration_months IS NULL OR default_proration_months > 0",
            name="ck_categories_proration_months_positive",
        ),
    )


class Import(Base):
    """One act of reading a downloaded bank file and creating or matching rows."""

    __tablename__ = "imports"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"))
    file_name: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(255))
    imported_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
    )
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    first_transaction_date: Mapped[date | None] = mapped_column(Date)
    last_transaction_date: Mapped[date | None] = mapped_column(Date)
    continuity_ok: Mapped[bool] = mapped_column(Boolean, default=True)


class Transaction(Base):
    """A single movement of money.

    Imported bank fields are kept separate from classification fields.
    ``operation_date``, ``value_date``, ``original_description``,
    ``normalized_description``, ``counterparty``, ``amount_minor``,
    ``balance_minor`` and ``currency_code`` come from the bank and are
    immutable by design. ``cost_center_id``, ``category_id``, ``origin``,
    ``status``, ``proration_start_date``, ``proration_end_date`` and ``note``
    are editable classification metadata.

    ``origin`` is only meaningful once ``category_id`` is set: an empty
    ``category_id`` means the transaction is unclassified, whatever ``origin``
    holds (imports store a placeholder). Classification code must key off
    ``category_id``, never off ``origin``.

    Two transactions that form an internal transfer are linked through
    ``paired_transaction_id``. A transaction can belong to at most one pair;
    this is enforced by the application because a single self-referential
    nullable foreign key cannot express mutual exclusivity on its own.
    """

    __tablename__ = "transactions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"))
    import_id: Mapped[UUID | None] = mapped_column(ForeignKey("imports.id"))
    operation_date: Mapped[date] = mapped_column(Date)
    value_date: Mapped[date] = mapped_column(Date)
    original_description: Mapped[str] = mapped_column(String(500))
    normalized_description: Mapped[str] = mapped_column(String(500))
    counterparty: Mapped[str | None] = mapped_column(String(255))
    amount_minor: Mapped[int] = mapped_column(Integer)
    balance_minor: Mapped[int | None] = mapped_column(Integer)
    currency_code: Mapped[str] = mapped_column(String(3))
    transaction_type: Mapped[str] = mapped_column("type", String(20))
    cost_center_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cost_centers.id"),
    )
    category_id: Mapped[UUID | None] = mapped_column(ForeignKey("categories.id"))
    origin: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(
        String(20), default=TransactionStatus.PENDING.value
    )
    proration_start_date: Mapped[date | None] = mapped_column(Date)
    proration_end_date: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(1000))
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    identity_key: Mapped[str] = mapped_column(String(255))
    paired_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("transactions.id"),
    )

    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "identity_key",
            name="uq_transactions_identity_per_account",
        ),
        Index(
            "ix_transactions_paired_transaction_id",
            "paired_transaction_id",
            unique=True,
        ),
        CheckConstraint(
            f"type IN {_transaction_type_values()}",
            name="ck_transactions_type",
        ),
        CheckConstraint(
            f"status IN {_transaction_status_values()}",
            name="ck_transactions_status",
        ),
        CheckConstraint(
            f"origin IN {_classification_origin_values()}",
            name="ck_transactions_origin",
        ),
        CheckConstraint(
            "proration_end_date IS NULL OR proration_start_date IS NULL OR "
            "proration_end_date >= proration_start_date",
            name="ck_transactions_proration_period",
        ),
    )


class RejectedPair(Base):
    """A pair of transactions the user explicitly chose not to link.

    Storing the rejection prevents the same proposal from surfacing again on
    later imports. The two transaction ids are stored in a deterministic order
    so the pair has a single canonical row.
    """

    __tablename__ = "rejected_pairs"

    left_transaction_id: Mapped[UUID] = mapped_column(
        ForeignKey("transactions.id"),
        primary_key=True,
    )
    right_transaction_id: Mapped[UUID] = mapped_column(
        ForeignKey("transactions.id"),
        primary_key=True,
    )
    rejected_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
    )


class ClosedMonth(Base):
    """A month that has been explicitly closed for an account.

    Nothing may be imported or reclassified in a closed month until it is
    reopened. The month is stored as the first day of the month.
    """

    __tablename__ = "closed_months"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"))
    month: Mapped[date] = mapped_column(Date)
    closed_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "month",
            name="uq_closed_months_account_month",
        ),
    )
