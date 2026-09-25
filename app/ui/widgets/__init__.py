"""统一 UI 组件库。

所有页面必须复用这里的组件，避免每个页面各写一套样式。
"""

from __future__ import annotations

from app.ui.widgets.badge import StatusBadge
from app.ui.widgets.buttons import (
    AppButton,
    DangerButton,
    GhostButton,
    IconButton,
    PrimaryButton,
    SecondaryButton,
)
from app.ui.widgets.cards import Card, SectionHeader, apply_shadow
from app.ui.widgets.empty_state import EmptyState
from app.ui.widgets.inputs import ComboRow, LabeledRow, PathPicker, SpinRow, SwitchRow
from app.ui.widgets.progress_bar import ProgressBar
from app.ui.widgets.section import Section
from app.ui.widgets.stat_card import StatCard
from app.ui.widgets.toast import ToastHost, ToastLevel
from app.ui.widgets.url_input import UrlInputBox

__all__ = [
    "AppButton",
    "Card",
    "ComboRow",
    "DangerButton",
    "EmptyState",
    "GhostButton",
    "IconButton",
    "LabeledRow",
    "PathPicker",
    "PrimaryButton",
    "ProgressBar",
    "SecondaryButton",
    "Section",
    "SectionHeader",
    "SpinRow",
    "StatCard",
    "StatusBadge",
    "SwitchRow",
    "ToastHost",
    "ToastLevel",
    "UrlInputBox",
    "apply_shadow",
]
