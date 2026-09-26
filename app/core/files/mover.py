"""文件移动。

* 同一磁盘：使用 ``os.replace`` 直接重命名（瞬时完成，不复制数据）；
* 跨磁盘：必须使用 ``复制 → 校验 → 删除源文件`` 流程
  （Windows 不允许跨卷 rename，直接 move 会抛出 WinError 17）。

安全约定：

* 绝不先删除源文件；
* 复制或校验失败时删除不完整的目标文件，**源文件保持不变**；
* 目标已存在时不覆盖，自动生成 ``名称 (1).扩展名``。
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.common.cancellation import CancelledError
from app.core.common.exceptions import FileOperationError, describe_os_error
from app.core.common.helpers import unique_path
from app.core.common.logger import get_logger
from app.core.files.hasher import compute_sha256

_log = get_logger("files.mover")

COPY_CHUNK_SIZE = 1024 * 1024
PROGRESS_MIN_INTERVAL = 0.15

ProgressCallback = Callable[[int, int], None]  # (已复制字节, 总字节)
CancelCheck = Callable[[], bool]


@dataclass(slots=True)
class MoveResult:
    """一次移动操作的结果。"""

    source: Path
    target: Path
    method: str  # "rename"（同盘）或 "copy"（跨盘）
    bytes_moved: int
    hash_verified: bool = False

    @property
    def cross_volume(self) -> bool:
        return self.method == "copy"


def volume_key(path: Path | str) -> str:
    """返回路径所在的卷标识（Windows 为盘符，其它平台为挂载点）。"""
    target = Path(path)
    try:
        anchor = target.resolve().anchor
    except OSError:  # pragma: no cover - 路径不可解析时退回原始锚点
        anchor = target.anchor
    return (anchor or "/").lower()


def same_volume(source: Path | str, target: Path | str) -> bool:
    """判断源与目标是否位于同一磁盘/卷。"""
    return volume_key(source) == volume_key(target)


def resolve_target(
    source: Path,
    target_dir: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """计算目标路径；目标已存在时自动改名（默认不覆盖）。"""
    target = target_dir / source.name
    if overwrite or not target.exists():
        return target
    return unique_path(target)


def move_file(
    source: Path | str,
    target_dir: Path | str,
    *,
    overwrite: bool = False,
    verify_hash: bool = False,
    should_cancel: CancelCheck | None = None,
    on_progress: ProgressCallback | None = None,
) -> MoveResult:
    """把文件移动到 ``target_dir``，自动选择同盘重命名或跨盘复制。

    ``verify_hash=True`` 时在跨盘复制后会额外比对 SHA-256（较慢但最安全）。
    """
    src = Path(source)
    directory = Path(target_dir)
    if not src.exists():
        raise FileOperationError("源文件不存在或已被移动。", detail=str(src))
    if src.is_dir():
        raise FileOperationError("暂不支持移动文件夹，请选择文件。", detail=str(src))

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FileOperationError(
            "无法创建目标文件夹，请检查权限。", detail=f"{directory}: {exc}"
        ) from exc

    target = resolve_target(src, directory, overwrite=overwrite)
    size = src.stat().st_size

    if same_volume(src, target):
        return _rename(src, target, size=size)
    return _copy_verify_delete(
        src,
        target,
        size=size,
        verify_hash=verify_hash,
        should_cancel=should_cancel,
        on_progress=on_progress,
    )


def _rename(source: Path, target: Path, *, size: int) -> MoveResult:
    """同盘移动：直接重命名（原子操作，不复制数据）。"""
    try:
        os.replace(source, target)
    except OSError as exc:
        # 极端情况（例如目录被挂载、权限异常）退化为跨盘流程
        _log.warning("同盘重命名失败，改用复制流程：%s", exc)
        return _copy_verify_delete(source, target, size=size, verify_hash=False)
    return MoveResult(source=source, target=target, method="rename", bytes_moved=size)


def _copy_verify_delete(
    source: Path,
    target: Path,
    *,
    size: int,
    verify_hash: bool,
    should_cancel: CancelCheck | None = None,
    on_progress: ProgressCallback | None = None,
) -> MoveResult:
    """跨盘移动：复制 → 校验 → 删除源文件。"""
    copied = 0
    last_report = 0.0
    try:
        with source.open("rb") as reader, target.open("wb") as writer:
            while True:
                if should_cancel is not None and should_cancel():
                    raise CancelledError("移动已取消。")
                chunk = reader.read(COPY_CHUNK_SIZE)
                if not chunk:
                    break
                writer.write(chunk)
                copied += len(chunk)
                now = time.monotonic()
                if on_progress is not None and (
                    now - last_report >= PROGRESS_MIN_INTERVAL or copied >= size
                ):
                    last_report = now
                    on_progress(copied, size)
            writer.flush()
            os.fsync(writer.fileno())  # 确保数据真正落盘后再删除源文件
        try:
            shutil.copystat(source, target)
        except OSError as exc:  # pragma: no cover - 元数据失败不影响内容
            _log.debug("复制文件属性失败（可忽略）：%s", exc)
    except CancelledError:
        _discard_partial(target)
        raise
    except OSError as exc:
        _discard_partial(target)
        raise FileOperationError(
            describe_os_error(exc), detail=f"{source} → {target}: {exc}"
        ) from exc

    # 1) 大小校验
    try:
        written = target.stat().st_size
    except OSError as exc:  # pragma: no cover - 刚写完不可能缺失
        _discard_partial(target)
        raise FileOperationError("无法读取目标文件以校验。", detail=str(exc)) from exc
    if written != size:
        _discard_partial(target)
        raise FileOperationError(
            "复制结果不完整，已保留源文件。",
            detail=f"期望 {size} 字节，实际 {written} 字节（{target}）",
        )

    # 2) 可选的内容校验
    hash_ok = False
    if verify_hash:
        source_hash = compute_sha256(source)
        target_hash = compute_sha256(target)
        if source_hash != target_hash:
            _discard_partial(target)
            raise FileOperationError(
                "复制内容校验失败，已保留源文件。",
                detail=f"{source_hash[:16]}… != {target_hash[:16]}…",
            )
        hash_ok = True

    # 3) 校验通过后才删除源文件
    try:
        source.unlink()
    except OSError as exc:
        _log.warning("删除源文件失败（目标文件已完整）：%s", exc)
        raise FileOperationError(
            "文件已复制到目标位置，但无法删除源文件，请手动确认。",
            detail=f"{source}: {exc}",
        ) from exc
    return MoveResult(
        source=source,
        target=target,
        method="copy",
        bytes_moved=written,
        hash_verified=hash_ok,
    )


def _discard_partial(target: Path) -> None:
    """删除未完成的目标文件（仅在复制失败时调用）。"""
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - 目标被占用
        _log.warning("清理不完整的目标文件失败：%s", exc)
