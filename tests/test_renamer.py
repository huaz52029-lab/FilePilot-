"""批量重命名测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.files.renamer import (
    RenameMode,
    RenameStatus,
    SortKey,
    build_rename_plan,
    execute_rename_plan,
    natural_key,
    split_extension,
)


def make_files(root: Path, names: list[str]) -> list[Path]:
    created: list[Path] = []
    for index, name in enumerate(names):
        path = root / name
        path.write_bytes(f"file-{name}".encode() * (index + 1))
        created.append(path)
    return created


def test_split_extension_keeps_compound_suffix() -> None:
    assert split_extension("photo.jpg") == ("photo", ".jpg")
    assert split_extension("archive.tar.gz") == ("archive", ".tar.gz")
    assert split_extension("noext") == ("noext", "")
    assert split_extension(".env") == (".env", "")


def test_natural_sort_orders_numbers() -> None:
    names = ["IMG_10.jpg", "IMG_2.jpg", "IMG_1.jpg"]
    assert sorted(names, key=natural_key) == ["IMG_1.jpg", "IMG_2.jpg", "IMG_10.jpg"]


def test_plan_prefix_numbering_with_ten_jpgs(tmp_path: Path) -> None:
    make_files(tmp_path, [f"IMG_{index:03d}.jpg" for index in range(1, 11)])
    plan = build_rename_plan(tmp_path, mode=RenameMode.PREFIX_NUMBER, prefix="照片", start=1, padding=4)
    assert plan.scanned_files == 10
    assert plan.ready_count == 10
    assert plan.conflict_count == 0
    assert [action.target.name for action in plan.actions] == [
        f"照片{index:04d}.jpg" for index in range(1, 11)
    ]


def test_plan_number_only_keeps_extension(tmp_path: Path) -> None:
    make_files(tmp_path, ["a.jpg", "b.png", "c.webp"])
    plan = build_rename_plan(tmp_path, mode=RenameMode.NUMBER_ONLY, start=1, padding=4)
    assert [action.target.name for action in plan.actions] == ["0001.jpg", "0002.png", "0003.webp"]


def test_plan_padding_variants(tmp_path: Path) -> None:
    make_files(tmp_path, ["a.jpg", "b.jpg"])
    plan4 = build_rename_plan(tmp_path, mode=RenameMode.NUMBER_ONLY, padding=4)
    assert plan4.actions[0].target.name == "0001.jpg"
    plan5 = build_rename_plan(tmp_path, mode=RenameMode.NUMBER_ONLY, padding=5)
    assert plan5.actions[0].target.name == "00001.jpg"


def test_plan_custom_prefix_and_start(tmp_path: Path) -> None:
    make_files(tmp_path, ["a.jpg", "b.jpg"])
    plan = build_rename_plan(
        tmp_path, mode=RenameMode.PREFIX_NUMBER, prefix="课程-", start=7, padding=3
    )
    assert [action.target.name for action in plan.actions] == ["课程-007.jpg", "课程-008.jpg"]


def test_plan_sanitizes_invalid_prefix(tmp_path: Path) -> None:
    make_files(tmp_path, ["a.jpg"])
    plan = build_rename_plan(tmp_path, mode=RenameMode.PREFIX_NUMBER, prefix='照片<>:"/\\|?*')
    assert plan.prefix == "照片"
    assert plan.actions[0].target.name == "照片0001.jpg"


def test_plan_detects_conflict_and_blocks_execution(tmp_path: Path) -> None:
    make_files(tmp_path, ["a.jpg", "b.jpg"])
    (tmp_path / "照片0001.jpg").write_bytes(b"existing")
    plan = build_rename_plan(tmp_path, mode=RenameMode.PREFIX_NUMBER, prefix="照片", padding=4)
    conflict = next(action for action in plan.actions if action.status is RenameStatus.CONFLICT)
    assert conflict.source.name == "a.jpg"
    assert plan.conflict_count == 1
    assert plan.can_execute is False


def test_plan_marks_unchanged_files(tmp_path: Path) -> None:
    make_files(tmp_path, ["0001.jpg", "0002.jpg"])
    plan = build_rename_plan(tmp_path, mode=RenameMode.NUMBER_ONLY, padding=4)
    assert plan.unchanged_count == 2
    assert plan.ready_count == 0
    assert plan.can_execute is False


def test_plan_filters_by_extension(tmp_path: Path) -> None:
    make_files(tmp_path, ["a.jpg", "b.jpg", "c.png", "d.txt"])
    plan = build_rename_plan(
        tmp_path, mode=RenameMode.PREFIX_NUMBER, prefix="图", extensions=["jpg"]
    )
    assert plan.scanned_files == 2
    assert {action.source.name for action in plan.actions} == {"a.jpg", "b.jpg"}


def test_sort_by_size_and_modified(tmp_path: Path) -> None:
    small = tmp_path / "small.bin"
    large = tmp_path / "large.bin"
    small.write_bytes(b"x")
    large.write_bytes(b"y" * 1000)
    import os
    import time

    old = time.time() - 10_000
    os.utime(large, (old, old))

    by_size = build_rename_plan(tmp_path, mode=RenameMode.NUMBER_ONLY, sort_key=SortKey.SIZE)
    assert [action.source.name for action in by_size.actions] == ["small.bin", "large.bin"]

    by_modified = build_rename_plan(
        tmp_path, mode=RenameMode.NUMBER_ONLY, sort_key=SortKey.MODIFIED
    )
    assert [action.source.name for action in by_modified.actions] == ["large.bin", "small.bin"]

    by_created = build_rename_plan(tmp_path, mode=RenameMode.NUMBER_ONLY, sort_key=SortKey.CREATED)
    assert {action.source.name for action in by_created.actions} == {"small.bin", "large.bin"}


def test_execute_rename_plan_renames_files(tmp_path: Path) -> None:
    make_files(tmp_path, [f"IMG_{index:03d}.jpg" for index in range(1, 11)])
    plan = build_rename_plan(tmp_path, mode=RenameMode.PREFIX_NUMBER, prefix="照片", padding=4)
    result = execute_rename_plan(plan)
    assert result.renamed == 10
    assert result.failed == 0
    assert result.success is True
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        f"照片{index:04d}.jpg" for index in range(1, 11)
    ]
    # 内容不能被改变
    assert (tmp_path / "照片0001.jpg").read_bytes() == b"file-IMG_001.jpg"


def test_execute_rename_plan_handles_name_swap(tmp_path: Path) -> None:
    """目标名与批次内其它源文件同名时必须安全完成（两阶段重命名）。"""
    (tmp_path / "A.txt").write_bytes(b"AAA")
    (tmp_path / "B.txt").write_bytes(b"BBB")
    plan = build_rename_plan(
        tmp_path, mode=RenameMode.NUMBER_ONLY, start=1, padding=1
    )
    # 自定义：把 A→B、B→A 形成互为目标的情况
    plan.actions[0].target = tmp_path / "B.txt"
    plan.actions[1].target = tmp_path / "A.txt"
    result = execute_rename_plan(plan)
    assert result.failed == 0
    assert (tmp_path / "A.txt").read_bytes() == b"BBB"
    assert (tmp_path / "B.txt").read_bytes() == b"AAA"


def test_execute_reports_failure_without_stopping(tmp_path: Path) -> None:
    """中途失败不应中断整体流程，且不会覆盖已有文件。"""
    make_files(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    plan = build_rename_plan(tmp_path, mode=RenameMode.PREFIX_NUMBER, prefix="照片", padding=4)
    # 执行前突然出现同名文件 → 该文件失败，其余继续
    (tmp_path / "照片0002.jpg").write_bytes(b"appeared")
    result = execute_rename_plan(plan)
    assert result.renamed == 2
    assert result.failed == 1
    assert result.errors and result.errors[0][0].name == "b.jpg"
    assert (tmp_path / "照片0002.jpg").read_bytes() == b"appeared"
    assert (tmp_path / "照片0001.jpg").exists()
    assert (tmp_path / "照片0003.jpg").exists()
    assert (tmp_path / "b.jpg").exists()  # 失败文件保持原名


def test_execute_can_be_cancelled(tmp_path: Path) -> None:
    make_files(tmp_path, [f"f{index}.jpg" for index in range(6)])
    plan = build_rename_plan(tmp_path, mode=RenameMode.NUMBER_ONLY, padding=4)
    calls = {"count": 0}

    def should_cancel() -> bool:
        calls["count"] += 1
        return calls["count"] > 3

    result = execute_rename_plan(plan, should_cancel=should_cancel)
    assert result.cancelled is True
    assert 0 < result.renamed < 6


def test_plan_rejects_missing_folder(tmp_path: Path) -> None:
    from app.core.common.exceptions import FileOperationError

    with pytest.raises(FileOperationError):
        build_rename_plan(tmp_path / "nope")


def test_plan_skips_hidden_and_temp_files(tmp_path: Path) -> None:
    make_files(tmp_path, ["a.jpg", ".hidden.jpg", ".filepilot-renaming-abc.jpg"])
    plan = build_rename_plan(tmp_path, mode=RenameMode.NUMBER_ONLY)
    assert plan.scanned_files == 1
    assert plan.actions[0].source.name == "a.jpg"
