"""退出阶段崩溃的排查工具。

用法::

    python tools/exit_crash_probe.py windows 6      # 反复创建/关闭主窗口
    python tools/exit_crash_probe.py contexts 6     # 只创建应用上下文（含下载引擎）
    python tools/exit_crash_probe.py pages 6        # 只构建页面，不创建主窗口
    python tools/exit_crash_probe.py theme 6        # 只反复应用主题
    python tools/exit_crash_probe.py downloads 3    # 反复执行真实下载（含主窗口）

退出码非 0 说明该组合会触发进程级内存错误。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FILEPILOT_HOME", tempfile.mkdtemp(prefix="filepilot-probe-"))


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "windows"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 6

    from app.main import create_application

    app = create_application(["filepilot-probe"])

    if mode == "theme":
        from app.services.app_context import AppContext
        from app.services.settings_service import ThemeMode
        from app.ui.theme import ThemeManager

        context = AppContext(console_log=False)
        manager = ThemeManager(app, context.settings)
        for index in range(count):
            manager.set_mode(ThemeMode.LIGHT if index % 2 else ThemeMode.DARK)
            app.processEvents()
        context.shutdown()
        return 0

    if mode == "contexts":
        from app.services.app_context import AppContext

        for _ in range(count):
            context = AppContext(console_log=False)
            context.shutdown()
        return 0

    if mode == "pages":
        from app.services.app_context import AppContext
        from app.ui.pages.about_page import AboutPage
        from app.ui.pages.downloads_page import DownloadsPage
        from app.ui.pages.duplicates_page import DuplicatesPage
        from app.ui.pages.history_page import HistoryPage
        from app.ui.pages.home_page import HomePage
        from app.ui.pages.organizer_page import OrganizerPage
        from app.ui.pages.settings_page import SettingsPage
        from app.ui.pages.storage_page import StoragePage
        from app.ui.theme import ThemeManager, set_active_theme

        page_types = (
            HomePage,
            DownloadsPage,
            OrganizerPage,
            StoragePage,
            DuplicatesPage,
            HistoryPage,
            SettingsPage,
            AboutPage,
        )
        for _ in range(count):
            context = AppContext(console_log=False)
            manager = ThemeManager(app, context.settings)
            set_active_theme(manager)
            manager.apply()
            for page_type in page_types:
                page = page_type(context, None)
                app.processEvents()
                page.deleteLater()
            app.processEvents()
            context.shutdown()
        return 0

    if mode == "downloads":
        import time

        from app.services.app_context import AppContext
        from app.ui.main_window import MainWindow
        from tests.http_server import serve

        payload = bytes(range(256)) * 2048
        save_dir = Path(tempfile.mkdtemp(prefix="filepilot-probe-dl-"))
        with serve() as server:
            url = server.register("/probe.bin", payload, etag='"probe"')
            for _ in range(count):
                context = AppContext(console_log=False)
                window = MainWindow(context, app)
                window.show()

                deadline = time.monotonic() + 20
                while time.monotonic() < deadline and not context.download_backend.started:
                    app.processEvents()
                    time.sleep(0.02)

                probes: list[object] = []
                context.downloads.probe_ready.connect(probes.append)
                context.downloads.probe(url, save_dir)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline and not probes:
                    app.processEvents()
                    time.sleep(0.02)

                task_id = context.downloads.start(probes[0], save_dir=save_dir)
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    app.processEvents()
                    task = context.downloads.task(task_id)
                    if task is not None and task.status.value in {
                        "completed",
                        "failed",
                        "cancelled",
                    }:
                        break
                    time.sleep(0.02)
                window.close()
                app.processEvents()
                context.shutdown()
        return 0

    from app.services.app_context import AppContext
    from app.ui.main_window import MainWindow

    for _ in range(count):
        context = AppContext(console_log=False)
        window = MainWindow(context, app)
        window.show()
        app.processEvents()
        window.close()
        app.processEvents()
        context.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
