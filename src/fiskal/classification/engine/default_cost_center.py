"""The account default cost-center layer of the classification cascade."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from fiskal.db.models import Account, Transaction


def apply_default_cost_center(session: Session, targets: list[Transaction]) -> int:
    """Fill the account default cost center when no earlier layer set one.

    Only the cost center is written, never type or category, and an existing
    cost center is never overridden. The returned count only includes rows that
    still have no category after the fill.
    """
    account_ids = {txn.account_id for txn in targets}
    accounts = list(
        session.execute(select(Account).where(Account.id.in_(account_ids))).scalars()
    )
    default_by_account = {
        account.id: account.default_cost_center_id
        for account in accounts
        if account.default_cost_center_id is not None
    }
    count = 0
    for txn in targets:
        if txn.cost_center_id is None:
            default_cost_center_id = default_by_account.get(txn.account_id)
            if default_cost_center_id is not None:
                txn.cost_center_id = default_cost_center_id
                if txn.category_id is None:
                    count += 1
    return count
