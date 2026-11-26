"""Settings section: list, edit, deactivate and reactivate classification rules.

Every change is an explicit action and is confirmed on screen; pending movements
are reclassified afterwards, validated history is never touched.
"""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from fiskal.classification import (
    RuleNotFoundError,
    RuleSpec,
    list_rules,
    set_rule_active,
    update_rule,
)
from fiskal.classification.rules_service import spec_from_rule
from fiskal.db.models import Category, CostCenter, Rule
from fiskal.domain.enums import RuleMatchMode, RuleOrigin, TransactionType
from fiskal.domain.rule_matching import RuleValidationError
from fiskal.i18n import translate
from fiskal.services import list_categories, list_cost_centers
from fiskal.ui.forms import (
    FormValueError,
    format_optional_amount,
    format_optional_int,
    parse_optional_amount_minor,
    parse_optional_int,
)
from fiskal.ui.labels import (
    category_label,
    cost_center_label,
    format_minor_plain,
    status_marker,
    transaction_type_label,
)
from fiskal.ui.messages import flash

_MODES = [member.value for member in RuleMatchMode]
_TYPES = [
    member.value
    for member in TransactionType
    if member is not TransactionType.INTERNAL_TRANSFER
]


def _rule_title(rule: Rule, categories: dict[object, Category]) -> str:
    origin = translate(f"settings.rule_origin.{rule.origin}")
    category = category_label(categories.get(rule.category_id))
    return (
        f"{rule.pattern} → {category} · {origin} {status_marker(rule.is_active)}"
    ).strip()


def _index(options: list[object], value: object | None) -> int:
    return options.index(value) if value in options else 0


def _read_spec(
    rule: Rule,
    *,
    prefix: str,
    categories: list[Category],
    cost_centers: list[CostCenter],
) -> RuleSpec | None:
    pattern = st.text_input(
        translate("settings.rule_pattern"), value=rule.pattern, key=f"{prefix}_pattern"
    )
    mode = st.selectbox(
        translate("settings.rule_mode"),
        _MODES,
        index=_MODES.index(rule.mode),
        format_func=lambda value: translate(f"settings.rule_mode.{value}"),
        key=f"{prefix}_mode",
    )
    txn_type = st.selectbox(
        translate("settings.rule_type"),
        _TYPES,
        index=_index(list(_TYPES), rule.transaction_type),
        format_func=transaction_type_label,
        key=f"{prefix}_type",
    )
    category_ids = [category.id for category in categories]
    category = st.selectbox(
        translate("validate.category"),
        categories,
        index=_index(category_ids, rule.category_id),
        format_func=category_label,
        key=f"{prefix}_category",
    )
    cost_center_ids = [cost_center.id for cost_center in cost_centers]
    cost_center = st.selectbox(
        translate("validate.cost_center"),
        [None, *cost_centers],
        index=0
        if rule.cost_center_id is None
        else _index(cost_center_ids, rule.cost_center_id) + 1,
        format_func=lambda value: (
            translate("common.none") if value is None else cost_center_label(value)
        ),
        key=f"{prefix}_cost_center",
    )
    priority_text = st.text_input(
        translate("settings.rule_priority"),
        value=str(rule.priority),
        help=translate("settings.rule_priority_help"),
        key=f"{prefix}_priority",
    )
    amount_min = st.text_input(
        translate("settings.rule_amount_min"),
        value=format_optional_amount(rule.amount_min_minor),
        key=f"{prefix}_amount_min",
    )
    amount_max = st.text_input(
        translate("settings.rule_amount_max"),
        value=format_optional_amount(rule.amount_max_minor),
        key=f"{prefix}_amount_max",
    )
    day_min = st.text_input(
        translate("settings.rule_day_min"),
        value=format_optional_int(rule.day_of_month_min),
        key=f"{prefix}_day_min",
    )
    day_max = st.text_input(
        translate("settings.rule_day_max"),
        value=format_optional_int(rule.day_of_month_max),
        key=f"{prefix}_day_max",
    )
    try:
        priority = parse_optional_int(priority_text)
        spec = spec_from_rule(rule)
        return RuleSpec(
            priority=spec.priority if priority is None else priority,
            mode=mode,
            pattern=pattern,
            transaction_type=txn_type,
            category_id=category.id if category is not None else rule.category_id,
            cost_center_id=cost_center.id if cost_center is not None else None,
            person_id=rule.person_id,
            amount_min_minor=parse_optional_amount_minor(amount_min),
            amount_max_minor=parse_optional_amount_minor(amount_max),
            day_of_month_min=parse_optional_int(day_min),
            day_of_month_max=parse_optional_int(day_max),
            is_active=rule.is_active,
        )
    except FormValueError:
        st.error(translate("settings.invalid_number"))
        return None


def _render_item(
    session: Session,
    rule: Rule,
    categories: list[Category],
    cost_centers: list[CostCenter],
    by_id: dict[object, Category],
) -> None:
    key = f"settings_rule_{rule.id}"
    with st.expander(_rule_title(rule, by_id)):
        if rule.origin == RuleOrigin.SEED.value:
            st.caption(translate("settings.rule_seed_note"))
        st.caption(
            translate("settings.rule_amounts").format(
                low=format_minor_plain(rule.amount_min_minor),
                high=format_minor_plain(rule.amount_max_minor),
            )
        )
        with st.form(f"{key}_edit"):
            spec = _read_spec(
                rule, prefix=key, categories=categories, cost_centers=cost_centers
            )
            saved = st.form_submit_button(translate("settings.rule_save"))
        toggled = st.button(
            translate(
                "settings.deactivate" if rule.is_active else "settings.reactivate"
            ),
            key=f"{key}_toggle",
        )
    try:
        if saved and spec is not None:
            update_rule(session, rule.id, spec)
            flash("success", translate("settings.rule_saved"))
            st.rerun()
        if toggled:
            set_rule_active(session, rule.id, active=not rule.is_active)
            flash("success", translate("settings.rule_saved"))
            st.rerun()
    except (RuleValidationError, RuleNotFoundError) as exc:
        st.error(str(exc))


def render_rules(session: Session) -> None:
    """Render the rule management section."""
    st.caption(translate("settings.rules_intro"))
    rules = list_rules(session)
    if not rules:
        st.info(translate("settings.rules_empty"))
        return
    categories = list_categories(session, active_only=False)
    active_categories = [category for category in categories if category.is_active]
    by_id: dict[object, Category] = {category.id: category for category in categories}
    cost_centers = list_cost_centers(session)
    for rule in rules:
        # Keep an inactive current category selectable so the editor shows it.
        choices = list(active_categories)
        current = by_id.get(rule.category_id)
        if current is not None and current not in choices:
            choices.append(current)
        _render_item(session, rule, choices, cost_centers, by_id)
