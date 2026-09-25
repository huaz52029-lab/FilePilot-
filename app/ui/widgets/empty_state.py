"""空状态占位。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from app.ui.icons import default_icon_provider
from app.ui.theme import token


class EmptyState(QWidget):
    """图标 + 标题 + 说明（+ 可选操作按钮）。"""

    def __init__(
        self,
        title: str = "暂无内容",
        description: str = "",
        *,
        icon: str = "file",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icons = default_icon_provider()
        self._icon_name = icon
        # 说明文字使用自动换行，布局的最小高度不可靠，这里给出明确下限，
        # 避免父布局空间不足时出现文字重叠。
        self.setMinimumHeight(138)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 28, 24, 28)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon_label = QLabel(self)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setFixedSize(48, 48)
        layout.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self._title_label = QLabel(title, self)
        self._title_label.setProperty("role", "empty-title")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title_label)

        self._description_label = QLabel(description, self)
        self._description_label.setProperty("role", "empty-description")
        self._description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._description_label.setWordWrap(True)
        self._description_label.setVisible(bool(description))
        layout.addWidget(self._description_label)

        self._action_layout = QHBoxLayout()
        self._action_layout.setContentsMargins(0, 6, 0, 0)
        self._action_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(self._action_layout)
        self.refresh_icon()

    def add_action(self, widget: QWidget) -> QWidget:
        """添加操作按钮。"""
        self._action_layout.addWidget(widget)
        return widget

    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def set_description(self, description: str) -> None:
        self._description_label.setText(description)
        self._description_label.setVisible(bool(description))

    def set_icon(self, name: str) -> None:
        self._icon_name = name
        self.refresh_icon()

    def refresh_icon(self) -> None:
        self._icon_label.setPixmap(
            self._icons.pixmap(self._icon_name, color=token("text_muted", "#767D89"), size=40)
        )
