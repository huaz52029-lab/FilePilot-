"""下载链接探测。

只发 HEAD / 极小的 Range GET，不会真正下载文件内容。
"""

from __future__ import annotations

import asyncio
import re
from email.message import Message
from typing import Final
from urllib.parse import urlparse

import aiohttp

from app.core.common.constants import (
    CONNECTION_PLAN,
    LARGE_FILE_CONNECTIONS,
    MAX_CONNECTIONS,
    MIN_CONNECTIONS,
    PROBE_TIMEOUT,
)
from app.core.common.exceptions import (
    DownloadNetworkError,
    DownloadProbeError,
    describe_network_error,
)
from app.core.common.helpers import filename_from_url, is_http_url, sanitize_filename
from app.core.common.logger import get_logger
from app.core.download.models import ProbeResult
from app.core.download.retry import RetryPolicy

_log = get_logger("download.probe")

_CONTENT_RANGE_RE: Final[re.Pattern[str]] = re.compile(
    r"bytes\s+(?P<start>\d+)-(?P<end>\d+)/(?P<total>\d+|\*)", re.IGNORECASE
)
_STATUS_MESSAGES: Final[dict[int, str]] = {
    400: "服务器认为请求无效（400），请确认链接是否完整。",
    401: "服务器要求身份验证（401），无法直接下载。",
    403: "服务器拒绝访问（403），链接可能已过期或存在防盗链限制。",
    404: "文件不存在（404），请确认链接是否正确。",
    405: "该服务器不支持当前请求方式，已尝试其它探测方式。",
    410: "文件已被删除（410）。",
    429: "请求过于频繁（429），请稍后再试。",
    451: "该资源因法律原因不可用（451）。",
}


def _parse_content_disposition(value: str | None) -> str | None:
    """解析 ``Content-Disposition`` 中的文件名（含 RFC 5987）。"""
    if not value:
        return None
    message = Message()
    message["content-disposition"] = value
    filename = message.get_filename()
    if not filename:
        return None
    try:
        return sanitize_filename(filename)
    except Exception:  # noqa: BLE001 - 极端文件名不影响探测  # pragma: no cover
        return None


def _parse_content_range(value: str | None) -> tuple[int, int, int] | None:
    """解析 ``Content-Range`` → ``(start, end, total)``；``total`` 未知为 -1。"""
    if not value:
        return None
    match = _CONTENT_RANGE_RE.search(value)
    if not match:
        return None
    total_text = match.group("total")
    total = -1 if total_text == "*" else int(total_text)
    return int(match.group("start")), int(match.group("end")), total


def suggest_connections(
    total_size: int,
    *,
    supports_range: bool,
    requested: int = 0,
) -> int:
    """根据文件大小与 Range 支持情况推荐连接数。"""
    if not supports_range or total_size <= 0:
        return MIN_CONNECTIONS
    if requested > 0:
        return max(MIN_CONNECTIONS, min(MAX_CONNECTIONS, int(requested)))
    for threshold, connections in CONNECTION_PLAN:
        if total_size < threshold:
            return connections
    return LARGE_FILE_CONNECTIONS


async def probe_url(
    session: aiohttp.ClientSession,
    url: str,
    *,
    requested_connections: int = 0,
    timeout: float = PROBE_TIMEOUT,
    verify_range: bool = True,
    retry: RetryPolicy | None = None,
) -> ProbeResult:
    """探测 URL 并返回服务器能力。

    流程：HEAD → （不可靠时）Range GET → 汇总文件名 / 大小 / 类型 / ETag 等。
    """
    if not is_http_url(url):
        raise DownloadProbeError("链接格式不正确，请提供 http:// 或 https:// 地址。")

    policy = retry or RetryPolicy()
    attempt = 0
    _log.info("开始探测：%s", url)
    while True:
        try:
            head_info = await _head(session, url, timeout=timeout)
            result = await _finalize(
                session,
                url,
                head_info,
                requested_connections=requested_connections,
                timeout=timeout,
                verify_range=verify_range,
            )
        except DownloadNetworkError as exc:
            if not policy.should_retry(exc, attempt=attempt):
                raise
            delay = policy.delay_for(attempt)
            attempt += 1
            _log.warning("探测失败，%.1f 秒后重试（第 %s 次）：%s", delay, attempt, exc)
            await asyncio.sleep(delay)
            continue
        _log.info(
            "探测完成：%s（大小 %s，Range=%s，推荐连接 %s）",
            result.file_name,
            result.total_size or "未知",
            result.supports_range,
            result.suggested_connections,
        )
        return result


async def _head(
    session: aiohttp.ClientSession,
    url: str,
    *,
    timeout: float,
) -> dict[str, object]:
    """发起 HEAD 请求；失败或信息不足时返回可用的部分结果。"""
    try:
        async with session.head(
            url, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True
        ) as response:
            payload: dict[str, object] = {
                "status": response.status,
                "headers": dict(response.headers),
                "url": str(response.url),
            }
    except TimeoutError as exc:
        raise DownloadProbeError("分析超时：服务器在限定时间内没有响应。") from exc
    except aiohttp.ClientError as exc:
        raise DownloadNetworkError(
            describe_network_error(exc), retryable=True, detail=str(exc)
        ) from exc
    except OSError as exc:
        raise DownloadNetworkError(describe_network_error(exc), detail=str(exc)) from exc

    status = int(payload["status"])  # type: ignore[arg-type]
    headers = payload["headers"]  # type: ignore[assignment]
    assert isinstance(headers, dict)
    if status in _STATUS_MESSAGES and status != 405:
        raise DownloadProbeError(_STATUS_MESSAGES[status], detail=f"HEAD {status}")
    if status >= 400:
        _log.info("HEAD 返回 %s，将改用 Range GET 探测", status)
        payload["status"] = 0
        payload["headers"] = {}
    return payload


async def _finalize(
    session: aiohttp.ClientSession,
    url: str,
    head_info: dict[str, object],
    *,
    requested_connections: int,
    timeout: float,
    verify_range: bool,
) -> ProbeResult:
    """结合 HEAD 与（必要时的）Range GET 结果生成最终探测结论。"""
    headers: dict[str, str] = {
        str(key).lower(): str(value)
        for key, value in dict(head_info.get("headers") or {}).items()
    }
    final_url = str(head_info.get("url") or url)
    status = int(head_info.get("status") or 0)

    total_size = _int_or_zero(headers.get("content-length"))
    supports_range = headers.get("accept-ranges", "").lower() == "bytes"
    need_range_probe = verify_range and (
        status == 0 or total_size <= 0 or not supports_range
    )

    if need_range_probe:
        range_info = await _range_probe(session, url, timeout=timeout)
        if range_info is not None:
            range_status, range_headers, range_url = range_info
            final_url = range_url
            if range_status == 206:
                parsed = _parse_content_range(range_headers.get("content-range"))
                if parsed is not None:
                    total_size = parsed[2] if parsed[2] > 0 else total_size
                supports_range = True
                headers = {**headers, **range_headers}
            else:
                # 服务器忽略了 Range，返回完整内容 → 不支持分段
                supports_range = False
                if total_size <= 0:
                    total_size = _int_or_zero(range_headers.get("content-length"))
                headers = {**headers, **range_headers}
        elif status == 0:
            raise DownloadProbeError("无法从服务器获取文件信息，请稍后重试。")

    content_type = headers.get("content-type", "application/octet-stream").split(";")[0].strip()
    file_name = (
        _parse_content_disposition(headers.get("content-disposition"))
        or filename_from_url(final_url)
    )
    etag = headers.get("etag", "")
    last_modified = headers.get("last-modified", "")
    can_resume = bool(supports_range and total_size > 0 and (etag or last_modified))

    note = ""
    if not supports_range:
        note = "服务器不支持 Range 分段下载，将使用稳定的单连接模式。"
    elif not total_size:
        note = "服务器未提供文件大小，无法显示完整进度，但下载仍可进行。"
    elif not can_resume:
        note = "服务器未提供 ETag / Last-Modified，暂停后需要重新下载已丢失的部分。"

    return ProbeResult(
        url=url,
        final_url=final_url,
        file_name=file_name,
        total_size=total_size,
        content_type=content_type,
        supports_range=supports_range,
        accept_ranges=headers.get("accept-ranges", ""),
        etag=etag,
        last_modified=last_modified,
        server=headers.get("server", ""),
        is_https=urlparse(final_url).scheme == "https",
        can_resume=can_resume,
        suggested_connections=suggest_connections(
            total_size, supports_range=supports_range, requested=requested_connections
        ),
        status=status or 200,
        note=note,
    )


async def _range_probe(
    session: aiohttp.ClientSession,
    url: str,
    *,
    timeout: float,
) -> tuple[int, dict[str, str], str] | None:
    """发送 ``Range: bytes=0-0`` 探测服务器是否真正支持分段。"""
    try:
        async with session.get(
            url,
            headers={"Range": "bytes=0-0"},
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True,
        ) as response:
            headers = {key.lower(): value for key, value in response.headers.items()}
            # 只读取 1 字节，避免触发完整下载
            await response.content.read(1)
            return response.status, headers, str(response.url)
    except TimeoutError:
        return None
    except aiohttp.ClientError as exc:
        raise DownloadNetworkError(
            describe_network_error(exc), retryable=True, detail=str(exc)
        ) from exc
    except OSError as exc:
        raise DownloadNetworkError(describe_network_error(exc), detail=str(exc)) from exc


def _int_or_zero(value: str | None) -> int:
    if not value:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        return 0
