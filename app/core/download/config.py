"""下载引擎的运行时配置与回调。

引擎不依赖 Qt：所有对外通知都通过普通 Python 回调完成，由服务层负责把
回调转换成 Qt 信号（见 ``app/services/download_backend.py``）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.common.constants import DEFAULT_CONNECTIONS, DEFAULT_MAX_CONCURRENT_TASKS
from app.core.download.models import DownloadTask


@dataclass(slots=True)
class EngineOptions:
    """引擎运行参数（由设置页面同步）。"""

    max_concurrent_tasks: int = DEFAULT_MAX_CONCURRENT_TASKS
    connection_mode: int = 0  # 0 = 自动
    default_connections: int = DEFAULT_CONNECTIONS
    auto_retry: bool = True
    auto_verify: bool = True
    adaptive: bool = True

    def connections_for(self, fallback: int) -> int:
        """返回用户配置的连接数（0 表示沿用推荐值）。"""
        return self.connection_mode if self.connection_mode > 0 else max(1, fallback)


@dataclass(slots=True)
class EngineCallbacks:
    """引擎 → 外部的通知回调（在 asyncio 线程中调用）。"""

    on_task_added: Callable[[DownloadTask], None] | None = None
    on_task_updated: Callable[[DownloadTask], None] | None = None
    on_task_removed: Callable[[str], None] | None = None
    on_task_finished: Callable[[DownloadTask], None] | None = None
    on_notice: Callable[[str, str], None] | None = None  # (level, message)
    extras: dict[str, object] = field(default_factory=dict)

    def task_added(self, task: DownloadTask) -> None:
        if self.on_task_added is not None:
            self.on_task_added(task)

    def task_updated(self, task: DownloadTask) -> None:
        if self.on_task_updated is not None:
            self.on_task_updated(task)

    def task_removed(self, task_id: str) -> None:
        if self.on_task_removed is not None:
            self.on_task_removed(task_id)

    def task_finished(self, task: DownloadTask) -> None:
        if self.on_task_finished is not None:
            self.on_task_finished(task)

    def notice(self, level: str, message: str) -> None:
        if self.on_notice is not None:
            self.on_notice(level, message)
