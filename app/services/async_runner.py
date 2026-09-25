"""后台 asyncio 事件循环。

Qt 主线程负责界面，下载引擎运行在独立线程的 asyncio 事件循环中，
两者通过信号 / 回调通信，绝不阻塞 UI。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Callable, Coroutine
from typing import Any

from PySide6.QtCore import QObject

from app.core.common.logger import get_logger

_log = get_logger("services.async_runner")


class AsyncRunner(QObject):
    """在专用线程中运行 asyncio 事件循环。"""

    def __init__(self, parent: QObject | None = None, *, name: str = "filepilot-asyncio") -> None:
        super().__init__(parent)
        self._name = name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    # -- 生命周期 ----------------------------------------------------------
    def start(self) -> None:
        """启动事件循环线程（幂等）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name=self._name, daemon=True)
        self._thread.start()
        if not self._ready.wait(5.0):  # pragma: no cover - 极端情况
            _log.error("asyncio 事件循环启动超时")

    def _run_loop(self) -> None:
        loop = self._loop
        assert loop is not None
        asyncio.set_event_loop(loop)
        loop.call_soon(self._ready.set)
        try:
            loop.run_forever()
        finally:
            try:
                pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception as exc:  # noqa: BLE001 - 退出阶段尽力清理
                _log.warning("关闭事件循环时出现问题：%s", exc)
            finally:
                loop.close()
                _log.info("asyncio 事件循环已关闭")

    def stop(self, *, timeout: float = 8.0) -> None:
        """停止事件循环线程。"""
        loop = self._loop
        thread = self._thread
        if loop is None or thread is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout)
        if thread.is_alive():  # pragma: no cover - 极端情况
            _log.warning("asyncio 线程未在 %.1f 秒内退出", timeout)
        self._thread = None
        self._loop = None

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- 提交任务 ----------------------------------------------------------
    def submit(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        callback: Callable[[Any], None] | None = None,
        error_callback: Callable[[BaseException], None] | None = None,
    ) -> concurrent.futures.Future[Any]:
        """线程安全地提交协程。"""
        loop = self._loop
        if loop is None or not self.is_running:  # pragma: no cover - 调用顺序错误
            raise RuntimeError("asyncio 事件循环尚未启动")
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        if callback is not None or error_callback is not None:
            future.add_done_callback(
                lambda done: self._dispatch(done, callback, error_callback)
            )
        return future

    def call_soon(self, function: Callable[..., None], *args: Any) -> None:
        """在事件循环线程中执行同步函数。"""
        loop = self._loop
        if loop is None:  # pragma: no cover
            return
        loop.call_soon_threadsafe(function, *args)

    @staticmethod
    def _dispatch(
        future: concurrent.futures.Future[Any],
        callback: Callable[[Any], None] | None,
        error_callback: Callable[[BaseException], None] | None,
    ) -> None:
        try:
            result = future.result()
        except concurrent.futures.CancelledError:  # pragma: no cover
            return
        except BaseException as exc:  # noqa: BLE001 - 交给调用方处理
            if error_callback is not None:
                error_callback(exc)
            else:
                _log.error("后台协程执行失败：%s", exc)
            return
        if callback is not None:
            callback(result)

    def run_blocking(self, coro: Coroutine[Any, Any, Any], *, timeout: float = 8.0) -> Any:
        """同步等待协程结果（仅用于退出流程）。"""
        future = self.submit(coro)
        return future.result(timeout)
