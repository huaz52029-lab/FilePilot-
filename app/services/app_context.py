"""应用上下文（组合根）。

集中创建并持有：日志、数据库、仓储、设置、线程池与下载服务。
UI 通过 ``AppContext`` 获取依赖，而不是自行 new 出全局单例。
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject

from app.core.common.constants import DATA_DIR_ENV
from app.core.common.logger import get_logger, setup_logging
from app.core.common.paths import database_path, ensure_runtime_dirs
from app.core.download.models import DownloadTask
from app.core.files.organizer import archive_download
from app.core.storage.database import Database
from app.core.storage.models import HistoryKind
from app.core.storage.repositories import (
    DownloadRepository,
    HistoryRepository,
    RuleRepository,
    SettingsRepository,
)
from app.services.async_runner import AsyncRunner
from app.services.download_backend import DownloadBackend
from app.services.download_service import DownloadService
from app.services.library_service import LibraryService
from app.services.settings_service import AppSettings
from app.services.workers import TaskRunner

_log = get_logger("services.context")


class AppContext(QObject):
    """应用级依赖容器。"""

    def __init__(
        self,
        *,
        data_dir: Path | None = None,
        console_log: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if data_dir is not None:
            os.environ[DATA_DIR_ENV] = str(data_dir)
        ensure_runtime_dirs()
        self.logger = setup_logging(console=console_log)

        self.database = Database(database_path())
        self.database.initialize()

        self.settings_repo = SettingsRepository(self.database)
        self.download_repo = DownloadRepository(self.database)
        self.history_repo = HistoryRepository(self.database)
        self.rule_repo = RuleRepository(self.database)

        self.settings = AppSettings(self.settings_repo, self)
        self.runner = TaskRunner(self)
        self.downloads = DownloadService(
            self.download_repo, self.history_repo, self.settings, self
        )
        self.async_runner = AsyncRunner(self)
        self.async_runner.start()
        self.download_backend = DownloadBackend(
            runner=self.async_runner,
            repository=self.download_repo,
            settings=self.settings,
            parent=self,
        )
        self.downloads.attach_backend(self.download_backend)
        self.library = LibraryService(
            runner=self.runner,
            history=self.history_repo,
            rules=self.rule_repo,
            settings=self.settings,
            parent=self,
        )
        self.downloads.set_archiver(self._archive_completed_download)
        self.settings.changed.connect(self._on_setting_changed)
        _log.info("应用上下文初始化完成（数据目录：%s）", self.database.path.parent)

    # ------------------------------------------------------------------
    # 下载完成自动归档
    # ------------------------------------------------------------------
    def _archive_completed_download(self, task: DownloadTask) -> None:
        """在后台线程按设置归档刚下载完成的文件。"""
        from app.services.settings_service import ArchiveMode

        mode = self.settings.archive_mode
        if mode is ArchiveMode.OFF or not task.save_path:
            return
        root = self.settings.archive_root
        rules = self.rule_repo.list_rules(enabled_only=True)
        self.runner.submit(
            archive_download,
            task.save_path,
            mode=mode.value,
            archive_root=root,
            rules=rules,
            on_result=lambda path: self._notify_archived(task, path),
            on_error=lambda message, detail: _log.warning("自动归档失败：%s（%s）", message, detail),
        )

    def _notify_archived(self, task: DownloadTask, path: Path | None) -> None:
        if path is None:
            return
        self.downloads.notify_info(f"{task.file_name} 已归档到 {path.parent}")
        try:
            self.history_repo.add(
                kind=HistoryKind.FILE,
                action="archived",
                title=task.file_name,
                detail=f"自动归档到 {path.parent}",
                path=str(path),
                size=task.total_size,
            )
        except Exception as exc:  # noqa: BLE001 - 历史写入失败可忽略
            _log.warning("写入归档历史失败：%s", exc)

    def _on_setting_changed(self, key: str, _value: object) -> None:
        """设置变更时同步给下载引擎。"""
        try:
            self.download_backend.on_settings_changed(key)
        except Exception as exc:  # noqa: BLE001 - 设置同步失败不应影响界面
            _log.warning("同步设置到下载引擎失败：%s", exc)

    def start_downloads(self) -> None:
        """启动后台下载引擎（界面构建完成后调用）。"""
        self.downloads.start_backend()

    # -- 常用路径 ----------------------------------------------------------
    @property
    def database_path(self) -> Path:
        return self.database.path

    def shutdown(self) -> None:
        """释放资源（退出前调用）。"""
        _log.info("正在关闭应用上下文…")
        try:
            self.download_backend.stop()
        except Exception as exc:  # noqa: BLE001 - 退出流程不应抛出异常
            _log.warning("停止下载引擎失败：%s", exc)
        try:
            self.async_runner.stop()
        except Exception as exc:  # noqa: BLE001
            _log.warning("停止事件循环失败：%s", exc)
        try:
            self.runner.cancel_all()
            self.runner.wait_all(3000)
        except Exception as exc:  # noqa: BLE001 - 退出流程不应抛出异常
            _log.warning("等待后台任务结束超时：%s", exc)
        try:
            self.database.dispose()
        except Exception as exc:  # noqa: BLE001
            _log.warning("关闭数据库失败：%s", exc)
