"""数据库、迁移与仓储测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.download.models import (
    DownloadMode,
    DownloadSegment,
    DownloadStatus,
    DownloadTask,
    SegmentState,
)
from app.core.files.models import MatchField
from app.core.files.rules import OrganizeRule
from app.core.storage.migrations import LATEST_VERSION
from app.core.storage.models import HistoryKind, HistoryStatus


def test_migrations_create_schema(database) -> None:  # type: ignore[no-untyped-def]
    assert database.version == LATEST_VERSION
    tables = {
        row["name"]
        for row in database.query_all("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "download_tasks",
        "download_segments",
        "history_events",
        "organize_rules",
        "settings",
        "schema_version",
    } <= tables
    # 重复初始化应保持幂等
    assert database.initialize() == LATEST_VERSION


def test_settings_repository(repositories) -> None:  # type: ignore[no-untyped-def]
    repo = repositories["settings"]
    assert repo.get("missing", "fallback") == "fallback"
    repo.set("download.dir", "D:/Downloads")
    assert repo.get("download.dir") == "D:/Downloads"
    repo.set_many({"a": "1", "b": "2"})
    assert repo.get_all()["a"] == "1"
    repo.delete("a")
    assert repo.get("a") is None


def test_download_repository_roundtrip(repositories, tmp_path: Path) -> None:
    repo = repositories["downloads"]
    task = DownloadTask(
        task_id="task-1",
        url="https://example.com/file.zip",
        final_url="https://cdn.example.com/file.zip",
        file_name="file.zip",
        save_dir=tmp_path,
        save_path=tmp_path / "file.zip",
        total_size=4_000,
        downloaded=1_000,
        status=DownloadStatus.DOWNLOADING,
        mode=DownloadMode.SEGMENTED,
        supports_range=True,
        can_resume=True,
        connection_count=4,
        requested_connections=8,
        etag='"abc"',
        last_modified="Wed, 21 Oct 2026 07:28:00 GMT",
        segments=[
            DownloadSegment(index=0, start=0, end=1_999, current=1_000, state=SegmentState.RUNNING),
            DownloadSegment(index=1, start=2_000, end=3_999),
        ],
    )
    repo.save_task(task)

    loaded = repo.get_task("task-1")
    assert loaded is not None
    assert loaded.file_name == "file.zip"
    assert loaded.status is DownloadStatus.DOWNLOADING
    assert loaded.mode is DownloadMode.SEGMENTED
    assert loaded.supports_range is True
    assert len(loaded.segments) == 2
    assert loaded.segments[0].current == 1_000

    repo.update_progress(
        "task-1",
        downloaded=2_500,
        status=DownloadStatus.PAUSED,
        segments=[DownloadSegment(index=0, start=0, end=1_999, current=2_000)],
    )
    updated = repo.get_task("task-1")
    assert updated is not None
    assert updated.status is DownloadStatus.PAUSED
    assert updated.downloaded == 2_500

    assert len(repo.list_unfinished()) == 1
    assert repo.count_by_status()[DownloadStatus.PAUSED] == 1

    repo.delete_task("task-1")
    assert repo.get_task("task-1") is None
    assert repo.list_segments("task-1") == []


def test_download_repository_totals(repositories, tmp_path: Path) -> None:
    repo = repositories["downloads"]
    task = DownloadTask(
        task_id="done-1",
        url="https://example.com/a.bin",
        file_name="a.bin",
        save_dir=tmp_path,
        total_size=1_000,
        status=DownloadStatus.COMPLETED,
        completed_at=1_000_000.0,
    )
    repo.save_task(task)
    assert repo.total_downloaded_since(0) == 1_000
    assert repo.total_downloaded_since(2_000_000.0) == 0
    assert repo.delete_finished() == 1


def test_history_repository(repositories) -> None:
    repo = repositories["history"]
    repo.add(
        kind=HistoryKind.DOWNLOAD,
        action="completed",
        title="Ubuntu.iso",
        detail="下载完成",
        path="D:/Downloads/Ubuntu.iso",
        size=4_700,
    )
    repo.add(
        kind=HistoryKind.ORGANIZE,
        action="moved",
        title="课程资料.pdf",
        status=HistoryStatus.WARNING,
    )
    assert repo.count() == 2
    assert repo.count(kinds=[HistoryKind.DOWNLOAD]) == 1

    downloads = repo.list_events(kinds=[HistoryKind.DOWNLOAD])
    assert downloads[0].title == "Ubuntu.iso"
    assert downloads[0].size == 4_700
    assert downloads[0].kind is HistoryKind.DOWNLOAD

    assert len(repo.list_events(query="课程")) == 1
    assert repo.latest(1)[0].kind is HistoryKind.ORGANIZE

    assert repo.clear(kinds=[HistoryKind.DOWNLOAD]) == 1
    assert repo.count() == 1
    assert repo.clear() == 1
    assert repo.count() == 0


def test_rule_repository(repositories, tmp_path: Path) -> None:
    repo = repositories["rules"]
    rule = OrganizeRule(
        name="课程 PDF",
        field=MatchField.EXTENSION,
        value="pdf",
        target_dir=tmp_path / "学习资料",
    )
    repo.save_rule(rule)
    loaded = repo.list_rules()
    assert len(loaded) == 1
    assert loaded[0].name == "课程 PDF"
    assert loaded[0].field is MatchField.EXTENSION
    repo.set_enabled(rule.rule_id, False)
    assert repo.list_rules(enabled_only=True) == []
    repo.delete_rule(rule.rule_id)
    assert repo.list_rules() == []


def test_database_transaction_rollback(database) -> None:
    with pytest.raises(RuntimeError), database.transaction() as connection:
        connection.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('x', '1', 0)"
        )
        raise RuntimeError("boom")
    assert database.query_one("SELECT * FROM settings WHERE key = 'x'") is None
