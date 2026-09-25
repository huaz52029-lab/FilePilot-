"""统计卡片（首页 Dashboard）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.ui.icons import default_icon_provider
from app.ui.theme import token
from app.ui.widgets.cards import Card


class StatCard(Card):
    """展示一个关键指标：数值 + 单位 + 说明。"""

    def __init__(
        self,
        title: str,
        value: str = "—",
        *,
        unit: str = "",
        caption: str = "",
        icon: str = "chart",
        tone: str = "accent",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, padding=(18, 16, 18, 16), spacing=6, shadow=True)
        self._icons = default_icon_provider()
        self._tone = tone
        self._unit = unit

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)

        self._icon_label = QLabel(self)
        self._icon_label.setFixedSize(34, 34)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setObjectName("StatIcon")
        self._icon_label.setProperty("tone", tone)
        self._icon_name = icon
        top.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignTop)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        self._title_label = QLabel(title, self)
        self._title_label.setProperty("role", "stat-title")
        text_column.addWidget(self._title_label)
        self._caption_label = QLabel(caption, self)
        self._caption_label.setProperty("role", "stat-caption")
        self._caption_label.setVisible(bool(caption))
        text_column.addWidget(self._caption_label)
        top.addLayout(text_column)
        top.addStretch(1)
        self.body.addLayout(top)

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(6)
        self._value_label = QLabel(value, self)
        self._value_label.setProperty("role", "stat-value")
        value_row.addWidget(self._value_label, 0, Qt.AlignmentFlag.AlignBottom)
        self._unit_label = QLabel(unit, self)
        self._unit_label.setProperty("role", "stat-unit")
        self._unit_label.setVisible(bool(unit))
        value_row.addWidget(self._unit_label, 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addStretch(1)
        self.body.addLayout(value_row)

        self.refresh_icon()

    # -- API ---------------------------------------------------------------
    def set_value(self, value: str, *, unit: str | None = None) -> None:
        self._value_label.setText(value)
        if unit is not None:
            self._unit = unit
            self._unit_label.setText(unit)
            self._unit_label.setVisible(bool(unit))

    def set_caption(self, caption: str) -> None:
        self._caption_label.setText(caption)
        self._caption_label.setVisible(bool(caption))

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self._icon_label.setProperty("tone", tone)
        self.refresh_icon()

    def refresh_icon(self) -> None:
        """按主题与色调重新渲染图标。"""
        color = token(self._tone, token("accent", "#3B82F6"))
        self._icon_label.setPixmap(self._icons.pixmap(self._icon_name, color=color, size=18))
