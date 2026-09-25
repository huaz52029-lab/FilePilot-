"""pytest 公共夹具。

所有测试都在隔离的数据目录中运行，不会触碰真实用户数据。
"""

from __future__ import annotations

import gc
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Qt 测试必须使用离屏平台，避免依赖真实显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: 保持 QApplication 引用，避免解释器退出阶段析构顺序导致 Qt 内存错误
_APP_REFS: list[object] = []


@pytest.fixture(scope="session")
def qapp() -> Iterator[QApplication]:  # noqa: F821
    """会话级 QApplication。"""
    from PySide6.QtWidgets import QApplication

    from app.main import create_application

    app = QApplication.instance() or create_application(["filepilot-tests"])
    _APP_REFS.append(app)
    yield app  # type: ignore[misc]
    # 显式收敛 Qt 对象：确保控件先于 QApplication 释放
    app.closeAllWindows()
    app.processEvents()
    for widget in list(QApplication.topLevelWidgets()):
        widget.close()
        widget.deleteLater()
    app.processEvents()
    app.setStyleSheet("")
    gc.collect()


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离的运行时数据目录。"""
    target = tmp_path / "filepilot-data"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FILEPILOT_HOME", str(target))
    return target


@pytest.fixture()
def database(data_dir: Path):  # type: ignore[no-untyped-def]
    """已初始化的数据库实例。"""
    from app.core.common.paths import database_path
    from app.core.storage.database import Database

    db = Database(database_path())
    db.initialize()
    yield db
    db.dispose()


@pytest.fixture()
def repositories(database):  # type: ignore[no-untyped-def]
    """常用仓储集合。"""
    from app.core.storage.repositories import (
        DownloadRepository,
        HistoryRepository,
        RuleRepository,
        SettingsRepository,
    )

    return {
        "settings": SettingsRepository(database),
        "downloads": DownloadRepository(database),
        "history": HistoryRepository(database),
        "rules": RuleRepository(database),
    }


@pytest.fixture()
def temp_dir() -> Iterator[Path]:
    """临时目录（自动清理）。"""
    with tempfile.TemporaryDirectory(prefix="filepilot-test-") as directory:
        yield Path(directory)
