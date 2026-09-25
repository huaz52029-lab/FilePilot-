"""运行时目录解析。

要求：

* 数据库、配置、日志写入用户目录，**绝不写入安装目录**；
* 支持 ``FILEPILOT_HOME`` 环境变量覆盖（便携模式与测试隔离）；
* 兼容 PyInstaller 打包后的资源路径。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.core.common.constants import (
    APP_NAME,
    CACHE_DIRNAME,
    DATA_DIR_ENV,
    DATABASE_FILENAME,
    LOGS_DIRNAME,
)


def _default_root() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def data_root() -> Path:
    """用户数据根目录。"""
    return _default_root()


def database_path() -> Path:
    """SQLite 数据库文件路径。"""
    return data_root() / DATABASE_FILENAME


def logs_dir() -> Path:
    """日志目录。"""
    return data_root() / LOGS_DIRNAME


def cache_dir() -> Path:
    """缓存目录（扫描缓存、缩略图等）。"""
    return data_root() / CACHE_DIRNAME


def default_download_dir() -> Path:
    """默认下载目录：``%USERPROFILE%\\Downloads``，不存在时退回用户主目录。"""
    candidate = Path.home() / "Downloads"
    if candidate.parent.exists():
        return candidate
    return Path.home()


def ensure_runtime_dirs() -> None:
    """确保运行时目录存在。"""
    for directory in (data_root(), logs_dir(), cache_dir()):
        directory.mkdir(parents=True, exist_ok=True)


def resource_root() -> Path:
    """资源根目录（兼容 PyInstaller 单文件模式）。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "app" / "resources"
    return Path(__file__).resolve().parents[2] / "resources"


def resource_path(relative: str | Path) -> Path:
    """解析 ``resources`` 下的相对路径。"""
    return resource_root() / str(relative).replace("\\", "/")


def stylesheet_path(name: str) -> Path:
    """样式表文件路径，例如 ``dark`` / ``light``。"""
    return resource_path(Path("styles") / f"{name}.qss")


def icon_path(name: str) -> Path:
    """图标文件路径（SVG）。"""
    return resource_path(Path("icons") / f"{name}.svg")


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包环境中。"""
    return bool(getattr(sys, "frozen", False))
