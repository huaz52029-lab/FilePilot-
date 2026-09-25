"""重复文件检测页面：先比大小，再算 SHA-256；默认只移动，不删除。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.common.helpers import format_bytes, open_in_explorer, truncate_text
from app.core.files.duplicate import duplicate_folder
from app.core.files.models import DuplicateGroup, DuplicateScanResult
from app.ui.navigation import PageId
from app.ui.pages.base_page import BasePage
from app.ui.widgets.badge import StatusBadge
from app.ui.widgets.buttons import GhostButton, PrimaryButton, SecondaryButton
from app.ui.widgets.cards import Card, SectionHeader
from app.ui.widgets.dialog import confirm
from app.ui.widgets.empty_state import EmptyState
from app.ui.widgets.section import Section
from app.ui.widgets.toast import ToastLevel

MAX_GROUP_ROWS = 200


class DuplicatesPage(BasePage):
    """重复文件页面。"""

    page_id = PageId.DUPLICATES
    title = "重复文件"
    subtitle = "先比较文件大小，再计算 SHA-256；默认只移动到“重复文件”文件夹，绝不自动删除"

    def __init__(self, context, parent: QWidget | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(context, parent)
        self._result: DuplicateScanResult | None = None
        self._group_widgets: list[QWidget] = []
        self._worker = None
        self._build()
        self._connect_service()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        duplicate_folder_button = GhostButton("打开重复文件夹", icon_name="folder-open", parent=self)
        duplicate_folder_button.clicked.connect(self._open_duplicate_folder)
        self.add_action(duplicate_folder_button)

        content = self.add_scrollable()

        roots_card = Card(padding=(20, 18, 20, 18), spacing=14)
        roots_card.add(
            SectionHeader(
                "扫描位置",
                "可以同时添加多个文件夹（例如下载目录与图片目录）",
                icon="copy",
                parent=roots_card,
            )
        )
        self.roots_list = QListWidget(roots_card)
        self.roots_list.setObjectName("RootsList")
        self.roots_list.setMinimumHeight(110)
        roots_card.add(self.roots_list)

        roots_buttons = QHBoxLayout()
        roots_buttons.setContentsMargins(0, 0, 0, 0)
        roots_buttons.setSpacing(8)
        add_button = SecondaryButton("添加文件夹", icon_name="plus", parent=roots_card)
        add_button.clicked.connect(self._add_root)
        roots_buttons.addWidget(add_button, 0)
        remove_button = SecondaryButton("移除选中", icon_name="trash", parent=roots_card)
        remove_button.clicked.connect(self._remove_root)
        roots_buttons.addWidget(remove_button, 0)
        roots_buttons.addStretch(1)
        self.cancel_button = SecondaryButton("取消", icon_name="close", parent=roots_card)
        self.cancel_button.clicked.connect(self._cancel_scan)
        self.cancel_button.setEnabled(False)
        roots_buttons.addWidget(self.cancel_button, 0)
        self.scan_button = PrimaryButton("开始检测", icon_name="search", parent=roots_card)
        self.scan_button.clicked.connect(self._start_scan)
        roots_buttons.addWidget(self.scan_button, 0)
        roots_card.add_layout(roots_buttons)
        self.progress_label = QLabel("", roots_card)
        self.progress_label.setProperty("role", "hint")
        self.progress_label.setWordWrap(True)
        roots_card.add(self.progress_label)
        content.addWidget(roots_card)

        summary_card = Card(padding=(20, 16, 20, 16), spacing=8)
        summary_row = QHBoxLayout()
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.setSpacing(28)
        self.groups_label, groups_widget = self._make_stat(summary_card, "重复组数")
        self.files_label, files_widget = self._make_stat(summary_card, "重复文件")
        self.wasted_label, wasted_widget = self._make_stat(summary_card, "可回收空间")
        self.scanned_label, scanned_widget = self._make_stat(summary_card, "已扫描文件")
        for widget in (groups_widget, files_widget, wasted_widget, scanned_widget):
            summary_row.addWidget(widget)
        summary_row.addStretch(1)
        summary_card.add_layout(summary_row)
        content.addWidget(summary_card)

        self.result_section = Section("检测结果", "每组默认保留第一份，其余可移动到重复文件夹", icon="copy", parent=self)
        self.result_empty = EmptyState(
            "尚未检测重复文件",
            "添加扫描位置后点击“开始检测”，检测过程不会修改任何文件。",
            icon="copy",
        )
        self.result_section.set_empty_widget(self.result_empty)
        content.addWidget(self.result_section)
        content.addStretch(1)

    def _make_stat(self, parent: QWidget, caption: str) -> tuple[QLabel, QWidget]:
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        value = QLabel("—", parent)
        value.setProperty("role", "stat-value-small")
        caption_label = QLabel(caption, parent)
        caption_label.setProperty("role", "stat-caption")
        column.addWidget(value)
        column.addWidget(caption_label)
        holder = QWidget(parent)
        holder.setLayout(column)
        return value, holder

    def _connect_service(self) -> None:
        library = self.context.library
        library.duplicates_ready.connect(self._on_scan_ready)
        library.duplicates_moved.connect(self._on_duplicates_moved)
        library.failed.connect(self._on_failed)
        library.progress.connect(self._on_progress)

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        if self.roots_list.count() == 0:
            directory = self.settings.download_dir
            if directory.exists():
                self._append_root(directory)

    # ------------------------------------------------------------------
    # 扫描位置
    # ------------------------------------------------------------------
    def _append_root(self, path: Path) -> None:
        for index in range(self.roots_list.count()):
            item = self.roots_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == str(path):
                return
        item = QListWidgetItem(str(path))
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        self.roots_list.addItem(item)

    def _add_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择需要检测的文件夹", str(Path.home()))
        if selected:
            self._append_root(Path(selected))

    def _remove_root(self) -> None:
        for item in self.roots_list.selectedItems():
            self.roots_list.takeItem(self.roots_list.row(item))

    def _selected_roots(self) -> list[Path]:
        return [
            Path(str(self.roots_list.item(index).data(Qt.ItemDataRole.UserRole)))
            for index in range(self.roots_list.count())
        ]

    # ------------------------------------------------------------------
    # 检测
    # ------------------------------------------------------------------
    def _start_scan(self) -> None:
        roots = self._selected_roots()
        if not roots:
            self.toast("请先添加至少一个扫描位置。", ToastLevel.WARNING)
            return
        missing = [root for root in roots if not root.exists()]
        if missing:
            self.toast(f"目录不存在：{missing[0]}", ToastLevel.WARNING)
            return
        self.scan_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_label.setText("正在扫描文件并计算 SHA-256…")
        self._worker = self.context.library.scan_duplicates(roots)

    def _cancel_scan(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.progress_label.setText("正在取消…")

    def _on_progress(self, payload: object) -> None:
        if isinstance(payload, str):
            self.progress_label.setText(payload)

    def _on_scan_ready(self, result: DuplicateScanResult) -> None:
        self._worker = None
        self._result = result
        self.scan_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.groups_label.setText(str(result.group_count))
        self.files_label.setText(str(result.duplicate_file_count))
        self.wasted_label.setText(format_bytes(result.wasted_bytes))
        self.scanned_label.setText(str(result.scanned_files))
        if result.cancelled:
            self.progress_label.setText("检测已取消，结果可能不完整。")
        else:
            self.progress_label.setText(
                f"检测完成：扫描 {result.scanned_files} 个文件，"
                f"计算 {result.hashed_files} 个哈希，耗时 {result.duration:.1f} 秒。"
            )
        self._render_groups()
        if result.group_count:
            self.toast(
                f"发现 {result.group_count} 组重复文件，可回收 {format_bytes(result.wasted_bytes)}",
                ToastLevel.SUCCESS,
            )
        else:
            self.toast("没有发现重复文件。", ToastLevel.INFO)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self.scan_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress_label.setText("")
        self.toast(message, ToastLevel.ERROR)

    def _on_duplicates_moved(self, moved: int) -> None:
        """移动完成后更新本地结果（无需重新扫描整个目录）。"""
        self._worker = None
        self.progress_label.setText(f"已移动 {moved} 个重复文件到“重复文件”文件夹。")
        if self._result is None:
            return
        refreshed: list[DuplicateGroup] = []
        for group in self._result.groups:
            remaining = tuple(path for path in group.files if path.exists())
            if len(remaining) > 1:
                refreshed.append(
                    DuplicateGroup(size=group.size, sha256=group.sha256, files=remaining)
                )
        self._result.groups = refreshed
        self.groups_label.setText(str(self._result.group_count))
        self.files_label.setText(str(self._result.duplicate_file_count))
        self.wasted_label.setText(format_bytes(self._result.wasted_bytes))
        self._render_groups()

    # ------------------------------------------------------------------
    # 结果渲染
    # ------------------------------------------------------------------
    def _render_groups(self) -> None:
        for widget in self._group_widgets:
            self.result_section.content.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._group_widgets.clear()
        groups = self._result.groups[:MAX_GROUP_ROWS] if self._result else []
        for group in groups:
            card = self._make_group_card(group)
            self.result_section.content.addWidget(card)
            self._group_widgets.append(card)
        self.result_section.set_count(len(groups))
        self.result_empty.setVisible(not groups)

    def _make_group_card(self, group: DuplicateGroup) -> QWidget:
        card = Card(self, padding=(16, 14, 16, 14), spacing=8)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        title = QLabel(truncate_text(group.preview_name, 48), card)
        title.setProperty("role", "item-title")
        title.setToolTip(group.preview_name)
        header.addWidget(title, 1)
        header.addWidget(StatusBadge(f"{group.count} 个相同文件", "accent"), 0)
        header.addWidget(StatusBadge(format_bytes(group.size), "neutral"), 0)
        card.add_layout(header)

        digest = QLabel(f"SHA-256：{group.sha256}", card)
        digest.setProperty("role", "item-meta")
        digest.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.add(digest)

        for index, path in enumerate(group.files):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            label = QLabel(truncate_text(str(path), 84), card)
            label.setProperty("role", "item-subtitle")
            label.setToolTip(str(path))
            row.addWidget(label, 1)
            if index == 0:
                row.addWidget(StatusBadge("保留", "success"), 0)
            else:
                row.addWidget(StatusBadge("重复", "warning"), 0)
            open_button = GhostButton("打开", icon_name="folder-open", parent=card)
            open_button.clicked.connect(
                lambda _checked=False, target=path: open_in_explorer(target, select=True)
            )
            row.addWidget(open_button, 0)
            card.add_layout(row)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 4, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)
        move_button = SecondaryButton("移动重复项", icon_name="folder", parent=card)
        move_button.clicked.connect(lambda _checked=False, item=group: self._move_group(item))
        actions.addWidget(move_button, 0)
        card.add_layout(actions)
        return card

    # ------------------------------------------------------------------
    # 处理动作
    # ------------------------------------------------------------------
    def _move_group(self, group: DuplicateGroup) -> None:
        duplicates = list(group.files[1:])
        if not duplicates:
            return
        target = duplicate_folder(self.settings.download_dir)
        if not confirm(
            self,
            "移动重复文件",
            f"将把 {len(duplicates)} 个重复文件移动到：\n{target}\n\n"
            "保留文件：\n" + str(group.files[0]) + "\n\n文件不会从磁盘删除，随时可以还原。",
            confirm_text="移动",
        ):
            return
        self.progress_label.setText("正在移动重复文件…")
        self._worker = self.context.library.move_duplicates(duplicates, target_dir=target)

    def _open_duplicate_folder(self) -> None:
        target = duplicate_folder(self.settings.download_dir)
        if not target.exists():
            self.toast("还没有创建“重复文件”文件夹。", ToastLevel.INFO)
            return
        open_in_explorer(target)
