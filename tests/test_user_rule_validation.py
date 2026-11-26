"""Regression tests: rules created by the owner validate matching rows.

Reproduces the alpha-test bug where creating a rule from a correction left the
other matching pending movements in the queue (classified, never validated).
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from fiskal.classification import (
    RuleSpec,
    ValidationError,
    classify_pending,
    confirm_pair,
    create_rule,
    create_rule_from_correction,
    delete_rule,
    list_rule_validated_transactions,
    reclassify_pending,
    reject_pair,
    return_to_queue,
    suggest_from_history,
    update_rule,
    validate_and_create_rule,
    validate_transaction,
)
from fiskal.db.models import Import
from fiskal.domain.enums import (
    CategoryKind,
    ClassificationOrigin,
    RuleOrigin,
    TransactionStatus,
)
from fiskal.domain.rule_matching import RuleValidationError
from fiskal.reports.helpers import TransactionFilter, list_transactions
from fiskal.services import HomeOverview, home_overview
from tests.support.factories import (
    _account,
    _category,
    _person,
    _rule,
    _transaction,
)


def test_rule_from_correction_clears_every_matching_pending_row(
    session: Session,
) -> None:
    """After a rule is created, all matching pending rows leave the queue."""
    account = _account(session)
    food = _category(session, key="category.food")
    first = _transaction(session, account=account, counterparty="Fresh Market")
    others = [
        _transaction(
            session,
            account=account,
            counterparty="Fresh Market",
            day=day,
            identity=f"fm-{day}",
        )
        for day in (16, 17, 18)
    ]

    create_rule_from_correction(
        session,
        first.id,
        transaction_type="expense",
        category_id=food.id,
        cost_center_id=None,
        person_id=None,
    )
    validate_transaction(
        session,
        first.id,
        transaction_type="expense",
        category_id=food.id,
        cost_center_id=None,
        person_id=None,
    )

    for txn in others:
        session.refresh(txn)
        assert txn.status == TransactionStatus.VALIDATED.value
        assert txn.origin == ClassificationOrigin.RULE.value
        assert txn.category_id == food.id


def test_import_classification_validates_by_user_rule_only(session: Session) -> None:
    """User rules validate; seed rules only preselect and stay pending."""
    account = _account(session)
    food = _category(session, key="category.food")
    misc = _category(session, key="category.misc")
    _rule(session, pattern="fresh", category=food, origin=RuleOrigin.USER)
    _rule(session, pattern="other", category=misc, origin=RuleOrigin.SEED)
    user_match = _transaction(session, account=account, counterparty="Fresh Market")
    seed_match = _transaction(
        session, account=account, counterparty="Other Shop", identity="seed"
    )

    classify_pending(session)

    session.refresh(user_match)
    session.refresh(seed_match)
    assert user_match.status == TransactionStatus.VALIDATED.value
    assert seed_match.status == TransactionStatus.PENDING.value
    assert seed_match.category_id == misc.id


def _spec_kwargs(food_id, *, pattern: str = "fresh"):
    return {
        "priority": 1,
        "mode": "contains",
        "pattern": pattern,
        "transaction_type": "expense",
        "category_id": food_id,
        "cost_center_id": None,
        "person_id": None,
        "amount_min_minor": None,
        "amount_max_minor": None,
        "day_of_month_min": None,
        "day_of_month_max": None,
        "is_active": True,
    }


def test_validate_and_create_rule_counts_other_rows_and_keeps_correction_manual(
    session: Session,
) -> None:
    """The corrected row stays manual; the count covers only the other rows."""
    account = _account(session)
    food = _category(session, key="category.food")
    first = _transaction(session, account=account, counterparty="Fresh Market")
    others = [
        _transaction(
            session,
            account=account,
            counterparty="Fresh Market",
            day=day,
            identity=f"fm-{day}",
        )
        for day in (16, 17)
    ]
    unrelated = _transaction(
        session, account=account, counterparty="Elsewhere", identity="x"
    )

    outcome = validate_and_create_rule(
        session,
        first.id,
        transaction_type="expense",
        category_id=food.id,
        cost_center_id=None,
        person_id=None,
    )

    assert outcome.validated_count == len(others)
    session.refresh(first)
    assert first.origin == ClassificationOrigin.MANUAL.value
    assert first.validated_by_rule is False
    for txn in others:
        session.refresh(txn)
        assert txn.validated_by_rule is True
        assert txn.rule_id == outcome.rule.id
    session.refresh(unrelated)
    assert unrelated.status == TransactionStatus.PENDING.value


def test_validated_count_skips_rows_already_validated_by_another_rule(
    session: Session,
) -> None:
    """Rows another user rule had validated are not counted as cleared."""
    account = _account(session)
    food = _category(session, key="category.food")
    create_rule(session, RuleSpec(**_spec_kwargs(food.id, pattern="market")))
    already = _transaction(
        session, account=account, counterparty="Fresh Market", identity="a"
    )
    classify_pending(session)
    session.refresh(already)
    assert already.validated_by_rule is True
    first = _transaction(
        session, account=account, counterparty="Fresh Market", identity="f", day=16
    )
    pending = _transaction(
        session, account=account, counterparty="Fresh Market", identity="p", day=17
    )
    pending.category_id = food.id
    pending.origin = ClassificationOrigin.HISTORY.value
    session.commit()

    outcome = validate_and_create_rule(
        session,
        first.id,
        transaction_type="expense",
        category_id=food.id,
        cost_center_id=None,
        person_id=None,
    )

    session.refresh(already)
    session.refresh(pending)
    assert already.rule_id == outcome.rule.id
    assert pending.rule_id == outcome.rule.id
    assert outcome.validated_count == 1


def test_invalid_rule_leaves_correction_unvalidated(session: Session) -> None:
    """A rule that fails validation writes nothing, not even the correction."""
    account = _account(session)
    income = _category(session, key="category.income", kind=CategoryKind.INCOME)
    txn = _transaction(session, account=account)

    with pytest.raises(RuleValidationError):
        validate_and_create_rule(
            session,
            txn.id,
            transaction_type="expense",
            category_id=income.id,
            cost_center_id=None,
            person_id=None,
        )

    session.refresh(txn)
    assert txn.status == TransactionStatus.PENDING.value


def test_seed_rule_and_history_only_preselect(session: Session) -> None:
    """Seed rules and the history layer never validate."""
    account = _account(session)
    food = _category(session, key="category.food")
    misc = _category(session, key="category.misc")
    _rule(session, pattern="seeded", category=misc, origin=RuleOrigin.SEED)
    _transaction(
        session,
        account=account,
        counterparty="Known Shop",
        status=TransactionStatus.VALIDATED,
        category=food,
        identity="old",
    )
    by_seed = _transaction(
        session, account=account, description="seeded", counterparty="A", identity="a"
    )
    by_history = _transaction(
        session, account=account, counterparty="Known Shop", identity="b", day=20
    )

    result = classify_pending(session)

    assert result.validated_by_rule == 0
    for txn, category in ((by_seed, misc), (by_history, food)):
        session.refresh(txn)
        assert txn.status == TransactionStatus.PENDING.value
        assert txn.category_id == category.id
        assert txn.validated_by_rule is False


def test_rule_person_is_rule_owned_and_hand_person_stays(session: Session) -> None:
    """A person set by a rule follows the rule; one set by hand is never cleared."""
    account = _account(session)
    food = _category(session, key="category.food")
    ada = _person(session, name="Ada")
    bob = _person(session, name="Bob")
    spec = _spec_kwargs(food.id)
    spec["person_id"] = ada.id
    rule = create_rule(session, RuleSpec(**spec))
    by_rule = _transaction(session, account=account, counterparty="Fresh Market")
    classify_pending(session)
    session.refresh(by_rule)
    assert by_rule.person_id == ada.id
    assert by_rule.validated_by_rule is True

    hand = _transaction(
        session, account=account, counterparty="Other", identity="hand", person=bob
    )
    hand.status = TransactionStatus.VALIDATED.value
    hand.category_id = food.id
    session.commit()

    delete_rule(session, rule.id)

    session.refresh(by_rule)
    session.refresh(hand)
    assert by_rule.person_id is None
    assert by_rule.status == TransactionStatus.PENDING.value
    assert hand.person_id == bob.id
    assert hand.status == TransactionStatus.VALIDATED.value


def test_rule_edit_follows_rule_validated_rows_but_not_hand_validated(
    session: Session,
) -> None:
    """Editing a rule re-evaluates its own rows and never touches manual ones."""
    account = _account(session)
    food = _category(session, key="category.food")
    misc = _category(session, key="category.misc")
    rule = create_rule(session, RuleSpec(**_spec_kwargs(food.id)))
    auto = _transaction(session, account=account, counterparty="Fresh Market")
    classify_pending(session)
    manual = _transaction(
        session,
        account=account,
        counterparty="Fresh Market",
        identity="manual",
        status=TransactionStatus.VALIDATED,
        category=food,
    )

    update_rule(session, rule.id, RuleSpec(**{**_spec_kwargs(misc.id)}))

    session.refresh(auto)
    session.refresh(manual)
    assert auto.category_id == misc.id
    assert auto.validated_by_rule is True
    assert manual.category_id == food.id
    assert manual.origin == ClassificationOrigin.MANUAL.value


def test_correcting_a_rule_validated_row_sticks_as_manual(session: Session) -> None:
    """The owner can correct an auto-validated row and a rule change keeps it."""
    account = _account(session)
    food = _category(session, key="category.food")
    misc = _category(session, key="category.misc")
    rule = create_rule(session, RuleSpec(**_spec_kwargs(food.id)))
    txn = _transaction(session, account=account, counterparty="Fresh Market")
    classify_pending(session)
    session.refresh(txn)
    assert txn.validated_by_rule is True

    validate_transaction(
        session,
        txn.id,
        transaction_type="expense",
        category_id=misc.id,
        cost_center_id=None,
        person_id=None,
    )
    delete_rule(session, rule.id)

    session.refresh(txn)
    assert txn.validated_by_rule is False
    assert txn.origin == ClassificationOrigin.MANUAL.value
    assert txn.category_id == misc.id
    assert txn.status == TransactionStatus.VALIDATED.value


def test_hand_validated_rows_cannot_be_corrected_or_sent_back(
    session: Session,
) -> None:
    """Only rule-validated rows can be sent back; hand-validated ones are final."""
    account = _account(session)
    food = _category(session, key="category.food")
    txn = _transaction(
        session,
        account=account,
        status=TransactionStatus.VALIDATED,
        category=food,
    )

    with pytest.raises(ValidationError):
        return_to_queue(session, txn.id)


def test_return_to_queue_keeps_classification_and_blocks_revalidation(
    session: Session,
) -> None:
    """A sent-back row is pending, preselected and ignored by later rule changes."""
    account = _account(session)
    food = _category(session, key="category.food")
    create_rule(session, RuleSpec(**_spec_kwargs(food.id)))
    txn = _transaction(session, account=account, counterparty="Fresh Market")
    classify_pending(session)

    return_to_queue(session, txn.id)
    reclassify_pending(session)

    session.refresh(txn)
    assert txn.status == TransactionStatus.PENDING.value
    assert txn.validated_by_rule is False
    assert txn.category_id == food.id
    assert txn.origin == ClassificationOrigin.MANUAL.value
    assert [t.id for t in list_rule_validated_transactions(session)] == []


def test_rule_validated_row_does_not_feed_history(session: Session) -> None:
    """History suggestions come from the owner's own validations only."""
    account = _account(session)
    food = _category(session, key="category.food")
    create_rule(session, RuleSpec(**_spec_kwargs(food.id)))
    _transaction(session, account=account, counterparty="Fresh Market")
    classify_pending(session)
    later = _transaction(
        session, account=account, counterparty="Fresh Market", identity="l", day=20
    )
    assert suggest_from_history(session, later.id) is None


def test_proposed_transfer_legs_stay_pending(session: Session) -> None:
    """A user rule never validates a row that has an open pair proposal."""
    source = _account(session, name="Source")
    target = _account(session, name="Target")
    misc = _category(session, key="category.misc")
    create_rule(session, RuleSpec(**_spec_kwargs(misc.id, pattern="move")))
    left = _transaction(
        session,
        account=source,
        description="move money",
        counterparty="Move",
        amount_minor=-5000,
        identity="l",
    )
    right = _transaction(
        session,
        account=target,
        description="incoming",
        counterparty="Other",
        amount_minor=5000,
        identity="r",
    )

    classify_pending(session)

    session.refresh(left)
    session.refresh(right)
    assert left.status == TransactionStatus.PENDING.value
    assert left.category_id == misc.id
    confirm_pair(session, left.id, right.id)


def test_rejecting_a_pair_validates_the_rule_matched_leg(session: Session) -> None:
    """Once the proposal is rejected, a user rule validates the held leg."""
    source = _account(session, name="Source")
    target = _account(session, name="Target")
    misc = _category(session, key="category.misc")
    create_rule(session, RuleSpec(**_spec_kwargs(misc.id, pattern="move")))
    left = _transaction(
        session,
        account=source,
        description="move money",
        counterparty="Move",
        amount_minor=-5000,
        identity="l",
    )
    right = _transaction(
        session,
        account=target,
        description="incoming",
        counterparty="Other",
        amount_minor=5000,
        identity="r",
    )
    classify_pending(session)

    reject_pair(session, left.id, right.id)

    session.refresh(left)
    session.refresh(right)
    assert left.status == TransactionStatus.VALIDATED.value
    assert left.validated_by_rule is True
    assert left.category_id == misc.id
    assert right.status == TransactionStatus.PENDING.value


def test_rule_validated_leg_can_be_paired_later(session: Session) -> None:
    """Pairing releases a leg a rule validated before its counterpart arrived."""
    source = _account(session, name="Source")
    target = _account(session, name="Target")
    misc = _category(session, key="category.misc")
    create_rule(session, RuleSpec(**_spec_kwargs(misc.id, pattern="move")))
    left = _transaction(
        session,
        account=source,
        description="move money",
        counterparty="Move",
        amount_minor=-5000,
        identity="l",
    )
    classify_pending(session)
    session.refresh(left)
    assert left.validated_by_rule is True
    right = _transaction(
        session,
        account=target,
        description="incoming",
        counterparty="Other",
        amount_minor=5000,
        identity="r",
    )

    confirm_pair(session, left.id, right.id)

    session.refresh(left)
    assert left.validated_by_rule is False
    assert left.status == TransactionStatus.PENDING.value
    assert left.paired_transaction_id == right.id


def test_list_transactions_filters_rule_validated(session: Session) -> None:
    """The report filter separates rule-validated rows from the rest."""
    account = _account(session)
    food = _category(session, key="category.food")
    create_rule(session, RuleSpec(**_spec_kwargs(food.id)))
    auto = _transaction(session, account=account, counterparty="Fresh Market")
    other = _transaction(
        session, account=account, counterparty="Elsewhere", identity="o"
    )
    classify_pending(session)

    found = list_transactions(session, TransactionFilter(validated_by_rule=True))
    rest = list_transactions(session, TransactionFilter(validated_by_rule=False))

    assert [t.id for t in found] == [auto.id]
    assert [t.id for t in rest] == [other.id]


def test_home_overview_counts_pending_and_last_import(session: Session) -> None:
    """The Home figures: pending queue size and the newest import date."""
    account = _account(session)
    assert home_overview(session) == HomeOverview(0, None)
    _transaction(session, account=account)
    _transaction(
        session, account=account, identity="v", status=TransactionStatus.VALIDATED
    )
    session.add(Import(account_id=account.id, file_name="a.xls", file_hash="h"))
    session.commit()

    overview = home_overview(session)

    assert overview.pending_count == 1
    assert overview.last_import_at is not None
