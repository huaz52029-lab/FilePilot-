"""文件管理领域模型（纯数据结构，不依赖 GUI）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final

from app.core.files.categories import FileCategory, category_label


class OrganizeActionStatus(StrEnum):
    """整理动作的执行状态。"""

    READY = "ready"
    CONFLICT = "conflict"
    SKIPPED = "skipped"
    DONE = "done"
    FAILED = "failed"


ORGANIZE_STATUS_LABELS: Final[dict[OrganizeActionStatus, str]] = {
    OrganizeActionStatus.READY: "待移动",
    OrganizeActionStatus.CONFLICT: "目标已存在",
    OrganizeActionStatus.SKIPPED: "已跳过",
    OrganizeActionStatus.DONE: "已完成",
    OrganizeActionStatus.FAILED: "失败",
}


class MatchField(StrEnum):
    """整理规则的匹配字段。"""

    EXTENSION = "extension"
    NAME_CONTAINS = "name_contains"
    NAME_PREFIX = "name_prefix"
    NAME_SUFFIX = "name_suffix"
    SIZE_GREATER = "size_greater"
    CATEGORY = "category"


MATCH_FIELD_LABELS: Final[dict[MatchField, str]] = {
    MatchField.EXTENSION: "扩展名等于",
    MatchField.NAME_CONTAINS: "文件名包含",
    MatchField.NAME_PREFIX: "文件名以…开头",
    MatchField.NAME_SUFFIX: "文件名以…结尾",
    MatchField.SIZE_GREATER: "文件大小超过",
    MatchField.CATEGORY: "文件类型属于",
}


@dataclass(slots=True, frozen=True)
class FileEntry:
    """扫描得到的一个文件条目。"""

    path: Path
    size: int
    mtime: float
    category: FileCategory = FileCategory.OTHER

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def extension(self) -> str:
        return self.path.suffix.lower().lstrip(".")

    @property
    def category_label(self) -> str:
        return category_label(self.category)


@dataclass(slots=True)
class ScanStats:
    """一次扫描的统计结果。"""

    root: Path
    file_count: int = 0
    directory_count: int = 0
    total_size: int = 0
    skipped: int = 0
    errors: int = 0
    duration: float = 0.0
    cancelled: bool = False
    category_sizes: dict[FileCategory, int] = field(default_factory=dict)

    def add_file(self, entry: FileEntry) -> None:
        self.file_count += 1
        self.total_size += entry.size
        self.category_sizes[entry.category] = (
            self.category_sizes.get(entry.category, 0) + entry.size
        )

    @property
    def average_file_size(self) -> int:
        return self.total_size // self.file_count if self.file_count else 0


@dataclass(slots=True, frozen=True)
class DuplicateGroup:
    """一组内容完全相同的文件。"""

    size: int
    sha256: str
    files: tuple[Path, ...]

    @property
    def count(self) -> int:
        return len(self.files)

    @property
    def wasted_bytes(self) -> int:
        """除保留一份外可以回收的空间。"""
        return self.size * max(len(self.files) - 1, 0)

    @property
    def preview_name(self) -> str:
        return self.files[0].name if self.files else "(空)"


@dataclass(slots=True)
class DuplicateScanResult:
    """重复文件检测结果。"""

    roots: tuple[Path, ...]
    scanned_files: int = 0
    groups: list[DuplicateGroup] = field(default_factory=list)
    duration: float = 0.0
    cancelled: bool = False
    hashed_files: int = 0

    @property
    def group_count(self) -> int:
        return len(self.groups)

    @property
    def duplicate_file_count(self) -> int:
        return sum(max(group.count - 1, 0) for group in self.groups)

    @property
    def wasted_bytes(self) -> int:
        return sum(group.wasted_bytes for group in self.groups)


@dataclass(slots=True)
class OrganizeAction:
    """一条整理计划：把 ``source`` 移动到 ``target``。"""

    source: Path
    target: Path
    category: FileCategory = FileCategory.OTHER
    rule_name: str = "默认分类"
    size: int = 0
    status: OrganizeActionStatus = OrganizeActionStatus.READY
    message: str = ""

    @property
    def status_label(self) -> str:
        return ORGANIZE_STATUS_LABELS.get(self.status, "未知")


@dataclass(slots=True)
class OrganizePlan:
    """整理预览计划：执行前必须由用户确认。"""

    root: Path
    actions: list[OrganizeAction] = field(default_factory=list)
    scanned_files: int = 0
    skipped_files: int = 0
    duration: float = 0.0

    @property
    def total(self) -> int:
        return len(self.actions)

    @property
    def move_count(self) -> int:
        return sum(1 for action in self.actions if action.status is OrganizeActionStatus.READY)

    @property
    def conflict_count(self) -> int:
        return sum(1 for action in self.actions if action.status is OrganizeActionStatus.CONFLICT)

    @property
    def total_bytes(self) -> int:
        return sum(action.size for action in self.actions)

    def category_summary(self) -> dict[FileCategory, int]:
        """按分类统计待整理文件数量。"""
        summary: dict[FileCategory, int] = {}
        for action in self.actions:
            summary[action.category] = summary.get(action.category, 0) + 1
        return summary


@dataclass(slots=True)
class OrganizeResult:
    """整理执行结果。"""

    plan: OrganizePlan
    moved: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.failed == 0


@dataclass(slots=True, frozen=True)
class DiskUsageInfo:
    """磁盘整体使用情况。"""

    path: Path
    total: int
    used: int
    free: int

    @property
    def percent_used(self) -> float:
        return (self.used / self.total * 100.0) if self.total else 0.0


@dataclass(slots=True)
class DirectoryUsage:
    """目录占用统计节点（可递归）。"""

    path: Path
    total_size: int = 0
    file_count: int = 0
    dir_count: int = 0
    children: list[DirectoryUsage] = field(default_factory=list)
    error: str | None = None

    @property
    def name(self) -> str:
        return self.path.name or str(self.path)

    def sorted_children(self, *, by: str = "size") -> list[DirectoryUsage]:
        """按 ``size``（占用空间）或 ``count``（文件数量）排序子节点。"""
        if by == "count":
            return sorted(self.children, key=lambda node: node.file_count, reverse=True)
        return sorted(self.children, key=lambda node: node.total_size, reverse=True)

    def largest_files(
        self, entries: list[FileEntry], limit: int = 50
    ) -> list[FileEntry]:
        """在给定条目中返回当前目录范围内最大的若干文件。"""
        scoped = [entry for entry in entries if entry.path.is_relative_to(self.path)]
        return sorted(scoped, key=lambda item: item.size, reverse=True)[:limit]


@dataclass(slots=True)
class SearchQuery:
    """文件搜索条件（``size > 500MB`` 等高级语法的解析结果）。"""

    text: str = ""
    extensions: tuple[str, ...] = ()
    min_size: int | None = None
    max_size: int | None = None
    modified_after: float | None = None
    modified_before: float | None = None
    categories: tuple[FileCategory, ...] = ()
    case_sensitive: bool = False

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.text,
                self.extensions,
                self.min_size,
                self.max_size,
                self.modified_after,
                self.modified_before,
                self.categories,
            )
        )

    def matches(self, entry: FileEntry) -> bool:
        """判断文件条目是否满足条件。"""
        if self.text:
            haystack = entry.name if self.case_sensitive else entry.name.lower()
            needle = self.text if self.case_sensitive else self.text.lower()
            if needle not in haystack:
                return False
        if self.extensions and entry.extension not in self.extensions:
            return False
        if self.min_size is not None and entry.size < self.min_size:
            return False
        if self.max_size is not None and entry.size > self.max_size:
            return False
        if self.modified_after is not None and entry.mtime < self.modified_after:
            return False
        if self.modified_before is not None and entry.mtime > self.modified_before:
            return False
        if self.categories:
            return entry.category in self.categories
        return True
