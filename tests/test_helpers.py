"""通用工具函数测试。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.core.common.helpers import (
    clamp,
    filename_from_url,
    format_bytes,
    format_count,
    format_duration,
    format_speed,
    is_http_url,
    normalize_url,
    parse_size_text,
    sanitize_filename,
    suffix_of,
    truncate_text,
    unique_path,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.00 KB"),
        (1536, "1.50 KB"),
        (5 * 1024**2, "5.00 MB"),
        (int(4.72 * 1024**3), "4.72 GB"),
        (None, "—"),
        (-5, "—"),
    ],
)
def test_format_bytes(value, expected) -> None:  # type: ignore[no-untyped-def]
    assert format_bytes(value) == expected


def test_format_speed_and_duration() -> None:
    assert format_speed(0) == "0 B/s"
    assert format_speed(1024) == "1.0 KB/s"
    assert format_duration(112) == "1:52"
    assert format_duration(3725) == "1:02:05"
    assert format_duration(None) == "—"
    assert format_count(1284) == "1,284"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("500MB", 500 * 1024**2),
        ("1.5 GB", int(1.5 * 1024**3)),
        ("1024", 1024),
        ("2tb", 2 * 1024**4),
        ("abc", None),
        ("", None),
    ],
)
def test_parse_size_text(text, expected) -> None:  # type: ignore[no-untyped-def]
    assert parse_size_text(text) == expected


def test_sanitize_filename() -> None:
    assert sanitize_filename('a<b>c:d"e|f?g*h') == "a_b_c_d_e_f_g_h"
    assert sanitize_filename("CON") == "CON_"
    assert sanitize_filename("   ") == "unnamed"
    assert sanitize_filename("report.pdf") == "report.pdf"
    assert len(sanitize_filename("x" * 400)) <= 180


def test_unique_path_never_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "example.zip"
    assert unique_path(target) == target
    target.write_text("data", encoding="utf-8")
    first = unique_path(target)
    assert first.name == "example (1).zip"
    first.write_text("data", encoding="utf-8")
    second = unique_path(target)
    assert second.name == "example (2).zip"


def test_url_helpers() -> None:
    assert is_http_url("https://example.com/file.zip")
    assert is_http_url("http://example.com")
    assert not is_http_url("ftp://example.com/file")
    assert not is_http_url("just text")
    assert not is_http_url("https://example.com/a b.zip")
    assert normalize_url("example.com/a.zip") == "https://example.com/a.zip"
    assert normalize_url("  https://a.com/x  ") == "https://a.com/x"
    assert normalize_url("") is None


def test_filename_from_url() -> None:
    assert filename_from_url("https://example.com/path/Ubuntu.iso") == "Ubuntu.iso"
    assert filename_from_url("https://example.com/path/") == "download.bin"
    assert filename_from_url("https://example.com/%E8%AF%BE%E7%A8%8B.pdf") == "课程.pdf"


def test_suffix_and_clamp() -> None:
    assert suffix_of("A.PDF") == "pdf"
    assert suffix_of("noext") == ""
    assert clamp(5, 1, 3) == 3
    assert clamp(-1, 0, 10) == 0


def test_truncate_text_width() -> None:
    assert truncate_text("abcdef", 4) == "abcd…"
    assert truncate_text("中文测试", 4) == "中文…"
    assert truncate_text("短", 10) == "短"
    assert truncate_text("abc", 0) == ""


def test_format_datetime_variants() -> None:
    from app.core.common.helpers import format_datetime

    moment = datetime(2026, 9, 25, 22, 30, 15)
    assert format_datetime(moment) == "2026-09-25 22:30"
    assert format_datetime(moment, with_seconds=True) == "2026-09-25 22:30:15"
    assert format_datetime(None) == "—"
