"""Transactions page: filter and list transactions."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from fiskal.classification import ValidationError, return_to_queue
from fiskal.db.models import Category, CostCenter, Transaction
from fiskal.domain.enums import CategoryNature, TransactionStatus, TransactionType
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
    description_text,
    format_minor,
    person_label,
    status_label,
    transaction_type_label,
)
from fiskal.ui.language import apply_profile_language
from fiskal.ui.messages import flash, show_flashes
from fiskal.ui.session import open_session

_TYPE_OPTIONS = [
    None,
    TransactionType.EXPENSE.value,
    TransactionType.INCOME.value,
    TransactionType.SAVINGS_INVESTMENT.value,
    TransactionType.INTERNAL_TRANSFER.value,
    TransactionType.REFUND.value,
]
# ``VALIDATED_BY_RULE`` is a filter value only: those rows have status validated
# and carry the ``validated_by_rule`` marker.
VALIDATED_BY_RULE = "validated_by_rule"
_STATUS_OPTIONS = [
    None,
    TransactionStatus.PENDING.value,
    TransactionStatus.VALIDATED.value,
    VALIDATED_BY_RULE,
]
_NATURE_OPTIONS = [None, CategoryNature.FIXED.value, CategoryNature.VARIABLE.value]
LIST_LIMIT = 500


def _type_option_label(value: str | None) -> str:
    if value is None:
        return translate("transactions.all_types")
    return transaction_type_label(value)


def _status_option_label(value: str | None) -> str:
    if value is None:
        return translate("transactions.all_statuses")
    if value == VALIDATED_BY_RULE:
        return translate("status.validated_by_rule")
    return status_label(value)


def _nature_option_label(value: str | None) -> str:
    if value is None:
        return translate("transactions.all_natures")
    return translate(f"transactions.nature.{value}")


def _render_catalog_filters(
    session: Session,
) -> tuple[Category | None, CostCenter | None, str | None]:
    """Draw the category, cost center and fixed/variable filters."""
    category = st.selectbox(
        translate("transactions.category"),
        [None, *list_categories(session, active_only=False)],
        format_func=lambda value: (
            translate("transactions.all_categories")
            if value is None
            else category_label(value)
        ),
        key="transactions_category",
    )
    cost_center = st.selectbox(
        translate("transactions.cost_center"),
        [None, *list_cost_centers(session, active_only=False)],
        format_func=lambda value: (
            translate("transactions.all_cost_centers")
            if value is None
            else cost_center_label(value)
        ),
        key="transactions_cost_center",
    )
    for chosen in (category, cost_center):
        shown = description_text(chosen)
        if shown:
            st.caption(shown)
    nature = st.selectbox(
        translate("transactions.nature"),
        _NATURE_OPTIONS,
        format_func=_nature_option_label,
        key="transactions_nature",
    )
    return category, cost_center, nature


def _row_status_label(txn: Transaction) -> str:
    if txn.validated_by_rule:
        return translate("status.validated_by_rule")
    return status_label(txn.status)


def _render_send_back(session: Session, transactions: list[Transaction]) -> None:
    marked = [txn for txn in transactions if txn.validated_by_rule]
    if not marked:
        return
    chosen = st.selectbox(
        translate("transactions.send_back_select"),
        marked,
        format_func=lambda txn: (
            f"{txn.operation_date.isoformat()} · "
            f"{format_minor(txn.amount_minor, txn.currency_code)} — "
            f"{txn.original_description}"
        ),
        key="transactions_send_back",
    )
    if st.button(
        translate("transactions.send_back"),
        help=translate("transactions.send_back_help"),
        key="transactions_send_back_button",
    ):
        try:
            return_to_queue(session, chosen.id)
        except ValidationError as exc:
            st.error(str(exc))
            return
        flash("success", translate("transactions.sent_back"))
        st.rerun()


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
    category, cost_center, nature = _render_catalog_filters(session)
    query = st.text_input(translate("transactions.search"), key="transactions_query")

    by_rule = chosen_status == VALIDATED_BY_RULE
    filters = TransactionFilter(
        account_id=account.id if account is not None else None,
        transaction_type=chosen_type,
        status=TransactionStatus.VALIDATED.value if by_rule else chosen_status,
        validated_by_rule=True if by_rule else None,
        query=query or None,
        category_id=category.id if category is not None else None,
        cost_center_id=cost_center.id if cost_center is not None else None,
        nature=nature,
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
                translate("transactions.status"): _row_status_label(txn),
                translate("transactions.amount"): format_minor(
                    txn.amount_minor, txn.currency_code
                ),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)
    if by_rule:
        _render_send_back(session, transactions)


def main() -> None:
    """Render the transactions page."""
    apply_profile_language()
    st.set_page_config(page_title=translate("nav.transactions"), page_icon="🧾")
    st.header(translate("nav.transactions"))
    show_flashes()
    with open_session() as session:
        _render_table(session)


if __name__ == "__main__":
    main()
