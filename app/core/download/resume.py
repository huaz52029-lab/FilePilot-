"""断点续传的临时目录与元数据管理。

目录结构::

    D:/Downloads/
        Ubuntu.iso.fp.part/
            meta.json      服务器校验信息 + 分段进度
            data.part      预分配的稀疏数据文件（按偏移写入）

下载完成后 ``data.part`` 会被**原子重命名**为目标文件，因此不会出现
“下载完成但文件不完整”的中间状态，也避免了大文件的二次拷贝。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from app.core.common.constants import META_FILENAME, PART_DIR_SUFFIX
from app.core.common.exceptions import DownloadFileError, DownloadResumeError
from app.core.common.helpers import unique_path
from app.core.common.logger import get_logger
from app.core.download.models import DownloadSegment, ProbeResult, SegmentState

_log = get_logger("download.resume")

DATA_FILENAME: Final[str] = "data.part"
META_VERSION: Final[int] = 1


@dataclass(slots=True)
class PartMeta:
    """断点续传元数据（写入 meta.json）。"""

    task_id: str
    url: str
    final_url: str
    file_name: str
    total_size: int
    etag: str = ""
    last_modified: str = ""
    supports_range: bool = False
    connection_count: int = 1
    segments: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    version: int = META_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PartMeta:
        known = {
            "task_id",
            "url",
            "final_url",
            "file_name",
            "total_size",
            "etag",
            "last_modified",
            "supports_range",
            "connection_count",
            "segments",
            "created_at",
            "version",
        }
        filtered = {key: value for key, value in payload.items() if key in known}
        return cls(**filtered)

    @classmethod
    def from_task(
        cls,
        *,
        task_id: str,
        url: str,
        final_url: str,
        file_name: str,
        total_size: int,
        etag: str,
        last_modified: str,
        supports_range: bool,
        connection_count: int,
        segments: list[DownloadSegment],
    ) -> PartMeta:
        """由任务与分段信息构造元数据。"""
        return cls(
            task_id=task_id,
            url=url,
            final_url=final_url,
            file_name=file_name,
            total_size=total_size,
            etag=etag,
            last_modified=last_modified,
            supports_range=supports_range,
            connection_count=connection_count,
            segments=[_segment_to_dict(segment) for segment in segments],
        )

    def to_segments(self) -> list[DownloadSegment]:
        """还原分段对象（跳过损坏条目）。"""
        segments: list[DownloadSegment] = []
        for item in self.segments:
            try:
                segments.append(_segment_from_dict(item))
            except (KeyError, TypeError, ValueError) as exc:  # pragma: no cover
                _log.warning("忽略损坏的分段元数据：%s", exc)
        return segments


def _segment_to_dict(segment: DownloadSegment) -> dict[str, Any]:
    return {
        "index": segment.index,
        "start": segment.start,
        "end": segment.end,
        "current": segment.current,
        "state": segment.state.value,
        "retry_count": segment.retry_count,
    }


def _segment_from_dict(item: dict[str, Any]) -> DownloadSegment:
    return DownloadSegment(
        index=int(item["index"]),
        start=int(item["start"]),
        end=int(item["end"]),
        current=int(item.get("current", item["start"])),
        state=SegmentState(item.get("state", SegmentState.PENDING)),
        retry_count=int(item.get("retry_count", 0)),
    )


class PartStore:
    """管理单个任务的临时目录、数据文件与元数据。"""

    def __init__(self, target: Path, task_id: str) -> None:
        self.target = Path(target)
        self.task_id = task_id
        self.directory = self.target.parent / f"{self.target.name}{PART_DIR_SUFFIX}"
        self.data_path = self.directory / DATA_FILENAME
        self.meta_path = self.directory / META_FILENAME

    # -- 生命周期 ----------------------------------------------------------
    def prepare(self, total_size: int) -> None:
        """创建临时目录并按需预分配数据文件。"""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            if total_size > 0 and self.data_size() != total_size:
                # 以 a+b 打开可确保文件不存在时自动创建，再调整为期望大小
                with self.data_path.open("a+b") as handle:
                    handle.truncate(total_size)
        except OSError as exc:
            raise DownloadFileError(
                "无法创建下载临时文件，请检查保存位置权限。",
                detail=f"{self.data_path}: {exc}",
            ) from exc

    def exists(self) -> bool:
        return self.directory.exists()

    def save_meta(self, meta: PartMeta) -> None:
        """写入元数据（先写临时文件再替换，避免半截 JSON）。"""
        if not self.directory.exists():
            self.directory.mkdir(parents=True, exist_ok=True)
        temp_path = self.meta_path.with_suffix(".json.tmp")
        try:
            temp_path.write_text(
                json.dumps(meta.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temp_path, self.meta_path)
        except OSError as exc:
            raise DownloadFileError(
                "无法保存断点续传信息。", detail=f"{self.meta_path}: {exc}"
            ) from exc

    def load_meta(self) -> PartMeta | None:
        """读取元数据；不存在或损坏时返回 None。"""
        if not self.meta_path.exists():
            return None
        try:
            payload = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("断点续传元数据损坏，将重新下载：%s", exc)
            return None
        if not isinstance(payload, dict):  # pragma: no cover - 防御式分支
            return None
        try:
            return PartMeta.from_dict(payload)
        except TypeError as exc:  # pragma: no cover - 字段异常
            _log.warning("断点续传元数据字段异常：%s", exc)
            return None

    def data_size(self) -> int:
        """数据文件当前大小（不存在时返回 0）。"""
        try:
            return self.data_path.stat().st_size
        except OSError:
            return 0

    def cleanup(self) -> None:
        """删除临时目录（取消任务或重新下载时调用）。"""
        if not self.directory.exists():
            return
        try:
            for child in self.directory.iterdir():
                child.unlink(missing_ok=True)
            self.directory.rmdir()
        except OSError as exc:  # pragma: no cover - 文件被占用时保留
            _log.warning("清理临时目录失败 %s：%s", self.directory, exc)

    def finalize(self) -> Path:
        """把 data.part 原子重命名到最终文件（默认不覆盖已有文件）。"""
        destination = self.target
        if destination.exists():
            destination = unique_path(destination)
        try:
            os.replace(self.data_path, destination)
        except OSError as exc:
            raise DownloadFileError(
                "无法把临时文件移动到目标位置，请检查磁盘空间与权限。",
                detail=f"{self.data_path} -> {destination}: {exc}",
            ) from exc
        self._remove_meta_only()
        return destination

    def _remove_meta_only(self) -> None:
        try:
            self.meta_path.unlink(missing_ok=True)
            if self.directory.exists() and not any(self.directory.iterdir()):
                self.directory.rmdir()
        except OSError:  # pragma: no cover - 目录被占用时忽略
            pass

    # -- 兼容性校验 --------------------------------------------------------
    def validate_resume(self, meta: PartMeta, probe: ProbeResult) -> None:
        """校验服务器文件是否与上次一致；不一致时抛出异常。"""
        reasons: list[str] = []
        if meta.total_size and probe.total_size and meta.total_size != probe.total_size:
            reasons.append(f"文件大小由 {meta.total_size} 变为 {probe.total_size}")
        if meta.etag and probe.etag and meta.etag != probe.etag:
            reasons.append("ETag 已变化")
        if (
            meta.last_modified
            and probe.last_modified
            and meta.last_modified != probe.last_modified
        ):
            reasons.append("Last-Modified 已变化")
        if reasons:
            raise DownloadResumeError(
                "服务器上的文件已更新，需要重新下载。", detail="；".join(reasons)
            )
