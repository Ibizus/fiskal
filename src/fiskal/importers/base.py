"""Framework-free importer interface and neutral result types.

This module has no third-party dependencies beyond the standard library and no
database access. Every bank importer maps its own file format into the neutral
``ImportedTransaction`` value object declared here and announces how one of its
transactions is identified, so the import service can de-duplicate without
knowing any bank-specific field.
"""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import ClassVar, Final
from uuid import UUID


@dataclass(frozen=True)
class ImportedTransaction:
    """A neutral, framework-free movement produced by an importer.

    All bank-specific text has already been normalized: card numbers are
    masked, the description padding is stripped, and ``identity_key`` is fully
    computed. Importers produce this shape; the service persists it.
    """

    operation_date: date
    value_date: date
    original_description: str
    normalized_description: str
    counterparty: str | None
    amount_minor: int
    balance_minor: int
    identity_key: str
    truncated: bool


@dataclass(frozen=True)
class ParsedStatement:
    """The result of parsing one bank file, before any persistence."""

    account_hint: str | None
    transactions: tuple[ImportedTransaction, ...]


@dataclass(frozen=True)
class ImportSummary:
    """The summary reported to the user after an import."""

    first_transaction_date: date | None
    last_transaction_date: date | None
    rows_read: int
    new_count: int
    duplicate_count: int
    truncated_count: int
    warnings: tuple[str, ...]

    @property
    def adds_no_movement(self) -> bool:
        """True when the file contributed no new transaction at all."""
        return self.new_count == 0


@dataclass(frozen=True)
class ImportResult:
    """Outcome of a completed import: the persisted record plus the summary."""

    import_id: UUID
    summary: ImportSummary
    account_hint: str | None


class ImporterError(Exception):
    """Raised when a file is not the expected bank format.

    Error messages are user-facing and must never include file content.
    """


class ImportRevertError(Exception):
    """Raised when a whole import cannot be reverted."""


def field_values(obj: object, fields: tuple[str, ...]) -> tuple[object, ...]:
    """Return the named attribute values of ``obj`` in declaration order."""
    return tuple(getattr(obj, name) for name in fields)


def _serialize(value: object) -> str:
    """Serialize one identity component deterministically."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def build_identity_key(values: tuple[object, ...], occurrence: int) -> str:
    """Hash identity field values plus an in-file occurrence counter.

    The occurrence counter keeps genuinely identical rows inside one file
    distinct, while the same row imported again always hashes the same way.
    """
    payload = "|".join(_serialize(value) for value in values) + f"|occ:{occurrence}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_match_key(values: tuple[object, ...]) -> str:
    """Hash the near-duplicate fields (identity minus the description)."""
    payload = "|".join(_serialize(value) for value in values)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# A card number has 13-19 digits, contiguous or separated by spaces/dashes.
# Account codes (20 digits) and IBANs are intentionally not matched here.
_CARD_MIN_DIGITS: Final = 13
_CARD_MAX_DIGITS: Final = 19
_CARD_DIGIT_RUN_RE = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
_CARD_GROUPED_RE = re.compile(r"\b\d[\d\s-]{10,25}\d\b")


def _mask_card_match(match: re.Match[str]) -> str:
    """Replace a matched card-like token with its last four digits."""
    token = match.group(0)
    digits = "".join(char for char in token if char.isdigit())
    if _CARD_MIN_DIGITS <= len(digits) <= _CARD_MAX_DIGITS:
        return digits[-4:]
    return token


def mask_card_numbers(text: str) -> str:
    """Replace any 13-19 digit card number with its last four digits.

    Handles both contiguous digit runs and groups separated by spaces or
    dashes. A 20-digit account code is left untouched.
    """
    masked = _CARD_GROUPED_RE.sub(_mask_card_match, text)
    return _CARD_DIGIT_RUN_RE.sub(lambda match: match.group(0)[-4:], masked)


class Importer(ABC):
    """Interface every bank importer implements.

    Subclasses declare the ``ImportedTransaction`` field names that identify a
    transaction (``identity_fields``) and the narrower set used to detect a
    near-duplicate whose description text changed (``match_fields``).
    """

    identity_fields: ClassVar[tuple[str, ...]]
    match_fields: ClassVar[tuple[str, ...]]

    @abstractmethod
    def parse(self, content: bytes) -> ParsedStatement:
        """Parse raw file bytes into a neutral statement."""
