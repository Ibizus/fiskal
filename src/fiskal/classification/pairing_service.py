"""Persistence-aware service for internal-transfer pairing.

This module bridges the framework-free pairing logic in
``fiskal.domain.transfer_pairing`` with the SQLAlchemy models. It owns
proposal lookup, confirmation, rejection, unpairing and the guards that keep
pairing from touching validated data.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from fiskal.db.models import RejectedPair, Transaction
from fiskal.domain.enums import (
    ClassificationOrigin,
    TransactionStatus,
    TransactionType,
)
from fiskal.domain.transfer_pairing import (
    DEFAULT_WINDOW_DAYS,
    PairingError,
    PairProposal,
    TransferCandidate,
    TransferPairing,
)


def _is_hand_validated(txn: Transaction) -> bool:
    """Return True for validated rows pairing must not touch.

    A row that a user rule validated automatically is not
    protected: the owner has not reviewed it yet, and pairing releases it.
    """
    return txn.status == TransactionStatus.VALIDATED.value and not txn.validated_by_rule


def _not_hand_validated() -> ColumnElement[bool]:
    return or_(
        Transaction.status != TransactionStatus.VALIDATED.value,
        Transaction.validated_by_rule.is_(True),
    )


def _ordered_ids(left: UUID, right: UUID) -> tuple[UUID, UUID]:
    """Return the two ids in a deterministic string order."""
    return (left, right) if str(left) <= str(right) else (right, left)


def _type_from_amount(amount_minor: int) -> TransactionType:
    """Restore the default type for a transaction that is no longer paired."""
    return TransactionType.INCOME if amount_minor >= 0 else TransactionType.EXPENSE


def _rejections_for(
    session: Session, transaction_ids: set[UUID]
) -> set[frozenset[UUID]]:
    """Return rejected pairs that involve any of ``transaction_ids``."""
    rows = session.execute(
        select(
            RejectedPair.left_transaction_id, RejectedPair.right_transaction_id
        ).where(
            (RejectedPair.left_transaction_id.in_(transaction_ids))
            | (RejectedPair.right_transaction_id.in_(transaction_ids))
        )
    ).all()
    return {
        frozenset({row.left_transaction_id, row.right_transaction_id}) for row in rows
    }


def _load_candidates(
    session: Session, sources: list[Transaction], window_days: int
) -> list[Transaction]:
    """Load transactions that may be the counterpart of any of ``sources``.

    Only unpaired, pending rows that could match on amount, currency and
    value-date window are loaded. The sources themselves stay in the pool so
    two pending sides of one transfer are proposed once.
    """
    window = timedelta(days=window_days)
    return list(
        session.execute(
            select(Transaction).where(
                Transaction.paired_transaction_id.is_(None),
                _not_hand_validated(),
                Transaction.currency_code.in_({txn.currency_code for txn in sources}),
                Transaction.amount_minor.in_({-txn.amount_minor for txn in sources}),
                Transaction.value_date
                >= min(txn.value_date for txn in sources) - window,
                Transaction.value_date
                <= max(txn.value_date for txn in sources) + window,
            )
        ).scalars()
    )


def _to_candidate(transaction: Transaction) -> TransferCandidate:
    return TransferCandidate(
        id=transaction.id,
        account_id=transaction.account_id,
        value_date=transaction.value_date,
        amount_minor=transaction.amount_minor,
        currency_code=transaction.currency_code,
    )


def find_proposals_for_transaction_ids(
    session: Session,
    transaction_ids: list[UUID],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[PairProposal]:
    """Propose counterpart candidates for the given transactions.

    Candidates are loaded from the whole profile. Already paired or hand-validated
    transactions and explicitly rejected pairs are excluded.
    """
    source_ids = set(transaction_ids)
    if not source_ids:
        return []

    sources = list(
        session.execute(
            select(Transaction).where(
                Transaction.id.in_(source_ids),
                Transaction.paired_transaction_id.is_(None),
                _not_hand_validated(),
            )
        ).scalars()
    )
    if not sources:
        return []

    rejected = _rejections_for(session, source_ids)

    source_candidates = [_to_candidate(txn) for txn in sources]
    candidate_pool = [
        _to_candidate(txn) for txn in _load_candidates(session, sources, window_days)
    ]

    pairing = TransferPairing(window_days=window_days)
    raw_proposals = pairing.propose(source_candidates, candidate_pool)

    # Rejections are checked per source/candidate combination because a
    # candidate may be rejected with one source while still valid for another.
    proposals: list[PairProposal] = []
    for proposal in raw_proposals:
        if frozenset({proposal.source_id, proposal.candidate_id}) in rejected:
            continue
        proposals.append(proposal)

    return proposals


def _require_both_sides(
    session: Session, left_id: UUID, right_id: UUID
) -> tuple[Transaction, Transaction]:
    """Load both transactions or raise."""
    left = session.get(Transaction, left_id)
    right = session.get(Transaction, right_id)
    if left is None or right is None:
        raise PairingError("both transactions must exist")
    return left, right


def _require_pairable(left: Transaction, right: Transaction) -> None:
    """Validate that two transactions may be paired."""
    if left.account_id == right.account_id:
        raise PairingError("cannot pair two transactions of the same account")
    if left.currency_code != right.currency_code:
        raise PairingError("currency must match")
    if left.amount_minor != -right.amount_minor:
        raise PairingError("amounts must be opposite")


def _require_pending(*transactions: Transaction) -> None:
    """Raise if any transaction was validated by hand."""
    for txn in transactions:
        if _is_hand_validated(txn):
            raise PairingError(
                f"transaction {txn.id} is validated and cannot be changed"
            )


def confirm_pair(session: Session, left_id: UUID, right_id: UUID) -> None:
    """Confirm an internal-transfer pair.

    Both sides are marked as ``internal_transfer`` and linked to each other.
    The classification fields are cleared because an internal transfer has no
    category or cost center. Imported bank fields are left untouched.
    """
    left, right = _require_both_sides(session, left_id, right_id)
    if (
        left.paired_transaction_id is not None
        or right.paired_transaction_id is not None
    ):
        raise PairingError("one of the transactions is already paired")

    _require_pairable(left, right)
    _require_pending(left, right)

    # Remove any previous rejection for this pair so the same row can be paired
    # later if the user changes their mind.
    ordered = _ordered_ids(left_id, right_id)
    rejected = session.get(
        RejectedPair,
        {"left_transaction_id": ordered[0], "right_transaction_id": ordered[1]},
    )
    if rejected is not None:
        session.delete(rejected)

    left.paired_transaction_id = right.id
    right.paired_transaction_id = left.id
    for txn in (left, right):
        txn.transaction_type = TransactionType.INTERNAL_TRANSFER.value
        txn.category_id = None
        txn.cost_center_id = None
        txn.origin = ClassificationOrigin.MANUAL.value
        txn.rule_id = None
        txn.status = TransactionStatus.PENDING.value
        txn.validated_by_rule = False

    session.commit()


def reject_pair(session: Session, left_id: UUID, right_id: UUID) -> None:
    """Remember that the user rejected this proposed pair."""
    left, right = _require_both_sides(session, left_id, right_id)
    _require_pairable(left, right)

    if (
        left.paired_transaction_id is not None
        or right.paired_transaction_id is not None
    ):
        raise PairingError("cannot reject a pair that is already confirmed")

    ordered = _ordered_ids(left_id, right_id)
    existing = session.get(
        RejectedPair,
        {"left_transaction_id": ordered[0], "right_transaction_id": ordered[1]},
    )
    if existing is not None:
        return

    session.add(
        RejectedPair(
            left_transaction_id=ordered[0],
            right_transaction_id=ordered[1],
        )
    )
    session.commit()


def unpair(session: Session, left_id: UUID, right_id: UUID) -> None:
    """Undo a confirmed pair and restore both sides to unclassified/pending."""
    left, right = _require_both_sides(session, left_id, right_id)
    if left.paired_transaction_id != right.id or right.paired_transaction_id != left.id:
        raise PairingError("transactions are not paired with each other")

    _require_pending(left, right)

    left.paired_transaction_id = None
    right.paired_transaction_id = None
    for txn in (left, right):
        txn.transaction_type = _type_from_amount(txn.amount_minor).value
        txn.category_id = None
        txn.cost_center_id = None
        txn.origin = ClassificationOrigin.MANUAL.value
        txn.rule_id = None
        txn.status = TransactionStatus.PENDING.value

    session.commit()
