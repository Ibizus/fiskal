"""Import page: upload a bank statement and revert whole imports."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from fiskal.i18n import translate
from fiskal.importers import (
    ImporterError,
    ImportRevertError,
    OpenbankImporter,
    import_statement,
    revert_import,
)
from fiskal.importers.base import ImportSummary
from fiskal.services import list_accounts, list_imports
from fiskal.ui.session import open_session

_ALLOWED_TYPES = ["xls", "html", "htm"]


def _account_label(account: object) -> str:
    hint = getattr(account, "account_hint", None)
    name = getattr(account, "name", "")
    if hint:
        return f"{name} ({translate('import.account_hint_suffix')} {hint})"
    return name


def _import_label(import_row: object) -> str:
    file_name = getattr(import_row, "file_name", "")
    imported_at = getattr(import_row, "imported_at", "")
    if imported_at:
        return f"{file_name} — {imported_at:%Y-%m-%d %H:%M}"
    return str(file_name)


def _render_summary(summary: ImportSummary) -> None:
    st.subheader(translate("import.summary_title"))
    if summary.first_transaction_date is not None:
        st.write(f"{translate('import.first_date')}: {summary.first_transaction_date}")
    if summary.last_transaction_date is not None:
        st.write(f"{translate('import.last_date')}: {summary.last_transaction_date}")
    st.write(f"{translate('import.rows_read')}: {summary.rows_read}")
    st.write(f"{translate('import.new')}: {summary.new_count}")
    st.write(f"{translate('import.duplicate')}: {summary.duplicate_count}")
    st.write(f"{translate('import.truncated')}: {summary.truncated_count}")
    st.write(f"{translate('import.classified_rule')}: {summary.classified_by_rule}")
    st.write(
        f"{translate('import.classified_history')}: {summary.classified_by_history}"
    )
    st.write(
        f"{translate('import.classified_default')}: {summary.classified_by_default}"
    )
    st.write(f"{translate('import.pending')}: {summary.unclassified}")
    st.write(f"{translate('import.proposed_pairs')}: {summary.proposed_pair_count}")
    if summary.adds_no_movement:
        st.warning(translate("import.no_new"))
    if summary.warnings:
        st.subheader(translate("import.warnings"))
        for warning in summary.warnings:
            st.write(f"- {warning}")


def _render_import(session: Session) -> None:
    accounts = list_accounts(session)
    if not accounts:
        st.info(translate("import.no_accounts"))
        return

    account = st.selectbox(
        translate("import.account"),
        accounts,
        format_func=_account_label,
        key="import_account",
    )
    uploaded = st.file_uploader(
        translate("import.upload"),
        type=_ALLOWED_TYPES,
        key="import_file",
    )
    if uploaded is not None and st.button(translate("import.import_button")):
        try:
            result = import_statement(
                session,
                OpenbankImporter(),
                account,
                uploaded.name,
                uploaded.getvalue(),
            )
        except ImporterError as exc:
            st.error(str(exc))
            return
        st.session_state["import_summary"] = result.summary

    if "import_summary" in st.session_state:
        _render_summary(st.session_state["import_summary"])


def _render_revert(session: Session) -> None:
    imports = list_imports(session)
    if not imports:
        return

    st.subheader(translate("import.revert_title"))
    selected = st.selectbox(
        translate("import.revert_select"),
        imports,
        format_func=_import_label,
        key="revert_import_select",
    )
    if st.button(translate("import.revert_button")):
        try:
            revert_import(session, selected.id)
        except ImportRevertError as exc:
            st.error(f"{translate('import.revert_error')} {exc}")
            return
        st.session_state.pop("import_summary", None)
        st.success(translate("import.revert_success"))
        st.rerun()


def main() -> None:
    """Render the import page."""
    st.set_page_config(page_title=translate("nav.import"), page_icon="📥")
    st.header(translate("nav.import"))
    with open_session() as session:
        _render_import(session)
        _render_revert(session)


if __name__ == "__main__":
    main()
