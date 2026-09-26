"""批量重命名。

流程与安全约定：

1. 先生成**预览计划**（不修改任何文件）；
2. 计划中标记冲突（目标已存在且不属于本次重命名集合），存在冲突时不允许执行；
3. 执行前再次校验，绝不覆盖已有文件；
4. 支持两种命名方式：``名称 + 编号``（照片0001.jpg）与 ``纯编号``（0001.jpg）；
5. 自动保留原扩展名（``.tar.gz`` 等双扩展名同样保留）；
6. 支持按 文件名 / 修改时间 / 创建时间 / 文件大小 排序后再编号。
"""

from __future__ import annotations

import os
import re
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final

from app.core.common.exceptions import FileOperationError
from app.core.common.logger import get_logger

_log = get_logger("files.renamer")

CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[int, int, Path], None]  # (已完成, 总数, 当前文件)

#: 需要整体保留的双扩展名
COMPOUND_SUFFIXES: Final[tuple[str, ...]] = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tar.zst",
)

_TEMP_PREFIX: Final[str] = ".filepilot-renaming-"
_NATURAL_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)")


class RenameMode(StrEnum):
    """命名方式。"""

    PREFIX_NUMBER = "prefix"
    NUMBER_ONLY = "number"

    @property
    def label(self) -> str:
        return {"prefix": "名称 + 编号", "number": "纯编号"}[self.value]


class SortKey(StrEnum):
    """编号顺序的排序依据。"""

    NAME = "name"
    MODIFIED = "modified"
    CREATED = "created"
    SIZE = "size"

    @property
    def label(self) -> str:
        return {
            "name": "文件名",
            "modified": "修改时间",
            "created": "创建时间",
            "size": "文件大小",
        }[self.value]


class RenameStatus(StrEnum):
    """单个重命名动作的状态。"""

    READY = "ready"
    CONFLICT = "conflict"
    UNCHANGED = "unchanged"
    INVALID = "invalid"

    @property
    def label(self) -> str:
        return {
            "ready": "待重命名",
            "conflict": "目标已存在",
            "unchanged": "名称未变化",
            "invalid": "名称非法",
        }[self.value]


@dataclass(slots=True)
class RenameAction:
    """一条重命名动作。"""

    source: Path
    target: Path
    status: RenameStatus = RenameStatus.READY
    message: str = ""
    size: int = 0

    @property
    def status_label(self) -> str:
        return self.status.label


@dataclass(slots=True)
class RenamePlan:
    """重命名预览计划（执行前必须由用户确认）。"""

    root: Path
    mode: RenameMode = RenameMode.PREFIX_NUMBER
    prefix: str = ""
    start: int = 1
    padding: int = 4
    sort_key: SortKey = SortKey.NAME
    extensions: tuple[str, ...] = ()
    actions: list[RenameAction] = field(default_factory=list)
    scanned_files: int = 0
    duration: float = 0.0

    @property
    def total(self) -> int:
        return len(self.actions)

    @property
    def ready_actions(self) -> list[RenameAction]:
        return [action for action in self.actions if action.status is RenameStatus.READY]

    @property
    def ready_count(self) -> int:
        return len(self.ready_actions)

    @property
    def conflict_count(self) -> int:
        return sum(1 for action in self.actions if action.status is RenameStatus.CONFLICT)

    @property
    def unchanged_count(self) -> int:
        return sum(1 for action in self.actions if action.status is RenameStatus.UNCHANGED)

    @property
    def invalid_count(self) -> int:
        return sum(1 for action in self.actions if action.status is RenameStatus.INVALID)

    @property
    def total_bytes(self) -> int:
        return sum(action.size for action in self.actions)

    @property
    def can_execute(self) -> bool:
        """存在冲突或非法名称时不允许执行。"""
        return self.ready_count > 0 and self.conflict_count == 0 and self.invalid_count == 0

    def preview_rows(self, limit: int = 200) -> list[tuple[str, str, RenameStatus]]:
        """返回 ``(原文件名, 新文件名, 状态)`` 预览行。"""
        return [
            (action.source.name, action.target.name, action.status)
            for action in self.actions[:limit]
        ]


@dataclass(slots=True)
class RenameResult:
    """重命名执行结果。"""

    plan: RenamePlan
    renamed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)
    cancelled: bool = False

    @property
    def success(self) -> bool:
        return self.failed == 0 and not self.cancelled

    @property
    def summary(self) -> str:
        parts = [f"成功 {self.renamed} 个"]
        if self.failed:
            parts.append(f"失败 {self.failed} 个")
        if self.skipped:
            parts.append(f"跳过 {self.skipped} 个")
        if self.cancelled:
            parts.append("已取消")
        return "，".join(parts)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def split_extension(name: str) -> tuple[str, str]:
    """拆分文件名与扩展名（保留 ``.tar.gz`` 这类双扩展名）。"""
    lowered = name.lower()
    for suffix in COMPOUND_SUFFIXES:
        if lowered.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)], name[-len(suffix) :]
    stem, dot, suffix = name.rpartition(".")
    if dot and stem:
        return stem, f".{suffix}"
    return name, ""


def natural_key(text: str) -> tuple[object, ...]:
    """自然排序键：``IMG_2`` 排在 ``IMG_10`` 之前。"""
    parts = _NATURAL_RE.split(text.lower())
    key: list[object] = []
    for index, part in enumerate(parts):
        key.append(int(part) if index % 2 else part)
    return tuple(key)


def has_invalid_characters(text: str) -> bool:
    """文件名是否包含 Windows 非法字符。"""
    return bool(re.search(r'[<>:"/\\|?*\x00-\x1f]', text))


def sanitize_prefix(prefix: str) -> str:
    """清理名称部分（去掉非法字符与首尾空白）。"""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", prefix or "")
    return cleaned.strip().strip(".")


def list_candidate_files(
    root: Path | str,
    *,
    extensions: Iterable[str] = (),
    recursive: bool = False,
) -> list[Path]:
    """列出待重命名的文件（跳过临时文件与隐藏文件）。"""
    base = Path(root)
    wanted = {item.strip().lower().lstrip(".") for item in extensions if item.strip()}
    try:
        entries = list(base.rglob("*") if recursive else base.glob("*"))
    except OSError as exc:
        raise FileOperationError("无法读取该文件夹。", detail=f"{base}: {exc}") from exc
    files: list[Path] = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
        except OSError:  # pragma: no cover - 条目在遍历中消失
            continue
        if entry.name.startswith(_TEMP_PREFIX) or entry.name.startswith("."):
            continue
        if wanted:
            extension = split_extension(entry.name)[1].lower().lstrip(".")
            if extension not in wanted:
                continue
        files.append(entry)
    return files


def _file_stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except OSError:  # pragma: no cover - 条目被占用或删除
        return None


def sort_paths(paths: Iterable[Path], sort_key: SortKey) -> list[Path]:
    """按指定依据排序（同值时用自然名称做稳定兜底）。"""
    items = list(paths)
    if sort_key is SortKey.NAME:
        return sorted(items, key=lambda path: natural_key(path.name))
    if sort_key is SortKey.MODIFIED:
        return sorted(
            items,
            key=lambda path: (
                stat.st_mtime if (stat := _file_stat(path)) else 0.0,
                natural_key(path.name),
            ),
        )
    if sort_key is SortKey.CREATED:
        return sorted(
            items,
            key=lambda path: (
                stat.st_ctime if (stat := _file_stat(path)) else 0.0,
                natural_key(path.name),
            ),
        )
    if sort_key is SortKey.SIZE:
        return sorted(
            items,
            key=lambda path: (
                stat.st_size if (stat := _file_stat(path)) else 0,
                natural_key(path.name),
            ),
        )
    return items  # pragma: no cover - 枚举已覆盖全部分支


# ---------------------------------------------------------------------------
# 计划与执行
# ---------------------------------------------------------------------------
def build_rename_plan(
    root: Path | str,
    *,
    mode: RenameMode = RenameMode.PREFIX_NUMBER,
    prefix: str = "",
    start: int = 1,
    padding: int = 4,
    sort_key: SortKey = SortKey.NAME,
    extensions: Iterable[str] = (),
    recursive: bool = False,
    should_cancel: CancelCheck | None = None,
) -> RenamePlan:
    """生成重命名预览计划（不会修改任何文件）。"""
    base = Path(root)
    if not base.exists() or not base.is_dir():
        raise FileOperationError("请选择一个存在的文件夹。", detail=str(base))

    cancelled = should_cancel or (lambda: False)
    started = time.perf_counter()
    plan = RenamePlan(
        root=base,
        mode=mode,
        prefix=sanitize_prefix(prefix),
        start=max(1, int(start)),
        padding=max(1, min(10, int(padding))),
        sort_key=sort_key,
        extensions=tuple(extensions),
    )
    candidates = sort_paths(
        list_candidate_files(base, extensions=plan.extensions, recursive=recursive),
        sort_key,
    )
    plan.scanned_files = len(candidates)

    # 同一批次内已占用的目标名（用于检测冲突）
    reserved: set[str] = set()
    index = plan.start
    for path in candidates:
        if cancelled():
            break
        stem, suffix = split_extension(path.name)
        number = str(index).zfill(plan.padding)
        new_stem = number if mode is RenameMode.NUMBER_ONLY else f"{plan.prefix}{number}"
        if not new_stem or has_invalid_characters(new_stem):
            plan.actions.append(
                RenameAction(
                    source=path,
                    target=path.with_name(path.name),
                    status=RenameStatus.INVALID,
                    message="生成的文件名不合法，请修改名称或编号设置。",
                )
            )
            continue
        target = path.with_name(f"{new_stem}{suffix}")
        stat = _file_stat(path)
        action = RenameAction(source=path, target=target, size=stat.st_size if stat else 0)

        if target.name == path.name:
            action.status = RenameStatus.UNCHANGED
            action.message = "新名称与原名称相同。"
        elif target.name.lower() in reserved:
            action.status = RenameStatus.CONFLICT
            action.message = "本次批量操作中有多个文件会得到相同名称。"
        elif target.exists() and target != path:
            action.status = RenameStatus.CONFLICT
            action.message = "目标文件名已存在，请调整名称 / 起始编号，或先改名。"
        reserved.add(target.name.lower())
        plan.actions.append(action)
        index += 1

    plan.duration = time.perf_counter() - started
    _log.info(
        "重命名计划：扫描 %s 个文件，待重命名 %s 个，冲突 %s 个",
        plan.scanned_files,
        plan.ready_count,
        plan.conflict_count,
    )
    return plan


def _needs_two_phase(actions: list[RenameAction]) -> bool:
    """是否存在“目标名恰好是另一个源文件名”的情况（需要两阶段重命名）。"""
    sources = {action.source.name.lower() for action in actions}
    return any(action.target.name.lower() in sources for action in actions)


def execute_rename_plan(
    plan: RenamePlan,
    *,
    should_cancel: CancelCheck | None = None,
    on_progress: ProgressCallback | None = None,
) -> RenameResult:
    """执行重命名计划。

    * 执行前重新检查目标是否已存在，绝不覆盖；
    * 若目标名与批次内其它源文件同名，先重命名为临时名再落到目标名，
      避免覆盖尚未处理的文件；
    * 单个文件失败不会中断整体流程。
    """
    result = RenameResult(plan=plan)
    cancelled = should_cancel or (lambda: False)
    ready = [action for action in plan.actions if action.status is RenameStatus.READY]
    result.skipped = plan.total - len(ready)
    if not ready:
        return result

    two_phase = _needs_two_phase(ready)
    total = len(ready)
    staged: dict[Path, Path] = {}  # 临时文件 → 最终目标

    for index, action in enumerate(ready, start=1):
        if cancelled():
            result.cancelled = True
            break
        if on_progress is not None:
            on_progress(index - 1, total, action.source)
        if not action.source.exists():
            result.failed += 1
            result.errors.append((action.source, "文件不存在或已被移动"))
            continue
        try:
            if two_phase:
                temporary = action.source.with_name(f"{_TEMP_PREFIX}{uuid.uuid4().hex}")
                os.replace(action.source, temporary)
                staged[temporary] = action.target
            else:
                _rename_one(action.source, action.target)
        except (OSError, FileOperationError) as exc:
            result.failed += 1
            result.errors.append((action.source, _friendly_reason(exc)))
            _log.warning("重命名失败 %s：%s", action.source, exc)
            continue
        result.renamed += 1

    if two_phase and staged:
        for temporary, target in list(staged.items()):
            if cancelled():
                result.cancelled = True
            try:
                _rename_one(temporary, target)
            except (OSError, FileOperationError) as exc:
                original = temporary.name
                result.failed += 1
                result.renamed = max(0, result.renamed - 1)
                result.errors.append((temporary, _friendly_reason(exc)))
                # 尽力恢复原名，避免文件停留在临时名
                try:
                    restore = temporary.parent / original
                    if temporary.exists():
                        os.replace(temporary, restore)
                except OSError:  # pragma: no cover - 恢复失败时保留临时名
                    _log.error("恢复临时文件名失败：%s", temporary)

    if on_progress is not None:
        last = ready[-1].source if ready else plan.root
        on_progress(total, total, last)
    _log.info("重命名完成：%s", result.summary)
    return result


def _rename_one(source: Path, target: Path) -> None:
    """执行单个重命名；目标已存在时抛出异常（绝不覆盖）。"""
    if target.exists() and target != source:
        raise FileOperationError(
            "目标文件名已存在，未执行重命名。", detail=str(target)
        )
    os.replace(source, target)


def _friendly_reason(exc: BaseException) -> str:
    """把底层错误转换为用户可读原因。"""
    from app.core.common.exceptions import describe_os_error

    if isinstance(exc, FileOperationError):
        return exc.user_message
    return describe_os_error(exc)
