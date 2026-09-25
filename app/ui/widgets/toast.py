"""轻量提示（Toast）。

Toast 由 :class:`ToastHost` 浮在窗口右下角，不阻塞操作，自动淡出。
"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)

from app.core.common.logger import get_logger
from app.ui.icons import default_icon_provider
from app.ui.theme import token

_log = get_logger("ui.toast")


class ToastLevel(StrEnum):
    """提示级别。"""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"

    @property
    def icon(self) -> str:
        return {
            ToastLevel.INFO: "info",
            ToastLevel.SUCCESS: "check",
            ToastLevel.WARNING: "alert",
            ToastLevel.ERROR: "alert",
        }[self]


class Toast(QFrame):
    """单条提示。"""

    def __init__(
        self,
        message: str,
        level: ToastLevel,
        *,
        timeout_ms: int = 4000,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setProperty("tone", level.value)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._timeout = max(1200, timeout_ms)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)
        icons = default_icon_provider()
        icon_label = QLabel(self)
        icon_label.setPixmap(
            icons.pixmap(level.icon, color=token(level.value, "#4C9EF5"), size=16)
        )
        icon_label.setFixedSize(16, 16)
        layout.addWidget(icon_label)

        text_label = QLabel(message, self)
        text_label.setWordWrap(True)
        text_label.setProperty("role", "toast-text")
        text_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        layout.addWidget(text_label)

        # 由 setGraphicsEffect 接管所有权（不要同时设置父对象）
        self._effect = QGraphicsOpacityEffect()
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._fade_in = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade_in.setDuration(160)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_out = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade_out.setDuration(220)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.finished.connect(self._on_faded_out)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

    def show_toast(self) -> None:
        """显示并启动计时。"""
        self.show()
        self.raise_()
        self._fade_in.start()
        self._timer.start(self._timeout)

    def dismiss(self) -> None:
        """开始淡出。"""
        self._timer.stop()
        self._fade_out.start()

    def _on_faded_out(self) -> None:
        host = self.parent()
        self.hide()
        self.deleteLater()
        if isinstance(host, ToastHost):
            host.remove(self)


class ToastHost(QWidget):
    """Toast 容器（覆盖在主窗口内容之上）。"""

    MARGIN = 20
    SPACING = 10
    MAX_VISIBLE = 3

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._toasts: list[Toast] = []
        self.hide()

    # -- API ---------------------------------------------------------------
    def show_message(
        self,
        message: str,
        level: ToastLevel | str = ToastLevel.INFO,
        *,
        timeout_ms: int = 4000,
    ) -> Toast:
        """显示一条提示。"""
        try:
            resolved = level if isinstance(level, ToastLevel) else ToastLevel(level)
        except ValueError:
            resolved = ToastLevel.INFO
        if not message:
            message = "操作已完成。"
        toast = Toast(message, resolved, timeout_ms=timeout_ms, parent=self)
        toast.setMaximumWidth(max(280, min(460, self.width() - 2 * self.MARGIN)))
        toast.adjustSize()
        self._toasts.append(toast)
        while len(self._toasts) > self.MAX_VISIBLE:
            oldest = self._toasts.pop(0)
            oldest.deleteLater()
        self.show()
        self.raise_()
        self._relayout()
        toast.show_toast()
        _log.debug("Toast[%s] %s", resolved.value, message)
        return toast

    def remove(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._relayout()
        if not self._toasts:
            self.hide()

    def clear(self) -> None:
        for toast in list(self._toasts):
            toast.deleteLater()
        self._toasts.clear()
        self.hide()

    # -- 布局 --------------------------------------------------------------
    def _relayout(self) -> None:
        bottom = self.height() - self.MARGIN
        for toast in reversed(self._toasts):
            toast.adjustSize()
            width = min(toast.sizeHint().width(), max(280, self.width() - 2 * self.MARGIN))
            toast.setFixedWidth(width)
            height = toast.sizeHint().height()
            x = max(self.MARGIN, self.width() - width - self.MARGIN)
            y = max(self.MARGIN, bottom - height)
            toast.move(x, y)
            toast.raise_()
            bottom = y - self.SPACING

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._relayout()
