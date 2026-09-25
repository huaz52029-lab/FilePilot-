"""下载引擎门面。

对外接口（与需求一致）::

    probe / start / pause / resume / cancel / retry / delete_task

引擎完全独立于 GUI：全部状态变化通过 :class:`EngineCallbacks` 回调通知，
由服务层转换为 Qt 信号。
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import aiohttp

from app.core.common.exceptions import DownloadError, DownloadProbeError
from app.core.common.logger import get_logger
from app.core.download.config import EngineCallbacks, EngineOptions
from app.core.download.control import DownloadControl
from app.core.download.models import DownloadStatus, DownloadTask, ProbeResult
from app.core.download.probe import probe_url
from app.core.download.resume import PartStore
from app.core.download.scheduler import DownloadScheduler
from app.core.download.session import create_session
from app.core.download.task import TaskRunner
from app.core.storage.repositories import DownloadRepository

_log = get_logger("download.engine")


class DownloadEngine:
    """下载引擎（asyncio）。"""

    def __init__(
        self,
        *,
        repository: DownloadRepository,
        options: EngineOptions | None = None,
        callbacks: EngineCallbacks | None = None,
    ) -> None:
        self._repo = repository
        self._options = options or EngineOptions()
        self._callbacks = callbacks or EngineCallbacks()
        self._scheduler = DownloadScheduler(max_concurrent=self._options.max_concurrent_tasks)
        self._session: aiohttp.ClientSession | None = None
        self._tasks: dict[str, DownloadTask] = {}
        self._controls: dict[str, DownloadControl] = {}
        self._running = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """启动引擎（创建共享 HTTP 会话）。"""
        if self._session is None or self._session.closed:
            self._session = create_session()
        self._running = True
        _log.info("下载引擎已启动（并发上限 %s）", self._scheduler.max_concurrent)

    async def shutdown(self) -> None:
        """停止全部任务并释放资源。"""
        self._running = False
        for control in self._controls.values():
            control.request_cancel()
        await self._scheduler.shutdown()
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        _log.info("下载引擎已停止")

    def set_options(self, options: EngineOptions) -> None:
        """同步设置页面中的参数。"""
        self._options = options
        self._scheduler.set_max_concurrent(options.max_concurrent_tasks)

    @property
    def options(self) -> EngineOptions:
        return self._options

    @property
    def scheduler(self) -> DownloadScheduler:
        return self._scheduler

    @property
    def tasks(self) -> dict[str, DownloadTask]:
        return self._tasks

    def session_or_none(self) -> aiohttp.ClientSession | None:
        return self._session

    def _require_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            raise DownloadError("下载引擎尚未启动。")
        return self._session

    # ------------------------------------------------------------------
    # 探测
    # ------------------------------------------------------------------
    async def probe(self, url: str, save_dir: Path | None = None) -> ProbeResult:
        """分析链接（不下载）。"""
        _ = save_dir
        session = self._require_session()
        requested = self._options.connection_mode
        return await probe_url(session, url, requested_connections=requested)

    # ------------------------------------------------------------------
    # 任务
    # ------------------------------------------------------------------
    def create_task(
        self,
        probe: ProbeResult,
        *,
        save_dir: Path,
        connections: int | None = None,
        expected_sha256: str = "",
    ) -> DownloadTask:
        """按探测结果创建任务对象（尚未开始下载）。"""
        requested = (
            int(connections)
            if connections is not None and connections > 0
            else self._options.connections_for(probe.suggested_connections)
        )
        return DownloadTask(
            task_id=uuid.uuid4().hex,
            url=probe.url,
            final_url=probe.final_url,
            file_name=probe.file_name,
            save_dir=Path(save_dir),
            total_size=probe.total_size,
            content_type=probe.content_type,
            etag=probe.etag,
            last_modified=probe.last_modified,
            supports_range=probe.supports_range,
            can_resume=probe.can_resume,
            connection_count=probe.suggested_connections,
            requested_connections=requested,
            expected_sha256=(expected_sha256 or "").strip().lower(),
            status=DownloadStatus.PENDING,
            probe=probe,
        )

    async def start_task(
        self,
        task: DownloadTask,
        *,
        probe: ProbeResult | None = None,
        is_new: bool = False,
    ) -> None:
        """把任务加入调度队列。"""
        self._require_session()
        self._tasks[task.task_id] = task
        control = DownloadControl()
        self._controls[task.task_id] = control
        task.status = DownloadStatus.QUEUED
        task.error_message = ""
        if is_new:
            self._callbacks.task_added(task.snapshot())
        self._callbacks.task_updated(task.snapshot())

        def _factory() -> asyncio.Future[None]:
            runner = TaskRunner(
                task=task,
                session=self._require_session(),
                repository=self._repo,
                options=self._options,
                callbacks=self._callbacks,
                control=control,
                probe=probe,
            )
            return runner.run()

        def _on_start() -> None:
            if task.status is DownloadStatus.QUEUED:
                task.status = DownloadStatus.DOWNLOADING
            _log.info("开始下载：%s", task.file_name)
            self._callbacks.task_updated(task.snapshot())

        self._scheduler.submit(task.task_id, _factory, on_start=_on_start)
        _log.info("任务已入队：%s（%s）", task.file_name, task.task_id)

    async def load_unfinished(self) -> list[DownloadTask]:
        """载入未完成任务（程序启动时调用）。

        重启后没有任何任务在运行，因此运行态会被标记为“已暂停”。
        """
        tasks = self._repo.list_unfinished()
        restored: list[DownloadTask] = []
        for task in tasks:
            if task.status.is_active:
                task.status = DownloadStatus.PAUSED
                task.notice = "程序重启，任务已暂停，可继续下载（支持断点续传）"
            self._tasks[task.task_id] = task
            restored.append(task)
        return restored

    def get_task(self, task_id: str) -> DownloadTask | None:
        return self._tasks.get(task_id)

    # ------------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------------
    def pause(self, task_id: str) -> bool:
        """暂停任务（排队中的任务会直接出队）。"""
        control = self._controls.get(task_id)
        if control is None:
            return False
        control.request_pause()
        if self._scheduler.is_queued(task_id):
            self._scheduler.cancel(task_id)
            task = self._tasks.get(task_id)
            if task is not None:
                task.status = DownloadStatus.PAUSED
                task.notice = "已暂停，可随时继续（支持断点续传）"
                self._callbacks.task_updated(task.snapshot())
        return True

    def cancel(self, task_id: str) -> bool:
        """取消任务（保留临时文件以便重试）。"""
        control = self._controls.get(task_id)
        if control is None:
            return False
        control.request_cancel()
        if self._scheduler.is_queued(task_id):
            self._scheduler.cancel(task_id)
            task = self._tasks.get(task_id)
            if task is not None:
                task.status = DownloadStatus.CANCELLED
                task.notice = "任务已取消，可重试继续（临时文件已保留）"
                self._callbacks.task_updated(task.snapshot())
        return True

    async def resume(self, task_id: str) -> bool:
        """继续（或重试）一个任务；会重新探测服务器以校验文件是否变化。"""
        task = await self._lookup(task_id)
        if task is None:
            return False
        if task.status in {DownloadStatus.COMPLETED, DownloadStatus.DOWNLOADING}:
            return False
        task.status = DownloadStatus.PENDING
        task.error_message = ""
        await self.start_task(task, probe=None)
        return True

    async def retry(self, task_id: str) -> bool:
        """重试失败任务（等价于继续）。"""
        return await self.resume(task_id)

    async def delete_task(self, task_id: str, *, delete_files: bool = False) -> bool:
        """删除任务记录；可选删除未完成的临时文件。"""
        control = self._controls.pop(task_id, None)
        if control is not None:
            control.request_cancel()
        self._scheduler.cancel(task_id)
        task = self._tasks.pop(task_id, None)
        if task is None:
            task = self._repo.get_task(task_id, with_segments=False)
        if task is None:
            return False
        self._repo.delete_task(task_id)
        if delete_files:
            target = task.save_path or (Path(task.save_dir) / task.file_name)
            store = PartStore(target, task_id)
            store.cleanup()
        self._callbacks.task_removed(task_id)
        return True

    async def _lookup(self, task_id: str) -> DownloadTask | None:
        task = self._tasks.get(task_id)
        if task is not None:
            return task
        task = self._repo.get_task(task_id)
        if task is not None:
            self._tasks[task_id] = task
        return task

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    @staticmethod
    def validate_url(url: str) -> str:
        """校验并返回 URL；非法时抛出 :class:`DownloadProbeError`。"""
        from app.core.common.helpers import normalize_url

        normalized = normalize_url(url)
        if normalized is None:
            raise DownloadProbeError("链接格式不正确，请输入以 http:// 或 https:// 开头的地址。")
        return normalized
