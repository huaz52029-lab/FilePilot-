"""页面标识与左侧导航定义。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class PageId(StrEnum):
    """全部页面标识。"""

    HOME = "home"
    DOWNLOADS = "downloads"
    ORGANIZER = "organizer"
    STORAGE = "storage"
    DUPLICATES = "duplicates"
    HISTORY = "history"
    SETTINGS = "settings"
    ABOUT = "about"

    @property
    def label(self) -> str:
        return PAGE_LABELS[self]


PAGE_LABELS: Final[dict[PageId, str]] = {
    PageId.HOME: "首页",
    PageId.DOWNLOADS: "下载中心",
    PageId.ORGANIZER: "文件整理",
    PageId.STORAGE: "空间分析",
    PageId.DUPLICATES: "重复文件",
    PageId.HISTORY: "历史记录",
    PageId.SETTINGS: "设置",
    PageId.ABOUT: "关于",
}


@dataclass(frozen=True, slots=True)
class NavItem:
    """左侧导航项。"""

    page_id: PageId
    label: str
    icon: str
    tooltip: str = ""


MAIN_NAV_ITEMS: Final[tuple[NavItem, ...]] = (
    NavItem(PageId.HOME, "首页", "home", "概览与快速下载"),
    NavItem(PageId.DOWNLOADS, "下载", "download", "下载中心"),
    NavItem(PageId.ORGANIZER, "文件整理", "folder", "扫描并整理文件"),
    NavItem(PageId.STORAGE, "空间分析", "chart", "磁盘与目录占用"),
    NavItem(PageId.DUPLICATES, "重复文件", "copy", "查找重复文件"),
    NavItem(PageId.HISTORY, "历史记录", "clock", "下载与整理历史"),
)

FOOTER_NAV_ITEMS: Final[tuple[NavItem, ...]] = (
    NavItem(PageId.SETTINGS, "设置", "settings", "应用设置"),
    NavItem(PageId.ABOUT, "关于", "info", "版本与开源许可"),
)

ALL_NAV_ITEMS: Final[tuple[NavItem, ...]] = MAIN_NAV_ITEMS + FOOTER_NAV_ITEMS
