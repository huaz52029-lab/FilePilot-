"""FilePilot 程序入口。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from app.core.common.constants import (
    APP_NAME,
    APP_VERSION,
    SETTINGS_ORG,
)
from app.core.common.logger import get_logger, setup_logging
from app.core.common.paths import ensure_runtime_dirs
from app.services.app_context import AppContext


def create_application(argv: list[str] | None = None) -> QApplication:
    """创建并配置 QApplication（已存在实例时直接复用）。"""
    existing = QApplication.instance()
    if existing is not None:
        return existing  # type: ignore[return-value]
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(SETTINGS_ORG)
    app.setOrganizationDomain("filepilot.local")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(True)
    return app


def main(argv: list[str] | None = None) -> int:
    """程序主入口。"""
    args = list(argv if argv is not None else sys.argv[1:])
    ensure_runtime_dirs()
    setup_logging()
    logger = get_logger("main")

    if "--version" in args:
        return 0
    if "--self-test" in args:
        probe_url = None
        if "--self-test-url" in args:
            index = args.index("--self-test-url")
            if index + 1 < len(args):
                probe_url = args[index + 1]
        return self_test(probe_url=probe_url)

    app = create_application(argv)
    context: AppContext | None = None
    window = None
    try:
        context = AppContext()
        from app.ui.icons import default_icon_provider
        from app.ui.main_window import MainWindow

        window = MainWindow(context, app)
        app.setWindowIcon(default_icon_provider().icon("logo", color="#3B82F6", size=64))
        window.show()
        logger.info("界面已启动")
        exit_code = app.exec()
        # 退出清理：先释放窗口，再关闭后台资源，避免 Qt 析构顺序问题
        window.hide()
        window.deleteLater()
        app.processEvents()
        context.shutdown()
        context = None
        app.processEvents()
        logger.info("程序已退出（退出码 %s）", exit_code)
        return exit_code
    except Exception as exc:  # noqa: BLE001 - 启动失败给出可读提示
        logger.exception("程序启动失败：%s", exc)
        QMessageBox.critical(
            None,
            f"{APP_NAME} 启动失败",
            "程序在启动过程中遇到问题，详细信息已写入日志。\n\n"
            f"错误摘要：{exc}",
        )
        return 1
    finally:
        if context is not None:
            context.shutdown()


def self_test(*, screenshot_dir: str | None = None, probe_url: str | None = None) -> int:
    """无界面自检：构建全部页面、启动下载引擎并渲染各页面。

    打包后的 EXE 也支持 ``FilePilot.exe --self-test``，用于验证交付产物可用
    （退出码 0 表示正常）。
    """
    import os
    from pathlib import Path

    # 自检不需要真实显示器，统一使用离屏渲染，便于在 CI / 无桌面环境执行
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    logger = get_logger("selftest")
    logger.info("开始自检（版本 %s）", APP_VERSION)
    app = create_application([APP_NAME, "--self-test"])
    context: AppContext | None = None
    try:
        context = AppContext()
        from app.ui.main_window import MainWindow
        from app.ui.navigation import PageId

        window = MainWindow(context, app)
        window.resize(1280, 800)
        window.show()
        app.processEvents()

        shots = Path(screenshot_dir) if screenshot_dir else None
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)

        for page_id in PageId:
            window._switch_page(page_id)  # noqa: SLF001 - 自检内部调用
            for _ in range(3):
                app.processEvents()
            if shots is not None:
                window.grab().save(str(shots / f"{page_id.value}.png"))

        import time

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and not context.download_backend.started:
            app.processEvents()
            time.sleep(0.05)
        if not context.download_backend.started:
            logger.error("自检失败：下载引擎未能在 15 秒内启动")
            return 1

        if probe_url:
            results: list[object] = []
            errors: list[str] = []
            context.downloads.probe_ready.connect(results.append)
            context.downloads.probe_failed.connect(errors.append)
            context.downloads.probe(probe_url, context.settings.download_dir)
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline and not results and not errors:
                app.processEvents()
                time.sleep(0.05)
            if errors or not results:
                logger.error("自检失败：探测 %s 未成功（%s）", probe_url, errors)
                return 1
            logger.info(
                "自检网络验证通过：%s（大小 %s，Range=%s）",
                getattr(results[0], "file_name", "?"),
                getattr(results[0], "total_size", 0),
                getattr(results[0], "supports_range", False),
            )

        logger.info(
            "自检通过：%s 个页面、数据库版本 %s、下载引擎已就绪",
            len(window.pages),
            context.database.version,
        )
        window.hide()
        window.deleteLater()
        app.processEvents()
        return 0
    except Exception as exc:  # noqa: BLE001 - 自检需要报告失败原因
        logger.exception("自检失败：%s", exc)
        return 1
    finally:
        if context is not None:
            context.shutdown()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
