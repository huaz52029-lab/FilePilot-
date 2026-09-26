"""重复文件检测：先按大小分组，再对同尺寸文件计算 SHA-256。

默认只提供“移动到重复文件夹”，绝不自动删除用户文件。
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from app.core.common.constants import DUPLICATE_MIN_FILE_SIZE, DUPLICATE_TARGET_DIRNAME
from app.core.common.exceptions import FileOperationError
from app.core.common.helpers import format_bytes
from app.core.common.logger import get_logger
from app.core.files.hasher import compute_sha256
from app.core.files.models import DuplicateGroup, DuplicateScanResult, FileEntry
from app.core.files.mover import move_file
from app.core.files.scanner import iter_file_entries

_log = get_logger("files.duplicate")

CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[str], None]
MoveProgressCallback = Callable[[int, int, Path], None]  # (已完成数量, 总数, 当前文件)


@dataclass(slots=True)
class DuplicateMoveResult:
    """重复文件批量移动的结果。"""

    moved: int = 0
    failed: int = 0
    skipped: int = 0
    bytes_moved: int = 0
    cross_volume: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)
    cancelled: bool = False

    @property
    def total(self) -> int:
        return self.moved + self.failed + self.skipped

    @property
    def success(self) -> bool:
        return self.failed == 0 and not self.cancelled

    @property
    def summary(self) -> str:
        """人类可读的汇总文本。"""
        parts = [f"成功 {self.moved} 个"]
        if self.failed:
            parts.append(f"失败 {self.failed} 个")
        if self.skipped:
            parts.append(f"跳过 {self.skipped} 个")
        if self.cross_volume:
            parts.append(f"其中跨盘复制 {self.cross_volume} 个")
        if self.cancelled:
            parts.append("已取消")
        return "，".join(parts)


def find_duplicates(
    roots: Iterable[Path | str],
    *,
    minimum_size: int = DUPLICATE_MIN_FILE_SIZE,
    should_cancel: CancelCheck | None = None,
    on_progress: ProgressCallback | None = None,
) -> DuplicateScanResult:
    """扫描若干目录，返回重复文件分组。"""
    root_paths = tuple(Path(root) for root in roots)
    result = DuplicateScanResult(roots=root_paths)
    started = time.perf_counter()
    cancelled = should_cancel or (lambda: False)

    by_size: dict[int, list[FileEntry]] = defaultdict(list)
    for root in root_paths:
        if not root.exists():
            continue
        for entry in iter_file_entries(root, should_cancel=cancelled):
            if entry.size < minimum_size:
                continue
            result.scanned_files += 1
            by_size[entry.size].append(entry)

    if on_progress is not None:
        on_progress(f"已扫描 {result.scanned_files} 个文件，正在计算内容指纹…")

    for size, entries in by_size.items():
        if len(entries) < 2:
            continue
        by_hash: dict[str, list[Path]] = defaultdict(list)
        for entry in entries:
            if cancelled():
                result.cancelled = True
                break
            try:
                digest = compute_sha256(entry.path)
            except FileOperationError as exc:
                _log.debug("跳过无法读取的文件 %s：%s", entry.path, exc.detail or exc.message)
                continue
            result.hashed_files += 1
            by_hash[digest].append(entry.path)
        for digest, paths in by_hash.items():
            if len(paths) > 1:
                result.groups.append(
                    DuplicateGroup(size=size, sha256=digest, files=tuple(sorted(paths)))
                )
        if result.cancelled:
            break

    result.groups.sort(key=lambda group: group.wasted_bytes, reverse=True)
    result.duration = time.perf_counter() - started
    _log.info(
        "重复文件检测完成：%s 组，可回收 %s",
        result.group_count,
        format_bytes(result.wasted_bytes),
    )
    return result


def move_to_duplicate_folder(
    paths: Iterable[Path | str],
    *,
    target_dir: Path | str,
    should_cancel: CancelCheck | None = None,
    on_progress: MoveProgressCallback | None = None,
    verify_hash: bool = False,
) -> DuplicateMoveResult:
    """把指定文件移动到“重复文件”文件夹。

    * 同盘使用重命名，跨盘自动使用 复制 → 校验 → 删除；
    * 单个文件失败不会中断整体流程，失败原因会记录在结果中。
    """
    destination_root = Path(target_dir)
    cancelled = should_cancel or (lambda: False)
    items = [Path(path) for path in paths]
    result = DuplicateMoveResult()
    index = 0
    for index, source in enumerate(items, start=1):
        if cancelled():
            result.cancelled = True
            break
        if on_progress is not None:
            on_progress(index - 1, len(items), source)
        if not source.exists():
            result.skipped += 1
            result.errors.append((source, "文件不存在或已被移动"))
            continue
        destination_dir = destination_root / source.parent.name
        try:
            outcome = move_file(
                source,
                destination_dir,
                verify_hash=verify_hash,
                should_cancel=cancelled,
            )
        except FileOperationError as exc:
            result.failed += 1
            result.errors.append((source, exc.user_message))
            _log.warning("移动重复文件失败 %s：%s", source, exc.detail or exc.message)
            continue
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不影响其它文件
            result.failed += 1
            result.errors.append((source, str(exc)))
            _log.warning("移动重复文件异常 %s：%s", source, exc)
            continue
        result.moved += 1
        result.bytes_moved += outcome.bytes_moved
        if outcome.cross_volume:
            result.cross_volume += 1
    if on_progress is not None:
        on_progress(index, len(items), items[-1] if items else destination_root)
    _log.info("重复文件移动完成：%s", result.summary)
    return result


def duplicate_folder(root: Path | str) -> Path:
    """返回重复文件文件夹路径。"""
    return Path(root) / DUPLICATE_TARGET_DIRNAME
