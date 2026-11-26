"""Income report helpers.

Income is not everything that enters an account. Earned income is work plus
benefits plus rental; gifts and one-off inflows are ``other`` and count in the
cash balance but not in earned income. Refunds, savings and internal transfers
are never income.

All amounts are computed from integer minor units.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from fiskal.db.models import Category, Person, Transaction
from fiskal.domain.enums import IncomeSource, TransactionType


@dataclass(frozen=True)
class IncomeBreakdown:
    """Income split into labor, rental and other inflows, in minor units."""

    labor_minor: int = 0
    rental_minor: int = 0
    other_minor: int = 0

    @property
    def earned_minor(self) -> int:
        """Earned income: work plus benefits plus rental."""
        return self.labor_minor + self.rental_minor

    @property
    def total_minor(self) -> int:
        """Everything counted as income: earned plus other inflows."""
        return self.earned_minor + self.other_minor


@dataclass(frozen=True)
class HolderIncome:
    """One holder's attributed income."""

    person_id: UUID
    person_name: str
    breakdown: IncomeBreakdown


_LABOR_SOURCES = frozenset({IncomeSource.WORK.value, IncomeSource.BENEFIT.value})


def _bucket(income_source: str | None) -> str:
    """Map a category income source to one of the three report buckets."""
    if income_source in _LABOR_SOURCES:
        return "labor"
    if income_source == IncomeSource.RENTAL.value:
        return "rental"
    return "other"


def _add(
    breakdown: IncomeBreakdown, income_source: str | None, amount: int
) -> IncomeBreakdown:
    """Return ``breakdown`` with ``amount`` added to the matching bucket."""
    bucket = _bucket(income_source)
    if bucket == "labor":
        return replace(breakdown, labor_minor=breakdown.labor_minor + amount)
    if bucket == "rental":
        return replace(breakdown, rental_minor=breakdown.rental_minor + amount)
    return replace(breakdown, other_minor=breakdown.other_minor + amount)


def _income_rows(session: Session) -> list[tuple[UUID | None, str | None, int]]:
    """Return (person, income source, amount) for every income transaction."""
    rows = session.execute(
        select(
            Transaction.person_id,
            Category.income_source,
            Transaction.amount_minor,
        )
        .join(Category, Transaction.category_id == Category.id)
        .where(Transaction.transaction_type == TransactionType.INCOME.value)
    ).all()
    return [(person_id, source, amount) for person_id, source, amount in rows]


def income_by_holder(session: Session) -> list[HolderIncome]:
    """Return attributed income grouped by holder, sorted by name.

    Only income attributed to a person is returned here; unattributed income is
    reported separately by :func:`unattributed_income`.
    """
    persons = {
        person.id: person.name for person in session.execute(select(Person)).scalars()
    }
    holders: dict[UUID, IncomeBreakdown] = {}
    for person_id, source, amount in _income_rows(session):
        if person_id is None:
            continue
        holders[person_id] = _add(
            holders.get(person_id, IncomeBreakdown()), source, amount
        )
    result = [
        HolderIncome(
            person_id=person_id,
            person_name=persons.get(person_id, ""),
            breakdown=breakdown,
        )
        for person_id, breakdown in holders.items()
    ]
    result.sort(key=lambda item: (item.person_name, str(item.person_id)))
    return result


def unattributed_income(session: Session) -> IncomeBreakdown:
    """Return income not attributed to any holder, in minor units.

    Unattributed income is not personal (for example shared rental income).
    """
    breakdown = IncomeBreakdown()
    for person_id, source, amount in _income_rows(session):
        if person_id is None:
            breakdown = _add(breakdown, source, amount)
    return breakdown


def total_income(session: Session) -> IncomeBreakdown:
    """Return income across the whole profile, in minor units."""
    breakdown = IncomeBreakdown()
    for _, source, amount in _income_rows(session):
        breakdown = _add(breakdown, source, amount)
    return breakdown


def labor_income_minor(session: Session) -> int:
    """Return labor income (work plus benefits) in minor units."""
    return total_income(session).labor_minor


def rental_income_minor(session: Session) -> int:
    """Return rental income in minor units."""
    return total_income(session).rental_minor


def earned_income_minor(session: Session) -> int:
    """Return total earned income (labor plus rental) in minor units."""
    return total_income(session).earned_minor


def other_inflows_minor(session: Session) -> int:
    """Return other inflows (gifts and one-offs) in minor units."""
    return total_income(session).other_minor
