"""通用工具函数：格式化、路径与 URL 处理。"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_SIZE_UNITS = {
    "b": 1,
    "kb": 1024,
    "k": 1024,
    "mb": 1024**2,
    "m": 1024**2,
    "gb": 1024**3,
    "g": 1024**3,
    "tb": 1024**4,
    "t": 1024**4,
}
_SIZE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*$")


# ---------------------------------------------------------------------------
# 数值格式化
# ---------------------------------------------------------------------------
def format_bytes(num: float | int | None, precision: int = 2) -> str:
    """把字节数格式化为人类可读文本，例如 ``4.72 GB``。"""
    if num is None:
        return "—"
    value = float(num)
    if value < 0:
        return "—"
    if value < 1024:
        return f"{int(value)} B"
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        value /= 1024.0
        if value < 1024 or unit == "PB":
            break
    digits = 0 if value >= 100 else precision
    return f"{value:.{digits}f} {unit}"


def format_speed(bytes_per_second: float | None) -> str:
    """下载速度，例如 ``11.8 MB/s``。"""
    if not bytes_per_second or bytes_per_second <= 0:
        return "0 B/s"
    return f"{format_bytes(bytes_per_second, 1)}/s"


def format_duration(seconds: float | None) -> str:
    """时长格式化：``1:52``、``1:02:03``。"""
    if seconds is None or seconds < 0:
        return "—"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_count(value: int | None) -> str:
    """千分位整数，例如 ``1,284``。"""
    if value is None:
        return "0"
    return f"{int(value):,}"


def format_datetime(value: datetime | float | None, *, with_seconds: bool = False) -> str:
    """本地时间格式化：``2026-09-25 22:30``。"""
    if value is None:
        return "—"
    if isinstance(value, int | float):
        moment = datetime.fromtimestamp(float(value)).astimezone()
    elif isinstance(value, datetime):
        moment = value.astimezone() if value.tzinfo else value
    else:  # pragma: no cover - 防御式分支
        return "—"
    fmt = "%Y-%m-%d %H:%M:%S" if with_seconds else "%Y-%m-%d %H:%M"
    return moment.strftime(fmt)


_ONE_DAY = timedelta(days=1)


def format_friendly_time(value: datetime | float | None) -> str:
    """相对时间：``刚刚`` / ``3 分钟前`` / ``昨天 22:30``。"""
    if value is None:
        return "—"
    if isinstance(value, int | float):
        moment = datetime.fromtimestamp(float(value)).astimezone()
    else:
        moment = value.astimezone() if value.tzinfo else value
    now = datetime.now().astimezone()
    delta = now - moment
    seconds = delta.total_seconds()
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86400 and moment.date() == now.date():
        return f"今天 {moment:%H:%M}"
    if moment.date() == (now.date() - _ONE_DAY):
        return f"昨天 {moment:%H:%M}"
    if seconds < 7 * 86400:
        return f"{int(seconds // 86400)} 天前"
    return moment.strftime("%Y-%m-%d")


def parse_size_text(text: str) -> int | None:
    """解析 ``500MB`` / ``1.5 GB`` / ``1024`` 为字节数。"""
    match = _SIZE_PATTERN.match(text or "")
    if not match:
        return None
    number, unit = match.groups()
    multiplier = _SIZE_UNITS.get(unit.lower(), 1)
    try:
        return int(float(number) * multiplier)
    except ValueError:  # pragma: no cover - 正则已保证可解析
        return None


def clamp(value: float, low: float, high: float) -> float:
    """把数值限制在 ``[low, high]`` 区间内。"""
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# 文件与路径
# ---------------------------------------------------------------------------
def sanitize_filename(name: str, *, replacement: str = "_", max_length: int = 180) -> str:
    """清理非法文件名字符，兼容 Windows 保留名。"""
    cleaned = _INVALID_FILENAME_CHARS.sub(replacement, (name or "").strip())
    cleaned = cleaned.strip(" .")
    if not cleaned:
        return "unnamed"
    stem, dot, suffix = cleaned.rpartition(".")
    if dot and stem.upper() in _RESERVED_WINDOWS_NAMES:
        cleaned = f"{stem}_{dot}{suffix}"
    elif not dot and cleaned.upper() in _RESERVED_WINDOWS_NAMES:
        cleaned = f"{cleaned}_"
    if len(cleaned) > max_length:
        keep = max_length - (len(suffix) + 1) if dot else max_length
        keep = max(keep, 1)
        cleaned = f"{cleaned[:keep]}.{suffix}" if dot else cleaned[:max_length]
    return cleaned


def unique_path(path: Path, *, max_attempts: int = 9999) -> Path:
    """若目标已存在则返回 ``name (1).ext`` 形式的可用路径。

    永不覆盖已有文件。
    """
    if not path.exists():
        return path
    directory = path.parent
    suffix = "".join(path.suffixes[-1:]) or ""
    stem = path.name[: len(path.name) - len(suffix)] if suffix else path.name
    for index in range(1, max_attempts + 1):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"无法为 {path} 生成唯一文件名")


def filename_from_url(url: str, *, fallback: str = "download.bin") -> str:
    """从 URL 推断文件名（仅基于路径，Content-Disposition 由探测层处理）。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return fallback
    if parsed.path.endswith("/"):
        return fallback
    candidate = unquote(Path(parsed.path).name)
    candidate = sanitize_filename(candidate)
    if not candidate or candidate in {".", ".."}:
        return fallback
    return candidate


def suffix_of(path: Path | str) -> str:
    """小写扩展名（不含点），无扩展名返回空串。"""
    return Path(path).suffix.lower().lstrip(".")


def is_http_url(text: str) -> bool:
    """判断文本是否为合法的 http(s) URL。"""
    if not text or not isinstance(text, str):
        return False
    candidate = text.strip()
    if any(ch.isspace() for ch in candidate):
        return False
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def normalize_url(text: str) -> str | None:
    """把用户输入规范化为 http(s) URL；非法输入返回 ``None``。"""
    candidate = (text or "").strip().strip('"').strip("'")
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    return candidate if is_http_url(candidate) else None


def open_in_explorer(path: Path | str, *, select: bool = False) -> None:
    """在资源管理器中打开目录或选中文件。"""
    target = Path(path)
    if os.name != "nt":  # pragma: no cover - 项目仅面向 Windows
        return
    if select and target.exists():
        os.startfile(str(target.parent), "open", "/select,", str(target))  # type: ignore[attr-defined]
    else:
        directory = target if target.is_dir() else target.parent
        os.startfile(str(directory))  # type: ignore[attr-defined]


def open_path(path: Path | str) -> None:
    """用系统默认程序打开文件或目录。"""
    target = Path(path)
    os.startfile(str(target))  # type: ignore[attr-defined]


def to_timestamp(value: datetime | None = None) -> float:
    """返回 Unix 时间戳（秒）。"""
    moment = value or datetime.now(UTC)
    return moment.timestamp()


def truncate_text(text: str, limit: int, *, ellipsis: str = "…") -> str:
    """按显示宽度截断文本（中文按 2 宽度计算）。"""
    if limit <= 0:
        return ""
    width = 0
    result: list[str] = []
    for char in text:
        char_width = 2 if ord(char) > 0x2E80 else 1
        if width + char_width > limit:
            return "".join(result) + ellipsis
        width += char_width
        result.append(char)
    return "".join(result)
