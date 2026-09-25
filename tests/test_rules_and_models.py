"""整理规则与领域模型测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.download.models import (
    DownloadMode,
    DownloadSegment,
    DownloadStatus,
    DownloadTask,
    ProbeResult,
    SegmentState,
)
from app.core.download.speed import SpeedMeter
from app.core.files.models import FileEntry, MatchField
from app.core.files.rules import OrganizeRule


def make_entry(name: str, size: int = 100, extension: str | None = None) -> FileEntry:
    path = Path(f"D:/Downloads/{name}")
    from app.core.files.categories import categorize_path

    return FileEntry(path=path, size=size, mtime=0.0, category=categorize_path(path))


@pytest.mark.parametrize(
    ("field", "value", "entry", "expected"),
    [
        (MatchField.EXTENSION, "pdf", make_entry("a.pdf"), True),
        (MatchField.EXTENSION, "pdf, docx", make_entry("a.docx"), True),
        (MatchField.EXTENSION, "pdf", make_entry("a.txt"), False),
        (MatchField.NAME_CONTAINS, "screenshot", make_entry("Screenshot_1.png"), True),
        (MatchField.NAME_PREFIX, "IMG_", make_entry("IMG_2.jpg"), True),
        (MatchField.NAME_SUFFIX, "_v2.zip", make_entry("pkg_v2.zip"), True),
        (MatchField.SIZE_GREATER, "500MB", make_entry("big.iso", 600 * 1024**2), True),
        (MatchField.SIZE_GREATER, "500MB", make_entry("small.iso", 100), False),
        (MatchField.CATEGORY, "video", make_entry("clip.mp4"), True),
        (MatchField.CATEGORY, "image,video", make_entry("clip.mp4"), True),
        (MatchField.CATEGORY, "video", make_entry("doc.pdf"), False),
    ],
)
def test_rule_matching(field, value, entry, expected) -> None:  # type: ignore[no-untyped-def]
    rule = OrganizeRule(name="t", field=field, value=value, target_dir=Path("D:/target"))
    assert rule.matches(entry) is expected


def test_rule_validation_and_state(tmp_path: Path) -> None:
    rule = OrganizeRule(
        name="", field=MatchField.EXTENSION, value="pdf", target_dir=tmp_path
    )
    assert rule.validate() == "规则名称不能为空。"
    rule.name = "课程"
    rule.value = ""
    assert rule.validate() == "匹配条件不能为空。"
    rule.field = MatchField.SIZE_GREATER
    rule.value = "五百兆"
    assert rule.validate() == "大小条件格式不正确，例如：500MB。"
    rule.value = "500MB"
    assert rule.validate() is None
    rule.field = MatchField.CATEGORY
    rule.value = "unknown-category"
    assert rule.validate() is not None
    rule.value = "image"
    assert rule.validate() is None
    assert "扩展名" not in rule.condition_text  # 当前为分类条件
    assert rule.target_text == str(tmp_path)

    disabled = OrganizeRule(
        name="d", field=MatchField.EXTENSION, value="pdf", target_dir=tmp_path, enabled=False
    )
    assert disabled.matches(make_entry("a.pdf")) is False


def test_segment_split_covers_whole_file() -> None:
    segments = DownloadSegment.split(1_000, 3)
    assert len(segments) == 3
    assert segments[0].start == 0
    assert segments[-1].end == 999
    assert sum(segment.length for segment in segments) == 1_000
    for previous, current in zip(segments, segments[1:], strict=False):
        assert current.start == previous.end + 1

    assert len(DownloadSegment.split(5, 10)) == 5
    assert DownloadSegment.split(0, 4) == []
    assert DownloadSegment.split(100, 0) == []


def test_segment_progress_helpers() -> None:
    segment = DownloadSegment(index=0, start=100, end=199, current=100)
    assert segment.length == 100
    assert segment.written == 0
    assert segment.remaining == 100
    assert not segment.is_complete
    segment.current = 150
    assert segment.written == 50
    segment.mark_complete()
    assert segment.is_complete
    assert segment.state is SegmentState.COMPLETED
    assert segment.remaining == 0


def test_download_task_progress_and_resume(tmp_path: Path) -> None:
    task = DownloadTask(
        task_id="t1",
        url="https://example.com/a.bin",
        file_name="a.bin",
        save_dir=tmp_path,
        total_size=1_000,
        downloaded=250,
        status=DownloadStatus.DOWNLOADING,
        mode=DownloadMode.SEGMENTED,
        connection_count=4,
    )
    assert task.progress_percent == 25.0
    assert task.remaining_bytes == 750
    assert task.eta_seconds is None  # 速度为 0 时不估算
    task.speed.current = 250.0
    assert task.eta_seconds == 3.0
    task.status = DownloadStatus.COMPLETED
    assert task.eta_seconds == 0.0
    assert task.progress_percent == 25.0
    task.downloaded = 1_000
    assert task.progress_percent == 100.0
    assert task.display_path.endswith("a.bin")
    assert task.normalize_connections(99) == 16
    assert task.normalize_connections(0) == 1


def test_download_task_hash_verification(tmp_path: Path) -> None:
    task = DownloadTask(
        task_id="t2",
        url="https://example.com/a.bin",
        file_name="a.bin",
        save_dir=tmp_path,
    )
    assert task.hash_verified is None
    task.sha256 = "ABC"
    task.expected_sha256 = "abc"
    assert task.hash_verified is True
    task.expected_sha256 = "def"
    assert task.hash_verified is False


def test_probe_result_helpers() -> None:
    probe = ProbeResult(
        url="https://example.com/a.zip",
        final_url="https://cdn.example.com/a.zip",
        file_name="a.zip",
        total_size=1_000,
        content_type="application/zip",
        supports_range=True,
    )
    assert probe.host == "cdn.example.com"
    assert probe.size_known
    assert probe.display_type == "ZIP 压缩包"
    unknown = ProbeResult(
        url="https://example.com/a",
        final_url="",
        file_name="a",
        total_size=0,
    )
    assert not unknown.size_known
    assert unknown.display_type == "二进制文件"


def test_speed_meter_uses_sliding_window() -> None:
    clock = {"now": 0.0}

    def fake_clock() -> float:
        return clock["now"]

    meter = SpeedMeter(window_seconds=5.0, clock=fake_clock)
    meter.reset(0)
    clock["now"] = 1.0
    assert meter.update(1_000) == pytest.approx(1_000.0)
    clock["now"] = 2.0
    assert meter.update(2_000) == pytest.approx(1_000.0)
    clock["now"] = 3.0
    meter.update(9_000)
    # 滑动窗口速率 = (9000-0)/3
    assert meter.current == pytest.approx(3_000.0)
    assert meter.peak >= 3_000.0
    assert meter.average == pytest.approx(3_000.0)
    snapshot = meter.snapshot()
    assert snapshot.current == pytest.approx(3_000.0)
    # 重置后速度归零
    meter.reset(0)
    assert meter.current == 0.0
    # 进度回退（重新下载）会触发重置
    clock["now"] = 4.0
    meter.update(10)
    assert meter.update(5) == 0.0
