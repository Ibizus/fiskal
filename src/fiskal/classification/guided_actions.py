"""Guided history mode, write side: confirm a group in one block and undo it.

Confirming a group validates every pending movement of the concept with one
classification, in a single database transaction, and can create the owner's
rule from the same decision. Each confirmation is recorded as a
``GuidedAction`` and its rows carry its id, so a mistake in a large group can
be undone.

What a confirmation does to its rows
------------------------------------
The rows become validated by hand: ``origin`` manual, no rule link. That keeps
the owner's decision, and a person set on the group, stable under later
reclassification, and lets the rows feed history suggestions. The optional rule
is created through the same path as any owner rule (``add_rule`` followed by
``reclassify_pending``), so it also validates *other* pending movements that
contain its pattern and classifies the next imports. Closed months do not exist
yet; the pairing guards that do exist are respected, see ``guided_groups``.

What an undo does
-----------------
Undoing returns the action's rows to pending. A row is returned only while it
is still exactly what the action left: validated, still linked to the action
and still ``origin`` manual. A row the owner has since corrected, or one that an
explicit rules reapply over validated history reclassified (``origin`` rule),
is a later decision and is left as it is. Returned rows keep their
classification as a preselection and stay manual, like a row sent back from the
queue, so they appear again in the group view for a corrected decision. Undoing
a whole action also deactivates the rule it created, which sends the movements
that rule validated automatically back to pending; returning single rows does
not touch the rule.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.orm import Session

from fiskal.classification.engine import reclassify_pending
from fiskal.classification.guided_groups import (
    GroupKey,
    group_transaction_ids,
    held_transaction_ids,
)
from fiskal.classification.rules_service import RuleSpec, add_rule, top_priority
from fiskal.classification.validation_service import (
    check_classification,
    validate_pending_in_bulk,
)
from fiskal.db.models import GuidedAction, Rule, Transaction
from fiskal.domain.enums import (
    ClassificationOrigin,
    GroupDirection,
    RuleMatchMode,
    TransactionStatus,
    TransactionType,
)

_UNDO_CHUNK_SIZE = 500


class GuidedError(Exception):
    """Raised when a guided-mode action cannot be applied."""


@dataclass(frozen=True)
class GroupClassification:
    """The decision applied to every movement of a group."""

    transaction_type: TransactionType | str
    category_id: UUID
    cost_center_id: UUID | None = None
    person_id: UUID | None = None


@dataclass(frozen=True)
class GroupOutcome:
    """Result of confirming a group.

    ``validated_count`` is the number of movements of the group that were
    validated. ``rule`` is the rule created with the group, if any, and
    ``rule_validated_count`` is how many pending movements outside the group
    that rule validated automatically.
    """

    action: GuidedAction
    validated_count: int
    rule: Rule | None
    rule_validated_count: int


@dataclass(frozen=True)
class UndoOutcome:
    """Result of an undo: rows returned, rows left as they were, rule state."""

    returned_count: int
    skipped_count: int
    rule_deactivated: bool


@dataclass(frozen=True)
class GuidedActionSummary:
    """A confirmed group that still has validated rows an undo could return."""

    action: GuidedAction
    remaining_rows: int


def _group_rule_spec(
    session: Session,
    key: GroupKey,
    classification: GroupClassification,
    pattern: str | None,
) -> RuleSpec:
    """Return the rule a group confirmation creates.

    The pattern defaults to the group's normalized description. The amount
    bound keeps the rule on the group's side of the books, so a rule made from
    purchases never validates a refund from the same shop.
    """
    is_income = key.direction is GroupDirection.INCOME
    return RuleSpec(
        priority=top_priority(session),
        mode=RuleMatchMode.CONTAINS,
        pattern=(pattern or "").strip() or key.normalized_description,
        transaction_type=classification.transaction_type,
        category_id=classification.category_id,
        cost_center_id=classification.cost_center_id,
        person_id=classification.person_id,
        amount_min_minor=0 if is_income else None,
        amount_max_minor=None if is_income else -1,
        day_of_month_min=None,
        day_of_month_max=None,
        is_active=True,
    )


def _count_rule_validated(session: Session, rule: Rule) -> int:
    return session.execute(
        select(func.count()).where(
            Transaction.rule_id == rule.id,
            Transaction.validated_by_rule.is_(True),
        )
    ).scalar_one()


def confirm_group(
    session: Session,
    key: GroupKey,
    classification: GroupClassification,
    *,
    create_rule: bool = False,
    rule_pattern: str | None = None,
    expected_count: int | None = None,
) -> GroupOutcome:
    """Validate every pending movement of a group with one classification.

    Everything happens in one database transaction: when any step fails
    nothing is written. Movements that belong to an open internal-transfer
    proposal or to a pair are never part of a group. ``expected_count`` is the
    row count the owner was shown; when the group has changed since, the
    confirmation is refused instead of applying to a different set.

    With ``create_rule`` an owner rule is created from the decision, with
    ``rule_pattern`` (default: the normalized description) as its pattern.
    """
    ids = group_transaction_ids(session, key, held_transaction_ids(session))
    if not ids:
        raise GuidedError("the group has no pending movements")
    if expected_count is not None and expected_count != len(ids):
        raise GuidedError("the group changed since it was shown; reload and retry")
    check_classification(
        session,
        transaction_type=classification.transaction_type,
        category_id=classification.category_id,
        cost_center_id=classification.cost_center_id,
        person_id=classification.person_id,
    )

    try:
        rule = None
        if create_rule:
            rule = add_rule(
                session, _group_rule_spec(session, key, classification, rule_pattern)
            )
        action = GuidedAction(
            normalized_description=key.normalized_description,
            direction=key.direction.value,
            row_count=0,
            rule_id=rule.id if rule is not None else None,
        )
        session.add(action)
        session.flush()
        action.row_count = validate_pending_in_bulk(
            session,
            ids,
            transaction_type=classification.transaction_type,
            category_id=classification.category_id,
            cost_center_id=classification.cost_center_id,
            person_id=classification.person_id,
            guided_action_id=action.id,
        )
        if rule is not None:
            reclassify_pending(session)
        session.commit()
    except Exception:
        session.rollback()
        raise

    return GroupOutcome(
        action=action,
        validated_count=action.row_count,
        rule=rule,
        rule_validated_count=(
            _count_rule_validated(session, rule) if rule is not None else 0
        ),
    )


def _return_to_pending(
    session: Session, transaction_ids: Sequence[UUID], action_id: UUID | None
) -> int:
    """Return still-untouched guided rows to pending and unlink them."""
    returned = 0
    for start in range(0, len(transaction_ids), _UNDO_CHUNK_SIZE):
        chunk = transaction_ids[start : start + _UNDO_CHUNK_SIZE]
        statement = update(Transaction).where(
            Transaction.id.in_(chunk),
            Transaction.status == TransactionStatus.VALIDATED.value,
            Transaction.origin == ClassificationOrigin.MANUAL.value,
            Transaction.guided_action_id.is_not(None),
        )
        if action_id is not None:
            statement = statement.where(Transaction.guided_action_id == action_id)
        result = session.execute(
            statement.values(
                status=TransactionStatus.PENDING.value,
                guided_action_id=None,
                validated_by_rule=False,
                rule_id=None,
            ).execution_options(synchronize_session="fetch")
        )
        returned += int(result.rowcount)  # type: ignore[attr-defined]
    return returned


def _linked_ids(session: Session, action_id: UUID) -> list[UUID]:
    return list(
        session.execute(
            select(Transaction.id).where(Transaction.guided_action_id == action_id)
        ).scalars()
    )


def undo_guided_action(session: Session, action_id: UUID) -> UndoOutcome:
    """Undo a confirmed group: return its rows to pending, deactivate its rule.

    Rows the owner or a reapply changed after the confirmation are skipped and
    counted, see the module docstring. The action is marked undone and cannot
    be undone twice. Deactivating the rule also sends the movements it
    validated automatically back to pending, so the whole effect of the group
    is reverted in one transaction.
    """
    action = session.get(GuidedAction, action_id)
    if action is None:
        raise GuidedError("the action does not exist")
    if action.undone_at is not None:
        raise GuidedError("the action was already undone")

    linked = _linked_ids(session, action.id)
    try:
        returned = _return_to_pending(session, linked, action.id)
        rule = session.get(Rule, action.rule_id) if action.rule_id else None
        deactivate = rule is not None and rule.is_active
        if rule is not None:
            rule.is_active = False
        action.undone_at = datetime.now(UTC)
        session.flush()
        reclassify_pending(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return UndoOutcome(
        returned_count=returned,
        skipped_count=len(linked) - returned,
        rule_deactivated=deactivate,
    )


def undo_guided_rows(session: Session, transaction_ids: Sequence[UUID]) -> UndoOutcome:
    """Return single rows of guided actions to pending.

    Only rows that are still what their action left are returned; the rest are
    skipped and counted. The action and its rule are not touched: undo the
    whole action to deactivate the rule.
    """
    ids = list(dict.fromkeys(transaction_ids))
    try:
        returned = _return_to_pending(session, ids, None)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return UndoOutcome(
        returned_count=returned,
        skipped_count=len(ids) - returned,
        rule_deactivated=False,
    )


def _undoable_actions() -> list[ColumnElement[bool]]:
    return [
        GuidedAction.undone_at.is_(None),
        Transaction.status == TransactionStatus.VALIDATED.value,
        Transaction.origin == ClassificationOrigin.MANUAL.value,
    ]


def count_guided_actions(session: Session) -> int:
    """Return how many confirmed groups still have rows an undo could return."""
    return session.execute(
        select(func.count(func.distinct(GuidedAction.id)))
        .join(Transaction, Transaction.guided_action_id == GuidedAction.id)
        .where(*_undoable_actions())
    ).scalar_one()


def list_guided_actions(
    session: Session, *, limit: int, offset: int = 0
) -> list[GuidedActionSummary]:
    """Return one page of confirmed groups that can still be undone, newest first."""
    rows = session.execute(
        select(GuidedAction, func.count(Transaction.id))
        .join(Transaction, Transaction.guided_action_id == GuidedAction.id)
        .where(*_undoable_actions())
        .group_by(GuidedAction.id)
        .order_by(GuidedAction.created_at.desc(), GuidedAction.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [GuidedActionSummary(action=row[0], remaining_rows=row[1]) for row in rows]


def list_guided_action_rows(
    session: Session, action_id: UUID, *, limit: int
) -> list[Transaction]:
    """Return up to ``limit`` rows of an action that an undo could return."""
    return list(
        session.execute(
            select(Transaction)
            .where(
                Transaction.guided_action_id == action_id,
                Transaction.status == TransactionStatus.VALIDATED.value,
                Transaction.origin == ClassificationOrigin.MANUAL.value,
            )
            .order_by(Transaction.operation_date.desc(), Transaction.id)
            .limit(limit)
        ).scalars()
    )
