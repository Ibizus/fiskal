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
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from fiskal.db.base import Base
from fiskal.domain.enums import (
    CategoryKind,
    CategoryNature,
    ClassificationOrigin,
    IncomeSource,
    RuleMatchMode,
    RuleOrigin,
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


def _rule_mode_values() -> tuple[str, ...]:
    return tuple(member.value for member in RuleMatchMode)


def _rule_origin_values() -> tuple[str, ...]:
    return tuple(member.value for member in RuleOrigin)


def _income_source_values() -> tuple[str, ...]:
    return tuple(member.value for member in IncomeSource)


class Person(Base):
    """A holder (person) who owns accounts and can be attributed income.

    Persons are lightweight: a name is the only required field. They are never
    seeded; each profile creates its own holders.
    """

    __tablename__ = "persons"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255))


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


class AccountHolder(Base):
    """The many-to-many link between an account and its holders.

    An account has one or more holders; the same person may hold several
    accounts. Both columns form the primary key, so a link is never duplicated.
    """

    __tablename__ = "account_holders"

    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id"), primary_key=True
    )
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"), primary_key=True)


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
    income_source: Mapped[str | None] = mapped_column(String(10))
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
            f"income_source IN {_income_source_values()}",
            name="ck_categories_income_source",
        ),
        CheckConstraint(
            "default_proration_months IS NULL OR default_proration_months > 0",
            name="ck_categories_proration_months_positive",
        ),
    )


class Rule(Base):
    """A configurable condition that assigns type, cost center and category.

    The text condition matches normalized fields only (the normalized
    description and the counterparty), never raw bank text. ``mode`` selects
    the case-insensitive ``contains`` match or the optional regex advanced
    mode. Amount and day-of-month ranges further narrow the match. User rules
    always beat seed rules; within each group the numeric ``priority`` decides
    and the first match wins, with a deterministic tie-break on ``id``.

    ``cost_center_id`` is optional: a rule that leaves it unset lets the
    account's default cost center fill it as the last classification layer.
    ``category_id`` is required because a rule always classifies a transaction,
    and ``origin`` is only meaningful once a category is set. ``person_id``
    optionally attributes the transaction to a holder; it is never a default.
    """

    __tablename__ = "rules"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    priority: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(10))
    pattern: Mapped[str] = mapped_column(String(500))
    amount_min_minor: Mapped[int | None] = mapped_column(Integer)
    amount_max_minor: Mapped[int | None] = mapped_column(Integer)
    day_of_month_min: Mapped[int | None] = mapped_column(Integer)
    day_of_month_max: Mapped[int | None] = mapped_column(Integer)
    transaction_type: Mapped[str] = mapped_column(String(20))
    cost_center_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cost_centers.id"),
    )
    category_id: Mapped[UUID] = mapped_column(ForeignKey("categories.id"))
    person_id: Mapped[UUID | None] = mapped_column(ForeignKey("persons.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    origin: Mapped[str] = mapped_column(String(10))
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        CheckConstraint(
            f"mode IN {_rule_mode_values()}",
            name="ck_rules_mode",
        ),
        CheckConstraint(
            f"origin IN {_rule_origin_values()}",
            name="ck_rules_origin",
        ),
        CheckConstraint(
            f"transaction_type IN {_transaction_type_values()}",
            name="ck_rules_type",
        ),
        CheckConstraint(
            "amount_min_minor IS NULL OR amount_max_minor IS NULL OR "
            "amount_min_minor <= amount_max_minor",
            name="ck_rules_amount_range",
        ),
        CheckConstraint(
            "day_of_month_min IS NULL OR day_of_month_max IS NULL OR "
            "day_of_month_min <= day_of_month_max",
            name="ck_rules_day_range",
        ),
        CheckConstraint(
            "day_of_month_min IS NULL OR "
            "(day_of_month_min >= 1 AND day_of_month_min <= 31)",
            name="ck_rules_day_min_bounds",
        ),
        CheckConstraint(
            "day_of_month_max IS NULL OR "
            "(day_of_month_max >= 1 AND day_of_month_max <= 31)",
            name="ck_rules_day_max_bounds",
        ),
        CheckConstraint(
            "hit_count >= 0",
            name="ck_rules_hit_count_nonnegative",
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

    ``validated_by_rule`` marks a transaction that a rule created by the owner
    validated automatically (``status`` validated, ``origin`` rule). It is the
    audit marker that separates those rows from ones the owner validated by
    hand: only marked rows follow later rule changes, and only they can be sent
    back to the queue. A manual validation or correction clears it.

    ``person_id`` optionally attributes the transaction to a holder. It is
    only used for income reporting at first, has no default from the account's
    holders, and a ``NULL`` value means the transaction is not personal.

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
    person_id: Mapped[UUID | None] = mapped_column(ForeignKey("persons.id"))
    origin: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(
        String(20), default=TransactionStatus.PENDING.value
    )
    proration_start_date: Mapped[date | None] = mapped_column(Date)
    proration_end_date: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(1000))
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    validated_by_rule: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    identity_key: Mapped[str] = mapped_column(String(255))
    paired_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("transactions.id"),
    )
    rule_id: Mapped[UUID | None] = mapped_column(ForeignKey("rules.id"))

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
