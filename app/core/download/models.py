"""下载领域模型。

这些对象是**不依赖 Qt** 的纯数据，UI 通过信号接收后渲染。
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

from app.core.common.constants import DEFAULT_CONNECTIONS, MAX_CONNECTIONS, MIN_CONNECTIONS


class DownloadStatus(StrEnum):
    """下载任务状态。"""

    PENDING = "pending"
    PROBING = "probing"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    RETRYING = "retrying"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return STATUS_LABELS.get(self, "未知")

    @property
    def is_active(self) -> bool:
        """是否占用下载槽位。"""
        return self in {
            DownloadStatus.PROBING,
            DownloadStatus.DOWNLOADING,
            DownloadStatus.RETRYING,
        }

    @property
    def is_final(self) -> bool:
        return self in {
            DownloadStatus.COMPLETED,
            DownloadStatus.FAILED,
            DownloadStatus.CANCELLED,
        }

    @property
    def is_resumable(self) -> bool:
        return self in {
            DownloadStatus.PAUSED,
            DownloadStatus.FAILED,
            DownloadStatus.PENDING,
        }


STATUS_LABELS: Final[dict[DownloadStatus, str]] = {
    DownloadStatus.PENDING: "等待中",
    DownloadStatus.PROBING: "分析中",
    DownloadStatus.QUEUED: "排队中",
    DownloadStatus.DOWNLOADING: "下载中",
    DownloadStatus.RETRYING: "重试中",
    DownloadStatus.PAUSED: "已暂停",
    DownloadStatus.COMPLETED: "已完成",
    DownloadStatus.FAILED: "失败",
    DownloadStatus.CANCELLED: "已取消",
}


class SegmentState(StrEnum):
    """分段状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PAUSED = "paused"
    FAILED = "failed"


class DownloadMode(StrEnum):
    """实际使用的下载模式（用于展示与回退判断）。"""

    SINGLE = "single"
    SEGMENTED = "segmented"
    FALLBACK = "fallback"

    @property
    def label(self) -> str:
        return {
            DownloadMode.SINGLE: "单连接",
            DownloadMode.SEGMENTED: "多连接分段",
            DownloadMode.FALLBACK: "回退单连接",
        }[self]


@dataclass(slots=True)
class DownloadSegment:
    """一个 Range 分段。``start`` / ``end`` 为闭区间字节偏移。"""

    index: int
    start: int
    end: int
    current: int = 0
    state: SegmentState = SegmentState.PENDING
    retry_count: int = 0
    error: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.state, str):
            # 兼容来自数据库 / JSON 的字符串状态
            try:
                self.state = SegmentState(self.state)
            except ValueError:
                self.state = SegmentState.PENDING
        if self.current < self.start:
            self.current = self.start

    @property
    def length(self) -> int:
        """分段总字节数。"""
        return max(self.end - self.start + 1, 0)

    @property
    def written(self) -> int:
        """已写入字节数。"""
        return max(self.current - self.start, 0)

    @property
    def remaining(self) -> int:
        return max(self.end - self.current + 1, 0)

    @property
    def is_complete(self) -> bool:
        return self.state is SegmentState.COMPLETED or self.current > self.end

    def mark_complete(self) -> None:
        self.current = self.end + 1
        self.state = SegmentState.COMPLETED
        self.error = ""

    @classmethod
    def split(cls, total_size: int, count: int) -> list[DownloadSegment]:
        """把 ``total_size`` 切分为 ``count`` 个互不重叠的分段。"""
        if total_size <= 0 or count <= 0:
            return []
        count = max(1, min(count, total_size))
        base, remainder = divmod(total_size, count)
        segments: list[DownloadSegment] = []
        offset = 0
        for index in range(count):
            length = base + (1 if index < remainder else 0)
            if length <= 0:
                break
            segments.append(cls(index=index, start=offset, end=offset + length - 1))
            offset += length
        return segments


@dataclass(slots=True)
class ProbeResult:
    """URL 探测结果。"""

    url: str
    final_url: str
    file_name: str
    total_size: int
    content_type: str = "application/octet-stream"
    supports_range: bool = False
    accept_ranges: str = ""
    etag: str = ""
    last_modified: str = ""
    server: str = ""
    is_https: bool = False
    can_resume: bool = False
    suggested_connections: int = 1
    status: int = 200
    note: str = ""

    @property
    def host(self) -> str:
        try:
            return urlparse(self.final_url or self.url).netloc
        except ValueError:  # pragma: no cover - 防御式分支
            return ""

    @property
    def size_known(self) -> bool:
        return self.total_size > 0

    @property
    def display_type(self) -> str:
        """把 MIME 类型转换为简短描述。"""
        mime = (self.content_type or "").split(";")[0].strip().lower()
        mapping = {
            "application/octet-stream": "二进制文件",
            "application/zip": "ZIP 压缩包",
            "application/x-msdownload": "Windows 程序",
            "application/pdf": "PDF 文档",
            "application/x-iso9660-image": "光盘镜像",
            "text/plain": "文本文件",
            "text/html": "网页",
        }
        if mime in mapping:
            return mapping[mime]
        if mime.startswith("video/"):
            return "视频"
        if mime.startswith("audio/"):
            return "音频"
        if mime.startswith("image/"):
            return "图片"
        return mime or "未知类型"


@dataclass(slots=True)
class SpeedSnapshot:
    """速度统计快照。"""

    current: float = 0.0
    average: float = 0.0
    peak: float = 0.0
    eta_seconds: float | None = None


@dataclass(slots=True)
class DownloadTask:
    """一个下载任务（同时作为持久化模型与 UI 渲染模型）。"""

    task_id: str
    url: str
    file_name: str
    save_dir: Path
    final_url: str = ""
    save_path: Path | None = None
    temp_dir: Path | None = None
    total_size: int = 0
    downloaded: int = 0
    status: DownloadStatus = DownloadStatus.PENDING
    mode: DownloadMode = DownloadMode.SINGLE
    content_type: str = "application/octet-stream"
    etag: str = ""
    last_modified: str = ""
    supports_range: bool = False
    can_resume: bool = False
    connection_count: int = 1
    requested_connections: int = DEFAULT_CONNECTIONS
    segments: list[DownloadSegment] = field(default_factory=list)
    sha256: str = ""
    expected_sha256: str = ""
    error_message: str = ""
    notice: str = ""
    retry_count: int = 0
    speed: SpeedSnapshot = field(default_factory=SpeedSnapshot)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    probe: ProbeResult | None = None
    #: 运行期标记：任务因程序退出而中断（不会写入数据库）。
    #: 用于区分“用户手动暂停”和“程序崩溃/退出导致的暂停”。
    interrupted: bool = False

    # -- 派生属性 ---------------------------------------------------------
    @property
    def status_label(self) -> str:
        return self.status.label

    @property
    def progress_percent(self) -> float:
        """0–100 的进度百分比；总大小未知时返回 0。"""
        if self.total_size <= 0:
            return 100.0 if self.status is DownloadStatus.COMPLETED else 0.0
        return max(0.0, min(100.0, self.downloaded / self.total_size * 100.0))

    @property
    def remaining_bytes(self) -> int:
        return max(self.total_size - self.downloaded, 0) if self.total_size else 0

    @property
    def eta_seconds(self) -> float | None:
        if self.status is DownloadStatus.COMPLETED:
            return 0.0
        if self.speed.current <= 0 or not self.total_size:
            return None
        return self.remaining_bytes / self.speed.current

    @property
    def display_path(self) -> str:
        target = self.save_path or (self.save_dir / self.file_name)
        return str(target)

    @property
    def active_segments(self) -> int:
        """仍在工作的分段数量（用于展示“8 个连接”）。"""
        if not self.segments:
            return max(1, self.connection_count)
        return sum(
            1
            for segment in self.segments
            if segment.state in {SegmentState.RUNNING, SegmentState.PENDING}
        )

    @property
    def hash_verified(self) -> bool | None:
        """``True`` 校验通过 / ``False`` 不通过 / ``None`` 未校验。"""
        if not self.sha256 or not self.expected_sha256:
            return None
        return self.sha256.lower() == self.expected_sha256.lower()

    def clamp_downloaded(self) -> None:
        """根据分段重新计算已下载字节数。"""
        if self.segments:
            self.downloaded = sum(segment.written for segment in self.segments)
        if self.total_size:
            self.downloaded = min(self.downloaded, self.total_size)

    def normalize_connections(self, requested: int | None = None) -> int:
        """把连接数限制在合法区间。"""
        value = requested if requested is not None else self.requested_connections
        return max(MIN_CONNECTIONS, min(MAX_CONNECTIONS, int(value)))

    def snapshot(self) -> DownloadTask:
        """返回深拷贝。

        引擎在 asyncio 线程中持续修改任务对象，界面通过信号接收快照，
        避免跨线程读取可变状态。
        """
        return copy.deepcopy(self)


@dataclass(slots=True, frozen=True)
class DownloadStats:
    """下载中心汇总统计。"""

    active_count: int = 0
    queued_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    paused_count: int = 0
    total_speed: float = 0.0
    total_downloaded_today: int = 0

    @property
    def has_active(self) -> bool:
        return self.active_count > 0
