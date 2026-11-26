"""Feedback messages that survive the page rerun that follows an action.

Streamlit clears everything a page drew when it reruns, so a success message
shown right before ``st.rerun()`` is gone before anyone can read it. Pages
queue the message with :func:`flash` and call :func:`show_flashes` once at the
top of the next render; the message then stays on screen until the owner's next
interaction.
"""

from __future__ import annotations

from typing import Literal

import streamlit as st

FlashKind = Literal["success", "error", "warning", "info"]
_STATE_KEY = "flash_messages"


def flash(kind: FlashKind, text: str) -> None:
    """Queue ``text`` to be shown once on the next render."""
    queued: list[tuple[str, str]] = st.session_state.setdefault(_STATE_KEY, [])
    queued.append((kind, text))


def show_flashes() -> None:
    """Show and clear every queued message."""
    queued: list[tuple[str, str]] = st.session_state.pop(_STATE_KEY, [])
    for kind, text in queued:
        getattr(st, kind)(text)
