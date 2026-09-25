"""重复文件检测测试。"""

from __future__ import annotations

from pathlib import Path

from app.core.files.duplicate import (
    duplicate_folder,
    find_duplicates,
    move_to_duplicate_folder,
)


def build_duplicate_tree(root: Path) -> None:
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir(parents=True)
    (root / "a" / "photo1.jpg").write_bytes(b"same-image-data")
    (root / "b" / "photo1_copy.jpg").write_bytes(b"same-image-data")
    (root / "b" / "photo2.jpg").write_bytes(b"same-image-data")
    (root / "a" / "unique.txt").write_bytes(b"totally different")
    # 大小相同但内容不同：不应被判定为重复
    (root / "b" / "fake1.bin").write_bytes(b"x" * 16)
    (root / "b" / "fake2.bin").write_bytes(b"y" * 16)
    (root / "a" / "tiny.txt").write_bytes(b"")  # 空文件默认忽略


def test_find_duplicates_groups_by_hash(tmp_path: Path) -> None:
    build_duplicate_tree(tmp_path)
    result = find_duplicates([tmp_path])
    assert result.group_count == 1
    group = result.groups[0]
    assert group.count == 3
    assert group.size == len(b"same-image-data")
    assert group.wasted_bytes == group.size * 2
    assert {path.name for path in group.files} == {
        "photo1.jpg",
        "photo1_copy.jpg",
        "photo2.jpg",
    }
    assert result.duplicate_file_count == 2
    assert result.scanned_files >= 6
    assert result.hashed_files >= 3
    assert result.cancelled is False


def test_find_duplicates_reports_progress(tmp_path: Path) -> None:
    build_duplicate_tree(tmp_path)
    messages: list[str] = []
    find_duplicates([tmp_path], on_progress=messages.append)
    assert messages
    assert "哈希" in messages[0] or "扫描" in messages[0]


def test_find_duplicates_can_be_cancelled(tmp_path: Path) -> None:
    build_duplicate_tree(tmp_path)
    result = find_duplicates([tmp_path], should_cancel=lambda: True)
    assert result.group_count == 0


def test_find_duplicates_handles_missing_root(tmp_path: Path) -> None:
    result = find_duplicates([tmp_path / "nope"])
    assert result.group_count == 0
    assert result.scanned_files == 0


def test_move_to_duplicate_folder_keeps_originals_safe(tmp_path: Path) -> None:
    build_duplicate_tree(tmp_path)
    result = find_duplicates([tmp_path])
    group = result.groups[0]
    keep, *duplicates = group.files
    target_root = duplicate_folder(tmp_path)

    moved, errors = move_to_duplicate_folder(duplicates, target_dir=target_root)
    assert moved == 2
    assert errors == []
    assert keep.exists()
    assert keep.read_bytes() == b"same-image-data"
    for path in duplicates:
        assert not path.exists()
    moved_files = list(target_root.rglob("*.jpg"))
    assert len(moved_files) == 2
    assert {item.read_bytes() for item in moved_files} == {b"same-image-data"}


def test_move_to_duplicate_folder_reports_errors(tmp_path: Path) -> None:
    missing = tmp_path / "ghost.jpg"
    moved, errors = move_to_duplicate_folder([missing], target_dir=tmp_path / "重复文件")
    assert moved == 0
    assert errors and "不存在" in errors[0]
