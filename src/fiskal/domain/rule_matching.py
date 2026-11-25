"""Framework-free rule matching, validation and collision detection.

Rules match only normalized fields: the lowercased normalized description and
the extracted counterparty. They never match raw bank text, and a ``contains``
condition never relies on the end of a description. Amount and day-of-month
ranges narrow a match; regular expressions are an optional advanced mode that
is validated when a rule is saved.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from fiskal.domain.enums import RuleMatchMode

MAX_PATTERN_LENGTH: int = 500
DAY_OF_MONTH_MIN: int = 1
DAY_OF_MONTH_MAX: int = 31


class RuleValidationError(Exception):
    """Raised when a rule condition is invalid."""


@dataclass(frozen=True)
class RuleCondition:
    """The matching part of a rule, independent of persistence."""

    mode: RuleMatchMode
    pattern: str
    amount_min_minor: int | None = None
    amount_max_minor: int | None = None
    day_of_month_min: int | None = None
    day_of_month_max: int | None = None


@dataclass(frozen=True)
class RuleCollision:
    """A report that a rule condition overlaps an existing rule."""

    other_rule_id: UUID
    reason: str


def validate_pattern(mode: RuleMatchMode, pattern: str) -> None:
    """Validate a rule text pattern for the given mode.

    Contains patterns must be non-empty. Regex patterns are compiled and
    checked against pathological constructs such as nested quantifiers, which
    can cause catastrophic backtracking.
    """
    stripped = pattern.strip()
    if not stripped:
        raise RuleValidationError("a rule pattern must not be empty")
    if len(stripped) > MAX_PATTERN_LENGTH:
        raise RuleValidationError(
            f"a rule pattern must be at most {MAX_PATTERN_LENGTH} characters"
        )
    if mode is RuleMatchMode.REGEX:
        try:
            re.compile(stripped, re.IGNORECASE)
        except re.error as exc:
            raise RuleValidationError(f"invalid regular expression: {exc}") from exc
        if _has_nested_quantifier(stripped):
            raise RuleValidationError(
                "regular expression uses a nested quantifier that can cause "
                "catastrophic backtracking"
            )


def validate_condition(condition: RuleCondition) -> None:
    """Validate a full rule condition including its numeric ranges."""
    validate_pattern(condition.mode, condition.pattern)
    if (
        condition.amount_min_minor is not None
        and condition.amount_max_minor is not None
        and condition.amount_min_minor > condition.amount_max_minor
    ):
        raise RuleValidationError("amount range minimum must not exceed its maximum")
    _validate_day_of_month(condition.day_of_month_min, "minimum")
    _validate_day_of_month(condition.day_of_month_max, "maximum")
    if (
        condition.day_of_month_min is not None
        and condition.day_of_month_max is not None
        and condition.day_of_month_min > condition.day_of_month_max
    ):
        raise RuleValidationError(
            "day-of-month range minimum must not exceed its maximum"
        )


def _validate_day_of_month(value: int | None, label: str) -> None:
    if value is not None and not DAY_OF_MONTH_MIN <= value <= DAY_OF_MONTH_MAX:
        raise RuleValidationError(
            f"day-of-month {label} must be between "
            f"{DAY_OF_MONTH_MIN} and {DAY_OF_MONTH_MAX}"
        )


_QUANTIFIER_CHARS = frozenset("+*{")


def _skip_character_class(pattern: str, start: int) -> int:
    """Return the index just past the character class that begins at ``start``."""
    n = len(pattern)
    index = start + 1
    if index < n and pattern[index] == "^":
        index += 1
    if index < n and pattern[index] == "]":
        index += 1
    while index < n and pattern[index] != "]":
        if pattern[index] == "\\":
            index += 2
        else:
            index += 1
    return index + 1 if index < n else n


def _matching_close_paren(pattern: str, start: int) -> int | None:
    """Return the index of the ``)`` matching the ``(`` at ``start``."""
    n = len(pattern)
    depth = 0
    index = start
    while index < n:
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            index = _skip_character_class(pattern, index)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _body_has_quantifier(body: str) -> bool:
    """Return True when ``body`` contains a repetition operator anywhere."""
    index = 0
    n = len(body)
    while index < n:
        char = body[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            index = _skip_character_class(body, index)
            continue
        if char in _QUANTIFIER_CHARS:
            return True
        index += 1
    return False


def _has_nested_quantifier(pattern: str) -> bool:
    """Return True when a quantified group's body also contains a quantifier.

    A pattern such as ``(a+)+`` or ``((a|b)*)*`` can backtrack catastrophically.
    The guard is intentionally conservative and heuristic, not a full proof of
    safety.
    """
    index = 0
    n = len(pattern)
    while index < n:
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            index = _skip_character_class(pattern, index)
            continue
        if char == "(":
            close = _matching_close_paren(pattern, index)
            if close is None:
                return False
            if (
                close + 1 < n
                and pattern[close + 1] in _QUANTIFIER_CHARS
                and _body_has_quantifier(pattern[index + 1 : close])
            ):
                return True
            index = close + 1
            continue
        index += 1
    return False


def _compile_regex(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern.strip(), re.IGNORECASE)


def _text_matches(
    condition: RuleCondition, normalized_description: str, counterparty: str | None
) -> bool:
    """Return True when the rule's text condition matches a normalized field."""
    haystacks = [normalized_description]
    if counterparty:
        haystacks.append(counterparty)
    if condition.mode is RuleMatchMode.CONTAINS:
        needle = condition.pattern.strip().casefold()
        return any(needle in haystack.casefold() for haystack in haystacks)
    compiled = _compile_regex(condition.pattern)
    return any(compiled.search(haystack) is not None for haystack in haystacks)


def condition_matches(
    condition: RuleCondition,
    *,
    normalized_description: str,
    counterparty: str | None,
    amount_minor: int,
    operation_date: date,
) -> bool:
    """Return True when ``condition`` matches the supplied transaction fields."""
    text_ok = _text_matches(condition, normalized_description, counterparty)
    amount_ok = (
        condition.amount_min_minor is None or amount_minor >= condition.amount_min_minor
    ) and (
        condition.amount_max_minor is None or amount_minor <= condition.amount_max_minor
    )
    day = operation_date.day
    day_ok = (
        condition.day_of_month_min is None or day >= condition.day_of_month_min
    ) and (condition.day_of_month_max is None or day <= condition.day_of_month_max)
    return text_ok and amount_ok and day_ok


def _range_overlaps(
    a_min: int | None,
    a_max: int | None,
    b_min: int | None,
    b_max: int | None,
) -> bool:
    """Return True when two inclusive ranges (``None`` means unbounded) overlap."""
    lower = a_min
    if b_min is not None:
        lower = b_min if lower is None else max(lower, b_min)
    upper = a_max
    if b_max is not None:
        upper = b_max if upper is None else min(upper, b_max)
    if lower is None or upper is None:
        return True
    return lower <= upper


def _text_may_overlap(a: RuleCondition, b: RuleCondition) -> bool:
    """Return True when two text conditions can match the same field value."""
    if a.mode is RuleMatchMode.CONTAINS and b.mode is RuleMatchMode.CONTAINS:
        pattern_a = a.pattern.strip().casefold()
        pattern_b = b.pattern.strip().casefold()
        return pattern_a in pattern_b or pattern_b in pattern_a
    # Regex intersection is undecidable in general; report a possible overlap
    # so the rule editor never misses a real collision.
    return True


def conditions_overlap(a: RuleCondition, b: RuleCondition) -> bool:
    """Return True when two conditions can both match the same transaction."""
    return (
        _text_may_overlap(a, b)
        and _range_overlaps(
            a.amount_min_minor,
            a.amount_max_minor,
            b.amount_min_minor,
            b.amount_max_minor,
        )
        and _range_overlaps(
            a.day_of_month_min,
            a.day_of_month_max,
            b.day_of_month_min,
            b.day_of_month_max,
        )
    )


def find_collisions(
    candidate: RuleCondition,
    existing: Sequence[tuple[UUID, RuleCondition]],
) -> list[RuleCollision]:
    """Return the existing rules whose condition overlaps ``candidate``.

    ``existing`` pairs each rule id with its condition. Only conditions that
    can match the same transaction as ``candidate`` are reported.
    """
    collisions: list[RuleCollision] = []
    for rule_id, condition in existing:
        if conditions_overlap(candidate, condition):
            collisions.append(
                RuleCollision(
                    other_rule_id=rule_id,
                    reason=_overlap_reason(candidate, condition),
                )
            )
    return collisions


def _overlap_reason(a: RuleCondition, b: RuleCondition) -> str:
    reasons: list[str] = []
    if _text_may_overlap(a, b):
        reasons.append("matching text")
    if _range_overlaps(
        a.amount_min_minor, a.amount_max_minor, b.amount_min_minor, b.amount_max_minor
    ):
        reasons.append("amount range")
    if _range_overlaps(
        a.day_of_month_min,
        a.day_of_month_max,
        b.day_of_month_min,
        b.day_of_month_max,
    ):
        reasons.append("day-of-month range")
    return f"{' and '.join(reasons)} overlap"
