"""Cross-cutting read services for the Streamlit pages.

These are small lookup queries shared by the Import, Validate and Transactions
pages. All business rules live in the classification, importers and reports
packages; these functions only fetch rows for rendering.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from fiskal.db.models import Account, Category, CostCenter, Import, Person


def list_accounts(session: Session, *, active_only: bool = True) -> list[Account]:
    """Return accounts ordered by name."""
    statement = select(Account)
    if active_only:
        statement = statement.where(Account.is_active.is_(True))
    return list(session.execute(statement.order_by(Account.name)).scalars())


def list_categories(session: Session, *, active_only: bool = True) -> list[Category]:
    """Return categories ordered by translation key."""
    statement = select(Category)
    if active_only:
        statement = statement.where(Category.is_active.is_(True))
    return list(
        session.execute(
            statement.order_by(Category.name_key, Category.custom_name)
        ).scalars()
    )


def list_cost_centers(
    session: Session, *, active_only: bool = True
) -> list[CostCenter]:
    """Return cost centers ordered by translation key."""
    statement = select(CostCenter)
    if active_only:
        statement = statement.where(CostCenter.is_active.is_(True))
    return list(
        session.execute(
            statement.order_by(CostCenter.name_key, CostCenter.custom_name)
        ).scalars()
    )


def list_persons(session: Session) -> list[Person]:
    """Return holders ordered by name."""
    return list(session.execute(select(Person).order_by(Person.name)).scalars())


def list_imports(
    session: Session,
    *,
    account_id: UUID | None = None,
    limit: int = 20,
) -> list[Import]:
    """Return the most recent imports, newest first."""
    statement = select(Import)
    if account_id is not None:
        statement = statement.where(Import.account_id == account_id)
    return list(
        session.execute(
            statement.order_by(Import.imported_at.desc()).limit(limit)
        ).scalars()
    )
