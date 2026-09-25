"""持久化层的数据传输对象。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class HistoryKind(StrEnum):
    """历史记录类型。"""

    DOWNLOAD = "download"
    ORGANIZE = "organize"
    HASH = "hash"
    FILE = "file"

    @property
    def label(self) -> str:
        return HISTORY_KIND_LABELS[self]


HISTORY_KIND_LABELS: Final[dict[HistoryKind, str]] = {
    HistoryKind.DOWNLOAD: "下载",
    HistoryKind.ORGANIZE: "整理",
    HistoryKind.HASH: "校验",
    HistoryKind.FILE: "文件操作",
}


class HistoryStatus(StrEnum):
    """历史记录结果状态。"""

    SUCCESS = "success"
    WARNING = "warning"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return HISTORY_STATUS_LABELS[self]


HISTORY_STATUS_LABELS: Final[dict[HistoryStatus, str]] = {
    HistoryStatus.SUCCESS: "成功",
    HistoryStatus.WARNING: "注意",
    HistoryStatus.FAILED: "失败",
}


@dataclass(slots=True, frozen=True)
class HistoryEvent:
    """一条历史记录（下载 / 整理 / 校验 / 文件操作）。"""

    kind: HistoryKind
    action: str
    title: str
    created_at: float
    event_id: int = 0
    detail: str = ""
    path: str = ""
    url: str = ""
    size: int = 0
    status: HistoryStatus = HistoryStatus.SUCCESS

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> HistoryEvent:
        """从数据库行构造对象。"""
        try:
            kind = HistoryKind(row["kind"])
        except ValueError:  # pragma: no cover - 兼容历史数据
            kind = HistoryKind.FILE
        try:
            status = HistoryStatus(row["status"])
        except ValueError:  # pragma: no cover
            status = HistoryStatus.SUCCESS
        return cls(
            event_id=int(row["event_id"]),
            kind=kind,
            action=str(row["action"]),
            title=str(row["title"]),
            detail=str(row["detail"]),
            path=str(row["path"]),
            url=str(row["url"]),
            size=int(row["size"]),
            status=status,
            created_at=float(row["created_at"]),
        )
