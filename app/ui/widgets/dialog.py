"""统一对话框：基类与常用确认 / 提示。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.ui.icons import default_icon_provider
from app.ui.theme import token
from app.ui.widgets.buttons import DangerButton, GhostButton, PrimaryButton, SecondaryButton
from app.ui.widgets.cards import Divider


class AppDialog(QDialog):
    """带标题、内容区与底部按钮栏的对话框。"""

    def __init__(
        self,
        title: str,
        *,
        subtitle: str = "",
        width: int = 460,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self._icons = default_icon_provider()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(14)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        title_column = QVBoxLayout()
        title_column.setContentsMargins(0, 0, 0, 0)
        title_column.setSpacing(2)
        self._title_label = QLabel(title, self)
        self._title_label.setProperty("role", "dialog-title")
        title_column.addWidget(self._title_label)
        self._subtitle_label = QLabel(subtitle, self)
        self._subtitle_label.setProperty("role", "dialog-subtitle")
        self._subtitle_label.setWordWrap(True)
        self._subtitle_label.setVisible(bool(subtitle))
        title_column.addWidget(self._subtitle_label)
        header.addLayout(title_column, 1)
        close_button = GhostButton("", icon_name="close", parent=self)
        close_button.setFixedSize(28, 28)
        close_button.clicked.connect(self.reject)
        header.addWidget(close_button, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header)
        outer.addWidget(Divider(self))

        self.content = QVBoxLayout()
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(12)
        outer.addLayout(self.content, 1)

        self.footer = QHBoxLayout()
        self.footer.setContentsMargins(0, 0, 0, 0)
        self.footer.setSpacing(8)
        self.footer.addStretch(1)
        outer.addLayout(self.footer)

    # -- 构建 --------------------------------------------------------------
    def add_widget(self, widget: QWidget) -> QWidget:
        self.content.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:  # type: ignore[no-untyped-def]
        self.content.addLayout(layout)

    def add_button(self, widget: QWidget) -> QWidget:
        self.footer.addWidget(widget)
        return widget

    def set_subtitle(self, text: str) -> None:
        self._subtitle_label.setText(text)
        self._subtitle_label.setVisible(bool(text))

    def add_scrollable(self, widget: QWidget, *, max_height: int = 320) -> QScrollArea:
        """把控件放入可滚动区域（长列表用）。"""
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMaximumHeight(max_height)
        scroll.setWidget(widget)
        self.content.addWidget(scroll, 1)
        return scroll


def _message_dialog(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    icon: str,
    tone: str,
    confirm_text: str = "知道了",
    cancel_text: str | None = None,
    danger: bool = False,
) -> bool:
    dialog = AppDialog(title, parent=parent)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(12)
    icon_label = QLabel(dialog)
    icon_label.setPixmap(default_icon_provider().pixmap(icon, color=token(tone, "#4C9EF5"), size=22))
    icon_label.setFixedSize(22, 22)
    row.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
    message = QLabel(text, dialog)
    message.setWordWrap(True)
    message.setProperty("role", "dialog-message")
    row.addWidget(message, 1)
    dialog.add_layout(row)

    if cancel_text:
        cancel = SecondaryButton(cancel_text, parent=dialog)
        cancel.clicked.connect(dialog.reject)
        dialog.add_button(cancel)
    confirm = DangerButton(confirm_text, parent=dialog) if danger else PrimaryButton(confirm_text, parent=dialog)
    confirm.setDefault(True)
    confirm.clicked.connect(dialog.accept)
    dialog.add_button(confirm)
    return dialog.exec() == QDialog.DialogCode.Accepted


def confirm(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    confirm_text: str = "确认",
    cancel_text: str = "取消",
    danger: bool = False,
) -> bool:
    """确认对话框（默认不执行任何操作）。"""
    return _message_dialog(
        parent,
        title,
        text,
        icon="alert" if danger else "info",
        tone="error" if danger else "info",
        confirm_text=confirm_text,
        cancel_text=cancel_text,
        danger=danger,
    )


def show_info(parent: QWidget | None, title: str, text: str) -> None:
    """信息提示。"""
    _message_dialog(parent, title, text, icon="info", tone="info")


def show_warning(parent: QWidget | None, title: str, text: str) -> None:
    """警告提示。"""
    _message_dialog(parent, title, text, icon="alert", tone="warning")


def show_error(parent: QWidget | None, title: str, text: str) -> None:
    """错误提示（只展示自然语言，不展示 traceback）。"""
    _message_dialog(parent, title, text, icon="alert", tone="error")


def show_result_details(
    parent: QWidget | None,
    title: str,
    summary: str,
    items: list[tuple[str, str]],
    *,
    max_height: int = 320,
) -> None:
    """展示“成功 N / 失败 M”明细列表（每条为 (名称, 原因)）。"""
    dialog = AppDialog(title, subtitle=summary, width=560, parent=parent)
    container = QWidget(dialog)
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    for name, reason in items[:500]:
        label = QLabel(f"{name}：{reason}", container)
        label.setProperty("role", "detail-value")
        label.setWordWrap(True)
        layout.addWidget(label)
    layout.addStretch(1)
    dialog.add_scrollable(container, max_height=max_height)
    close_button = SecondaryButton("关闭", parent=dialog)
    close_button.clicked.connect(dialog.accept)
    dialog.add_button(close_button)
    dialog.exec()
