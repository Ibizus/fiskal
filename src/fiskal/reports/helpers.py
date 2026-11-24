"""Reusable report queries and filters.

All reports are built directly over the persisted transaction table; none
stores its own totals.
"""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from fiskal.db.models import Transaction
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
