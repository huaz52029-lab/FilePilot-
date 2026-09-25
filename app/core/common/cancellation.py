"""协作式取消令牌。

核心层只依赖 ``should_cancel`` 回调或 :class:`CancelToken`，不依赖任何 GUI 机制，
因此扫描 / 哈希 / 下载逻辑都能在后台线程中安全取消。
"""

from __future__ import annotations

import threading


class CancelledError(Exception):
    """任务被用户取消。"""

    def __init__(self, message: str = "任务已取消。") -> None:
        super().__init__(message)
        self.message = message


class CancelToken:
    """线程安全的取消标记。"""

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """请求取消。"""
        self._event.set()

    def reset(self) -> None:
        """清除取消状态，便于复用。"""
        self._event.clear()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """已取消时抛出 :class:`CancelledError`。"""
        if self._event.is_set():
            raise CancelledError

    def __bool__(self) -> bool:  # pragma: no cover - 避免误用
        return self.cancelled


def never_cancelled() -> bool:
    """默认的 ``should_cancel`` 回调。"""
    return False
