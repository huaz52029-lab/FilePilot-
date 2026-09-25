"""文件整理：生成整理计划 → 用户预览确认 → 执行移动。

安全约定：

* 只做“移动”，不删除任何文件；
* 目标已存在时标记为冲突并在执行阶段跳过（绝不覆盖）；
* 规划与执行都支持协作式取消。
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from app.core.common.exceptions import FileOperationError, describe_os_error
from app.core.common.helpers import unique_path
from app.core.common.logger import get_logger
from app.core.files.categories import FileCategory, categorize_path, category_folder
from app.core.files.models import (
    OrganizeAction,
    OrganizeActionStatus,
    OrganizePlan,
    OrganizeResult,
)
from app.core.files.rules import OrganizeRule
from app.core.files.scanner import iter_file_entries

_log = get_logger("files.organizer")

CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[int, Path], None]


def plan_by_category(
    root: Path | str,
    *,
    categories: Iterable[FileCategory] | None = None,
    recursive: bool = True,
    target_root: Path | None = None,
    should_cancel: CancelCheck | None = None,
    on_progress: ProgressCallback | None = None,
) -> OrganizePlan:
    """按内置分类生成整理计划。

    每个文件会被规划移动到 ``<target_root>/<分类目录>/``。
    """
    base = Path(root)
    destination_root = Path(target_root) if target_root else base
    allowed = set(categories) if categories is not None else None
    plan = OrganizePlan(root=base)
    started = time.perf_counter()
    cancelled = should_cancel or (lambda: False)

    for entry in iter_file_entries(base, should_cancel=cancelled, on_progress=on_progress):
        category = entry.category or categorize_path(entry.path)
        if allowed is not None and category not in allowed:
            plan.skipped_files += 1
            continue
        plan.scanned_files += 1
        if entry.path.parent != destination_root / category_folder(category):
            plan.actions.append(
                _make_action(
                    entry.path,
                    destination_root / category_folder(category),
                    category=category,
                    rule_name="默认分类",
                    size=entry.size,
                )
            )
        else:
            plan.skipped_files += 1

    plan.duration = time.perf_counter() - started
    _log.info(
        "整理计划（默认分类）：扫描 %s 个文件，计划移动 %s 个，冲突 %s 个",
        plan.scanned_files,
        plan.move_count,
        plan.conflict_count,
    )
    return plan


def plan_by_rules(
    root: Path | str,
    rules: Iterable[OrganizeRule],
    *,
    recursive: bool = True,
    should_cancel: CancelCheck | None = None,
    on_progress: ProgressCallback | None = None,
) -> OrganizePlan:
    """按自定义规则生成整理计划（规则按优先级顺序匹配，先命中者生效）。"""
    base = Path(root)
    ordered = sorted(
        [rule for rule in rules if rule.enabled], key=lambda item: (item.priority, item.name)
    )
    plan = OrganizePlan(root=base)
    started = time.perf_counter()
    cancelled = should_cancel or (lambda: False)

    for entry in iter_file_entries(base, should_cancel=cancelled, on_progress=on_progress):
        plan.scanned_files += 1
        matched: OrganizeRule | None = None
        for rule in ordered:
            if rule.matches(entry):
                matched = rule
                break
        if matched is None:
            plan.skipped_files += 1
            continue
        plan.actions.append(
            _make_action(
                entry.path,
                matched.target_dir,
                category=entry.category or categorize_path(entry.path),
                rule_name=matched.name,
                size=entry.size,
            )
        )

    plan.duration = time.perf_counter() - started
    _log.info(
        "整理计划（自定义规则）：扫描 %s 个文件，计划移动 %s 个，冲突 %s 个",
        plan.scanned_files,
        plan.move_count,
        plan.conflict_count,
    )
    return plan


def _make_action(
    source: Path,
    target_dir: Path,
    *,
    category: FileCategory,
    rule_name: str,
    size: int,
) -> OrganizeAction:
    """构造一条整理动作，并检测目标冲突。"""
    target = target_dir / source.name
    if target.exists() and target != source:
        return OrganizeAction(
            source=source,
            target=target,
            category=category,
            rule_name=rule_name,
            size=size,
            status=OrganizeActionStatus.CONFLICT,
            message="目标位置已存在同名文件，默认跳过。",
        )
    if source.parent == target_dir:
        return OrganizeAction(
            source=source,
            target=target,
            category=category,
            rule_name=rule_name,
            size=size,
            status=OrganizeActionStatus.SKIPPED,
            message="文件已在该目录中。",
        )
    return OrganizeAction(
        source=source,
        target=target,
        category=category,
        rule_name=rule_name,
        size=size,
    )


def execute_plan(
    plan: OrganizePlan,
    *,
    should_cancel: CancelCheck | None = None,
    on_progress: Callable[[int, OrganizeAction], None] | None = None,
) -> OrganizeResult:
    """执行整理计划（同一分区使用移动，跨分区自动退化为复制 + 删除源文件）。"""
    result = OrganizeResult(plan=plan)
    cancelled = should_cancel or (lambda: False)
    for index, action in enumerate(plan.actions, start=1):
        if cancelled():
            result.errors.append("用户取消了整理操作。")
            break
        if action.status is OrganizeActionStatus.CONFLICT:
            action.status = OrganizeActionStatus.SKIPPED
            result.skipped += 1
            continue
        if action.status is OrganizeActionStatus.SKIPPED:
            result.skipped += 1
            continue
        try:
            _move_file(action.source, action.target)
        except FileOperationError as exc:
            action.status = OrganizeActionStatus.FAILED
            action.message = exc.user_message
            result.failed += 1
            result.errors.append(f"{action.source.name}：{exc.user_message}")
            _log.warning("整理失败 %s：%s", action.source, exc.detail or exc.message)
        else:
            action.status = OrganizeActionStatus.DONE
            action.message = f"已移动到 {action.target.parent}"
            result.moved += 1
        if on_progress is not None:
            on_progress(index, action)
    _log.info(
        "整理完成：移动 %s 个，跳过 %s 个，失败 %s 个",
        result.moved,
        result.skipped,
        result.failed,
    )
    return result


def _move_file(source: Path, target: Path) -> Path:
    """移动单个文件；目标存在时自动改名，绝不覆盖。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    destination = unique_path(target) if target.exists() else target
    try:
        shutil.move(str(source), str(destination))
    except OSError as exc:
        raise FileOperationError(describe_os_error(exc), detail=f"{source} → {destination}") from exc
    return destination


def archive_download(
    file_path: Path | str,
    *,
    mode: str,
    archive_root: Path | str | None = None,
    rules: Iterable[OrganizeRule] | None = None,
) -> Path | None:
    """下载完成后的自动归档。

    ``mode``：

    * ``off``      不做任何处理，返回 ``None``；
    * ``category`` 移动到 ``<归档根目录>/<分类>/``；
    * ``rules``    按自定义规则移动，未命中则留在原地。
    """
    source = Path(file_path)
    if mode == "off" or not source.exists():
        return None
    root = Path(archive_root) if archive_root else source.parent
    if mode == "category":
        category = categorize_path(source)
        destination_dir = root / category_folder(category)
    elif mode == "rules":
        from app.core.files.models import FileEntry

        try:
            stat = source.stat()
        except OSError:  # pragma: no cover - 文件被占用
            return None
        entry = FileEntry(
            path=source,
            size=stat.st_size,
            mtime=stat.st_mtime,
            category=categorize_path(source),
        )
        ordered = sorted(
            [rule for rule in (rules or []) if rule.enabled],
            key=lambda item: (item.priority, item.name),
        )
        matched = next((rule for rule in ordered if rule.matches(entry)), None)
        if matched is None:
            return None
        destination_dir = matched.target_dir
    else:  # pragma: no cover - 未知模式
        return None

    if source.parent == destination_dir:
        return None
    try:
        return _move_file(source, destination_dir / source.name)
    except FileOperationError as exc:
        _log.warning("自动归档失败：%s", exc.user_message)
        return None
