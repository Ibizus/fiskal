"""Transaction classification.

This package owns the deterministic classification cascade (rules, history and
the account default cost center), rule CRUD and neutral seed loading. Real
personal patterns belong in a local profile directory outside this repository.
"""

from fiskal.classification.engine import (
    ClassificationResult,
    HistoryOutcome,
    HistorySuggestion,
    ReapplyChange,
    apply_reapply_validated,
    classify_pending,
    preview_reapply_validated,
    reclassify_pending,
    suggest_from_history,
)
from fiskal.classification.pairing_service import (
    confirm_pair,
    find_proposals_for_transaction_ids,
    reject_pair,
    unpair,
)
from fiskal.classification.rules_service import (
    RuleNotFoundError,
    RuleProposal,
    create_rule,
    delete_rule,
    find_rule_collisions,
    list_active_rules,
    propose_rule_from_correction,
    update_rule,
)
from fiskal.classification.seeding import SeedResult, seed_neutral_data

__all__ = [
    "ClassificationResult",
    "HistoryOutcome",
    "HistorySuggestion",
    "ReapplyChange",
    "RuleNotFoundError",
    "RuleProposal",
    "SeedResult",
    "apply_reapply_validated",
    "classify_pending",
    "confirm_pair",
    "create_rule",
    "delete_rule",
    "find_proposals_for_transaction_ids",
    "find_rule_collisions",
    "list_active_rules",
    "preview_reapply_validated",
    "propose_rule_from_correction",
    "reclassify_pending",
    "reject_pair",
    "seed_neutral_data",
    "suggest_from_history",
    "unpair",
    "update_rule",
]
