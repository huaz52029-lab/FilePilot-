"""分段管理：工作队列分配器与单分段下载器。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import aiohttp

from app.core.common.constants import READ_CHUNK_SIZE, RETRYABLE_STATUS
from app.core.common.exceptions import (
    DownloadNetworkError,
    DownloadRangeError,
    describe_network_error,
)
from app.core.common.logger import get_logger
from app.core.download.control import (
    CancelledInterrupt,
    DownloadControl,
    DownloadInterrupt,
    PausedInterrupt,
    RangeUnsupported,
)
from app.core.download.models import DownloadSegment, SegmentState
from app.core.download.retry import RetryPolicy

_log = get_logger("download.segment")

RetryCallback = Callable[[DownloadSegment, BaseException, float, int], None]


class SegmentAllocator:
    """分段工作队列。

    * 多个 worker 从队列中领取分段，完成后继续领取下一段；
    * 支持运行时拆分（用于自适应增加连接数）；
    * 所有方法都在事件循环线程内调用，因此无需加锁。
    """

    def __init__(self, chunks: list[DownloadSegment]) -> None:
        self._chunks = chunks
        self._next_index = max((chunk.index for chunk in chunks), default=-1) + 1

    # -- 查询 --------------------------------------------------------------
    @property
    def chunks(self) -> list[DownloadSegment]:
        return self._chunks

    def snapshot(self) -> list[DownloadSegment]:
        """返回分段副本（用于持久化与界面展示）。"""
        return [replace(chunk) for chunk in self._chunks]

    @property
    def total(self) -> int:
        return sum(chunk.length for chunk in self._chunks)

    @property
    def downloaded(self) -> int:
        return sum(chunk.written for chunk in self._chunks)

    @property
    def remaining(self) -> int:
        return max(self.total - self.downloaded, 0)

    @property
    def completed(self) -> int:
        return sum(1 for chunk in self._chunks if chunk.is_complete)

    @property
    def is_finished(self) -> bool:
        return all(chunk.is_complete for chunk in self._chunks)

    @property
    def running(self) -> int:
        return sum(1 for chunk in self._chunks if chunk.state is SegmentState.RUNNING)

    # -- 调度 --------------------------------------------------------------
    def next_chunk(self) -> DownloadSegment | None:
        """领取下一个未完成分段（优先剩余量最大者）。"""
        candidates = [
            chunk
            for chunk in self._chunks
            if not chunk.is_complete and chunk.state is not SegmentState.RUNNING
        ]
        if not candidates:
            return None
        chunk = max(candidates, key=lambda item: (item.remaining, -item.index))
        chunk.state = SegmentState.RUNNING
        chunk.error = ""
        return chunk

    def release_chunk(self, chunk: DownloadSegment, *, done: bool = False) -> None:
        """归还分段；未完成时置为待下载以便其它 worker 继续。"""
        if done or chunk.is_complete:
            chunk.mark_complete()
        elif chunk.state is SegmentState.RUNNING:
            chunk.state = SegmentState.PENDING

    def split_largest(self) -> bool:
        """把剩余量最大的分段一分为二（用于自适应提升并发）。"""
        candidates = [chunk for chunk in self._chunks if chunk.remaining > 1]
        if not candidates:
            return False
        target = max(candidates, key=lambda item: item.remaining)
        midpoint = target.current + target.remaining // 2
        if midpoint > target.end:
            return False
        split = DownloadSegment(index=self._next_index, start=midpoint, end=target.end)
        self._next_index += 1
        target.end = midpoint - 1
        self._chunks.append(split)
        _log.debug("分段拆分：%s → [%s-%s] + [%s-%s]", target.index, target.start, target.end, split.start, split.end)
        return True

    @classmethod
    def from_total(cls, total_size: int, count: int) -> SegmentAllocator:
        """按总大小与连接数创建分配器。"""
        return cls(DownloadSegment.split(total_size, count))


class SegmentWorker:
    """下载单个分段的协程（含重试与进度更新）。"""

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession,
        url: str,
        data_path: Path,
        control: DownloadControl,
        retry: RetryPolicy,
        base_headers: dict[str, str],
        on_retry: RetryCallback | None = None,
        use_range: bool = True,
    ) -> None:
        self._session = session
        self._url = url
        self._data_path = data_path
        self._control = control
        self._retry = retry
        self._base_headers = base_headers
        self._on_retry = on_retry
        self._use_range = use_range

    @property
    def use_range(self) -> bool:
        """是否使用 Range 请求（回退单连接时会被关闭）。"""
        return self._use_range

    @use_range.setter
    def use_range(self, value: bool) -> None:
        self._use_range = bool(value)

    async def run(self, chunk: DownloadSegment) -> None:
        """把分段下载完整；失败会按策略重试，不可恢复时抛出异常。"""
        try:
            handle = self._data_path.open("r+b")
        except OSError as exc:
            raise DownloadNetworkError(
                "无法写入下载临时文件，请检查磁盘空间。", retryable=False, detail=str(exc)
            ) from exc
        try:
            while chunk.current <= chunk.end:
                self._control.check()
                position_before = chunk.current
                try:
                    await self._fetch_chunk(handle, chunk)
                except DownloadInterrupt:
                    raise
                except (
                    aiohttp.ClientError,
                    TimeoutError,
                    OSError,
                    DownloadNetworkError,
                ) as exc:
                    if not self._retry.should_retry(exc, attempt=chunk.retry_count):
                        raise self._as_download_error(exc) from exc
                    chunk.retry_count += 1
                    delay = self._retry.delay_for(chunk.retry_count - 1)
                    chunk.error = describe_network_error(exc)
                    if self._on_retry is not None:
                        self._on_retry(chunk, exc, delay, chunk.retry_count)
                    await self._control.sleep(delay)
                    if chunk.current == position_before:
                        # 完全没有进展：继续重试，但不要无限循环（已由 retry_count 限制）
                        continue
            chunk.mark_complete()
        finally:
            handle.close()

    # ------------------------------------------------------------------
    async def _fetch_chunk(self, handle, chunk: DownloadSegment) -> None:  # type: ignore[no-untyped-def]
        headers = dict(self._base_headers)
        if self._use_range:
            headers["Range"] = f"bytes={chunk.current}-{chunk.end}"
        async with self._session.get(self._url, headers=headers) as response:
            status = response.status
            if status == 200 and self._use_range:
                # 服务器忽略 Range，返回完整内容
                raise RangeUnsupported("服务器未按 Range 返回分段数据")
            if status == 416:
                # 请求区间超出文件末尾：说明该分段已经写完
                if chunk.current > chunk.end:
                    return
                raise DownloadRangeError(
                    "服务器拒绝了分段请求（416），可能文件已变化。",
                    detail=f"range={chunk.current}-{chunk.end}",
                )
            if status not in {200, 206}:
                raise self._status_error(status)

            if self._use_range:
                self._validate_range(response, chunk)
            else:
                # 单连接模式：服务器返回完整内容，从头顺序写入
                chunk.current = chunk.start
                handle.seek(chunk.current)
            async for data in response.content.iter_chunked(READ_CHUNK_SIZE):
                if not data:
                    continue
                self._control.check()
                offset = chunk.current
                remaining = chunk.end - offset + 1
                if len(data) > remaining:
                    data = data[:remaining]
                handle.seek(offset)
                handle.write(data)
                chunk.current = offset + len(data)
                if chunk.current > chunk.end:
                    break
        handle.flush()

    async def stream_unknown_size(self, *, start: int = 0) -> int:
        """流式下载未知大小的文件，返回已写入字节数。

        服务器未提供 Content-Length 时无法分段、也无法显示百分比，
        但仍然可以稳定地下载。
        """
        written = start
        attempt = 0
        while True:
            self._control.check()
            try:
                headers = {} if start == 0 else {"Range": f"bytes={start}-"}
                async with self._session.get(self._url, headers=headers) as response:
                    if response.status not in {200, 206}:
                        raise self._status_error(response.status)
                    if response.status == 200 and start > 0:
                        # 服务器忽略 Range：从头开始
                        start = 0
                        written = 0
                    mode = "r+b" if self._data_path.exists() else "wb"
                    with self._data_path.open(mode) as handle:
                        handle.seek(written)
                        async for data in response.content.iter_chunked(READ_CHUNK_SIZE):
                            if not data:
                                continue
                            self._control.check()
                            handle.write(data)
                            written += len(data)
                        handle.flush()
                    return written
            except DownloadInterrupt:
                raise
            except (aiohttp.ClientError, TimeoutError, OSError, DownloadNetworkError) as exc:
                if not self._retry.should_retry(exc, attempt=attempt):
                    raise self._as_download_error(exc) from exc
                attempt += 1
                start = written
                delay = self._retry.delay_for(attempt - 1)
                if self._on_retry is not None:
                    from app.core.download.models import DownloadSegment

                    self._on_retry(
                        DownloadSegment(index=0, start=0, end=-1, current=written),
                        exc,
                        delay,
                        attempt,
                    )
                await self._control.sleep(delay)

    def _validate_range(self, response: aiohttp.ClientResponse, chunk: DownloadSegment) -> None:
        """校验 Content-Range 是否与请求一致，避免写入错位。"""
        content_range = response.headers.get("Content-Range", "")
        if not content_range:
            raise DownloadRangeError(
                "服务器未返回 Content-Range，无法确认分段位置。",
                detail=f"range={chunk.current}-{chunk.end}",
            )
        if not content_range.lower().startswith("bytes "):
            raise DownloadRangeError(
                "服务器的 Content-Range 格式不正确。", detail=content_range
            )
        try:
            span = content_range.split(" ", 1)[1].split("/", 1)[0]
            start_text = span.split("-", 1)[0]
            start = int(start_text)
        except (IndexError, ValueError) as exc:
            raise DownloadRangeError(
                "无法解析服务器返回的分段信息。", detail=content_range
            ) from exc
        if start != chunk.current:
            raise DownloadRangeError(
                "服务器返回的分段起点与请求不一致。",
                detail=f"期望 {chunk.current}，实际 {start}",
            )

    @staticmethod
    def _status_error(status: int) -> DownloadNetworkError:
        retryable = status in RETRYABLE_STATUS
        if retryable:
            message = f"服务器暂时不可用（HTTP {status}），稍后将自动重试。"
        elif status in {401, 403}:
            message = f"服务器拒绝访问（HTTP {status}），链接可能已过期。"
        elif status == 404:
            message = "文件不存在（HTTP 404）。"
        else:
            message = f"服务器返回了意外状态：HTTP {status}。"
        return DownloadNetworkError(message, status=status, retryable=retryable)

    @staticmethod
    def _as_download_error(exc: BaseException) -> BaseException:
        if isinstance(exc, DownloadNetworkError | DownloadRangeError):
            return exc
        if isinstance(exc, aiohttp.ClientResponseError) and exc.status == 404:
            return DownloadNetworkError("文件不存在（HTTP 404）。", status=404, retryable=False)
        return DownloadNetworkError(describe_network_error(exc), detail=str(exc))


async def cancel_pending(tasks: list[asyncio.Task[None]]) -> None:
    """取消尚未结束的分段任务并等待收尾。"""
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


__all__ = [
    "CancelledInterrupt",
    "PausedInterrupt",
    "SegmentAllocator",
    "SegmentWorker",
    "cancel_pending",
]
