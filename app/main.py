"""FilePilot 程序入口。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from app.core.common.constants import (
    APP_HOMEPAGE,
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


def _self_test_downloads(
    context: AppContext,
    app: QApplication,
    *,
    fast_url: str,
    slow_url: str | None,
    workdir: str | None,
) -> list[str]:
    """在真实运行环境中验证下载链路，返回验证报告。

    覆盖：完整下载、进度更新、速度快照、SHA-256、文件落盘，
    以及（提供 ``slow_url`` 时）暂停 → 继续 → 完成 与 取消。
    失败时抛出 :class:`AssertionError`。
    """
    import tempfile
    import time
    from pathlib import Path

    from app.core.download.models import DownloadStatus

    service = context.downloads
    base = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="filepilot-selftest-"))
    base.mkdir(parents=True, exist_ok=True)
    report: list[str] = []
    counter = {"updates": 0, "speeds": 0}

    def _on_update(task: object) -> None:
        counter["updates"] += 1
        if getattr(getattr(task, "speed", None), "current", 0) > 0:
            counter["speeds"] += 1

    service.task_updated.connect(_on_update)

    def wait(predicate, timeout: float, label: str) -> None:  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError(f"{label} 超时（{timeout:.0f} 秒）")

    def probe(url: str):  # type: ignore[no-untyped-def]
        results: list[object] = []
        errors: list[str] = []
        service.probe_ready.connect(results.append)
        service.probe_failed.connect(errors.append)
        service.probe(url, base)
        wait(lambda: bool(results) or bool(errors), 30.0, "链接分析")
        if errors or not results:
            raise AssertionError(f"链接分析失败：{errors}")
        return results[-1]

    def status_of(task_id: str) -> DownloadStatus | None:
        task = service.task(task_id)
        return task.status if task is not None else None

    # 1) 完整下载：进度 / 速度 / SHA-256 / 文件落盘
    probe_result = probe(fast_url)
    task_id = service.start(probe_result, save_dir=base)
    wait(lambda: status_of(task_id) is not None and status_of(task_id).is_final, 180.0, "下载完成")
    task = service.task(task_id)
    assert task is not None and task.status is DownloadStatus.COMPLETED, (
        f"下载未完成：{getattr(task, 'error_message', '')}"
    )
    target = Path(task.save_path) if task.save_path else base / task.file_name
    assert target.exists(), "下载完成后未找到文件"
    assert target.stat().st_size == task.total_size, "文件大小与声明不一致"
    assert len(task.sha256) == 64, "未计算 SHA-256"
    assert counter["updates"] >= 2, "未收到进度更新（可能是假进度）"
    report.append(
        f"下载完成：{task.file_name}（{task.total_size} 字节，SHA-256 {task.sha256[:16]}…，"
        f"进度更新 {counter['updates']} 次）"
    )

    if not slow_url:
        return report

    # 2) 暂停 → 继续 → 完成（断点续传）
    slow_probe = probe(slow_url)
    pause_id = service.start(slow_probe, save_dir=base)
    wait(lambda: (service.task(pause_id) or None) is not None and service.task(pause_id).downloaded > 0, 60.0, "开始接收数据")
    service.pause(pause_id)
    wait(lambda: status_of(pause_id) is DownloadStatus.PAUSED, 60.0, "暂停")
    paused_bytes = service.task(pause_id).downloaded
    assert paused_bytes > 0, "暂停时没有任何已下载数据"
    service.resume(pause_id)
    wait(
        lambda: status_of(pause_id) is not None and status_of(pause_id).is_final,
        180.0,
        "续传完成",
    )
    resumed = service.task(pause_id)
    assert resumed is not None and resumed.status is DownloadStatus.COMPLETED, (
        f"续传失败：{getattr(resumed, 'error_message', '')}"
    )
    resumed_path = Path(resumed.save_path) if resumed.save_path else base / resumed.file_name
    assert resumed_path.stat().st_size == resumed.total_size, "续传后文件大小不正确"
    report.append(
        f"暂停/继续：暂停于 {paused_bytes} 字节 → 续传完成 {resumed.total_size} 字节"
    )

    # 3) 取消：不生成成品文件（临时文件保留以便重试）
    cancel_dir = base / "cancel"
    cancel_dir.mkdir(parents=True, exist_ok=True)
    cancel_id = service.start(probe(slow_url), save_dir=cancel_dir)
    wait(
        lambda: (service.task(cancel_id) or None) is not None and service.task(cancel_id).downloaded > 0,
        60.0,
        "开始接收数据（取消测试）",
    )
    service.cancel(cancel_id)
    wait(lambda: status_of(cancel_id) is DownloadStatus.CANCELLED, 60.0, "取消")
    cancelled = service.task(cancel_id)
    assert cancelled is not None
    final_path = Path(cancelled.save_path) if cancelled.save_path else cancel_dir / cancelled.file_name
    assert not final_path.exists(), "取消后不应生成成品文件"
    report.append("取消：任务已取消，未生成成品文件（临时文件保留以便重试）")
    return report


def main(argv: list[str] | None = None) -> int:
    """程序主入口。"""
    args = list(argv if argv is not None else sys.argv[1:])
    ensure_runtime_dirs()
    setup_logging()
    logger = get_logger("main")

    if "--version" in args:
        return 0
    if "--self-test" in args:
        def _value(flag: str) -> str | None:
            if flag not in args:
                return None
            index = args.index(flag)
            return args[index + 1] if index + 1 < len(args) else None

        return self_test(
            probe_url=_value("--self-test-url"),
            download_url=_value("--self-test-download"),
            slow_url=_value("--self-test-slow"),
            workdir=_value("--self-test-workdir"),
        )

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


def self_test(
    *,
    screenshot_dir: str | None = None,
    probe_url: str | None = None,
    download_url: str | None = None,
    slow_url: str | None = None,
    workdir: str | None = None,
) -> int:
    """无界面自检：构建全部页面、启动下载引擎并渲染各页面。

    打包后的 EXE 也支持 ``FilePilot.exe --self-test``，用于验证交付产物可用
    （退出码 0 表示正常）。可选开关：

    * ``--self-test-url``：验证真实 HTTP 探测链路；
    * ``--self-test-download`` / ``--self-test-slow`` / ``--self-test-workdir``：
      验证真实下载链路（下载、进度、暂停、继续、取消、SHA-256、文件保存）。
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

        # 项目地址验证：真实点击“设置 → 打开项目地址”，拦截浏览器打开的实际地址
        from PySide6.QtGui import QDesktopServices

        captured_urls: list[str] = []
        original_open_url = QDesktopServices.openUrl
        QDesktopServices.openUrl = staticmethod(  # type: ignore[assignment]
            lambda url: (captured_urls.append(url.toString()), True)[1]
        )
        try:
            settings_page = window.pages[PageId.SETTINGS]
            settings_page.on_show()  # type: ignore[attr-defined]
            app.processEvents()
            shown_address = settings_page.project_address_label.text()  # type: ignore[attr-defined]
            settings_page._open_project_page()  # noqa: SLF001 - 自检内部调用
            app.processEvents()
        finally:
            QDesktopServices.openUrl = original_open_url  # type: ignore[assignment]

        if shown_address != APP_HOMEPAGE or captured_urls != [APP_HOMEPAGE]:
            logger.error(
                "自检失败：项目地址不一致（页面显示 %s，实际打开 %s，期望 %s）",
                shown_address,
                captured_urls,
                APP_HOMEPAGE,
            )
            return 1
        logger.info("项目地址自检通过：设置页“打开项目地址” → %s", APP_HOMEPAGE)

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

        if download_url:
            try:
                lines = _self_test_downloads(
                    context,
                    app,
                    fast_url=download_url,
                    slow_url=slow_url,
                    workdir=workdir,
                )
            except AssertionError as exc:
                logger.error("自检失败（下载链路）：%s", exc)
                return 1
            except Exception as exc:  # noqa: BLE001 - 自检需要报告所有失败
                logger.exception("自检失败（下载链路异常）：%s", exc)
                return 1
            for line in lines:
                logger.info("下载自检 · %s", line)
            logger.info("下载链路自检通过（共 %s 项）", len(lines))

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
