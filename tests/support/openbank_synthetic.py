"""Deterministic generator for synthetic Openbank-style HTML statements.

The output imitates the structural description of the Openbank portal export
without using any real bank data. Every value is invented.
"""

from __future__ import annotations

import argparse
import html
import random
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

_DESCRIPTION_WIDTH: Final = 100
_ACCOUNT_CODE_LENGTH: Final = 20
_THOUSANDS_THRESHOLD: Final = 1000


@dataclass(frozen=True)
class GenerationOptions:
    """Options controlling the shape and edge cases of a synthetic statement."""

    movement_count: int
    start_date: date
    account_code: str
    opening_balance_minor: int
    seed: int
    truncate_descriptions: bool = False
    repeated_rows: bool = False
    accented_descriptions: bool = False
    thousands_in_amounts: bool = False
    income_expense_mix: float = 0.14
    extra_trailing_blank: bool = False
    zero_movements: bool = False


@dataclass(frozen=True)
class Movement:
    """One synthetic movement in chronological order (oldest first)."""

    operation_date: date
    value_date: date
    description: str
    amount_minor: int
    balance_minor: int


def _format_money_minor(value_minor: int, *, force_thousands: bool = False) -> str:
    """Format an integer minor-unit amount as Spanish-bank text.

    The format always has a comma decimal separator and exactly two decimals.
    A dot is used as the thousands separator when the absolute value is
    1 000 or more, or when ``force_thousands`` is True.
    """
    negative = value_minor < 0
    whole = abs(value_minor) // 100
    cents = abs(value_minor) % 100
    if force_thousands or whole >= _THOUSANDS_THRESHOLD:
        whole_text = f"{whole:,}".replace(",", ".")
    else:
        whole_text = str(whole)
    result = f"{whole_text},{cents:02d}"
    if negative:
        result = f"-{result}"
    return result


def _format_account_code(code: str) -> str:
    """Format a 20-digit account code as 4-4-2-10 groups."""
    if len(code) != _ACCOUNT_CODE_LENGTH or not code.isdigit():
        raise ValueError("account_code must be exactly 20 digits")
    return f"{code[0:4]} {code[4:8]} {code[8:10]} {code[10:20]}"


def _pad_description(text: str, width: int = _DESCRIPTION_WIDTH) -> str:
    """Space-pad ``text`` to exactly ``width`` characters and cut at ``width``."""
    if len(text) > width:
        return text[:width]
    return text.ljust(width)


def _generate_operation_dates(
    rng: random.Random,
    start_date: date,
    movement_count: int,
) -> list[date]:
    """Return operation dates in chronological order (oldest first).

    Dates are non-decreasing and grouped into same-day runs of 1-8 rows.
    """
    if movement_count <= 0:
        return []

    dates: list[date] = []
    current = start_date
    remaining = movement_count
    while remaining > 0:
        run_size = min(rng.randint(1, 8), remaining)
        dates.extend([current] * run_size)
        remaining -= run_size
        if remaining > 0:
            current += timedelta(days=rng.randint(1, 3))
    return dates


# Probability constants for description and amount generation.
_ACCENT_PROBABILITY: Final = 0.09
_REFERENCE_PROBABILITY: Final = 0.5
_CARD_TEMPLATE_PROBABILITY: Final = 0.15
_TRANSFER_TEMPLATE_PROBABILITY: Final = 0.15
_TRUNCATION_PROBABILITY: Final = 0.03
_THOUSANDS_AMOUNT_PROBABILITY: Final = 0.3
_REPEATED_ROW_PROBABILITY: Final = 0.2

# Invented word pools. Nothing here resembles a real merchant or person.
_DESCRIPTORS: Final[list[str]] = [
    "Payment",
    "Receipt",
    "Purchase",
    "Transfer",
    "Deposit",
    "Refund",
    "DirectDebit",
]

_SUBJECTS: Final[list[str]] = [
    "Fictional",
    "Example",
    "Demo",
    "Sample",
    "Specimen",
    "Demo Shop",
    "Demo Service",
    "Example Store",
]

_ACCENTED_SUBJECTS: Final[list[str]] = [
    "Fictional Café",
    "Tech Service",
    "Electronics Store",
    "Monthly Café",
    "Medical Payment",
]

_CARD_MASK: Final = "DEMO CARD 1234 56** **** 7890"
_TRANSFER_ACCOUNT: Final = "DEMO-ACCOUNT-1234-5678-90"


def _generate_description(
    rng: random.Random,
    *,
    truncate_descriptions: bool,
    accented_descriptions: bool,
    index: int,
) -> str:
    """Return a single invented description padded to exactly 100 characters."""
    use_accent = accented_descriptions or rng.random() < _ACCENT_PROBABILITY
    use_reference = rng.random() < _REFERENCE_PROBABILITY
    use_card = rng.random() < _CARD_TEMPLATE_PROBABILITY
    use_transfer = not use_card and rng.random() < _TRANSFER_TEMPLATE_PROBABILITY

    subject = rng.choice(_ACCENTED_SUBJECTS) if use_accent else rng.choice(_SUBJECTS)

    if use_card:
        base = f"{rng.choice(_DESCRIPTORS)} {subject} {_CARD_MASK} REF {index:06d}"
    elif use_transfer:
        base = (
            f"{rng.choice(_DESCRIPTORS)} {subject} {_TRANSFER_ACCOUNT} REF {index:06d}"
        )
    elif use_reference:
        base = f"{rng.choice(_DESCRIPTORS)} {subject}: REF {index:08d}"
    else:
        base = f"{rng.choice(_DESCRIPTORS)} {subject} sample transaction"

    # Occasionally produce a description that reaches the cut-off.
    if truncate_descriptions or rng.random() < _TRUNCATION_PROBABILITY:
        filler = " " + " ".join(
            [rng.choice(_SUBJECTS).lower() for _ in range(rng.randint(4, 8))]
        )
        base = (base + filler)[:_DESCRIPTION_WIDTH]

    return _pad_description(base)


def _generate_amount(
    rng: random.Random,
    *,
    force_thousands: bool,
    income_probability: float,
) -> int:
    """Return an amount in integer minor units."""
    if force_thousands and rng.random() < _THOUSANDS_AMOUNT_PROBABILITY:
        whole = rng.randint(1000, 5000)
    else:
        whole = rng.randint(1, 500)
    cents = rng.randint(0, 99)
    value = whole * 100 + cents
    is_income = rng.random() < income_probability
    if is_income:
        return value
    return -value


def generate_movements(options: GenerationOptions) -> list[Movement]:
    """Generate a coherent list of movements in file order (newest first).

    Balance coherence is guaranteed: for each row, its balance equals the
    next-older row's balance plus this row's amount.
    """
    rng = random.Random(options.seed)

    if options.zero_movements:
        return []

    operation_dates_chrono = _generate_operation_dates(
        rng, options.start_date, options.movement_count
    )

    movements_chrono: list[Movement] = []
    running_balance = options.opening_balance_minor
    for idx, op_date in enumerate(operation_dates_chrono):
        # In repeated-rows mode the second row always repeats the first one,
        # and later rows occasionally repeat their predecessor on the same day.
        if (
            options.repeated_rows
            and movements_chrono
            and (idx == 1 or rng.random() < _REPEATED_ROW_PROBABILITY)
        ):
            previous = movements_chrono[-1]
            operation_date = previous.operation_date
            value_date = previous.value_date
            description = previous.description
            amount = previous.amount_minor
        else:
            operation_date = op_date
            value_date = op_date + timedelta(days=rng.randint(0, 2))
            description = _generate_description(
                rng,
                truncate_descriptions=options.truncate_descriptions,
                accented_descriptions=options.accented_descriptions,
                index=idx + 1,
            )
            amount = _generate_amount(
                rng,
                force_thousands=options.thousands_in_amounts,
                income_probability=options.income_expense_mix,
            )

        running_balance += amount
        movements_chrono.append(
            Movement(
                operation_date=operation_date,
                value_date=value_date,
                description=description,
                amount_minor=amount,
                balance_minor=running_balance,
            )
        )

    # File order is newest first.
    return list(reversed(movements_chrono))


def _cell(content: str, *, bold: bool = False) -> str:
    """Build a content cell wrapping text in a ``<font>`` tag."""
    if bold:
        return f'<font class="fu"><b>{html.escape(content)}</b></font>'
    return f'<font class="fu">{html.escape(content)}</font>'


def _empty_row() -> str:
    """Build a fully blank row with ten empty cells."""
    return "<tr>" + "".join("<td>&nbsp;</td>" for _ in range(10)) + "</tr>"


def _header_rows(options: GenerationOptions, movements: list[Movement]) -> str:
    """Build the ten header rows that precede the column titles.

    ``movements`` is in file order (newest first). The header balance is the
    current balance, i.e. the newest row's balance, and the generation
    timestamp is derived from the newest operation date so output stays
    deterministic.
    """
    account_code_formatted = _format_account_code(options.account_code)
    if movements:
        current_balance_minor = movements[0].balance_minor
        generation_day = movements[0].operation_date
    else:
        current_balance_minor = options.opening_balance_minor
        generation_day = options.start_date
    header_amount = _format_money_minor(current_balance_minor)
    generation_timestamp = datetime.combine(generation_day, time())
    generated_at = generation_timestamp.strftime("%d/%m/%Y %H:%M") + "H"

    # Row 1: bold free-text title in col 1.
    title_text = "Fabricated -\n\t\tSynthetic"
    row1 = (
        "<tr>"
        "<td>&nbsp;</td>"
        f'<td class="celda bold">{_cell(title_text, bold=True)}</td>'
        + "".join("<td>&nbsp;</td>" for _ in range(8))
        + "</tr>"
    )

    # Row 2: bold label + timestamp in col 9 (0-based index 9).
    row2_label = f"Generation date: {generated_at}"
    row2 = (
        "<tr>"
        + "".join("<td>&nbsp;</td>" for _ in range(9))
        + f'<td class="celda bold">{_cell(row2_label, bold=True)}</td>'
        + "</tr>"
    )

    # Row 3: account-code label and value.
    row3_label = "Account:\n\t\t\t\t"
    row3 = (
        "<tr>"
        "<td>&nbsp;</td>"
        f'<td class="celda">{_cell(row3_label)}</td>'
        "<td>&nbsp;</td>"
        f'<td class="celda bold">{_cell(account_code_formatted, bold=True)}</td>'
        + "".join("<td>&nbsp;</td>" for _ in range(6))
        + "</tr>"
    )

    # Rows 5-7: label/value pairs with 5 tabs after the colon.
    label_tabs = ":\n\t\t\t\t\t"
    row5_label = f"Holder{label_tabs}"
    row5_value = "Demo Fictional User".ljust(50)
    row6_label = f"Product{label_tabs}"
    row6_value = "Demo Current Account"
    row7_label = f"Balance{label_tabs}"
    row7_value = f"{header_amount} EUR"

    def _label_value_row(label: str, value: str, *, value_bold: bool = False) -> str:
        return (
            "<tr>"
            "<td>&nbsp;</td>"
            f'<td class="celda">{_cell(label)}</td>'
            "<td>&nbsp;</td>"
            f'<td class="celda">{_cell(value, bold=value_bold)}</td>'
            + "".join("<td>&nbsp;</td>" for _ in range(6))
            + "</tr>"
        )

    row5 = _label_value_row(row5_label, row5_value)
    row6 = _label_value_row(row6_label, row6_value)
    row7 = _label_value_row(row7_label, row7_value, value_bold=True)

    # Row 8: bold three-word title in col 1 without colon.
    row8_title = "Demo Movement List"
    row8 = (
        "<tr>"
        "<td>&nbsp;</td>"
        f'<td class="celda bold">{_cell(row8_title, bold=True)}</td>'
        + "".join("<td>&nbsp;</td>" for _ in range(8))
        + "</tr>"
    )

    return (
        _empty_row()
        + row1
        + row2
        + row3
        + _empty_row()
        + row5
        + row6
        + row7
        + row8
        + _empty_row()
    )


def _column_title_row() -> str:
    """Build the column-title row with the exact expected headers.

    These five Spanish titles are part of the real Openbank portal export
    format and the importer locates the data section by matching them.
    Everything else in the generated statement is invented English text.
    """
    titles = [
        "Fecha Operación",
        "Fecha Valor",
        "Concepto",
        "Importe",
        "Saldo",
    ]
    cells = []
    for _idx, title in enumerate(titles):
        cells.append("<td>&nbsp;</td>")
        cells.append(
            f'<td class="celda bold" align="center">{_cell(title, bold=True)}</td>'
        )
    return "<tr>" + "".join(cells) + "</tr>"


def _data_rows(
    movements: list[Movement],
    *,
    extra_trailing_blank: bool = False,
) -> str:
    """Build the movement rows, newest first, plus one trailing blank row."""
    rows: list[str] = []
    for movement in movements:
        op_text = movement.operation_date.strftime("%d/%m/%Y")
        val_text = movement.value_date.strftime("%d/%m/%Y")
        amount_text = _format_money_minor(movement.amount_minor)
        balance_text = _format_money_minor(movement.balance_minor)

        row = (
            '<tr style="height:15px">'
            "<td>&nbsp;</td>"
            f'<td class="celda" width="80" align="center">{_cell(op_text)}</td>'
            "<td>&nbsp;</td>"
            f'<td class="celda" width="80" align="center">{_cell(val_text)}</td>'
            "<td>&nbsp;</td>"
            '<td class="celda" width="400" align="left">'
            f"{_cell(movement.description)}</td>"
            "<td>&nbsp;</td>"
            '<td class="celda" width="90" align="right">'
            f"{_cell(amount_text)}</td>"
            "<td>&nbsp;</td>"
            f'<td class="celda" width="90" align="right">{_cell(balance_text)}</td>'
            "</tr>"
        )
        rows.append(row)

    if movements:
        rows.append(_empty_row())
    if extra_trailing_blank:
        rows.append(_empty_row())
    return "".join(rows)


def generate_statement_html(options: GenerationOptions) -> str:
    """Return the full HTML statement as a Unicode string."""
    movements = generate_movements(options)

    html_doc = (
        '\n\t<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" '
        '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="es" xml:lang="es">\n'
        "<head>\n"
        '<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1" />\n'
        '<style type="text/css">\n'
        ".celda { font-family: Arial, sans-serif; font-size: 10px; }\n"
        ".fu { font-family: Arial, sans-serif; font-size: 10px; }\n"
        ".bold { font-weight: bold; }\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        "<table>\n"
        + _header_rows(options, movements)
        + _column_title_row()
        + _data_rows(
            movements,
            extra_trailing_blank=options.extra_trailing_blank,
        )
        + "</table>\n"
        "</body>\n"
        "</html>\n"
    )
    return html_doc


def write_statement(
    options: GenerationOptions,
    destination: Path,
    *,
    encoding: str = "iso-8859-1",
) -> Path:
    """Generate a synthetic statement and write it to ``destination``.

    Returns the written path. The file is encoded with ``iso-8859-1`` and
    therefore is byte-identical for the same options and seed.
    """
    html_doc = generate_statement_html(options)
    destination.write_bytes(html_doc.encode(encoding))
    return destination


def _positive_int(value: str) -> int:
    """Argparse helper: parse a positive integer."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _date_value(value: str) -> date:
    """Argparse helper: parse a date as YYYY-MM-DD."""
    return datetime.strptime(value, "%Y-%m-%d").date()


def _money_minor(value: str) -> int:
    """Argparse helper: parse a dot-decimal amount such as ``50000.00``."""
    try:
        minor = Decimal(value) * 100
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(
            "must be a dot-decimal amount such as 50000.00"
        ) from exc
    if not minor.is_finite() or minor != minor.to_integral_value():
        raise argparse.ArgumentTypeError(
            "must be a dot-decimal amount with at most two decimals"
        )
    return int(minor)


def build_argument_parser() -> argparse.ArgumentParser:
    """Return the CLI argument parser for the fixture generator."""
    parser = argparse.ArgumentParser(
        description="Generate a synthetic Openbank-style HTML statement.",
    )
    parser.add_argument(
        "destination",
        type=Path,
        help="Output file path (usually ending in .xls).",
    )
    parser.add_argument(
        "--movements",
        type=_positive_int,
        default=50,
        help="Number of movements to generate (default: 50).",
    )
    parser.add_argument(
        "--start-date",
        type=_date_value,
        default=date.today().isoformat(),
        help="Operation date of the oldest movement (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--account-code",
        type=str,
        default="12345678901234567890",
        help="20-digit synthetic account code.",
    )
    parser.add_argument(
        "--opening-balance",
        type=_money_minor,
        default="50000.00",
        help="Opening balance before the oldest movement (default: 50000.00).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed (default: 42).",
    )
    parser.add_argument(
        "--truncate-descriptions",
        action="store_true",
        help="Force many descriptions to reach the 100-character cut-off.",
    )
    parser.add_argument(
        "--repeated-rows",
        action="store_true",
        help="Include identical repeated rows on the same day.",
    )
    parser.add_argument(
        "--accented-descriptions",
        action="store_true",
        help="Force accented characters in descriptions.",
    )
    parser.add_argument(
        "--thousands-in-amounts",
        action="store_true",
        help="Include amounts with thousands separators.",
    )
    parser.add_argument(
        "--income-expense-mix",
        type=float,
        default=0.14,
        help="Probability that a movement is income (default: 0.14).",
    )
    parser.add_argument(
        "--extra-trailing-blank",
        action="store_true",
        help="Append an extra fully blank row at the end.",
    )
    parser.add_argument(
        "--zero-movements",
        action="store_true",
        help="Produce a file with zero movements.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the synthetic statement generator."""
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    if (
        len(args.account_code) != _ACCOUNT_CODE_LENGTH
        or not args.account_code.isdigit()
    ):
        parser.error("--account-code must be exactly 20 digits")

    options = GenerationOptions(
        movement_count=args.movements,
        start_date=args.start_date,
        account_code=args.account_code,
        opening_balance_minor=args.opening_balance,
        seed=args.seed,
        truncate_descriptions=args.truncate_descriptions,
        repeated_rows=args.repeated_rows,
        accented_descriptions=args.accented_descriptions,
        thousands_in_amounts=args.thousands_in_amounts,
        income_expense_mix=args.income_expense_mix,
        extra_trailing_blank=args.extra_trailing_blank,
        zero_movements=args.zero_movements,
    )

    write_statement(options, args.destination)
    print(f"Synthetic statement written to {args.destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
