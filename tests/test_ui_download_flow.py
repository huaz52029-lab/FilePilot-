"""GUI 与下载引擎的集成测试。

验证真实链路：Qt 主线程 → DownloadService → 后台 asyncio 引擎 → 信号回到界面。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest

from app.core.download.models import DownloadStatus
from app.core.storage.models import HistoryKind
from app.ui.navigation import PageId
from app.ui.widgets.download_card import DownloadCard
from tests.http_server import TestHTTPServer, serve
from tests.ui_utils import dispose_window

PAYLOAD = bytes(range(256)) * 2048  # 512 KiB


@pytest.fixture()
def http_server():  # type: ignore[no-untyped-def]
    with serve() as server:
        yield server


def pump_until(qapp, predicate: Callable[[], bool], *, timeout: float = 40.0) -> bool:
    """在保持事件循环运转的前提下等待条件成立。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_gui_download_flow(qapp, data_dir: Path, http_server: TestHTTPServer) -> None:
    """完整界面路径：粘贴链接 → 分析对话框 → 点击“开始下载” → 真实下载完成。"""
    from app.services.app_context import AppContext
    from app.ui.main_window import MainWindow
    from app.ui.widgets.probe_dialog import ProbeDialog

    save_dir = data_dir / "downloads"
    save_dir.mkdir(parents=True, exist_ok=True)
    url = http_server.register(
        "/gui.bin", PAYLOAD, etag='"gui"', modified="Wed, 21 Oct 2026 07:28:00 GMT"
    )

    context = AppContext(console_log=False)
    window = MainWindow(context, qapp)
    window.resize(1280, 800)
    window.show()
    try:
        # 引擎在后台线程异步启动
        assert pump_until(qapp, lambda: context.download_backend.started, timeout=20.0)

        service = context.downloads
        context.settings.download_dir = save_dir
        notices: list[tuple[str, str]] = []
        service.notice.connect(lambda level, message: notices.append((level, message)))
        updated: list[int] = []
        service.task_updated.connect(lambda _task: updated.append(1))

        # 走真实界面路径：下载中心接收链接并触发分析
        window._switch_page(PageId.DOWNLOADS)  # noqa: SLF001 - 集成测试
        page = window.pages[PageId.DOWNLOADS]
        page.start_download_flow(url)  # type: ignore[attr-defined]

        assert pump_until(
            qapp,
            lambda: bool(page.findChildren(ProbeDialog)),
            timeout=20.0,
        ), "分析对话框未在预期时间内出现"
        dialog = page.findChildren(ProbeDialog)[-1]
        assert dialog.isVisible()
        dialog.path_picker.set_path(save_dir)
        task_id_holder: list[str] = []
        service.task_added.connect(lambda task: task_id_holder.append(task.task_id))
        dialog.start_button.click()
        assert pump_until(qapp, lambda: bool(task_id_holder), timeout=10.0)
        task_id = task_id_holder[0]
        assert service.task(task_id) is not None

        assert pump_until(
            qapp,
            lambda: window.pages[PageId.DOWNLOADS].findChildren(DownloadCard) != [],
            timeout=10.0,
        )

        def finished() -> bool:
            task = service.task(task_id)
            return task is not None and task.status is DownloadStatus.COMPLETED

        assert pump_until(qapp, finished, timeout=60.0), "下载未在预期时间内完成"
        task = service.task(task_id)
        assert task is not None
        assert task.total_size == len(PAYLOAD)
        assert task.sha256
        assert task.save_path is not None
        assert task.save_path.read_bytes() == PAYLOAD

        # 界面确实收到了多次进度更新（而不是只在结束时刷新）
        assert len(updated) >= 2

        # 下载历史已写入数据库
        events = [
            event
            for event in context.history_repo.list_events(limit=20)
            if event.kind is HistoryKind.DOWNLOAD
        ]
        assert events, "下载完成后应写入历史记录"
        assert events[0].title == "gui.bin"
        assert "完成" in events[0].detail

        # 完成通知（提示消息）
        assert any(level == "success" for level, _message in notices)
    finally:
        dispose_window(qapp, window, context)


def test_paused_task_stays_visible_after_page_switch(
    qapp, data_dir: Path, http_server: TestHTTPServer
) -> None:
    """暂停的任务必须持续显示在下载页面，切换页面后仍然存在，并可继续。"""
    from app.core.download.models import DownloadStatus
    from app.services.app_context import AppContext
    from app.ui.main_window import MainWindow

    save_dir = data_dir / "paused-downloads"
    save_dir.mkdir(parents=True, exist_ok=True)
    payload = bytes((index * 5) % 251 for index in range(1_200_000))
    url = http_server.register("/paused.bin", payload, mode="slow", etag='"paused"')

    context = AppContext(console_log=False)
    window = MainWindow(context, qapp)
    window.resize(1280, 800)
    window.show()
    try:
        assert pump_until(qapp, lambda: context.download_backend.started, timeout=20.0)
        service = context.downloads
        probes: list[object] = []
        service.probe_ready.connect(probes.append)
        service.probe(url, save_dir)
        assert pump_until(qapp, lambda: bool(probes), timeout=20.0)

        task_id = service.start(probes[0], save_dir=save_dir)
        assert pump_until(
            qapp,
            lambda: (task := service.task(task_id)) is not None and task.downloaded > 0,
            timeout=40.0,
        ), "下载未开始接收数据"
        service.pause(task_id)
        assert pump_until(
            qapp,
            lambda: service.task(task_id) is not None
            and service.task(task_id).status is DownloadStatus.PAUSED,
            timeout=40.0,
        ), "任务未能暂停"
        paused_bytes = service.task(task_id).downloaded
        assert paused_bytes > 0

        page = window.pages[PageId.DOWNLOADS]
        # 切换页面再回来，暂停任务必须依然显示
        window._switch_page(PageId.HOME)  # noqa: SLF001 - 集成测试
        qapp.processEvents()
        window._switch_page(PageId.DOWNLOADS)  # noqa: SLF001 - 集成测试
        assert pump_until(
            qapp, lambda: len(page._paused_cards) == 1, timeout=15.0  # noqa: SLF001
        ), "暂停任务从下载页面消失了"
        assert page._paused_cards[0].task.task_id == task_id  # noqa: SLF001
        assert page.paused_section.count_badge.text() == "1"

        # 点击“继续”后应恢复下载并完成
        service.resume(task_id)
        assert pump_until(
            qapp,
            lambda: service.task(task_id) is not None
            and service.task(task_id).status is DownloadStatus.COMPLETED,
            timeout=120.0,
        ), "继续下载未能完成"
        assert (save_dir / "paused.bin").read_bytes() == payload
        assert service.task(task_id).downloaded > paused_bytes
    finally:
        dispose_window(qapp, window, context)


def test_gui_probe_failure_is_reported(qapp, data_dir: Path, http_server: TestHTTPServer) -> None:
    """链接失效时界面收到中文错误提示，而不是异常堆栈。"""
    from app.services.app_context import AppContext
    from app.ui.main_window import MainWindow

    save_dir = data_dir / "downloads"
    save_dir.mkdir(parents=True, exist_ok=True)
    url = http_server.register("/missing.bin", b"", mode="missing")

    context = AppContext(console_log=False)
    window = MainWindow(context, qapp)
    try:
        assert pump_until(qapp, lambda: context.download_backend.started, timeout=20.0)
        failures: list[str] = []
        context.downloads.probe_failed.connect(failures.append)
        context.downloads.probe(url, save_dir)
        assert pump_until(qapp, lambda: bool(failures), timeout=20.0)
        assert failures
        assert "404" in failures[0] or "不存在" in failures[0]
    finally:
        dispose_window(qapp, window, context)
