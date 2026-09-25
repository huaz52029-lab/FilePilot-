"""下载调度器：控制同时运行的文件任务数量。"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Coroutine
from typing import Any

from app.core.common.constants import DEFAULT_MAX_CONCURRENT_TASKS, MAX_CONNECTIONS
from app.core.common.logger import get_logger

_log = get_logger("download.scheduler")


class DownloadScheduler:
    """并发调度器。

    * 最多同时运行 ``max_concurrent`` 个任务，其余任务排队；
    * 支持运行时调整并发上限（设置页面即时生效）；
    * 单个任务内部的多个分段由任务自身管理，不额外占用槽位。
    """

    def __init__(self, *, max_concurrent: int = DEFAULT_MAX_CONCURRENT_TASKS) -> None:
        self._limit = max(1, min(MAX_CONNECTIONS, int(max_concurrent)))
        self._active = 0
        self._condition = asyncio.Condition()
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._queue: deque[str] = deque()

    # -- 属性 --------------------------------------------------------------
    @property
    def max_concurrent(self) -> int:
        return self._limit

    def set_max_concurrent(self, value: int) -> None:
        """调整并发上限（不会中断正在运行的任务）。"""
        self._limit = max(1, min(MAX_CONNECTIONS, int(value)))
        _log.info("下载并发上限已调整为 %s", self._limit)
        self._wake_waiters()

    @property
    def active_count(self) -> int:
        return self._active

    @property
    def queued_ids(self) -> list[str]:
        return [task_id for task_id in self._queue if task_id in self._jobs]

    @property
    def active_ids(self) -> list[str]:
        return [task_id for task_id in self._jobs if task_id not in self._queue]

    def is_running(self, task_id: str) -> bool:
        return task_id in self._jobs and task_id not in self._queue

    def is_queued(self, task_id: str) -> bool:
        return task_id in self._jobs and task_id in self._queue

    # -- 调度 --------------------------------------------------------------
    def submit(
        self,
        task_id: str,
        factory: Callable[[], Coroutine[Any, Any, None]],
        *,
        on_start: Callable[[], None] | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """提交任务；同一 task_id 的旧任务会被替换（用于续传）。"""
        previous = self._jobs.get(task_id)
        if previous is not None and not previous.done():  # pragma: no cover - 防御
            previous.cancel()
        if task_id in self._queue:
            self._queue.remove(task_id)
        self._queue.append(task_id)
        job = asyncio.create_task(
            self._run(task_id, factory, on_start=on_start, on_done=on_done),
            name=f"download-{task_id}",
        )
        self._jobs[task_id] = job

    async def _run(
        self,
        task_id: str,
        factory: Callable[[], Coroutine[Any, Any, None]],
        *,
        on_start: Callable[[], None] | None,
        on_done: Callable[[], None] | None,
    ) -> None:
        started = False
        try:
            await self._acquire()
            started = True
            if task_id in self._queue:
                self._queue.remove(task_id)
            if on_start is not None:
                on_start()
            await factory()
        except asyncio.CancelledError:
            _log.info("任务 %s 已取消", task_id)
            raise
        except Exception as exc:  # noqa: BLE001 - 状态由任务自身负责
            _log.error("任务 %s 执行失败：%s", task_id, exc)
        finally:
            if started:
                self._release()
                if on_done is not None:
                    on_done()

    async def _acquire(self) -> None:
        async with self._condition:
            while self._active >= self._limit:
                await self._condition.wait()
            self._active += 1

    def _release(self) -> None:
        self._active = max(0, self._active - 1)
        self._wake_waiters()

    def _wake_waiters(self) -> None:
        """通知等待中的任务重新检查并发槽位。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - 未处于事件循环
            return

        async def _notify() -> None:
            async with self._condition:
                self._condition.notify_all()

        loop.create_task(_notify())

    # -- 生命周期 ----------------------------------------------------------
    def cancel(self, task_id: str) -> bool:
        """取消排队或运行中的任务。"""
        job = self._jobs.get(task_id)
        if job is None or job.done():
            return False
        if task_id in self._queue:
            self._queue.remove(task_id)
        job.cancel()
        return True

    async def join(self) -> None:
        """等待全部任务结束。"""
        jobs = list(self._jobs.values())
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)

    async def shutdown(self) -> None:
        """取消所有任务并清空状态。"""
        jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        self._jobs.clear()
        self._queue.clear()
        self._active = 0
