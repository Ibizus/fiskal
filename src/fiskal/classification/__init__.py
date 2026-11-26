"""Transaction classification.

This package owns the deterministic classification cascade (rules, history and
the account default cost center), rule CRUD and neutral seed loading. Real
personal patterns belong in a local profile directory outside this repository.
"""

from fiskal.classification.catalog_service import (
    CatalogError,
    CategorySpec,
    create_category,
    create_cost_center,
    has_owner_cost_center,
    rename_cost_center,
    set_category_active,
    set_cost_center_active,
    update_category,
)
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
    CorrectionRuleResult,
    RuleNotFoundError,
    RuleProposal,
    RuleSpec,
    create_rule,
    create_rule_from_correction,
    delete_rule,
    find_rule_collisions,
    list_active_rules,
    list_rules,
    propose_rule_from_correction,
    set_rule_active,
    update_rule,
    validate_and_create_rule,
)
from fiskal.classification.seeding import SeedResult, seed_neutral_data
from fiskal.classification.validation_service import (
    ValidationError,
    list_pending_transactions,
    list_rule_validated_transactions,
    return_to_queue,
    validate_transaction,
)

__all__ = [
    "CatalogError",
    "CategorySpec",
    "ClassificationResult",
    "CorrectionRuleResult",
    "HistoryOutcome",
    "HistorySuggestion",
    "ReapplyChange",
    "RuleNotFoundError",
    "RuleProposal",
    "RuleSpec",
    "SeedResult",
    "ValidationError",
    "apply_reapply_validated",
    "classify_pending",
    "confirm_pair",
    "create_category",
    "create_cost_center",
    "create_rule",
    "create_rule_from_correction",
    "delete_rule",
    "find_proposals_for_transaction_ids",
    "find_rule_collisions",
    "has_owner_cost_center",
    "list_active_rules",
    "list_rules",
    "list_pending_transactions",
    "list_rule_validated_transactions",
    "preview_reapply_validated",
    "propose_rule_from_correction",
    "reclassify_pending",
    "rename_cost_center",
    "reject_pair",
    "return_to_queue",
    "seed_neutral_data",
    "set_category_active",
    "set_cost_center_active",
    "set_rule_active",
    "suggest_from_history",
    "unpair",
    "update_category",
    "update_rule",
    "validate_and_create_rule",
    "validate_transaction",
]
