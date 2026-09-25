"""下载任务卡片。"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QContextMenuEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QWidget

from app.core.common.helpers import (
    format_bytes,
    format_duration,
    format_speed,
    truncate_text,
)
from app.core.download.models import DownloadMode, DownloadStatus, DownloadTask
from app.ui.icons import default_icon_provider
from app.ui.theme import token
from app.ui.widgets.badge import StatusBadge
from app.ui.widgets.buttons import AppButton, IconButton
from app.ui.widgets.cards import Card
from app.ui.widgets.progress_bar import ProgressBar


class TaskAction(StrEnum):
    """任务卡片可触发的操作。"""

    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    RETRY = "retry"
    DETAIL = "detail"
    OPEN_FILE = "open_file"
    OPEN_FOLDER = "open_folder"
    COPY_URL = "copy_url"
    COPY_HASH = "copy_hash"
    DELETE = "delete"


STATUS_TONES: dict[DownloadStatus, str] = {
    DownloadStatus.PENDING: "neutral",
    DownloadStatus.PROBING: "info",
    DownloadStatus.QUEUED: "neutral",
    DownloadStatus.DOWNLOADING: "accent",
    DownloadStatus.RETRYING: "warning",
    DownloadStatus.PAUSED: "warning",
    DownloadStatus.COMPLETED: "success",
    DownloadStatus.FAILED: "error",
    DownloadStatus.CANCELLED: "neutral",
}


class DownloadCard(Card):
    """展示单个下载任务：进度、速度、ETA、能力标记与操作按钮。"""

    action_requested = Signal(str, object)  # task_id, TaskAction

    def __init__(self, task: DownloadTask, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=(16, 14, 16, 14), spacing=10)
        self._icons = default_icon_provider()
        self._task = task
        self._action_signature: tuple[str, str] = ("", "")
        self._build()
        self.update_task(task)

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def _build(self) -> None:
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)

        self._file_icon = QLabel(self)
        self._file_icon.setFixedSize(26, 26)
        self._file_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self._file_icon, 0, Qt.AlignmentFlag.AlignVCenter)

        self._name_label = QLabel("", self)
        self._name_label.setProperty("role", "item-title")
        header.addWidget(self._name_label, 1)

        self._status_badge = StatusBadge("", "neutral", self)
        header.addWidget(self._status_badge, 0)

        self._menu_button = IconButton("more", tooltip="更多操作", parent=self)
        self._menu_button.clicked.connect(self._show_menu)
        header.addWidget(self._menu_button, 0)
        self.body.addLayout(header)

        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(0, 0, 0, 0)
        progress_row.setSpacing(12)
        self._progress = ProgressBar(self)
        progress_row.addWidget(self._progress, 1)
        self._percent_label = QLabel("", self)
        self._percent_label.setProperty("role", "item-percent")
        self._percent_label.setMinimumWidth(52)
        self._percent_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        progress_row.addWidget(self._percent_label, 0)
        self.body.addLayout(progress_row)

        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 0)
        info_row.setSpacing(16)
        self._size_label = QLabel("", self)
        self._size_label.setProperty("role", "item-meta")
        info_row.addWidget(self._size_label, 0)
        self._speed_label = QLabel("", self)
        self._speed_label.setProperty("role", "item-meta")
        info_row.addWidget(self._speed_label, 0)
        self._eta_label = QLabel("", self)
        self._eta_label.setProperty("role", "item-meta")
        info_row.addWidget(self._eta_label, 0)
        info_row.addStretch(1)
        self.body.addLayout(info_row)

        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_row.setSpacing(6)
        self._connection_badge = StatusBadge("", "neutral", self)
        self._range_badge = StatusBadge("", "neutral", self)
        self._resume_badge = StatusBadge("", "neutral", self)
        self._hash_badge = StatusBadge("", "neutral", self)
        for badge in (
            self._connection_badge,
            self._range_badge,
            self._resume_badge,
            self._hash_badge,
        ):
            badge_row.addWidget(badge, 0)
        badge_row.addStretch(1)
        self.body.addLayout(badge_row)

        self._notice_label = QLabel("", self)
        self._notice_label.setProperty("role", "item-notice")
        self._notice_label.setWordWrap(True)
        self._notice_label.setVisible(False)
        self.body.addWidget(self._notice_label)

        self._error_label = QLabel("", self)
        self._error_label.setProperty("role", "item-error")
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)
        self.body.addWidget(self._error_label)

        self._actions_row = QHBoxLayout()
        self._actions_row.setContentsMargins(0, 2, 0, 0)
        self._actions_row.setSpacing(8)
        self._actions_row.addStretch(1)
        self.body.addLayout(self._actions_row)
        self._action_widgets: list[QWidget] = []

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------
    def update_task(self, task: DownloadTask) -> None:
        """按最新任务状态刷新界面。"""
        self._task = task
        self._name_label.setText(truncate_text(task.file_name, 64))
        self._name_label.setToolTip(f"{task.file_name}\n{task.display_path}")

        self._file_icon.setPixmap(
            self._icons.pixmap(
                "download" if task.status is not DownloadStatus.COMPLETED else "check",
                color=token(STATUS_TONES.get(task.status, "accent"), "#3B82F6"),
                size=18,
            )
        )

        self._status_badge.set_state(task.status_label, STATUS_TONES.get(task.status, "neutral"))

        self._progress.set_value(task.progress_percent)
        if task.total_size:
            self._percent_label.setText(f"{task.progress_percent:.1f}%")
            self._size_label.setText(
                f"{format_bytes(task.downloaded)} / {format_bytes(task.total_size)}"
            )
        else:
            self._percent_label.setText("—")
            self._size_label.setText(f"已下载 {format_bytes(task.downloaded)}")

        if task.status in {DownloadStatus.DOWNLOADING, DownloadStatus.RETRYING}:
            self._speed_label.setText(format_speed(task.speed.current))
            eta = task.eta_seconds
            self._eta_label.setText(f"剩余 {format_duration(eta)}" if eta else "")
        else:
            self._speed_label.setText("")
            self._eta_label.setText("")

        connections = task.connection_count or task.active_segments
        self._connection_badge.set_state(f"{connections} 个连接", "info" if task.supports_range else "neutral")
        self._range_badge.set_state(
            "Range ✓" if task.supports_range else "Range ✕",
            "success" if task.supports_range else "warning",
        )
        self._resume_badge.set_state(
            "断点续传 ✓" if task.can_resume else "断点续传 ✕",
            "success" if task.can_resume else "neutral",
        )
        if task.mode is DownloadMode.FALLBACK:
            self._resume_badge.set_state("已回退单连接", "warning")

        verified = task.hash_verified
        if task.status is DownloadStatus.COMPLETED and task.sha256:
            if verified is None:
                self._hash_badge.set_state("SHA-256 已计算", "info")
            elif verified:
                self._hash_badge.set_state("校验通过", "success")
            else:
                self._hash_badge.set_state("校验失败", "error")
            self._hash_badge.setVisible(True)
        else:
            self._hash_badge.setVisible(False)

        self._notice_label.setText(task.notice)
        self._notice_label.setVisible(bool(task.notice))
        self._error_label.setText(task.error_message)
        self._error_label.setVisible(bool(task.error_message))

        signature = (task.status.value, task.mode.value)
        if signature != self._action_signature:
            self._action_signature = signature
            self._rebuild_actions()

    # ------------------------------------------------------------------
    # 操作按钮
    # ------------------------------------------------------------------
    def _rebuild_actions(self) -> None:
        for widget in self._action_widgets:
            self._actions_row.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._action_widgets.clear()

        for action, label, variant, icon in self._actions_for_status(self._task.status):
            button = AppButton(label, variant=variant, icon_name=icon, parent=self)
            button.setMinimumHeight(30)
            button.clicked.connect(lambda _checked=False, a=action: self._emit(a))
            self._actions_row.addWidget(button, 0)
            self._action_widgets.append(button)

    @staticmethod
    def _actions_for_status(
        status: DownloadStatus,
    ) -> list[tuple[TaskAction, str, str, str | None]]:
        detail = (TaskAction.DETAIL, "详情", "secondary", None)
        match status:
            case DownloadStatus.DOWNLOADING | DownloadStatus.RETRYING | DownloadStatus.PROBING:
                return [
                    (TaskAction.PAUSE, "暂停", "secondary", "pause"),
                    (TaskAction.CANCEL, "取消", "ghost", "close"),
                    detail,
                ]
            case DownloadStatus.PAUSED:
                return [
                    (TaskAction.RESUME, "继续", "primary", "play"),
                    (TaskAction.CANCEL, "取消", "ghost", "close"),
                    detail,
                ]
            case DownloadStatus.FAILED:
                return [
                    (TaskAction.RETRY, "重试", "primary", "refresh"),
                    (TaskAction.DELETE, "删除记录", "ghost", "trash"),
                    detail,
                ]
            case DownloadStatus.COMPLETED:
                return [
                    (TaskAction.OPEN_FILE, "打开文件", "secondary", "file"),
                    (TaskAction.OPEN_FOLDER, "打开目录", "ghost", "folder-open"),
                    detail,
                ]
            case DownloadStatus.PENDING | DownloadStatus.QUEUED:
                return [
                    (TaskAction.PAUSE, "暂停", "secondary", "pause"),
                    (TaskAction.CANCEL, "取消", "ghost", "close"),
                    detail,
                ]
            case _:
                return [detail]

    def _emit(self, action: TaskAction) -> None:
        self.action_requested.emit(self._task.task_id, action)

    # ------------------------------------------------------------------
    # 菜单
    # ------------------------------------------------------------------
    def _menu_entries(self) -> list[tuple[TaskAction, str]]:
        entries: list[tuple[TaskAction, str]] = []
        match self._task.status:
            case DownloadStatus.DOWNLOADING | DownloadStatus.RETRYING | DownloadStatus.PROBING:
                entries.append((TaskAction.PAUSE, "暂停"))
            case DownloadStatus.PAUSED | DownloadStatus.FAILED:
                entries.append((TaskAction.RESUME, "继续" if self._task.status is DownloadStatus.PAUSED else "重试"))
            case _:
                pass
        if self._task.status is DownloadStatus.COMPLETED:
            entries.extend(
                [
                    (TaskAction.OPEN_FILE, "打开文件"),
                    (TaskAction.OPEN_FOLDER, "打开所在目录"),
                ]
            )
        else:
            entries.append((TaskAction.CANCEL, "取消任务"))
        entries.append((TaskAction.COPY_URL, "复制下载链接"))
        if self._task.sha256:
            entries.append((TaskAction.COPY_HASH, "复制 SHA-256"))
        entries.append((TaskAction.DETAIL, "查看详情"))
        entries.append((TaskAction.DELETE, "删除任务记录"))
        return entries

    def _show_menu(self) -> None:
        menu = QMenu(self)
        for action, label in self._menu_entries():
            qaction = QAction(label, menu)
            qaction.triggered.connect(lambda _checked=False, a=action: self._emit(a))
            menu.addAction(qaction)
        position = self._menu_button.mapToGlobal(QPoint(0, self._menu_button.height() + 4))
        menu.exec(position)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = QMenu(self)
        for action, label in self._menu_entries():
            qaction = QAction(label, menu)
            qaction.triggered.connect(lambda _checked=False, a=action: self._emit(a))
            menu.addAction(qaction)
        menu.exec(event.globalPos())

    @property
    def task(self) -> DownloadTask:
        return self._task
