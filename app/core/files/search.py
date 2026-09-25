"""文件搜索：普通关键字与简单高级语法。

支持的高级条件：

* ``size > 500MB`` / ``size < 10MB``
* ``type = video``（分类）或 ``ext = pdf``（扩展名）
* ``modified < 30d`` / ``modified > 7d``
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from app.core.common.constants import SCAN_MAX_DEPTH
from app.core.common.helpers import parse_size_text
from app.core.files.categories import FileCategory
from app.core.files.models import FileEntry, SearchQuery
from app.core.files.scanner import iter_file_entries

_SIZE_RE = re.compile(r"size\s*(>=|<=|>|<|=)\s*([0-9.]+\s*[a-zA-Z]*)", re.IGNORECASE)
_EXT_RE = re.compile(r"(?:ext|extension)\s*=\s*([a-z0-9]+(?:\s*,\s*[a-z0-9]+)*)", re.IGNORECASE)
_TYPE_RE = re.compile(r"type\s*=\s*([a-zA-Z]+(?:\s*,\s*[a-zA-Z]+)*)", re.IGNORECASE)
_MODIFIED_RE = re.compile(r"modified\s*(>=|<=|>|<)\s*(\d+)\s*([dhwm])", re.IGNORECASE)

_DURATION_SECONDS = {"h": 3600, "d": 86400, "w": 7 * 86400, "m": 30 * 86400}


def parse_query(text: str) -> SearchQuery:
    """把用户输入解析为 :class:`SearchQuery`。"""
    remaining = text or ""
    query = SearchQuery()

    match = _SIZE_RE.search(remaining)
    if match:
        operator, value_text = match.group(1), match.group(2)
        size = parse_size_text(value_text)
        if size is not None:
            if operator == ">":
                query.min_size = size + 1
            elif operator == ">=":
                query.min_size = size
            elif operator == "<":
                query.max_size = max(0, size - 1)
            elif operator == "<=":
                query.max_size = size
            else:  # 精确匹配
                query.min_size = size
                query.max_size = size
        remaining = remaining.replace(match.group(0), " ")

    match = _EXT_RE.search(remaining)
    if match:
        extensions = tuple(
            item.strip().lower().lstrip(".") for item in match.group(1).split(",") if item.strip()
        )
        query.extensions = extensions
        remaining = remaining.replace(match.group(0), " ")

    match = _TYPE_RE.search(remaining)
    if match:
        categories: list[FileCategory] = []
        for item in match.group(1).split(","):
            try:
                categories.append(FileCategory(item.strip().lower()))
            except ValueError:
                continue
        query.categories = tuple(categories)
        remaining = remaining.replace(match.group(0), " ")

    match = _MODIFIED_RE.search(remaining)
    if match:
        operator, amount_text, unit = match.group(1), match.group(2), match.group(3).lower()
        seconds = int(amount_text) * _DURATION_SECONDS.get(unit, 86400)
        threshold = time.time() - seconds
        if operator.startswith("<"):
            # 最近 N 天内修改过
            query.modified_after = threshold
        else:
            query.modified_before = threshold
        remaining = remaining.replace(match.group(0), " ")

    query.text = " ".join(remaining.split())
    return query


def search_files(
    roots: Iterable[Path | str],
    query: SearchQuery | str,
    *,
    max_depth: int = SCAN_MAX_DEPTH,
    limit: int = 500,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int, Path], None] | None = None,
) -> list[FileEntry]:
    """在若干目录中搜索符合条件的文件。"""
    resolved = parse_query(query) if isinstance(query, str) else query
    results: list[FileEntry] = []
    if resolved.is_empty:
        return results
    cancelled = should_cancel or (lambda: False)
    for root in roots:
        base = Path(root)
        if not base.exists():
            continue
        for entry in iter_file_entries(
            base,
            max_depth=max_depth,
            should_cancel=cancelled,
            on_progress=on_progress,
        ):
            if resolved.matches(entry):
                results.append(entry)
                if len(results) >= limit:
                    return results
    return results
