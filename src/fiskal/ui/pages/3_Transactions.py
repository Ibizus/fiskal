"""Transactions page: filter and list transactions."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from fiskal.domain.enums import TransactionStatus, TransactionType
from fiskal.i18n import translate
from fiskal.reports.helpers import TransactionFilter, list_transactions
from fiskal.services import (
    list_accounts,
    list_categories,
    list_cost_centers,
    list_persons,
)
from fiskal.ui.labels import (
    category_label,
    cost_center_label,
    format_minor,
    person_label,
    status_label,
    transaction_type_label,
)
from fiskal.ui.session import open_session

_TYPE_OPTIONS = [
    None,
    TransactionType.EXPENSE.value,
    TransactionType.INCOME.value,
    TransactionType.SAVINGS_INVESTMENT.value,
    TransactionType.INTERNAL_TRANSFER.value,
    TransactionType.REFUND.value,
]
_STATUS_OPTIONS = [
    None,
    TransactionStatus.PENDING.value,
    TransactionStatus.VALIDATED.value,
]
LIST_LIMIT = 500


def _type_option_label(value: str | None) -> str:
    if value is None:
        return translate("transactions.all_types")
    return transaction_type_label(value)


def _status_option_label(value: str | None) -> str:
    if value is None:
        return translate("transactions.all_statuses")
    return status_label(value)


def _render_table(session: Session) -> None:
    accounts = list_accounts(session, active_only=False)
    if not accounts:
        st.info(translate("transactions.empty"))
        return

    account = st.selectbox(
        translate("transactions.account"),
        [None, *accounts],
        format_func=lambda value: (
            translate("transactions.all_accounts") if value is None else value.name
        ),
        key="transactions_account",
    )
    chosen_type = st.selectbox(
        translate("transactions.type"),
        _TYPE_OPTIONS,
        format_func=_type_option_label,
        key="transactions_type",
    )
    chosen_status = st.selectbox(
        translate("transactions.status"),
        _STATUS_OPTIONS,
        format_func=_status_option_label,
        key="transactions_status",
    )
    query = st.text_input(translate("transactions.search"), key="transactions_query")

    filters = TransactionFilter(
        account_id=account.id if account is not None else None,
        transaction_type=chosen_type,
        status=chosen_status,
        query=query or None,
    )
    transactions = list_transactions(session, filters, limit=LIST_LIMIT)
    if not transactions:
        st.info(translate("transactions.empty"))
        return
    if len(transactions) == LIST_LIMIT:
        st.caption(translate("transactions.limit_reached").format(limit=LIST_LIMIT))

    account_names = {
        item.id: item.name for item in list_accounts(session, active_only=False)
    }
    categories_by_id = {
        item.id: item for item in list_categories(session, active_only=False)
    }
    cost_centers_by_id = {
        item.id: item for item in list_cost_centers(session, active_only=False)
    }
    persons_by_id = {item.id: item.name for item in list_persons(session)}

    rows = []
    for txn in transactions:
        rows.append(
            {
                translate("transactions.date"): txn.operation_date.isoformat(),
                translate("transactions.description"): txn.original_description,
                translate("transactions.account"): account_names.get(
                    txn.account_id, ""
                ),
                translate("transactions.type"): transaction_type_label(
                    txn.transaction_type
                ),
                translate("transactions.category"): category_label(
                    categories_by_id.get(txn.category_id)
                ),
                translate("transactions.cost_center"): cost_center_label(
                    cost_centers_by_id.get(txn.cost_center_id)
                ),
                translate("transactions.person"): person_label(
                    persons_by_id.get(txn.person_id)
                ),
                translate("transactions.status"): status_label(txn.status),
                translate("transactions.amount"): format_minor(
                    txn.amount_minor, txn.currency_code
                ),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)


def main() -> None:
    """Render the transactions page."""
    st.set_page_config(page_title=translate("nav.transactions"), page_icon="🧾")
    st.header(translate("nav.transactions"))
    with open_session() as session:
        _render_table(session)


if __name__ == "__main__":
    main()
