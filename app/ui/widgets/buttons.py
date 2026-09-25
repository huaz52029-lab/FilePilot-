"""统一按钮组件。"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QPushButton, QWidget

from app.ui.icons import IconProvider, default_icon_provider
from app.ui.theme import set_variant

_ICON_TOKENS = {
    "primary": "accent_text",
    "danger": "accent_text",
    "secondary": "text_secondary",
    "ghost": "text_secondary",
}


class AppButton(QPushButton):
    """带变体与主题图标的按钮。"""

    def __init__(
        self,
        text: str = "",
        *,
        variant: str = "secondary",
        icon_name: str | None = None,
        icon_size: int = 16,
        icons: IconProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._icons = icons or default_icon_provider()
        self._icon_name = icon_name
        self._icon_size = icon_size
        self._variant = variant
        self.setProperty("variant", variant)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(icon_size, icon_size))
        self._refresh_icon()

    # -- API ---------------------------------------------------------------
    def set_variant(self, variant: str) -> None:
        """切换视觉变体：primary / secondary / ghost / danger。"""
        self._variant = variant
        set_variant(self, variant)
        self._refresh_icon()

    def set_icon_name(self, name: str | None, *, color: str | None = None) -> None:
        """设置图标名称（``None`` 表示移除图标）。"""
        self._icon_name = name
        self._refresh_icon(color)

    def _refresh_icon(self, color: str | None = None) -> None:
        if not self._icon_name:
            self.setIcon(QIcon())
            self.setIconSize(QSize(0, 0))
            return
        self.setIconSize(QSize(self._icon_size, self._icon_size))
        stroke = color or self._icons.color_for(
            _ICON_TOKENS.get(self._variant, "text_secondary"), "#FFFFFF"
        )
        self.setIcon(self._icons.icon(self._icon_name, color=stroke, size=self._icon_size))


class PrimaryButton(AppButton):
    """主要操作按钮（蓝色实心）。"""

    def __init__(self, text: str = "", *, icon_name: str | None = None, parent: QWidget | None = None):
        super().__init__(text, variant="primary", icon_name=icon_name, parent=parent)


class SecondaryButton(AppButton):
    """次要操作按钮（描边）。"""

    def __init__(self, text: str = "", *, icon_name: str | None = None, parent: QWidget | None = None):
        super().__init__(text, variant="secondary", icon_name=icon_name, parent=parent)


class GhostButton(AppButton):
    """弱化按钮（无边框）。"""

    def __init__(self, text: str = "", *, icon_name: str | None = None, parent: QWidget | None = None):
        super().__init__(text, variant="ghost", icon_name=icon_name, parent=parent)


class DangerButton(AppButton):
    """危险操作按钮（红色）。"""

    def __init__(self, text: str = "", *, icon_name: str | None = None, parent: QWidget | None = None):
        super().__init__(text, variant="danger", icon_name=icon_name, parent=parent)


class IconButton(AppButton):
    """纯图标按钮（用于标题栏与列表操作）。"""

    def __init__(
        self,
        icon_name: str,
        *,
        tooltip: str = "",
        size: int = 16,
        button_size: int = 30,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("", variant="ghost", icon_name=icon_name, icon_size=size, parent=parent)
        self.setFixedSize(button_size, button_size)
        if tooltip:
            self.setToolTip(tooltip)
