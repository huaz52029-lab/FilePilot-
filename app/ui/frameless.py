"""无边框窗口支持（现代标题栏 + 系统级拖动 / 缩放）。

使用 Qt 6 的 ``startSystemMove`` / ``startSystemResize``，因此仍然保留系统
的贴靠布局、双击最大化与分屏行为。
"""

from __future__ import annotations

import os
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QMainWindow, QWidget

from app.core.common.logger import get_logger

_log = get_logger("ui.frameless")

RESIZE_MARGIN: Final[int] = 7


def edges_at(widget: QWidget, x: int, y: int, margin: int = RESIZE_MARGIN) -> Qt.Edge:
    """根据窗口内坐标计算需要缩放的方向。"""
    edges = Qt.Edge(0)
    if y <= margin:
        edges |= Qt.Edge.TopEdge
    if y >= widget.height() - margin:
        edges |= Qt.Edge.BottomEdge
    if x <= margin:
        edges |= Qt.Edge.LeftEdge
    if x >= widget.width() - margin:
        edges |= Qt.Edge.RightEdge
    return edges


def cursor_for_edges(edges: Qt.Edge) -> Qt.CursorShape | None:
    """缩放方向对应的鼠标指针形状。"""
    top = bool(edges & Qt.Edge.TopEdge)
    bottom = bool(edges & Qt.Edge.BottomEdge)
    left = bool(edges & Qt.Edge.LeftEdge)
    right = bool(edges & Qt.Edge.RightEdge)
    if (top and left) or (bottom and right):
        return Qt.CursorShape.SizeFDiagCursor
    if (top and right) or (bottom and left):
        return Qt.CursorShape.SizeBDiagCursor
    if left or right:
        return Qt.CursorShape.SizeHorCursor
    if top or bottom:
        return Qt.CursorShape.SizeVerCursor
    return None


def begin_system_resize(widget: QWidget, edges: Qt.Edge) -> bool:
    """请求系统缩放；返回是否成功接管。"""
    if not edges:
        return False
    window = widget.window()
    if window.isMaximized() or window.isFullScreen():
        return False
    handle = window.windowHandle()
    if handle is None:  # pragma: no cover - 窗口尚未显示
        return False
    return bool(handle.startSystemResize(edges))


def begin_system_move(widget: QWidget) -> bool:
    """请求系统拖动窗口。"""
    window = widget.window()
    if window.isFullScreen():
        return False
    handle = window.windowHandle()
    if handle is None:  # pragma: no cover
        return False
    return bool(handle.startSystemMove())


def apply_rounded_corners(widget: QWidget, preference: int = 2) -> None:
    """请求 Windows 11 圆角（DWMWA_WINDOW_CORNER_PREFERENCE）。

    ``preference``：0=默认，1=禁用，2=圆角，3=小圆角。
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        hwnd = int(widget.winId())
        value = ctypes.c_int(preference)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
            hwnd, 33, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception as exc:  # noqa: BLE001 - 非关键效果，失败即忽略
        _log.debug("设置窗口圆角失败：%s", exc)


class FramelessMixin:
    """为窗口提供边缘缩放与指针反馈。"""

    resize_margin: int = RESIZE_MARGIN

    def _handle_resize_press(self, event: QMouseEvent, widget: QWidget) -> bool:
        """在按下鼠标时尝试系统缩放。"""
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        position = event.position().toPoint()
        return begin_system_resize(widget, edges_at(widget, position.x(), position.y()))

    def _update_resize_cursor(self, event: QMouseEvent, widget: QWidget) -> None:
        """根据鼠标位置更新指针形状。"""
        if widget.window().isMaximized():
            widget.unsetCursor()
            return
        position = event.position().toPoint()
        edges = edges_at(widget, position.x(), position.y())
        shape = cursor_for_edges(edges)
        if shape is None:
            widget.unsetCursor()
        else:
            widget.setCursor(shape)


class FramelessMainWindow(QMainWindow, FramelessMixin):
    """无边框主窗口基类。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMouseTracking(True)

    # -- 事件 --------------------------------------------------------------
    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        if not getattr(self, "_corners_applied", False):
            apply_rounded_corners(self)
            self._corners_applied = True

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._handle_resize_press(event, self):
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._update_resize_cursor(event, self)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.unsetCursor()
        super().leaveEvent(event)
