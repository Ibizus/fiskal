"""Guided history mode, read side: group pending movements by concept.

Importing years of history leaves thousands of pending movements. Instead of
reviewing them one by one, the owner reviews *concepts*: pending, unpaired
movements sharing the same normalized description. Income and expenses form
separate groups even when the description matches, and a description that
appears once stays in the normal queue. Everything here is computed by the
database; no transaction is loaded into memory to build or page the groups.

The group key is the stored ``normalized_description`` column, the same text
that rules match against, so a rule whose pattern is the key matches every row
of the group.

Movements that are part of a pending internal-transfer proposal are left out:
the owner must decide on the pairing first, and a block action must never
validate them as plain income or expense.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Final
from uuid import UUID

from sqlalchemy import ColumnElement, Select, case, func, select
from sqlalchemy.orm import Session

from fiskal.classification.pairing_service import find_proposals_for_transaction_ids
from fiskal.db.models import Transaction
from fiskal.domain.enums import GroupDirection, TransactionStatus
from fiskal.domain.transfer_pairing import PairProposal

# A group's amounts spread widely when its largest absolute amount is at least
# this many times its smallest. A subscription that changes by a few percent
# stays below it; a shop where one visit costs 8 and another 120 does not.
WIDE_SPREAD_FACTOR: Final = 3

_MIN_GROUP_ROWS: Final = 2


@dataclass(frozen=True)
class GroupKey:
    """Identifies one group: a normalized description on one side of the books."""

    normalized_description: str
    direction: GroupDirection


@dataclass(frozen=True)
class TransactionGroup:
    """Aggregates of the pending movements that share one group key.

    ``currency_code`` is ``None`` when the rows mix currencies, in which case
    the amount aggregates are not comparable and callers should not show them.
    """

    key: GroupKey
    row_count: int
    total_minor: int
    min_abs_minor: int
    max_abs_minor: int
    first_date: date
    last_date: date
    currency_code: str | None

    @property
    def has_wide_spread(self) -> bool:
        """Return True when the group's amounts differ widely."""
        return is_wide_spread(self.min_abs_minor, self.max_abs_minor)


@dataclass(frozen=True)
class GroupSummary:
    """How many multi-row groups exist and how many movements they hold."""

    group_count: int
    row_count: int


@dataclass(frozen=True)
class ClassificationProgress:
    """Where the review stands: pending movements and the share classified.

    Paired internal transfers are not counted: they carry no category by
    design. ``classified_percent`` rounds down, so it only reads 100 once
    nothing is pending.
    """

    pending: int
    validated: int

    @property
    def classified_percent(self) -> int:
        """Return the whole percentage of movements already validated."""
        total = self.pending + self.validated
        if total == 0:
            return 0
        return self.validated * 100 // total


def is_wide_spread(min_abs_minor: int, max_abs_minor: int) -> bool:
    """Return True when the largest amount is far from the smallest.

    Amounts are compared by absolute value in integer minor units, so the
    check is exact. A zero smallest amount next to a non-zero one is wide.
    """
    return max_abs_minor >= WIDE_SPREAD_FACTOR * min_abs_minor and max_abs_minor > 0


def direction_of(amount_minor: int) -> GroupDirection:
    """Return the group side of an amount: zero and positive are income."""
    return GroupDirection.INCOME if amount_minor >= 0 else GroupDirection.EXPENSE


def pending_proposals(session: Session) -> list[PairProposal]:
    """Return the open internal-transfer proposals among pending movements."""
    pending_ids = list(
        session.execute(
            select(Transaction.id).where(
                Transaction.status == TransactionStatus.PENDING.value,
                Transaction.paired_transaction_id.is_(None),
            )
        ).scalars()
    )
    return find_proposals_for_transaction_ids(session, pending_ids)


def proposal_leg_ids(proposals: list[PairProposal]) -> set[UUID]:
    """Return both legs of every proposal."""
    return {proposal.source_id for proposal in proposals} | {
        proposal.candidate_id for proposal in proposals
    }


def held_transaction_ids(session: Session) -> set[UUID]:
    """Return pending movements that are part of an internal-transfer proposal.

    Both legs of every open proposal are returned. They cannot be validated as
    income or expense until the owner confirms or rejects the pairing.
    """
    return proposal_leg_ids(pending_proposals(session))


def _direction_column() -> ColumnElement[str]:
    return case(
        (Transaction.amount_minor >= 0, GroupDirection.INCOME.value),
        else_=GroupDirection.EXPENSE.value,
    )


def _eligible(held_ids: set[UUID]) -> list[ColumnElement[bool]]:
    conditions = [
        Transaction.status == TransactionStatus.PENDING.value,
        Transaction.paired_transaction_id.is_(None),
        Transaction.normalized_description != "",
    ]
    if held_ids:
        conditions.append(Transaction.id.not_in(held_ids))
    return conditions


def _grouped(held_ids: set[UUID]) -> Select[*tuple[Any, ...]]:
    direction = _direction_column()
    return (
        select(
            Transaction.normalized_description.label("description"),
            direction.label("direction"),
            func.count().label("row_count"),
            func.sum(Transaction.amount_minor).label("total_minor"),
            func.min(func.abs(Transaction.amount_minor)).label("min_abs_minor"),
            func.max(func.abs(Transaction.amount_minor)).label("max_abs_minor"),
            func.min(Transaction.operation_date).label("first_date"),
            func.max(Transaction.operation_date).label("last_date"),
            func.min(Transaction.currency_code).label("currency_min"),
            func.max(Transaction.currency_code).label("currency_max"),
        )
        .where(*_eligible(held_ids))
        .group_by(Transaction.normalized_description, direction)
        .having(func.count() >= _MIN_GROUP_ROWS)
    )


def list_pending_groups(
    session: Session,
    *,
    held_ids: set[UUID],
    limit: int,
    offset: int = 0,
) -> list[TransactionGroup]:
    """Return one page of groups, the largest first.

    Groups are ordered from most rows to fewest (ties by description and
    direction, so paging is stable), which puts the biggest queue reduction on
    the first page. ``held_ids`` are excluded, see ``held_transaction_ids``.
    """
    statement = (
        _grouped(held_ids)
        .order_by(func.count().desc(), "description", "direction")
        .limit(limit)
        .offset(offset)
    )
    return [
        TransactionGroup(
            key=GroupKey(row.description, GroupDirection(row.direction)),
            row_count=row.row_count,
            total_minor=row.total_minor,
            min_abs_minor=row.min_abs_minor,
            max_abs_minor=row.max_abs_minor,
            first_date=_as_date(row.first_date),
            last_date=_as_date(row.last_date),
            currency_code=(
                row.currency_min if row.currency_min == row.currency_max else None
            ),
        )
        for row in session.execute(statement)
    ]


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def summarize_groups(session: Session, held_ids: set[UUID]) -> GroupSummary:
    """Return the number of multi-row groups and the movements they hold."""
    grouped = _grouped(held_ids).subquery()
    group_count, row_count = session.execute(
        select(func.count(), func.coalesce(func.sum(grouped.c.row_count), 0))
    ).one()
    return GroupSummary(group_count=group_count, row_count=row_count)


def group_transaction_ids(
    session: Session, key: GroupKey, held_ids: set[UUID]
) -> list[UUID]:
    """Return the ids of the pending movements that belong to ``key``."""
    amount_side = (
        Transaction.amount_minor >= 0
        if key.direction is GroupDirection.INCOME
        else Transaction.amount_minor < 0
    )
    return list(
        session.execute(
            select(Transaction.id)
            .where(
                *_eligible(held_ids),
                Transaction.normalized_description == key.normalized_description,
                amount_side,
            )
            .order_by(Transaction.operation_date, Transaction.id)
        ).scalars()
    )


def classification_progress(session: Session) -> ClassificationProgress:
    """Return how many movements are pending and how many are validated."""
    pending, validated = session.execute(
        select(
            func.coalesce(
                func.sum(
                    case((Transaction.status == TransactionStatus.PENDING.value, 1))
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((Transaction.status == TransactionStatus.VALIDATED.value, 1))
                ),
                0,
            ),
        ).where(Transaction.paired_transaction_id.is_(None))
    ).one()
    return ClassificationProgress(pending=pending, validated=validated)
