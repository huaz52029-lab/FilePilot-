"""任务级控制信号（暂停 / 取消）与内部中断异常。"""

from __future__ import annotations

import threading


class DownloadInterrupt(Exception):
    """任务内部中断（不是错误，用于跳出下载循环）。"""


class PausedInterrupt(DownloadInterrupt):
    """用户暂停。"""


class CancelledInterrupt(DownloadInterrupt):
    """用户取消。"""


class RangeUnsupported(DownloadInterrupt):
    """服务器实际不支持 Range，需要回退到单连接。"""


class UrlExpiredError(Exception):
    """临时下载地址已失效（HTTP 618 / jwt:expired / 签名 URL 过期）。

    这类错误不是下载失败：重新向**原始 URL** 请求一次即可拿到新的临时地址，
    然后带着 ``Range`` 从已下载位置继续。
    """

    def __init__(self, status: int, detail: str = "") -> None:
        super().__init__(f"临时下载地址失效（HTTP {status}）")
        self.status = status
        self.detail = detail


class DownloadControl:
    """任务控制标记（可在任意线程设置，协程内轮询检查）。"""

    __slots__ = ("_pause", "_cancel")

    def __init__(self) -> None:
        self._pause = threading.Event()
        self._cancel = threading.Event()

    @property
    def paused(self) -> bool:
        return self._pause.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def request_pause(self) -> None:
        self._pause.set()

    def request_cancel(self) -> None:
        self._cancel.set()

    def clear_pause(self) -> None:
        self._pause.clear()

    def check(self) -> None:
        """检查控制信号，必要时抛出中断异常。"""
        if self._cancel.is_set():
            raise CancelledInterrupt
        if self._pause.is_set():
            raise PausedInterrupt

    async def sleep(self, seconds: float, *, step: float = 0.2) -> None:
        """可被打断的等待（用于重试退避）。"""
        import asyncio

        remaining = max(0.0, seconds)
        while remaining > 0:
            self.check()
            interval = min(step, remaining)
            await asyncio.sleep(interval)
            remaining -= interval
