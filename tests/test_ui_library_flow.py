"""文件整理 / 重复文件页面的集成测试（验证页面与服务层接线）。"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest

from app.ui.navigation import PageId


def pump_until(qapp, predicate: Callable[[], bool], *, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.03)
    return False


@pytest.fixture()
def window(qapp, data_dir: Path):  # type: ignore[no-untyped-def]
    from app.services.app_context import AppContext
    from app.ui.main_window import MainWindow

    context = AppContext(console_log=False)
    win = MainWindow(context, qapp)
    win.resize(1280, 800)
    win.show()
    qapp.processEvents()
    try:
        yield win
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()
        context.shutdown()


def test_organizer_page_preview_and_execute(
    window, qapp, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整理页面：预览 → 确认 → 真实移动文件 → 写入历史。"""
    from app.core.storage.models import HistoryKind
    from app.ui.pages import organizer_page as page_module

    folder = data_dir / "messy"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "photo.jpg").write_bytes(b"j" * 32)
    (folder / "notes.pdf").write_bytes(b"p" * 16)
    (folder / "clip.mp4").write_bytes(b"v" * 64)

    # 跳过确认对话框（真实的确认逻辑已在核心层测试覆盖）
    monkeypatch.setattr(page_module, "confirm", lambda *args, **kwargs: True)

    page = window.pages[PageId.ORGANIZER]
    page.path_picker.set_path(str(folder))  # type: ignore[attr-defined]
    page._preview()  # noqa: SLF001 - 集成测试

    assert pump_until(qapp, lambda: page._plan is not None), "整理预览未返回结果"  # noqa: SLF001
    plan = page._plan  # noqa: SLF001
    assert plan is not None
    assert plan.move_count == 3

    page._execute()  # noqa: SLF001
    assert pump_until(
        qapp, lambda: (folder / "图片" / "photo.jpg").exists(), timeout=30.0
    ), "整理未真正移动文件"
    assert (folder / "文档" / "notes.pdf").exists()
    assert (folder / "视频" / "clip.mp4").exists()

    events = [
        event
        for event in window.context.history_repo.list_events(limit=10)
        if event.kind is HistoryKind.ORGANIZE
    ]
    assert events, "整理完成后应写入历史记录"
    assert "移动 3" in events[0].detail


def test_duplicates_page_detects_and_moves(window, qapp, data_dir: Path) -> None:
    """重复文件页面：检测 → 结果统计 → 渲染分组。"""
    from app.ui.pages.duplicates_page import DuplicatesPage

    folder = data_dir / "dup-scan"
    (folder / "a").mkdir(parents=True, exist_ok=True)
    (folder / "b").mkdir(parents=True, exist_ok=True)
    (folder / "a" / "same1.txt").write_bytes(b"duplicate-content")
    (folder / "b" / "same2.txt").write_bytes(b"duplicate-content")
    (folder / "a" / "unique.txt").write_bytes(b"unique-content")

    page = window.pages[PageId.DUPLICATES]
    assert isinstance(page, DuplicatesPage)
    page.roots_list.clear()
    page._append_root(folder)  # noqa: SLF001
    page._start_scan()  # noqa: SLF001

    assert pump_until(
        qapp, lambda: page._result is not None, timeout=40.0  # noqa: SLF001
    ), "重复文件检测未返回结果"
    result = page._result  # noqa: SLF001
    assert result is not None
    assert result.group_count == 1
    assert result.duplicate_file_count == 1
    assert page.groups_label.text() == "1"
    assert page.files_label.text() == "1"
    assert len(page._group_widgets) == 1  # noqa: SLF001
