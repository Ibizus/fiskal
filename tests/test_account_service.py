"""Tests for the minimal account and holder creation services."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from fiskal.db.models import AccountHolder
from fiskal.services import (
    AccountCreationError,
    create_account,
    create_person,
    list_accounts,
    list_persons,
)


def test_create_account_with_holders(session: Session) -> None:
    """An account is stored with its normalized fields and holder links."""
    ada = create_person(session, "  Ada ")
    bob = create_person(session, "Bob")

    account = create_account(
        session,
        name=" Checking ",
        currency_code="eur",
        account_hint="0042",
        holder_ids=[ada.id, bob.id, ada.id],
    )

    assert [item.id for item in list_accounts(session)] == [account.id]
    assert account.name == "Checking"
    assert account.currency_code == "EUR"
    assert account.account_hint == "0042"
    assert account.is_active is True
    links = session.execute(
        select(AccountHolder.person_id).where(AccountHolder.account_id == account.id)
    ).scalars()
    assert set(links) == {ada.id, bob.id}
    assert [person.name for person in list_persons(session)] == ["Ada", "Bob"]


def test_create_account_without_hint_or_holders(session: Session) -> None:
    """Hint and holders are optional."""
    account = create_account(
        session, name="Cash", currency_code="EUR", account_hint="  "
    )

    assert account.account_hint is None
    assert list(session.execute(select(AccountHolder)).scalars()) == []


@pytest.mark.parametrize(
    ("name", "currency_code", "account_hint"),
    [
        ("", "EUR", None),
        ("Checking", "EU", None),
        ("Checking", "E1R", None),
        ("Checking", "EUR", "123"),
        ("Checking", "EUR", "12a4"),
        ("Checking", "EUR", "12345"),
    ],
)
def test_create_account_rejects_invalid_input(
    session: Session, name: str, currency_code: str, account_hint: str | None
) -> None:
    """Invalid input raises and stores nothing."""
    with pytest.raises(AccountCreationError):
        create_account(
            session,
            name=name,
            currency_code=currency_code,
            account_hint=account_hint,
        )

    assert list_accounts(session) == []


def test_create_account_rejects_unknown_holder(session: Session) -> None:
    """A holder id that does not exist is rejected before anything is stored."""
    with pytest.raises(AccountCreationError):
        create_account(
            session, name="Checking", currency_code="EUR", holder_ids=[uuid4()]
        )

    assert list_accounts(session) == []


def test_create_person_requires_name(session: Session) -> None:
    """A blank holder name is rejected."""
    with pytest.raises(AccountCreationError):
        create_person(session, "   ")

    assert list_persons(session) == []
