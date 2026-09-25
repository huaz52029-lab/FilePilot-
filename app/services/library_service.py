"""文件管理类后台任务的应用服务。

负责把耗时的扫描 / 整理 / 查重 / 搜索放到线程池，并统一写入历史记录。
界面只负责展示，不直接触碰业务逻辑。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from app.core.common.exceptions import to_user_message
from app.core.common.logger import get_logger
from app.core.files.categories import FileCategory
from app.core.files.duplicate import find_duplicates, move_to_duplicate_folder
from app.core.files.models import OrganizePlan, OrganizeResult
from app.core.files.organizer import execute_plan, plan_by_category, plan_by_rules
from app.core.files.rules import OrganizeRule
from app.core.files.search import search_files
from app.core.storage.models import HistoryKind, HistoryStatus
from app.core.storage.repositories import HistoryRepository, RuleRepository
from app.services.settings_service import AppSettings
from app.services.workers import FunctionWorker, TaskRunner

_log = get_logger("services.library")


class LibraryService(QObject):
    """文件整理 / 重复文件 / 搜索任务编排。"""

    organize_preview_ready = Signal(object)  # OrganizePlan
    organize_finished = Signal(object)  # OrganizeResult
    duplicates_ready = Signal(object)  # DuplicateScanResult
    duplicates_moved = Signal(int)  # 已移动的重复文件数量
    search_ready = Signal(object)  # list[FileEntry]
    failed = Signal(str)
    progress = Signal(object)

    def __init__(
        self,
        *,
        runner: TaskRunner,
        history: HistoryRepository,
        rules: RuleRepository,
        settings: AppSettings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner = runner
        self._history = history
        self._rules = rules
        self._settings = settings

    # ------------------------------------------------------------------
    # 整理
    # ------------------------------------------------------------------
    def preview_organize(
        self,
        root: Path,
        *,
        mode: str = "category",
        categories: Iterable[FileCategory] | None = None,
        target_root: Path | None = None,
    ) -> FunctionWorker:
        """生成整理计划（只读，不修改任何文件）。"""
        rules = [rule for rule in self._rules.list_rules(enabled_only=True)]
        if mode == "rules":
            return self._runner.submit(
                plan_by_rules,
                root,
                rules,
                on_result=self.organize_preview_ready.emit,
                on_error=self._on_error,
                on_progress=self.progress.emit,
                on_cancelled=lambda: self.failed.emit("已取消整理预览。"),
            )
        return self._runner.submit(
            plan_by_category,
            root,
            on_result=self.organize_preview_ready.emit,
            on_error=self._on_error,
            on_progress=self.progress.emit,
            on_cancelled=lambda: self.failed.emit("已取消整理预览。"),
            categories=categories,
            # 默认在所选文件夹内部建立分类子目录；
            # 只有显式传入 target_root 时才移动到其它位置。
            target_root=target_root,
        )

    def execute_organize(self, plan: OrganizePlan) -> FunctionWorker:
        """执行整理计划（由界面确认后调用）。"""
        return self._runner.submit(
            self._run_plan,
            plan,
            on_result=self._on_plan_executed,
            on_error=self._on_error,
            on_cancelled=lambda: self.failed.emit("已取消整理操作。"),
        )

    def _run_plan(self, plan: OrganizePlan, *, report=None, token=None) -> OrganizeResult:  # type: ignore[no-untyped-def]
        should_cancel = (lambda: token.cancelled) if token is not None else None
        return execute_plan(
            plan,
            should_cancel=should_cancel,
            on_progress=(lambda index, action: report((index, action))) if report else None,
        )

    def _on_plan_executed(self, result: OrganizeResult) -> None:
        """写入整理历史并通知界面。"""
        detail = f"移动 {result.moved} 个，跳过 {result.skipped} 个，失败 {result.failed} 个"
        try:
            self._history.add(
                kind=HistoryKind.ORGANIZE,
                action="moved",
                title=f"整理 {result.plan.root.name or result.plan.root}",
                detail=detail,
                path=str(result.plan.root),
                size=result.plan.total_bytes,
                status=HistoryStatus.SUCCESS if result.failed == 0 else HistoryStatus.WARNING,
            )
        except Exception as exc:  # noqa: BLE001 - 历史写入失败不影响结果
            _log.warning("写入整理历史失败：%s", exc)
        self.organize_finished.emit(result)

    # ------------------------------------------------------------------
    # 重复文件
    # ------------------------------------------------------------------
    def scan_duplicates(self, roots: Iterable[Path]) -> FunctionWorker:
        """检测重复文件。"""
        return self._runner.submit(
            find_duplicates,
            list(roots),
            on_result=self._on_duplicates_ready,
            on_error=self._on_error,
            on_progress=self.progress.emit,
            on_cancelled=lambda: self.failed.emit("已取消重复文件检测。"),
        )

    def _on_duplicates_ready(self, result) -> None:  # type: ignore[no-untyped-def]
        try:
            self._history.add(
                kind=HistoryKind.HASH,
                action="duplicates",
                title=f"重复文件检测（{result.group_count} 组）",
                detail=f"扫描 {result.scanned_files} 个文件，可回收 {result.wasted_bytes} 字节",
                path="；".join(str(root) for root in result.roots),
                size=result.wasted_bytes,
                status=HistoryStatus.SUCCESS,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("写入查重历史失败：%s", exc)
        self.duplicates_ready.emit(result)

    def move_duplicates(self, paths: Iterable[Path], *, target_dir: Path) -> FunctionWorker:
        """把重复文件移动到指定文件夹（默认不删除）。"""
        return self._runner.submit(
            move_to_duplicate_folder,
            list(paths),
            target_dir=target_dir,
            on_result=self._on_duplicates_moved,
            on_error=self._on_error,
        )

    def _on_duplicates_moved(self, result: tuple[int, list[str]]) -> None:
        moved, errors = result
        try:
            self._history.add(
                kind=HistoryKind.FILE,
                action="moved",
                title=f"移动重复文件 {moved} 个",
                detail="；".join(errors) if errors else "已移动到“重复文件”文件夹",
                status=HistoryStatus.SUCCESS if not errors else HistoryStatus.WARNING,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("写入文件操作历史失败：%s", exc)
        if errors:
            self.failed.emit(f"移动完成 {moved} 个，{len(errors)} 个失败：{errors[0]}")
        else:
            self.progress.emit(f"已移动 {moved} 个重复文件。")
        self.duplicates_moved.emit(moved)

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------
    def search(self, roots: Iterable[Path], query: str, *, limit: int = 500) -> FunctionWorker:
        """执行文件搜索。"""
        return self._runner.submit(
            search_files,
            list(roots),
            query,
            limit=limit,
            on_result=self.search_ready.emit,
            on_error=self._on_error,
            on_progress=self.progress.emit,
        )

    # ------------------------------------------------------------------
    # 规则
    # ------------------------------------------------------------------
    def list_rules(self, *, enabled_only: bool = False) -> list[OrganizeRule]:
        return self._rules.list_rules(enabled_only=enabled_only)

    def save_rule(self, rule: OrganizeRule) -> None:
        self._rules.save_rule(rule)

    def delete_rule(self, rule_id: str) -> None:
        self._rules.delete_rule(rule_id)

    def _on_error(self, message: str, detail: str) -> None:
        _log.error("后台文件任务失败：%s\n%s", message, detail)
        self.failed.emit(to_user_message(message))
