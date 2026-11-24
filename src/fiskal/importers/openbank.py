"""Openbank portal export importer.

The Openbank export is an HTML table saved with an ``.xls`` extension and
encoded in ``iso-8859-1``. This module parses it with the standard library's
``html.parser``; no third-party HTML library is required.

Only the five Spanish column titles are located by text, because they are part
of the real bank format. Everything else is detected by shape: spacer columns
are ignored, the data section starts at the first row whose two date cells
parse as ``dd/mm/yyyy``, and trailing blank rows are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from typing import ClassVar, Final

from fiskal.importers.base import (
    ImportedTransaction,
    Importer,
    ImporterError,
    ParsedStatement,
    build_identity_key,
    mask_card_numbers,
)

_COLUMN_TITLES: Final = (
    "Fecha Operación",
    "Fecha Valor",
    "Concepto",
    "Importe",
    "Saldo",
)

# Physical table columns are 10; the even indexes (0, 2, 4, 6, 8) are always
# empty spacers, so only the odd indexes carry content.
_CONTENT_COLUMN_INDICES: Final = (1, 3, 5, 7, 9)
_DESCRIPTION_WIDTH: Final = 100

# An Openbank account code is 20 digits grouped 4-4-2-10, e.g.
# "1234 5678 90 1234567890". Only its last four digits are ever kept.
_ACCOUNT_CODE_RE: Final = re.compile(r"\b(\d{4})\s+(\d{4})\s+(\d{2})\s+(\d{10})\b")

_DATE_TEXT_RE: Final = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_AMOUNT_RE: Final = re.compile(r"^-?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}$")

# Normalization helpers: strip the trailing reference so rules never depend on
# the end of a (possibly truncated) description, then extract the counterparty.
_REFERENCE_RE: Final = re.compile(r"\s*:?\s*REF\s*[0-9A-Za-z-]+\s*$", re.IGNORECASE)
_CARD_TOKEN_RE: Final = re.compile(r"\s+(?:\S+\s+)?CARD\b.*$", re.IGNORECASE)
_ACCOUNT_TOKEN_RE: Final = re.compile(r"\s+[\w-]+-\d[\w.-]*$")


@dataclass(frozen=True)
class _DataRow:
    """One physical movement row after dropping the spacer columns."""

    operation_date_text: str
    value_date_text: str
    description: str
    amount_text: str
    balance_text: str


class _TableParser(HTMLParser):
    """Minimal HTML table parser capturing the text of every ``<td>`` cell."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._rows: list[list[str]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._cell_parts: list[str] = []
        self._current_row: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._in_table = True
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._current_row = []
        elif tag == "td" and self._in_row:
            self._in_cell = True
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_cell:
            self._in_cell = False
            self._current_row.append("".join(self._cell_parts))
        elif tag == "tr" and self._in_row:
            self._in_row = False
            self._rows.append(self._current_row)
        elif tag == "table":
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)

    @property
    def rows(self) -> list[list[str]]:
        return self._rows


def _is_openbank_html(content: bytes) -> bool:
    """Return True when the leading bytes look like an HTML document."""
    head = content[:512].decode("iso-8859-1", errors="replace")
    head = head.lstrip("\ufeff").lstrip()
    lowered = head[:64].lower()
    return lowered.startswith("<!doctype html") or lowered.startswith("<html")


def parse_date(text: str) -> date:
    """Parse a ``dd/mm/yyyy`` date cell, raising on malformed input."""
    stripped = text.strip()
    if not _DATE_TEXT_RE.fullmatch(stripped):
        raise ImporterError("invalid date format")
    try:
        return datetime.strptime(stripped, "%d/%m/%Y").date()
    except ValueError as exc:
        raise ImporterError("invalid date") from exc


def parse_amount_minor(text: str) -> int:
    """Parse an amount or balance cell into integer minor units.

    The bank format is comma decimal, dot thousands, leading minus for
    expenses and no currency symbol. Removing the separators yields the minor
    units directly, so the arithmetic is exact integer arithmetic.
    """
    stripped = text.strip()
    if not _AMOUNT_RE.fullmatch(stripped):
        raise ImporterError("invalid amount format")
    negative = stripped.startswith("-")
    body = stripped[1:] if negative else stripped
    minor = int(body.replace(".", "").replace(",", ""))
    return -minor if negative else minor


def normalize_description(original: str) -> str:
    """Return the rule-ready, lowercased description without trailing reference."""
    text = " ".join(original.split())
    text = _REFERENCE_RE.sub("", text)
    return text.strip().lower()


def extract_counterparty(original: str) -> str | None:
    """Extract the other party from a description with a simple heuristic.

    The leading operation-kind word, any trailing card or account token, and
    the trailing reference are removed; what remains is the counterparty. This
    is intentionally minimal and will be refined with the classification
    engine.
    """
    text = " ".join(original.split())
    text = _REFERENCE_RE.sub("", text).strip()
    text = _CARD_TOKEN_RE.sub("", text)
    text = _ACCOUNT_TOKEN_RE.sub("", text)
    words = text.split()
    if not words:
        return None
    counterparty = " ".join(words[1:]).strip(" :.,")
    return counterparty or None


def _content_cells(row: list[str]) -> list[str]:
    """Return the five content cells of a physical row, ignoring spacers."""
    return [row[i] if i < len(row) else "" for i in _CONTENT_COLUMN_INDICES]


def _extract_account_hint(rows: list[list[str]]) -> str | None:
    """Return the last four digits of the account code, if the header has one."""
    for row in rows:
        for cell in row:
            match = _ACCOUNT_CODE_RE.search(cell)
            if match:
                digits = "".join(match.groups())
                return digits[-4:]
    return None


def _is_data_row(cells: list[str]) -> bool:
    """Return True when both leading cells look like ``dd/mm/yyyy`` dates."""
    return bool(
        _DATE_TEXT_RE.fullmatch(cells[0].strip())
        and _DATE_TEXT_RE.fullmatch(cells[1].strip())
    )


class OpenbankImporter(Importer):
    """Parse an Openbank HTML statement into neutral transactions."""

    _seen_identity_values: dict[tuple[object, ...], int]

    identity_fields: ClassVar[tuple[str, ...]] = (
        "operation_date",
        "value_date",
        "original_description",
        "amount_minor",
        "balance_minor",
    )
    match_fields: ClassVar[tuple[str, ...]] = (
        "operation_date",
        "value_date",
        "amount_minor",
        "balance_minor",
    )

    def parse(self, content: bytes) -> ParsedStatement:
        """Parse raw Openbank HTML bytes into a neutral statement."""
        # The occurrence counter is per file, so each parse starts fresh.
        self._seen_identity_values = {}
        if not _is_openbank_html(content):
            raise ImporterError(
                "Unsupported file: expected an Openbank statement exported as "
                "an HTML document (iso-8859-1)."
            )

        parser = _TableParser()
        parser.feed(content.decode("iso-8859-1"))
        parser.close()

        rows = parser.rows
        content_rows = [_content_cells(row) for row in rows]
        title_index = self._find_title_row(content_rows)
        # The account code lives in the header block, above the column titles.
        account_hint = _extract_account_hint(rows[:title_index])
        data_cells = self._extract_data_rows(content_rows, title_index)
        transactions = tuple(self._build_transaction(cells) for cells in data_cells)
        return ParsedStatement(account_hint=account_hint, transactions=transactions)

    @staticmethod
    def _find_title_row(content_rows: list[list[str]]) -> int:
        for index, row in enumerate(content_rows):
            if tuple(cell.strip() for cell in row) == _COLUMN_TITLES:
                return index
        raise ImporterError("Could not locate the Openbank column titles.")

    @staticmethod
    def _extract_data_rows(
        content_rows: list[list[str]], title_index: int
    ) -> list[list[str]]:
        data_rows: list[list[str]] = []
        seen_data = False
        for row in content_rows[title_index + 1 :]:
            stripped = [cell.strip() for cell in row]
            if not any(stripped):
                if seen_data:
                    break  # trailing blank rows are ignored
                continue  # tolerate blank rows before the first movement
            if _is_data_row(stripped):
                data_rows.append(stripped)
                seen_data = True
            elif seen_data:
                raise ImporterError("Unexpected content after the movement rows.")
            # Otherwise tolerate arbitrary header-block content before data.
        return data_rows

    def _build_transaction(self, cells: list[str]) -> ImportedTransaction:
        operation_date = parse_date(cells[0])
        value_date = parse_date(cells[1])
        amount_minor = parse_amount_minor(cells[3])
        balance_minor = parse_amount_minor(cells[4])

        original = mask_card_numbers(cells[2]).rstrip()
        truncated = len(original) == _DESCRIPTION_WIDTH
        normalized = normalize_description(original)
        counterparty = extract_counterparty(original)

        # Identity uses the masked, padding-stripped description so that the
        # same row re-imported always hashes identically.
        identity_values: tuple[object, ...] = (
            operation_date,
            value_date,
            original,
            amount_minor,
            balance_minor,
        )
        occurrence = self._occurrence_count(identity_values)
        identity_key = build_identity_key(identity_values, occurrence)

        return ImportedTransaction(
            operation_date=operation_date,
            value_date=value_date,
            original_description=original,
            normalized_description=normalized,
            counterparty=counterparty,
            amount_minor=amount_minor,
            balance_minor=balance_minor,
            identity_key=identity_key,
            truncated=truncated,
        )

    def _occurrence_count(self, identity_values: tuple[object, ...]) -> int:
        """Return the in-file occurrence counter for identical rows."""
        count = self._seen_identity_values.get(identity_values, 0)
        self._seen_identity_values[identity_values] = count + 1
        return count

    def __init__(self) -> None:
        """Create an importer with a fresh per-file occurrence counter."""
        self._seen_identity_values = {}
