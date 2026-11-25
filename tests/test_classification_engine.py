"""Tests for the classification engine, rule CRUD and neutral seeding.

All data is synthetic; no personal or bank-specific information is used.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from fiskal.classification import (
    apply_reapply_validated,
    classify_pending,
    create_rule,
    delete_rule,
    find_rule_collisions,
    preview_reapply_validated,
    propose_rule_from_correction,
    reclassify_pending,
    seed_neutral_data,
    update_rule,
)
from fiskal.db.base import Base
from fiskal.db.engine import make_engine
from fiskal.db.models import Account, Category, CostCenter, Rule, Transaction
from fiskal.domain.enums import (
    CategoryKind,
    CategoryNature,
    ClassificationOrigin,
    RuleMatchMode,
    RuleOrigin,
    TransactionStatus,
    TransactionType,
)
from fiskal.domain.rule_matching import RuleCondition, RuleValidationError
from fiskal.domain.seed import DEFAULT_CATEGORIES, DEFAULT_COST_CENTERS, DEFAULT_RULES


@pytest.fixture
def session() -> Session:
    """Return a session on an empty in-memory database with FKs enabled."""
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _account(
    session: Session, *, name: str = "Account", default_cc: CostCenter | None = None
) -> Account:
    account = Account(
        name=name,
        currency_code="EUR",
        default_cost_center_id=default_cc.id if default_cc else None,
    )
    session.add(account)
    session.commit()
    return account


def _category(
    session: Session,
    *,
    key: str = "category.food",
    kind: CategoryKind = CategoryKind.EXPENSE,
) -> Category:
    category = Category(
        name_key=key,
        kind=kind.value,
        nature=CategoryNature.VARIABLE.value,
    )
    session.add(category)
    session.commit()
    return category


def _cost_center(session: Session, *, key: str = "costcenter.household") -> CostCenter:
    cost_center = CostCenter(name_key=key)
    session.add(cost_center)
    session.commit()
    return cost_center


def _rule(  # noqa: PLR0913
    session: Session,
    *,
    priority: int = 1,
    pattern: str = "grocery",
    mode: str = "contains",
    transaction_type: str = "expense",
    category: Category,
    cost_center: CostCenter | None = None,
    origin: RuleOrigin = RuleOrigin.USER,
    is_active: bool = True,
) -> Rule:
    rule = Rule(
        priority=priority,
        mode=mode,
        pattern=pattern,
        transaction_type=transaction_type,
        category_id=category.id,
        cost_center_id=cost_center.id if cost_center else None,
        origin=origin.value,
        is_active=is_active,
    )
    session.add(rule)
    session.commit()
    return rule


def _transaction(  # noqa: PLR0913
    session: Session,
    *,
    account: Account,
    description: str = "grocery store",
    counterparty: str | None = "Fresh Market",
    amount_minor: int = -1000,
    day: int = 15,
    status: TransactionStatus = TransactionStatus.PENDING,
    origin: ClassificationOrigin = ClassificationOrigin.MANUAL,
    category: Category | None = None,
    cost_center: CostCenter | None = None,
    transaction_type: TransactionType | None = None,
    identity: str | None = None,
) -> Transaction:
    txn = Transaction(
        account_id=account.id,
        operation_date=date(2024, 1, day),
        value_date=date(2024, 1, day),
        original_description=description,
        normalized_description=description,
        counterparty=counterparty,
        amount_minor=amount_minor,
        balance_minor=0,
        currency_code="EUR",
        transaction_type=(
            transaction_type.value
            if transaction_type
            else (
                TransactionType.INCOME.value
                if amount_minor >= 0
                else TransactionType.EXPENSE.value
            )
        ),
        cost_center_id=cost_center.id if cost_center else None,
        category_id=category.id if category else None,
        origin=origin.value,
        status=status.value,
        identity_key=identity or str(uuid4()),
    )
    session.add(txn)
    session.commit()
    return txn


def test_rule_layer_classifies_and_records_origin(session: Session) -> None:
    """A matching rule sets type, cost center, category, origin and rule id."""
    account = _account(session)
    category = _category(session)
    cost_center = _cost_center(session)
    rule = _rule(session, category=category, cost_center=cost_center)
    txn = _transaction(session, account=account)

    result = classify_pending(session)

    session.refresh(txn)
    assert result.classified_by_rule == 1
    assert result.unclassified == 0
    assert txn.category_id == category.id
    assert txn.cost_center_id == cost_center.id
    assert txn.transaction_type == TransactionType.EXPENSE.value
    assert txn.origin == ClassificationOrigin.RULE.value
    assert txn.rule_id == rule.id


def test_rule_hit_count_and_last_hit_are_updated(session: Session) -> None:
    """A matched rule records a hit and the time of the hit."""
    account = _account(session)
    category = _category(session)
    rule = _rule(session, category=category)
    _transaction(session, account=account)

    classify_pending(session)

    session.refresh(rule)
    assert rule.hit_count == 1
    assert rule.last_hit_at is not None


def test_user_rule_beats_seed_rule_regardless_of_priority(session: Session) -> None:
    """User rules always win over seed rules, whatever the numeric priority."""
    account = _account(session)
    seed_category = _category(session, key="category.misc")
    user_category = _category(session, key="category.food")
    _rule(
        session,
        priority=500,
        pattern="grocery",
        category=user_category,
        origin=RuleOrigin.USER,
    )
    _rule(
        session,
        priority=1,
        pattern="grocery",
        category=seed_category,
        origin=RuleOrigin.SEED,
    )
    txn = _transaction(session, account=account)

    classify_pending(session)

    session.refresh(txn)
    assert txn.category_id == user_category.id


def test_lower_priority_wins_within_group(session: Session) -> None:
    """Inside a group the lower numeric priority is matched first."""
    account = _account(session)
    first = _category(session, key="category.food")
    second = _category(session, key="category.misc")
    _rule(session, priority=10, pattern="grocery", category=first)
    _rule(session, priority=1, pattern="grocery", category=second)
    txn = _transaction(session, account=account)

    classify_pending(session)

    session.refresh(txn)
    assert txn.category_id == second.id


def test_priority_tie_breaks_deterministically_on_id(session: Session) -> None:
    """Two equal-priority matches resolve by id, not by insertion order."""
    account = _account(session)
    first = _category(session, key="category.food")
    second = _category(session, key="category.misc")
    rule_a = _rule(session, priority=1, pattern="grocery", category=first)
    rule_b = _rule(session, priority=1, pattern="grocery", category=second)
    txn = _transaction(session, account=account)

    classify_pending(session)

    session.refresh(txn)
    winner = min(rule_a, rule_b, key=lambda rule: str(rule.id))
    assert txn.category_id == winner.category_id


def test_history_single_outcome_classifies(session: Session) -> None:
    """A counterparty validated only one way is proposed with origin history."""
    account = _account(session)
    category = _category(session)
    cost_center = _cost_center(session)
    _transaction(
        session,
        account=account,
        counterparty="Fresh Market",
        status=TransactionStatus.VALIDATED,
        origin=ClassificationOrigin.HISTORY,
        category=category,
        cost_center=cost_center,
        transaction_type=TransactionType.EXPENSE,
    )
    pending = _transaction(session, account=account, counterparty="Fresh Market")

    result = classify_pending(session)

    session.refresh(pending)
    assert result.classified_by_history == 1
    assert pending.category_id == category.id
    assert pending.cost_center_id == cost_center.id
    assert pending.origin == ClassificationOrigin.HISTORY.value


def test_history_several_outcomes_stays_unclassified(session: Session) -> None:
    """Several validated outcomes leave the transaction in the queue."""
    account = _account(session)
    food = _category(session, key="category.food")
    misc = _category(session, key="category.misc")
    _transaction(
        session,
        account=account,
        counterparty="Fresh Market",
        status=TransactionStatus.VALIDATED,
        category=food,
        transaction_type=TransactionType.EXPENSE,
    )
    _transaction(
        session,
        account=account,
        counterparty="Fresh Market",
        status=TransactionStatus.VALIDATED,
        category=misc,
        transaction_type=TransactionType.EXPENSE,
    )
    pending = _transaction(session, account=account, counterparty="Fresh Market")

    result = classify_pending(session)

    session.refresh(pending)
    assert pending.category_id is None
    assert result.classified_by_history == 0
    assert result.unclassified == 1
    assert len(result.history_suggestions) == 1
    suggestion = result.history_suggestions[0]
    assert suggestion.transaction_id == pending.id
    assert suggestion.category_id in {food.id, misc.id}


def test_history_most_frequent_outcome_is_suggested(session: Session) -> None:
    """The most frequent validated outcome is returned as the suggestion."""
    account = _account(session)
    food = _category(session, key="category.food")
    misc = _category(session, key="category.misc")
    for _ in range(3):
        _transaction(
            session,
            account=account,
            counterparty="Fresh Market",
            status=TransactionStatus.VALIDATED,
            category=food,
            transaction_type=TransactionType.EXPENSE,
        )
    _transaction(
        session,
        account=account,
        counterparty="Fresh Market",
        status=TransactionStatus.VALIDATED,
        category=misc,
        transaction_type=TransactionType.EXPENSE,
    )
    _transaction(session, account=account, counterparty="Fresh Market")

    result = classify_pending(session)

    assert len(result.history_suggestions) == 1
    assert result.history_suggestions[0].category_id == food.id


def test_default_cost_center_only_fills_cost_center(session: Session) -> None:
    """The account default fills only the cost center, never type or category."""
    cost_center = _cost_center(session)
    account = _account(session, default_cc=cost_center)
    txn = _transaction(session, account=account, amount_minor=-500)

    result = classify_pending(session)

    session.refresh(txn)
    assert result.classified_by_default == 1
    assert result.unclassified == 1
    assert txn.cost_center_id == cost_center.id
    assert txn.category_id is None
    assert txn.transaction_type == TransactionType.EXPENSE.value
    assert txn.origin == ClassificationOrigin.MANUAL.value


def test_default_cost_center_never_overrides_earlier_cost_center(
    session: Session,
) -> None:
    """A cost center already set by a rule is not replaced by the default."""
    default_cc = _cost_center(session, key="costcenter.personal")
    rule_cc = _cost_center(session, key="costcenter.household")
    account = _account(session, default_cc=default_cc)
    category = _category(session)
    _rule(session, category=category, cost_center=rule_cc)
    txn = _transaction(session, account=account)

    classify_pending(session)

    session.refresh(txn)
    assert txn.cost_center_id == rule_cc.id


def test_engine_never_touches_validated_transactions(session: Session) -> None:
    """A validated row is left alone even when a rule would match it."""
    account = _account(session)
    category = _category(session)
    _rule(session, category=category)
    txn = _transaction(
        session,
        account=account,
        status=TransactionStatus.VALIDATED,
        category=_category(session, key="category.misc"),
        origin=ClassificationOrigin.MANUAL,
        transaction_type=TransactionType.EXPENSE,
    )
    original_category = txn.category_id

    classify_pending(session)

    session.refresh(txn)
    assert txn.category_id == original_category
    assert txn.origin == ClassificationOrigin.MANUAL.value


def test_reclassify_pending_resets_and_reapplies_rules(session: Session) -> None:
    """Reclassification resets engine-derived pending rows and re-runs rules."""
    account = _account(session)
    food = _category(session, key="category.food")
    misc = _category(session, key="category.misc")
    _rule(session, priority=1, pattern="grocery", category=food)
    txn = _transaction(session, account=account, description="grocery store")
    classify_pending(session)
    session.refresh(txn)
    assert txn.category_id == food.id

    _rule(session, priority=0, pattern="grocery", category=misc)
    reclassify_pending(session)

    session.refresh(txn)
    assert txn.category_id == misc.id


def test_preview_reapply_validated_reports_without_writing(session: Session) -> None:
    """The reapply preview lists changes but does not modify anything."""
    account = _account(session)
    food = _category(session, key="category.food")
    shopping = _category(session, key="category.shopping")
    rule = _rule(session, category=shopping)
    txn = _transaction(
        session,
        account=account,
        description="grocery store",
        status=TransactionStatus.VALIDATED,
        category=food,
        origin=ClassificationOrigin.MANUAL,
        transaction_type=TransactionType.EXPENSE,
    )

    changes = preview_reapply_validated(session)

    assert len(changes) == 1
    assert changes[0].transaction_id == txn.id
    assert changes[0].rule_id == rule.id
    assert changes[0].category_id == shopping.id
    session.refresh(txn)
    assert txn.category_id == food.id
    assert txn.origin == ClassificationOrigin.MANUAL.value


def test_apply_reapply_validated_writes_changes(session: Session) -> None:
    """Applying a preview updates validated rows and the rule's hit count."""
    account = _account(session)
    food = _category(session, key="category.food")
    shopping = _category(session, key="category.shopping")
    rule = _rule(session, category=shopping)
    txn = _transaction(
        session,
        account=account,
        description="grocery store",
        status=TransactionStatus.VALIDATED,
        category=food,
        origin=ClassificationOrigin.MANUAL,
        transaction_type=TransactionType.EXPENSE,
    )

    changes = preview_reapply_validated(session)
    apply_reapply_validated(session, changes)

    session.refresh(txn)
    session.refresh(rule)
    assert txn.category_id == shopping.id
    assert txn.origin == ClassificationOrigin.RULE.value
    assert txn.rule_id == rule.id
    assert rule.hit_count == 1


def test_create_rule_validates_regex(session: Session) -> None:
    """Creating a rule with a pathological regex is rejected."""
    category = _category(session)
    with pytest.raises(RuleValidationError):
        create_rule(
            session,
            priority=1,
            mode=RuleMatchMode.REGEX,
            pattern="(a+)+",
            transaction_type=TransactionType.EXPENSE,
            category_id=category.id,
        )


def test_create_rule_rejects_internal_transfer_type(session: Session) -> None:
    """Rules never assign the internal_transfer type."""
    category = _category(session)
    with pytest.raises(RuleValidationError, match="internal_transfer"):
        create_rule(
            session,
            priority=1,
            mode=RuleMatchMode.CONTAINS,
            pattern="transfer",
            transaction_type=TransactionType.INTERNAL_TRANSFER,
            category_id=category.id,
        )


def test_create_rule_rejects_incompatible_category_kind(session: Session) -> None:
    """A rule's type and category kind must agree."""
    income = _category(session, key="category.income", kind=CategoryKind.INCOME)
    with pytest.raises(RuleValidationError, match="incompatible"):
        create_rule(
            session,
            priority=1,
            mode=RuleMatchMode.CONTAINS,
            pattern="salary",
            transaction_type=TransactionType.EXPENSE,
            category_id=income.id,
        )


def test_update_rule_reclassifies_only_pending(session: Session) -> None:
    """Updating a rule reclassifies pending rows and leaves validated ones."""
    account = _account(session)
    food = _category(session, key="category.food")
    misc = _category(session, key="category.misc")
    rule = _rule(session, pattern="grocery", category=food)
    pending = _transaction(session, account=account, description="grocery store")
    validated = _transaction(
        session,
        account=account,
        description="grocery store",
        counterparty=None,
        status=TransactionStatus.VALIDATED,
        category=food,
        origin=ClassificationOrigin.RULE,
        transaction_type=TransactionType.EXPENSE,
    )
    classify_pending(session)

    update_rule(
        session,
        rule.id,
        priority=1,
        mode=RuleMatchMode.CONTAINS,
        pattern="rent",
        transaction_type=TransactionType.EXPENSE,
        category_id=misc.id,
        cost_center_id=None,
        amount_min_minor=None,
        amount_max_minor=None,
        day_of_month_min=None,
        day_of_month_max=None,
        is_active=True,
    )

    session.refresh(pending)
    session.refresh(validated)
    assert pending.category_id is None
    assert validated.category_id == food.id


def test_delete_rule_clears_rule_link_and_reclassifies(session: Session) -> None:
    """Deleting a rule clears its link and reclassifies pending rows."""
    account = _account(session)
    category = _category(session)
    rule = _rule(session, category=category)
    txn = _transaction(session, account=account, description="grocery store")
    classify_pending(session)
    session.refresh(txn)
    assert txn.rule_id == rule.id

    delete_rule(session, rule.id)

    session.refresh(txn)
    assert txn.rule_id is None
    assert txn.category_id is None


def test_propose_rule_from_correction_places_above_classifier(
    session: Session,
) -> None:
    """A correction proposes an editable contains rule above the classifier."""
    account = _account(session)
    category = _category(session)
    priority = 5
    rule = _rule(session, priority=priority, category=category)
    txn = _transaction(session, account=account, description="grocery store")
    classify_pending(session)
    session.refresh(txn)

    proposal = propose_rule_from_correction(
        session,
        txn.id,
        transaction_type=TransactionType.EXPENSE,
        category_id=category.id,
        cost_center_id=None,
    )

    assert proposal.mode is RuleMatchMode.CONTAINS
    assert proposal.pattern == "Fresh Market"
    assert proposal.priority == rule.priority - 1
    session.refresh(rule)
    assert rule.priority == priority


def test_find_rule_collisions_reports_overlap(session: Session) -> None:
    """The service helper reports overlapping active rules."""
    category = _category(session)
    _rule(session, pattern="grocery store", category=category)
    collisions = find_rule_collisions(
        session, RuleCondition(mode=RuleMatchMode.CONTAINS, pattern="grocery")
    )
    assert len(collisions) == 1


def test_seed_neutral_data_is_idempotent(session: Session) -> None:
    """Seeding inserts defaults once and changes nothing on a re-run."""
    first = seed_neutral_data(session)
    assert first.categories_created == len(DEFAULT_CATEGORIES)
    assert first.cost_centers_created == len(DEFAULT_COST_CENTERS)
    assert first.rules_created == len(DEFAULT_RULES)

    second = seed_neutral_data(session)
    assert second.categories_created == 0
    assert second.cost_centers_created == 0
    assert second.rules_created == 0

    rules = session.query(Rule).filter_by(origin=RuleOrigin.SEED.value).all()
    assert len(rules) == len(DEFAULT_RULES)


def test_seed_neutral_data_never_overwrites_custom_names(session: Session) -> None:
    """A customised category name survives a second seeding run."""
    seed_neutral_data(session)
    category = session.query(Category).filter_by(name_key="category.food").one()
    category.custom_name = "Groceries"
    session.commit()

    seed_neutral_data(session)

    session.refresh(category)
    assert category.custom_name == "Groceries"
