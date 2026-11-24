"""Framework-free internal-transfer pairing logic.

The pairing step proposes the matching counterpart transaction in another
account of the profile after an import. Both sides must be confirmed by the
user before they are marked as ``internal_transfer``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID


@dataclass(frozen=True)
class TransferCandidate:
    """A transaction considered for internal-transfer pairing.

    Only the fields needed for matching are exposed here; persistence details
    stay outside the domain layer.
    """

    id: UUID
    account_id: UUID
    value_date: date
    amount_minor: int
    currency_code: str


@dataclass(frozen=True)
class PairProposal:
    """A ranked proposal to pair two transactions as an internal transfer."""

    source_id: UUID
    candidate_id: UUID
    day_delta: int
    rank_key: tuple[int, str]


class PairingError(Exception):
    """Raised when a pairing action violates a business rule."""


DEFAULT_WINDOW_DAYS: int = 3


class TransferPairing:
    """Pure logic for proposing internal-transfer counterparts.

    The ranking is deterministic: candidates with the smallest value-date
    distance come first, and ties are broken by the candidate's id so the
    output is stable across runs.
    """

    window_days: int

    def __init__(self, window_days: int = DEFAULT_WINDOW_DAYS) -> None:
        """Create a pairing engine with the given value-date window."""
        self.window_days = window_days

    def propose(
        self,
        sources: list[TransferCandidate],
        pool: list[TransferCandidate],
    ) -> list[PairProposal]:
        """Return all plausible pair proposals for ``sources``.

        ``pool`` contains every transaction that may serve as a counterpart,
        including the sources themselves. Already paired candidates and
        rejected pairs are expected to be removed from ``pool`` by the caller.

        When both sides of a possible pair are in ``sources``, the proposal is
        emitted only once, ordered by transaction id string.
        """
        source_ids = {candidate.id for candidate in sources}
        proposals: list[PairProposal] = []

        for source in sources:
            for candidate in pool:
                if candidate.id == source.id:
                    continue
                if candidate.account_id == source.account_id:
                    continue
                if candidate.currency_code != source.currency_code:
                    continue
                if candidate.amount_minor != -source.amount_minor:
                    continue
                if candidate.id in source_ids and str(source.id) >= str(candidate.id):
                    continue
                day_delta = abs((source.value_date - candidate.value_date).days)
                if day_delta > self.window_days:
                    continue
                proposals.append(
                    PairProposal(
                        source_id=source.id,
                        candidate_id=candidate.id,
                        day_delta=day_delta,
                        rank_key=(day_delta, str(candidate.id)),
                    )
                )

        proposals.sort(
            key=lambda proposal: (proposal.rank_key, str(proposal.source_id))
        )
        return proposals
