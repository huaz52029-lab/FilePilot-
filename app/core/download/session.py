"""aiohttp 会话工厂：连接池、超时与默认请求头。"""

from __future__ import annotations

import aiohttp

from app.core.common.constants import (
    CONNECT_TIMEOUT,
    MAX_CONNECTIONS,
    READ_TIMEOUT,
    TOTAL_TIMEOUT,
    USER_AGENT,
)


def build_timeout(
    *,
    total: float = TOTAL_TIMEOUT,
    connect: float = CONNECT_TIMEOUT,
    read: float = READ_TIMEOUT,
) -> aiohttp.ClientTimeout:
    """构造下载用超时配置（默认不限制整体时长，允许大文件长时间下载）。"""
    return aiohttp.ClientTimeout(
        total=total if total > 0 else None,
        connect=connect,
        sock_read=read,
    )


def build_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """默认请求头。"""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",  # 关闭压缩，保证 Range 偏移与文件大小准确
    }
    if extra:
        headers.update(extra)
    return headers


def create_connector(*, limit_per_host: int = MAX_CONNECTIONS) -> aiohttp.TCPConnector:
    """连接池：允许多个分段复用连接，同时保持 Keep-Alive。"""
    return aiohttp.TCPConnector(
        limit=max(limit_per_host * 2, 16),
        limit_per_host=limit_per_host,
        ttl_dns_cache=300,
        enable_cleanup_closed=True,
        force_close=False,
    )


def create_session(
    *,
    timeout: aiohttp.ClientTimeout | None = None,
    limit_per_host: int = MAX_CONNECTIONS,
) -> aiohttp.ClientSession:
    """创建下载引擎使用的共享会话。"""
    return aiohttp.ClientSession(
        connector=create_connector(limit_per_host=limit_per_host),
        timeout=timeout or build_timeout(),
        headers=build_headers(),
        auto_decompress=False,  # 与 Accept-Encoding: identity 保持一致
        raise_for_status=False,
    )
