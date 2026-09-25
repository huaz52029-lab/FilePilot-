"""下载引擎的 Qt 桥接层。

把 ``app.core.download`` 的纯 Python 回调转换成 Qt 信号，
使界面与引擎彻底解耦：

    Qt 主线程  ←信号——  DownloadBackend  ←回调——  DownloadEngine（asyncio 线程）
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from app.core.common.exceptions import to_user_message
from app.core.common.logger import get_logger
from app.core.download.config import EngineCallbacks, EngineOptions
from app.core.download.engine import DownloadEngine
from app.core.download.models import DownloadTask, ProbeResult
from app.core.storage.repositories import DownloadRepository
from app.services.async_runner import AsyncRunner
from app.services.settings_service import AppSettings

_log = get_logger("services.download_backend")


class DownloadBackend(QObject):
    """下载引擎与界面之间的桥梁。"""

    task_added = Signal(object)
    task_updated = Signal(object)
    task_removed = Signal(str)
    task_finished = Signal(object)
    notice = Signal(str, str)
    probe_ready = Signal(object)
    probe_failed = Signal(str)
    ready = Signal()

    def __init__(
        self,
        *,
        runner: AsyncRunner,
        repository: DownloadRepository,
        settings: AppSettings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner = runner
        self._repo = repository
        self._settings = settings
        callbacks = EngineCallbacks(
            on_task_added=lambda task: self.task_added.emit(task),
            on_task_updated=lambda task: self.task_updated.emit(task),
            on_task_removed=lambda task_id: self.task_removed.emit(task_id),
            on_task_finished=lambda task: self.task_finished.emit(task),
            on_notice=lambda level, message: self.notice.emit(level, message),
        )
        self._engine = DownloadEngine(
            repository=repository,
            options=self.collect_options(),
            callbacks=callbacks,
        )
        self._started = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    @property
    def engine(self) -> DownloadEngine:
        return self._engine

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        """在后台事件循环中启动引擎并载入未完成任务。"""
        self._runner.submit(self._start_async())

    async def _start_async(self) -> None:
        try:
            await self._engine.start()
            self._engine.set_options(self.collect_options())
            restored = await self._engine.load_unfinished()
            for task in restored:
                self.task_added.emit(task.snapshot())
            self._started = True
            self.ready.emit()
            _log.info("下载引擎已就绪，恢复 %s 个未完成任务", len(restored))
        except Exception as exc:  # noqa: BLE001 - 启动失败要给出可读提示
            _log.exception("下载引擎启动失败")
            self.notice.emit("error", to_user_message(exc))

    def stop(self) -> None:
        """停止引擎（退出流程调用）。"""
        if not self._runner.is_running:
            return
        try:
            self._runner.run_blocking(self._engine.shutdown(), timeout=10.0)
        except Exception as exc:  # noqa: BLE001
            _log.warning("停止下载引擎失败：%s", exc)
        self._started = False

    def collect_options(self) -> EngineOptions:
        """从设置生成引擎参数快照。"""
        settings = self._settings
        return EngineOptions(
            max_concurrent_tasks=settings.max_concurrent_tasks,
            connection_mode=settings.connection_mode,
            default_connections=settings.default_connections,
            auto_retry=settings.auto_retry,
            auto_verify=settings.auto_verify,
        )

    def refresh_options(self) -> None:
        """设置变化后同步到引擎。"""
        options = self.collect_options()
        if not self._started:
            self._engine.set_options(options)
            return
        self._runner.submit(self._apply_options(options))

    async def _apply_options(self, options: EngineOptions) -> None:
        self._engine.set_options(options)

    # ------------------------------------------------------------------
    # DownloadService 期望的接口
    # ------------------------------------------------------------------
    def probe(self, url: str, save_dir: Path) -> None:
        """分析链接（结果通过 ``probe_ready`` / ``probe_failed`` 返回）。"""
        self._runner.submit(self._probe_async(url, save_dir))

    async def _probe_async(self, url: str, save_dir: Path) -> None:
        try:
            result: ProbeResult = await self._engine.probe(url, save_dir)
        except Exception as exc:  # noqa: BLE001
            _log.warning("链接分析失败：%s", exc)
            self.probe_failed.emit(to_user_message(exc))
            return
        self.probe_ready.emit(result)

    def create_task(
        self,
        probe: ProbeResult,
        *,
        save_dir: Path,
        connections: int | None = None,
        expected_sha256: str = "",
    ) -> DownloadTask:
        """创建任务对象（同步，不会开始下载）。"""
        return self._engine.create_task(
            probe,
            save_dir=save_dir,
            connections=connections,
            expected_sha256=expected_sha256,
        )

    def start_task(self, task: DownloadTask) -> None:
        """把任务加入队列。"""
        self._runner.submit(self._start_task_async(task))

    async def _start_task_async(self, task: DownloadTask) -> None:
        try:
            await self._engine.start_task(task, is_new=True)
        except Exception as exc:  # noqa: BLE001
            self.notice.emit("error", to_user_message(exc))

    def pause(self, task_id: str) -> None:
        if not self._engine.pause(task_id):
            self.notice.emit("warning", "该任务当前无法暂停。")

    def cancel(self, task_id: str) -> None:
        if not self._engine.cancel(task_id):
            self.notice.emit("warning", "该任务当前无法取消。")

    def resume(self, task_id: str) -> None:
        self._runner.submit(self._resume_async(task_id))

    async def _resume_async(self, task_id: str) -> None:
        try:
            started = await self._engine.resume(task_id)
        except Exception as exc:  # noqa: BLE001
            self.notice.emit("error", to_user_message(exc))
            return
        if not started:
            self.notice.emit("warning", "该任务无需继续，或已经在下载中。")

    def retry(self, task_id: str) -> None:
        self._runner.submit(self._retry_async(task_id))

    async def _retry_async(self, task_id: str) -> None:
        try:
            started = await self._engine.retry(task_id)
        except Exception as exc:  # noqa: BLE001
            self.notice.emit("error", to_user_message(exc))
            return
        if started:
            self.notice.emit("info", "已重新开始下载。")
        else:
            self.notice.emit("warning", "该任务当前无法重试。")

    def delete_task(self, task_id: str, *, delete_files: bool = False) -> None:
        self._runner.submit(self._delete_async(task_id, delete_files))

    async def _delete_async(self, task_id: str, delete_files: bool) -> None:
        try:
            await self._engine.delete_task(task_id, delete_files=delete_files)
        except Exception as exc:  # noqa: BLE001
            _log.warning("删除任务失败：%s", exc)
            self.notice.emit("error", to_user_message(exc))

    # ------------------------------------------------------------------
    # 队列控制
    # ------------------------------------------------------------------
    def pause_all(self, task_ids: list[str]) -> None:
        for task_id in task_ids:
            self._engine.pause(task_id)

    def resume_all(self, task_ids: list[str]) -> None:
        for task_id in task_ids:
            self._runner.submit(self._resume_async(task_id))

    def on_settings_changed(self, key: str) -> None:
        """设置变更触发引擎参数刷新。"""
        if key in {
            "download.max_concurrent_tasks",
            "download.connections",
            "download.auto_retry",
            "download.auto_verify",
        }:
            self.refresh_options()
