"""Validate page: review the pending queue and confirm internal-transfer pairs."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from fiskal.classification import (
    ValidationError,
    confirm_pair,
    find_proposals_for_transaction_ids,
    has_owner_cost_center,
    list_pending_transactions,
    reject_pair,
    suggest_from_history,
    validate_and_create_rule,
    validate_transaction,
)
from fiskal.db.models import Category, CostCenter, Person, Transaction
from fiskal.domain.enums import TransactionType
from fiskal.domain.rule_matching import RuleValidationError
from fiskal.domain.transfer_pairing import PairingError, PairProposal
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
from fiskal.ui.language import apply_profile_language
from fiskal.ui.messages import flash, show_flashes
from fiskal.ui.navigation import show_settings_link
from fiskal.ui.session import open_session

_TYPE_OPTIONS = [
    TransactionType.EXPENSE.value,
    TransactionType.INCOME.value,
    TransactionType.SAVINGS_INVESTMENT.value,
    TransactionType.REFUND.value,
]
QUEUE_PAGE_SIZE = 25


def _default_type_value(txn: Transaction) -> str:
    if txn.amount_minor >= 0:
        return TransactionType.INCOME.value
    return TransactionType.EXPENSE.value


def _type_index(txn: Transaction, value: str) -> int:
    if value not in _TYPE_OPTIONS:
        value = _default_type_value(txn)
    return _TYPE_OPTIONS.index(value)


def _none_first_index(ids: list[object], value: object | None) -> int:
    if value is None:
        return 0
    try:
        return ids.index(value) + 1
    except ValueError:
        return 0


def _category_option_label(category: Category | None) -> str:
    if category is None:
        return translate("validate.choose_category")
    return category_label(category)


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
    type_value = txn.transaction_type
    if suggestion is not None and category_id is None:
        type_value = suggestion.preselected.transaction_type.value
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
            index=_type_index(txn, type_value),
            format_func=transaction_type_label,
            key=f"validate_{txn.id}_type",
        )
        chosen_category = st.selectbox(
            translate("validate.category"),
            [None, *categories],
            index=_none_first_index(category_ids, category_id),
            format_func=_category_option_label,
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
            help=translate("validate.create_rule_help"),
            key=f"validate_{txn.id}_create_rule",
        )
        pattern = st.text_input(
            translate("validate.rule_pattern"),
            help=translate("validate.rule_pattern_help"),
            value=(txn.counterparty or txn.normalized_description).strip(),
            key=f"validate_{txn.id}_pattern",
        )

        # Confirming the proposal as shown and saving a correction are the same
        # action: the movement is validated with whatever the fields hold.
        submitted = st.form_submit_button(
            translate("validate.submit"),
            help=translate("validate.submit_help"),
            key=f"validate_{txn.id}_submit",
        )

    if not submitted:
        return

    if chosen_category is None:
        st.error(translate("validate.category_required"))
        return
    cost_center_id = chosen_cost_center.id if chosen_cost_center is not None else None
    person_id = chosen_person.id if chosen_person is not None else None
    try:
        if create_rule:
            outcome = validate_and_create_rule(
                session,
                txn.id,
                transaction_type=chosen_type,
                category_id=chosen_category.id,
                cost_center_id=cost_center_id,
                person_id=person_id,
                pattern=pattern or None,
            )
            flash("success", translate("validate.validated"))
            flash(
                "success",
                translate("validate.rule_created").format(
                    count=outcome.validated_count
                ),
            )
        else:
            validate_transaction(
                session,
                txn.id,
                transaction_type=chosen_type,
                category_id=chosen_category.id,
                cost_center_id=cost_center_id,
                person_id=person_id,
            )
            flash("success", translate("validate.validated"))
    except (RuleValidationError, ValidationError) as exc:
        st.error(str(exc))
        return
    st.rerun()


def _render_pair_proposals(session: Session, proposals: list[PairProposal]) -> None:
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
            flash("success", translate("validate.pair_confirmed"))
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
            flash("success", translate("validate.pair_rejected"))
            st.rerun()


def _render_queue(
    session: Session, pending: list[Transaction], proposals: list[PairProposal]
) -> None:
    in_proposal = {proposal.source_id for proposal in proposals} | {
        proposal.candidate_id for proposal in proposals
    }
    queue = [txn for txn in pending if txn.id not in in_proposal]
    if not queue:
        st.info(translate("validate.empty"))
        return

    st.subheader(translate("validate.queue_title"))
    if len(queue) > QUEUE_PAGE_SIZE:
        st.caption(
            translate("validate.page_limit").format(
                shown=QUEUE_PAGE_SIZE, total=len(queue)
            )
        )

    categories = list_categories(session)
    cost_centers = list_cost_centers(session)
    persons = list_persons(session)
    account_names = {
        account.id: account.name
        for account in list_accounts(session, active_only=False)
    }
    for txn in queue[:QUEUE_PAGE_SIZE]:
        _render_transaction(
            session, txn, categories, cost_centers, persons, account_names
        )


def main() -> None:
    """Render the validate page."""
    apply_profile_language()
    st.set_page_config(page_title=translate("nav.validate"), page_icon="✅")
    st.header(translate("nav.validate"))
    show_flashes()
    with open_session() as session:
        if not has_owner_cost_center(session):
            st.info(translate("validate.no_cost_center"))
            show_settings_link()
        pending = list_pending_transactions(session)
        proposals = find_proposals_for_transaction_ids(
            session, [txn.id for txn in pending]
        )
        _render_pair_proposals(session, proposals)
        _render_queue(session, pending, proposals)


if __name__ == "__main__":
    main()
