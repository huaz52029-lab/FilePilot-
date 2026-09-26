"""文件移动测试：同盘重命名 与 跨盘 复制 → 校验 → 删除。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from app.core.common.cancellation import CancelledError
from app.core.common.exceptions import FileOperationError
from app.core.files import mover
from app.core.files.mover import move_file, same_volume, volume_key

#: 工作区所在的盘（本项目位于 E:），用于制造真实的跨盘场景
WORKSPACE_DRIVE_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def source_drive_dir() -> Path:
    """在**工作区所在磁盘**上准备一个临时目录（真实跨盘测试的源盘）。"""
    base = WORKSPACE_DRIVE_ROOT / "build" / "pytest-cross-volume"
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    yield base
    shutil.rmtree(base, ignore_errors=True)


def _payload(size: int = 2 * 1024 * 1024) -> bytes:
    return bytes((index * 7) % 251 for index in range(size))


def test_volume_detection(source_drive_dir: Path, tmp_path: Path) -> None:
    other_drive = Path(tempfile.gettempdir())
    assert volume_key(source_drive_dir) == volume_key(WORKSPACE_DRIVE_ROOT)
    assert same_volume(source_drive_dir, WORKSPACE_DRIVE_ROOT / "build" / "x.bin") is True
    # tmp_path 位于系统盘（C:），与工作区不同盘
    if volume_key(other_drive) == volume_key(source_drive_dir):  # pragma: no cover - 环境相关
        pytest.skip("当前环境的工作区与临时目录位于同一磁盘，无法测试跨盘场景")
    assert same_volume(source_drive_dir, tmp_path) is False


def test_cross_volume_move_copies_verifies_and_deletes(
    source_drive_dir: Path, tmp_path: Path
) -> None:
    """真实 E: → C: 跨盘移动：必须复制成功后才删除源文件。"""
    if same_volume(source_drive_dir, tmp_path):  # pragma: no cover - 环境相关
        pytest.skip("当前环境无法构造跨盘场景")
    data = _payload()
    source = source_drive_dir / "跨盘测试.bin"
    source.write_bytes(data)
    progress: list[tuple[int, int]] = []

    result = move_file(source, tmp_path, on_progress=lambda done, total: progress.append((done, total)))

    assert result.method == "copy", "跨盘必须走复制流程，而不是直接 rename"
    assert result.cross_volume is True
    assert result.bytes_moved == len(data)
    assert result.target.exists()
    assert result.target.read_bytes() == data
    assert result.target.stat().st_size == len(data)
    assert not source.exists(), "复制并校验成功后源文件应被删除"
    assert progress and progress[-1][0] == len(data)


def test_cross_volume_move_keeps_source_when_verification_fails(
    source_drive_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if same_volume(source_drive_dir, tmp_path):  # pragma: no cover
        pytest.skip("当前环境无法构造跨盘场景")
    source = source_drive_dir / "verify.bin"
    source.write_bytes(_payload(256 * 1024))

    calls = {"count": 0}

    def fake_sha256(path: Path | str, **_kwargs: object) -> str:
        calls["count"] += 1
        return "a" * 64 if calls["count"] == 1 else "b" * 64

    monkeypatch.setattr(mover, "compute_sha256", fake_sha256)

    with pytest.raises(FileOperationError):
        move_file(source, tmp_path, verify_hash=True)

    assert source.exists(), "校验失败时源文件必须保留"
    assert list(tmp_path.iterdir()) == [], "不完整的目标文件应被清理"


def test_cross_volume_move_avoids_overwriting(
    source_drive_dir: Path, tmp_path: Path
) -> None:
    if same_volume(source_drive_dir, tmp_path):  # pragma: no cover
        pytest.skip("当前环境无法构造跨盘场景")
    source = source_drive_dir / "dup.bin"
    source.write_bytes(b"new-content")
    (tmp_path / "dup.bin").write_bytes(b"existing-content")

    result = move_file(source, tmp_path)

    assert result.target.name == "dup (1).bin"
    assert (tmp_path / "dup.bin").read_bytes() == b"existing-content"
    assert result.target.read_bytes() == b"new-content"
    assert not source.exists()


def test_cross_volume_move_cancellation_keeps_source(
    source_drive_dir: Path, tmp_path: Path
) -> None:
    if same_volume(source_drive_dir, tmp_path):  # pragma: no cover
        pytest.skip("当前环境无法构造跨盘场景")
    source = source_drive_dir / "cancel.bin"
    source.write_bytes(_payload(4 * 1024 * 1024))

    with pytest.raises(CancelledError):
        move_file(source, tmp_path, should_cancel=lambda: True)

    assert source.exists(), "取消移动后源文件必须保留"
    assert list(tmp_path.iterdir()) == []


def test_same_volume_move_uses_rename(source_drive_dir: Path) -> None:
    """同盘移动必须使用重命名（不复制数据）。"""
    source = source_drive_dir / "same.bin"
    data = _payload(512 * 1024)
    source.write_bytes(data)
    target_dir = source_drive_dir / "target"

    result = move_file(source, target_dir)

    assert result.method == "rename"
    assert result.cross_volume is False
    assert result.target.read_bytes() == data
    assert not source.exists()


def test_move_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileOperationError):
        move_file(tmp_path / "ghost.bin", tmp_path)
