"""卡片与区块标题等基础容器。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui.icons import default_icon_provider
from app.ui.theme import token


def apply_shadow(
    widget: QWidget,
    *,
    color: str | None = None,
    blur: int = 26,
    y_offset: int = 6,
) -> QGraphicsDropShadowEffect:
    """给控件添加轻微投影（用于强调卡片层次）。

    注意：不能同时给 effect 设置父对象再调用 ``setGraphicsEffect``，
    否则 Qt 会重复接管所有权，在退出阶段可能触发内存错误。
    """
    effect = QGraphicsDropShadowEffect()
    from PySide6.QtGui import QColor

    effect.setColor(QColor(color or token("shadow", "#101114")))
    effect.setBlurRadius(blur)
    effect.setOffset(0, y_offset)
    widget.setGraphicsEffect(effect)
    return effect


class Card(QFrame):
    """圆角卡片容器。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        padding: tuple[int, int, int, int] = (18, 18, 18, 18),
        spacing: int = 12,
        hoverable: bool = False,
        shadow: bool = False,
        object_name: str = "Card",
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setProperty("hoverable", hoverable)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*padding)
        self.body.setSpacing(spacing)
        if shadow:
            apply_shadow(self)

    # -- 便捷方法 ----------------------------------------------------------
    def add(self, widget: QWidget, *, stretch: int = 0) -> QWidget:
        """把控件加入卡片主体。"""
        self.body.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout: QLayout) -> QLayout:
        self.body.addLayout(layout)
        return layout

    def add_stretch(self, stretch: int = 1) -> None:
        self.body.addStretch(stretch)


class SectionHeader(QWidget):
    """区块标题：标题 + 副标题 + 右侧操作区。"""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        icon: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icons = default_icon_provider()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        if icon:
            self._icon_label = QLabel(self)
            self._icon_label.setPixmap(self._icons.pixmap(icon, size=18))
            self._icon_label.setFixedSize(18, 18)
            layout.addWidget(self._icon_label)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        self._title = QLabel(title, self)
        self._title.setProperty("role", "section-title")
        text_layout.addWidget(self._title)
        self._subtitle = QLabel(subtitle, self)
        self._subtitle.setProperty("role", "section-subtitle")
        self._subtitle.setVisible(bool(subtitle))
        text_layout.addWidget(self._subtitle)
        layout.addLayout(text_layout)
        layout.addStretch(1)

        self._actions_layout = QHBoxLayout()
        self._actions_layout.setContentsMargins(0, 0, 0, 0)
        self._actions_layout.setSpacing(8)
        layout.addLayout(self._actions_layout)

    # -- API ---------------------------------------------------------------
    def add_action(self, widget: QWidget) -> QWidget:
        """在右侧添加操作控件。"""
        self._actions_layout.addWidget(widget)
        return widget

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def set_title(self, text: str) -> None:
        self._title.setText(text)


class Divider(QFrame):
    """一像素分隔线。"""

    def __init__(self, parent: QWidget | None = None, *, horizontal: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("Divider")
        if horizontal:
            self.setFixedHeight(1)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setFixedWidth(1)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
