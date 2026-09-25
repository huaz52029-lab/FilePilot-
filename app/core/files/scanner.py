"""目录扫描与空间统计。

设计要点：

* 使用 ``os.scandir`` 迭代，内存占用可控；
* 全过程支持协作式取消与进度回调；
* 单个条目出错只累计错误数并记录日志，不中断整体扫描。
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from app.core.common.constants import SCAN_IGNORED_DIRS, SCAN_MAX_DEPTH
from app.core.common.exceptions import describe_os_error
from app.core.common.logger import get_logger
from app.core.files.categories import categorize_path
from app.core.files.models import (
    DirectoryUsage,
    DiskUsageInfo,
    FileEntry,
    ScanStats,
)

_log = get_logger("files.scanner")

ProgressCallback = Callable[[int, Path], None]
CancelCheck = Callable[[], bool]


def _no_cancel() -> bool:
    return False


def iter_file_entries(
    root: Path | str,
    *,
    max_depth: int = SCAN_MAX_DEPTH,
    ignore_dirs: frozenset[str] = SCAN_IGNORED_DIRS,
    include_hidden: bool = True,
    should_cancel: CancelCheck | None = None,
    on_progress: ProgressCallback | None = None,
    on_error: Callable[[Path, OSError], None] | None = None,
) -> Iterator[FileEntry]:
    """迭代目录下的所有文件条目（深度优先）。"""
    base = Path(root)
    cancelled = should_cancel or _no_cancel
    counter = 0
    stack: list[tuple[Path, int]] = [(base, 0)]

    while stack:
        if cancelled():
            return
        directory, depth = stack.pop()
        try:
            with os.scandir(directory) as iterator:
                for item in iterator:
                    if cancelled():
                        return
                    name = item.name
                    if not include_hidden and name.startswith("."):
                        continue
                    try:
                        if item.is_dir(follow_symlinks=False):
                            if name in ignore_dirs:
                                continue
                            if depth + 1 <= max_depth:
                                stack.append((Path(item.path), depth + 1))
                            continue
                        if not item.is_file(follow_symlinks=False):
                            continue
                        stat = item.stat(follow_symlinks=False)
                    except OSError as exc:
                        if on_error is not None:
                            on_error(Path(item.path), exc)
                        _log.debug("跳过无法访问的条目 %s：%s", item.path, exc)
                        continue
                    file_path = Path(item.path)
                    counter += 1
                    if on_progress is not None:
                        on_progress(counter, file_path)
                    yield FileEntry(
                        path=file_path,
                        size=int(stat.st_size),
                        mtime=float(stat.st_mtime),
                        category=categorize_path(file_path),
                    )
        except OSError as exc:
            if on_error is not None:
                on_error(directory, exc)
            _log.debug("无法读取目录 %s：%s", directory, exc)
            continue


def scan_directory(
    root: Path | str,
    *,
    max_depth: int = SCAN_MAX_DEPTH,
    limit: int | None = None,
    ignore_dirs: frozenset[str] = SCAN_IGNORED_DIRS,
    should_cancel: CancelCheck | None = None,
    on_progress: ProgressCallback | None = None,
) -> tuple[list[FileEntry], ScanStats]:
    """完整扫描目录，返回 ``(文件列表, 统计信息)``。"""
    started = time.perf_counter()
    base = Path(root)
    stats = ScanStats(root=base)
    entries: list[FileEntry] = []
    cancelled = should_cancel or _no_cancel
    seen_dirs: set[Path] = set()

    def _on_error(_path: Path, _exc: OSError) -> None:
        stats.errors += 1

    for entry in iter_file_entries(
        base,
        max_depth=max_depth,
        ignore_dirs=ignore_dirs,
        should_cancel=cancelled,
        on_progress=on_progress,
        on_error=_on_error,
    ):
        if entry.path.parent not in seen_dirs:
            seen_dirs.add(entry.path.parent)
        entries.append(entry)
        stats.add_file(entry)
        if limit is not None and len(entries) >= limit:
            break

    stats.directory_count = len(seen_dirs)
    stats.cancelled = cancelled()
    stats.duration = time.perf_counter() - started
    return entries, stats


def quick_directory_stats(
    root: Path | str,
    *,
    ignore_dirs: frozenset[str] = SCAN_IGNORED_DIRS,
    should_cancel: CancelCheck | None = None,
) -> ScanStats:
    """只统计文件数量与总大小（用于首页概览，避免占用内存）。"""
    started = time.perf_counter()
    base = Path(root)
    stats = ScanStats(root=base)
    cancelled = should_cancel or _no_cancel
    directories: set[Path] = set()

    for entry in iter_file_entries(
        base,
        ignore_dirs=ignore_dirs,
        should_cancel=cancelled,
        on_error=lambda _p, _e: setattr(stats, "errors", stats.errors + 1),
    ):
        stats.add_file(entry)
        directories.add(entry.path.parent)

    stats.directory_count = len(directories)
    stats.cancelled = cancelled()
    stats.duration = time.perf_counter() - started
    return stats


def directory_usage(
    root: Path | str,
    *,
    max_depth: int = 1,
    should_cancel: CancelCheck | None = None,
) -> DirectoryUsage:
    """统计目录及其直接子目录的占用（``max_depth`` 控制递归层级）。"""
    cancelled = should_cancel or _no_cancel
    node = _usage_for(Path(root), depth=0, max_depth=max_depth, should_cancel=cancelled)
    return node


def _usage_for(
    directory: Path,
    *,
    depth: int,
    max_depth: int,
    should_cancel: CancelCheck,
) -> DirectoryUsage:
    """统计目录占用。

    ``children`` 只展开到 ``max_depth`` 层，但每个节点的 ``total_size`` /
    ``file_count`` / ``dir_count`` 始终是**完整递归**结果，保证空间分析
    界面的目录排行真实可靠。
    """
    node = DirectoryUsage(path=directory)
    if should_cancel():
        return node
    try:
        with os.scandir(directory) as iterator:
            children: list[Path] = []
            for item in iterator:
                if should_cancel():
                    return node
                try:
                    if item.is_dir(follow_symlinks=False):
                        node.dir_count += 1
                        children.append(Path(item.path))
                        continue
                    if not item.is_file(follow_symlinks=False):
                        continue
                    size = int(item.stat(follow_symlinks=False).st_size)
                except OSError as exc:
                    node.error = describe_os_error(exc)
                    continue
                node.file_count += 1
                node.total_size += size
            for child in children:
                child_node = _usage_for(
                    child,
                    depth=depth + 1,
                    max_depth=max_depth,
                    should_cancel=should_cancel,
                )
                node.total_size += child_node.total_size
                node.file_count += child_node.file_count
                node.dir_count += child_node.dir_count
                if depth + 1 <= max_depth:
                    node.children.append(child_node)
    except OSError as exc:
        node.error = describe_os_error(exc)
        _log.debug("目录统计失败 %s：%s", directory, exc)
    return node


def disk_usage_info(path: Path | str) -> DiskUsageInfo:
    """查询磁盘整体使用情况。"""
    target = Path(path)
    usage = shutil.disk_usage(target.anchor or str(target))
    return DiskUsageInfo(
        path=target,
        total=int(usage.total),
        used=int(usage.used),
        free=int(usage.free),
    )


def available_bytes(path: Path | str) -> int:
    """目标路径所在磁盘的可用空间。"""
    target = Path(path)
    return int(shutil.disk_usage(target.anchor or str(target)).free)


def ensure_disk_space(path: Path | str, required: int, *, margin: int = 64 * 1024 * 1024) -> None:
    """空间不足时抛出 :class:`InsufficientDiskSpaceError`。

    ``margin`` 为预留缓冲（默认 64 MB），避免写满磁盘。
    """
    from app.core.common.exceptions import InsufficientDiskSpaceError

    free = available_bytes(path)
    if required > 0 and free < required + margin:
        raise InsufficientDiskSpaceError(required + margin, free, path=str(path))
