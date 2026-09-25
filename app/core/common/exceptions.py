"""统一异常类型定义。

设计目标：

* 核心层抛出语义明确的异常；
* 每个异常都可以给出**中文自然语言**提示（``user_message``），UI 直接展示；
* 技术细节保留在 ``detail`` / ``__str__`` 中，只写入日志，不直接展示给用户。
"""

from __future__ import annotations

import socket
from typing import Any


class FilePilotError(Exception):
    """所有 FilePilot 异常的基类。"""

    default_message = "发生了未知错误，请查看日志了解详情。"

    def __init__(self, message: str | None = None, *, detail: str | None = None) -> None:
        self.message = message or self.default_message
        self.detail = detail
        super().__init__(self.message if detail is None else f"{self.message} ({detail})")

    @property
    def user_message(self) -> str:
        """返回可直接展示给用户的中文提示。"""
        return self.message


# ---------------------------------------------------------------------------
# 下载相关
# ---------------------------------------------------------------------------
class DownloadError(FilePilotError):
    """下载相关错误的基类。"""

    default_message = "下载过程中发生错误。"


class DownloadProbeError(DownloadError):
    """URL 探测（HEAD / Range 请求）失败。"""

    default_message = "无法分析该下载链接，请确认链接是否正确。"


class DownloadNetworkError(DownloadError):
    """网络层错误，携带状态码与可重试标记。"""

    default_message = "网络连接异常。"

    def __init__(
        self,
        message: str | None = None,
        *,
        status: int | None = None,
        retryable: bool = True,
        detail: str | None = None,
    ) -> None:
        super().__init__(message, detail=detail)
        self.status = status
        self.retryable = retryable


class DownloadRangeError(DownloadError):
    """服务器 Range 行为不符合预期（不支持 / 返回区间错误）。"""

    default_message = "服务器不支持或未正确响应分段下载。"


class DownloadResumeError(DownloadError):
    """断点续传校验失败（ETag / 大小 / 文件被修改）。"""

    default_message = "该任务无法继续断点续传，需要重新下载。"


class DownloadFileError(DownloadError):
    """本地文件写入失败（权限、路径非法等）。"""

    default_message = "无法写入目标文件，请检查保存位置权限。"


class InsufficientDiskSpaceError(DownloadError):
    """目标磁盘空间不足。"""

    default_message = "目标磁盘空间不足。"

    def __init__(self, required: int, available: int, *, path: str | None = None) -> None:
        from app.core.common.helpers import format_bytes

        location = f"（{path}）" if path else ""
        message = (
            f"磁盘空间不足{location}：需要 {format_bytes(required)}，"
            f"可用 {format_bytes(available)}。请更换保存位置。"
        )
        super().__init__(message, detail=f"required={required} available={available}")
        self.required = required
        self.available = available


# ---------------------------------------------------------------------------
# 文件与数据相关
# ---------------------------------------------------------------------------
class HashMismatchError(FilePilotError):
    """SHA-256 校验不通过。"""

    default_message = "文件校验失败，SHA-256 与预期值不一致。"

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(
            f"文件校验失败：预期 {expected[:16]}…，实际 {actual[:16]}…",
            detail=f"expected={expected} actual={actual}",
        )
        self.expected = expected
        self.actual = actual


class FileOperationError(FilePilotError):
    """通用文件操作错误（复制、移动、重命名、删除等）。"""

    default_message = "文件操作失败。"


class FileScanError(FilePilotError):
    """文件扫描过程中的错误。"""

    default_message = "扫描文件时发生错误。"


class DatabaseError(FilePilotError):
    """数据库读写错误。"""

    default_message = "本地数据库操作失败。"


class ConfigurationError(FilePilotError):
    """配置读写错误。"""

    default_message = "配置读取失败，已恢复默认设置。"


class TaskNotImplementedError(FilePilotError):
    """功能尚未在当前开发阶段实现（用于分阶段交付时给出清晰提示）。"""

    def __init__(self, feature: str, *, phase: str | None = None) -> None:
        suffix = f"（计划于 {phase} 阶段提供）" if phase else ""
        super().__init__(f"“{feature}”功能尚未接入{suffix}。")
        self.feature = feature
        self.phase = phase


# ---------------------------------------------------------------------------
# 错误 → 用户可读文案
# ---------------------------------------------------------------------------
_ERRNO_MESSAGES: dict[int, str] = {
    10013: "网络访问被系统或防火墙拦截。",
    10048: "网络端口被占用。",
    10050: "网络不可达，请检查网络连接。",
    10051: "网络不可达，请检查网络连接。",
    10052: "网络连接被中断。",
    10053: "当前连接被本机中断，可能是网络切换或代理异常。",
    10054: "网络连接被远程主机中断，正在尝试重新连接。",
    10060: "连接超时，请检查网络或代理设置。",
    10061: "目标服务器拒绝连接。",
    11001: "无法解析域名，请检查网络或 DNS 设置。",
}


def describe_network_error(exc: BaseException) -> str:
    """把底层网络异常转换成中文自然语言。"""
    if isinstance(exc, FilePilotError):
        return exc.user_message
    if isinstance(exc, socket.gaierror):
        return "无法解析域名，请检查网络或 DNS 设置。"
    if isinstance(exc, TimeoutError | socket.timeout):
        return "网络响应超时，请稍后重试。"
    if isinstance(exc, ConnectionResetError):
        return "网络连接被远程主机中断，正在尝试重新连接。"
    if isinstance(exc, ConnectionRefusedError):
        return "目标服务器拒绝连接。"
    if isinstance(exc, PermissionError):
        return "没有权限访问该位置，请更换目录或以管理员身份运行。"
    if isinstance(exc, FileNotFoundError):
        return "目标路径不存在。"
    if isinstance(exc, OSError):
        errno_value: Any = getattr(exc, "errno", None) or getattr(exc, "winerror", None)
        if isinstance(errno_value, int) and errno_value in _ERRNO_MESSAGES:
            return _ERRNO_MESSAGES[errno_value]
        return "操作系统报告了一个错误，请查看日志了解详情。"
    return "发生了未知错误，请查看日志了解详情。"


def to_user_message(exc: BaseException | str) -> str:
    """任意异常 → 用户可读文案。"""
    if isinstance(exc, str):
        return exc
    if isinstance(exc, FilePilotError):
        return exc.user_message
    return describe_network_error(exc)


def describe_os_error(exc: BaseException) -> str:
    """文件系统错误的用户可读文案（扫描 / 整理 / 复制等场景）。"""
    if isinstance(exc, FilePilotError):
        return exc.user_message
    if isinstance(exc, PermissionError):
        return "没有权限访问该位置，请检查文件属性或改用其他目录。"
    if isinstance(exc, FileNotFoundError):
        return "目标路径不存在或已被移动。"
    if isinstance(exc, FileExistsError):
        return "目标文件已存在。"
    if isinstance(exc, IsADirectoryError):
        return "目标是一个目录，无法作为文件处理。"
    if isinstance(exc, OSError):
        errno_value: Any = getattr(exc, "errno", None) or getattr(exc, "winerror", None)
        if isinstance(errno_value, int) and errno_value in _ERRNO_MESSAGES:
            return _ERRNO_MESSAGES[errno_value]
        return "操作系统报告了一个错误，请查看日志了解详情。"
    return to_user_message(exc)
