"""Tests for the framework-free internal-transfer pairing logic."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

from fiskal.domain.transfer_pairing import (
    DEFAULT_WINDOW_DAYS,
    PairProposal,
    TransferCandidate,
    TransferPairing,
)


def _candidate(
    *,
    account: int,
    value_date: date,
    amount_minor: int,
    currency_code: str = "EUR",
    is_paired: bool = False,
) -> TransferCandidate:
    """Build a candidate with deterministic ids derived from a counter."""
    return TransferCandidate(
        id=UUID(int=account),
        account_id=UUID(int=account),
        value_date=value_date,
        amount_minor=amount_minor,
        currency_code=currency_code,
        is_paired=is_paired,
    )


def _candidate_uuid(
    *,
    account_id: UUID,
    value_date: date,
    amount_minor: int,
    currency_code: str = "EUR",
    is_paired: bool = False,
) -> TransferCandidate:
    return TransferCandidate(
        id=uuid4(),
        account_id=account_id,
        value_date=value_date,
        amount_minor=amount_minor,
        currency_code=currency_code,
        is_paired=is_paired,
    )


def test_simple_pair() -> None:
    """Two transactions in different accounts with opposite amounts pair."""
    day = date(2024, 3, 15)
    source = _candidate(account=1, value_date=day, amount_minor=-10_000)
    counterpart = _candidate(account=2, value_date=day, amount_minor=10_000)

    proposals = TransferPairing().propose([source], [counterpart])

    assert proposals == [
        PairProposal(
            source_id=source.id,
            candidate_id=counterpart.id,
            day_delta=0,
            rank_key=(0, str(counterpart.id)),
        )
    ]


def test_no_counterpart() -> None:
    """When no transaction matches, no proposal is returned."""
    day = date(2024, 3, 15)
    source = _candidate(account=1, value_date=day, amount_minor=-10_000)
    unrelated = _candidate(account=2, value_date=day, amount_minor=5_000)

    assert TransferPairing().propose([source], [unrelated]) == []


def test_same_account_never_paired() -> None:
    """A transaction never pairs with another movement of the same account."""
    day = date(2024, 3, 15)
    source = _candidate(account=1, value_date=day, amount_minor=-10_000)
    same_account = _candidate(account=1, value_date=day, amount_minor=10_000)

    assert TransferPairing().propose([source], [same_account]) == []


def test_already_paired_excluded() -> None:
    """Already paired transactions are not proposed as candidates."""
    day = date(2024, 3, 15)
    source = _candidate(account=1, value_date=day, amount_minor=-10_000)
    paired = _candidate(account=2, value_date=day, amount_minor=10_000, is_paired=True)

    assert TransferPairing().propose([source], [paired]) == []


def test_currency_mismatch() -> None:
    """Transactions with different currencies are not paired."""
    day = date(2024, 3, 15)
    source = _candidate(
        account=1, value_date=day, amount_minor=-10_000, currency_code="EUR"
    )
    other_currency = _candidate(
        account=2, value_date=day, amount_minor=10_000, currency_code="USD"
    )

    assert TransferPairing().propose([source], [other_currency]) == []


def test_amount_mismatch() -> None:
    """Only the exact opposite amount is accepted."""
    day = date(2024, 3, 15)
    source = _candidate(account=1, value_date=day, amount_minor=-10_000)
    close = _candidate(account=2, value_date=day, amount_minor=9_999)

    assert TransferPairing().propose([source], [close]) == []


def test_window_edges() -> None:
    """Candidates exactly at the window boundary are included; beyond it not."""
    source = _candidate(account=1, value_date=date(2024, 3, 15), amount_minor=-10_000)
    at_boundary = _candidate(
        account=2,
        value_date=date(2024, 3, 15) + timedelta(days=DEFAULT_WINDOW_DAYS),
        amount_minor=10_000,
    )
    beyond = _candidate(
        account=2,
        value_date=date(2024, 3, 15) + timedelta(days=DEFAULT_WINDOW_DAYS + 1),
        amount_minor=10_000,
    )

    proposals = TransferPairing().propose([source], [at_boundary, beyond])

    assert len(proposals) == 1
    assert proposals[0].candidate_id == at_boundary.id


def test_multiple_candidates_ranked_by_date_distance() -> None:
    """Several plausible candidates are all returned, closest first."""
    source = _candidate(account=1, value_date=date(2024, 3, 15), amount_minor=-10_000)
    same_day = _candidate(account=2, value_date=date(2024, 3, 15), amount_minor=10_000)
    one_day = _candidate(account=2, value_date=date(2024, 3, 16), amount_minor=10_000)
    two_day = _candidate(account=2, value_date=date(2024, 3, 17), amount_minor=10_000)

    proposals = TransferPairing().propose([source], [two_day, same_day, one_day])

    assert [proposal.candidate_id for proposal in proposals] == [
        same_day.id,
        one_day.id,
        two_day.id,
    ]


def test_same_day_multiple_candidates_are_sorted_by_id() -> None:
    """Ties are broken deterministically by candidate id."""
    account_b = uuid4()
    source = _candidate_uuid(
        account_id=uuid4(), value_date=date(2024, 3, 15), amount_minor=-10_000
    )
    first = _candidate_uuid(
        account_id=account_b, value_date=date(2024, 3, 15), amount_minor=10_000
    )
    second = _candidate_uuid(
        account_id=account_b, value_date=date(2024, 3, 15), amount_minor=10_000
    )

    proposals = TransferPairing().propose([source], [second, first])

    ids = [proposal.candidate_id for proposal in proposals]
    assert ids == sorted(ids, key=str)


def test_both_sides_as_sources_are_proposed_once() -> None:
    """When both sides are in the source set, only one proposal is emitted."""
    source_a = _candidate(account=1, value_date=date(2024, 3, 15), amount_minor=-10_000)
    source_b = _candidate(account=2, value_date=date(2024, 3, 15), amount_minor=10_000)

    proposals = TransferPairing().propose([source_a, source_b], [source_a, source_b])

    assert len(proposals) == 1
    # The proposal is emitted from the smaller-id source.
    assert proposals[0].source_id == min(source_a.id, source_b.id, key=str)
