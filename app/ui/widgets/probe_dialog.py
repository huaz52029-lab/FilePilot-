"""下载分析对话框：展示探测结果并确认下载参数。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QLineEdit, QWidget

from app.core.common.constants import MAX_CONNECTIONS
from app.core.common.helpers import format_bytes
from app.core.download.models import ProbeResult
from app.services.settings_service import AppSettings
from app.ui.widgets.buttons import PrimaryButton, SecondaryButton
from app.ui.widgets.dialog import AppDialog
from app.ui.widgets.inputs import PathPicker


class ProbeDialog(AppDialog):
    """探测结果确认对话框（“下载分析”）。"""

    def __init__(
        self,
        probe: ProbeResult,
        *,
        settings: AppSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("下载分析", subtitle=probe.file_name, width=520, parent=parent)
        self._probe = probe
        self._settings = settings

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        rows: list[tuple[str, str]] = [
            ("文件", probe.file_name),
            ("大小", format_bytes(probe.total_size) if probe.size_known else "服务器未提供"),
            ("类型", f"{probe.display_type}（{probe.content_type or '未知'}）"),
            ("服务器", probe.host),
            ("HTTPS", "是" if probe.is_https else "否"),
            ("Range", "支持" if probe.supports_range else "不支持（将使用单连接）"),
            ("断点续传", "支持" if probe.can_resume else "不支持"),
            ("推荐连接", f"{probe.suggested_connections}（最大 {MAX_CONNECTIONS}）"),
        ]
        for row, (label, value) in enumerate(rows):
            name = QLabel(label, self)
            name.setProperty("role", "detail-label")
            field = QLabel(value, self)
            field.setProperty("role", "detail-value")
            field.setWordWrap(True)
            field.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(name, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(field, row, 1)
        self.add_layout(grid)

        if probe.etag or probe.last_modified:
            note = QLabel(
                f"ETag：{probe.etag or '—'}\nLast-Modified：{probe.last_modified or '—'}\n"
                "续传时会校验以上标记，若服务器文件已变化将重新下载。",
                self,
            )
            note.setProperty("role", "detail-note")
            note.setWordWrap(True)
            self.add_widget(note)

        if probe.note:
            warning = QLabel(probe.note, self)
            warning.setProperty("role", "item-notice")
            warning.setWordWrap(True)
            self.add_widget(warning)

        self.path_picker = PathPicker(
            str(settings.download_dir),
            placeholder="选择保存目录",
            title="选择保存目录",
            parent=self,
        )
        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(10)
        path_label = QLabel("保存目录", self)
        path_label.setProperty("role", "detail-label")
        path_row.addWidget(path_label, 0, Qt.AlignmentFlag.AlignTop)
        path_row.addWidget(self.path_picker, 1)
        self.add_layout(path_row)

        hash_row = QHBoxLayout()
        hash_row.setContentsMargins(0, 0, 0, 0)
        hash_row.setSpacing(10)
        hash_label = QLabel("预期 SHA-256", self)
        hash_label.setProperty("role", "detail-label")
        hash_row.addWidget(hash_label, 0)
        self.hash_edit = QLineEdit(self)
        self.hash_edit.setPlaceholderText("可选：填写后下载完成会自动比对")
        hash_row.addWidget(self.hash_edit, 1)
        self.add_layout(hash_row)

        self.cancel_button = SecondaryButton("取消", parent=self)
        self.cancel_button.clicked.connect(self.reject)
        self.add_button(self.cancel_button)
        self.start_button = PrimaryButton("开始下载", icon_name="download", parent=self)
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self._on_start)
        self.add_button(self.start_button)

    # ------------------------------------------------------------------
    @property
    def save_dir(self) -> Path:
        return Path(self.path_picker.path() or str(self._settings.download_dir))

    @property
    def expected_sha256(self) -> str:
        return self.hash_edit.text().strip()

    def _on_start(self) -> None:
        if not self.path_picker.path():
            self.path_picker.set_path(self._settings.download_dir)
        self.accept()
