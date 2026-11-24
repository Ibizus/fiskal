"""Persistence-aware service for internal-transfer pairing.

This module bridges the framework-free pairing logic in
``fiskal.domain.transfer_pairing`` with the SQLAlchemy models. It owns
proposal lookup, confirmation, rejection, unpairing and the guards that keep
pairing from touching closed or validated data.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from fiskal.db.models import ClosedMonth, RejectedPair, Transaction
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


def _ordered_ids(left: UUID, right: UUID) -> tuple[UUID, UUID]:
    """Return the two ids in a deterministic string order."""
    return (left, right) if str(left) <= str(right) else (right, left)


def _type_from_amount(amount_minor: int) -> TransactionType:
    """Restore the default type for a transaction that is no longer paired."""
    return TransactionType.INCOME if amount_minor >= 0 else TransactionType.EXPENSE


def _month_start(day: date) -> date:
    """Return the first day of the month containing ``day``."""
    return day.replace(day=1)


def _is_month_closed(session: Session, account_id: UUID, day: date) -> bool:
    """Return True when ``day``'s month is closed for ``account_id``."""
    month = _month_start(day)
    return bool(
        session.scalar(
            select(ClosedMonth).where(
                ClosedMonth.account_id == account_id,
                ClosedMonth.month == month,
            )
        )
    )


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


def _load_available_transactions(
    session: Session, excluded_ids: set[UUID] | None = None
) -> list[Transaction]:
    """Load transactions that may appear as pairing candidates.

    Paired transactions are excluded because a transaction can only belong to
    one pair.
    """
    excluded_ids = excluded_ids or set()
    return list(
        session.execute(
            select(Transaction).where(
                Transaction.paired_transaction_id.is_(None),
                Transaction.id.notin_(excluded_ids),
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
        is_paired=False,
    )


def find_proposals_for_transaction_ids(
    session: Session,
    transaction_ids: list[UUID],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[PairProposal]:
    """Propose counterpart candidates for the given transactions.

    Candidates are loaded from the whole profile. Already paired transactions
    and explicitly rejected pairs are excluded.
    """
    source_ids = set(transaction_ids)
    if not source_ids:
        return []

    sources = list(
        session.execute(
            select(Transaction).where(Transaction.id.in_(source_ids))
        ).scalars()
    )

    rejected = _rejections_for(session, source_ids)

    candidates = _load_available_transactions(session, excluded_ids=source_ids)
    candidate_pool: list[TransferCandidate] = []
    source_candidates: list[TransferCandidate] = []
    for txn in sources:
        source_candidates.append(_to_candidate(txn))
    for txn in candidates:
        candidate_pool.append(_to_candidate(txn))

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


def _require_not_closed(session: Session, *transactions: Transaction) -> None:
    """Raise if any transaction belongs to a closed month."""
    for txn in transactions:
        if _is_month_closed(session, txn.account_id, txn.operation_date):
            raise PairingError(
                f"transaction {txn.id} belongs to a closed month and cannot be paired"
            )


def _require_pending(*transactions: Transaction) -> None:
    """Raise if any transaction is already validated."""
    for txn in transactions:
        if txn.status == TransactionStatus.VALIDATED.value:
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
    _require_not_closed(session, left, right)
    _require_pending(left, right)

    # Remove any previous rejection for this pair so the same row can be paired
    # later if the user changes their mind.
    ordered = _ordered_ids(left_id, right_id)
    session.execute(
        select(RejectedPair).where(
            RejectedPair.left_transaction_id == ordered[0],
            RejectedPair.right_transaction_id == ordered[1],
        )
    )
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

    _require_not_closed(session, left, right)
    _require_pending(left, right)

    left.paired_transaction_id = None
    right.paired_transaction_id = None
    for txn in (left, right):
        txn.transaction_type = _type_from_amount(txn.amount_minor).value
        txn.category_id = None
        txn.cost_center_id = None
        txn.origin = ClassificationOrigin.MANUAL.value
        txn.status = TransactionStatus.PENDING.value

    session.commit()
