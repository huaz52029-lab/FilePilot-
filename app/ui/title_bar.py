"""自定义标题栏（无边框窗口）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QWidget

from app.core.common.constants import APP_NAME, TITLE_BAR_HEIGHT
from app.ui.frameless import (
    begin_system_move,
    begin_system_resize,
    cursor_for_edges,
    edges_at,
)
from app.ui.icons import default_icon_provider
from app.ui.theme import token
from app.ui.widgets.buttons import IconButton


class TitleBar(QWidget):
    """顶部标题栏：应用名 + 窗口控制按钮。"""

    def __init__(self, window: QMainWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self._icons = default_icon_provider()
        self.setObjectName("TitleBar")
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        self.setMouseTracking(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 8, 0)
        layout.setSpacing(8)

        self._logo = QLabel(self)
        self._logo.setFixedSize(20, 20)
        layout.addWidget(self._logo, 0)

        self._title = QLabel(APP_NAME, self)
        self._title.setProperty("role", "app-title")
        layout.addWidget(self._title, 0)
        layout.addStretch(1)

        self.minimize_button = IconButton("minimize", tooltip="最小化", size=14, parent=self)
        self.minimize_button.setObjectName("WinButton")
        self.minimize_button.clicked.connect(self._window.showMinimized)
        layout.addWidget(self.minimize_button, 0)

        self.maximize_button = IconButton("maximize", tooltip="最大化", size=13, parent=self)
        self.maximize_button.setObjectName("WinButton")
        self.maximize_button.clicked.connect(self._toggle_maximize)
        layout.addWidget(self.maximize_button, 0)

        self.close_button = IconButton("close", tooltip="关闭", size=14, parent=self)
        self.close_button.setObjectName("WinButton")
        self.close_button.setProperty("variant", "close")
        self.close_button.clicked.connect(self._window.close)
        layout.addWidget(self.close_button, 0)

        self._icon_names: dict[QWidget, str] = {
            self.minimize_button: "minimize",
            self.maximize_button: "maximize",
            self.close_button: "close",
        }
        self.refresh_icons()
        self.set_maximized(False)

    # ------------------------------------------------------------------
    def refresh_icons(self) -> None:
        """主题切换后刷新图标。"""
        self._logo.setPixmap(self._icons.pixmap("logo", color=token("accent"), size=20))
        for button, icon_name in self._icon_names.items():
            button.set_icon_name(icon_name, color=token("text_secondary"))  # type: ignore[attr-defined]

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_maximized(self, maximized: bool) -> None:
        """切换最大化 / 还原图标。"""
        name = "restore" if maximized else "maximize"
        self._icon_names[self.maximize_button] = name
        self.maximize_button.set_icon_name(
            name, color=token("text_secondary")
        )
        self.maximize_button.setToolTip("还原" if maximized else "最大化")

    # ------------------------------------------------------------------
    def _toggle_maximize(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    # -- 拖动与边缘缩放 ----------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            position = event.position().toPoint()
            edges = edges_at(self, position.x(), position.y())
            if edges and begin_system_resize(self, edges):
                return
            begin_system_move(self)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._window.isMaximized():
            self.unsetCursor()
        else:
            position = event.position().toPoint()
            shape = cursor_for_edges(edges_at(self, position.x(), position.y()))
            if shape is None:
                self.unsetCursor()
            else:
                self.setCursor(shape)
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()
        super().mouseDoubleClickEvent(event)
