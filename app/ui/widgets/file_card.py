"""文件条目卡片（整理预览、搜索结果、重复文件等复用）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.core.common.helpers import format_bytes
from app.core.files.categories import FileCategory, categorize_path, category_label
from app.core.files.models import FileEntry
from app.ui.icons import default_icon_provider
from app.ui.theme import token
from app.ui.widgets.badge import StatusBadge
from app.ui.widgets.cards import Card

CATEGORY_ICONS: dict[FileCategory, str] = {
    FileCategory.IMAGE: "image",
    FileCategory.VIDEO: "video",
    FileCategory.AUDIO: "audio",
    FileCategory.DOCUMENT: "document",
    FileCategory.ARCHIVE: "archive",
    FileCategory.PROGRAM: "program",
    FileCategory.CODE: "code",
    FileCategory.OTHER: "file",
}

CATEGORY_TONES: dict[FileCategory, str] = {
    FileCategory.IMAGE: "info",
    FileCategory.VIDEO: "accent",
    FileCategory.AUDIO: "success",
    FileCategory.DOCUMENT: "warning",
    FileCategory.ARCHIVE: "accent",
    FileCategory.PROGRAM: "error",
    FileCategory.CODE: "info",
    FileCategory.OTHER: "neutral",
}


class FileCard(Card):
    """展示单个文件的名称、路径与大小。"""

    activated = Signal(object)

    def __init__(
        self,
        path: Path,
        *,
        size: int = 0,
        subtitle: str = "",
        category: FileCategory | None = None,
        badge_text: str = "",
        badge_tone: str = "neutral",
        trailing: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, padding=(14, 12, 14, 12), spacing=6, hoverable=True)
        self._icons = default_icon_provider()
        self._path = Path(path)
        self._category = category or categorize_path(self._path)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        icon_label = QLabel(self)
        icon_label.setFixedSize(32, 32)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setPixmap(
            self._icons.pixmap(
                CATEGORY_ICONS.get(self._category, "file"),
                color=token(CATEGORY_TONES.get(self._category, "neutral"), "#A3AAB5"),
                size=20,
            )
        )
        layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(3)

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        self._name_label = QLabel(self._path.name, self)
        self._name_label.setProperty("role", "item-title")
        self._name_label.setToolTip(str(self._path))
        name_row.addWidget(self._name_label, 0)
        if badge_text:
            name_row.addWidget(StatusBadge(badge_text, badge_tone, self), 0)
        name_row.addStretch(1)
        column.addLayout(name_row)

        detail = subtitle or str(self._path.parent)
        self._subtitle_label = QLabel(detail, self)
        self._subtitle_label.setProperty("role", "item-subtitle")
        self._subtitle_label.setToolTip(detail)
        column.addWidget(self._subtitle_label)

        meta_bits: list[str] = []
        if size:
            meta_bits.append(format_bytes(size))
        meta_bits.append(category_label(self._category))
        meta_label = QLabel("  ·  ".join(meta_bits), self)
        meta_label.setProperty("role", "item-meta")
        column.addWidget(meta_label)

        layout.addLayout(column, 1)
        if trailing is not None:
            layout.addWidget(trailing, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body.addLayout(layout)

    @property
    def path(self) -> Path:
        return self._path

    @classmethod
    def from_entry(cls, entry: FileEntry, **kwargs) -> FileCard:  # type: ignore[no-untyped-def]
        """按 :class:`FileEntry` 构造。"""
        return cls(entry.path, size=entry.size, category=entry.category, **kwargs)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.activated.emit(self._path)
        super().mouseDoubleClickEvent(event)
