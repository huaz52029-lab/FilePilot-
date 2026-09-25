"""带标题、计数与空状态的区块容器。"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from app.ui.widgets.badge import StatusBadge
from app.ui.widgets.cards import SectionHeader


class Section(QWidget):
    """列表区块：标题栏 + 内容区 + 空状态。"""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        icon: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        self.header = SectionHeader(title, subtitle, icon=icon, parent=self)
        header_row.addWidget(self.header, 1)
        self.count_badge = StatusBadge("0", "neutral", self)
        header_row.addWidget(self.count_badge, 0)
        layout.addLayout(header_row)

        self.content = QVBoxLayout()
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(10)
        layout.addLayout(self.content)

        self._empty_widget: QWidget | None = None
        self._items: list[QWidget] = []

    # -- API ---------------------------------------------------------------
    def add_widget(self, widget: QWidget) -> QWidget:
        """添加一个列表项。"""
        self.content.addWidget(widget)
        self._items.append(widget)
        self._refresh_count()
        return widget

    def add_layout(self, layout) -> None:  # type: ignore[no-untyped-def]
        self.content.addLayout(layout)

    def clear(self) -> None:
        """清空所有列表项（保留空状态控件）。"""
        for widget in self._items:
            self.content.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._items.clear()
        self._refresh_count()

    def set_empty_widget(self, widget: QWidget) -> None:
        """设置空状态控件（不参与计数）。"""
        if self._empty_widget is not None:
            self.content.removeWidget(self._empty_widget)
            self._empty_widget.setParent(None)
        self._empty_widget = widget
        self.content.addWidget(widget)
        self._refresh_count()

    def set_count(self, value: int, *, tone: str = "neutral") -> None:
        """直接设置计数徽标。"""
        self.count_badge.set_state(str(value), tone if value else "neutral")

    def _refresh_count(self) -> None:
        count = len(self._items)
        self.count_badge.set_state(str(count), "accent" if count else "neutral")
        if self._empty_widget is not None:
            self._empty_widget.setVisible(count == 0)

    @property
    def items(self) -> list[QWidget]:
        return list(self._items)

    def set_title(self, text: str) -> None:
        self.header.set_title(text)

    def set_subtitle(self, text: str) -> None:
        self.header.set_subtitle(text)
