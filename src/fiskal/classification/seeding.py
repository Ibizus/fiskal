"""Neutral seed loading.

Installs the repository's default categories, cost centers and seed rules into
a profile database idempotently. Seeding only inserts missing rows by their
natural key: it never overwrites user rules or user-customised names.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from fiskal.db.models import Category, CostCenter, Rule
from fiskal.domain.enums import RuleMatchMode, RuleOrigin
from fiskal.domain.rule_matching import validate_pattern
from fiskal.domain.seed import DEFAULT_CATEGORIES, DEFAULT_COST_CENTERS, DEFAULT_RULES


@dataclass(frozen=True)
class SeedResult:
    """How many rows a seeding run created."""

    categories_created: int
    cost_centers_created: int
    rules_created: int


def seed_neutral_data(session: Session) -> SeedResult:
    """Install default categories, cost centers and seed rules idempotently.

    Categories and cost centers are matched by translation key; seed rules are
    matched by their (seed, mode, pattern, type) signature. Existing rows are
    never modified, so user rules and user-customised names survive a re-run.
    """
    categories_created = _seed_categories(session)
    cost_centers_created = _seed_cost_centers(session)
    session.flush()
    rules_created = _seed_rules(session)
    session.commit()
    return SeedResult(
        categories_created=categories_created,
        cost_centers_created=cost_centers_created,
        rules_created=rules_created,
    )


def _seed_categories(session: Session) -> int:
    created = 0
    for seed_category in DEFAULT_CATEGORIES:
        existing = session.execute(
            select(Category).where(Category.name_key == seed_category.key)
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                Category(
                    name_key=seed_category.key,
                    kind=seed_category.kind,
                    nature=seed_category.nature,
                    is_active=True,
                )
            )
            created += 1
    return created


def _seed_cost_centers(session: Session) -> int:
    created = 0
    for seed_cost_center in DEFAULT_COST_CENTERS:
        existing = session.execute(
            select(CostCenter).where(CostCenter.name_key == seed_cost_center.key)
        ).scalar_one_or_none()
        if existing is None:
            session.add(CostCenter(name_key=seed_cost_center.key, is_active=True))
            created += 1
    return created


def _seed_rules(session: Session) -> int:
    categories = {
        category.name_key: category
        for category in session.execute(select(Category)).scalars()
    }
    cost_centers = {
        cost_center.name_key: cost_center
        for cost_center in session.execute(select(CostCenter)).scalars()
    }
    created = 0
    for seed_rule in DEFAULT_RULES:
        category = categories.get(seed_rule.category_key)
        if category is None:
            continue
        cost_center_id = (
            cost_centers[seed_rule.cost_center_key].id
            if seed_rule.cost_center_key is not None
            else None
        )
        existing = session.execute(
            select(Rule).where(
                Rule.origin == RuleOrigin.SEED.value,
                Rule.mode == seed_rule.mode,
                Rule.pattern == seed_rule.pattern,
                Rule.transaction_type == seed_rule.transaction_type,
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        validate_pattern(RuleMatchMode(seed_rule.mode), seed_rule.pattern)
        session.add(
            Rule(
                priority=seed_rule.priority,
                mode=seed_rule.mode,
                pattern=seed_rule.pattern,
                amount_min_minor=seed_rule.amount_min_minor,
                amount_max_minor=seed_rule.amount_max_minor,
                transaction_type=seed_rule.transaction_type,
                category_id=category.id,
                cost_center_id=cost_center_id,
                is_active=True,
                origin=RuleOrigin.SEED.value,
            )
        )
        created += 1
    return created
