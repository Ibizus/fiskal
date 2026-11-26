"""Rule CRUD with automatic reclassification of pending transactions.

Creating, updating or deleting a rule reclassifies only pending transactions;
validated history never changes on its own. Rules the owner creates validate
the pending transactions they match, so creating one clears them from the
queue; ``count_rule_validated`` reports how many a rule has validated. The
bulk reapply over validated history is a separate, explicit function with a preview.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from fiskal.classification.engine import (
    reclassify_pending,
    rule_to_condition,
    sorted_active_rules,
)
from fiskal.classification.validation_service import validate_transaction
from fiskal.db.models import Category, CostCenter, Person, Rule, Transaction
from fiskal.domain.enums import (
    CategoryKind,
    ClassificationOrigin,
    RuleMatchMode,
    RuleOrigin,
    TransactionStatus,
    TransactionType,
)
from fiskal.domain.rule_matching import (
    RuleCollision,
    RuleCondition,
    RuleValidationError,
    find_collisions,
    validate_condition,
)


class RuleNotFoundError(Exception):
    """Raised when a rule or transaction does not exist."""


@dataclass(frozen=True)
class RuleProposal:
    """A proposed rule, ready for the editor to adjust before saving."""

    mode: RuleMatchMode
    pattern: str
    transaction_type: TransactionType
    category_id: UUID
    cost_center_id: UUID | None
    priority: int


@dataclass(frozen=True)
class RuleSpec:
    """The editable specification of a rule, shared by create and update.

    This is the full set of fields the rule editor collects. Every field must
    be stated, so an update replaces the whole rule and never silently resets
    an omitted field to a default.
    """

    priority: int
    mode: RuleMatchMode | str
    pattern: str
    transaction_type: TransactionType | str
    category_id: UUID
    cost_center_id: UUID | None
    person_id: UUID | None
    amount_min_minor: int | None
    amount_max_minor: int | None
    day_of_month_min: int | None
    day_of_month_max: int | None
    is_active: bool


def _coerce_mode(mode: RuleMatchMode | str) -> RuleMatchMode:
    if isinstance(mode, RuleMatchMode):
        return mode
    try:
        return RuleMatchMode(mode)
    except ValueError as exc:
        raise RuleValidationError(f"unknown rule mode: {mode}") from exc


def _coerce_type(transaction_type: TransactionType | str) -> TransactionType:
    if isinstance(transaction_type, TransactionType):
        return transaction_type
    try:
        return TransactionType(transaction_type)
    except ValueError as exc:
        raise RuleValidationError(
            f"unknown transaction type: {transaction_type}"
        ) from exc


def _required_category_kind(transaction_type: TransactionType) -> CategoryKind:
    if transaction_type is TransactionType.INTERNAL_TRANSFER:
        raise RuleValidationError(
            "rules cannot assign the internal_transfer type; pairing owns it"
        )
    if transaction_type is TransactionType.INCOME:
        return CategoryKind.INCOME
    return CategoryKind.EXPENSE


def _validate_rule_input(
    session: Session,
    spec: RuleSpec,
) -> tuple[RuleMatchMode, TransactionType, str]:
    mode_enum = _coerce_mode(spec.mode)
    type_enum = _coerce_type(spec.transaction_type)
    required_kind = _required_category_kind(type_enum)
    stripped_pattern = spec.pattern.strip()
    validate_condition(
        RuleCondition(
            mode=mode_enum,
            pattern=stripped_pattern,
            amount_min_minor=spec.amount_min_minor,
            amount_max_minor=spec.amount_max_minor,
            day_of_month_min=spec.day_of_month_min,
            day_of_month_max=spec.day_of_month_max,
        )
    )

    category = session.get(Category, spec.category_id)
    if category is None:
        raise RuleValidationError("the rule category does not exist")
    if not category.is_active:
        raise RuleValidationError("the rule category is inactive")
    if category.kind != required_kind.value:
        raise RuleValidationError(
            f"category kind {category.kind} is incompatible with type {type_enum.value}"
        )

    if spec.cost_center_id is not None:
        cost_center = session.get(CostCenter, spec.cost_center_id)
        if cost_center is None:
            raise RuleValidationError("the rule cost center does not exist")
        if not cost_center.is_active:
            raise RuleValidationError("the rule cost center is inactive")

    if spec.person_id is not None:
        person = session.get(Person, spec.person_id)
        if person is None:
            raise RuleValidationError("the rule person does not exist")

    return mode_enum, type_enum, stripped_pattern


def create_rule(session: Session, spec: RuleSpec) -> Rule:
    """Create a user rule from ``spec`` and reclassify pending transactions.

    The rule is always a user rule; seed rules are installed by the seeding
    function. Validation runs before anything is written.
    """
    mode_enum, type_enum, stripped_pattern = _validate_rule_input(session, spec)
    rule = Rule(
        priority=spec.priority,
        mode=mode_enum.value,
        pattern=stripped_pattern,
        amount_min_minor=spec.amount_min_minor,
        amount_max_minor=spec.amount_max_minor,
        day_of_month_min=spec.day_of_month_min,
        day_of_month_max=spec.day_of_month_max,
        transaction_type=type_enum.value,
        category_id=spec.category_id,
        cost_center_id=spec.cost_center_id,
        person_id=spec.person_id,
        is_active=spec.is_active,
        origin=RuleOrigin.USER.value,
    )
    session.add(rule)
    session.commit()
    reclassify_pending(session)
    return rule


def update_rule(session: Session, rule_id: UUID, spec: RuleSpec) -> Rule:
    """Update a rule from ``spec`` and reclassify only pending transactions.

    Validated transactions never change as a side effect of a rule change.
    """
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise RuleNotFoundError("rule not found")

    mode_enum, type_enum, stripped_pattern = _validate_rule_input(session, spec)
    rule.priority = spec.priority
    rule.mode = mode_enum.value
    rule.pattern = stripped_pattern
    rule.amount_min_minor = spec.amount_min_minor
    rule.amount_max_minor = spec.amount_max_minor
    rule.day_of_month_min = spec.day_of_month_min
    rule.day_of_month_max = spec.day_of_month_max
    rule.transaction_type = type_enum.value
    rule.category_id = spec.category_id
    rule.cost_center_id = spec.cost_center_id
    rule.person_id = spec.person_id
    rule.is_active = spec.is_active
    session.commit()
    reclassify_pending(session)
    return rule


def delete_rule(session: Session, rule_id: UUID) -> None:
    """Delete a rule and reclassify only pending transactions.

    Validated transactions keep their classification; their rule reference is
    cleared because the rule no longer exists.
    """
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise RuleNotFoundError("rule not found")

    session.execute(
        update(Transaction).where(Transaction.rule_id == rule_id).values(rule_id=None)
    )
    session.flush()
    session.delete(rule)
    session.commit()
    reclassify_pending(session)


def list_active_rules(session: Session) -> list[Rule]:
    """Return active rules in precedence order."""
    return sorted_active_rules(session)


def find_rule_collisions(
    session: Session,
    candidate: RuleCondition,
    *,
    exclude_rule_id: UUID | None = None,
) -> list[RuleCollision]:
    """Report active rules whose condition overlaps ``candidate``."""
    rules = list(
        session.execute(select(Rule).where(Rule.is_active.is_(True))).scalars()
    )
    existing = [
        (rule.id, rule_to_condition(rule))
        for rule in rules
        if rule.id != exclude_rule_id
    ]
    return find_collisions(candidate, existing)


def propose_rule_from_correction(
    session: Session,
    transaction_id: UUID,
    *,
    transaction_type: TransactionType | str,
    category_id: UUID,
    cost_center_id: UUID | None,
) -> RuleProposal:
    """Propose a ``contains`` rule from a manual correction.

    The proposed pattern is the transaction's counterparty (or normalized
    description when it has none) and is meant to be edited by the rule editor.
    The proposal is placed one priority step above the rule that classified the
    transaction, without modifying that rule; when the transaction was not
    classified by a rule, it is placed above every existing rule.
    """
    txn = session.get(Transaction, transaction_id)
    if txn is None:
        raise RuleNotFoundError("transaction not found")

    type_enum = _coerce_type(transaction_type)
    pattern = (txn.counterparty or txn.normalized_description).strip()

    if txn.rule_id is not None and txn.origin == ClassificationOrigin.RULE.value:
        classifying_rule = session.get(Rule, txn.rule_id)
        if classifying_rule is not None:
            priority = classifying_rule.priority - 1
        else:
            priority = _top_priority(session)
    else:
        priority = _top_priority(session)

    return RuleProposal(
        mode=RuleMatchMode.CONTAINS,
        pattern=pattern,
        transaction_type=type_enum,
        category_id=category_id,
        cost_center_id=cost_center_id,
        priority=priority,
    )


def _top_priority(session: Session) -> int:
    minimum = session.execute(select(func.min(Rule.priority))).scalar()
    if minimum is None:
        return 0
    return minimum - 1


def _correction_spec(
    session: Session,
    transaction_id: UUID,
    *,
    transaction_type: TransactionType | str,
    category_id: UUID,
    cost_center_id: UUID | None,
    person_id: UUID | None,
    pattern: str | None,
) -> RuleSpec:
    proposal = propose_rule_from_correction(
        session,
        transaction_id,
        transaction_type=transaction_type,
        category_id=category_id,
        cost_center_id=cost_center_id,
    )
    stripped_pattern = (pattern or "").strip()
    return RuleSpec(
        priority=proposal.priority,
        mode=proposal.mode,
        pattern=stripped_pattern or proposal.pattern,
        transaction_type=proposal.transaction_type,
        category_id=proposal.category_id,
        cost_center_id=proposal.cost_center_id,
        person_id=person_id,
        amount_min_minor=None,
        amount_max_minor=None,
        day_of_month_min=None,
        day_of_month_max=None,
        is_active=True,
    )


def create_rule_from_correction(
    session: Session,
    transaction_id: UUID,
    *,
    transaction_type: TransactionType | str,
    category_id: UUID,
    cost_center_id: UUID | None,
    person_id: UUID | None,
    pattern: str | None = None,
) -> Rule:
    """Create a user rule from a corrected transaction.

    The proposed pattern is the transaction's counterparty (or its normalized
    description when it has none) and can be overridden with ``pattern``. Call
    this before validating the correction so the proposal can be placed one
    step above the rule that originally classified the transaction; after
    validation the transaction has no rule link, so the rule is placed above
    every existing rule instead. The corrected transaction itself matches the
    new rule, so it is validated by it; ``validate_and_create_rule`` is the
    variant that keeps the correction manual.
    """
    spec = _correction_spec(
        session,
        transaction_id,
        transaction_type=transaction_type,
        category_id=category_id,
        cost_center_id=cost_center_id,
        person_id=person_id,
        pattern=pattern,
    )
    return create_rule(session, spec)


@dataclass(frozen=True)
class CorrectionRuleResult:
    """Outcome of validating a correction and creating a rule from it."""

    rule: Rule
    validated_count: int


def validate_and_create_rule(
    session: Session,
    transaction_id: UUID,
    *,
    transaction_type: TransactionType | str,
    category_id: UUID,
    cost_center_id: UUID | None,
    person_id: UUID | None,
    pattern: str | None = None,
) -> CorrectionRuleResult:
    """Validate a correction by hand, then create a rule from it.

    The corrected transaction becomes a manual validated classification; the
    new user rule then validates every other pending transaction it matches.
    ``validated_count`` is the number of those other transactions. The rule
    input is checked first, so nothing is written when it is invalid.
    """
    spec = _correction_spec(
        session,
        transaction_id,
        transaction_type=transaction_type,
        category_id=category_id,
        cost_center_id=cost_center_id,
        person_id=person_id,
        pattern=pattern,
    )
    _validate_rule_input(session, spec)
    validate_transaction(
        session,
        transaction_id,
        transaction_type=transaction_type,
        category_id=category_id,
        cost_center_id=cost_center_id,
        person_id=person_id,
    )
    rule = create_rule(session, spec)
    return CorrectionRuleResult(
        rule=rule, validated_count=count_rule_validated(session, rule.id)
    )


def count_rule_validated(session: Session, rule_id: UUID) -> int:
    """Return how many transactions the given rule has validated."""
    return int(
        session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.rule_id == rule_id,
                Transaction.status == TransactionStatus.VALIDATED.value,
                Transaction.validated_by_rule.is_(True),
            )
        ).scalar_one()
    )
