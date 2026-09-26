"""重复文件检测测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

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

    result = move_to_duplicate_folder(duplicates, target_dir=target_root)
    assert result.moved == 2
    assert result.failed == 0
    assert result.errors == []
    assert result.success is True
    assert keep.exists()
    assert keep.read_bytes() == b"same-image-data"
    for path in duplicates:
        assert not path.exists()
    moved_files = list(target_root.rglob("*.jpg"))
    assert len(moved_files) == 2
    assert {item.read_bytes() for item in moved_files} == {b"same-image-data"}


def test_move_to_duplicate_folder_reports_errors(tmp_path: Path) -> None:
    missing = tmp_path / "ghost.jpg"
    result = move_to_duplicate_folder([missing], target_dir=tmp_path / "重复文件")
    assert result.moved == 0
    assert result.skipped == 1
    assert result.errors and "不存在" in result.errors[0][1]


def test_move_all_duplicates_same_disk_with_progress(tmp_path: Path) -> None:
    """“全部移动”：同盘场景 + 进度回调 + 每组保留第一份。"""
    build_duplicate_tree(tmp_path)
    scan = find_duplicates([tmp_path])
    duplicate_files = [path for group in scan.groups for path in group.files[1:]]
    assert len(duplicate_files) == 2

    events: list[tuple[int, int]] = []
    result = move_to_duplicate_folder(
        duplicate_files,
        target_dir=duplicate_folder(tmp_path),
        on_progress=lambda index, total, _path: events.append((index, total)),
    )

    assert result.moved == 2
    assert result.failed == 0
    assert result.cross_volume == 0  # 同盘使用重命名
    assert result.success is True
    assert events and events[-1] == (2, 2)
    kept = scan.groups[0].files[0]
    assert kept.exists()
    assert all(not path.exists() for path in duplicate_files)


def test_move_all_duplicates_cross_disk(tmp_path: Path) -> None:
    """跨盘“全部移动”：必须走 复制 → 校验 → 删除，且源文件成功后才删除。"""
    from app.core.files.mover import same_volume

    source_root = Path(__file__).resolve().parents[1] / "build" / "pytest-cross-dup"
    if source_root.exists():
        import shutil

        shutil.rmtree(source_root, ignore_errors=True)
    (source_root / "子目录").mkdir(parents=True, exist_ok=True)
    try:
        if same_volume(source_root, tmp_path):  # pragma: no cover - 环境相关
            pytest.skip("当前环境无法构造跨盘场景")
        payload = b"duplicate-cross-disk" * 4096
        first = source_root / "子目录" / "a_copy1.bin"
        second = source_root / "子目录" / "a_copy2.bin"
        first.write_bytes(payload)
        second.write_bytes(payload)

        target_root = duplicate_folder(tmp_path)
        result = move_to_duplicate_folder([second], target_dir=target_root)

        assert result.moved == 1
        assert result.failed == 0
        assert result.cross_volume == 1
        assert first.exists(), "保留的文件必须存在"
        assert not second.exists(), "跨盘复制并校验成功后应删除源文件"
        moved_files = list(target_root.rglob("*.bin"))
        assert len(moved_files) == 1
        assert moved_files[0].read_bytes() == payload
    finally:
        import shutil

        shutil.rmtree(source_root, ignore_errors=True)
