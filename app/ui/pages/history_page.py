"""历史记录页面：下载、整理、校验与文件操作。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLineEdit, QMenu, QWidget

from app.core.common.exceptions import to_user_message
from app.core.common.helpers import (
    format_bytes,
    format_datetime,
    format_friendly_time,
    open_in_explorer,
    open_path,
    truncate_text,
)
from app.core.storage.models import (
    HISTORY_KIND_LABELS,
    HistoryEvent,
    HistoryKind,
    HistoryStatus,
)
from app.ui.navigation import PageId
from app.ui.pages.base_page import BasePage
from app.ui.theme import token
from app.ui.widgets.badge import StatusBadge
from app.ui.widgets.buttons import DangerButton, SecondaryButton
from app.ui.widgets.cards import Card
from app.ui.widgets.dialog import confirm
from app.ui.widgets.empty_state import EmptyState
from app.ui.widgets.inputs import ComboRow
from app.ui.widgets.section import Section
from app.ui.widgets.toast import ToastLevel

MAX_EVENTS = 300

KIND_ICONS: dict[HistoryKind, str] = {
    HistoryKind.DOWNLOAD: "download",
    HistoryKind.ORGANIZE: "folder",
    HistoryKind.HASH: "hash",
    HistoryKind.FILE: "file",
}

KIND_TONES: dict[HistoryKind, str] = {
    HistoryKind.DOWNLOAD: "accent",
    HistoryKind.ORGANIZE: "info",
    HistoryKind.HASH: "success",
    HistoryKind.FILE: "neutral",
}


class HistoryPage(BasePage):
    """历史记录页面。"""

    page_id = PageId.HISTORY
    title = "历史记录"
    subtitle = "下载、整理、校验与文件操作的完整轨迹"

    def __init__(self, context, parent: QWidget | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(context, parent)
        self._rows: list[QWidget] = []
        self._events: list[HistoryEvent] = []
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        refresh = SecondaryButton("刷新", icon_name="refresh", parent=self)
        refresh.clicked.connect(lambda: self.reload_events())
        self.add_action(refresh)
        clear = DangerButton("清空历史", icon_name="trash", parent=self)
        clear.clicked.connect(self._clear_history)
        self.add_action(clear)

        content = self.add_scrollable()

        filter_card = Card(padding=(18, 14, 18, 14), spacing=12)
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(12)
        self.search_edit = QLineEdit(filter_card)
        self.search_edit.setPlaceholderText("搜索文件名、路径或说明…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(lambda _text: self.reload_events())
        search_row.addWidget(self.search_edit, 1)
        filter_card.add_layout(search_row)

        self.kind_row = ComboRow(
            "记录类型",
            [
                ("all", "全部"),
                (HistoryKind.DOWNLOAD.value, "下载"),
                (HistoryKind.ORGANIZE.value, "整理"),
                (HistoryKind.HASH.value, "校验"),
                (HistoryKind.FILE.value, "文件操作"),
            ],
            parent=filter_card,
        )
        self.kind_row.changed.connect(lambda _value: self.reload_events())
        filter_card.add(self.kind_row)
        content.addWidget(filter_card)

        self.section = Section("记录", f"最多显示最近 {MAX_EVENTS} 条", icon="clock", parent=self)
        self.empty = EmptyState(
            "暂无历史记录",
            "完成下载、整理或校验后，记录会自动出现在这里。",
            icon="clock",
        )
        self.section.set_empty_widget(self.empty)
        content.addWidget(self.section)
        content.addStretch(1)

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        self.reload_events()

    def reload_events(self) -> None:
        """按当前筛选条件重新读取历史。"""
        kinds: tuple[HistoryKind, ...] | None = None
        selected = self.kind_row.value()
        if selected and selected != "all":
            kinds = (HistoryKind(selected),)
        try:
            events = self.history.list_events(
                kinds=kinds,
                query=self.search_edit.text().strip(),
                limit=MAX_EVENTS,
            )
        except Exception as exc:  # noqa: BLE001 - 数据库异常转为提示
            self.report_error(exc, title="读取历史失败")
            return
        self._events = events
        self._render_events()

    def _render_events(self) -> None:
        for widget in self._rows:
            self.section.content.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._rows.clear()

        for event in self._events:
            row = self._make_row(event)
            self.section.content.addWidget(row)
            self._rows.append(row)
        self.section.set_count(len(self._events))
        self.empty.setVisible(not self._events)

    def _make_row(self, event: HistoryEvent) -> QWidget:
        card = Card(self, padding=(14, 12, 14, 12), spacing=6, hoverable=True)
        card.setToolTip(event.path or event.detail or event.title)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)

        icon = QLabel(card)
        icon.setPixmap(
            self.icons.pixmap(
                KIND_ICONS.get(event.kind, "file"),
                color=token(KIND_TONES.get(event.kind, "neutral")),
                size=18,
            )
        )
        icon.setFixedSize(18, 18)
        top.addWidget(icon, 0)

        title = QLabel(truncate_text(event.title, 60), card)
        title.setProperty("role", "item-title")
        top.addWidget(title, 1)

        top.addWidget(StatusBadge(HISTORY_KIND_LABELS[event.kind], KIND_TONES.get(event.kind, "neutral")), 0)
        tone = {
            HistoryStatus.SUCCESS: "success",
            HistoryStatus.WARNING: "warning",
            HistoryStatus.FAILED: "error",
        }[event.status]
        top.addWidget(StatusBadge(event.status.label, tone), 0)
        card.add_layout(top)

        meta_bits = [format_friendly_time(event.created_at)]
        if event.size:
            meta_bits.append(format_bytes(event.size))
        if event.detail:
            meta_bits.append(truncate_text(event.detail, 60))
        meta = QLabel("  ·  ".join(meta_bits), card)
        meta.setProperty("role", "item-meta")
        card.add(meta)

        if event.path:
            path_label = QLabel(truncate_text(event.path, 100), card)
            path_label.setProperty("role", "item-subtitle")
            card.add(path_label)

        time_label = QLabel(format_datetime(event.created_at, with_seconds=True), card)
        time_label.setProperty("role", "item-meta")
        card.add(time_label)

        card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        card.customContextMenuRequested.connect(
            lambda position, e=event, c=card: self._show_context_menu(c, position, e)
        )
        return card

    # ------------------------------------------------------------------
    def _show_context_menu(self, widget: QWidget, position, event: HistoryEvent) -> None:  # type: ignore[no-untyped-def]
        menu = QMenu(widget)
        path = Path(event.path) if event.path else None
        if path is not None and path.exists():
            open_file = QAction("打开文件", menu)
            open_file.triggered.connect(lambda: open_path(path))
            menu.addAction(open_file)
            open_folder = QAction("打开所在目录", menu)
            open_folder.triggered.connect(lambda: open_in_explorer(path, select=True))
            menu.addAction(open_folder)
        if event.path:
            copy_path = QAction("复制路径", menu)
            copy_path.triggered.connect(
                lambda: (QApplication.clipboard().setText(event.path), self.toast("路径已复制。", ToastLevel.SUCCESS))
            )
            menu.addAction(copy_path)
        if event.url:
            copy_url = QAction("复制下载链接", menu)
            copy_url.triggered.connect(
                lambda: (QApplication.clipboard().setText(event.url), self.toast("链接已复制。", ToastLevel.SUCCESS))
            )
            menu.addAction(copy_url)
        if menu.actions():
            menu.exec(widget.mapToGlobal(position))

    def _clear_history(self) -> None:
        if not confirm(
            self,
            "清空历史记录",
            "所有历史记录将被删除（不会删除任何文件），此操作无法撤销。",
            confirm_text="清空记录",
            danger=True,
        ):
            return
        try:
            count = self.history.clear()
        except Exception as exc:  # noqa: BLE001
            self.report_error(to_user_message(exc), title="清空失败")
            return
        self.toast(f"已清空 {count} 条历史记录。", ToastLevel.SUCCESS)
        self.reload_events()
