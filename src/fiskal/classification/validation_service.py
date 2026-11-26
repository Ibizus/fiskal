"""Validation queue service: list and validate pending transactions.

The queue shows every pending, unpaired transaction that still needs a
category. Confirming or correcting one marks it manual and validated, which
makes it stable under later reclassification and feeds the validated-history
suggestions used by the next import.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from fiskal.db.models import Category, CostCenter, Person, Transaction
from fiskal.domain.enums import (
    CategoryKind,
    ClassificationOrigin,
    TransactionStatus,
    TransactionType,
)


class ValidationError(Exception):
    """Raised when a transaction cannot be validated."""


def list_pending_transactions(
    session: Session,
    *,
    account_id: UUID | None = None,
) -> list[Transaction]:
    """Return pending, unpaired transactions, newest first.

    Paired internal transfers are excluded: they have no category by design.
    """
    statement = select(Transaction).where(
        Transaction.status == TransactionStatus.PENDING.value,
        Transaction.paired_transaction_id.is_(None),
    )
    if account_id is not None:
        statement = statement.where(Transaction.account_id == account_id)
    statement = statement.order_by(
        Transaction.operation_date.desc(),
        Transaction.id.desc(),
    )
    return list(session.execute(statement).scalars())


def _coerce_type(transaction_type: TransactionType | str) -> TransactionType:
    if isinstance(transaction_type, TransactionType):
        return transaction_type
    try:
        return TransactionType(transaction_type)
    except ValueError as exc:
        raise ValidationError(f"unknown transaction type: {transaction_type}") from exc


def _required_category_kind(transaction_type: TransactionType) -> CategoryKind:
    if transaction_type is TransactionType.INTERNAL_TRANSFER:
        raise ValidationError("internal transfers are handled by pairing")
    if transaction_type is TransactionType.INCOME:
        return CategoryKind.INCOME
    return CategoryKind.EXPENSE


def validate_transaction(
    session: Session,
    transaction_id: UUID,
    *,
    transaction_type: TransactionType | str,
    category_id: UUID,
    cost_center_id: UUID | None,
    person_id: UUID | None,
) -> Transaction:
    """Confirm or correct one pending transaction and mark it validated.

    The classification becomes manual so later reclassification never changes
    it, and the row starts feeding validated-history suggestions. Setting a
    person by hand also flips the origin to manual, so reclassification cannot
    clear the person as rule-owned.
    """
    type_enum = _coerce_type(transaction_type)
    required_kind = _required_category_kind(type_enum)

    txn = session.get(Transaction, transaction_id)
    if txn is None:
        raise ValidationError("transaction not found")
    if txn.status == TransactionStatus.VALIDATED.value:
        raise ValidationError("transaction is already validated")
    if txn.paired_transaction_id is not None:
        raise ValidationError("cannot validate an internal transfer")

    category = session.get(Category, category_id)
    if category is None:
        raise ValidationError("the category does not exist")
    if not category.is_active:
        raise ValidationError("the category is inactive")
    if category.kind != required_kind.value:
        raise ValidationError(
            f"category kind {category.kind} is incompatible with type {type_enum.value}"
        )

    if cost_center_id is not None:
        cost_center = session.get(CostCenter, cost_center_id)
        if cost_center is None:
            raise ValidationError("the cost center does not exist")
        if not cost_center.is_active:
            raise ValidationError("the cost center is inactive")

    if person_id is not None:
        person = session.get(Person, person_id)
        if person is None:
            raise ValidationError("the person does not exist")

    txn.transaction_type = type_enum.value
    txn.category_id = category_id
    txn.cost_center_id = cost_center_id
    txn.person_id = person_id
    txn.origin = ClassificationOrigin.MANUAL.value
    txn.rule_id = None
    txn.status = TransactionStatus.VALIDATED.value
    session.commit()
    return txn
