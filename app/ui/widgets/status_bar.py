"""底部状态栏。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from app.core.common.constants import APP_NAME, APP_VERSION


class StatusBar(QWidget):
    """显示状态消息与下载汇总信息。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusBar")
        self.setFixedHeight(30)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(16)

        self.message_label = QLabel("就绪", self)
        self.message_label.setProperty("role", "status-text")
        layout.addWidget(self.message_label, 1)

        self.download_label = QLabel("", self)
        self.download_label.setProperty("role", "status-text")
        layout.addWidget(self.download_label, 0)

        self.version_label = QLabel(f"{APP_NAME} {APP_VERSION}", self)
        self.version_label.setProperty("role", "status-muted")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.version_label, 0)

    def show_message(self, message: str) -> None:
        self.message_label.setText(message or "就绪")

    def set_download_summary(self, text: str) -> None:
        self.download_label.setText(text)
