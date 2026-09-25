"""页面基类。

统一页面结构：标题 / 副标题 / 右上角操作区 / 主体内容。
页面只负责展示与收集输入，业务逻辑一律交给 ``AppContext`` 中的服务。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core.common.constants import PAGE_MARGIN
from app.services.app_context import AppContext
from app.ui.icons import IconProvider, default_icon_provider
from app.ui.navigation import PageId
from app.ui.widgets.toast import ToastLevel


class BasePage(QWidget):
    """所有页面的基类。"""

    page_id: PageId = PageId.HOME
    title: str = ""
    subtitle: str = ""

    toast_requested = Signal(str, str)
    navigate_requested = Signal(object)

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        self.settings = context.settings
        self.download_repo = context.download_repo
        self.downloads = context.downloads
        self.history = context.history_repo
        self.rules = context.rule_repo
        self.icons: IconProvider = default_icon_provider()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(PAGE_MARGIN, 22, PAGE_MARGIN, PAGE_MARGIN)
        outer.setSpacing(18)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(16)
        title_column = QVBoxLayout()
        title_column.setContentsMargins(0, 0, 0, 0)
        title_column.setSpacing(4)

        self.title_label = QLabel(self.title, self)
        self.title_label.setProperty("role", "page-title")
        title_column.addWidget(self.title_label)
        self.subtitle_label = QLabel(self.subtitle, self)
        self.subtitle_label.setProperty("role", "page-subtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(self.subtitle))
        title_column.addWidget(self.subtitle_label)
        header_row.addLayout(title_column, 1)

        self.actions_layout = QHBoxLayout()
        self.actions_layout.setContentsMargins(0, 0, 0, 0)
        self.actions_layout.setSpacing(8)
        header_row.addLayout(self.actions_layout, 0)
        outer.addLayout(header_row)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(16)
        outer.addLayout(self.body, 1)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def on_show(self) -> None:
        """页面显示时调用（刷新数据）。"""

    def on_hide(self) -> None:
        """页面隐藏时调用（可选：停止定时器 / 取消任务）。"""

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------
    def add_action(self, widget: QWidget) -> QWidget:
        """在标题右侧添加操作控件。"""
        self.actions_layout.addWidget(widget)
        return widget

    def add_scrollable(self, *, spacing: int = 16) -> QVBoxLayout:
        """在主体中创建可滚动内容区，返回内容布局。"""
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setObjectName("PageScroll")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget(scroll)
        container.setObjectName("PageScrollContent")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 10, 8)
        layout.setSpacing(spacing)
        scroll.setWidget(container)
        self.body.addWidget(scroll, 1)
        self.scroll_area = scroll
        return layout

    def toast(self, message: str, level: ToastLevel | str = ToastLevel.INFO) -> None:
        """请求主窗口显示提示。"""
        resolved = level.value if isinstance(level, ToastLevel) else str(level)
        self.toast_requested.emit(message, resolved)

    def report_error(self, exc: BaseException | str, *, title: str | None = None) -> None:
        """统一的错误展示（自然语言，不含 traceback）。"""
        from app.core.common.exceptions import to_user_message

        message = to_user_message(exc)
        if title:
            message = f"{title}：{message}"
        self.toast(message, ToastLevel.ERROR)

    def refresh_theme_assets(self) -> None:
        """主题切换后刷新自绘资源（图标、进度条等）。"""
        for child in self.findChildren(QWidget):
            refresh = getattr(child, "refresh_icon", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception:  # noqa: BLE001 - 刷新失败不影响主题切换
                    continue
            else:
                child.update()

    def set_title_text(self, text: str) -> None:
        self.title_label.setText(text)

    def set_subtitle_text(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))

    @staticmethod
    def clear_layout(layout, *, keep: tuple[QWidget, ...] = ()) -> None:  # type: ignore[no-untyped-def]
        """移除布局中的全部控件（``keep`` 中的控件仅隐藏）。"""
        for index in reversed(range(layout.count())):
            item = layout.itemAt(index)
            widget = item.widget()
            if widget is None:
                continue
            if widget in keep:
                widget.setVisible(False)
                continue
            layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
