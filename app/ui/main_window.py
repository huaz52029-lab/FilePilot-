"""主窗口：标题栏 + 侧边导航 + 页面栈 + 状态栏 + Toast。"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtGui import QCloseEvent, QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.common.constants import (
    APP_NAME,
    SETTINGS_APP,
    SETTINGS_ORG,
    TITLE_BAR_HEIGHT,
    WINDOW_DEFAULT_HEIGHT,
    WINDOW_DEFAULT_WIDTH,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
)
from app.core.common.helpers import format_speed, normalize_url
from app.core.common.logger import get_logger
from app.core.download.models import DownloadStats, DownloadStatus
from app.services.app_context import AppContext
from app.ui.frameless import FramelessMainWindow
from app.ui.icons import default_icon_provider
from app.ui.navigation import PageId
from app.ui.pages.about_page import AboutPage
from app.ui.pages.base_page import BasePage
from app.ui.pages.downloads_page import DownloadsPage
from app.ui.pages.duplicates_page import DuplicatesPage
from app.ui.pages.history_page import HistoryPage
from app.ui.pages.home_page import HomePage
from app.ui.pages.organizer_page import OrganizerPage
from app.ui.pages.settings_page import SettingsPage
from app.ui.pages.storage_page import StoragePage
from app.ui.sidebar import Sidebar
from app.ui.theme import ThemeManager, set_active_theme
from app.ui.title_bar import TitleBar
from app.ui.widgets.dialog import confirm
from app.ui.widgets.status_bar import StatusBar
from app.ui.widgets.toast import ToastHost, ToastLevel

_log = get_logger("ui.main_window")


class MainWindow(FramelessMainWindow):
    """FilePilot 主窗口。"""

    def __init__(
        self,
        context: AppContext,
        app: QApplication,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.context = context
        self.app = app
        self.icons = default_icon_provider()
        self._qsettings = QSettings(SETTINGS_ORG, SETTINGS_APP)

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.resize(WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT)

        central = QWidget(self)
        central.setObjectName("Central")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.title_bar = TitleBar(self, central)
        outer.addWidget(self.title_bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = Sidebar(parent=central)
        body.addWidget(self.sidebar)

        right_column = QVBoxLayout()
        right_column.setContentsMargins(0, 0, 0, 0)
        right_column.setSpacing(0)
        self.stack = QStackedWidget(central)
        right_column.addWidget(self.stack, 1)
        self.status_bar = StatusBar(central)
        right_column.addWidget(self.status_bar)
        body.addLayout(right_column, 1)
        outer.addLayout(body, 1)

        # 主题
        self.theme_manager = ThemeManager(app, context.settings, self)
        set_active_theme(self.theme_manager)
        self.icons.bind_theme(self.theme_manager)
        self.theme_manager.apply()

        # 页面
        self.pages: dict[PageId, BasePage] = {}
        self._create_pages()
        # 主题信号必须在页面创建之后连接，否则首次应用主题时会访问不存在的页面
        self.theme_manager.theme_changed.connect(self._on_theme_changed)

        self.toast_host = ToastHost(central)

        # 信号
        self.sidebar.page_selected.connect(self._switch_page)
        self.context.downloads.stats_changed.connect(self._on_download_stats)
        self.context.downloads.notice.connect(self.show_toast)
        for page in self.pages.values():
            page.toast_requested.connect(self.show_toast)
            page.navigate_requested.connect(self._on_navigate_requested)

        self._restore_window_state()
        self.context.downloads.ready.connect(self._on_downloads_ready)
        self._load_downloads()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def _create_pages(self) -> None:
        page_types: tuple[tuple[PageId, type[BasePage]], ...] = (
            (PageId.HOME, HomePage),
            (PageId.DOWNLOADS, DownloadsPage),
            (PageId.ORGANIZER, OrganizerPage),
            (PageId.STORAGE, StoragePage),
            (PageId.DUPLICATES, DuplicatesPage),
            (PageId.HISTORY, HistoryPage),
            (PageId.SETTINGS, SettingsPage),
            (PageId.ABOUT, AboutPage),
        )
        for page_id, page_type in page_types:
            page = page_type(self.context, self)
            self.pages[page_id] = page
            self.stack.addWidget(page)

        last_page = self._resolve_page(self.context.settings.last_page)
        self._switch_page(last_page)

    @staticmethod
    def _resolve_page(value: str) -> PageId:
        try:
            return PageId(value)
        except ValueError:
            return PageId.HOME

    # ------------------------------------------------------------------
    # 导航
    # ------------------------------------------------------------------
    def _switch_page(self, page_id: PageId | str) -> None:
        try:
            resolved = page_id if isinstance(page_id, PageId) else PageId(page_id)
        except ValueError:
            resolved = PageId.HOME
        page = self.pages.get(resolved)
        if page is None:
            return
        current = self.stack.currentWidget()
        if current is page:
            page.on_show()
            return
        if isinstance(current, BasePage):
            current.on_hide()
        self.stack.setCurrentWidget(page)
        self.sidebar.set_current(resolved)
        page.on_show()
        self.context.settings.last_page = resolved.value
        self.status_bar.show_message(f"{resolved.label} · 就绪")

    def _on_navigate_requested(self, page_id: object) -> None:
        self._switch_page(page_id)  # type: ignore[arg-type]

    def start_download_flow(self, url: str) -> None:
        """首页快速下载入口：切换到下载中心并开始分析。"""
        self._switch_page(PageId.DOWNLOADS)
        page = self.pages.get(PageId.DOWNLOADS)
        handler = getattr(page, "start_download_flow", None)
        if callable(handler):
            handler(url)

    # ------------------------------------------------------------------
    # 状态同步
    # ------------------------------------------------------------------
    def _on_download_stats(self, stats: DownloadStats) -> None:
        pending = stats.active_count + stats.queued_count
        self.sidebar.set_download_badge(pending)
        parts: list[str] = []
        if stats.active_count:
            parts.append(f"{stats.active_count} 个任务下载中")
        if stats.total_speed > 0:
            parts.append(format_speed(stats.total_speed))
        if stats.queued_count:
            parts.append(f"队列 {stats.queued_count}")
        self.status_bar.set_download_summary(" · ".join(parts))

    def show_toast(self, message: str, level: str = "info") -> None:
        """统一 Toast 入口。"""
        try:
            resolved = ToastLevel(level)
        except ValueError:
            resolved = ToastLevel.INFO
        timeout = 6000 if resolved in {ToastLevel.ERROR, ToastLevel.WARNING} else 3800
        self.toast_host.show_message(message, resolved, timeout_ms=timeout)
        self.status_bar.show_message(message)

    def _load_downloads(self) -> None:
        """启动后台下载引擎（未完成任务由引擎载入并通知界面）。"""
        try:
            self.context.start_downloads()
        except Exception as exc:  # noqa: BLE001 - 启动阶段不能中断
            _log.error("启动下载引擎失败：%s", exc)
            self.show_toast("下载引擎启动失败，请查看日志。", ToastLevel.ERROR.value)

    def _on_downloads_ready(self) -> None:
        """下载引擎就绪：提示未完成任务，并按设置自动恢复**被中断**的任务。

        用户主动“暂停”的任务不会被自动恢复，重启后仍显示为已暂停，
        需要用户手动点击“继续”。
        """
        unfinished = [
            task for task in self.context.downloads.tasks() if not task.status.is_final
        ]
        if not unfinished:
            return
        interrupted = [task for task in unfinished if task.interrupted]
        paused = [
            task
            for task in unfinished
            if task.status is DownloadStatus.PAUSED and not task.interrupted
        ]
        if self.context.settings.notify_restored:
            parts: list[str] = []
            if interrupted:
                parts.append(f"{len(interrupted)} 个被中断的任务")
            if paused:
                parts.append(f"{len(paused)} 个暂停的任务")
            message = "已恢复 " + "、".join(parts) if parts else "已恢复未完成的任务"
            self.show_toast(f"{message}，可在下载中心查看。", ToastLevel.INFO.value)
        if not self.context.settings.auto_resume or not interrupted:
            return
        for task in interrupted:
            try:
                self.context.downloads.resume(task.task_id)
            except Exception as exc:  # noqa: BLE001
                _log.warning("自动恢复任务 %s 失败：%s", task.task_id, exc)

    # ------------------------------------------------------------------
    # 主题与窗口状态
    # ------------------------------------------------------------------
    def _on_theme_changed(self, _effective: str) -> None:
        self.icons.bind_theme(self.theme_manager)
        self.title_bar.refresh_icons()
        self.sidebar.refresh_icons()
        for page in getattr(self, "pages", {}).values():
            page.refresh_theme_assets()

    def _restore_window_state(self) -> None:
        geometry = self._qsettings.value("ui/geometry", QByteArray())
        if isinstance(geometry, QByteArray) and not geometry.isEmpty():
            self.restoreGeometry(geometry)
        else:
            self.resize(WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT)
            screen = self.app.primaryScreen()
            if screen is not None:
                center = screen.availableGeometry().center()
                self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)
        if self._qsettings.value("ui/maximized", "0") == "1":
            self.showMaximized()
        self.title_bar.set_maximized(self.isMaximized())

    def _save_window_state(self) -> None:
        self._qsettings.setValue("ui/geometry", self.saveGeometry())
        self._qsettings.setValue("ui/maximized", "1" if self.isMaximized() else "0")
        self._qsettings.sync()

    def changeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            self.title_bar.set_maximized(self.isMaximized())

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if hasattr(self, "toast_host") and self.toast_host is not None:
            top = self.title_bar.height() or TITLE_BAR_HEIGHT
            left = self.sidebar.width()
            bottom = self.status_bar.height()
            self.toast_host.setGeometry(
                left,
                top,
                max(0, self.width() - left),
                max(0, self.height() - top - bottom),
            )

    # ------------------------------------------------------------------
    # 键盘与关闭
    # ------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.StandardKey.Paste):
            focus = self.app.focusWidget()
            from PySide6.QtWidgets import QAbstractSpinBox, QLineEdit, QTextEdit

            is_text_input = isinstance(focus, (QLineEdit, QTextEdit, QAbstractSpinBox))
            if not is_text_input:
                clipboard_text = QApplication.clipboard().text().strip()
                url = normalize_url(clipboard_text)
                if url is not None:
                    self.start_download_flow(url)
                    return
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        active = [
            task
            for task in self.context.downloads.tasks()
            if task.status.is_active or task.status.value == "queued"
        ]
        if active and not confirm(
            self,
            "退出 FilePilot",
            f"仍有 {len(active)} 个下载任务正在进行。\n"
            "退出后任务会暂停，下次启动时可以继续（断点续传）。",
            confirm_text="退出",
            cancel_text="继续下载",
            danger=True,
        ):
            event.ignore()
            return
        self._save_window_state()
        super().closeEvent(event)
