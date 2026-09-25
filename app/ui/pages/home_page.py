"""首页（Dashboard）：快速下载、统计卡、正在下载、最近文件、空间概览。"""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.core.common.helpers import (
    format_bytes,
    format_count,
    format_friendly_time,
    open_in_explorer,
    truncate_text,
)
from app.core.files.models import DiskUsageInfo, ScanStats
from app.core.files.scanner import disk_usage_info, quick_directory_stats
from app.core.storage.models import HISTORY_KIND_LABELS, HistoryStatus
from app.ui.navigation import PageId
from app.ui.pages.base_page import BasePage
from app.ui.theme import token
from app.ui.widgets.badge import StatusBadge
from app.ui.widgets.buttons import GhostButton, SecondaryButton
from app.ui.widgets.cards import Card, SectionHeader
from app.ui.widgets.download_card import DownloadCard, TaskAction
from app.ui.widgets.empty_state import EmptyState
from app.ui.widgets.progress_bar import ProgressBar
from app.ui.widgets.stat_card import StatCard
from app.ui.widgets.toast import ToastLevel
from app.ui.widgets.url_input import UrlInputBox

MAX_ACTIVE_CARDS = 3
MAX_RECENT_FILES = 5
SCAN_INTERVAL_SECONDS = 60.0


class HomePage(BasePage):
    """Dashboard 页面。"""

    page_id = PageId.HOME

    def __init__(self, context, parent: QWidget | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(context, parent)
        self.set_title_text("欢迎使用 FilePilot")
        self.set_subtitle_text(self._welcome_text())
        self._library_files = 0
        self._library_size = 0
        self._scan_running = False
        self._last_scan_at = 0.0
        self._disk_info: DiskUsageInfo | None = None
        self._download_widgets: list[QWidget] = []
        self._build()
        self._connect_service()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def _build(self) -> None:
        content = self.add_scrollable()

        quick_card = Card(padding=(20, 18, 20, 18), spacing=12, shadow=True)
        quick_card.add(
            SectionHeader(
                "快速下载",
                "先分析服务器能力，再决定使用单连接还是多连接分段下载",
                icon="download",
                parent=quick_card,
            )
        )
        self.url_box = UrlInputBox(parent=quick_card)
        self.url_box.submitted.connect(self._on_quick_download)
        quick_card.add(self.url_box)
        hint = QLabel(
            "支持 Ctrl+V 粘贴链接、直接把链接拖到输入框；开始前会检查磁盘空间与服务器是否支持 Range。",
            quick_card,
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        quick_card.add(hint)
        content.addWidget(quick_card)

        stats_grid = QGridLayout()
        stats_grid.setContentsMargins(0, 0, 0, 0)
        stats_grid.setHorizontalSpacing(16)
        stats_grid.setVerticalSpacing(16)
        self.files_card = StatCard("文件总数", "0", unit="个", caption="下载目录", icon="file")
        self.size_card = StatCard("总空间占用", "0 B", caption="下载目录", icon="chart", tone="info")
        self.active_card = StatCard(
            "当前下载", "0", unit="任务", caption="正在下载", icon="download", tone="accent"
        )
        self.today_card = StatCard(
            "今日下载", "0 B", caption="完成的任务", icon="check", tone="success"
        )
        for column, card in enumerate(
            (self.files_card, self.size_card, self.active_card, self.today_card)
        ):
            stats_grid.addWidget(card, 0, column)
            stats_grid.setColumnStretch(column, 1)
        content.addLayout(stats_grid)

        download_section = Card(padding=(20, 18, 20, 18), spacing=14)
        download_header = SectionHeader("正在下载", "", icon="download", parent=download_section)
        view_all = GhostButton("查看全部", icon_name="chevron-right", parent=download_section)
        view_all.clicked.connect(lambda: self.navigate_requested.emit(PageId.DOWNLOADS))
        download_header.add_action(view_all)
        download_section.add(download_header)
        self.download_container = QVBoxLayout()
        self.download_container.setContentsMargins(0, 0, 0, 0)
        self.download_container.setSpacing(10)
        download_section.add_layout(self.download_container)
        self.download_empty = EmptyState(
            "当前没有下载任务",
            "在上方粘贴链接即可开始一次稳定下载。",
            icon="download",
            parent=download_section,
        )
        self.download_container.addWidget(self.download_empty)
        content.addWidget(download_section)

        recent_section = Card(padding=(20, 18, 20, 18), spacing=14)
        recent_header = SectionHeader(
            "最近文件", "来自下载与整理历史", icon="clock", parent=recent_section
        )
        history_button = GhostButton("历史记录", icon_name="chevron-right", parent=recent_section)
        history_button.clicked.connect(lambda: self.navigate_requested.emit(PageId.HISTORY))
        recent_header.add_action(history_button)
        recent_section.add(recent_header)
        self.recent_container = QVBoxLayout()
        self.recent_container.setContentsMargins(0, 0, 0, 0)
        self.recent_container.setSpacing(8)
        recent_section.add_layout(self.recent_container)
        self.recent_empty = EmptyState(
            "还没有文件记录",
            "完成一次下载或整理后，这里会显示最近的变动。",
            icon="clock",
            parent=recent_section,
        )
        self.recent_container.addWidget(self.recent_empty)
        content.addWidget(recent_section)

        storage_section = Card(padding=(20, 18, 20, 18), spacing=14)
        storage_header = SectionHeader("空间概览", "", icon="chart", parent=storage_section)
        storage_button = GhostButton("空间分析", icon_name="chevron-right", parent=storage_section)
        storage_button.clicked.connect(lambda: self.navigate_requested.emit(PageId.STORAGE))
        storage_header.add_action(storage_button)
        storage_section.add(storage_header)

        self.disk_title = QLabel("正在读取磁盘信息…", storage_section)
        self.disk_title.setProperty("role", "item-title")
        storage_section.add(self.disk_title)
        self.disk_bar = ProgressBar(storage_section, height=10)
        storage_section.add(self.disk_bar)
        self.disk_detail = QLabel("", storage_section)
        self.disk_detail.setProperty("role", "item-meta")
        storage_section.add(self.disk_detail)
        self.library_detail = QLabel("", storage_section)
        self.library_detail.setProperty("role", "item-meta")
        self.library_detail.setWordWrap(True)
        storage_section.add(self.library_detail)

        open_dir_button = SecondaryButton("打开下载目录", icon_name="folder-open", parent=storage_section)
        open_dir_button.clicked.connect(self._open_download_dir)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 4, 0, 0)
        footer.addWidget(open_dir_button, 0)
        footer.addStretch(1)
        storage_section.add_layout(footer)
        content.addWidget(storage_section)
        content.addStretch(1)

    def _connect_service(self) -> None:
        self.downloads.task_added.connect(self._on_task_changed)
        self.downloads.task_updated.connect(self._on_task_changed)
        self.downloads.task_removed.connect(self._on_task_removed)
        self.downloads.stats_changed.connect(self._on_stats_changed)

    # ------------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------------
    def on_show(self) -> None:
        self.set_subtitle_text(self._welcome_text())
        self._refresh_stat_cards()
        self._refresh_downloads()
        self._refresh_recent()
        self._refresh_disk()
        self._schedule_library_scan()

    def _welcome_text(self) -> str:
        today = datetime.now()
        weekday = "一二三四五六日"[today.weekday()]
        return (
            f"今天是 {today:%Y 年 %m 月 %d 日} 星期{weekday} · "
            "本地优先的文件管理与稳定下载"
        )

    def _refresh_stat_cards(self) -> None:
        stats = self.downloads.stats()
        self.active_card.set_value(format_count(stats.active_count), unit="任务")
        caption_bits: list[str] = []
        if stats.queued_count:
            caption_bits.append(f"排队 {stats.queued_count}")
        if stats.paused_count:
            caption_bits.append(f"暂停 {stats.paused_count}")
        self.active_card.set_caption(" · ".join(caption_bits) or "正在下载")
        self.today_card.set_value(format_bytes(stats.total_downloaded_today))
        self.today_card.set_caption(f"已完成 {stats.completed_count} 个任务")
        self.files_card.set_value(format_count(self._library_files), unit="个")
        self.size_card.set_value(format_bytes(self._library_size))

    def _refresh_downloads(self) -> None:
        tasks = self.downloads.active_tasks()[:MAX_ACTIVE_CARDS]
        for widget in self._download_widgets:
            self.download_container.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._download_widgets.clear()

        self.download_empty.setVisible(not tasks)
        for index, task in enumerate(tasks):
            card = DownloadCard(task, self)
            card.action_requested.connect(self._on_task_action)
            self.download_container.insertWidget(index, card)
            self._download_widgets.append(card)

    def _refresh_recent(self) -> None:
        events = self.history.latest(MAX_RECENT_FILES)
        self.clear_layout(self.recent_container, keep=(self.recent_empty,))
        self.recent_empty.setVisible(not events)
        for event in events:
            row = Card(self, padding=(14, 10, 14, 10), spacing=4)
            top = QHBoxLayout()
            top.setContentsMargins(0, 0, 0, 0)
            title = QLabel(truncate_text(event.title, 52), row)
            title.setProperty("role", "item-title")
            top.addWidget(title, 1)
            tone = {
                HistoryStatus.SUCCESS: "success",
                HistoryStatus.WARNING: "warning",
                HistoryStatus.FAILED: "error",
            }[event.status]
            top.addWidget(StatusBadge(HISTORY_KIND_LABELS[event.kind], tone), 0)
            row.add_layout(top)
            meta_bits = [format_friendly_time(event.created_at)]
            if event.size:
                meta_bits.append(format_bytes(event.size))
            if event.detail:
                meta_bits.append(truncate_text(event.detail, 40))
            meta = QLabel("  ·  ".join(meta_bits), row)
            meta.setProperty("role", "item-meta")
            row.add(meta)
            tooltip = event.path or event.detail
            if tooltip:
                row.setToolTip(tooltip)
            self.recent_container.addWidget(row)

    def _refresh_disk(self) -> None:
        download_dir = self.settings.download_dir
        try:
            self._disk_info = disk_usage_info(download_dir)
        except OSError as exc:
            self.disk_title.setText("无法读取磁盘信息")
            self.disk_detail.setText(str(exc))
            self.disk_bar.set_value(0)
            return
        info = self._disk_info
        drive = download_dir.anchor or str(download_dir)
        self.disk_title.setText(f"{drive} 已使用 {info.percent_used:.1f}%")
        self.disk_bar.set_value(info.percent_used)
        percent = info.percent_used
        self.disk_bar.set_color(
            token("error") if percent >= 95 else token("warning") if percent >= 85 else token("accent")
        )
        self.disk_detail.setText(
            f"总空间 {format_bytes(info.total)} · 已用 {format_bytes(info.used)} · "
            f"可用 {format_bytes(info.free)}"
        )
        self.library_detail.setText(
            f"下载目录：{download_dir}（{format_count(self._library_files)} 个文件，"
            f"{format_bytes(self._library_size)}）"
        )

    # ------------------------------------------------------------------
    # 后台扫描（不阻塞 UI）
    # ------------------------------------------------------------------
    def _schedule_library_scan(self, *, force: bool = False) -> None:
        if self._scan_running:
            return
        if not force and time.monotonic() - self._last_scan_at < SCAN_INTERVAL_SECONDS:
            return
        target = self.settings.download_dir
        if not target.exists():
            self.files_card.set_value("—")
            self.size_card.set_value("—")
            self.files_card.set_caption("下载目录不存在")
            return
        self._scan_running = True
        self.files_card.set_caption("正在统计…")
        self.context.runner.submit(
            quick_directory_stats,
            target,
            on_result=self._on_library_scan_done,
            on_error=lambda message, _detail: self._on_library_scan_failed(message),
            on_finished=self._on_library_scan_finished,
        )

    def _on_library_scan_done(self, stats: ScanStats) -> None:
        self._library_files = stats.file_count
        self._library_size = stats.total_size
        self._last_scan_at = time.monotonic()
        self.files_card.set_caption("下载目录")
        self._refresh_stat_cards()
        self._refresh_disk()

    def _on_library_scan_failed(self, message: str) -> None:
        self.files_card.set_caption(message)

    def _on_library_scan_finished(self) -> None:
        self._scan_running = False

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _on_quick_download(self, url: str) -> None:
        self.navigate_requested.emit(PageId.DOWNLOADS)
        window = self.window()
        handler = getattr(window, "start_download_flow", None)
        if callable(handler):
            handler(url)
        else:  # pragma: no cover - 兜底
            self.toast("请前往下载中心继续操作。", ToastLevel.INFO)

    def _on_task_changed(self, _task: object) -> None:
        self._refresh_downloads()
        self._refresh_stat_cards()

    def _on_task_removed(self, _task_id: str) -> None:
        self._refresh_downloads()
        self._refresh_stat_cards()

    def _on_stats_changed(self, _stats: object) -> None:
        self._refresh_stat_cards()

    def _on_task_action(self, task_id: str, action: TaskAction) -> None:
        from app.ui.widgets.download_actions import handle_task_action

        handle_task_action(self, task_id, action)

    def _open_download_dir(self) -> None:
        target = self.settings.download_dir
        try:
            target.mkdir(parents=True, exist_ok=True)
            open_in_explorer(target)
        except OSError as exc:
            self.report_error(exc, title="无法打开下载目录")
