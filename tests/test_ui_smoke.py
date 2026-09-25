"""界面冒烟测试（离屏渲染）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ui.navigation import MAIN_NAV_ITEMS, PageId


@pytest.fixture()
def window(qapp, data_dir: Path):  # type: ignore[no-untyped-def]
    """构建完整主窗口。"""
    from app.services.app_context import AppContext
    from app.ui.main_window import MainWindow

    context = AppContext(console_log=False)
    win = MainWindow(context, qapp)
    win.resize(1280, 800)
    qapp.processEvents()
    yield win
    win.close()
    win.deleteLater()
    qapp.processEvents()
    context.shutdown()


def test_theme_tokens_are_complete(qapp, window) -> None:  # type: ignore[no-untyped-def]
    manager = window.theme_manager
    for key in ("window_bg", "card_bg", "text", "accent", "error", "success"):
        assert manager.token(key), f"缺少主题令牌 {key}"
    assert manager.effective_mode in {"dark", "light"}
    stylesheet = qapp.styleSheet()
    assert "QPushButton" in stylesheet
    assert "$" not in stylesheet, "样式表占位符未被替换"


def test_pages_switch(window, qapp) -> None:  # type: ignore[no-untyped-def]
    assert set(window.pages) == set(PageId)
    for page_id in PageId:
        window._switch_page(page_id)  # noqa: SLF001 - 冒烟测试
        qapp.processEvents()
        assert window.stack.currentWidget() is window.pages[page_id]
        assert window.context.settings.last_page == page_id.value


def test_sidebar_nav_matches_pages(window) -> None:  # type: ignore[no-untyped-def]
    labels = [item.label for item in MAIN_NAV_ITEMS]
    assert labels[0] == "首页"
    assert "下载" in labels
    assert window.sidebar.width() == 220


def test_theme_switch_updates_stylesheet(window, qapp) -> None:  # type: ignore[no-untyped-def]
    from app.services.settings_service import ThemeMode

    window.theme_manager.set_mode(ThemeMode.LIGHT)
    qapp.processEvents()
    assert window.theme_manager.effective_mode == "light"
    light_sheet = qapp.styleSheet()
    window.theme_manager.set_mode(ThemeMode.DARK)
    qapp.processEvents()
    assert window.theme_manager.effective_mode == "dark"
    assert qapp.styleSheet() != light_sheet
    assert window.context.settings.theme.value == "dark"


def test_toast_and_navigation_api(window, qapp) -> None:  # type: ignore[no-untyped-def]
    window.show_toast("测试提示", "success")
    qapp.processEvents()
    assert window.status_bar.message_label.text() == "测试提示"
    window.start_download_flow("not a url")
    qapp.processEvents()
    assert window.stack.currentWidget() is window.pages[PageId.DOWNLOADS]


def test_download_backend_is_attached(window) -> None:  # type: ignore[no-untyped-def]
    """主窗口启动后下载引擎必须已接入（异步就绪由 ready 信号通知）。"""
    assert window.context.downloads.has_backend is True
    assert window.context.async_runner.is_running is True


def test_download_service_requires_backend(qapp, data_dir) -> None:  # type: ignore[no-untyped-def]
    """没有引擎时调用下载接口应给出明确的阶段提示，而不是静默失败。"""
    from app.core.common.exceptions import TaskNotImplementedError
    from app.core.storage.database import Database
    from app.core.storage.repositories import (
        DownloadRepository,
        HistoryRepository,
        SettingsRepository,
    )
    from app.services.download_service import DownloadService
    from app.services.settings_service import AppSettings

    db = Database(data_dir / "engine-less.db")
    db.initialize()
    settings = AppSettings(SettingsRepository(db))
    service = DownloadService(DownloadRepository(db), HistoryRepository(db), settings)
    assert service.has_backend is False
    with pytest.raises(TaskNotImplementedError):
        service.probe("https://example.com/a.zip")
    db.dispose()


def test_main_entrypoint_starts_and_exits_cleanly(qapp, data_dir) -> None:  # type: ignore[no-untyped-def]
    """程序入口应能正常启动并干净退出（不残留后台线程）。"""
    from PySide6.QtCore import QTimer

    from app.main import main

    QTimer.singleShot(1200, qapp.quit)
    exit_code = main(["filepilot-entrypoint-test"])
    assert exit_code == 0
    assert qapp.topLevelWidgets() == [] or all(
        not widget.isVisible() for widget in qapp.topLevelWidgets()
    )
