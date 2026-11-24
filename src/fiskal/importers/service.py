"""Import service: persist parsed statements and support whole-import revert.

The parser modules stay framework-free; this module is the only importer code
that touches the database. It owns idempotency (identity and near-duplicate
checks), balance-continuity warnings, summary construction and whole-import
revert. Placed here because the specification assigns these responsibilities
to the import feature.
"""

from __future__ import annotations

import hashlib
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from fiskal.classification.pairing_service import (
    find_proposals_for_transaction_ids,
)
from fiskal.db.models import Account, Import, Transaction
from fiskal.domain.enums import (
    ClassificationOrigin,
    TransactionStatus,
    TransactionType,
)
from fiskal.importers.base import (
    ImportedTransaction,
    Importer,
    ImportResult,
    ImportRevertError,
    ImportSummary,
    NearDuplicate,
    build_match_key,
    field_values,
)

# Imported movements are unclassified, so origin only holds a placeholder. It
# means something once category_id is set; classification keys off category_id.
_ORIGIN_PLACEHOLDER = ClassificationOrigin.MANUAL.value


def _latest_day_balances(
    transactions: list[Transaction],
) -> tuple[date, set[int]] | None:
    """Return the latest loaded operation date and the balances reached on it.

    Several movements can share a date and their stored order is not kept, so
    every balance reached on that date is a candidate closing balance.
    """
    if not transactions:
        return None
    latest = max(txn.operation_date for txn in transactions)
    balances = {
        txn.balance_minor
        for txn in transactions
        if txn.operation_date == latest and txn.balance_minor is not None
    }
    return latest, balances


def _to_transaction(
    account: Account, import_row: Import, txn: ImportedTransaction
) -> Transaction:
    """Map a neutral imported transaction onto the persisted model."""
    return Transaction(
        account_id=account.id,
        import_id=import_row.id,
        operation_date=txn.operation_date,
        value_date=txn.value_date,
        original_description=txn.original_description,
        normalized_description=txn.normalized_description,
        counterparty=txn.counterparty,
        amount_minor=txn.amount_minor,
        balance_minor=txn.balance_minor,
        currency_code=account.currency_code,
        transaction_type=(
            TransactionType.INCOME.value
            if txn.amount_minor >= 0
            else TransactionType.EXPENSE.value
        ),
        origin=_ORIGIN_PLACEHOLDER,
        status=TransactionStatus.PENDING.value,
        identity_key=txn.identity_key,
    )


def import_statement(
    session: Session,
    importer: Importer,
    account: Account,
    file_name: str,
    content: bytes,
) -> ImportResult:
    """Parse ``content`` and persist its new movements for ``account``.

    Returns the persisted import id and the summary. Re-importing the same or
    an overlapping file creates zero duplicates: a movement whose identity
    matches exactly is skipped silently, and a movement whose date, amount and
    balance match but whose description differs is skipped with a warning.
    """
    parsed = importer.parse(content)
    transactions = parsed.transactions
    file_hash = hashlib.sha256(content).hexdigest()

    existing = list(
        session.execute(
            select(Transaction).where(Transaction.account_id == account.id)
        ).scalars()
    )
    existing_identity_keys = {existing_txn.identity_key for existing_txn in existing}
    # A near-duplicate shares date, amount and balance with an already-loaded
    # movement but differs only in its description text. Only loaded movements
    # feed this map: rows of the same file never make each other duplicates.
    existing_match_descriptions: dict[str, list[str]] = {}
    for existing_txn in existing:
        match_key = build_match_key(field_values(existing_txn, importer.match_fields))
        existing_match_descriptions.setdefault(match_key, []).append(
            existing_txn.original_description
        )

    new_transactions: list[ImportedTransaction] = []
    duplicate_count = 0
    warnings: list[str] = []
    near_duplicates: list[NearDuplicate] = []

    for txn in transactions:
        if txn.identity_key in existing_identity_keys:
            duplicate_count += 1
            continue
        match_key = build_match_key(field_values(txn, importer.match_fields))
        descriptions = existing_match_descriptions.get(match_key)
        if descriptions is not None and txn.original_description not in descriptions:
            duplicate_count += 1
            warnings.append(
                "a movement matches an already-loaded movement except for its "
                "description text; it was not added again"
            )
            near_duplicates.append(
                NearDuplicate(
                    stored_description=descriptions[0],
                    incoming_description=txn.original_description,
                )
            )
            continue
        new_transactions.append(txn)
        existing_identity_keys.add(txn.identity_key)

    continuity_ok = _check_continuity(existing, transactions, warnings)

    first_transaction_date = (
        min(txn.operation_date for txn in transactions) if transactions else None
    )
    last_transaction_date = (
        max(txn.operation_date for txn in transactions) if transactions else None
    )

    import_row = Import(
        account_id=account.id,
        file_name=file_name,
        file_hash=file_hash,
        rows_read=len(transactions),
        new_count=len(new_transactions),
        duplicate_count=duplicate_count,
        first_transaction_date=first_transaction_date,
        last_transaction_date=last_transaction_date,
        continuity_ok=continuity_ok,
    )
    session.add(import_row)
    session.flush()

    orm_transactions = [
        _to_transaction(account, import_row, new_txn) for new_txn in new_transactions
    ]
    for orm_txn in orm_transactions:
        session.add(orm_txn)
    session.commit()

    new_transaction_ids = [orm_txn.id for orm_txn in orm_transactions]
    pair_proposals = find_proposals_for_transaction_ids(session, new_transaction_ids)

    summary = ImportSummary(
        first_transaction_date=first_transaction_date,
        last_transaction_date=last_transaction_date,
        rows_read=len(transactions),
        new_count=len(new_transactions),
        duplicate_count=duplicate_count,
        truncated_count=sum(1 for txn in transactions if txn.truncated),
        warnings=tuple(warnings),
        near_duplicates=tuple(near_duplicates),
        proposed_pair_count=len(pair_proposals),
    )
    return ImportResult(
        import_id=import_row.id,
        summary=summary,
        account_hint=parsed.account_hint,
        pair_proposals=tuple(pair_proposals),
    )


def _check_continuity(
    existing: list[Transaction],
    transactions: tuple[ImportedTransaction, ...],
    warnings: list[str],
) -> bool:
    """Check running balances and loaded-history continuity; return True if OK.

    Both checks only add warnings and never block the import.
    """
    continuity_ok = True

    # Incoherent running balance inside the file (rows are newest first).
    for newer, older in zip(transactions, transactions[1:], strict=False):
        if older.balance_minor + newer.amount_minor != newer.balance_minor:
            continuity_ok = False
            warnings.append("incoherent running balance detected inside the file")
            break

    # Gap between the newest loaded movement and the oldest movement in the
    # new file. Only checked when the file extends beyond loaded history.
    latest_loaded = _latest_day_balances(existing)
    if latest_loaded is not None and transactions:
        latest_date, closing_balances = latest_loaded
        oldest_new = transactions[-1]
        if (
            oldest_new.operation_date > latest_date
            and closing_balances
            and oldest_new.balance_minor - oldest_new.amount_minor
            not in closing_balances
        ):
            continuity_ok = False
            warnings.append(
                "gap detected between the loaded history and the movements in this file"
            )

    return continuity_ok


def revert_import(session: Session, import_id: UUID) -> None:
    """Delete an import and all its transactions, when allowed.

    An import can only be reverted while none of its transactions has been
    validated. The closed-month guard will be added with the month-close
    feature; there is no month-close table yet.
    """
    import_row = session.get(Import, import_id)
    if import_row is None:
        raise ImportRevertError("import not found")

    transactions = list(
        session.execute(
            select(Transaction).where(Transaction.import_id == import_id)
        ).scalars()
    )
    for txn in transactions:
        if txn.status == TransactionStatus.VALIDATED.value:
            raise ImportRevertError(
                "cannot revert an import that contains validated transactions"
            )

    for txn in transactions:
        session.delete(txn)
    session.flush()
    session.delete(import_row)
    session.commit()
