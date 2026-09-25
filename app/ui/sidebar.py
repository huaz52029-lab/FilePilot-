"""左侧导航栏。"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.common.constants import APP_NAME, APP_VERSION, SIDEBAR_WIDTH
from app.ui.icons import default_icon_provider
from app.ui.navigation import ALL_NAV_ITEMS, FOOTER_NAV_ITEMS, MAIN_NAV_ITEMS, NavItem, PageId
from app.ui.theme import token
from app.ui.widgets.badge import StatusBadge
from app.ui.widgets.cards import Divider


class NavButton(QPushButton):
    """侧边导航按钮（可选带计数徽标）。"""

    def __init__(self, item: NavItem, *, icons=None, parent: QWidget | None = None) -> None:
        super().__init__(item.label, parent)
        self.item = item
        self._icons = icons or default_icon_provider()
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(38)
        self.setIconSize(QSize(18, 18))
        if item.tooltip:
            self.setToolTip(item.tooltip)
        self.refresh_icon()

    def refresh_icon(self) -> None:
        """按主题刷新图标（选中态使用强调色）。"""
        color = token("accent") if self.isChecked() else token("text_secondary")
        self.setIcon(self._icons.icon(self.item.icon, color=color, size=18))


class Sidebar(QWidget):
    """固定宽度导航栏：品牌区 + 主导航 + 底部导航。"""

    page_selected = Signal(object)

    def __init__(self, *, icons=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icons = icons or default_icon_provider()
        self.setObjectName("Sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)
        self._buttons: dict[PageId, NavButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 16)
        layout.setSpacing(6)

        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 0)
        brand.setSpacing(10)
        self._logo = QLabel(self)
        self._logo.setFixedSize(28, 28)
        brand.addWidget(self._logo, 0)
        brand_text = QVBoxLayout()
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(0)
        name = QLabel(APP_NAME, self)
        name.setProperty("role", "brand-title")
        brand_text.addWidget(name)
        version = QLabel(f"v{APP_VERSION}", self)
        version.setProperty("role", "brand-subtitle")
        brand_text.addWidget(version)
        brand.addLayout(brand_text)
        brand.addStretch(1)
        layout.addLayout(brand)
        layout.addSpacing(14)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for item in MAIN_NAV_ITEMS:
            button = self._make_button(item)
            layout.addWidget(button)

        layout.addSpacing(10)
        layout.addWidget(Divider(self))
        layout.addSpacing(10)
        for item in FOOTER_NAV_ITEMS:
            button = self._make_button(item)
            layout.addWidget(button)
        layout.addStretch(1)

        self._footer = QLabel("本地存储 · 无云端同步", self)
        self._footer.setProperty("role", "sidebar-footer")
        self._footer.setWordWrap(True)
        layout.addWidget(self._footer)

        self._downloads_badge: StatusBadge | None = None
        self.refresh_icons()
        _ = ALL_NAV_ITEMS

    # ------------------------------------------------------------------
    def _make_button(self, item: NavItem) -> NavButton:
        button = NavButton(item, icons=self._icons, parent=self)
        self._group.addButton(button)
        button.clicked.connect(lambda _checked=False, page=item.page_id: self._on_clicked(page))
        self._buttons[item.page_id] = button
        return button

    def _on_clicked(self, page_id: PageId) -> None:
        self.set_current(page_id)
        self.page_selected.emit(page_id)

    # ------------------------------------------------------------------
    def set_current(self, page_id: PageId) -> None:
        """设置当前选中项（不触发信号）。"""
        button = self._buttons.get(page_id)
        for other in self._buttons.values():
            other.setChecked(other is button)
            other.refresh_icon()
        if button is not None and not button.isChecked():
            button.setChecked(True)

    def set_download_badge(self, count: int) -> None:
        """在“下载”项上显示活动任务数量。"""
        button = self._buttons.get(PageId.DOWNLOADS)
        if button is None:
            return
        if self._downloads_badge is None:
            self._downloads_badge = StatusBadge("", "accent", self)
            self._downloads_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._downloads_badge.setVisible(False)
        badge = self._downloads_badge
        if count > 0:
            badge.setText(str(count))
            badge.set_tone("accent")
            badge.setVisible(True)
            badge.adjustSize()
            badge.move(button.width() - badge.width() - 10, button.y() + 12)
            badge.raise_()
        else:
            badge.setVisible(False)

    def refresh_icons(self) -> None:
        """主题切换后刷新品牌与导航图标。"""
        self._logo.setPixmap(self._icons.pixmap("logo", color=token("accent"), size=28))
        for button in self._buttons.values():
            button.refresh_icon()
