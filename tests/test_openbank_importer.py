"""Tests for the Openbank importer and the import service.

All fixtures are synthetic statements produced by the generator under
``tests/support/``; no real bank file is involved. Any full card number used
to exercise masking is built dynamically at runtime so the personal-data guard
never sees a 13-19 digit literal in the repository.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from fiskal.db.base import Base
from fiskal.db.engine import make_engine
from fiskal.db.models import Account, Import, Transaction
from fiskal.domain.enums import TransactionStatus
from fiskal.importers import (
    ImporterError,
    ImportRevertError,
    OpenbankImporter,
    import_statement,
    revert_import,
)
from fiskal.importers.base import mask_card_numbers
from fiskal.importers.openbank import (
    extract_counterparty,
    normalize_description,
    parse_amount_minor,
    parse_date,
)
from tests.support.openbank_synthetic import (
    GenerationOptions,
    Movement,
    build_statement_html,
    generate_movements,
    write_statement,
)

_ACCOUNT_CODE = "12345678901234567890"


@pytest.fixture
def session():
    """Return a session on an empty in-memory database with FKs enabled."""
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def account(session):
    """Return a persisted EUR account."""
    account = Account(name="Test account", currency_code="EUR")
    session.add(account)
    session.commit()
    return account


@pytest.fixture
def default_options() -> GenerationOptions:
    """Return a small deterministic generation configuration."""
    return GenerationOptions(
        movement_count=30,
        start_date=date(2024, 1, 1),
        account_code=_ACCOUNT_CODE,
        opening_balance_minor=5_000_000,
        seed=42,
    )


def _fake_card_number() -> str:
    """Build a 16-digit card number without a literal 13-19 digit run."""
    return "".join(str((i * 7 + 3) % 10) for i in range(16))


def _movement(day: date, description: str, amount: int, balance: int) -> Movement:
    """Build one padded synthetic movement."""
    return Movement(
        operation_date=day,
        value_date=day,
        description=description.ljust(100),
        amount_minor=amount,
        balance_minor=balance,
    )


def _write_custom_statement(options: GenerationOptions, movements, path: Path) -> Path:
    """Write a synthetic statement built from explicit movements."""
    html = build_statement_html(options, list(movements))
    path.write_bytes(html.encode("iso-8859-1"))
    return path


# --- Parser unit tests -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12,34", 1234),
        ("-12,34", -1234),
        ("0,00", 0),
        ("1.000,00", 100_000),
        ("-1.234,56", -123_456),
        ("1.234.567,89", 123_456_789),
    ],
)
def test_parse_amount_minor_formats(text: str, expected: int) -> None:
    """Amounts use comma decimals, dot thousands, exact integer arithmetic."""
    assert parse_amount_minor(text) == expected


@pytest.mark.parametrize(
    "value", ["12.34", "12,345", "1.23,45", "abc", "12,3", "12,3456"]
)
def test_parse_amount_minor_rejects_invalid(value: str) -> None:
    """Anything but the exact bank money shape must be rejected."""
    with pytest.raises(ImporterError):
        parse_amount_minor(value)


def test_parse_date() -> None:
    """Dates parse strictly as dd/mm/yyyy."""
    assert parse_date("01/01/2024") == date(2024, 1, 1)
    assert parse_date("31/12/2023") == date(2023, 12, 31)
    with pytest.raises(ImporterError):
        parse_date("2024-01-01")
    with pytest.raises(ImporterError):
        parse_date("32/01/2024")


def test_mask_card_numbers() -> None:
    """Card numbers shrink to their last four digits; account codes do not."""
    card = _fake_card_number()
    assert mask_card_numbers(f"buy {card} end") == f"buy {card[-4:]} end"
    grouped = f"{card[:4]} {card[4:8]} {card[8:12]} {card[12:]}"
    assert mask_card_numbers(f"buy {grouped} end") == f"buy {card[-4:]} end"
    # A 20-digit account code is not a card number.
    assert mask_card_numbers(f"account {_ACCOUNT_CODE}") == f"account {_ACCOUNT_CODE}"
    dashed = "-".join((card[:4], card[4:8], card[8:12], card[12:]))
    # Digit groups next to a grouped card may be masked with it, but no window
    # of the card's digits survives.
    assert mask_card_numbers(f"CARD {grouped} 2024 SHOP") == "CARD 2024 SHOP"
    assert mask_card_numbers(f"CARD {dashed} 1234 REF") == "CARD 1234 REF"
    assert mask_card_numbers(f"CARD {grouped} 12 EUR") == (f"CARD {card[-2:]}12 EUR")
    assert mask_card_numbers(f"PAY 1 {card}") == f"PAY 1 {card[-4:]}"
    assert mask_card_numbers(f"ORDER 1234567890 {grouped}") == (f"ORDER {card[-4:]}")
    assert mask_card_numbers(f"TEL 612345678 {dashed} X") == f"TEL {card[-4:]} X"
    assert mask_card_numbers(f"A 12 {grouped}") == f"A {card[-4:]}"
    # An already-masked bank card is left alone.
    assert mask_card_numbers("DEMO CARD 1234 56** **** 7890") == (
        "DEMO CARD 1234 56** **** 7890"
    )


def test_normalize_and_counterparty() -> None:
    """Normalization lowers, collapses and strips the trailing reference."""
    assert normalize_description("  Payment   Fictional: REF 00000008  ") == (
        "payment fictional"
    )
    assert (
        extract_counterparty(
            "Purchase Fictional DEMO CARD 1234 56** **** 7890 REF 00000003"
        )
        == "Fictional"
    )
    assert (
        extract_counterparty(
            "Transfer Fictional DEMO-ACCOUNT-1234-5678-90 REF 00000002"
        )
        == "Fictional"
    )
    assert extract_counterparty("Payment Fictional: REF 00000008") == "Fictional"
    assert extract_counterparty("Payment Fictional Café: REF 00000001") == (
        "Fictional Café"
    )


# --- Parser integration tests ------------------------------------------------


def test_parse_generated_statement(default_options, tmp_path) -> None:
    """A generated file round-trips into the neutral transaction shape."""
    path = tmp_path / "statement.xls"
    write_statement(default_options, path)
    movements = generate_movements(default_options)

    parsed = OpenbankImporter().parse(path.read_bytes())

    assert parsed.account_hint == _ACCOUNT_CODE[-4:]
    assert len(parsed.transactions) == len(movements)
    for txn, movement in zip(parsed.transactions, movements, strict=True):
        assert txn.operation_date == movement.operation_date
        assert txn.value_date == movement.value_date
        assert txn.amount_minor == movement.amount_minor
        assert txn.balance_minor == movement.balance_minor
        assert txn.original_description == movement.description.rstrip()
        assert txn.identity_key
        # Normalized text never carries the trailing reference.
        assert not txn.normalized_description.endswith("ref")


def test_parse_rejects_non_html(tmp_path) -> None:
    """Non-HTML content is rejected by magic bytes, never by extension."""
    importer = OpenbankImporter()
    with pytest.raises(ImporterError) as exc:
        importer.parse(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 128)
    assert "expected an Openbank" in str(exc.value)
    assert "Unsupported file" in str(exc.value)


def test_parse_rejects_html_without_titles(tmp_path) -> None:
    """An HTML document without the Openbank titles is rejected."""
    html = (
        "<!DOCTYPE html><html><body><table>"
        "<tr><td>a</td><td>b</td></tr>"
        "</table></body></html>"
    )
    with pytest.raises(ImporterError) as exc:
        OpenbankImporter().parse(html.encode("iso-8859-1"))
    assert "column titles" in str(exc.value)


def test_parse_zero_movements(default_options, tmp_path) -> None:
    """A statement with no movement rows parses to zero transactions."""
    options = replace(default_options, movement_count=0)
    path = tmp_path / "empty.xls"
    write_statement(options, path)
    parsed = OpenbankImporter().parse(path.read_bytes())
    assert parsed.transactions == ()


def test_identical_rows_get_distinct_identity_keys(default_options, tmp_path) -> None:
    """Two identical rows in one file are kept distinct by the occurrence counter."""
    movements = generate_movements(replace(default_options, movement_count=3))
    duplicated = [movements[0], replace(movements[0]), *movements[1:]]
    path = _write_custom_statement(default_options, duplicated, tmp_path / "dup.xls")

    parsed = OpenbankImporter().parse(path.read_bytes())
    keys = [txn.identity_key for txn in parsed.transactions]
    assert len(parsed.transactions) == len(duplicated)
    assert keys[0] != keys[1]
    assert len(set(keys)) == len(duplicated)


# --- Import service tests ----------------------------------------------------


def test_import_persists_movements(session, account, default_options, tmp_path) -> None:
    """Importing a file persists the movements and an import record."""
    path = tmp_path / "statement.xls"
    write_statement(default_options, path)
    movements = generate_movements(default_options)

    result = import_statement(
        session, OpenbankImporter(), account, "statement.xls", path.read_bytes()
    )

    assert result.summary.rows_read == len(movements)
    assert result.summary.new_count == len(movements)
    assert result.summary.duplicate_count == 0
    assert result.summary.adds_no_movement is False
    assert result.summary.first_transaction_date == movements[-1].operation_date
    assert result.summary.last_transaction_date == movements[0].operation_date

    stored = list(session.query(Transaction).order_by(Transaction.operation_date).all())
    assert len(stored) == len(movements)
    import_row = session.get(Import, result.import_id)
    assert import_row is not None
    assert import_row.rows_read == len(movements)
    assert import_row.new_count == len(movements)
    assert import_row.continuity_ok is True


def test_reimport_creates_zero_duplicates(
    session, account, default_options, tmp_path
) -> None:
    """Re-importing the same file adds nothing."""
    path = tmp_path / "statement.xls"
    write_statement(default_options, path)
    content = path.read_bytes()
    importer = OpenbankImporter()

    first = import_statement(session, importer, account, "statement.xls", content)
    second = import_statement(session, importer, account, "statement.xls", content)

    assert first.summary.new_count == default_options.movement_count
    assert second.summary.new_count == 0
    assert second.summary.duplicate_count == default_options.movement_count
    assert second.summary.adds_no_movement is True
    assert session.query(Transaction).count() == default_options.movement_count


def test_overlap_reimport_adds_only_new_rows(
    session, account, default_options, tmp_path
) -> None:
    """Re-importing the newest half of a loaded file adds nothing new."""
    path = tmp_path / "full.xls"
    write_statement(default_options, path)
    import_statement(
        session, OpenbankImporter(), account, "full.xls", path.read_bytes()
    )

    movements = generate_movements(default_options)
    overlap = _write_custom_statement(
        default_options, movements[:15], tmp_path / "overlap.xls"
    )
    result = import_statement(
        session, OpenbankImporter(), account, "overlap.xls", overlap.read_bytes()
    )

    assert result.summary.new_count == 0
    assert result.summary.duplicate_count == len(movements[:15])


def test_repeated_identical_rows_are_all_imported(
    session, account, default_options, tmp_path
) -> None:
    """Two identical rows inside one file become two transactions."""
    movements = generate_movements(replace(default_options, movement_count=3))
    duplicated = [movements[0], replace(movements[0]), *movements[1:]]
    path = _write_custom_statement(default_options, duplicated, tmp_path / "dup.xls")

    result = import_statement(
        session, OpenbankImporter(), account, "dup.xls", path.read_bytes()
    )

    assert result.summary.new_count == len(duplicated)


def test_text_difference_is_duplicate_with_warning(
    session, account, default_options, tmp_path
) -> None:
    """A row whose description changed but date/amount/balance match is skipped."""
    path = tmp_path / "first.xls"
    write_statement(default_options, path)
    import_statement(
        session, OpenbankImporter(), account, "first.xls", path.read_bytes()
    )

    movements = generate_movements(default_options)
    changed = [replace(movements[0], description="Z" + movements[0].description[1:])]
    changed += movements[1:]
    second_path = _write_custom_statement(
        default_options, changed, tmp_path / "second.xls"
    )

    result = import_statement(
        session, OpenbankImporter(), account, "second.xls", second_path.read_bytes()
    )

    assert result.summary.new_count == 0
    assert result.summary.duplicate_count == len(movements)
    assert any("description" in warning for warning in result.summary.warnings)
    assert session.query(Transaction).count() == len(movements)
    (near_duplicate,) = result.summary.near_duplicates
    assert near_duplicate.stored_description == movements[0].description.rstrip()
    assert near_duplicate.incoming_description == changed[0].description.rstrip()


def test_near_duplicate_summary_holds_only_masked_text(
    session, account, default_options, tmp_path
) -> None:
    """The reported text difference never carries a card number or account code."""
    card = _fake_card_number()
    grouped = f"{card[:4]} {card[4:8]} {card[8:12]} {card[12:]}"
    day = date(2024, 3, 1)
    first = _write_custom_statement(
        default_options,
        [_movement(day, f"Card purchase Shop A {card}", -1_000, 9_000)],
        tmp_path / "first.xls",
    )
    import_statement(
        session, OpenbankImporter(), account, "first.xls", first.read_bytes()
    )
    second = _write_custom_statement(
        default_options,
        [_movement(day, f"Card purchase Shop B {grouped} 2024", -1_000, 9_000)],
        tmp_path / "second.xls",
    )

    result = import_statement(
        session, OpenbankImporter(), account, "second.xls", second.read_bytes()
    )

    (near_duplicate,) = result.summary.near_duplicates
    assert near_duplicate.stored_description == f"Card purchase Shop A {card[-4:]}"
    assert near_duplicate.incoming_description == "Card purchase Shop B 2024"
    reported = (
        near_duplicate.stored_description,
        near_duplicate.incoming_description,
        *result.summary.warnings,
    )
    for text in reported:
        digits = "".join(char for char in text if char.isdigit())
        assert card not in digits
        assert _ACCOUNT_CODE not in digits


def test_same_file_rows_are_never_near_duplicates(
    session, account, default_options, tmp_path
) -> None:
    """Rows of one file that differ only in text are all imported on first load."""
    day = date(2024, 3, 1)
    movements = [
        _movement(day, "Card purchase Shop B", -1_000, 9_000),
        _movement(day, "Refund Shop A", 1_000, 10_000),
        _movement(day, "Card purchase Shop A", -1_000, 9_000),
    ]
    path = _write_custom_statement(default_options, movements, tmp_path / "day.xls")

    result = import_statement(
        session, OpenbankImporter(), account, "day.xls", path.read_bytes()
    )

    assert result.summary.new_count == len(movements)
    assert result.summary.duplicate_count == 0
    assert result.summary.near_duplicates == ()
    assert session.query(Transaction).count() == len(movements)


def test_imported_rows_are_unclassified_and_pending(
    session, account, default_options, tmp_path
) -> None:
    """An imported movement has no category and waits for validation."""
    path = tmp_path / "statement.xls"
    write_statement(default_options, path)
    import_statement(
        session, OpenbankImporter(), account, "statement.xls", path.read_bytes()
    )

    stored = session.query(Transaction).all()
    assert stored
    for txn in stored:
        assert txn.category_id is None
        assert txn.status == TransactionStatus.PENDING.value


def test_gap_between_files_is_warning(
    session, account, default_options, tmp_path
) -> None:
    """A missing stretch between loaded history and a new file is warned about."""
    path_a = tmp_path / "a.xls"
    write_statement(default_options, path_a)
    import_statement(session, OpenbankImporter(), account, "a.xls", path_a.read_bytes())

    movements_a = generate_movements(default_options)
    final_balance = movements_a[0].balance_minor
    newest_date = movements_a[0].operation_date

    options_b = replace(
        default_options,
        start_date=newest_date + timedelta(days=5),
        opening_balance_minor=final_balance + 12_345,
        seed=99,
    )
    path_b = tmp_path / "b.xls"
    write_statement(options_b, path_b)
    result = import_statement(
        session, OpenbankImporter(), account, "b.xls", path_b.read_bytes()
    )

    assert any("gap" in warning for warning in result.summary.warnings)


def test_same_day_movements_do_not_fake_a_gap(
    session, account, default_options, tmp_path
) -> None:
    """Several movements on one day are not mistaken for a gap in history."""
    first = _write_custom_statement(
        default_options,
        [
            _movement(date(2024, 3, 1), "Payment Fictional B", -5_000, 100_000),
            _movement(date(2024, 3, 1), "Transfer Fictional A", 5_000, 105_000),
            _movement(date(2024, 3, 1), "Payment Fictional C", -5_000, 100_000),
        ][::-1],
        tmp_path / "first.xls",
    )
    import_statement(
        session, OpenbankImporter(), account, "first.xls", first.read_bytes()
    )
    second = _write_custom_statement(
        default_options,
        [
            _movement(date(2024, 3, 2), "Payment Fictional E", -5_000, 105_000),
            _movement(date(2024, 3, 2), "Transfer Fictional D", 10_000, 110_000),
        ],
        tmp_path / "second.xls",
    )

    result = import_statement(
        session, OpenbankImporter(), account, "second.xls", second.read_bytes()
    )

    assert not any("gap" in warning for warning in result.summary.warnings)
    import_row = session.get(Import, result.import_id)
    assert import_row is not None
    assert import_row.continuity_ok is True


def test_incoherent_balance_is_warning(
    session, account, default_options, tmp_path
) -> None:
    """A running balance that does not add up produces a warning, not a block."""
    movements = generate_movements(default_options)
    corrupted = [replace(movements[0], balance_minor=movements[0].balance_minor + 100)]
    corrupted += movements[1:]
    path = _write_custom_statement(default_options, corrupted, tmp_path / "bad.xls")

    result = import_statement(
        session, OpenbankImporter(), account, "bad.xls", path.read_bytes()
    )

    assert any("incoherent" in warning for warning in result.summary.warnings)
    assert result.summary.new_count == len(corrupted)


def test_truncated_descriptions_are_counted(
    session, account, default_options, tmp_path
) -> None:
    """Descriptions that reach 100 characters are flagged in the summary."""
    options = replace(default_options, truncate_descriptions=True)
    path = tmp_path / "truncated.xls"
    write_statement(options, path)
    result = import_statement(
        session, OpenbankImporter(), account, "truncated.xls", path.read_bytes()
    )

    assert 0 < result.summary.truncated_count <= options.movement_count


def test_truncated_description_with_card_is_counted(default_options, tmp_path) -> None:
    """A 100-character description is flagged even after its card is masked."""
    card = _fake_card_number()
    description = f"Card purchase {card} ".ljust(100, "X")
    path = _write_custom_statement(
        default_options,
        [_movement(date(2024, 3, 1), description, -1_000, 9_000)],
        tmp_path / "card.xls",
    )

    (txn,) = OpenbankImporter().parse(path.read_bytes()).transactions

    assert txn.truncated is True
    assert card not in txn.original_description


def test_no_full_card_or_account_code_is_stored(
    session, account, default_options, tmp_path
) -> None:
    """Masking keeps full card and account codes out of rows, summary and errors."""
    card = _fake_card_number()
    movements = generate_movements(default_options)
    movements[0] = replace(movements[0], description=f"Payment Fictional card {card}")
    path = _write_custom_statement(default_options, movements, tmp_path / "mask.xls")

    result = import_statement(
        session, OpenbankImporter(), account, "mask.xls", path.read_bytes()
    )

    assert result.account_hint == _ACCOUNT_CODE[-4:]
    for txn in session.query(Transaction).all():
        fields = (
            txn.original_description,
            txn.normalized_description,
            txn.counterparty or "",
        )
        for field in fields:
            assert card not in field
            assert _ACCOUNT_CODE not in field
        assert txn.identity_key != card
    for warning in result.summary.warnings:
        assert card not in warning
        assert _ACCOUNT_CODE not in warning


def test_zero_movements_adds_nothing(
    session, account, default_options, tmp_path
) -> None:
    """A valid but empty statement raises the no-new-movement alert."""
    options = replace(default_options, movement_count=0)
    path = tmp_path / "empty.xls"
    write_statement(options, path)
    result = import_statement(
        session, OpenbankImporter(), account, "empty.xls", path.read_bytes()
    )

    assert result.summary.rows_read == 0
    assert result.summary.new_count == 0
    assert result.summary.adds_no_movement is True
    assert result.summary.first_transaction_date is None
    assert result.summary.last_transaction_date is None


# --- Revert tests ------------------------------------------------------------


def test_revert_removes_import_and_transactions(
    session, account, default_options, tmp_path
) -> None:
    """Reverting an import deletes its transactions and the import record."""
    path = tmp_path / "statement.xls"
    write_statement(default_options, path)
    result = import_statement(
        session, OpenbankImporter(), account, "statement.xls", path.read_bytes()
    )

    revert_import(session, result.import_id)

    assert session.query(Transaction).count() == 0
    assert session.get(Import, result.import_id) is None


def test_revert_refused_when_a_transaction_is_validated(
    session, account, default_options, tmp_path
) -> None:
    """A validated transaction blocks whole-import revert."""
    path = tmp_path / "statement.xls"
    write_statement(default_options, path)
    result = import_statement(
        session, OpenbankImporter(), account, "statement.xls", path.read_bytes()
    )
    txn = session.query(Transaction).first()
    assert txn is not None
    txn.status = TransactionStatus.VALIDATED.value
    session.commit()

    with pytest.raises(ImportRevertError):
        revert_import(session, result.import_id)


def test_revert_missing_import_raises(session) -> None:
    """Reverting a non-existent import raises a clear error."""
    with pytest.raises(ImportRevertError):
        revert_import(session, uuid4())
