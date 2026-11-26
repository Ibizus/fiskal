"""Tests for confirming and undoing groups in the guided history mode."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fiskal.classification import (
    GroupClassification,
    GroupKey,
    GuidedError,
    ValidationError,
    apply_reapply_validated,
    confirm_group,
    count_guided_actions,
    delete_rule,
    list_guided_action_rows,
    list_guided_actions,
    preview_reapply_validated,
    reclassify_pending,
    suggest_from_history,
    undo_guided_action,
    undo_guided_rows,
    update_rule,
    validate_transaction,
)
from fiskal.classification.rules_service import spec_from_rule
from fiskal.db.models import Category, GuidedAction, Rule, Transaction
from fiskal.domain.enums import (
    CategoryKind,
    ClassificationOrigin,
    GroupDirection,
    RuleOrigin,
    TransactionStatus,
)
from fiskal.domain.rule_matching import RuleValidationError
from tests.support.factories import (
    _account,
    _bulk_transactions,
    _category,
    _cost_center,
    _person,
    _rule,
    _transaction,
)

EXPENSE = GroupDirection.EXPENSE
INCOME = GroupDirection.INCOME
ROWS = 4


def _classification(category: Category, **extra: object) -> GroupClassification:
    return GroupClassification(
        transaction_type="expense",
        category_id=category.id,
        **extra,  # type: ignore[arg-type]
    )


def _refresh(session: Session, rows: list[Transaction]) -> list[Transaction]:
    session.expire_all()
    return [session.get(Transaction, row.id) for row in rows]  # type: ignore[misc]


def _statuses(rows: list[Transaction]) -> set[str]:
    return {row.status for row in rows}


def test_confirm_without_rule_validates_every_row_and_creates_no_rule(
    session: Session,
) -> None:
    """The block is validated by hand: manual origin, no rule, linked to the action."""
    account = _account(session)
    food = _category(session)
    cost_center = _cost_center(session)
    rows = _bulk_transactions(session, account=account, description="shop", count=ROWS)

    outcome = confirm_group(
        session,
        GroupKey("shop", EXPENSE),
        _classification(food, cost_center_id=cost_center.id),
    )

    assert outcome.validated_count == ROWS
    assert outcome.rule is None
    assert outcome.rule_validated_count == 0
    assert session.scalar(select(func.count()).select_from(Rule)) == 0
    for row in _refresh(session, rows):
        assert row.status == TransactionStatus.VALIDATED.value
        assert row.category_id == food.id
        assert row.cost_center_id == cost_center.id
        assert row.origin == ClassificationOrigin.MANUAL.value
        assert row.rule_id is None
        assert not row.validated_by_rule
        assert row.guided_action_id == outcome.action.id
    assert outcome.action.row_count == ROWS
    assert outcome.action.rule_id is None


def test_confirm_only_touches_its_own_group(session: Session) -> None:
    """Other concepts and the other side of the same description stay pending."""
    account = _account(session)
    food = _category(session)
    _bulk_transactions(session, account=account, description="shop", count=ROWS)
    refunds = _bulk_transactions(
        session, account=account, description="shop", count=2, amount_minor=300
    )
    others = _bulk_transactions(session, account=account, description="gym", count=2)

    confirm_group(session, GroupKey("shop", EXPENSE), _classification(food))

    assert _statuses(_refresh(session, refunds + others)) == {
        TransactionStatus.PENDING.value
    }


def test_confirm_with_rule_creates_owner_rule_with_group_pattern(
    session: Session,
) -> None:
    """The rule is a user rule on the normalized description, on the group's side."""
    account = _account(session)
    food = _category(session)
    _bulk_transactions(session, account=account, description="shop", count=ROWS)

    outcome = confirm_group(
        session, GroupKey("shop", EXPENSE), _classification(food), create_rule=True
    )

    rule = outcome.rule
    assert rule is not None
    assert rule.origin == RuleOrigin.USER.value
    assert rule.pattern == "shop"
    assert (rule.amount_min_minor, rule.amount_max_minor) == (None, -1)
    assert outcome.action.rule_id == rule.id
    assert outcome.validated_count == ROWS


def test_confirm_with_rule_uses_the_edited_pattern(session: Session) -> None:
    """The rule pattern is editable; a blank one falls back to the description."""
    account = _account(session)
    food = _category(session)
    _bulk_transactions(session, account=account, description="alpha store", count=2)
    _bulk_transactions(session, account=account, description="beta store", count=2)

    custom = confirm_group(
        session,
        GroupKey("alpha store", EXPENSE),
        _classification(food),
        create_rule=True,
        rule_pattern="  alpha  ",
    )
    blank = confirm_group(
        session,
        GroupKey("beta store", EXPENSE),
        _classification(food),
        create_rule=True,
        rule_pattern="  ",
    )

    assert custom.rule is not None
    assert custom.rule.pattern == "alpha"
    assert blank.rule is not None
    assert blank.rule.pattern == "beta store"


def test_income_rule_does_not_match_expenses_and_vice_versa(session: Session) -> None:
    """A rule made from a group never validates the opposite side of the books."""
    account = _account(session)
    income = _category(session, key="category.income", kind=CategoryKind.INCOME)
    _bulk_transactions(
        session, account=account, description="shop", count=2, amount_minor=500
    )
    purchases = _bulk_transactions(
        session, account=account, description="shop", count=2
    )

    outcome = confirm_group(
        session,
        GroupKey("shop", INCOME),
        GroupClassification(transaction_type="income", category_id=income.id),
        create_rule=True,
    )

    assert outcome.rule is not None
    assert (outcome.rule.amount_min_minor, outcome.rule.amount_max_minor) == (0, None)
    assert outcome.rule_validated_count == 0
    assert _statuses(_refresh(session, purchases)) == {TransactionStatus.PENDING.value}


def test_rule_also_validates_other_pending_rows_it_matches(session: Session) -> None:
    """Rows outside the group that contain the pattern are validated by the rule."""
    account = _account(session)
    food = _category(session)
    _bulk_transactions(session, account=account, description="shop", count=ROWS)
    longer = _transaction(session, account=account, description="shop online")

    outcome = confirm_group(
        session, GroupKey("shop", EXPENSE), _classification(food), create_rule=True
    )

    (longer,) = _refresh(session, [longer])
    assert outcome.rule_validated_count == 1
    assert longer.status == TransactionStatus.VALIDATED.value
    assert longer.validated_by_rule
    assert longer.guided_action_id is None


def test_group_rows_stay_manual_when_the_rule_is_later_edited(
    session: Session,
) -> None:
    """Rows validated in a block keep their classification when the rule changes."""
    account = _account(session)
    food = _category(session)
    other = _category(session, key="category.other")
    person = _person(session)
    rows = _bulk_transactions(session, account=account, description="shop", count=2)
    outcome = confirm_group(
        session,
        GroupKey("shop", EXPENSE),
        _classification(food, person_id=person.id),
        create_rule=True,
    )
    assert outcome.rule is not None

    spec = spec_from_rule(outcome.rule)
    update_rule(
        session,
        outcome.rule.id,
        type(spec)(**{**spec.__dict__, "category_id": other.id, "person_id": None}),
    )
    reclassify_pending(session)

    for row in _refresh(session, rows):
        assert row.category_id == food.id
        assert row.person_id == person.id
        assert row.origin == ClassificationOrigin.MANUAL.value


def test_person_on_a_group_is_a_manual_attribution(session: Session) -> None:
    """The person set on the group lands on every row and survives reclassify."""
    account = _account(session)
    food = _category(session)
    person = _person(session)
    rows = _bulk_transactions(session, account=account, description="shop", count=ROWS)

    confirm_group(
        session, GroupKey("shop", EXPENSE), _classification(food, person_id=person.id)
    )
    reclassify_pending(session)

    assert {row.person_id for row in _refresh(session, rows)} == {person.id}


def test_confirmed_rows_feed_history_suggestions(session: Session) -> None:
    """Rows validated in a block count as validated history for new imports."""
    account = _account(session)
    food = _category(session)
    _bulk_transactions(session, account=account, description="shop", count=2)
    confirm_group(session, GroupKey("shop", EXPENSE), _classification(food))
    new = _transaction(
        session, account=account, description="other text", counterparty="Fresh Market"
    )
    old = _transaction(
        session,
        account=account,
        description="x",
        counterparty="Fresh Market",
        status=TransactionStatus.VALIDATED,
        category=food,
        origin=ClassificationOrigin.MANUAL,
    )
    assert old.status == TransactionStatus.VALIDATED.value

    assert suggest_from_history(session, new.id) is not None


def test_proposal_legs_and_paired_rows_are_never_validated(session: Session) -> None:
    """A block never validates halves of a pending transfer pair as expense."""
    account = _account(session)
    other = _account(session, name="Other")
    food = _category(session)
    group_rows = _bulk_transactions(
        session, account=account, description="topup", count=2, amount_minor=-5000
    )
    leg_out = _transaction(
        session, account=account, description="topup", amount_minor=-7777
    )
    leg_in = _transaction(
        session, account=other, description="deposit", amount_minor=7777
    )
    paired_a = _transaction(
        session, account=account, description="topup", amount_minor=-1
    )
    paired_b = _transaction(session, account=other, description="back", amount_minor=1)
    paired_a.paired_transaction_id = paired_b.id
    paired_b.paired_transaction_id = paired_a.id
    session.commit()

    outcome = confirm_group(
        session, GroupKey("topup", EXPENSE), _classification(food), create_rule=True
    )

    assert outcome.validated_count == len(group_rows)
    legs = _refresh(session, [leg_out, leg_in, paired_a, paired_b])
    assert _statuses(legs) == {TransactionStatus.PENDING.value}
    assert all(row.category_id is None or row.category_id == food.id for row in legs)
    assert not any(row.validated_by_rule for row in legs)


def test_invalid_classification_writes_nothing(session: Session) -> None:
    """An incompatible category is refused before any row changes."""
    account = _account(session)
    income = _category(session, key="category.income", kind=CategoryKind.INCOME)
    rows = _bulk_transactions(session, account=account, description="shop", count=2)

    with pytest.raises(ValidationError):
        confirm_group(
            session,
            GroupKey("shop", EXPENSE),
            _classification(income),
            create_rule=True,
        )

    assert _statuses(_refresh(session, rows)) == {TransactionStatus.PENDING.value}
    assert session.scalar(select(func.count()).select_from(GuidedAction)) == 0


def test_invalid_rule_pattern_writes_nothing(session: Session) -> None:
    """A pattern the rule engine refuses leaves rows pending and creates no action."""
    account = _account(session)
    food = _category(session)
    rows = _bulk_transactions(session, account=account, description="shop", count=2)

    with pytest.raises(RuleValidationError):
        confirm_group(
            session,
            GroupKey("shop", EXPENSE),
            _classification(food),
            create_rule=True,
            rule_pattern="x" * 501,
        )

    assert _statuses(_refresh(session, rows)) == {TransactionStatus.PENDING.value}
    assert session.scalar(select(func.count()).select_from(GuidedAction)) == 0
    assert session.scalar(select(func.count()).select_from(Rule)) == 0


def test_failure_after_validation_rolls_everything_back(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure while reclassifying undoes the rule, the action and the rows."""
    account = _account(session)
    food = _category(session)
    rows = _bulk_transactions(session, account=account, description="shop", count=2)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("fiskal.classification.guided_actions.reclassify_pending", boom)

    with pytest.raises(RuntimeError):
        confirm_group(
            session, GroupKey("shop", EXPENSE), _classification(food), create_rule=True
        )

    assert _statuses(_refresh(session, rows)) == {TransactionStatus.PENDING.value}
    assert session.scalar(select(func.count()).select_from(GuidedAction)) == 0
    assert session.scalar(select(func.count()).select_from(Rule)) == 0


def test_confirm_refuses_when_the_group_changed_or_is_empty(session: Session) -> None:
    """The row count shown must match; an empty group is an error."""
    account = _account(session)
    food = _category(session)
    _bulk_transactions(session, account=account, description="shop", count=ROWS)

    with pytest.raises(GuidedError, match="changed"):
        confirm_group(
            session,
            GroupKey("shop", EXPENSE),
            _classification(food),
            expected_count=ROWS - 1,
        )
    with pytest.raises(GuidedError, match="no pending"):
        confirm_group(session, GroupKey("absent", EXPENSE), _classification(food))


def test_undo_without_rule_returns_rows_to_pending(session: Session) -> None:
    """Undo returns the rows as pending, manual, keeping the classification."""
    account = _account(session)
    food = _category(session)
    person = _person(session)
    rows = _bulk_transactions(session, account=account, description="shop", count=ROWS)
    outcome = confirm_group(
        session, GroupKey("shop", EXPENSE), _classification(food, person_id=person.id)
    )

    undone = undo_guided_action(session, outcome.action.id)

    assert (undone.returned_count, undone.skipped_count) == (ROWS, 0)
    assert not undone.rule_deactivated
    for row in _refresh(session, rows):
        assert row.status == TransactionStatus.PENDING.value
        assert row.guided_action_id is None
        assert row.origin == ClassificationOrigin.MANUAL.value
        assert row.category_id == food.id
        assert row.person_id == person.id
    assert outcome.action.undone_at is not None


def test_undo_with_rule_deactivates_it_and_releases_its_rows(
    session: Session,
) -> None:
    """Undoing a group with a rule turns the rule off and reverts its effects."""
    account = _account(session)
    food = _category(session)
    rows = _bulk_transactions(session, account=account, description="shop", count=ROWS)
    longer = _transaction(session, account=account, description="shop online")
    outcome = confirm_group(
        session, GroupKey("shop", EXPENSE), _classification(food), create_rule=True
    )
    assert outcome.rule is not None

    undone = undo_guided_action(session, outcome.action.id)

    assert undone.rule_deactivated
    session.refresh(outcome.rule)
    assert not outcome.rule.is_active
    assert _statuses(_refresh(session, rows)) == {TransactionStatus.PENDING.value}
    (longer,) = _refresh(session, [longer])
    assert longer.status == TransactionStatus.PENDING.value
    assert not longer.validated_by_rule


def test_undo_twice_or_unknown_action_is_refused(session: Session) -> None:
    """An action can be undone once, and only if it exists."""
    account = _account(session)
    food = _category(session)
    _bulk_transactions(session, account=account, description="shop", count=2)
    outcome = confirm_group(session, GroupKey("shop", EXPENSE), _classification(food))
    undo_guided_action(session, outcome.action.id)

    with pytest.raises(GuidedError, match="already undone"):
        undo_guided_action(session, outcome.action.id)
    with pytest.raises(GuidedError, match="does not exist"):
        undo_guided_action(session, uuid4())


def test_undo_leaves_rows_changed_after_the_action_untouched(
    session: Session,
) -> None:
    """A row reclassified by an explicit reapply is a later decision: undo skips it."""
    account = _account(session)
    food = _category(session)
    other = _category(session, key="category.other")
    rows = _bulk_transactions(session, account=account, description="shop", count=ROWS)
    outcome = confirm_group(session, GroupKey("shop", EXPENSE), _classification(food))
    _rule(session, pattern="shop", category=other)
    changes = [
        change
        for change in preview_reapply_validated(session)
        if change.transaction_id == rows[0].id
    ]
    apply_reapply_validated(session, changes)

    undone = undo_guided_action(session, outcome.action.id)

    assert (undone.returned_count, undone.skipped_count) == (ROWS - 1, 1)
    changed, *rest = _refresh(session, rows)
    assert changed.status == TransactionStatus.VALIDATED.value
    assert changed.category_id == other.id
    assert _statuses(rest) == {TransactionStatus.PENDING.value}


def test_undo_skips_rows_corrected_by_hand_and_unlinked(session: Session) -> None:
    """A hand correction clears the action link, so undo no longer sees the row."""
    account = _account(session)
    food = _category(session)
    other = _category(session, key="category.other")
    rows = _bulk_transactions(session, account=account, description="shop", count=ROWS)
    outcome = confirm_group(session, GroupKey("shop", EXPENSE), _classification(food))
    row = rows[0]
    row.validated_by_rule = True  # simulate a rule-validated row linked to the action
    session.commit()
    validate_transaction(
        session,
        row.id,
        transaction_type="expense",
        category_id=other.id,
        cost_center_id=None,
        person_id=None,
    )

    undone = undo_guided_action(session, outcome.action.id)

    assert undone.returned_count == ROWS - 1
    (row,) = _refresh(session, [row])
    assert row.status == TransactionStatus.VALIDATED.value
    assert row.category_id == other.id
    assert row.guided_action_id is None


def test_undo_rows_returns_only_selected_rows_and_keeps_the_rule(
    session: Session,
) -> None:
    """Row-level undo leaves the action and its rule alone and skips other rows."""
    account = _account(session)
    food = _category(session)
    rows = _bulk_transactions(session, account=account, description="shop", count=ROWS)
    stray = _transaction(
        session,
        account=account,
        description="plain",
        status=TransactionStatus.VALIDATED,
        category=food,
    )
    outcome = confirm_group(
        session, GroupKey("shop", EXPENSE), _classification(food), create_rule=True
    )

    undone = undo_guided_rows(session, [rows[0].id, rows[0].id, stray.id])

    assert (undone.returned_count, undone.skipped_count) == (1, 1)
    assert not undone.rule_deactivated
    assert outcome.rule is not None
    session.refresh(outcome.rule)
    assert outcome.rule.is_active
    first, *rest = _refresh(session, rows)
    assert first.status == TransactionStatus.PENDING.value
    assert _statuses(rest) == {TransactionStatus.VALIDATED.value}
    assert count_guided_actions(session) == 1
    assert (
        len(list_guided_action_rows(session, outcome.action.id, limit=100)) == ROWS - 1
    )


def test_list_guided_actions_newest_first_and_hides_finished_ones(
    session: Session,
) -> None:
    """Only actions with returnable rows are listed, newest first, with paging."""
    account = _account(session)
    food = _category(session)
    _bulk_transactions(session, account=account, description="aaa", count=2)
    _bulk_transactions(session, account=account, description="bbb", count=3)
    first = confirm_group(session, GroupKey("aaa", EXPENSE), _classification(food))
    second = confirm_group(session, GroupKey("bbb", EXPENSE), _classification(food))
    first.action.created_at = second.action.created_at.replace(year=2000)
    session.commit()

    listed = list_guided_actions(session, limit=10)
    assert [(s.action.id, s.remaining_rows) for s in listed] == [
        (second.action.id, 3),
        (first.action.id, 2),
    ]
    assert [s.action.id for s in list_guided_actions(session, limit=1, offset=1)] == [
        first.action.id
    ]
    undo_guided_action(session, second.action.id)
    assert [s.action.id for s in list_guided_actions(session, limit=10)] == [
        first.action.id
    ]
    assert count_guided_actions(session) == 1


def test_deleting_the_rule_of_an_action_clears_the_link(session: Session) -> None:
    """Deleting a rule keeps the action, which can still be undone."""
    account = _account(session)
    food = _category(session)
    _bulk_transactions(session, account=account, description="shop", count=2)
    outcome = confirm_group(
        session, GroupKey("shop", EXPENSE), _classification(food), create_rule=True
    )
    assert outcome.rule is not None

    delete_rule(session, outcome.rule.id)

    session.refresh(outcome.action)
    assert outcome.action.rule_id is None
    undone = undo_guided_action(session, outcome.action.id)
    assert (undone.returned_count, undone.rule_deactivated) == (2, False)


def test_confirm_and_undo_are_committed_even_when_nothing_else_is_pending(
    session: Session,
) -> None:
    """The block is durable when it leaves nothing for the reclassification."""
    account = _account(session)
    food = _category(session)
    rows = _bulk_transactions(session, account=account, description="shop", count=2)

    outcome = confirm_group(
        session, GroupKey("shop", EXPENSE), _classification(food), create_rule=True
    )
    session.rollback()

    assert _statuses(_refresh(session, rows)) == {TransactionStatus.VALIDATED.value}
    undo_guided_action(session, outcome.action.id)
    session.rollback()
    assert _statuses(_refresh(session, rows)) == {TransactionStatus.PENDING.value}
    assert session.get(Rule, outcome.rule.id).is_active is False  # type: ignore[union-attr]
