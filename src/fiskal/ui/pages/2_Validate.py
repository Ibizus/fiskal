"""Validate page: review the pending queue and confirm internal-transfer pairs."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from fiskal.classification import (
    ValidationError,
    confirm_pair,
    create_rule_from_correction,
    find_proposals_for_transaction_ids,
    list_pending_transactions,
    reject_pair,
    suggest_from_history,
    validate_transaction,
)
from fiskal.db.models import Category, CostCenter, Person, Transaction
from fiskal.domain.enums import TransactionType
from fiskal.domain.transfer_pairing import PairingError
from fiskal.i18n import translate
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
    transaction_type_label,
)
from fiskal.ui.session import open_session

_TYPE_OPTIONS = [
    TransactionType.EXPENSE.value,
    TransactionType.INCOME.value,
    TransactionType.SAVINGS_INVESTMENT.value,
    TransactionType.REFUND.value,
]


def _default_type_value(txn: Transaction) -> str:
    if txn.amount_minor >= 0:
        return TransactionType.INCOME.value
    return TransactionType.EXPENSE.value


def _type_index(txn: Transaction) -> int:
    value = txn.transaction_type
    if value not in _TYPE_OPTIONS:
        value = _default_type_value(txn)
    return _TYPE_OPTIONS.index(value)


def _index_of_id(ids: list[object], value: object | None) -> int:
    if value is None:
        return 0
    try:
        return ids.index(value)
    except ValueError:
        return 0


def _none_first_index(ids: list[object], value: object | None) -> int:
    if value is None:
        return 0
    try:
        return ids.index(value) + 1
    except ValueError:
        return 0


def _cost_center_option_label(cost_center: CostCenter | None) -> str:
    if cost_center is None:
        return translate("common.none")
    return cost_center_label(cost_center)


def _person_option_label(person: Person | None) -> str:
    if person is None:
        return translate("person.unattributed")
    return person_label(person.name)


def _render_transaction(
    session: Session,
    txn: Transaction,
    categories: list[Category],
    cost_centers: list[CostCenter],
    persons: list[Person],
    account_names: dict[object, str],
) -> None:
    categories_by_id = {category.id: category for category in categories}
    category_ids = [category.id for category in categories]
    cost_center_ids = [cost_center.id for cost_center in cost_centers]
    person_ids = [person.id for person in persons]

    suggestion = suggest_from_history(session, txn.id)
    category_id = txn.category_id
    cost_center_id = txn.cost_center_id
    if suggestion is not None and category_id is None:
        category_id = suggestion.preselected.category_id
        if cost_center_id is None:
            cost_center_id = suggestion.preselected.cost_center_id

    with st.form(f"validate_{txn.id}"):
        caption = (
            f"{account_names.get(txn.account_id, '')} · "
            f"{txn.operation_date.isoformat()} · "
            f"{format_minor(txn.amount_minor, txn.currency_code)} — "
            f"{txn.original_description}"
        )
        st.caption(caption)
        if suggestion is not None:
            suggested = categories_by_id.get(suggestion.preselected.category_id)
            if suggested is not None:
                st.caption(
                    f"{translate('validate.history')}: {category_label(suggested)} "
                    f"({suggestion.preselected.count})"
                )

        chosen_type = st.selectbox(
            translate("validate.type"),
            _TYPE_OPTIONS,
            index=_type_index(txn),
            format_func=transaction_type_label,
            key=f"validate_{txn.id}_type",
        )
        chosen_category = st.selectbox(
            translate("validate.category"),
            categories,
            index=_index_of_id(category_ids, category_id),
            format_func=category_label,
            key=f"validate_{txn.id}_category",
        )
        chosen_cost_center = st.selectbox(
            translate("validate.cost_center"),
            [None, *cost_centers],
            index=_none_first_index(cost_center_ids, cost_center_id),
            format_func=_cost_center_option_label,
            key=f"validate_{txn.id}_cost_center",
        )
        chosen_person = st.selectbox(
            translate("validate.person"),
            [None, *persons],
            index=_none_first_index(person_ids, txn.person_id),
            format_func=_person_option_label,
            key=f"validate_{txn.id}_person",
        )

        create_rule = st.checkbox(
            translate("validate.create_rule"),
            key=f"validate_{txn.id}_create_rule",
        )
        pattern = ""
        if create_rule:
            pattern = st.text_input(
                translate("validate.rule_pattern"),
                value=(txn.counterparty or txn.normalized_description).strip(),
                key=f"validate_{txn.id}_pattern",
            )

        confirm_col, save_col = st.columns(2)
        confirmed = confirm_col.form_submit_button(
            translate("validate.confirm"),
            key=f"validate_{txn.id}_confirm",
        )
        saved = save_col.form_submit_button(
            translate("validate.save"),
            key=f"validate_{txn.id}_save",
        )

    if not (confirmed or saved):
        return

    cost_center_id = chosen_cost_center.id if chosen_cost_center is not None else None
    person_id = chosen_person.id if chosen_person is not None else None
    if create_rule:
        create_rule_from_correction(
            session,
            txn.id,
            transaction_type=chosen_type,
            category_id=chosen_category.id,
            cost_center_id=cost_center_id,
            person_id=person_id,
            pattern=pattern or None,
        )
    try:
        validate_transaction(
            session,
            txn.id,
            transaction_type=chosen_type,
            category_id=chosen_category.id,
            cost_center_id=cost_center_id,
            person_id=person_id,
        )
    except ValidationError as exc:
        st.error(str(exc))
        return
    st.success(translate("validate.confirm"))
    st.rerun()


def _render_pair_proposals(session: Session) -> None:
    pending = list_pending_transactions(session)
    proposals = find_proposals_for_transaction_ids(session, [txn.id for txn in pending])
    if not proposals:
        st.caption(translate("validate.pair_none"))
        return

    st.subheader(translate("validate.pair_proposals"))
    account_names = {
        account.id: account.name
        for account in list_accounts(session, active_only=False)
    }
    for proposal in proposals:
        source = session.get(Transaction, proposal.source_id)
        candidate = session.get(Transaction, proposal.candidate_id)
        if source is None or candidate is None:
            continue
        st.write(
            f"{account_names.get(source.account_id, '')} "
            f"{format_minor(source.amount_minor, source.currency_code)} → "
            f"{account_names.get(candidate.account_id, '')} "
            f"{format_minor(candidate.amount_minor, candidate.currency_code)}"
        )
        confirm_col, reject_col = st.columns(2)
        if confirm_col.button(
            translate("validate.confirm_pair"),
            key=f"pair_confirm_{source.id}_{candidate.id}",
        ):
            try:
                confirm_pair(session, source.id, candidate.id)
            except PairingError as exc:
                st.error(str(exc))
                continue
            st.rerun()
        if reject_col.button(
            translate("validate.reject_pair"),
            key=f"pair_reject_{source.id}_{candidate.id}",
        ):
            try:
                reject_pair(session, source.id, candidate.id)
            except PairingError as exc:
                st.error(str(exc))
                continue
            st.rerun()


def _render_queue(session: Session) -> None:
    pending = list_pending_transactions(session)
    if not pending:
        st.info(translate("validate.empty"))
        return

    categories = list_categories(session)
    cost_centers = list_cost_centers(session)
    persons = list_persons(session)
    account_names = {
        account.id: account.name
        for account in list_accounts(session, active_only=False)
    }
    for txn in pending:
        _render_transaction(
            session, txn, categories, cost_centers, persons, account_names
        )


def main() -> None:
    """Render the validate page."""
    st.set_page_config(page_title=translate("nav.validate"), page_icon="✅")
    st.header(translate("nav.validate"))
    with open_session() as session:
        _render_queue(session)
        _render_pair_proposals(session)


if __name__ == "__main__":
    main()
