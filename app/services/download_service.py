"""下载服务门面（Qt 侧接口）。

UI 只与本类交互；真正的下载逻辑位于 ``app.core.download``（Phase 2 起接入）。
本类负责：

* 维护任务的内存缓存（数据来源为 SQLite）；
* 通过 Qt 信号把状态变化推送给界面；
* 把用户操作转发给下载引擎。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from app.core.common.exceptions import FilePilotError, TaskNotImplementedError
from app.core.common.logger import get_logger
from app.core.download.models import (
    DownloadStats,
    DownloadStatus,
    DownloadTask,
    ProbeResult,
)
from app.core.storage.models import HistoryKind, HistoryStatus
from app.core.storage.repositories import DownloadRepository, HistoryRepository
from app.services.settings_service import AppSettings

_log = get_logger("services.downloads")


class DownloadService(QObject):
    """下载任务的应用服务。"""

    task_added = Signal(object)
    task_updated = Signal(object)
    task_removed = Signal(str)
    stats_changed = Signal(object)
    notice = Signal(str, str)  # level, message
    probe_ready = Signal(object)
    probe_failed = Signal(str)
    ready = Signal()

    def __init__(
        self,
        repository: DownloadRepository,
        history: HistoryRepository,
        settings: AppSettings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._repo = repository
        self._history = history
        self._settings = settings
        self._tasks: dict[str, DownloadTask] = {}
        self._backend: Any | None = None
        self._archiver: Callable[[DownloadTask], None] | None = None

    # ------------------------------------------------------------------
    # 引擎接入（Phase 2 起由 AppContext 注入）
    # ------------------------------------------------------------------
    def attach_backend(self, backend: Any) -> None:
        """绑定下载引擎后端并把其信号转发到界面层。"""
        self._backend = backend
        backend.task_added.connect(lambda task: self._store(task, is_new=True))
        backend.task_updated.connect(lambda task: self._store(task))
        backend.task_removed.connect(self.notify_task_removed)
        backend.notice.connect(self.notice.emit)
        backend.probe_ready.connect(self.probe_ready.emit)
        backend.probe_failed.connect(self.probe_failed.emit)
        backend.task_finished.connect(self._on_task_finished)
        backend.ready.connect(self.ready.emit)

    def set_archiver(self, archiver: Callable[[DownloadTask], None] | None) -> None:
        """注入“下载完成自动归档”处理函数。

        归档在后台线程执行（可能涉及跨分区复制），因此回调不返回结果，
        完成后由归档实现自行提示用户。
        """
        self._archiver = archiver

    def start_backend(self) -> None:
        """启动后台下载引擎。"""
        if self._backend is not None:
            self._backend.start()

    @property
    def has_backend(self) -> bool:
        return self._backend is not None

    def _require_backend(self) -> Any:
        if self._backend is None:
            raise TaskNotImplementedError("下载引擎", phase="第 2 阶段")
        return self._backend

    # ------------------------------------------------------------------
    # 数据访问
    # ------------------------------------------------------------------
    def load_from_database(self) -> int:
        """从数据库载入任务缓存（程序启动时调用）。"""
        if self._backend is not None:
            # 有引擎时由引擎负责载入未完成任务并通过信号同步
            return 0
        tasks = self._repo.list_tasks(order_by="created_at DESC", with_segments=False)
        self._tasks = {task.task_id: task for task in tasks}
        for task in tasks:
            self.task_added.emit(task)
        self.emit_stats()
        _log.info("已从数据库载入 %s 个下载任务", len(tasks))
        return len(tasks)

    def tasks(
        self,
        *,
        statuses: Iterable[DownloadStatus] | None = None,
        newest_first: bool = True,
    ) -> list[DownloadTask]:
        """返回内存中的任务列表。"""
        items = list(self._tasks.values())
        if statuses is not None:
            allowed = set(statuses)
            items = [task for task in items if task.status in allowed]
        items.sort(key=lambda task: task.created_at, reverse=newest_first)
        return items

    def active_tasks(self) -> list[DownloadTask]:
        return self.tasks(statuses=[s for s in DownloadStatus if s.is_active])

    def queued_tasks(self) -> list[DownloadTask]:
        return self.tasks(statuses=[DownloadStatus.PENDING, DownloadStatus.QUEUED])

    def completed_tasks(self) -> list[DownloadTask]:
        return self.tasks(
            statuses=[DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.CANCELLED]
        )

    def task(self, task_id: str) -> DownloadTask | None:
        return self._tasks.get(task_id)

    def stats(self) -> DownloadStats:
        """汇总统计（首页与下载页底部使用）。"""
        active = 0
        queued = 0
        completed = 0
        failed = 0
        paused = 0
        speed = 0.0
        for task in self._tasks.values():
            match task.status:
                case DownloadStatus.DOWNLOADING | DownloadStatus.RETRYING | DownloadStatus.PROBING:
                    active += 1
                    speed += task.speed.current
                case DownloadStatus.PENDING | DownloadStatus.QUEUED:
                    queued += 1
                case DownloadStatus.COMPLETED:
                    completed += 1
                case DownloadStatus.FAILED:
                    failed += 1
                case DownloadStatus.PAUSED:
                    paused += 1
        start_of_day = _start_of_today()
        try:
            today = self._repo.total_downloaded_since(start_of_day)
        except FilePilotError:
            today = 0
        return DownloadStats(
            active_count=active,
            queued_count=queued,
            completed_count=completed,
            failed_count=failed,
            paused_count=paused,
            total_speed=speed,
            total_downloaded_today=today,
        )

    def emit_stats(self) -> None:
        """主动推送统计变化。"""
        self.stats_changed.emit(self.stats())

    # ------------------------------------------------------------------
    # 任务状态更新（由引擎回调）
    # ------------------------------------------------------------------
    def _store(self, task: DownloadTask, *, is_new: bool = False) -> None:
        self._tasks[task.task_id] = task
        if is_new:
            self.task_added.emit(task)
        self.task_updated.emit(task)

    def notify_task_changed(self, task: DownloadTask, *, is_new: bool = False) -> None:
        """引擎侧状态变化入口。"""
        self._store(task, is_new=is_new)
        self.emit_stats()

    def notify_task_removed(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)
        self.task_removed.emit(task_id)
        self.emit_stats()

    def _on_task_finished(self, task: DownloadTask) -> None:
        """任务结束（完成 / 失败 / 取消）——在 Qt 主线程执行。"""
        self._store(task)
        if task.status is DownloadStatus.COMPLETED and self._archiver is not None:
            try:
                self._archiver(task)
            except Exception as exc:  # noqa: BLE001 - 归档失败不影响下载结果
                _log.warning("自动归档失败：%s", exc)

        match task.status:
            case DownloadStatus.COMPLETED:
                detail = "下载完成"
                if task.sha256:
                    verified = task.hash_verified
                    detail += (
                        "，SHA-256 校验通过"
                        if verified is True
                        else "，SHA-256 校验失败"
                        if verified is False
                        else "，已计算 SHA-256"
                    )
                self.record_history(
                    task, action="completed", status=HistoryStatus.SUCCESS, detail=detail
                )
                if self._settings.notify_completed:
                    self.notice.emit("success", f"{task.file_name} 下载完成")
            case DownloadStatus.FAILED:
                self.record_history(
                    task,
                    action="failed",
                    status=HistoryStatus.FAILED,
                    detail=task.error_message or "下载失败",
                )
                if self._settings.notify_errors:
                    self.notice.emit(
                        "error",
                        f"{task.file_name} 下载失败：{task.error_message or '未知原因'}",
                    )
            case DownloadStatus.CANCELLED:
                self.record_history(
                    task, action="cancelled", status=HistoryStatus.WARNING, detail="任务已取消"
                )
            case _:
                pass
        self.emit_stats()

    def notify_error(self, message: str) -> None:
        self.notice.emit("error", message)

    def notify_info(self, message: str) -> None:
        self.notice.emit("info", message)

    def record_history(
        self,
        task: DownloadTask,
        *,
        action: str,
        status: HistoryStatus = HistoryStatus.SUCCESS,
        detail: str = "",
    ) -> None:
        """写入下载历史。"""
        try:
            self._history.add(
                kind=HistoryKind.DOWNLOAD,
                action=action,
                title=task.file_name,
                detail=detail or task.status_label,
                path=task.display_path,
                url=task.final_url or task.url,
                size=task.total_size,
                status=status,
            )
        except FilePilotError as exc:
            _log.warning("写入下载历史失败：%s", exc)

    # ------------------------------------------------------------------
    # 用户操作（Phase 2 起生效）
    # ------------------------------------------------------------------
    def probe(self, url: str, save_dir: Path | None = None) -> None:
        """分析 URL 并返回探测结果。"""
        self._require_backend().probe(url, save_dir or self._settings.download_dir)

    def start(
        self,
        probe: ProbeResult,
        *,
        save_dir: Path,
        connections: int | None = None,
        expected_sha256: str = "",
    ) -> str:
        """按探测结果创建并启动下载任务。"""
        backend = self._require_backend()
        task = backend.create_task(
            probe,
            save_dir=save_dir,
            connections=connections,
            expected_sha256=expected_sha256,
        )
        self._store(task.snapshot(), is_new=True)
        self.emit_stats()
        backend.start_task(task)
        return task.task_id

    def pause(self, task_id: str) -> None:
        self._require_backend().pause(task_id)

    def resume(self, task_id: str) -> None:
        self._require_backend().resume(task_id)

    def cancel(self, task_id: str) -> None:
        self._require_backend().cancel(task_id)

    def retry(self, task_id: str) -> None:
        self._require_backend().retry(task_id)

    def pause_all(self) -> None:
        for task in self.active_tasks():
            self.pause(task.task_id)

    def resume_all(self) -> None:
        for task in self.tasks(statuses=[DownloadStatus.PAUSED]):
            self.resume(task.task_id)

    def delete_task(self, task_id: str, *, delete_files: bool = False) -> None:
        """删除任务记录（可选删除未完成的临时文件）。"""
        task = self._tasks.get(task_id)
        if self._backend is not None:
            # 删除完成后由后端的 task_removed 信号同步界面
            self._backend.delete_task(task_id, delete_files=delete_files)
        else:
            self._repo.delete_task(task_id)
            self.notify_task_removed(task_id)
        if task is not None:
            _log.info("已删除任务记录：%s", task.file_name)

    def clear_finished(self) -> int:
        """清空已完成 / 已取消的任务记录。"""
        for task in self.completed_tasks():
            self.delete_task(task.task_id)
        return len(self.completed_tasks())


def _start_of_today() -> float:
    """今天 00:00 的时间戳。"""
    now = time.localtime()
    midnight = time.mktime(
        (now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1)
    )
    return midnight
