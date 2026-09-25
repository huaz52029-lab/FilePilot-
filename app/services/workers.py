"""后台任务运行器。

所有耗时操作（扫描、哈希、空间分析、文件整理）都通过 :class:`TaskRunner`
提交到 ``QThreadPool`` 执行，避免阻塞 UI 线程。

约定：被提交的函数可以声明名为 ``report`` 与 ``token`` 的可选参数，
运行器会分别注入进度回调与取消令牌。
"""

from __future__ import annotations

import inspect
import os
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from app.core.common.cancellation import CancelledError, CancelToken
from app.core.common.exceptions import to_user_message
from app.core.common.logger import get_logger

_log = get_logger("services.workers")


class WorkerSignals(QObject):
    """后台任务的信号集合（始终在创建线程的上下文中派发）。"""

    started = Signal()
    progress = Signal(object)
    result = Signal(object)
    failed = Signal(str, str)
    cancelled = Signal()
    finished = Signal()


class FunctionWorker(QRunnable):
    """把一个可调用对象包装成 ``QRunnable``。"""

    def __init__(
        self,
        function: Callable[..., Any],
        *args: Any,
        token: CancelToken | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self._function = function
        self._args = args
        self._kwargs = kwargs
        self._token = token or CancelToken()
        self.setAutoDelete(True)

    # -- 控制 --------------------------------------------------------------
    @property
    def token(self) -> CancelToken:
        return self._token

    def cancel(self) -> None:
        """请求取消（协作式：任务需要在合适位置检查令牌）。"""
        self._token.cancel()

    # -- 执行 --------------------------------------------------------------
    @Slot()
    def run(self) -> None:
        self.signals.started.emit()
        try:
            self._inject_dependencies()
            value = self._function(*self._args, **self._kwargs)
        except CancelledError:
            _log.info("后台任务已取消：%s", getattr(self._function, "__name__", self._function))
            self.signals.cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - 后台任务不能把异常抛回线程池
            detail = traceback.format_exc()
            _log.error("后台任务失败：%s\n%s", exc, detail)
            self.signals.failed.emit(to_user_message(exc), detail)
        else:
            self.signals.result.emit(value)
        finally:
            self.signals.finished.emit()

    def _inject_dependencies(self) -> None:
        """按需注入 ``report`` / ``token`` 参数。"""
        try:
            parameters = inspect.signature(self._function).parameters
        except (TypeError, ValueError):  # pragma: no cover - 内建函数等
            return
        if "report" in parameters and "report" not in self._kwargs:
            self._kwargs["report"] = self.signals.progress.emit
        if "token" in parameters and "token" not in self._kwargs:
            self._kwargs["token"] = self._token


class TaskRunner(QObject):
    """应用级线程池封装。"""

    def __init__(self, parent: QObject | None = None, *, max_threads: int | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        cpu_count = os.cpu_count() or 4
        self._pool.setMaxThreadCount(max_threads or max(2, min(8, cpu_count)))
        self._workers: list[FunctionWorker] = []

    @property
    def pool(self) -> QThreadPool:
        return self._pool

    def submit(
        self,
        function: Callable[..., Any],
        *args: Any,
        on_result: Callable[[Any], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        on_progress: Callable[[Any], None] | None = None,
        on_finished: Callable[[], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
        token: CancelToken | None = None,
        **kwargs: Any,
    ) -> FunctionWorker:
        """提交后台任务并连接回调（回调在 UI 线程执行）。"""
        worker = FunctionWorker(function, *args, token=token, **kwargs)
        if on_result is not None:
            worker.signals.result.connect(on_result)
        if on_error is not None:
            worker.signals.failed.connect(on_error)
        if on_progress is not None:
            worker.signals.progress.connect(on_progress)
        if on_finished is not None:
            worker.signals.finished.connect(on_finished)
        if on_cancelled is not None:
            worker.signals.cancelled.connect(on_cancelled)
        worker.signals.finished.connect(lambda: self._forget(worker))
        self._workers.append(worker)
        self._pool.start(worker)
        return worker

    def _forget(self, worker: FunctionWorker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)

    def cancel_all(self) -> None:
        """请求取消所有仍在运行的任务。"""
        for worker in list(self._workers):
            worker.cancel()

    def wait_all(self, timeout_ms: int = 3000) -> bool:
        """等待所有任务结束（退出程序时调用）。"""
        return self._pool.waitForDone(timeout_ms)

    @property
    def active_count(self) -> int:
        return self._pool.activeThreadCount()
