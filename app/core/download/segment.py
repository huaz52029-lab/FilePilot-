"""分段管理：工作队列分配器与单分段下载器。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
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
    UrlExpiredError,
)
from app.core.download.models import DownloadSegment, SegmentState
from app.core.download.retry import RetryPolicy

_log = get_logger("download.segment")

RetryCallback = Callable[[DownloadSegment, BaseException, float, int], None]
UrlRefreshCallback = Callable[[], Awaitable[str]]

#: 同一任务内允许的“重新建立连接”次数上限，避免失效地址无限重试
MAX_URL_REFRESH: int = 5

#: 识别临时签名地址失效的关键信息
_EXPIRY_MARKERS: tuple[bytes, ...] = (
    b"jwt",
    b"expired",
    b"expire",
    b"signature",
    b"token",
    b"unauthorized",
)


def looks_like_url_expired(status: int, body: bytes) -> bool:
    """判断响应是否属于“临时下载地址失效”。

    覆盖 GitHub Release 常见的 ``618 jwt:expired``，以及签名 URL / CDN 在过期时
    返回的 401 / 403（响应体包含 jwt / expired / signature 等关键字）。
    """
    if status == 618:  # GitHub 使用的非标准状态码
        return True
    if status in {401, 403}:
        lowered = body.lower()
        return any(marker in lowered for marker in _EXPIRY_MARKERS)
    return False


#: 200 响应里出现这些片段说明服务端返回了错误页（签名 URL 过期常见）
_STRONG_EXPIRY_MARKERS: tuple[bytes, ...] = (
    b"<error",
    b"jwt",
    b"expiredrequest",
    b"signaturedoesnotmatch",
    b"accessdenied",
)


def body_looks_like_expiry_page(body: bytes) -> bool:
    """响应体是否明显是“地址过期 / 签名错误”的错误页。"""
    lowered = body.lower()
    return any(marker in lowered for marker in _STRONG_EXPIRY_MARKERS)


def response_snippet(body: bytes, limit: int = 2048) -> str:
    """把响应体片段转成便于日志阅读的文本。"""
    try:
        return body[:limit].decode("utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001 - 仅用于日志  # pragma: no cover
        return ""


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
        on_url_expired: UrlRefreshCallback | None = None,
        url_is_redirected: bool = False,
    ) -> None:
        self._session = session
        self._url = url
        self._data_path = data_path
        self._control = control
        self._retry = retry
        self._base_headers = base_headers
        self._on_retry = on_retry
        self._use_range = use_range
        self._on_url_expired = on_url_expired
        self._url_is_redirected = url_is_redirected
        self._refresh_count = 0

    @property
    def use_range(self) -> bool:
        """是否使用 Range 请求（回退单连接时会被关闭）。"""
        return self._use_range

    @use_range.setter
    def use_range(self, value: bool) -> None:
        self._use_range = bool(value)

    @property
    def url(self) -> str:
        return self._url

    @url.setter
    def url(self, value: str) -> None:
        """切换下载地址（临时签名地址刷新后调用）。"""
        self._url = value

    @property
    def refresh_count(self) -> int:
        return self._refresh_count

    @property
    def url_is_redirected(self) -> bool:
        """当前使用的是否为重定向 / 签名后的临时地址。"""
        return self._url_is_redirected

    @url_is_redirected.setter
    def url_is_redirected(self, value: bool) -> None:
        self._url_is_redirected = bool(value)

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
                except UrlExpiredError as exc:
                    # 临时地址失效：重新获取下载地址后从当前位置继续（不丢进度）
                    await self._handle_url_expired(exc)
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
                snippet = await self._peek_error_body(response)
                if self._url_is_redirected and body_looks_like_expiry_page(snippet):
                    raise UrlExpiredError(200, response_snippet(snippet))
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
                snippet = await self._peek_error_body(response)
                if looks_like_url_expired(status, snippet) or (
                    self._url_is_redirected and status in {403, 404}
                ):
                    raise UrlExpiredError(status, response_snippet(snippet))
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

    @staticmethod
    async def _peek_error_body(response: aiohttp.ClientResponse, limit: int = 2048) -> bytes:
        """读取错误响应的一小段内容用于判断原因（不读取完整响应体）。"""
        try:
            return await response.content.read(limit)
        except Exception:  # noqa: BLE001 - 读取失败时按普通错误处理
            return b""

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

    async def _handle_url_expired(self, exc: UrlExpiredError) -> None:
        """处理临时地址失效：重新获取新地址，保留已下载进度。"""
        _log.warning(
            "临时下载地址失效（HTTP %s，第 %s 次）：%s",
            exc.status,
            self._refresh_count + 1,
            exc.detail[:200],
        )
        if self._on_url_expired is None or self._refresh_count >= MAX_URL_REFRESH:
            raise DownloadNetworkError(
                "临时下载地址已失效且无法重新建立连接，请稍后重试。",
                status=exc.status,
                retryable=False,
                detail=exc.detail,
            )
        self._refresh_count += 1
        try:
            self._url = await self._on_url_expired()
        except DownloadNetworkError:
            raise
        except Exception as exc2:  # noqa: BLE001 - 统一转换成网络错误
            raise DownloadNetworkError(
                "重新获取下载地址失败，请检查网络后重试。",
                detail=str(exc2),
            ) from exc2
        self._url_is_redirected = True
        # 地址已更新，交给外层循环继续按 Range 下载剩余部分

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
