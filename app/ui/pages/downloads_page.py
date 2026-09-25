"""下载中心：URL 分析、正在下载、队列、已完成与全局速度统计。"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.core.common.helpers import format_bytes, format_count, format_speed, normalize_url
from app.core.download.models import DownloadTask, ProbeResult
from app.ui.navigation import PageId
from app.ui.pages.base_page import BasePage
from app.ui.widgets.buttons import GhostButton, SecondaryButton
from app.ui.widgets.cards import Card
from app.ui.widgets.dialog import confirm
from app.ui.widgets.download_actions import handle_task_action
from app.ui.widgets.download_card import DownloadCard, TaskAction
from app.ui.widgets.empty_state import EmptyState
from app.ui.widgets.probe_dialog import ProbeDialog
from app.ui.widgets.section import Section
from app.ui.widgets.toast import ToastLevel
from app.ui.widgets.url_input import UrlInputBox

MAX_COMPLETED_CARDS = 30


class DownloadsPage(BasePage):
    """下载中心页面。"""

    page_id = PageId.DOWNLOADS
    title = "下载中心"
    subtitle = "稳定、高效地管理你的下载任务"

    def __init__(self, context, parent: QWidget | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(context, parent)
        self._pending_url = ""
        self._probe_dialog: ProbeDialog | None = None
        self._active_cards: list[DownloadCard] = []
        self._queued_cards: list[DownloadCard] = []
        self._completed_cards: list[DownloadCard] = []
        self._build()
        self._connect_service()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def _build(self) -> None:
        pause_all = SecondaryButton("全部暂停", icon_name="pause", parent=self)
        pause_all.clicked.connect(self._pause_all)
        self.add_action(pause_all)
        resume_all = SecondaryButton("全部继续", icon_name="play", parent=self)
        resume_all.clicked.connect(self._resume_all)
        self.add_action(resume_all)
        clear_finished = GhostButton("清空已完成", icon_name="trash", parent=self)
        clear_finished.clicked.connect(self._clear_finished)
        self.add_action(clear_finished)

        content = self.add_scrollable()

        input_card = Card(padding=(18, 16, 18, 16), spacing=10)
        self.url_box = UrlInputBox(parent=input_card)
        self.url_box.submitted.connect(self.start_download_flow)
        input_card.add(self.url_box)
        hint = QLabel(
            "支持 HTTP / HTTPS、重定向与 Range 分段；可拖拽链接到输入框，或使用 Ctrl+V 粘贴。",
            input_card,
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        input_card.add(hint)
        content.addWidget(input_card)

        self.active_section = Section("正在下载", "实时速度与剩余时间", icon="download", parent=self)
        self.active_empty = EmptyState(
            "暂无正在下载的任务", "粘贴链接开始下载，或到队列中继续暂停的任务。", icon="download"
        )
        self.active_section.set_empty_widget(self.active_empty)
        content.addWidget(self.active_section)

        self.queued_section = Section("队列", "等待可用并发槽位", icon="clock", parent=self)
        self.queued_empty = EmptyState("队列为空", "新任务会自动进入队列并依次开始。", icon="clock")
        self.queued_section.set_empty_widget(self.queued_empty)
        content.addWidget(self.queued_section)

        self.completed_section = Section(
            "已完成", f"最多显示最近 {MAX_COMPLETED_CARDS} 条", icon="check", parent=self
        )
        self.completed_empty = EmptyState(
            "还没有完成的下载", "任务完成后会显示在这里，并自动计算 SHA-256。", icon="check"
        )
        self.completed_section.set_empty_widget(self.completed_empty)
        content.addWidget(self.completed_section)
        content.addStretch(1)

        stats_card = Card(padding=(18, 14, 18, 14), spacing=8)
        stats_row = QHBoxLayout()
        stats_row.setContentsMargins(0, 0, 0, 0)
        stats_row.setSpacing(28)
        self.speed_label, speed_widget = self._make_stat(stats_card, "总下载速度")
        self.active_label, active_widget = self._make_stat(stats_card, "活动任务")
        self.queue_label, queue_widget = self._make_stat(stats_card, "队列 / 暂停")
        self.today_label, today_widget = self._make_stat(stats_card, "今日下载")
        for widget in (speed_widget, active_widget, queue_widget, today_widget):
            stats_row.addWidget(widget)
        stats_row.addStretch(1)
        stats_card.add_layout(stats_row)
        self.body.addWidget(stats_card)

    def _make_stat(self, parent: QWidget, caption: str) -> tuple[QLabel, QWidget]:
        """返回 ``(数值标签, 容器控件)``。"""
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        value = QLabel("—", parent)
        value.setProperty("role", "stat-value-small")
        caption_label = QLabel(caption, parent)
        caption_label.setProperty("role", "stat-caption")
        column.addWidget(value)
        column.addWidget(caption_label)
        holder = QWidget(parent)
        holder.setLayout(column)
        return value, holder

    def _connect_service(self) -> None:
        self.downloads.task_added.connect(self._on_task_event)
        self.downloads.task_updated.connect(self._on_task_event)
        self.downloads.task_removed.connect(self._on_task_removed)
        self.downloads.stats_changed.connect(self._on_stats_changed)
        self.downloads.probe_ready.connect(self._on_probe_ready)
        self.downloads.probe_failed.connect(self._on_probe_failed)

    # ------------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------------
    def on_show(self) -> None:
        self._refresh()
        self._on_stats_changed(None)

    def _refresh(self) -> None:
        self._rebuild_section(
            self.active_section,
            self.downloads.active_tasks(),
            self._active_cards,
            show_speed=True,
        )
        self._rebuild_section(
            self.queued_section,
            self.downloads.queued_tasks(),
            self._queued_cards,
            show_speed=False,
        )
        completed = self.downloads.completed_tasks()[:MAX_COMPLETED_CARDS]
        self._rebuild_section(
            self.completed_section,
            completed,
            self._completed_cards,
            show_speed=False,
        )

    def _rebuild_section(
        self,
        section: Section,
        tasks: list[DownloadTask],
        card_cache: list[DownloadCard],
        *,
        show_speed: bool,
    ) -> None:
        for card in card_cache:
            section.content.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        card_cache.clear()
        for task in tasks:
            card = DownloadCard(task, self)
            card.action_requested.connect(self._on_task_action)
            section.content.addWidget(card)
            card_cache.append(card)
        section.set_count(len(tasks))
        if section._empty_widget is not None:  # noqa: SLF001 - 同一模块内受控访问
            section._empty_widget.setVisible(not tasks)
        section.set_subtitle(self._section_subtitle(tasks, show_speed=show_speed))

    @staticmethod
    def _section_subtitle(tasks: list[DownloadTask], *, show_speed: bool) -> str:
        if not tasks:
            return ""
        total = sum(task.speed.current for task in tasks) if show_speed else 0
        if show_speed and total > 0:
            return f"{len(tasks)} 个任务 · {format_speed(total)}"
        return f"{len(tasks)} 个任务"

    def _on_stats_changed(self, _stats: object) -> None:
        stats = self.downloads.stats()
        self.speed_label.setText(format_speed(stats.total_speed))
        self.active_label.setText(format_count(stats.active_count))
        self.queue_label.setText(f"{stats.queued_count} / {stats.paused_count}")
        self.today_label.setText(format_bytes(stats.total_downloaded_today))

    def _on_task_event(self, _task: object) -> None:
        self._refresh()

    def _on_task_removed(self, _task_id: str) -> None:
        self._refresh()

    # ------------------------------------------------------------------
    # 下载流程
    # ------------------------------------------------------------------
    def start_download_flow(self, url: str) -> None:
        """由首页或本页触发：填入链接并开始分析。"""
        normalized = normalize_url(url)
        if normalized is None:
            self.toast("链接格式不正确，请输入以 http:// 或 https:// 开头的地址。", ToastLevel.WARNING)
            return
        self.url_box.set_text(normalized)
        self._pending_url = normalized
        try:
            self.downloads.probe(normalized, self.settings.download_dir)
            self.toast("正在分析链接…", ToastLevel.INFO)
        except Exception as exc:  # noqa: BLE001 - 统一转换为用户提示
            self.report_error(exc, title="无法开始分析")

    def _on_probe_ready(self, probe: ProbeResult) -> None:
        """显示非阻塞的分析结果对话框（不冻结界面）。"""
        dialog = ProbeDialog(probe, settings=self.settings, parent=self)
        self._probe_dialog = dialog
        dialog.accepted.connect(lambda: self._start_from_dialog(dialog, probe))
        dialog.finished.connect(lambda _result: setattr(self, "_probe_dialog", None))
        dialog.open()

    def _start_from_dialog(self, dialog: ProbeDialog, probe: ProbeResult) -> None:
        """用户在“下载分析”对话框中确认后创建任务。"""
        try:
            task_id = self.downloads.start(
                probe,
                save_dir=dialog.save_dir,
                expected_sha256=dialog.expected_sha256,
            )
        except Exception as exc:  # noqa: BLE001
            self.report_error(exc, title="无法开始下载")
            return
        self.toast(f"已加入下载队列：{probe.file_name}", ToastLevel.SUCCESS)
        self._pending_url = ""
        _ = task_id

    def _on_probe_failed(self, message: str) -> None:
        self.toast(message, ToastLevel.ERROR)

    # ------------------------------------------------------------------
    # 批量操作
    # ------------------------------------------------------------------
    def _on_task_action(self, task_id: str, action: TaskAction) -> None:
        handle_task_action(self, task_id, action)

    def _pause_all(self) -> None:
        try:
            self.downloads.pause_all()
            self.toast("已请求暂停全部活动任务。", ToastLevel.INFO)
        except Exception as exc:  # noqa: BLE001
            self.report_error(exc, title="暂停失败")

    def _resume_all(self) -> None:
        try:
            self.downloads.resume_all()
            self.toast("已请求继续所有暂停任务。", ToastLevel.INFO)
        except Exception as exc:  # noqa: BLE001
            self.report_error(exc, title="继续失败")

    def _clear_finished(self) -> None:
        finished = self.downloads.completed_tasks()
        if not finished:
            self.toast("没有可清理的任务记录。", ToastLevel.INFO)
            return
        if not confirm(
            self,
            "清空已完成任务",
            f"将移除 {len(finished)} 条任务记录（不会删除已下载的文件）。",
            confirm_text="清空记录",
            danger=True,
        ):
            return
        removed = 0
        for task in finished:
            self.downloads.delete_task(task.task_id, delete_files=False)
            removed += 1
        self.toast(f"已清理 {removed} 条任务记录。", ToastLevel.SUCCESS)
