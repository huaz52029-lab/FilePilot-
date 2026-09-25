"""自定义整理规则。

规则 = 匹配条件 + 目标目录。执行前必须先生成预览计划并由用户确认。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.core.common.helpers import parse_size_text
from app.core.files.categories import FileCategory, categorize_path
from app.core.files.models import MATCH_FIELD_LABELS, FileEntry, MatchField


@dataclass(slots=True)
class OrganizeRule:
    """一条用户自定义整理规则。"""

    name: str
    field: MatchField
    value: str
    target_dir: Path
    rule_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    enabled: bool = True
    priority: int = 100
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # -- 展示 --------------------------------------------------------------
    @property
    def condition_text(self) -> str:
        """人类可读的条件描述。"""
        label = MATCH_FIELD_LABELS.get(self.field, self.field.value)
        return f"{label} {self.value}"

    @property
    def target_text(self) -> str:
        return str(self.target_dir)

    @property
    def field_label(self) -> str:
        return MATCH_FIELD_LABELS.get(self.field, self.field.value)

    # -- 匹配 --------------------------------------------------------------
    def matches(self, entry: FileEntry) -> bool:
        """判断文件是否命中该规则。"""
        if not self.enabled:
            return False
        raw = (self.value or "").strip()
        if not raw:
            return False
        match self.field:
            case MatchField.EXTENSION:
                wanted = {item.strip().lower().lstrip(".") for item in raw.split(",") if item.strip()}
                return entry.extension in wanted
            case MatchField.NAME_CONTAINS:
                return raw.lower() in entry.name.lower()
            case MatchField.NAME_PREFIX:
                return entry.name.lower().startswith(raw.lower())
            case MatchField.NAME_SUFFIX:
                return entry.name.lower().endswith(raw.lower())
            case MatchField.SIZE_GREATER:
                threshold = parse_size_text(raw)
                return threshold is not None and entry.size > threshold
            case MatchField.CATEGORY:
                wanted_categories = {
                    item.strip().lower() for item in raw.split(",") if item.strip()
                }
                category = entry.category or categorize_path(entry.path)
                return category.value in wanted_categories
        return False

    def touch(self) -> None:
        """更新时间戳。"""
        self.updated_at = time.time()

    def validate(self) -> str | None:
        """返回错误信息；校验通过返回 ``None``。"""
        if not self.name.strip():
            return "规则名称不能为空。"
        if not self.value.strip():
            return "匹配条件不能为空。"
        if self.field is MatchField.SIZE_GREATER and parse_size_text(self.value) is None:
            return "大小条件格式不正确，例如：500MB。"
        if self.field is MatchField.CATEGORY:
            valid = {category.value for category in FileCategory}
            provided = {item.strip().lower() for item in self.value.split(",") if item.strip()}
            if not provided <= valid:
                return "文件类型取值无效（可用：image、video、document…）。"
        if not str(self.target_dir).strip():
            return "目标目录不能为空。"
        return None


DEFAULT_RULE_VALUE_PLACEHOLDER = "pdf"
