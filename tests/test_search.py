"""文件搜索测试（含高级语法解析）。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from app.core.files.categories import FileCategory
from app.core.files.search import parse_query, search_files


def build_tree(root: Path) -> None:
    (root / "docs").mkdir(parents=True)
    (root / "Python教程.pdf").write_bytes(b"p" * 100)
    (root / "movie.mp4").write_bytes(b"v" * 5_000)
    (root / "script.py").write_bytes(b"c" * 20)
    (root / "docs" / "python_notes.md").write_bytes(b"n" * 300)


def test_parse_query_advanced_syntax() -> None:
    query = parse_query("size > 500MB")
    # `>` 为严格大于：内部用 “阈值 + 1 字节” 表示
    assert query.min_size == 500 * 1024**2 + 1
    assert query.text == ""

    query = parse_query("size >= 500MB")
    assert query.min_size == 500 * 1024**2

    query = parse_query("type = video")
    assert query.categories == (FileCategory.VIDEO,)

    query = parse_query("ext = pdf, docx")
    assert query.extensions == ("pdf", "docx")

    query = parse_query("modified < 7d")
    assert query.modified_after is not None
    assert query.modified_after > time.time() - 8 * 86400

    query = parse_query("modified > 30d")
    assert query.modified_before is not None

    query = parse_query("Python")
    assert query.text == "Python"
    assert query.min_size is None
    assert not query.is_empty

    assert parse_query("").is_empty


def test_search_by_keyword_and_extension(tmp_path: Path) -> None:
    build_tree(tmp_path)
    results = search_files([tmp_path], "python")
    names = {entry.name for entry in results}
    assert names == {"Python教程.pdf", "python_notes.md"}

    results = search_files([tmp_path], parse_query("ext = py"))
    assert [entry.name for entry in results] == ["script.py"]


def test_search_by_size_and_type(tmp_path: Path) -> None:
    build_tree(tmp_path)
    results = search_files([tmp_path], parse_query("size > 1KB"))
    assert {entry.name for entry in results} == {"movie.mp4"}
    results = search_files([tmp_path], parse_query("size > 100B"))
    assert {entry.name for entry in results} == {"movie.mp4", "python_notes.md"}

    results = search_files([tmp_path], parse_query("type = video"))
    assert [entry.name for entry in results] == ["movie.mp4"]

    results = search_files([tmp_path], parse_query("type = document, code"))
    assert {entry.name for entry in results} == {
        "Python教程.pdf",
        "python_notes.md",
        "script.py",
    }


def test_search_respects_limit_and_empty_query(tmp_path: Path) -> None:
    build_tree(tmp_path)
    assert search_files([tmp_path], "") == []
    results = search_files([tmp_path], parse_query("size > 1B"), limit=2)
    assert len(results) == 2


def test_search_skips_missing_root(tmp_path: Path) -> None:
    assert search_files([tmp_path / "nope"], "python") == []


def test_search_can_be_cancelled(tmp_path: Path) -> None:
    build_tree(tmp_path)
    results = search_files([tmp_path], "python", should_cancel=lambda: True)
    assert results == []


def test_search_recent_modified(tmp_path: Path) -> None:
    build_tree(tmp_path)
    old_file = tmp_path / "old_report.pdf"
    old_file.write_bytes(b"p" * 10)
    old_time = time.time() - 60 * 86400
    os.utime(old_file, (old_time, old_time))

    recent = search_files([tmp_path], parse_query("modified < 30d"))
    names = {entry.name for entry in recent}
    assert "old_report.pdf" not in names
