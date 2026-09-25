"""文件整理与自动归档测试。"""

from __future__ import annotations

from pathlib import Path

from app.core.files.categories import FileCategory
from app.core.files.models import MatchField, OrganizeActionStatus
from app.core.files.organizer import (
    archive_download,
    execute_plan,
    plan_by_category,
    plan_by_rules,
)
from app.core.files.rules import OrganizeRule


def build_messy_folder(root: Path) -> None:
    (root / "photo.jpg").write_bytes(b"j" * 100)
    (root / "clip.mp4").write_bytes(b"v" * 200)
    (root / "notes.pdf").write_bytes(b"p" * 50)
    (root / "song.mp3").write_bytes(b"a" * 30)
    (root / "archive.zip").write_bytes(b"z" * 10)
    (root / "script.py").write_bytes(b"c" * 5)
    (root / "unknown.dat").write_bytes(b"x" * 7)


def test_plan_by_category_creates_targets(tmp_path: Path) -> None:
    build_messy_folder(tmp_path)
    plan = plan_by_category(tmp_path)
    assert plan.total == 7
    assert plan.move_count == 7
    assert plan.conflict_count == 0
    targets = {action.target.parent.name for action in plan.actions}
    assert targets == {"图片", "视频", "文档", "音频", "压缩包", "代码", "其他"}
    assert all(action.status is OrganizeActionStatus.READY for action in plan.actions)


def test_plan_respects_selected_categories(tmp_path: Path) -> None:
    build_messy_folder(tmp_path)
    plan = plan_by_category(tmp_path, categories=[FileCategory.IMAGE, FileCategory.VIDEO])
    assert plan.move_count == 2
    assert plan.skipped_files == 5
    assert {action.category for action in plan.actions} == {FileCategory.IMAGE, FileCategory.VIDEO}


def test_plan_detects_conflicts(tmp_path: Path) -> None:
    build_messy_folder(tmp_path)
    (tmp_path / "文档").mkdir()
    (tmp_path / "文档" / "notes.pdf").write_bytes(b"existing")
    plan = plan_by_category(tmp_path)
    conflict = next(action for action in plan.actions if action.source.name == "notes.pdf")
    assert conflict.status is OrganizeActionStatus.CONFLICT
    assert plan.conflict_count == 1
    # 冲突文件在规划阶段不会被标记为可移动
    assert conflict not in [
        action for action in plan.actions if action.status is OrganizeActionStatus.READY
    ]


def test_plan_skips_files_already_in_place(tmp_path: Path) -> None:
    (tmp_path / "图片").mkdir()
    (tmp_path / "图片" / "photo.jpg").write_bytes(b"j" * 10)
    plan = plan_by_category(tmp_path)
    assert plan.total == 0
    assert plan.skipped_files == 1


def test_execute_plan_moves_files_without_overwriting(tmp_path: Path) -> None:
    build_messy_folder(tmp_path)
    (tmp_path / "文档").mkdir()
    (tmp_path / "文档" / "notes.pdf").write_bytes(b"existing")
    plan = plan_by_category(tmp_path)
    result = execute_plan(plan)

    assert result.moved == 6
    assert result.skipped == 1
    assert result.failed == 0
    assert result.success is True
    assert (tmp_path / "图片" / "photo.jpg").exists()
    assert (tmp_path / "视频" / "clip.mp4").exists()
    assert (tmp_path / "文档" / "notes.pdf").read_bytes() == b"existing"
    assert (tmp_path / "课程").exists() is False
    # 原始位置已被清空
    assert not (tmp_path / "photo.jpg").exists()
    assert not (tmp_path / "clip.mp4").exists()


def test_plan_by_rules_uses_priority(tmp_path: Path) -> None:
    build_messy_folder(tmp_path)
    (tmp_path / "学习").mkdir()
    rules = [
        OrganizeRule(
            name="课程 PDF",
            field=MatchField.EXTENSION,
            value="pdf",
            target_dir=tmp_path / "学习" / "PDF",
            priority=10,
        ),
        OrganizeRule(
            name="截图",
            field=MatchField.NAME_CONTAINS,
            value="photo",
            target_dir=tmp_path / "图片存档",
            priority=20,
        ),
        OrganizeRule(
            name="视频",
            field=MatchField.CATEGORY,
            value="video",
            target_dir=tmp_path / "媒体" / "视频",
            priority=30,
        ),
    ]
    plan = plan_by_rules(tmp_path, rules)
    mapping = {action.source.name: action for action in plan.actions}
    assert set(mapping) == {"notes.pdf", "photo.jpg", "clip.mp4"}
    assert mapping["notes.pdf"].target.parent == tmp_path / "学习" / "PDF"
    assert mapping["photo.jpg"].rule_name == "截图"
    assert mapping["clip.mp4"].target.parent == tmp_path / "媒体" / "视频"


def test_disabled_rule_is_ignored(tmp_path: Path) -> None:
    build_messy_folder(tmp_path)
    rules = [
        OrganizeRule(
            name="停用规则",
            field=MatchField.EXTENSION,
            value="pdf",
            target_dir=tmp_path / "PDF",
            enabled=False,
        )
    ]
    plan = plan_by_rules(tmp_path, rules)
    assert plan.total == 0
    assert plan.skipped_files == 7


def test_execute_plan_can_be_cancelled(tmp_path: Path) -> None:
    build_messy_folder(tmp_path)
    plan = plan_by_category(tmp_path)
    calls = {"count": 0}

    def should_cancel() -> bool:
        calls["count"] += 1
        return calls["count"] > 2

    result = execute_plan(plan, should_cancel=should_cancel)
    assert result.moved < plan.total
    assert result.errors


def test_archive_download_by_category(tmp_path: Path) -> None:
    download = tmp_path / "setup.exe"
    download.write_bytes(b"binary")
    moved = archive_download(download, mode="category", archive_root=tmp_path)
    assert moved is not None
    assert moved.parent.name == "程序"
    assert moved.read_bytes() == b"binary"
    assert not download.exists()


def test_archive_download_with_rules_and_off(tmp_path: Path) -> None:
    download = tmp_path / "course.pdf"
    download.write_bytes(b"%PDF")
    rule = OrganizeRule(
        name="课程",
        field=MatchField.EXTENSION,
        value="pdf",
        target_dir=tmp_path / "学习资料",
    )
    moved = archive_download(download, mode="rules", rules=[rule])
    assert moved is not None
    assert moved.parent == tmp_path / "学习资料"

    other = tmp_path / "movie.mp4"
    other.write_bytes(b"video")
    assert archive_download(other, mode="off") is None
    assert other.exists()
    # 规则未命中时保持原位置
    assert archive_download(other, mode="rules", rules=[rule]) is None
    assert other.exists()
