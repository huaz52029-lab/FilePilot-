"""重复文件检测：先按大小分组，再对同尺寸文件计算 SHA-256。

默认只提供“移动到重复文件夹”，绝不自动删除用户文件。
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path

from app.core.common.constants import DUPLICATE_MIN_FILE_SIZE, DUPLICATE_TARGET_DIRNAME
from app.core.common.exceptions import FileOperationError, describe_os_error
from app.core.common.helpers import format_bytes, unique_path
from app.core.common.logger import get_logger
from app.core.files.hasher import compute_sha256
from app.core.files.models import DuplicateGroup, DuplicateScanResult, FileEntry
from app.core.files.scanner import iter_file_entries

_log = get_logger("files.duplicate")

CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[str], None]


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
) -> tuple[int, list[str]]:
    """把指定文件移动到“重复文件”文件夹。

    返回 ``(移动成功数量, 错误信息列表)``。
    """
    destination_root = Path(target_dir)
    cancelled = should_cancel or (lambda: False)
    moved = 0
    errors: list[str] = []
    for path in paths:
        if cancelled():
            break
        source = Path(path)
        if not source.exists():
            errors.append(f"{source.name}：文件不存在")
            continue
        destination_dir = destination_root / source.parent.name
        try:
            destination_dir.mkdir(parents=True, exist_ok=True)
            target = unique_path(destination_dir / source.name)
            source.rename(target)
        except OSError as exc:
            message = describe_os_error(exc)
            errors.append(f"{source.name}：{message}")
            _log.warning("移动重复文件失败 %s：%s", source, exc)
            continue
        moved += 1
    return moved, errors


def duplicate_folder(root: Path | str) -> Path:
    """返回重复文件文件夹路径。"""
    return Path(root) / DUPLICATE_TARGET_DIRNAME
