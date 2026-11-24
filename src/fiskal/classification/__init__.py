"""Transaction classification rules.

Modules in this package assign categories and cost centers to transactions
based on configurable, non-personal rules. Real personal patterns belong in
a local profile directory outside this repository.
"""

from fiskal.classification.pairing_service import (
    confirm_pair,
    find_proposals_for_transaction_ids,
    reject_pair,
    unpair,
)

__all__ = [
    "confirm_pair",
    "find_proposals_for_transaction_ids",
    "reject_pair",
    "unpair",
]
