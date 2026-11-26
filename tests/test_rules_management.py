"""Tests for listing, deactivating and reactivating rules."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from fiskal.classification import (
    RuleNotFoundError,
    classify_pending,
    list_rules,
    set_rule_active,
)
from fiskal.domain.enums import (
    ClassificationOrigin,
    RuleOrigin,
    TransactionStatus,
)
from fiskal.domain.rule_matching import RuleValidationError
from tests.support.factories import _account, _category, _rule, _transaction


def test_list_rules_includes_inactive_in_cascade_order(session: Session) -> None:
    """Every rule is listed, user rules first, then by priority."""
    category = _category(session)
    seed = _rule(
        session, priority=1, pattern="s", category=category, origin=RuleOrigin.SEED
    )
    late = _rule(session, priority=9, pattern="b", category=category)
    early = _rule(session, priority=2, pattern="a", category=category, is_active=False)

    assert [rule.id for rule in list_rules(session)] == [early.id, late.id, seed.id]


def test_deactivate_rule_reclassifies_pending_only(session: Session) -> None:
    """Deactivating a rule leaves validated movements untouched."""
    category = _category(session)
    rule = _rule(session, category=category)
    account = _account(session)
    validated = _transaction(
        session,
        account=account,
        status=TransactionStatus.VALIDATED,
        origin=ClassificationOrigin.MANUAL,
        category=category,
        identity="v",
    )
    pending = _transaction(session, account=account, identity="p")
    classify_pending(session)
    assert pending.rule_id == rule.id

    set_rule_active(session, rule.id, active=False)

    assert not rule.is_active
    assert pending.rule_id is None
    assert validated.category_id == category.id
    assert validated.status == TransactionStatus.VALIDATED.value


def test_reactivate_rule_classifies_pending(session: Session) -> None:
    """Reactivating a rule applies it to pending movements again."""
    category = _category(session)
    rule = _rule(session, category=category, is_active=False)
    pending = _transaction(session, account=_account(session))

    set_rule_active(session, rule.id, active=True)

    assert rule.is_active
    assert pending.category_id == category.id


def test_reactivate_rule_with_inactive_category_is_refused(session: Session) -> None:
    """A rule cannot be revived while its category is deactivated."""
    category = _category(session)
    rule = _rule(session, category=category, is_active=False)
    category.is_active = False
    session.commit()

    with pytest.raises(RuleValidationError):
        set_rule_active(session, rule.id, active=True)

    assert not rule.is_active


def test_deactivate_rule_with_inactive_category_is_allowed(session: Session) -> None:
    """Deactivating never needs the category to be active."""
    category = _category(session)
    rule = _rule(session, category=category)
    category.is_active = False
    session.commit()

    set_rule_active(session, rule.id, active=False)

    assert not rule.is_active


def test_set_rule_active_unknown_rule(session: Session) -> None:
    """A missing rule raises the not-found error."""
    with pytest.raises(RuleNotFoundError):
        set_rule_active(session, uuid4(), active=False)
