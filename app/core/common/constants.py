"""全局常量。

该模块不导入任何 GUI 依赖，保证核心层可以脱离 PySide6 单独运行与测试。
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# 应用元信息
# ---------------------------------------------------------------------------
APP_NAME: Final[str] = "FilePilot"
APP_TAGLINE: Final[str] = "现代 Windows 文件管理与智能下载工具"
APP_VERSION: Final[str] = "0.1.1"
APP_AUTHOR: Final[str] = "FilePilot Contributors"
APP_LICENSE: Final[str] = "MIT"
#: 项目唯一官方地址（代码、About / 设置页面、打包元数据、User-Agent 均引用此处）
APP_HOMEPAGE: Final[str] = "https://github.com/huaz52029-lab/FilePilot-"
APP_REPOSITORY: Final[str] = f"{APP_HOMEPAGE}.git"
APP_ISSUES: Final[str] = f"{APP_HOMEPAGE}/issues"

SETTINGS_ORG: Final[str] = "FilePilot"
SETTINGS_APP: Final[str] = "FilePilot"

# ---------------------------------------------------------------------------
# 运行时目录
# ---------------------------------------------------------------------------
#: 覆盖数据目录的环境变量（便携模式 / 测试隔离使用）。
DATA_DIR_ENV: Final[str] = "FILEPILOT_HOME"
DATABASE_FILENAME: Final[str] = "filepilot.db"
LOGS_DIRNAME: Final[str] = "logs"
CACHE_DIRNAME: Final[str] = "cache"

# ---------------------------------------------------------------------------
# 界面基础尺寸
# ---------------------------------------------------------------------------
WINDOW_DEFAULT_WIDTH: Final[int] = 1280
WINDOW_DEFAULT_HEIGHT: Final[int] = 800
WINDOW_MIN_WIDTH: Final[int] = 1080
WINDOW_MIN_HEIGHT: Final[int] = 680
SIDEBAR_WIDTH: Final[int] = 220
TITLE_BAR_HEIGHT: Final[int] = 44
PAGE_MARGIN: Final[int] = 28
CONTENT_MAX_WIDTH: Final[int] = 1180

# ---------------------------------------------------------------------------
# 下载引擎
# ---------------------------------------------------------------------------
DEFAULT_MAX_CONCURRENT_TASKS: Final[int] = 3
DEFAULT_CONNECTIONS: Final[int] = 8
MIN_CONNECTIONS: Final[int] = 1
MAX_CONNECTIONS: Final[int] = 16

#: 连接数自动推荐表：(文件大小上限, 推荐连接数)。超过最后一项时使用 MAX_CONNECTIONS 前的上限。
CONNECTION_PLAN: Final[tuple[tuple[int, int], ...]] = (
    (50 * 1024 * 1024, 1),
    (500 * 1024 * 1024, 4),
    (2 * 1024 * 1024 * 1024, 8),
)
LARGE_FILE_CONNECTIONS: Final[int] = 8

READ_CHUNK_SIZE: Final[int] = 256 * 1024
HASH_CHUNK_SIZE: Final[int] = 1024 * 1024
PROGRESS_EMIT_INTERVAL: Final[float] = 0.25
SPEED_WINDOW_SECONDS: Final[float] = 5.0

MAX_RETRIES: Final[int] = 5
BACKOFF_BASE_SECONDS: Final[float] = 1.0
BACKOFF_FACTOR: Final[float] = 2.0
BACKOFF_MAX_SECONDS: Final[float] = 30.0
BACKOFF_JITTER: Final[float] = 0.2

RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({408, 425, 429, 500, 502, 503, 504})
FATAL_STATUS: Final[frozenset[int]] = frozenset({400, 401, 403, 404, 405, 410, 451})

CONNECT_TIMEOUT: Final[float] = 15.0
READ_TIMEOUT: Final[float] = 30.0
PROBE_TIMEOUT: Final[float] = 20.0
TOTAL_TIMEOUT: Final[float] = 0.0  # 0 表示不限制整体时长（大文件必须允许长时间下载）

USER_AGENT: Final[str] = (
    f"FilePilot/{APP_VERSION} (Windows; +{APP_HOMEPAGE})"
)

#: 断点续传临时目录后缀：file.zip.fp.part/
PART_DIR_SUFFIX: Final[str] = ".fp.part"
META_FILENAME: Final[str] = "meta.json"
SEGMENT_FILENAME_TEMPLATE: Final[str] = "segment_{index:04d}.part"

#: 自适应并发调节参数
ADAPTIVE_MIN_SAMPLES: Final[int] = 4
ADAPTIVE_UPGRADE_GAIN: Final[float] = 1.25
ADAPTIVE_DOWNGRADE_DROP: Final[float] = 0.70
ADAPTIVE_ERROR_RATE_LIMIT: Final[float] = 0.35

# ---------------------------------------------------------------------------
# 文件管理
# ---------------------------------------------------------------------------
#: 0 表示不限制；仅在需要时设置。
SCAN_MAX_DEPTH: Final[int] = 32
SCAN_IGNORED_DIRS: Final[frozenset[str]] = frozenset(
    {
        "$RECYCLE.BIN",
        "System Volume Information",
        ".git",
        ".svn",
        "__pycache__",
        ".venv",
        "node_modules",
    }
)
DUPLICATE_MIN_FILE_SIZE: Final[int] = 1
DUPLICATE_TARGET_DIRNAME: Final[str] = "重复文件"

DISK_WARNING_PERCENT: Final[float] = 90.0
DISK_CRITICAL_PERCENT: Final[float] = 97.0

LOGS_RETENTION_DAYS: Final[int] = 14
