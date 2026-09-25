"""下载任务详情对话框。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QGridLayout, QLabel, QWidget

from app.core.common.helpers import (
    format_bytes,
    format_datetime,
    format_duration,
    format_speed,
)
from app.core.download.models import DownloadSegment, DownloadTask
from app.ui.widgets.buttons import GhostButton, SecondaryButton
from app.ui.widgets.dialog import AppDialog


class DownloadDetailDialog(AppDialog):
    """展示单个任务的完整信息（含 SHA-256 与分段状态）。"""

    def __init__(self, task: DownloadTask, parent: QWidget | None = None) -> None:
        super().__init__(f"下载详情 · {task.file_name}", width=560, parent=parent)
        self._task = task
        self.set_subtitle(task.display_path)
        self._build(task)

    def _build(self, task: DownloadTask) -> None:
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        row = 0

        def add_field(label: str, value: str) -> None:
            nonlocal row
            name_label = QLabel(label, self)
            name_label.setProperty("role", "detail-label")
            value_label = QLabel(value or "—", self)
            value_label.setProperty("role", "detail-value")
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(name_label, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value_label, row, 1)
            row += 1

        add_field("状态", f"{task.status_label}")
        add_field("文件", task.file_name)
        add_field("大小", format_bytes(task.total_size) if task.total_size else "未知")
        add_field("已下载", f"{format_bytes(task.downloaded)}（{task.progress_percent:.1f}%）")
        add_field("类型", task.content_type or "未知")
        add_field("服务器", task.probe.host if task.probe else _host_of(task.final_url or task.url))
        add_field("HTTPS", "是" if (task.final_url or task.url).startswith("https://") else "否")
        add_field("Range", "支持" if task.supports_range else "不支持")
        add_field("断点续传", "支持" if task.can_resume else "不支持")
        add_field("下载模式", task.mode.label)
        add_field("并发连接", f"{task.connection_count}（请求 {task.requested_connections}）")
        add_field("当前速度", format_speed(task.speed.current))
        add_field("峰值速度", format_speed(task.speed.peak))
        add_field("平均速度", format_speed(task.speed.average))
        add_field("剩余时间", format_duration(task.eta_seconds))
        add_field("重试次数", str(task.retry_count))
        add_field("保存位置", task.display_path)
        add_field("最终 URL", task.final_url or task.url)
        add_field("ETag", task.etag or "服务器未提供")
        add_field("Last-Modified", task.last_modified or "服务器未提供")
        add_field("创建时间", format_datetime(task.created_at, with_seconds=True))
        if task.completed_at:
            add_field("完成时间", format_datetime(task.completed_at, with_seconds=True))
        add_field("SHA-256", task.sha256 or "下载完成后自动计算")
        if task.expected_sha256:
            verified = task.hash_verified
            text = "未校验"
            if verified is True:
                text = "✓ 校验通过"
            elif verified is False:
                text = "✕ 校验失败"
            add_field("校验结果", text)
        if task.error_message:
            add_field("错误信息", task.error_message)

        self.add_layout(grid)

        if task.segments:
            summary = QLabel(self._segments_summary(task.segments), self)
            summary.setProperty("role", "detail-note")
            summary.setWordWrap(True)
            self.add_widget(summary)

        if task.sha256:
            copy_hash = GhostButton("复制 SHA-256", icon_name="hash", parent=self)
            copy_hash.clicked.connect(
                lambda: QApplication.clipboard().setText(task.sha256)
            )
            self.add_button(copy_hash)
        copy_url = GhostButton("复制链接", icon_name="copy", parent=self)
        copy_url.clicked.connect(
            lambda: QApplication.clipboard().setText(task.final_url or task.url)
        )
        self.add_button(copy_url)
        close_button = SecondaryButton("关闭", parent=self)
        close_button.clicked.connect(self.accept)
        self.add_button(close_button)

    @staticmethod
    def _segments_summary(segments: list[DownloadSegment]) -> str:
        completed = sum(1 for segment in segments if segment.is_complete)
        retries = sum(segment.retry_count for segment in segments)
        mode = "分段下载" if len(segments) > 1 else "单连接"
        return (
            f"{mode}：{len(segments)} 个分段，已完成 {completed} 个，"
            f"累计重试 {retries} 次。"
        )


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return urlparse(url).netloc or "—"
    except ValueError:  # pragma: no cover
        return "—"
