"""应用设置。

* 结构化设置（下载目录、并发数、归档策略等）保存在 SQLite ``settings`` 表；
* 窗口几何、上次页面等纯 UI 状态由 :class:`QSettings` 管理（见 UI 层）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from PySide6.QtCore import QObject, Signal

from app.core.common.constants import (
    DEFAULT_CONNECTIONS,
    DEFAULT_MAX_CONCURRENT_TASKS,
    MAX_CONNECTIONS,
    MIN_CONNECTIONS,
)
from app.core.common.logger import get_logger
from app.core.common.paths import default_download_dir
from app.core.storage.repositories import SettingsRepository

_log = get_logger("services.settings")


class SettingKey(StrEnum):
    """设置键名（同时作为 ``changed`` 信号的标识）。"""

    DOWNLOAD_DIR = "download.dir"
    MAX_CONCURRENT_TASKS = "download.max_concurrent_tasks"
    CONNECTION_MODE = "download.connections"
    AUTO_RESUME = "download.auto_resume"
    AUTO_RETRY = "download.auto_retry"
    AUTO_VERIFY = "download.auto_verify"
    ARCHIVE_MODE = "file.archive_mode"
    ARCHIVE_ROOT = "file.archive_root"
    ORGANIZE_ROOT = "file.organize_root"
    DUPLICATE_ACTION = "file.duplicate_action"
    THEME = "ui.theme"
    NOTIFY_COMPLETED = "ui.notify_completed"
    NOTIFY_ERRORS = "ui.notify_errors"
    NOTIFY_RESTORED = "ui.notify_restored"
    LAST_PAGE = "ui.last_page"


class ArchiveMode(StrEnum):
    """下载完成后的自动归档策略。"""

    OFF = "off"
    CATEGORY = "category"
    RULES = "rules"

    @property
    def label(self) -> str:
        return {
            ArchiveMode.OFF: "关闭",
            ArchiveMode.CATEGORY: "按文件类型自动归档",
            ArchiveMode.RULES: "按自定义规则自动归档",
        }[self]


class DuplicateAction(StrEnum):
    """重复文件默认处理方式。"""

    MOVE_TO_FOLDER = "move"
    ASK = "ask"

    @property
    def label(self) -> str:
        return {
            DuplicateAction.MOVE_TO_FOLDER: "移动到“重复文件”文件夹",
            DuplicateAction.ASK: "每次询问",
        }[self]


class ThemeMode(StrEnum):
    """界面主题。"""

    DARK = "dark"
    LIGHT = "light"
    SYSTEM = "system"

    @property
    def label(self) -> str:
        return {
            ThemeMode.DARK: "深色",
            ThemeMode.LIGHT: "浅色",
            ThemeMode.SYSTEM: "跟随系统",
        }[self]


@dataclass(frozen=True, slots=True)
class SettingSpec:
    """一项设置的类型规格。"""

    default: Any
    kind: type


SETTING_SPECS: Final[dict[SettingKey, SettingSpec]] = {
    SettingKey.DOWNLOAD_DIR: SettingSpec(str(default_download_dir()), str),
    SettingKey.MAX_CONCURRENT_TASKS: SettingSpec(DEFAULT_MAX_CONCURRENT_TASKS, int),
    SettingKey.CONNECTION_MODE: SettingSpec(0, int),  # 0 = 自动
    SettingKey.AUTO_RESUME: SettingSpec(True, bool),
    SettingKey.AUTO_RETRY: SettingSpec(True, bool),
    SettingKey.AUTO_VERIFY: SettingSpec(True, bool),
    SettingKey.ARCHIVE_MODE: SettingSpec(ArchiveMode.OFF.value, str),
    SettingKey.ARCHIVE_ROOT: SettingSpec("", str),
    SettingKey.ORGANIZE_ROOT: SettingSpec("", str),
    SettingKey.DUPLICATE_ACTION: SettingSpec(DuplicateAction.MOVE_TO_FOLDER.value, str),
    SettingKey.THEME: SettingSpec(ThemeMode.DARK.value, str),
    SettingKey.NOTIFY_COMPLETED: SettingSpec(True, bool),
    SettingKey.NOTIFY_ERRORS: SettingSpec(True, bool),
    SettingKey.NOTIFY_RESTORED: SettingSpec(True, bool),
    SettingKey.LAST_PAGE: SettingSpec("home", str),
}


class AppSettings(QObject):
    """类型化设置访问器。

    写入立即落库，并通过 ``changed`` 信号通知界面刷新。
    """

    changed = Signal(str, object)

    def __init__(self, repository: SettingsRepository, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._repo = repository
        self._specs = dict(SETTING_SPECS)
        self._cache: dict[str, str] = {}
        try:
            self._cache.update(repository.get_all())
        except Exception as exc:  # noqa: BLE001 - 设置损坏时回退默认值
            _log.warning("读取设置失败，使用默认值：%s", exc)

    # -- 原始读写 ----------------------------------------------------------
    @staticmethod
    def _serialize(value: Any) -> str:
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, Path):
            return str(value)
        return str(value)

    def _raw(self, key: SettingKey) -> str:
        """返回设置的字符串形式。"""
        if key.value in self._cache:
            return self._cache[key.value]
        return self._serialize(self._specs[key].default)

    def _deserialize(self, key: SettingKey, text: str) -> Any:
        spec = self._specs[key]
        if spec.kind is bool:
            return text == "1"
        if spec.kind is int:
            try:
                return int(text)
            except ValueError:
                return int(spec.default)
        return text

    def value(self, key: SettingKey) -> Any:
        """按类型读取设置值。"""
        return self._deserialize(key, self._raw(key))

    def set_value(self, key: SettingKey, value: Any, *, notify: bool = True) -> None:
        """写入设置（自动转换为字符串）。"""
        text = self._serialize(value)
        if self._cache.get(key.value) == text:
            return
        self._cache[key.value] = text
        try:
            self._repo.set(key.value, text)
        except Exception as exc:  # noqa: BLE001 - 落库失败不应阻塞界面
            _log.error("保存设置 %s 失败：%s", key.value, exc)
        if notify:
            self.changed.emit(key.value, self._deserialize(key, text))

    def reset_to_defaults(self) -> None:
        """恢复默认设置。"""
        for key, spec in self._specs.items():
            self.set_value(key, spec.default)

    # -- 下载 --------------------------------------------------------------
    @property
    def download_dir(self) -> Path:
        value = self._raw(SettingKey.DOWNLOAD_DIR)
        return Path(value) if value else default_download_dir()

    @download_dir.setter
    def download_dir(self, value: Path | str) -> None:
        self.set_value(SettingKey.DOWNLOAD_DIR, Path(value))

    @property
    def max_concurrent_tasks(self) -> int:
        return max(1, min(10, int(self.value(SettingKey.MAX_CONCURRENT_TASKS))))

    @max_concurrent_tasks.setter
    def max_concurrent_tasks(self, value: int) -> None:
        self.set_value(SettingKey.MAX_CONCURRENT_TASKS, max(1, min(10, int(value))))

    @property
    def connection_mode(self) -> int:
        """0 表示自动；否则为 1–16 的固定连接数。"""
        raw = int(self.value(SettingKey.CONNECTION_MODE))
        if raw <= 0:
            return 0
        return max(MIN_CONNECTIONS, min(MAX_CONNECTIONS, raw))

    @connection_mode.setter
    def connection_mode(self, value: int) -> None:
        normalized = (
            0 if int(value) <= 0 else max(MIN_CONNECTIONS, min(MAX_CONNECTIONS, int(value)))
        )
        self.set_value(SettingKey.CONNECTION_MODE, normalized)

    @property
    def default_connections(self) -> int:
        """自动模式下的默认连接数。"""
        return self.connection_mode or DEFAULT_CONNECTIONS

    @property
    def auto_resume(self) -> bool:
        return bool(self.value(SettingKey.AUTO_RESUME))

    @auto_resume.setter
    def auto_resume(self, value: bool) -> None:
        self.set_value(SettingKey.AUTO_RESUME, bool(value))

    @property
    def auto_retry(self) -> bool:
        return bool(self.value(SettingKey.AUTO_RETRY))

    @auto_retry.setter
    def auto_retry(self, value: bool) -> None:
        self.set_value(SettingKey.AUTO_RETRY, bool(value))

    @property
    def auto_verify(self) -> bool:
        return bool(self.value(SettingKey.AUTO_VERIFY))

    @auto_verify.setter
    def auto_verify(self, value: bool) -> None:
        self.set_value(SettingKey.AUTO_VERIFY, bool(value))

    # -- 文件 --------------------------------------------------------------
    @property
    def archive_mode(self) -> ArchiveMode:
        try:
            return ArchiveMode(self._raw(SettingKey.ARCHIVE_MODE))
        except ValueError:
            return ArchiveMode.OFF

    @archive_mode.setter
    def archive_mode(self, value: ArchiveMode | str) -> None:
        self.set_value(SettingKey.ARCHIVE_MODE, ArchiveMode(value).value)

    @property
    def archive_root(self) -> Path:
        value = self._raw(SettingKey.ARCHIVE_ROOT)
        return Path(value) if value else self.download_dir

    @archive_root.setter
    def archive_root(self, value: Path | str) -> None:
        self.set_value(SettingKey.ARCHIVE_ROOT, Path(value))

    @property
    def organize_root(self) -> Path | None:
        value = self._raw(SettingKey.ORGANIZE_ROOT)
        return Path(value) if value else None

    @organize_root.setter
    def organize_root(self, value: Path | str | None) -> None:
        self.set_value(SettingKey.ORGANIZE_ROOT, Path(value) if value else "")

    @property
    def duplicate_action(self) -> DuplicateAction:
        try:
            return DuplicateAction(self._raw(SettingKey.DUPLICATE_ACTION))
        except ValueError:
            return DuplicateAction.MOVE_TO_FOLDER

    @duplicate_action.setter
    def duplicate_action(self, value: DuplicateAction | str) -> None:
        self.set_value(SettingKey.DUPLICATE_ACTION, DuplicateAction(value).value)

    # -- 界面 --------------------------------------------------------------
    @property
    def theme(self) -> ThemeMode:
        try:
            return ThemeMode(self._raw(SettingKey.THEME))
        except ValueError:
            return ThemeMode.DARK

    @theme.setter
    def theme(self, value: ThemeMode | str) -> None:
        self.set_value(SettingKey.THEME, ThemeMode(value).value)

    @property
    def notify_completed(self) -> bool:
        return bool(self.value(SettingKey.NOTIFY_COMPLETED))

    @notify_completed.setter
    def notify_completed(self, value: bool) -> None:
        self.set_value(SettingKey.NOTIFY_COMPLETED, bool(value))

    @property
    def notify_errors(self) -> bool:
        return bool(self.value(SettingKey.NOTIFY_ERRORS))

    @notify_errors.setter
    def notify_errors(self, value: bool) -> None:
        self.set_value(SettingKey.NOTIFY_ERRORS, bool(value))

    @property
    def notify_restored(self) -> bool:
        return bool(self.value(SettingKey.NOTIFY_RESTORED))

    @notify_restored.setter
    def notify_restored(self, value: bool) -> None:
        self.set_value(SettingKey.NOTIFY_RESTORED, bool(value))

    @property
    def last_page(self) -> str:
        return self._raw(SettingKey.LAST_PAGE)

    @last_page.setter
    def last_page(self, value: str) -> None:
        self.set_value(SettingKey.LAST_PAGE, value, notify=False)
