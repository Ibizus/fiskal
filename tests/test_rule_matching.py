"""Tests for the framework-free rule matching, validation and collision layer."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from fiskal.domain.enums import RuleMatchMode
from fiskal.domain.rule_matching import (
    RuleCondition,
    RuleValidationError,
    condition_matches,
    conditions_overlap,
    find_collisions,
    validate_condition,
    validate_pattern,
)

_DESCRIPTION = "grocery store purchase"
_COUNTERPARTY = "Fresh Market"


def _condition(**overrides: object) -> RuleCondition:
    values: dict[str, object] = {
        "mode": RuleMatchMode.CONTAINS,
        "pattern": "grocery",
    }
    values.update(overrides)
    return RuleCondition(**values)  # type: ignore[arg-type]


def test_contains_matches_normalized_description_case_insensitively() -> None:
    """Contains is case-insensitive and matches the normalized description."""
    condition = _condition(pattern="GROCERY")
    assert condition_matches(
        condition,
        normalized_description=_DESCRIPTION,
        counterparty=None,
        amount_minor=0,
        operation_date=date(2024, 1, 15),
    )


def test_contains_matches_counterparty_case_insensitively() -> None:
    """Contains also matches the counterparty, case-insensitively."""
    condition = _condition(pattern="fresh market")
    assert condition_matches(
        condition,
        normalized_description="purchase",
        counterparty=_COUNTERPARTY,
        amount_minor=0,
        operation_date=date(2024, 1, 15),
    )


def test_contains_does_not_match_missing_text() -> None:
    """A pattern absent from both normalized fields does not match."""
    condition = _condition(pattern="rent")
    assert not condition_matches(
        condition,
        normalized_description=_DESCRIPTION,
        counterparty=_COUNTERPARTY,
        amount_minor=0,
        operation_date=date(2024, 1, 15),
    )


def test_contains_matches_mid_description_not_only_its_end() -> None:
    """Contains never relies on the end of a description."""
    condition = _condition(pattern="store")
    assert condition_matches(
        condition,
        normalized_description="grocery store purchase",
        counterparty=None,
        amount_minor=0,
        operation_date=date(2024, 1, 15),
    )


def test_amount_range_is_inclusive_and_optional() -> None:
    """Amount range narrows the match; bounds are inclusive."""
    bounded = _condition(amount_min_minor=-5000, amount_max_minor=-100)
    assert condition_matches(
        bounded,
        normalized_description=_DESCRIPTION,
        counterparty=None,
        amount_minor=-5000,
        operation_date=date(2024, 1, 15),
    )
    assert condition_matches(
        bounded,
        normalized_description=_DESCRIPTION,
        counterparty=None,
        amount_minor=-100,
        operation_date=date(2024, 1, 15),
    )
    assert not condition_matches(
        bounded,
        normalized_description=_DESCRIPTION,
        counterparty=None,
        amount_minor=-99,
        operation_date=date(2024, 1, 15),
    )


def test_day_of_month_range_narrows_match() -> None:
    """Day-of-month range applies to the operation date's day."""
    condition = _condition(day_of_month_min=10, day_of_month_max=20)
    assert condition_matches(
        condition,
        normalized_description=_DESCRIPTION,
        counterparty=None,
        amount_minor=0,
        operation_date=date(2024, 1, 15),
    )
    assert not condition_matches(
        condition,
        normalized_description=_DESCRIPTION,
        counterparty=None,
        amount_minor=0,
        operation_date=date(2024, 1, 21),
    )


def test_regex_mode_matches_normalized_fields() -> None:
    """Regex mode searches normalized fields case-insensitively."""
    condition = _condition(mode=RuleMatchMode.REGEX, pattern=r"grocer(y|ies)")
    assert condition_matches(
        condition,
        normalized_description="grocery store",
        counterparty=None,
        amount_minor=0,
        operation_date=date(2024, 1, 15),
    )


def test_validate_pattern_rejects_empty() -> None:
    """An empty or whitespace-only pattern is rejected."""
    with pytest.raises(RuleValidationError, match="not be empty"):
        validate_pattern(RuleMatchMode.CONTAINS, "   ")


def test_validate_pattern_rejects_too_long() -> None:
    """Patterns longer than the stored column are rejected."""
    with pytest.raises(RuleValidationError, match="at most"):
        validate_pattern(RuleMatchMode.CONTAINS, "x" * 501)


def test_validate_pattern_rejects_invalid_regex() -> None:
    """A regex that does not compile is rejected."""
    with pytest.raises(RuleValidationError, match="invalid regular expression"):
        validate_pattern(RuleMatchMode.REGEX, "(")


def test_validate_pattern_rejects_nested_quantifier() -> None:
    """A regex with a pathological nested quantifier is rejected."""
    with pytest.raises(RuleValidationError, match="nested quantifier"):
        validate_pattern(RuleMatchMode.REGEX, "(a+)+")


def test_validate_pattern_accepts_safe_regex() -> None:
    """A well-formed, non-pathological regex is accepted."""
    validate_pattern(RuleMatchMode.REGEX, r"(salary|payroll)")


def test_validate_condition_rejects_amount_range_inversion() -> None:
    """An amount range whose minimum exceeds its maximum is rejected."""
    with pytest.raises(RuleValidationError, match="amount range"):
        validate_condition(_condition(amount_min_minor=100, amount_max_minor=0))


def test_validate_condition_rejects_day_of_month_out_of_bounds() -> None:
    """A day-of-month bound outside 1..31 is rejected."""
    with pytest.raises(RuleValidationError, match="day-of-month"):
        validate_condition(_condition(day_of_month_min=0))


def test_validate_condition_rejects_day_range_inversion() -> None:
    """A day-of-month range whose minimum exceeds its maximum is rejected."""
    with pytest.raises(RuleValidationError, match="day-of-month"):
        validate_condition(_condition(day_of_month_min=20, day_of_month_max=10))


def test_contains_overlap_is_substring_relation() -> None:
    """Two contains conditions overlap when one pattern is inside the other."""
    assert conditions_overlap(
        _condition(pattern="grocery"), _condition(pattern="grocery store")
    )
    assert not conditions_overlap(
        _condition(pattern="grocery"), _condition(pattern="rent")
    )


def test_ranges_block_collision_even_when_text_overlaps() -> None:
    """Disjoint amount or day ranges mean the rules cannot both match."""
    assert not conditions_overlap(
        _condition(pattern="grocery", amount_max_minor=-100),
        _condition(pattern="grocery", amount_min_minor=100),
    )
    assert not conditions_overlap(
        _condition(pattern="grocery", day_of_month_max=10),
        _condition(pattern="grocery", day_of_month_min=20),
    )


def test_regex_condition_overlaps_conservatively() -> None:
    """Regex intersections are undecidable, so they are reported conservatively."""
    assert conditions_overlap(
        _condition(mode=RuleMatchMode.REGEX, pattern=r"foo+"),
        _condition(pattern="rent"),
    )


def test_find_collisions_reports_overlapping_rules() -> None:
    """Collisions report the overlapping rule id, not the disjoint one."""
    other_id = uuid4()
    disjoint_id = uuid4()
    existing = [
        (other_id, _condition(pattern="grocery store")),
        (disjoint_id, _condition(pattern="rent")),
    ]
    collisions = find_collisions(_condition(pattern="grocery"), existing)
    assert len(collisions) == 1
    assert collisions[0].other_rule_id == other_id
    assert "matching text" in collisions[0].reason


def test_find_collisions_with_no_overlap_is_empty() -> None:
    """Disjoint conditions produce no collision report."""
    collisions = find_collisions(
        _condition(pattern="rent"),
        [(uuid4(), _condition(pattern="grocery"))],
    )
    assert collisions == []
