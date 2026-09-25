"""文件哈希计算（SHA-256）。

用于下载完成后的校验与重复文件检测；支持进度回调与协作式取消。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from app.core.common.constants import HASH_CHUNK_SIZE
from app.core.common.exceptions import FileOperationError
from app.core.common.logger import get_logger

_log = get_logger("files.hasher")

ProgressCallback = Callable[[int, int], None]  # (已处理字节, 总字节)


def hash_file(
    path: Path | str,
    *,
    algorithm: str = "sha256",
    chunk_size: int = HASH_CHUNK_SIZE,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: ProgressCallback | None = None,
) -> str:
    """计算文件哈希值（默认 SHA-256）。"""
    target = Path(path)
    digest = hashlib.new(algorithm)
    cancelled = should_cancel or (lambda: False)
    try:
        total = target.stat().st_size
    except OSError:
        total = 0
    processed = 0
    try:
        with target.open("rb") as handle:
            while True:
                if cancelled():
                    raise InterruptedError("哈希计算已取消")
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
                processed += len(chunk)
                if on_progress is not None:
                    on_progress(processed, total)
    except InterruptedError:
        raise
    except OSError as exc:
        raise FileOperationError(
            "无法读取文件以计算校验值。", detail=f"{target}: {exc}"
        ) from exc
    value = digest.hexdigest()
    _log.debug("已计算 %s 的 %s：%s", target.name, algorithm.upper(), value[:16])
    return value


def compute_sha256(
    path: Path | str,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: ProgressCallback | None = None,
) -> str:
    """计算 SHA-256（``hash_file`` 的便捷封装）。"""
    return hash_file(
        path, algorithm="sha256", should_cancel=should_cancel, on_progress=on_progress
    )


def hash_text(text: str, *, algorithm: str = "sha256") -> str:
    """计算文本哈希（用于快速内容指纹）。"""
    digest = hashlib.new(algorithm)
    digest.update(text.encode("utf-8"))
    return digest.hexdigest()
