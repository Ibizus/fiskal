"""Validate page, guided view: classify a large history by concept.

The view shows pending movements grouped by normalized description, one form
per group (never per movement), paginated, plus the recently validated groups
with their undo. All grouping, counting and writing happens in the
classification services; this module only draws widgets and forwards choices.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from uuid import UUID

import streamlit as st
from sqlalchemy.orm import Session

from fiskal.classification import (
    ClassificationProgress,
    GroupClassification,
    GroupKey,
    GuidedActionSummary,
    GuidedError,
    TransactionGroup,
    UndoOutcome,
    ValidationError,
    classification_progress,
    confirm_group,
    count_guided_actions,
    list_guided_action_rows,
    list_guided_actions,
    list_pending_groups,
    summarize_groups,
    undo_guided_action,
    undo_guided_rows,
)
from fiskal.db.models import Category, CostCenter, Person, Transaction
from fiskal.domain.enums import GroupDirection
from fiskal.domain.rule_matching import RuleValidationError
from fiskal.i18n import translate
from fiskal.services import list_categories, list_cost_centers, list_persons
from fiskal.ui.labels import format_minor, transaction_type_label
from fiskal.ui.messages import flash
from fiskal.ui.validate_common import (
    TYPE_OPTIONS,
    category_option_label,
    cost_center_option_label,
    person_option_label,
)

GROUP_PAGE_SIZE = 10
UNDO_LIST_SIZE = 5
UNDO_ROWS_SIZE = 50
PAGE_STATE_KEY = "guided_page"


@dataclass(frozen=True)
class _Options:
    """The catalogs a group form chooses from."""

    categories: list[Category]
    cost_centers: list[CostCenter]
    persons: list[Person]


@dataclass(frozen=True)
class _FormValues:
    """What the owner chose in one group form."""

    transaction_type: str
    category: Category | None
    cost_center: CostCenter | None
    person: Person | None
    create_rule: bool
    pattern: str


def group_widget_key(key: GroupKey) -> str:
    """Return the stable widget key prefix of a group.

    It derives from the group's identity, not its position, so widget state
    never leaks to the next group when a confirmed one disappears.
    """
    digest = hashlib.sha1(
        f"{key.direction.value}|{key.normalized_description}".encode(),
        usedforsecurity=False,
    ).hexdigest()
    return f"guided_{digest[:12]}"


def render_progress(session: Session) -> None:
    """Show how many movements are pending and the share already classified."""
    progress: ClassificationProgress = classification_progress(session)
    if progress.pending + progress.validated == 0:
        return
    pending_col, percent_col = st.columns(2)
    pending_col.metric(translate("validate.progress.pending"), progress.pending)
    percent_col.metric(
        translate("validate.progress.classified"), f"{progress.classified_percent}%"
    )
    st.progress(progress.classified_percent / 100)


def _direction_label(direction: GroupDirection | str) -> str:
    return translate(f"guided.direction.{GroupDirection(direction).value}")


def _money(group: TransactionGroup, amount_minor: int) -> str:
    return format_minor(amount_minor, group.currency_code or "")


def _render_group_header(group: TransactionGroup) -> None:
    st.text(
        f"{group.key.normalized_description} · {_direction_label(group.key.direction)}"
    )
    values = {
        "count": group.row_count,
        "first": group.first_date.isoformat(),
        "last": group.last_date.isoformat(),
    }
    if group.currency_code is None:
        st.caption(translate("guided.group_caption_mixed").format(**values))
    else:
        st.caption(
            translate("guided.group_caption").format(
                total=_money(group, group.total_minor), **values
            )
        )
        if group.has_wide_spread:
            st.warning(
                translate("guided.spread_warning").format(
                    low=_money(group, group.min_abs_minor),
                    high=_money(group, group.max_abs_minor),
                )
            )
    st.caption(translate("guided.affected").format(count=group.row_count))


def _default_type(group: TransactionGroup) -> str:
    return (
        TYPE_OPTIONS[1]
        if group.key.direction is GroupDirection.INCOME
        else TYPE_OPTIONS[0]
    )


def _render_group_form(
    group: TransactionGroup, options: _Options
) -> _FormValues | None:
    base = group_widget_key(group.key)
    with st.form(base):
        _render_group_header(group)
        chosen_type = st.selectbox(
            translate("validate.type"),
            TYPE_OPTIONS,
            index=TYPE_OPTIONS.index(_default_type(group)),
            format_func=transaction_type_label,
            key=f"{base}_type",
        )
        chosen_category = st.selectbox(
            translate("validate.category"),
            [None, *options.categories],
            format_func=category_option_label,
            key=f"{base}_category",
        )
        chosen_cost_center = st.selectbox(
            translate("validate.cost_center"),
            [None, *options.cost_centers],
            format_func=cost_center_option_label,
            key=f"{base}_cost_center",
        )
        chosen_person = st.selectbox(
            translate("validate.person"),
            [None, *options.persons],
            format_func=person_option_label,
            key=f"{base}_person",
        )
        create_rule = st.checkbox(
            translate("guided.create_rule"),
            value=not group.has_wide_spread,
            help=translate("guided.create_rule_help"),
            key=f"{base}_create_rule",
        )
        pattern = st.text_input(
            translate("validate.rule_pattern"),
            help=translate("validate.rule_pattern_help"),
            value=group.key.normalized_description,
            key=f"{base}_pattern",
        )
        submitted = st.form_submit_button(
            translate("guided.submit"),
            help=translate("guided.submit_help"),
            key=f"{base}_submit",
        )
    if not submitted:
        return None
    return _FormValues(
        transaction_type=chosen_type,
        category=chosen_category,
        cost_center=chosen_cost_center,
        person=chosen_person,
        create_rule=create_rule,
        pattern=pattern,
    )


def _confirm(session: Session, group: TransactionGroup, values: _FormValues) -> bool:
    """Apply the owner's choice to the group; return True when it was applied."""
    if values.category is None:
        st.error(translate("validate.category_required"))
        return False
    classification = GroupClassification(
        transaction_type=values.transaction_type,
        category_id=values.category.id,
        cost_center_id=values.cost_center.id if values.cost_center else None,
        person_id=values.person.id if values.person else None,
    )
    try:
        outcome = confirm_group(
            session,
            group.key,
            classification,
            create_rule=values.create_rule,
            rule_pattern=values.pattern,
            expected_count=group.row_count,
        )
    except (GuidedError, RuleValidationError, ValidationError) as exc:
        st.error(str(exc))
        return False
    flash(
        "success", translate("guided.confirmed").format(count=outcome.validated_count)
    )
    if outcome.rule is not None:
        flash(
            "success",
            translate("guided.rule_created").format(count=outcome.rule_validated_count),
        )
    return True


def _current_page(total_groups: int) -> int:
    pages = max(1, math.ceil(total_groups / GROUP_PAGE_SIZE))
    st.session_state[PAGE_STATE_KEY] = min(
        max(1, int(st.session_state.get(PAGE_STATE_KEY, 1))), pages
    )
    if pages > 1:
        st.number_input(
            translate("guided.page"),
            min_value=1,
            max_value=pages,
            step=1,
            key=PAGE_STATE_KEY,
        )
        st.caption(
            translate("guided.page_caption").format(
                page=st.session_state[PAGE_STATE_KEY], pages=pages
            )
        )
    return int(st.session_state[PAGE_STATE_KEY])


def _render_groups(session: Session, held_ids: set[UUID]) -> None:
    summary = summarize_groups(session, held_ids)
    if summary.group_count == 0:
        st.info(translate("guided.empty"))
        return
    st.caption(
        translate("guided.summary").format(
            groups=summary.group_count, rows=summary.row_count
        )
    )
    page = _current_page(summary.group_count)
    options = _Options(
        list_categories(session), list_cost_centers(session), list_persons(session)
    )
    groups = list_pending_groups(
        session,
        held_ids=held_ids,
        limit=GROUP_PAGE_SIZE,
        offset=(page - 1) * GROUP_PAGE_SIZE,
    )
    for group in groups:
        values = _render_group_form(group, options)
        if values is not None and _confirm(session, group, values):
            st.rerun()


def _flash_undo(outcome: UndoOutcome) -> None:
    flash("success", translate("guided.undone").format(count=outcome.returned_count))
    if outcome.rule_deactivated:
        flash("info", translate("guided.undone_rule"))
    if outcome.skipped_count:
        flash(
            "warning",
            translate("guided.undone_skipped").format(count=outcome.skipped_count),
        )


def _row_label(txn: Transaction) -> str:
    return (
        f"{txn.operation_date.isoformat()} · "
        f"{format_minor(txn.amount_minor, txn.currency_code)} — "
        f"{txn.original_description}"
    )


def _render_undo_rows(session: Session, summary: GuidedActionSummary) -> None:
    action = summary.action
    with st.expander(translate("guided.undo_rows_expander")):
        rows = list_guided_action_rows(session, action.id, limit=UNDO_ROWS_SIZE)
        if summary.remaining_rows > len(rows):
            st.caption(translate("guided.undo_rows_limit").format(shown=len(rows)))
        by_id = {row.id: row for row in rows}
        chosen = st.multiselect(
            translate("guided.undo_rows_select"),
            list(by_id),
            format_func=lambda txn_id: _row_label(by_id[txn_id]),
            key=f"guided_undo_rows_{action.id}",
        )
        if st.button(
            translate("guided.undo_rows_button"),
            disabled=not chosen,
            key=f"guided_undo_rows_button_{action.id}",
        ):
            _flash_undo(undo_guided_rows(session, chosen))
            st.rerun()


def _render_action(session: Session, summary: GuidedActionSummary) -> None:
    action = summary.action
    caption = translate("guided.undo_caption").format(
        description=action.normalized_description,
        direction=_direction_label(action.direction),
        count=summary.remaining_rows,
    )
    if action.rule_id is not None:
        caption = f"{caption} · {translate('guided.undo_rule_tag')}"
    st.text(caption)
    if st.button(
        translate("guided.undo_group"),
        help=translate("guided.undo_group_help"),
        key=f"guided_undo_{action.id}",
    ):
        try:
            _flash_undo(undo_guided_action(session, action.id))
        except GuidedError as exc:
            st.error(str(exc))
            return
        st.rerun()
    _render_undo_rows(session, summary)


def _render_recent_actions(session: Session) -> None:
    total = count_guided_actions(session)
    if total == 0:
        return
    st.subheader(translate("guided.undo_title"))
    st.caption(translate("guided.undo_help"))
    summaries = list_guided_actions(session, limit=UNDO_LIST_SIZE)
    if total > len(summaries):
        st.caption(
            translate("guided.undo_limit").format(shown=len(summaries), total=total)
        )
    for summary in summaries:
        _render_action(session, summary)


def render_guided(session: Session, held_ids: set[UUID]) -> None:
    """Render the by-concept view: groups to classify and groups to undo."""
    st.subheader(translate("validate.view.by_concept"))
    st.caption(translate("guided.intro"))
    _render_groups(session, held_ids)
    _render_recent_actions(session)
