"""重试策略（指数退避）。

只对可恢复错误重试：408 / 429 / 5xx、连接重置、读取超时等。
401 / 403 / 404 等明确不可恢复的错误不重试。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.core.common.constants import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_FACTOR,
    BACKOFF_JITTER,
    BACKOFF_MAX_SECONDS,
    FATAL_STATUS,
    MAX_RETRIES,
    RETRYABLE_STATUS,
)
from app.core.common.exceptions import DownloadNetworkError, FilePilotError


@dataclass(slots=True)
class RetryPolicy:
    """指数退避重试策略。"""

    max_retries: int = MAX_RETRIES
    base_delay: float = BACKOFF_BASE_SECONDS
    factor: float = BACKOFF_FACTOR
    max_delay: float = BACKOFF_MAX_SECONDS
    jitter: float = BACKOFF_JITTER
    retryable_status: frozenset[int] = RETRYABLE_STATUS
    fatal_status: frozenset[int] = FATAL_STATUS

    def should_retry(self, exc: BaseException, *, attempt: int) -> bool:
        """判断是否应继续重试（``attempt`` 从 0 开始）。"""
        if attempt >= self.max_retries:
            return False
        return is_retryable(exc, policy=self)

    def delay_for(self, attempt: int, *, rng: random.Random | None = None) -> float:
        """第 ``attempt`` 次重试前的等待时间（秒），带轻微抖动。"""
        raw = min(self.base_delay * (self.factor**attempt), self.max_delay)
        generator = rng or random
        if self.jitter <= 0:
            return raw
        spread = raw * self.jitter
        return max(0.0, raw + generator.uniform(-spread, spread))


def is_retryable(exc: BaseException, *, policy: RetryPolicy | None = None) -> bool:
    """判断异常是否属于可重试的临时错误。"""
    resolved = policy or RetryPolicy()
    if isinstance(exc, DownloadNetworkError):
        if exc.status is not None:
            if exc.status in resolved.fatal_status:
                return False
            return exc.status in resolved.retryable_status
        return exc.retryable
    if isinstance(exc, FilePilotError):
        # 其它业务异常（磁盘不足、路径非法等）不应重试
        return False
    # 网络层异常：连接重置 / 超时 / DNS 抖动
    import socket

    return isinstance(exc, TimeoutError | socket.timeout | ConnectionError | OSError)


def backoff_sequence(policy: RetryPolicy | None = None, *, count: int | None = None) -> list[float]:
    """返回退避序列（用于文档、日志与测试）。"""
    resolved = policy or RetryPolicy()
    size = resolved.max_retries if count is None else count
    return [
        min(resolved.base_delay * (resolved.factor**attempt), resolved.max_delay)
        for attempt in range(size)
    ]
