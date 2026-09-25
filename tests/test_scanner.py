"""目录扫描与空间统计测试。"""

from __future__ import annotations

from pathlib import Path

from app.core.common.exceptions import FileScanError, InsufficientDiskSpaceError
from app.core.files.analyzer import analyze_directory
from app.core.files.categories import FileCategory, categorize_path
from app.core.files.scanner import (
    directory_usage,
    disk_usage_info,
    ensure_disk_space,
    quick_directory_stats,
    scan_directory,
)


def build_tree(root: Path) -> Path:
    """构造测试目录树：

    root/
      a.txt (10)
      movie.mp4 (100)
      sub/
        b.pdf (20)
        deep/
          c.py (5)
      ignored/__pycache__/x.pyc (999, 应被忽略)
    """
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "a.txt").write_text("x" * 10, encoding="utf-8")
    (root / "movie.mp4").write_bytes(b"m" * 100)
    (root / "sub" / "b.pdf").write_bytes(b"p" * 20)
    (root / "sub" / "deep" / "c.py").write_bytes(b"c" * 5)
    ignored = root / "__pycache__"
    ignored.mkdir(exist_ok=True)
    (ignored / "x.pyc").write_bytes(b"z" * 999)
    return root


def test_scan_directory_counts(temp_dir: Path) -> None:
    build_tree(temp_dir)
    entries, stats = scan_directory(temp_dir)
    assert stats.file_count == 4
    assert stats.total_size == 135
    assert stats.cancelled is False
    assert stats.directory_count >= 3
    assert {entry.name for entry in entries} == {"a.txt", "movie.mp4", "b.pdf", "c.py"}
    assert stats.category_sizes[FileCategory.VIDEO] == 100
    # a.txt 与 b.pdf 同属“文档”分类
    assert stats.category_sizes[FileCategory.DOCUMENT] == 30


def test_scan_directory_max_depth(temp_dir: Path) -> None:
    build_tree(temp_dir)
    entries, _stats = scan_directory(temp_dir, max_depth=1)
    names = {entry.name for entry in entries}
    assert "a.txt" in names
    assert "c.py" not in names


def test_scan_directory_cancellation(temp_dir: Path) -> None:
    build_tree(temp_dir)
    tokens = {"count": 0}

    def should_cancel() -> bool:
        tokens["count"] += 1
        return tokens["count"] > 1

    _entries, stats = scan_directory(temp_dir, should_cancel=should_cancel)
    assert stats.cancelled is True
    assert stats.file_count < 4


def test_quick_stats_and_usage(temp_dir: Path) -> None:
    build_tree(temp_dir)
    stats = quick_directory_stats(temp_dir)
    assert stats.file_count == 4
    assert stats.total_size == 135

    usage = directory_usage(temp_dir, max_depth=1)
    # 空间统计必须覆盖磁盘上的全部内容（包含 __pycache__）
    assert usage.file_count == 5
    assert usage.total_size == 1134
    children = {child.name: child.total_size for child in usage.children}
    assert children == {"sub": 25, "__pycache__": 999}


def test_analyze_directory(temp_dir: Path) -> None:
    build_tree(temp_dir)
    progress: list[object] = []
    analysis = analyze_directory(temp_dir, largest_limit=2, report=progress.append)
    assert analysis.file_count == 5
    assert analysis.total_size == 1134
    assert [entry.name for entry in analysis.largest_files] == ["x.pyc", "movie.mp4"]
    breakdown = analysis.category_breakdown()
    # .pyc 未归类（other，999 字节）体积最大；movie.mp4 属于视频分类
    assert breakdown[0][0] is FileCategory.OTHER
    sizes = {category: size for category, size, _share in breakdown}
    assert sizes[FileCategory.VIDEO] == 100
    assert analysis.disk.total > 0
    assert analysis.child_rows()[0].name == "__pycache__"
    # sub 目录含 b.pdf 与更深层的 c.py：递归统计必须覆盖全部子目录
    by_count = analysis.child_rows(by="count")
    assert by_count[0].name == "sub"
    assert by_count[0].file_count == 2
    assert by_count[0].total_size == 25
    assert by_count[0].dir_count == 1
    assert analysis.duration >= 0


def test_analyze_directory_errors(temp_dir: Path) -> None:
    missing = temp_dir / "nope"
    try:
        analyze_directory(missing)
    except FileScanError as exc:
        assert "不存在" in exc.user_message
    else:  # pragma: no cover - 必须抛出异常
        raise AssertionError("应当抛出 FileScanError")

    file_path = temp_dir / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    try:
        analyze_directory(file_path)
    except FileScanError as exc:
        assert "文件夹" in exc.user_message
    else:  # pragma: no cover
        raise AssertionError("应当抛出 FileScanError")


def test_disk_usage_and_space_check(temp_dir: Path) -> None:
    info = disk_usage_info(temp_dir)
    assert info.total > 0
    assert info.free > 0
    assert 0 <= info.percent_used <= 100

    ensure_disk_space(temp_dir, 1024)
    try:
        ensure_disk_space(temp_dir, info.free * 10)
    except InsufficientDiskSpaceError as exc:
        assert exc.required > exc.available
    else:  # pragma: no cover
        raise AssertionError("应当抛出空间不足异常")


def test_categorize_path() -> None:
    assert categorize_path("a.JPG") is FileCategory.IMAGE
    assert categorize_path("movie.mkv") is FileCategory.VIDEO
    assert categorize_path("doc.pdf") is FileCategory.DOCUMENT
    assert categorize_path("song.flac") is FileCategory.AUDIO
    assert categorize_path("pkg.zip") is FileCategory.ARCHIVE
    assert categorize_path("setup.exe") is FileCategory.PROGRAM
    assert categorize_path("main.py") is FileCategory.CODE
    assert categorize_path("Dockerfile") is FileCategory.CODE
    assert categorize_path("unknown.xyz") is FileCategory.OTHER
