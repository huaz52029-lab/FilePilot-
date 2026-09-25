"""设置页面：下载、文件、界面与通知设置。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QWidget

from app.core.common.constants import APP_HOMEPAGE, APP_NAME, APP_VERSION
from app.core.common.helpers import open_in_explorer
from app.core.common.paths import cache_dir, data_root, logs_dir
from app.services.settings_service import ArchiveMode, DuplicateAction, ThemeMode
from app.ui.navigation import PageId
from app.ui.pages.base_page import BasePage
from app.ui.theme import active_theme
from app.ui.widgets.buttons import DangerButton, GhostButton, SecondaryButton
from app.ui.widgets.cards import Card, SectionHeader
from app.ui.widgets.dialog import confirm
from app.ui.widgets.inputs import ComboRow, PathPicker, SpinRow, SwitchRow
from app.ui.widgets.toast import ToastLevel


class SettingsPage(BasePage):
    """应用设置页面。"""

    page_id = PageId.SETTINGS
    title = "设置"
    subtitle = "所有设置保存在本地，可随时恢复默认值"

    def __init__(self, context, parent: QWidget | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(context, parent)
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        content = self.add_scrollable()

        # 下载设置
        download_card = Card(padding=(20, 18, 20, 18), spacing=16)
        download_card.add(SectionHeader("下载设置", "下载目录、并发与校验策略", icon="download", parent=download_card))
        self.download_dir_picker = PathPicker(
            str(self.settings.download_dir),
            placeholder="选择默认下载目录",
            title="选择默认下载目录",
            parent=download_card,
        )
        self.download_dir_picker.changed.connect(self._on_download_dir_changed)
        download_card.add(self.download_dir_picker)

        self.concurrent_row = SpinRow(
            "同时下载的任务数",
            minimum=1,
            maximum=10,
            value=self.settings.max_concurrent_tasks,
            suffix=" 个",
            description="同时进行的文件任务数量；单个文件内部仍可使用多连接分段。",
            parent=download_card,
        )
        self.concurrent_row.changed.connect(self._on_concurrency_changed)
        download_card.add(self.concurrent_row)

        connection_options = [("0", "自动（推荐）")] + [
            (str(value), f"{value} 个连接") for value in range(1, 17)
        ]
        self.connection_row = ComboRow(
            "每个任务的连接数",
            connection_options,
            description="自动模式会根据文件大小与服务器 Range 支持情况选择 1–8 个连接。",
            parent=download_card,
        )
        self.connection_row.set_value(str(self.settings.connection_mode))
        self.connection_row.changed.connect(self._on_connection_changed)
        download_card.add(self.connection_row)

        self.auto_resume = SwitchRow(
            "启动时恢复未完成下载",
            "打开软件后自动继续上次未完成的任务；会校验 ETag 与文件大小。",
            checked=self.settings.auto_resume,
            parent=download_card,
        )
        self.auto_resume.toggled.connect(lambda value: setattr(self.settings, "auto_resume", value))
        download_card.add(self.auto_resume)

        self.auto_retry = SwitchRow(
            "失败自动重试",
            "对 408/429/5xx 与连接中断等临时错误使用指数退避重试，最多 5 次。",
            checked=self.settings.auto_retry,
            parent=download_card,
        )
        self.auto_retry.toggled.connect(lambda value: setattr(self.settings, "auto_retry", value))
        download_card.add(self.auto_retry)

        self.auto_verify = SwitchRow(
            "下载完成后计算 SHA-256",
            "完成后自动计算校验值，可与预期值自动比对。",
            checked=self.settings.auto_verify,
            parent=download_card,
        )
        self.auto_verify.toggled.connect(lambda value: setattr(self.settings, "auto_verify", value))
        download_card.add(self.auto_verify)
        content.addWidget(download_card)

        # 文件设置
        file_card = Card(padding=(20, 18, 20, 18), spacing=16)
        file_card.add(SectionHeader("文件设置", "自动归档与重复文件处理", icon="folder", parent=file_card))
        self.archive_row = ComboRow(
            "下载完成后自动归档",
            [(mode.value, mode.label) for mode in ArchiveMode],
            description="按文件类型把下载内容移动到下载目录下的分类文件夹。",
            parent=file_card,
        )
        self.archive_row.set_value(self.settings.archive_mode.value)
        self.archive_row.changed.connect(self._on_archive_changed)
        file_card.add(self.archive_row)

        self.archive_root_picker = PathPicker(
            str(self.settings.archive_root),
            placeholder="选择归档根目录（默认与下载目录相同）",
            title="选择归档根目录",
            parent=file_card,
        )
        self.archive_root_picker.changed.connect(
            lambda value: setattr(self.settings, "archive_root", value)
        )
        file_card.add(self.archive_root_picker)

        self.duplicate_row = ComboRow(
            "重复文件处理方式",
            [(action.value, action.label) for action in DuplicateAction],
            description="FilePilot 默认不会删除文件，只会移动到“重复文件”文件夹。",
            parent=file_card,
        )
        self.duplicate_row.set_value(self.settings.duplicate_action.value)
        self.duplicate_row.changed.connect(
            lambda value: setattr(self.settings, "duplicate_action", value)
        )
        file_card.add(self.duplicate_row)
        content.addWidget(file_card)

        # 界面设置
        ui_card = Card(padding=(20, 18, 20, 18), spacing=16)
        ui_card.add(SectionHeader("界面设置", "深色优先，可跟随系统", icon="settings", parent=ui_card))
        self.theme_row = ComboRow(
            "主题",
            [(mode.value, mode.label) for mode in ThemeMode],
            parent=ui_card,
        )
        self.theme_row.set_value(self.settings.theme.value)
        self.theme_row.changed.connect(self._on_theme_changed)
        ui_card.add(self.theme_row)
        content.addWidget(ui_card)

        # 通知设置
        notify_card = Card(padding=(20, 18, 20, 18), spacing=16)
        notify_card.add(SectionHeader("通知", "仅在需要时提醒", icon="info", parent=notify_card))
        self.notify_completed = SwitchRow(
            "下载完成通知",
            "任务下载与校验完成时提示。",
            checked=self.settings.notify_completed,
            parent=notify_card,
        )
        self.notify_completed.toggled.connect(
            lambda value: setattr(self.settings, "notify_completed", value)
        )
        notify_card.add(self.notify_completed)
        self.notify_errors = SwitchRow(
            "错误通知",
            "任务失败或网络中断时提示。",
            checked=self.settings.notify_errors,
            parent=notify_card,
        )
        self.notify_errors.toggled.connect(
            lambda value: setattr(self.settings, "notify_errors", value)
        )
        notify_card.add(self.notify_errors)
        self.notify_restored = SwitchRow(
            "任务恢复通知",
            "启动时恢复未完成任务后提示。",
            checked=self.settings.notify_restored,
            parent=notify_card,
        )
        self.notify_restored.toggled.connect(
            lambda value: setattr(self.settings, "notify_restored", value)
        )
        notify_card.add(self.notify_restored)
        content.addWidget(notify_card)

        # 数据与日志
        data_card = Card(padding=(20, 18, 20, 18), spacing=16)
        data_card.add(SectionHeader("数据与日志", "数据库与日志都保存在用户目录", icon="file", parent=data_card))
        info_lines = [
            f"数据目录：{data_root()}",
            f"数据库：{self.context.database_path}",
            f"日志目录：{logs_dir()}",
            f"缓存目录：{cache_dir()}",
        ]
        for line in info_lines:
            label = QLabel(line, data_card)
            label.setProperty("role", "form-description")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            data_card.add(label)

        data_buttons = QHBoxLayout()
        data_buttons.setContentsMargins(0, 4, 0, 0)
        data_buttons.setSpacing(8)
        open_data = SecondaryButton("打开数据目录", icon_name="folder-open", parent=data_card)
        open_data.clicked.connect(lambda: self._open_directory(data_root()))
        data_buttons.addWidget(open_data, 0)
        open_logs = SecondaryButton("打开日志目录", icon_name="folder-open", parent=data_card)
        open_logs.clicked.connect(lambda: self._open_directory(logs_dir()))
        data_buttons.addWidget(open_logs, 0)
        data_buttons.addStretch(1)
        reset_button = DangerButton("恢复默认设置", icon_name="refresh", parent=data_card)
        reset_button.clicked.connect(self._reset_settings)
        data_buttons.addWidget(reset_button, 0)
        data_card.add_layout(data_buttons)
        content.addWidget(data_card)

        # 关于与项目地址（唯一地址来源：app.core.common.constants.APP_HOMEPAGE）
        about_card = Card(padding=(20, 18, 20, 18), spacing=12)
        about_card.add(
            SectionHeader("关于与反馈", "本地优先，不需要登录，也不上传任何数据", icon="info", parent=about_card)
        )
        version_label = QLabel(f"{APP_NAME} 版本 v{APP_VERSION}", about_card)
        version_label.setProperty("role", "form-title")
        about_card.add(version_label)

        address_row = QHBoxLayout()
        address_row.setContentsMargins(0, 0, 0, 0)
        address_row.setSpacing(10)
        address_title = QLabel("项目地址", about_card)
        address_title.setProperty("role", "detail-label")
        address_row.addWidget(address_title, 0)
        self.project_address_label = QLabel(APP_HOMEPAGE, about_card)
        self.project_address_label.setProperty("role", "detail-value")
        self.project_address_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.project_address_label.setWordWrap(True)
        address_row.addWidget(self.project_address_label, 1)
        about_card.add_layout(address_row)

        project_buttons = QHBoxLayout()
        project_buttons.setContentsMargins(0, 4, 0, 0)
        project_buttons.setSpacing(8)
        open_project = SecondaryButton("打开项目地址", icon_name="external", parent=about_card)
        open_project.clicked.connect(self._open_project_page)
        project_buttons.addWidget(open_project, 0)
        copy_address = GhostButton("复制地址", icon_name="copy", parent=about_card)
        copy_address.clicked.connect(self._copy_project_address)
        project_buttons.addWidget(copy_address, 0)
        project_buttons.addStretch(1)
        about_card.add_layout(project_buttons)
        content.addWidget(about_card)
        content.addStretch(1)

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _on_download_dir_changed(self, value: str) -> None:
        if not value:
            return
        self.settings.download_dir = Path(value)
        self.toast("默认下载目录已更新。", ToastLevel.SUCCESS)

    def _on_concurrency_changed(self, value: int) -> None:
        self.settings.max_concurrent_tasks = value
        self.context.downloads.emit_stats()

    def _on_connection_changed(self, value: str) -> None:
        self.settings.connection_mode = int(value or 0)
        self.toast("连接数设置已更新，将在新任务中生效。", ToastLevel.SUCCESS)

    def _on_archive_changed(self, value: str) -> None:
        self.settings.archive_mode = value
        self.archive_root_picker.set_path(self.settings.archive_root)

    def _on_theme_changed(self, value: str) -> None:
        theme = active_theme()
        if theme is None:
            self.settings.theme = value
            return
        theme.set_mode(ThemeMode(value))
        self.toast(f"已切换到{ThemeMode(value).label}主题。", ToastLevel.SUCCESS)

    def _open_directory(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
            open_in_explorer(path)
        except OSError as exc:
            self.report_error(exc, title="无法打开目录")

    def _open_project_page(self) -> None:
        """在默认浏览器中打开项目地址。"""
        if not QDesktopServices.openUrl(QUrl(APP_HOMEPAGE)):
            self.toast(f"无法自动打开浏览器，请手动访问：{APP_HOMEPAGE}", ToastLevel.WARNING)
            return
        self.toast(f"已在浏览器中打开：{APP_HOMEPAGE}", ToastLevel.SUCCESS)

    def _copy_project_address(self) -> None:
        QApplication.clipboard().setText(APP_HOMEPAGE)
        self.toast("项目地址已复制到剪贴板。", ToastLevel.SUCCESS)

    def _reset_settings(self) -> None:
        if not confirm(
            self,
            "恢复默认设置",
            "所有设置将恢复为默认值（不会删除已下载的文件与历史记录）。",
            confirm_text="恢复默认",
            danger=True,
        ):
            return
        self.settings.reset_to_defaults()
        self._sync_widgets()
        theme = active_theme()
        if theme is not None:
            theme.apply()
        self.toast("设置已恢复默认值。", ToastLevel.SUCCESS)

    def _sync_widgets(self) -> None:
        self.download_dir_picker.set_path(self.settings.download_dir)
        self.concurrent_row.set_value(self.settings.max_concurrent_tasks)
        self.connection_row.set_value(str(self.settings.connection_mode))
        self.auto_resume.set_checked(self.settings.auto_resume)
        self.auto_retry.set_checked(self.settings.auto_retry)
        self.auto_verify.set_checked(self.settings.auto_verify)
        self.archive_row.set_value(self.settings.archive_mode.value)
        self.archive_root_picker.set_path(self.settings.archive_root)
        self.duplicate_row.set_value(self.settings.duplicate_action.value)
        self.theme_row.set_value(self.settings.theme.value)
        self.notify_completed.set_checked(self.settings.notify_completed)
        self.notify_errors.set_checked(self.settings.notify_errors)
        self.notify_restored.set_checked(self.settings.notify_restored)

    def on_show(self) -> None:
        self._sync_widgets()
