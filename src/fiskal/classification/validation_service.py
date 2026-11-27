"""Validation queue service: list and validate pending transactions.

The queue shows every pending, unpaired transaction that still needs a
category. Confirming or correcting one marks it manual and validated, which
makes it stable under later reclassification and feeds the validated-history
suggestions used by the next import.

Rows that a rule created by the owner validated automatically (flagged
``validated_by_rule``) are not in the queue. The owner can still correct one with
``validate_transaction``, which turns it into a manual classification, or send
it back to the queue with ``return_to_queue``.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select, update
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


def _is_rule_validated(txn: Transaction) -> bool:
    return txn.status == TransactionStatus.VALIDATED.value and txn.validated_by_rule


def list_rule_validated_transactions(session: Session) -> list[Transaction]:
    """Return transactions that a rule validated automatically, newest first."""
    return list(
        session.execute(
            select(Transaction)
            .where(
                Transaction.status == TransactionStatus.VALIDATED.value,
                Transaction.validated_by_rule.is_(True),
            )
            .order_by(Transaction.operation_date.desc(), Transaction.id.desc())
        ).scalars()
    )


def return_to_queue(session: Session, transaction_id: UUID) -> Transaction:
    """Send a rule-validated transaction back to the pending queue.

    The row keeps its classification as a preselection but becomes manual, so
    no rule validates it again and the owner's next decision is final. Rows the
    owner validated by hand are refused.
    """
    txn = session.get(Transaction, transaction_id)
    if txn is None:
        raise ValidationError("transaction not found")
    if not _is_rule_validated(txn):
        raise ValidationError("only rule-validated transactions can be sent back")
    txn.origin = ClassificationOrigin.MANUAL.value
    txn.rule_id = None
    txn.validated_by_rule = False
    txn.status = TransactionStatus.PENDING.value
    session.commit()
    return txn


def check_classification(
    session: Session,
    *,
    transaction_type: TransactionType | str,
    category_id: UUID,
    cost_center_id: UUID | None,
    person_id: UUID | None,
) -> TransactionType:
    """Check a classification against the catalogs and return its type.

    The category must exist, be active and match the type's kind; the cost
    center must exist and be active; the person must exist. This is the single
    set of checks behind every manual validation, one row or a whole block.
    """
    type_enum = _coerce_type(transaction_type)
    required_kind = _required_category_kind(type_enum)

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
    return type_enum


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
    clear the person as rule-owned. A row that a user rule validated
    automatically can be corrected the same way and then sticks as manual.
    """
    type_enum = check_classification(
        session,
        transaction_type=transaction_type,
        category_id=category_id,
        cost_center_id=cost_center_id,
        person_id=person_id,
    )
    txn = session.get(Transaction, transaction_id)
    if txn is None:
        raise ValidationError("transaction not found")
    if txn.status == TransactionStatus.VALIDATED.value and not _is_rule_validated(txn):
        raise ValidationError("transaction is already validated")
    if txn.paired_transaction_id is not None:
        raise ValidationError("cannot validate an internal transfer")

    txn.transaction_type = type_enum.value
    txn.category_id = category_id
    txn.cost_center_id = cost_center_id
    txn.person_id = person_id
    txn.origin = ClassificationOrigin.MANUAL.value
    txn.rule_id = None
    txn.validated_by_rule = False
    txn.guided_action_id = None
    txn.status = TransactionStatus.VALIDATED.value
    session.commit()
    return txn


_BULK_CHUNK_SIZE = 500


def validate_pending_in_bulk(
    session: Session,
    transaction_ids: Sequence[UUID],
    *,
    transaction_type: TransactionType | str,
    category_id: UUID,
    cost_center_id: UUID | None,
    person_id: UUID | None,
    guided_action_id: UUID | None = None,
    rule_id: UUID | None = None,
) -> int:
    """Validate many pending transactions with one classification.

    The checks and the resulting fields are the ones ``validate_transaction``
    applies: the classification becomes manual, so reclassification never
    changes it and a person set here survives. With ``rule_id`` the rows are
    validated by that owner rule instead (``origin`` rule,
    ``validated_by_rule``), exactly as the engine leaves the rows a user rule
    validates, so they follow later edits of the rule. Only rows that are still
    pending and unpaired are touched, and the changes are flushed but not
    committed, so the caller decides the transaction boundary. Returns how many
    rows were validated.
    """
    type_enum = check_classification(
        session,
        transaction_type=transaction_type,
        category_id=category_id,
        cost_center_id=cost_center_id,
        person_id=person_id,
    )
    validated = 0
    for start in range(0, len(transaction_ids), _BULK_CHUNK_SIZE):
        chunk = transaction_ids[start : start + _BULK_CHUNK_SIZE]
        result = session.execute(
            update(Transaction)
            .where(
                Transaction.id.in_(chunk),
                Transaction.status == TransactionStatus.PENDING.value,
                Transaction.paired_transaction_id.is_(None),
            )
            .values(
                transaction_type=type_enum.value,
                category_id=category_id,
                cost_center_id=cost_center_id,
                person_id=person_id,
                origin=(
                    ClassificationOrigin.MANUAL.value
                    if rule_id is None
                    else ClassificationOrigin.RULE.value
                ),
                rule_id=rule_id,
                validated_by_rule=rule_id is not None,
                guided_action_id=guided_action_id,
                status=TransactionStatus.VALIDATED.value,
            )
            .execution_options(synchronize_session="fetch")
        )
        validated += int(result.rowcount)  # type: ignore[attr-defined]
    session.flush()
    return validated
