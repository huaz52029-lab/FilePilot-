"""日志系统。

* 日志文件：``<用户数据目录>/logs/YYYY-MM-DD.log``；
* 控制台输出仅在开发模式下启用；
* 完整 traceback 写入日志，UI 只展示自然语言提示。
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from types import TracebackType

from app.core.common.constants import APP_NAME, APP_VERSION, LOGS_RETENTION_DAYS
from app.core.common.paths import logs_dir

_LOGGER_NAME = "filepilot"
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_configured = False


class DailyFileHandler(logging.Handler):
    """按日期命名的文件处理器：每天一个 ``YYYY-MM-DD.log``。"""

    terminator = "\n"

    def __init__(self, directory: Path, *, encoding: str = "utf-8") -> None:
        super().__init__()
        self._directory = Path(directory)
        self._encoding = encoding
        self._lock = threading.Lock()
        self._stream = None
        self._current_date: str | None = None

    def _open_stream(self, date_key: str) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        self._stream = (self._directory / f"{date_key}.log").open("a", encoding=self._encoding)
        self._current_date = date_key

    def emit(self, record: logging.LogRecord) -> None:
        try:
            date_key = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d")
            message = self.format(record)
            with self._lock:
                if self._stream is None or date_key != self._current_date:
                    self._close_stream()
                    self._open_stream(date_key)
                assert self._stream is not None
                self._stream.write(message + self.terminator)
                self._stream.flush()
        except Exception:  # noqa: BLE001 - 日志失败不能影响主流程
            self.handleError(record)

    def _close_stream(self) -> None:
        if self._stream is not None:
            with contextlib.suppress(OSError):  # pragma: no cover - 关闭失败无需处理
                self._stream.close()
            self._stream = None

    def close(self) -> None:
        with self._lock:
            self._close_stream()
        super().close()

    @property
    def current_file(self) -> Path | None:
        if self._current_date is None:
            return None
        return self._directory / f"{self._current_date}.log"


def _prune_old_logs(directory: Path, keep_days: int) -> None:
    if keep_days <= 0 or not directory.exists():
        return
    files = sorted(directory.glob("*.log"), key=lambda item: item.name, reverse=True)
    for stale in files[keep_days:]:
        try:
            stale.unlink()
        except OSError:  # pragma: no cover - 文件被占用时忽略
            continue


def setup_logging(
    *,
    level: int = logging.INFO,
    directory: Path | None = None,
    console: bool | None = None,
    retention_days: int = LOGS_RETENTION_DAYS,
) -> logging.Logger:
    """初始化日志系统，可重复调用（幂等）。"""
    global _configured

    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    log_directory = Path(directory) if directory else logs_dir()
    log_directory.mkdir(parents=True, exist_ok=True)

    logger.setLevel(level)
    logger.propagate = False
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    file_handler = DailyFileHandler(log_directory)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    show_console = console if console is not None else sys.stderr is not None
    if show_console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    _prune_old_logs(log_directory, retention_days)
    _install_excepthook(logger)
    logger.info("=" * 68)
    logger.info("%s %s 启动，日志目录：%s", APP_NAME, APP_VERSION, log_directory)
    _configured = True
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """获取带命名空间的子日志器。"""
    if not name or name == _LOGGER_NAME:
        return logging.getLogger(_LOGGER_NAME)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")


def _install_excepthook(logger: logging.Logger) -> None:
    def _hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical("未捕获的异常", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _hook
