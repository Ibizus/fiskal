"""Reusable report queries and filters.

All reports are built directly over the persisted transaction table; none
stores its own totals.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from fiskal.db.models import Category, Transaction
from fiskal.domain.enums import TransactionType

_INCOME_EXPENSE_TYPES = (
    TransactionType.EXPENSE.value,
    TransactionType.INCOME.value,
    TransactionType.REFUND.value,
)


def income_expense_transactions() -> Select[tuple[Transaction]]:
    """Return a query for transactions that count in income/expense totals.

    Internal transfers and savings/investment movements are excluded because
    they are neutral or reported in separate blocks.
    """
    return select(Transaction).where(
        Transaction.transaction_type.in_(_INCOME_EXPENSE_TYPES)
    )


def income_expense_total_minor(session: Session) -> int:
    """Return the signed net total of all counted transactions.

    Paired internal transfers are excluded by type; their other side is never
    counted either because it is also an ``internal_transfer``.
    """
    rows = session.execute(
        select(Transaction.amount_minor).where(
            Transaction.transaction_type.in_(_INCOME_EXPENSE_TYPES)
        )
    ).all()
    return sum(row[0] for row in rows)


@dataclass(frozen=True)
class TransactionFilter:
    """Optional filters for listing transactions."""

    account_id: UUID | None = None
    transaction_type: str | None = None
    status: str | None = None
    validated_by_rule: bool | None = None
    query: str | None = None
    category_id: UUID | None = None
    cost_center_id: UUID | None = None
    nature: str | None = None  # "fixed" or "variable", from the category


def list_transactions(
    session: Session,
    filters: TransactionFilter | None = None,
    *,
    limit: int = 500,
) -> list[Transaction]:
    """Return transactions matching ``filters``, newest first.

    ``query`` is a case-insensitive substring match over the normalized
    description and the counterparty. ``nature`` keeps only movements whose
    category is fixed or variable; unclassified movements never match it.
    """
    filters = filters or TransactionFilter()
    statement = select(Transaction)
    if filters.account_id is not None:
        statement = statement.where(Transaction.account_id == filters.account_id)
    if filters.transaction_type is not None:
        statement = statement.where(
            Transaction.transaction_type == filters.transaction_type
        )
    if filters.status is not None:
        statement = statement.where(Transaction.status == filters.status)
    if filters.validated_by_rule is not None:
        statement = statement.where(
            Transaction.validated_by_rule.is_(filters.validated_by_rule)
        )
    if filters.category_id is not None:
        statement = statement.where(Transaction.category_id == filters.category_id)
    if filters.cost_center_id is not None:
        statement = statement.where(
            Transaction.cost_center_id == filters.cost_center_id
        )
    if filters.nature is not None:
        statement = statement.where(
            Transaction.category_id.in_(
                select(Category.id).where(Category.nature == filters.nature)
            )
        )
    if filters.query:
        needle = f"%{filters.query.strip().lower()}%"
        statement = statement.where(
            Transaction.normalized_description.ilike(needle)
            | Transaction.counterparty.ilike(needle)
        )
    statement = statement.order_by(
        Transaction.operation_date.desc(),
        Transaction.id.desc(),
    ).limit(limit)
    return list(session.execute(statement).scalars())
