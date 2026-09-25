"""空间分析页面：磁盘总览、目录钻取、最大文件与类型分布。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QWidget,
)

from app.core.common.helpers import format_bytes, format_count, open_in_explorer, truncate_text
from app.core.files.analyzer import DirectoryAnalysis, analyze_directory
from app.core.files.categories import category_label
from app.core.files.models import DirectoryUsage
from app.ui.navigation import PageId
from app.ui.pages.base_page import BasePage
from app.ui.theme import token
from app.ui.widgets.badge import StatusBadge
from app.ui.widgets.buttons import GhostButton, PrimaryButton, SecondaryButton
from app.ui.widgets.cards import Card, SectionHeader
from app.ui.widgets.empty_state import EmptyState
from app.ui.widgets.file_card import FileCard
from app.ui.widgets.inputs import ComboRow, PathPicker
from app.ui.widgets.progress_bar import ProgressBar
from app.ui.widgets.section import Section
from app.ui.widgets.toast import ToastLevel

MAX_CHILD_ROWS = 100


class StoragePage(BasePage):
    """磁盘与目录占用分析。"""

    page_id = PageId.STORAGE
    title = "空间分析"
    subtitle = "查看磁盘使用情况、目录占用排行与最大的文件"

    def __init__(self, context, parent: QWidget | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(context, parent)
        self._analysis: DirectoryAnalysis | None = None
        self._worker = None
        self._child_widgets: list[QWidget] = []
        self._largest_widgets: list[QWidget] = []
        self._category_widgets: list[QWidget] = []
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        open_button = GhostButton("在资源管理器中打开", icon_name="folder-open", parent=self)
        open_button.clicked.connect(self._open_current)
        self.add_action(open_button)

        content = self.add_scrollable()

        location_card = Card(padding=(20, 18, 20, 18), spacing=14)
        location_card.add(
            SectionHeader("分析位置", "选择磁盘或目录后开始扫描（不会修改任何文件）", icon="chart", parent=location_card)
        )
        self.path_picker = PathPicker(
            str(self.settings.download_dir),
            placeholder="选择需要分析的磁盘或目录",
            title="选择需要分析的目录",
            parent=location_card,
        )
        location_card.add(self.path_picker)
        self.sort_row = ComboRow(
            "排序方式",
            [("size", "按占用空间"), ("count", "按文件数量")],
            description="影响下方子目录的排列顺序。",
            parent=location_card,
        )
        self.sort_row.changed.connect(lambda _value: self._render_children())

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 4, 0, 0)
        buttons.setSpacing(8)
        self.analyze_button = PrimaryButton("开始分析", icon_name="search", parent=location_card)
        self.analyze_button.clicked.connect(self._start_analysis)
        buttons.addWidget(self.analyze_button, 0)
        self.cancel_button = SecondaryButton("取消", icon_name="close", parent=location_card)
        self.cancel_button.clicked.connect(self._cancel_analysis)
        self.cancel_button.setEnabled(False)
        buttons.addWidget(self.cancel_button, 0)
        buttons.addStretch(1)
        location_card.add_layout(buttons)
        self.progress_label = QLabel("", location_card)
        self.progress_label.setProperty("role", "hint")
        self.progress_label.setWordWrap(True)
        location_card.add(self.progress_label)
        content.addWidget(location_card)

        disk_card = Card(padding=(20, 18, 20, 18), spacing=10)
        disk_card.add(SectionHeader("磁盘使用", "", icon="chart", parent=disk_card))
        self.disk_title = QLabel("尚未分析", disk_card)
        self.disk_title.setProperty("role", "item-title")
        disk_card.add(self.disk_title)
        self.disk_bar = ProgressBar(disk_card, height=10)
        disk_card.add(self.disk_bar)
        self.disk_detail = QLabel("", disk_card)
        self.disk_detail.setProperty("role", "item-meta")
        disk_card.add(self.disk_detail)
        self.summary_detail = QLabel("", disk_card)
        self.summary_detail.setProperty("role", "item-meta")
        self.summary_detail.setWordWrap(True)
        disk_card.add(self.summary_detail)
        content.addWidget(disk_card)

        breadcrumb_card = Card(padding=(18, 12, 18, 12), spacing=8)
        self.breadcrumb_layout = QHBoxLayout()
        self.breadcrumb_layout.setContentsMargins(0, 0, 0, 0)
        self.breadcrumb_layout.setSpacing(6)
        breadcrumb_card.add_layout(self.breadcrumb_layout)
        content.addWidget(breadcrumb_card)

        self.children_section = Section("子目录占用", "点击任意目录可继续下钻", icon="folder", parent=self)
        self.children_empty = EmptyState("暂无数据", "选择位置并开始分析后显示子目录排行。", icon="folder")
        self.children_section.set_empty_widget(self.children_empty)
        content.addWidget(self.children_section)

        self.largest_section = Section("最大文件", "当前目录内占用最多的文件", icon="file", parent=self)
        self.largest_empty = EmptyState("暂无数据", "分析完成后显示最大的文件列表。", icon="file")
        self.largest_section.set_empty_widget(self.largest_empty)
        content.addWidget(self.largest_section)

        self.category_section = Section("文件类型分布", "按占用空间统计", icon="copy", parent=self)
        self.category_empty = EmptyState("暂无数据", "分析完成后显示类型分布。", icon="copy")
        self.category_section.set_empty_widget(self.category_empty)
        content.addWidget(self.category_section)
        content.addStretch(1)

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        if self._analysis is None:
            self._render_breadcrumb(Path(self.path_picker.path()))

    # ------------------------------------------------------------------
    # 分析
    # ------------------------------------------------------------------
    def _start_analysis(self, root: Path | None = None) -> None:
        target = root or (Path(self.path_picker.path()) if self.path_picker.path() else None)
        if target is None or not target.exists():
            self.toast("请先选择一个存在的目录。", ToastLevel.WARNING)
            return
        if self._worker is not None:
            self.toast("已有分析任务正在进行。", ToastLevel.WARNING)
            return
        self.path_picker.set_path(target)
        self.analyze_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_label.setText("正在扫描…")
        self._worker = self.context.runner.submit(
            analyze_directory,
            target,
            on_result=self._on_analysis_done,
            on_error=self._on_analysis_error,
            on_progress=self._on_analysis_progress,
            on_finished=self._on_analysis_finished,
            on_cancelled=lambda: self.toast("已取消分析。", ToastLevel.INFO),
        )

    def _cancel_analysis(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.progress_label.setText("正在取消…")

    def _on_analysis_progress(self, payload: object) -> None:
        try:
            count, path = payload  # type: ignore[misc]
        except (TypeError, ValueError):
            return
        self.progress_label.setText(f"已扫描 {format_count(int(count))} 个文件 · {truncate_text(str(path), 70)}")

    def _on_analysis_done(self, analysis: DirectoryAnalysis) -> None:
        self._analysis = analysis
        self._render_all()
        self.progress_label.setText(
            f"分析完成：{format_count(analysis.file_count)} 个文件 · "
            f"{format_bytes(analysis.total_size)} · 耗时 {analysis.duration:.1f} 秒"
        )

    def _on_analysis_error(self, message: str, _detail: str) -> None:
        self.progress_label.setText("")
        self.toast(message, ToastLevel.ERROR)

    def _on_analysis_finished(self) -> None:
        self._worker = None
        self.analyze_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _render_all(self) -> None:
        analysis = self._analysis
        if analysis is None:
            return
        disk = analysis.disk
        drive = analysis.root.anchor or str(analysis.root)
        self.disk_title.setText(f"{drive} 已使用 {disk.percent_used:.1f}%")
        self.disk_bar.set_value(disk.percent_used)
        percent = disk.percent_used
        self.disk_bar.set_color(
            token("error") if percent >= 95 else token("warning") if percent >= 85 else token("accent")
        )
        self.disk_detail.setText(
            f"总空间 {format_bytes(disk.total)} · 已用 {format_bytes(disk.used)} · 可用 {format_bytes(disk.free)}"
        )
        self.summary_detail.setText(
            f"当前目录 {analysis.root}：{format_count(analysis.file_count)} 个文件，"
            f"{format_bytes(analysis.total_size)}；子目录 {format_count(analysis.usage.dir_count)} 个。"
        )
        self._render_breadcrumb(analysis.root)
        self._render_children()
        self._render_largest()
        self._render_categories()

    def _render_breadcrumb(self, path: Path) -> None:
        while self.breadcrumb_layout.count():
            item = self.breadcrumb_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        parts = list(path.parts)
        current = Path(parts[0]) if parts else path
        button = GhostButton(str(current).rstrip("\\") or str(current), parent=self)
        button.clicked.connect(lambda _checked=False, p=current: self._start_analysis(p))
        self.breadcrumb_layout.addWidget(button)
        for part in parts[1:]:
            current = current / part
            separator = QLabel("›", self)
            separator.setProperty("role", "item-meta")
            self.breadcrumb_layout.addWidget(separator)
            child = GhostButton(part, parent=self)
            child.clicked.connect(lambda _checked=False, p=current: self._start_analysis(p))
            self.breadcrumb_layout.addWidget(child)
        self.breadcrumb_layout.addStretch(1)

    def _render_children(self) -> None:
        for widget in self._child_widgets:
            self.children_section.content.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._child_widgets.clear()
        analysis = self._analysis
        if analysis is None:
            return
        by = self.sort_row.value() or "size"
        children = analysis.child_rows(by=by)[:MAX_CHILD_ROWS]
        total = max(analysis.total_size, 1)
        for child in children:
            card = self._make_child_row(child, share=child.total_size / total * 100.0)
            self.children_section.content.addWidget(card)
            self._child_widgets.append(card)
        self.children_section.set_count(len(children))
        self.children_empty.setVisible(not children)

    def _make_child_row(self, child: DirectoryUsage, *, share: float) -> QWidget:
        card = Card(self, padding=(14, 12, 14, 12), spacing=8, hoverable=True)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        icon = QLabel(card)
        icon.setPixmap(self.icons.pixmap("folder", color=token("accent"), size=18))
        icon.setFixedSize(18, 18)
        row.addWidget(icon, 0)

        name = QLabel(truncate_text(child.name, 42), card)
        name.setProperty("role", "item-title")
        name.setToolTip(str(child.path))
        row.addWidget(name, 1)
        row.addWidget(StatusBadge(f"{format_count(child.file_count)} 个文件", "neutral", card), 0)
        row.addWidget(StatusBadge(format_bytes(child.total_size), "accent", card), 0)
        card.add_layout(row)

        bar = ProgressBar(card, height=6)
        bar.set_value(share)
        card.add(bar)

        open_button = GhostButton("打开", icon_name="folder-open", parent=card)
        open_button.clicked.connect(lambda _checked=False, p=child.path: self._drill(p))
        drill_button = GhostButton("下钻", icon_name="chevron-right", parent=card)
        drill_button.clicked.connect(lambda _checked=False, p=child.path: self._drill(p))
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)
        actions.addWidget(open_button, 0)
        actions.addWidget(drill_button, 0)
        card.add_layout(actions)
        return card

    def _render_largest(self) -> None:
        for widget in self._largest_widgets:
            self.largest_section.content.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._largest_widgets.clear()
        analysis = self._analysis
        if analysis is None:
            return
        for entry in analysis.largest_files:
            card = FileCard.from_entry(entry, parent=self)
            card.activated.connect(lambda path: open_in_explorer(path, select=True))
            self.largest_section.content.addWidget(card)
            self._largest_widgets.append(card)
        self.largest_section.set_count(len(analysis.largest_files))
        self.largest_empty.setVisible(not analysis.largest_files)

    def _render_categories(self) -> None:
        for widget in self._category_widgets:
            self.category_section.content.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._category_widgets.clear()
        analysis = self._analysis
        if analysis is None:
            return
        breakdown = analysis.category_breakdown()
        for category, size, share in breakdown:
            card = Card(self, padding=(14, 10, 14, 10), spacing=6)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            title = QLabel(category_label(category), card)
            title.setProperty("role", "item-title")
            row.addWidget(title, 1)
            value = QLabel(f"{format_bytes(size)} · {share:.1f}%", card)
            value.setProperty("role", "item-meta")
            row.addWidget(value, 0)
            card.add_layout(row)
            bar = ProgressBar(card, height=6)
            bar.set_value(share)
            card.add(bar)
            self.category_section.content.addWidget(card)
            self._category_widgets.append(card)
        self.category_section.set_count(len(breakdown))
        self.category_empty.setVisible(not breakdown)

    # ------------------------------------------------------------------
    def _drill(self, path: Path) -> None:
        self._start_analysis(path)

    def _open_current(self) -> None:
        text = self.path_picker.path()
        target = Path(text) if text else self.settings.download_dir
        if not target.exists():
            self.toast("目录不存在。", ToastLevel.WARNING)
            return
        open_in_explorer(target)
