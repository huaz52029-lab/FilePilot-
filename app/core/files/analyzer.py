"""空间分析：目录占用、最大文件与类型分布。

该模块只做计算，不涉及任何界面代码，可脱离 GUI 单独测试。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.core.common.cancellation import CancelToken
from app.core.common.exceptions import FileScanError
from app.core.common.logger import get_logger
from app.core.files.categories import FileCategory, category_label
from app.core.files.models import DirectoryUsage, DiskUsageInfo, FileEntry, ScanStats
from app.core.files.scanner import directory_usage, disk_usage_info, scan_directory

_log = get_logger("files.analyzer")

ProgressCallback = Callable[[object], None]
CancelCheck = Callable[[], bool]


@dataclass(slots=True)
class DirectoryAnalysis:
    """一次目录分析的完整结果。"""

    root: Path
    disk: DiskUsageInfo
    usage: DirectoryUsage
    stats: ScanStats
    largest_files: list[FileEntry] = field(default_factory=list)
    duration: float = 0.0
    cancelled: bool = False

    @property
    def total_size(self) -> int:
        return self.stats.total_size

    @property
    def file_count(self) -> int:
        return self.stats.file_count

    def category_breakdown(self) -> list[tuple[FileCategory, int, float]]:
        """返回 ``(分类, 字节数, 占比)``，按大小降序。"""
        total = self.stats.total_size or 1
        items = [
            (category, size, size / total * 100.0)
            for category, size in self.stats.category_sizes.items()
        ]
        return sorted(items, key=lambda item: item[1], reverse=True)

    def child_rows(self, *, by: str = "size") -> list[DirectoryUsage]:
        """当前目录的直接子目录，按指定维度排序。"""
        return self.usage.sorted_children(by=by)


def analyze_directory(
    root: Path | str,
    *,
    max_depth: int = 1,
    largest_limit: int = 20,
    should_cancel: CancelCheck | None = None,
    token: CancelToken | None = None,
    report: ProgressCallback | None = None,
) -> DirectoryAnalysis:
    """分析目录占用（可在后台线程中运行）。

    ``report`` 会收到 ``(已扫描文件数, 当前路径)`` 形式的进度元组。
    """
    base = Path(root)
    if not base.exists():
        raise FileScanError("目录不存在或无法访问。", detail=str(base))
    if not base.is_dir():
        raise FileScanError("所选路径不是文件夹。", detail=str(base))

    cancelled: CancelCheck = should_cancel or (
        (lambda: token.cancelled) if token is not None else (lambda: False)
    )
    started = time.perf_counter()

    def _on_progress(count: int, path: Path) -> None:
        if report is not None and count % 100 == 0:
            report((count, path))

    usage = directory_usage(base, max_depth=max_depth, should_cancel=cancelled)
    entries, stats = scan_directory(
        base,
        # 空间分析必须统计磁盘上的真实内容，因此不忽略任何系统目录
        ignore_dirs=frozenset(),
        should_cancel=cancelled,
        on_progress=_on_progress,
    )
    largest = sorted(entries, key=lambda entry: entry.size, reverse=True)[:largest_limit]
    duration = time.perf_counter() - started
    _log.info(
        "空间分析完成：%s（%s 个文件，%s 字节，耗时 %.2fs）",
        base,
        stats.file_count,
        stats.total_size,
        duration,
    )
    return DirectoryAnalysis(
        root=base,
        disk=disk_usage_info(base),
        usage=usage,
        stats=stats,
        largest_files=largest,
        duration=duration,
        cancelled=stats.cancelled,
    )


def format_category_line(category: FileCategory | str, size: int, share: float) -> str:
    """生成“分类：大小（占比）”文本。"""
    from app.core.common.helpers import format_bytes

    return f"{category_label(category)}：{format_bytes(size)}（{share:.1f}%）"
