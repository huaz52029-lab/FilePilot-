"""下载任务的通用操作处理（首页与下载页共用）。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication

from app.core.common.exceptions import FilePilotError
from app.core.common.helpers import open_in_explorer, open_path
from app.core.download.models import DownloadTask
from app.ui.widgets.dialog import confirm
from app.ui.widgets.download_card import TaskAction
from app.ui.widgets.download_detail import DownloadDetailDialog
from app.ui.widgets.toast import ToastLevel

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注
    from app.ui.pages.base_page import BasePage


def handle_task_action(page: BasePage, task_id: str, action: TaskAction) -> None:
    """统一处理任务卡片上的操作。"""
    service = page.downloads
    task = service.task(task_id)
    if task is None:
        page.toast("该任务已不存在。", ToastLevel.WARNING)
        return

    try:
        match action:
            case TaskAction.PAUSE:
                service.pause(task_id)
            case TaskAction.RESUME:
                service.resume(task_id)
            case TaskAction.RETRY:
                service.retry(task_id)
            case TaskAction.CANCEL:
                service.cancel(task_id)
            case TaskAction.DETAIL:
                DownloadDetailDialog(task, page).exec()
            case TaskAction.OPEN_FILE:
                _open_file(page, task)
            case TaskAction.OPEN_FOLDER:
                _open_folder(page, task)
            case TaskAction.COPY_URL:
                QApplication.clipboard().setText(task.final_url or task.url)
                page.toast("下载链接已复制到剪贴板。", ToastLevel.SUCCESS)
            case TaskAction.COPY_HASH:
                QApplication.clipboard().setText(task.sha256)
                page.toast("SHA-256 已复制到剪贴板。", ToastLevel.SUCCESS)
            case TaskAction.DELETE:
                _delete_task(page, task)
    except FilePilotError as exc:
        page.report_error(exc)
    except OSError as exc:
        page.report_error(exc, title="文件操作失败")


def _open_file(page: BasePage, task: DownloadTask) -> None:
    target = task.save_path or (task.save_dir / task.file_name)
    if not Path(target).exists():
        page.toast("文件不存在或已被移动。", ToastLevel.WARNING)
        return
    open_path(target)


def _open_folder(page: BasePage, task: DownloadTask) -> None:
    target = task.save_path or (task.save_dir / task.file_name)
    if Path(target).exists():
        open_in_explorer(target, select=True)
    else:
        open_in_explorer(task.save_dir)


def _delete_task(page: BasePage, task: DownloadTask) -> None:
    """删除任务记录（默认不删除已下载文件）。"""
    if task.status.is_active:
        page.toast("请先暂停或取消任务，再删除记录。", ToastLevel.WARNING)
        return
    finished = task.status.value in {"completed", "failed", "cancelled"}
    detail = (
        "任务记录将被移除，已下载的文件会保留在磁盘上。"
        if finished
        else "任务记录将被移除，未完成的临时文件会被清理。"
    )
    if not confirm(
        page,
        "删除任务记录",
        f"确定要删除“{task.file_name}”的任务记录吗？\n{detail}",
        confirm_text="删除记录",
        danger=True,
    ):
        return
    page.downloads.delete_task(task.task_id, delete_files=not finished)
    page.toast("任务记录已删除。", ToastLevel.SUCCESS)
