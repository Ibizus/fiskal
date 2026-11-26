"""Tests for the guided history mode: grouping pending movements by concept.

All data is synthetic. The volume tests build thousands of rows to check that
grouping and paging happen in the database.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import event
from sqlalchemy.orm import Session

from fiskal.classification import (
    WIDE_SPREAD_FACTOR,
    GroupKey,
    TransactionGroup,
    classification_progress,
    held_transaction_ids,
    is_wide_spread,
    list_pending_groups,
    summarize_groups,
)
from fiskal.classification.guided_groups import group_transaction_ids
from fiskal.db.models import Transaction
from fiskal.domain.enums import GroupDirection, TransactionStatus
from tests.support.factories import _account, _bulk_transactions, _transaction

EXPENSE = GroupDirection.EXPENSE
INCOME = GroupDirection.INCOME
THREE = 3
TOTAL_MINOR = -6000
PERCENT_ONE_THIRD = 33
PERCENT_NOT_YET_COMPLETE = 99
VOLUME_FLOOR = 5000
PAGE_SIZE = 10
CONCEPTS = 80
QUERIES_PER_REQUEST_SET = 3


def _groups(
    session: Session, *, limit: int = 100, offset: int = 0
) -> list[TransactionGroup]:
    return list_pending_groups(
        session, held_ids=held_transaction_ids(session), limit=limit, offset=offset
    )


def test_groups_split_income_from_expense_with_same_description(
    session: Session,
) -> None:
    """The same description on both sides of the books forms two groups."""
    account = _account(session)
    _bulk_transactions(session, account=account, description="shop", count=3)
    _bulk_transactions(
        session, account=account, description="shop", count=2, amount_minor=500
    )

    groups = _groups(session)

    assert [(g.key, g.row_count) for g in groups] == [
        (GroupKey("shop", EXPENSE), 3),
        (GroupKey("shop", INCOME), 2),
    ]


def test_single_row_descriptions_stay_out_of_groups(session: Session) -> None:
    """A description that appears once is left for the normal queue."""
    account = _account(session)
    _bulk_transactions(session, account=account, description="rent", count=2)
    _bulk_transactions(session, account=account, description="one-off", count=1)

    groups = _groups(session)

    assert [g.key.normalized_description for g in groups] == ["rent"]
    assert summarize_groups(session, set()).group_count == 1


def test_groups_are_ordered_from_most_rows_to_fewest(session: Session) -> None:
    """The biggest queue reduction comes first; ties are stable by description."""
    account = _account(session)
    _bulk_transactions(session, account=account, description="b", count=2)
    _bulk_transactions(session, account=account, description="a", count=2)
    _bulk_transactions(session, account=account, description="big", count=5)
    _bulk_transactions(session, account=account, description="mid", count=3)

    groups = _groups(session)

    assert [(g.key.normalized_description, g.row_count) for g in groups] == [
        ("big", 5),
        ("mid", 3),
        ("a", 2),
        ("b", 2),
    ]


def test_only_pending_unpaired_rows_are_grouped(session: Session) -> None:
    """Validated rows, paired rows and blank descriptions never form groups."""
    account = _account(session)
    other = _account(session, name="Other")
    _bulk_transactions(
        session,
        account=account,
        description="done",
        count=3,
        status=TransactionStatus.VALIDATED,
    )
    _bulk_transactions(session, account=account, description="", count=3)
    left = _transaction(session, account=account, description="move", amount_minor=-5)
    right = _transaction(session, account=other, description="move", amount_minor=5)
    left.paired_transaction_id = right.id
    right.paired_transaction_id = left.id
    session.commit()

    assert _groups(session) == []
    assert summarize_groups(session, set()).row_count == 0


def test_groups_exclude_internal_transfer_proposal_legs(session: Session) -> None:
    """Rows in an open pairing proposal are not part of any group."""
    account = _account(session)
    other = _account(session, name="Other")
    _bulk_transactions(
        session, account=account, description="topup", count=3, amount_minor=-5000
    )
    leg_out = _transaction(
        session, account=account, description="topup", amount_minor=-7777
    )
    leg_in = _transaction(
        session, account=other, description="deposit", amount_minor=7777
    )

    held = held_transaction_ids(session)

    assert held == {leg_out.id, leg_in.id}
    groups = _groups(session)
    assert [(g.key.normalized_description, g.row_count) for g in groups] == [
        ("topup", 3)
    ]
    ids = group_transaction_ids(session, GroupKey("topup", EXPENSE), held)
    assert leg_out.id not in ids
    assert len(ids) == THREE


def test_group_aggregates_dates_amounts_and_currency(session: Session) -> None:
    """A group reports its total, amount range, date range and currency."""
    account = _account(session)
    for day, amount in ((3, -1000), (9, -3000), (20, -2000)):
        _transaction(
            session, account=account, description="mixed", amount_minor=amount, day=day
        )

    (group,) = _groups(session)

    assert group.total_minor == TOTAL_MINOR
    assert (group.min_abs_minor, group.max_abs_minor) == (1000, 3000)
    assert group.first_date == date(2024, 1, 3)
    assert group.last_date == date(2024, 1, 20)
    assert group.currency_code == "EUR"


def test_group_with_mixed_currencies_reports_no_currency(session: Session) -> None:
    """Amounts in different currencies are not comparable, so none is reported."""
    account = _account(session)
    first = _transaction(session, account=account, description="fx", amount_minor=-1)
    _transaction(session, account=account, description="fx", amount_minor=-2)
    first.currency_code = "USD"
    session.commit()

    (group,) = _groups(session)

    assert group.currency_code is None


def test_wide_spread_threshold(session: Session) -> None:
    """A group is wide exactly when max >= factor * min (absolute amounts)."""
    assert WIDE_SPREAD_FACTOR == THREE
    assert not is_wide_spread(1000, 2999)
    assert is_wide_spread(1000, 3000)
    assert is_wide_spread(0, 1)
    assert not is_wide_spread(0, 0)

    account = _account(session)
    _transaction(session, account=account, description="steady", amount_minor=-1299)
    _transaction(session, account=account, description="steady", amount_minor=-1399)
    _transaction(session, account=account, description="wide", amount_minor=-800)
    _transaction(session, account=account, description="wide", amount_minor=-12000)
    by_name = {g.key.normalized_description: g for g in _groups(session)}

    assert not by_name["steady"].has_wide_spread
    assert by_name["wide"].has_wide_spread


def test_amounts_do_not_split_a_group(session: Session) -> None:
    """Wildly different amounts with one description stay in one group."""
    account = _account(session)
    for amount in (-100, -10_000, -1_000_000):
        _transaction(session, account=account, description="shop", amount_minor=amount)

    assert [g.row_count for g in _groups(session)] == [3]


def test_progress_counts_pending_and_validated_percent(session: Session) -> None:
    """Progress rounds down and ignores paired internal transfers."""
    account = _account(session)
    other = _account(session, name="Other")
    _bulk_transactions(session, account=account, description="a", count=2)
    _bulk_transactions(
        session,
        account=account,
        description="b",
        count=1,
        status=TransactionStatus.VALIDATED,
    )
    left = _transaction(session, account=account, amount_minor=-9)
    right = _transaction(session, account=other, amount_minor=9)
    left.paired_transaction_id = right.id
    right.paired_transaction_id = left.id
    session.commit()

    progress = classification_progress(session)

    assert (progress.pending, progress.validated) == (2, 1)
    assert progress.classified_percent == PERCENT_ONE_THIRD


def test_progress_is_zero_without_movements(session: Session) -> None:
    """An empty profile reports zero pending and zero percent."""
    progress = classification_progress(session)

    assert (progress.pending, progress.classified_percent) == (0, 0)


def test_progress_reaches_100_only_when_nothing_is_pending(session: Session) -> None:
    """Rounding down never shows 100 while a movement is pending."""
    account = _account(session)
    _bulk_transactions(session, account=account, description="a", count=1)
    _bulk_transactions(
        session,
        account=account,
        description="b",
        count=500,
        status=TransactionStatus.VALIDATED,
    )

    assert (
        classification_progress(session).classified_percent == PERCENT_NOT_YET_COMPLETE
    )


def test_thousands_of_rows_are_grouped_and_paged_in_the_database(
    session: Session,
) -> None:
    """Grouping 5000+ rows runs a bounded number of statements and pages cleanly."""
    account = _account(session)
    for concept in range(CONCEPTS):
        _bulk_transactions(
            session,
            account=account,
            description=f"concept {concept:02d}",
            count=100 - concept % 40,
        )
    _bulk_transactions(session, account=account, description="singleton", count=1)
    total_rows = session.query(Transaction).count()
    assert total_rows > VOLUME_FLOOR

    statements: list[str] = []
    engine = session.get_bind()
    event.listen(
        engine, "before_cursor_execute", lambda *args: statements.append(args[2])
    )
    held = held_transaction_ids(session)
    statements.clear()
    first_page = list_pending_groups(session, held_ids=held, limit=PAGE_SIZE)
    second_page = list_pending_groups(
        session, held_ids=held, limit=PAGE_SIZE, offset=PAGE_SIZE
    )
    summary = summarize_groups(session, held)

    assert len(statements) == QUERIES_PER_REQUEST_SET
    assert len(first_page) == len(second_page) == PAGE_SIZE
    counts = [g.row_count for g in first_page + second_page]
    assert counts == sorted(counts, reverse=True)
    assert summary.group_count == CONCEPTS
    assert summary.row_count == total_rows - 1
    assert {g.key for g in first_page}.isdisjoint({g.key for g in second_page})
