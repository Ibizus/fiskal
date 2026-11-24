"""Tests for the synthetic Openbank statement generator.

These tests verify that the generator produces files that match the structural
specification, are deterministic, and can be read back with pandas.
"""

from __future__ import annotations

import itertools
import random
import re
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tests.support.openbank_synthetic import (
    GenerationOptions,
    Movement,
    _format_money_minor,
    generate_movements,
    generate_statement_html,
    main,
    write_statement,
)

_ROW_COUNT_HEADER: int = 10
_COLUMN_COUNT: int = 10
_DATE_TEXT_LENGTH: int = 10
_CENTS_LENGTH: int = 2
_DESCRIPTION_WIDTH: int = 100
_ZERO_MOVEMENTS_ROW_COUNT: int = 11


@pytest.fixture
def default_options() -> GenerationOptions:
    """Return a default set of generation options for tests."""
    return GenerationOptions(
        movement_count=50,
        start_date=date(2024, 1, 1),
        account_code="12345678901234567890",
        opening_balance_minor=5000000,
        seed=42,
    )


def _read_html_tables(path: Path) -> list[pd.DataFrame]:
    """Read the HTML statement and return the parsed tables."""
    raw = path.read_bytes()
    assert raw.startswith(b"\n\t<!DOCTYPE html")
    return pd.read_html(
        path,
        flavor="lxml",
        keep_default_na=False,
        encoding="iso-8859-1",
    )


def test_determinism(default_options: GenerationOptions, tmp_path: Path) -> None:
    """Same seed and options must produce byte-identical files."""
    path_a = tmp_path / "a.xls"
    path_b = tmp_path / "b.xls"
    write_statement(default_options, path_a)
    write_statement(default_options, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_seed_changes_output(
    default_options: GenerationOptions, tmp_path: Path
) -> None:
    """Different seeds must produce different files."""
    path_a = tmp_path / "a.xls"
    path_b = tmp_path / "b.xls"
    write_statement(default_options, path_a)
    write_statement(replace(default_options, seed=43), path_b)
    assert path_a.read_bytes() != path_b.read_bytes()


def test_html_structure_facts(default_options: GenerationOptions) -> None:
    """The document must follow the required HTML container structure."""
    html_text = generate_statement_html(default_options)

    assert html_text.startswith(
        '\n\t<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN"',
    )
    assert (
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="es" xml:lang="es">'
        in html_text
    )
    assert "<head>" in html_text and "</head>" in html_text
    assert "<style" in html_text and "</style>" in html_text
    assert "charset=iso-8859-1" in html_text
    assert "<title>" not in html_text
    assert html_text.count("<table>") == 1
    assert "<thead>" not in html_text
    assert "<tbody>" not in html_text
    assert html_text.endswith("</html>\n")

    # Every <tr> has exactly 10 <td> children.
    rows = re.findall(r"<tr\b.*?</tr>", html_text, flags=re.DOTALL)
    assert rows
    for row in rows:
        open_td = len(re.findall(r"<td[ >]", row))
        assert open_td == _COLUMN_COUNT, (
            f"Row does not have {_COLUMN_COUNT} cells: {row[:200]}"
        )


def test_ten_header_rows_before_titles(
    default_options: GenerationOptions,
    tmp_path: Path,
) -> None:
    """There must be exactly 10 header rows before the column-title row."""
    path = tmp_path / "statement.xls"
    write_statement(default_options, path)
    tables = _read_html_tables(path)
    assert len(tables) == 1
    df = tables[0]

    title_row_index = None
    for idx, row in df.iterrows():
        if "Fecha Operación" in row.values:
            assert isinstance(idx, int)
            title_row_index = idx
            break
    assert title_row_index is not None
    assert title_row_index == _ROW_COUNT_HEADER


def test_column_titles_exact(
    default_options: GenerationOptions, tmp_path: Path
) -> None:
    """The column-title row must contain the exact expected headers."""
    path = tmp_path / "statement.xls"
    write_statement(default_options, path)
    tables = _read_html_tables(path)
    df = tables[0]

    title_row = df.iloc[10]
    # Content lives in the odd columns (1, 3, 5, 7, 9).
    titles = [title_row.iloc[i] for i in (1, 3, 5, 7, 9)]
    assert titles == [
        "Fecha Operación",
        "Fecha Valor",
        "Concepto",
        "Importe",
        "Saldo",
    ]


def test_spacer_columns_empty(
    default_options: GenerationOptions, tmp_path: Path
) -> None:
    """Columns 0, 2, 4, 6, 8 must always be empty in every row."""
    path = tmp_path / "statement.xls"
    write_statement(default_options, path)
    tables = _read_html_tables(path)
    df = tables[0]

    for _, row in df.iterrows():
        for col in (0, 2, 4, 6, 8):
            cell = str(row.iloc[col])
            assert cell in {"", "nan"}, f"Spacer column {col} not empty: {cell!r}"


def test_dates_newest_first(default_options: GenerationOptions, tmp_path: Path) -> None:
    """Data rows must be ordered newest first and formatted dd/mm/yyyy."""
    path = tmp_path / "statement.xls"
    write_statement(default_options, path)
    tables = _read_html_tables(path)
    df = tables[0]

    data_rows = df.iloc[11:].reset_index(drop=True)
    # Drop the trailing blank row.
    data_rows = data_rows[data_rows.iloc[:, 1] != ""].reset_index(drop=True)

    prev_date: date | None = None
    for _, row in data_rows.iterrows():
        op_text = row.iloc[1]
        assert (
            len(op_text) == _DATE_TEXT_LENGTH
            and op_text[2] == "/"
            and op_text[5] == "/"
        )
        op_date = pd.to_datetime(op_text, format="%d/%m/%Y").date()
        if prev_date is not None:
            assert op_date <= prev_date
        prev_date = op_date

        val_text = row.iloc[3]
        assert (
            len(val_text) == _DATE_TEXT_LENGTH
            and val_text[2] == "/"
            and val_text[5] == "/"
        )
        val_date = pd.to_datetime(val_text, format="%d/%m/%Y").date()
        assert val_date >= op_date


def _extract_data_cells(path: Path) -> list[tuple[str, str, str, str, str]]:
    """Return (op_date, val_date, description, amount, balance) for each row.

    Parses the raw HTML so that whitespace and text formatting are preserved
    exactly as written by the generator.
    """
    text = path.read_text(encoding="iso-8859-1")
    data_row_pattern = re.compile(
        r'<tr style="height:15px">'
        r"<td>&nbsp;</td>"
        r'<td class="celda" width="80" align="center">'
        r'<font class="fu">(.*?)</font></td>'
        r"<td>&nbsp;</td>"
        r'<td class="celda" width="80" align="center">'
        r'<font class="fu">(.*?)</font></td>'
        r"<td>&nbsp;</td>"
        r'<td class="celda" width="400" align="left">'
        r'<font class="fu">(.*?)</font></td>'
        r"<td>&nbsp;</td>"
        r'<td class="celda" width="90" align="right">'
        r'<font class="fu">(.*?)</font></td>'
        r"<td>&nbsp;</td>"
        r'<td class="celda" width="90" align="right">'
        r'<font class="fu">(.*?)</font></td>'
        r"</tr>"
    )
    return data_row_pattern.findall(text)


def test_amount_text_format(default_options: GenerationOptions, tmp_path: Path) -> None:
    """Amount cells must use comma decimals and dot thousands."""
    options = replace(default_options, thousands_in_amounts=True)
    path = tmp_path / "statement.xls"
    write_statement(options, path)
    cells = _extract_data_cells(path)
    assert cells

    seen_thousands = False
    seen_negative = False
    seen_positive = False
    for _op, _val, _desc, amount_text, _bal in cells:
        # Remove optional leading minus.
        body = amount_text.lstrip("-")
        assert "," in body
        whole, cents = body.rsplit(",", 1)
        assert len(cents) == _CENTS_LENGTH
        assert whole.replace(".", "").isdigit()
        if "." in whole:
            seen_thousands = True
        if amount_text.startswith("-"):
            seen_negative = True
        else:
            seen_positive = True

    assert seen_thousands
    assert seen_negative
    assert seen_positive


def test_descriptions_padded_to_100(
    default_options: GenerationOptions,
    tmp_path: Path,
) -> None:
    """Every description cell must contain exactly 100 characters."""
    options = replace(default_options, truncate_descriptions=True)
    path = tmp_path / "statement.xls"
    write_statement(options, path)
    cells = _extract_data_cells(path)
    assert cells

    lengths = {len(desc) for _op, _val, desc, _amt, _bal in cells}
    for _op, _val, desc, _amt, _bal in cells:
        assert len(desc) == _DESCRIPTION_WIDTH, (
            f"Description length {len(desc)} != {_DESCRIPTION_WIDTH}"
        )

    # With truncation enabled at least one description should be cut.
    assert max(lengths) == _DESCRIPTION_WIDTH


def test_balance_coherence(default_options: GenerationOptions) -> None:
    """Each row's balance equals the next-older row's balance plus its amount."""
    movements = generate_movements(default_options)
    assert len(movements) == default_options.movement_count

    for i in range(len(movements) - 1):
        # movements is newest first; next-older is i + 1.
        expected = movements[i + 1].balance_minor + movements[i].amount_minor
        assert movements[i].balance_minor == expected


def test_zero_movements_file(
    default_options: GenerationOptions, tmp_path: Path
) -> None:
    """A file with zero movements must still contain the header and titles."""
    options = replace(default_options, zero_movements=True)
    path = tmp_path / "empty.xls"
    write_statement(options, path)
    tables = _read_html_tables(path)
    df = tables[0]

    assert len(df) == _ZERO_MOVEMENTS_ROW_COUNT  # header rows + title row
    assert "Fecha Operación" in df.iloc[10].values


def test_extra_trailing_blank(
    default_options: GenerationOptions, tmp_path: Path
) -> None:
    """The extra-trailing-blank switch appends one additional blank row."""
    options = replace(default_options, extra_trailing_blank=True)
    path = tmp_path / "statement.xls"
    write_statement(options, path)
    tables = _read_html_tables(path)
    df = tables[0]

    # Last two rows should be blank (spacer columns only).
    for offset in (-1, -2):
        row = df.iloc[offset]
        assert row.iloc[1] == ""


def test_repeated_rows_on_same_day(default_options: GenerationOptions) -> None:
    """Repeated-rows mode produces rows identical in every non-balance field."""
    options = replace(default_options, repeated_rows=True)
    chrono = list(reversed(generate_movements(options)))

    def _key(movement: Movement) -> tuple[date, date, str, int]:
        return (
            movement.operation_date,
            movement.value_date,
            movement.description,
            movement.amount_minor,
        )

    assert _key(chrono[0]) == _key(chrono[1])
    repeats = sum(
        _key(older) == _key(newer) for older, newer in itertools.pairwise(chrono)
    )
    assert repeats > 1


def test_header_balance_is_current_balance(
    default_options: GenerationOptions, tmp_path: Path
) -> None:
    """The header balance must equal the newest row's balance."""
    path = tmp_path / "statement.xls"
    write_statement(default_options, path)
    df = _read_html_tables(path)[0]
    newest = generate_movements(default_options)[0]
    assert df.iloc[7, 3] == f"{_format_money_minor(newest.balance_minor)} EUR"


def test_header_timestamp_comes_from_movements(
    default_options: GenerationOptions,
) -> None:
    """The generation timestamp must derive from the data, not the clock."""
    html_text = generate_statement_html(default_options)
    newest = generate_movements(default_options)[0]
    expected = newest.operation_date.strftime("%d/%m/%Y") + " 00:00H"
    assert f"Fecha de generacion: {expected}" in html_text


def test_cli_parses_dot_decimal_opening_balance(tmp_path: Path) -> None:
    """``--opening-balance 50000.00`` must mean fifty thousand euros."""
    path = tmp_path / "cli.xls"
    assert main([str(path), "--zero-movements", "--opening-balance", "50000.00"]) == 0
    df = _read_html_tables(path)[0]
    assert df.iloc[7, 3] == "50.000,00 EUR"


@pytest.mark.parametrize("value", ["50.000,00", "1.234", "abc", "nan"])
def test_cli_rejects_ambiguous_opening_balance(tmp_path: Path, value: str) -> None:
    """Amounts that are not dot-decimal with two decimals must be rejected."""
    with pytest.raises(SystemExit):
        main([str(tmp_path / "x.xls"), "--opening-balance", value])


def test_accented_characters_survive(
    default_options: GenerationOptions, tmp_path: Path
) -> None:
    """Accented characters must be preserved through iso-8859-1 encoding."""
    options = replace(default_options, accented_descriptions=True)
    path = tmp_path / "statement.xls"
    write_statement(options, path)
    raw = path.read_bytes()
    # The column title contains 'ó', encoded as 0xF3 in iso-8859-1.
    assert b"Fecha Operaci\xf3n" in raw


def test_pandas_read_html_roundtrip(
    default_options: GenerationOptions,
    tmp_path: Path,
) -> None:
    """pandas.read_html must successfully parse the generated file."""
    path = tmp_path / "statement.xls"
    write_statement(default_options, path)
    tables = _read_html_tables(path)
    assert len(tables) == 1
    df = tables[0]
    # header rows + title row + data rows + trailing blank row.
    assert len(df) >= _ZERO_MOVEMENTS_ROW_COUNT + 1


def test_format_money_minor() -> None:
    """Money formatting helpers produce the expected bank text."""
    assert _format_money_minor(1234) == "12,34"
    assert _format_money_minor(-1234) == "-12,34"
    assert _format_money_minor(100000) == "1.000,00"
    assert _format_money_minor(0) == "0,00"
    assert _format_money_minor(99) == "0,99"


def test_random_generator_is_seeded(default_options: GenerationOptions) -> None:
    """The internal RNG must be seeded and reproducible."""
    movements_a = generate_movements(default_options)
    movements_b = generate_movements(default_options)
    assert movements_a == movements_b

    # Sanity check: the default seed is not silently ignored by using the
    # global random state.
    default_options_copy = replace(default_options)
    random.seed(999)
    movements_c = generate_movements(default_options_copy)
    assert movements_c == movements_a
