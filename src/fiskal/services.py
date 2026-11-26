"""Cross-cutting services for the Streamlit pages.

These are small lookup queries shared by the Import, Validate and Transactions
pages, plus the minimal account and holder creation the Import page needs.
Classification, import and report rules live in their own packages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fiskal.db.models import (
    Account,
    AccountHolder,
    Category,
    CostCenter,
    Import,
    Person,
    Transaction,
)
from fiskal.domain.enums import TransactionStatus

CURRENCY_CODE_LENGTH = 3
ACCOUNT_HINT_LENGTH = 4


class AccountCreationError(Exception):
    """Raised when an account or holder cannot be created."""


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


@dataclass(frozen=True)
class HomeOverview:
    """The few figures the Home page shows."""

    pending_count: int
    last_import_at: datetime | None


def home_overview(session: Session) -> HomeOverview:
    """Return the pending movement count and the date of the last import.

    Pending movements are the ones waiting in the validation queue, so paired
    internal transfers are not counted.
    """
    pending_count = session.execute(
        select(func.count())
        .select_from(Transaction)
        .where(
            Transaction.status == TransactionStatus.PENDING.value,
            Transaction.paired_transaction_id.is_(None),
        )
    ).scalar_one()
    last_import_at = session.execute(select(func.max(Import.imported_at))).scalar()
    return HomeOverview(pending_count=int(pending_count), last_import_at=last_import_at)


def create_person(session: Session, name: str) -> Person:
    """Create a holder with the given name."""
    stripped = name.strip()
    if not stripped:
        raise AccountCreationError("the holder name is required")
    person = Person(name=stripped)
    session.add(person)
    session.commit()
    return person


def create_account(
    session: Session,
    *,
    name: str,
    currency_code: str,
    account_hint: str | None = None,
    holder_ids: list[UUID] | None = None,
) -> Account:
    """Create an account, optionally linked to existing holders.

    ``account_hint`` is the last four digits of the account number, never the
    full number. ``currency_code`` is a three-letter ISO code.
    """
    stripped_name = name.strip()
    if not stripped_name:
        raise AccountCreationError("the account name is required")
    currency = currency_code.strip().upper()
    if (
        len(currency) != CURRENCY_CODE_LENGTH
        or not currency.isascii()
        or not currency.isalpha()
    ):
        raise AccountCreationError("the currency must be a three-letter code")
    hint = (account_hint or "").strip() or None
    if hint is not None and (
        len(hint) != ACCOUNT_HINT_LENGTH or not hint.isascii() or not hint.isdigit()
    ):
        raise AccountCreationError("the account hint must be exactly four digits")
    unique_holder_ids = list(dict.fromkeys(holder_ids or []))
    for holder_id in unique_holder_ids:
        if session.get(Person, holder_id) is None:
            raise AccountCreationError("the holder does not exist")

    account = Account(name=stripped_name, currency_code=currency, account_hint=hint)
    session.add(account)
    session.flush()
    for holder_id in unique_holder_ids:
        session.add(AccountHolder(account_id=account.id, person_id=holder_id))
    session.commit()
    return account
