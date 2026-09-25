"""关于页面：版本、功能列表、技术栈与开源许可。"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.core.common.constants import (
    APP_AUTHOR,
    APP_HOMEPAGE,
    APP_LICENSE,
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
)
from app.core.common.helpers import open_in_explorer
from app.core.common.paths import data_root, logs_dir
from app.ui.navigation import PageId
from app.ui.pages.base_page import BasePage
from app.ui.theme import token
from app.ui.widgets.buttons import GhostButton, SecondaryButton
from app.ui.widgets.cards import Card, SectionHeader
from app.ui.widgets.toast import ToastLevel

FEATURES: tuple[tuple[str, str], ...] = (
    ("⚡ Range 多连接下载", "支持 Accept-Ranges 的服务器可分段并行下载。"),
    ("🔄 断点续传", "分段进度持久化到 SQLite，重启后可继续。"),
    ("🧠 自适应并发", "根据速度与失败率动态增减连接数，避免无效连接。"),
    ("🛡 自动重试与回退", "指数退避重试，Range 不兼容时自动回退单连接。"),
    ("📁 文件整理", "按类型或自定义规则整理目录，执行前可预览。"),
    ("🔍 重复文件检测", "先比大小再算 SHA-256，默认只移动不删除。"),
    ("💾 空间分析", "磁盘总览、目录钻取、最大文件与类型分布。"),
    ("🔐 SHA-256 校验", "下载完成后自动计算并与预期值比对。"),
    ("🖥 现代中文界面", "深色优先的 Windows 11 风格界面。"),
)

STACK: tuple[str, ...] = (
    "Python 3.13+",
    "PySide6",
    "asyncio + aiohttp",
    "SQLite",
    "QSettings",
    "PyInstaller",
)


class AboutPage(BasePage):
    """关于页面。"""

    page_id = PageId.ABOUT
    title = "关于 FilePilot"
    subtitle = "本地优先、可长期使用的文件管理与智能下载工具"

    def __init__(self, context, parent: QWidget | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(context, parent)
        self._build()

    def _build(self) -> None:
        content = self.add_scrollable()

        hero = Card(padding=(24, 22, 24, 22), spacing=10)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(16)
        logo = QLabel(hero)
        logo.setPixmap(self.icons.pixmap("logo", color=token("accent"), size=44))
        logo.setFixedSize(44, 44)
        header.addWidget(logo, 0)
        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        name = QLabel(APP_NAME, hero)
        name.setProperty("role", "page-title")
        text_column.addWidget(name)
        tagline = QLabel(APP_TAGLINE, hero)
        tagline.setProperty("role", "page-subtitle")
        text_column.addWidget(tagline)
        version = QLabel(f"版本 {APP_VERSION} · {APP_LICENSE} License · {APP_AUTHOR}", hero)
        version.setProperty("role", "item-meta")
        text_column.addWidget(version)
        header.addLayout(text_column, 1)
        hero.add_layout(header)

        description = QLabel(
            "FilePilot 是一个本地优先的 Windows 桌面工具：下载引擎与界面完全解耦，"
            "所有下载状态都来自真实网络请求与实际文件写入。下载速度取决于你的网络、服务器与 CDN，"
            "多连接分段只在服务器支持 Range 时启用，不保证对所有服务器都能加速。",
            hero,
        )
        description.setWordWrap(True)
        description.setProperty("role", "form-description")
        hero.add(description)

        hero_buttons = QHBoxLayout()
        hero_buttons.setContentsMargins(0, 6, 0, 0)
        hero_buttons.setSpacing(8)
        homepage = SecondaryButton("打开项目地址", icon_name="external", parent=hero)
        homepage.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(APP_HOMEPAGE)))
        hero_buttons.addWidget(homepage, 0)
        copy_info = GhostButton("复制版本信息", icon_name="copy", parent=hero)
        copy_info.clicked.connect(self._copy_version_info)
        hero_buttons.addWidget(copy_info, 0)
        hero_buttons.addStretch(1)
        hero.add_layout(hero_buttons)
        content.addWidget(hero)

        features_card = Card(padding=(20, 18, 20, 18), spacing=12)
        features_card.add(SectionHeader("核心功能", "", icon="check", parent=features_card))
        for title, detail in FEATURES:
            row = QVBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(2)
            title_label = QLabel(title, features_card)
            title_label.setProperty("role", "form-title")
            row.addWidget(title_label)
            detail_label = QLabel(detail, features_card)
            detail_label.setProperty("role", "form-description")
            detail_label.setWordWrap(True)
            row.addWidget(detail_label)
            features_card.add_layout(row)
        content.addWidget(features_card)

        stack_card = Card(padding=(20, 18, 20, 18), spacing=12)
        stack_card.add(SectionHeader("技术栈", "核心模块均可脱离界面单独测试", icon="code", parent=stack_card))
        stack_label = QLabel(" · ".join(STACK), stack_card)
        stack_label.setProperty("role", "form-description")
        stack_label.setWordWrap(True)
        stack_card.add(stack_label)

        limitations = QLabel(
            "已知限制：下载速度受用户网络、服务器带宽与限速策略影响；"
            "部分服务器不返回 Content-Length，此类任务无法显示百分比与 ETA；"
            "断点续传依赖服务器提供 ETag 或 Last-Modified 以校验文件是否变化。",
            stack_card,
        )
        limitations.setProperty("role", "detail-note")
        limitations.setWordWrap(True)
        stack_card.add(limitations)

        stack_buttons = QHBoxLayout()
        stack_buttons.setContentsMargins(0, 4, 0, 0)
        stack_buttons.setSpacing(8)
        open_logs = SecondaryButton("打开日志目录", icon_name="folder-open", parent=stack_card)
        open_logs.clicked.connect(lambda: self._open_directory(logs_dir()))
        stack_buttons.addWidget(open_logs, 0)
        open_data = SecondaryButton("打开数据目录", icon_name="folder-open", parent=stack_card)
        open_data.clicked.connect(lambda: self._open_directory(data_root()))
        stack_buttons.addWidget(open_data, 0)
        stack_buttons.addStretch(1)
        stack_card.add_layout(stack_buttons)
        content.addWidget(stack_card)
        content.addStretch(1)

    # ------------------------------------------------------------------
    def _copy_version_info(self) -> None:
        text = f"{APP_NAME} {APP_VERSION} · {APP_TAGLINE} · {APP_AUTHOR}"
        QApplication.clipboard().setText(text)
        self.toast("版本信息已复制。", ToastLevel.SUCCESS)

    def _open_directory(self, path) -> None:  # type: ignore[no-untyped-def]
        try:
            path.mkdir(parents=True, exist_ok=True)
            open_in_explorer(path)
        except OSError as exc:
            self.report_error(exc, title="无法打开目录")
