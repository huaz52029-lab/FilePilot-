"""GUI 下载流程的调试脚本（输出写入文件，便于定位卡住的位置）。

用法::

    python tools/debug_gui_flow.py [输出日志路径]
"""

from __future__ import annotations

import faulthandler
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FILEPILOT_HOME", tempfile.mkdtemp(prefix="filepilot-debug-"))

from tests.http_server import serve  # noqa: E402

PAYLOAD = bytes(range(256)) * 2048


def main() -> int:
    log_path = Path(sys.argv[1] if len(sys.argv) > 1 else REPO_ROOT / "debug_gui_flow.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")

    def log(message: str) -> None:
        handle.write(f"{time.strftime('%H:%M:%S')} {message}\n")
        handle.flush()

    faulthandler.dump_traceback_later(12, repeat=True, file=handle)

    save_dir = Path(tempfile.mkdtemp(prefix="filepilot-debug-dl-"))
    with serve() as server:
        url = server.register("/gui.bin", PAYLOAD, etag='"gui"')
        log(f"server up {url}")

        from app.main import create_application
        from app.services.app_context import AppContext
        from app.ui.main_window import MainWindow

        app = create_application(["filepilot-debug"])
        context = AppContext(console_log=False)
        window = MainWindow(context, app)
        window.resize(1280, 800)
        window.show()
        log("window shown")

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not context.download_backend.started:
            app.processEvents()
            time.sleep(0.05)
        log(f"backend started: {context.download_backend.started}")

        service = context.downloads
        probes: list[object] = []
        failures: list[str] = []
        service.probe_ready.connect(probes.append)
        service.probe_failed.connect(failures.append)
        service.probe(url, save_dir)
        log("probe requested")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not probes and not failures:
            app.processEvents()
            time.sleep(0.05)
        log(f"probe finished: probes={len(probes)} failures={failures}")
        if not probes:
            handle.close()
            return 1

        task_id = service.start(probes[0], save_dir=save_dir)
        log(f"task started {task_id}")
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            app.processEvents()
            task = service.task(task_id)
            if task is not None and task.status.value in {"completed", "failed", "cancelled"}:
                log(f"final status={task.status.value} error={task.error_message!r}")
                break
            time.sleep(0.05)
        else:
            log(f"TIMEOUT: {service.task(task_id)}")

        log("closing window")
        window.close()
        log("window closed")
        context.shutdown()
        log("context shutdown")
    handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
