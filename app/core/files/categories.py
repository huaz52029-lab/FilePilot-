"""文件分类定义（图片 / 视频 / 文档 / 音频 / 压缩包 / 程序 / 代码 / 其他）。"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Final

from app.core.common.helpers import suffix_of


class FileCategory(StrEnum):
    """内置文件分类。"""

    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    AUDIO = "audio"
    ARCHIVE = "archive"
    PROGRAM = "program"
    CODE = "code"
    OTHER = "other"


CATEGORY_LABELS: Final[dict[FileCategory, str]] = {
    FileCategory.IMAGE: "图片",
    FileCategory.VIDEO: "视频",
    FileCategory.DOCUMENT: "文档",
    FileCategory.AUDIO: "音频",
    FileCategory.ARCHIVE: "压缩包",
    FileCategory.PROGRAM: "程序",
    FileCategory.CODE: "代码",
    FileCategory.OTHER: "其他",
}

#: 自动归档时使用的子目录名（用户可在设置中修改根目录）。
CATEGORY_FOLDER_NAMES: Final[dict[FileCategory, str]] = {
    FileCategory.IMAGE: "图片",
    FileCategory.VIDEO: "视频",
    FileCategory.DOCUMENT: "文档",
    FileCategory.AUDIO: "音频",
    FileCategory.ARCHIVE: "压缩包",
    FileCategory.PROGRAM: "程序",
    FileCategory.CODE: "代码",
    FileCategory.OTHER: "其他",
}

CATEGORY_EXTENSIONS: Final[dict[FileCategory, frozenset[str]]] = {
    FileCategory.IMAGE: frozenset(
        {
            "jpg",
            "jpeg",
            "png",
            "gif",
            "bmp",
            "webp",
            "svg",
            "ico",
            "tif",
            "tiff",
            "heic",
            "heif",
            "avif",
            "raw",
            "cr2",
            "nef",
            "psd",
        }
    ),
    FileCategory.VIDEO: frozenset(
        {
            "mp4",
            "mkv",
            "avi",
            "mov",
            "wmv",
            "flv",
            "webm",
            "m4v",
            "mpg",
            "mpeg",
            "ts",
            "rmvb",
            "3gp",
            "vob",
        }
    ),
    FileCategory.DOCUMENT: frozenset(
        {
            "pdf",
            "doc",
            "docx",
            "xls",
            "xlsx",
            "xlsm",
            "ppt",
            "pptx",
            "txt",
            "rtf",
            "md",
            "csv",
            "epub",
            "mobi",
            "azw3",
            "odt",
            "ods",
            "odp",
            "wps",
            "et",
            "dps",
            "tex",
        }
    ),
    FileCategory.AUDIO: frozenset(
        {
            "mp3",
            "wav",
            "flac",
            "aac",
            "ogg",
            "oga",
            "m4a",
            "wma",
            "opus",
            "mid",
            "midi",
            "ape",
            "aiff",
            "amr",
        }
    ),
    FileCategory.ARCHIVE: frozenset(
        {
            "zip",
            "rar",
            "7z",
            "tar",
            "gz",
            "tgz",
            "bz2",
            "xz",
            "zst",
            "cab",
            "iso",
            "img",
            "vhd",
            "vhdx",
            "dmg",
            "wim",
            "esd",
        }
    ),
    FileCategory.PROGRAM: frozenset(
        {
            "exe",
            "msi",
            "msix",
            "appx",
            "apk",
            "bat",
            "cmd",
            "com",
            "scr",
            "jar",
            "bin",
            "deb",
            "rpm",
            "appimage",
        }
    ),
    FileCategory.CODE: frozenset(
        {
            "py",
            "pyi",
            "ipynb",
            "js",
            "mjs",
            "cjs",
            "ts",
            "tsx",
            "jsx",
            "vue",
            "svelte",
            "java",
            "kt",
            "kts",
            "scala",
            "groovy",
            "c",
            "h",
            "cc",
            "cpp",
            "hpp",
            "cs",
            "go",
            "rs",
            "rb",
            "php",
            "swift",
            "lua",
            "pl",
            "r",
            "jl",
            "dart",
            "html",
            "htm",
            "css",
            "scss",
            "sass",
            "less",
            "json",
            "xml",
            "yaml",
            "yml",
            "toml",
            "ini",
            "cfg",
            "conf",
            "sql",
            "sh",
            "ps1",
            "psm1",
        }
    ),
}

_EXTENSION_INDEX: Final[dict[str, FileCategory]] = {
    extension: category
    for category, extensions in CATEGORY_EXTENSIONS.items()
    for extension in extensions
}

#: 少量无扩展名但仍然属于特定分类的常见文件名。
_FILENAME_INDEX: Final[dict[str, FileCategory]] = {
    "dockerfile": FileCategory.CODE,
    "makefile": FileCategory.CODE,
    "cmakelists.txt": FileCategory.CODE,
}


def categorize_extension(extension: str) -> FileCategory:
    """按扩展名判断分类。"""
    normalized = (extension or "").lower().lstrip(".")
    return _EXTENSION_INDEX.get(normalized, FileCategory.OTHER)


def categorize_path(path: Path | str) -> FileCategory:
    """按路径判断分类。"""
    name = Path(path).name.lower()
    if name in _FILENAME_INDEX:
        return _FILENAME_INDEX[name]
    return categorize_extension(suffix_of(path))


def category_label(category: FileCategory | str) -> str:
    """分类的中文名称。"""
    if isinstance(category, str):
        try:
            category = FileCategory(category)
        except ValueError:
            return "其他"
    return CATEGORY_LABELS.get(category, "其他")


def category_folder(category: FileCategory | str) -> str:
    """分类对应的默认归档目录名。"""
    if isinstance(category, str):
        try:
            category = FileCategory(category)
        except ValueError:
            category = FileCategory.OTHER
    return CATEGORY_FOLDER_NAMES.get(category, "其他")


def all_categories() -> tuple[FileCategory, ...]:
    """返回全部分类（保持 UI 展示顺序）。"""
    return (
        FileCategory.IMAGE,
        FileCategory.VIDEO,
        FileCategory.DOCUMENT,
        FileCategory.AUDIO,
        FileCategory.ARCHIVE,
        FileCategory.PROGRAM,
        FileCategory.CODE,
        FileCategory.OTHER,
    )
