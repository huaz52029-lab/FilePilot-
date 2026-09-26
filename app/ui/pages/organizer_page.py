"""文件整理页面：选择目录 → 预览计划 → 确认执行；并管理自定义规则。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.common.helpers import format_bytes, open_in_explorer, truncate_text
from app.core.files.categories import all_categories, category_label
from app.core.files.models import OrganizePlan
from app.core.files.renamer import RenameMode, RenamePlan, RenameStatus, SortKey
from app.core.files.rules import OrganizeRule
from app.ui.navigation import PageId
from app.ui.pages.base_page import BasePage
from app.ui.widgets.badge import StatusBadge
from app.ui.widgets.buttons import DangerButton, GhostButton, PrimaryButton, SecondaryButton
from app.ui.widgets.cards import Card, SectionHeader
from app.ui.widgets.dialog import confirm, show_result_details
from app.ui.widgets.empty_state import EmptyState
from app.ui.widgets.file_card import FileCard
from app.ui.widgets.inputs import ComboRow, PathPicker, SwitchRow
from app.ui.widgets.rule_dialog import RuleDialog
from app.ui.widgets.section import Section
from app.ui.widgets.toast import ToastLevel

MAX_PREVIEW_ROWS = 200
MAX_RENAME_ROWS = 200

RENAME_STATUS_TONES: dict[RenameStatus, str] = {
    RenameStatus.READY: "accent",
    RenameStatus.CONFLICT: "error",
    RenameStatus.UNCHANGED: "neutral",
    RenameStatus.INVALID: "error",
}


class OrganizerPage(BasePage):
    """文件整理页面。"""

    page_id = PageId.ORGANIZER
    title = "文件整理"
    subtitle = "按类型或自定义规则整理文件夹；执行前始终先预览，不删除任何文件"

    def __init__(self, context, parent: QWidget | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(context, parent)
        self._plan: OrganizePlan | None = None
        self._preview_widgets: list[QWidget] = []
        self._rule_widgets: list[QWidget] = []
        self._worker = None
        self._rename_plan: RenamePlan | None = None
        self._rename_rows: list[QWidget] = []
        self._rename_worker = None
        self._build()
        self._connect_service()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def _build(self) -> None:
        open_folder = GhostButton("打开所在目录", icon_name="folder-open", parent=self)
        open_folder.clicked.connect(self._open_target_folder)
        self.add_action(open_folder)

        content = self.add_scrollable()

        target_card = Card(padding=(20, 18, 20, 18), spacing=14)
        target_card.add(
            SectionHeader(
                "整理位置",
                "选择需要整理的文件夹；文件只会被移动，不会被删除",
                icon="folder",
                parent=target_card,
            )
        )
        self.path_picker = PathPicker(
            str(self.settings.organize_root or self.settings.download_dir),
            placeholder="请选择需要整理的文件夹",
            title="选择需要整理的文件夹",
            parent=target_card,
        )
        target_card.add(self.path_picker)
        self.mode_row = ComboRow(
            "整理方式",
            [("category", "默认分类规则（图片 / 视频 / 文档 …）"), ("rules", "自定义整理规则")],
            description="自定义规则按优先级依次匹配，未命中的文件保持不动。",
            parent=target_card,
        )
        target_card.add(self.mode_row)
        self.recursive_switch = SwitchRow(
            "包含子目录",
            "开启后会递归扫描所有子文件夹。",
            checked=True,
            parent=target_card,
        )
        target_card.add(self.recursive_switch)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 4, 0, 0)
        button_row.setSpacing(8)
        self.preview_button = PrimaryButton("预览整理计划", icon_name="search", parent=target_card)
        self.preview_button.clicked.connect(self._preview)
        button_row.addWidget(self.preview_button, 0)
        self.cancel_scan_button = SecondaryButton("取消", icon_name="close", parent=target_card)
        self.cancel_scan_button.clicked.connect(self._cancel_scan)
        self.cancel_scan_button.setEnabled(False)
        button_row.addWidget(self.cancel_scan_button, 0)
        button_row.addStretch(1)
        target_card.add_layout(button_row)
        self.progress_label = QLabel("", target_card)
        self.progress_label.setProperty("role", "hint")
        self.progress_label.setWordWrap(True)
        target_card.add(self.progress_label)
        content.addWidget(target_card)

        category_card = Card(padding=(20, 18, 20, 18), spacing=12)
        category_card.add(
            SectionHeader("默认分类", "勾选需要参与整理的分类", icon="copy", parent=category_card)
        )
        grid = QHBoxLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)
        self.category_checks: dict[str, QCheckBox] = {}
        left = QVBoxLayout()
        right = QVBoxLayout()
        for index, category in enumerate(all_categories()):
            checkbox = QCheckBox(category_label(category), category_card)
            checkbox.setChecked(True)
            self.category_checks[category.value] = checkbox
            (left if index % 2 == 0 else right).addWidget(checkbox)
        grid.addLayout(left, 1)
        grid.addLayout(right, 1)
        category_card.add_layout(grid)
        content.addWidget(category_card)

        self.rules_section = Section(
            "自定义规则", "规则按优先级从上到下匹配，先命中者生效", icon="settings", parent=self
        )
        add_rule = SecondaryButton("添加规则", icon_name="plus", parent=self)
        add_rule.clicked.connect(self._add_rule)
        self.rules_section.header.add_action(add_rule)
        self.rules_empty = EmptyState(
            "还没有自定义规则",
            "例如：扩展名 = pdf → 移动到 D:\\学习资料\\PDF",
            icon="settings",
        )
        self.rules_section.set_empty_widget(self.rules_empty)
        content.addWidget(self.rules_section)

        self.preview_section = Section("整理预览", "确认后才会真正移动文件", icon="copy", parent=self)
        self.preview_empty = EmptyState(
            "尚未生成整理计划",
            "选择文件夹后点击“预览整理计划”，这里会列出每个文件的去向与冲突情况。",
            icon="file",
        )
        self.preview_section.set_empty_widget(self.preview_empty)
        self.summary_label = QLabel("", self)
        self.summary_label.setProperty("role", "item-meta")
        self.summary_label.setWordWrap(True)
        self.summary_label.setVisible(False)
        self.preview_section.content.addWidget(self.summary_label)
        content.addWidget(self.preview_section)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 4, 0, 0)
        action_row.setSpacing(8)
        self.cancel_button = SecondaryButton("取消", parent=self)
        self.cancel_button.clicked.connect(self._cancel_plan)
        self.cancel_button.setEnabled(False)
        action_row.addWidget(self.cancel_button, 0)
        self.execute_button = DangerButton("确认整理", icon_name="check", parent=self)
        self.execute_button.setEnabled(False)
        self.execute_button.clicked.connect(self._execute)
        action_row.addWidget(self.execute_button, 0)
        action_row.addStretch(1)
        self.preview_section.content.addLayout(action_row)

        self._build_rename_section(content)
        content.addStretch(1)

    # ------------------------------------------------------------------
    # 批量重命名（表单 + 预览）
    # ------------------------------------------------------------------
    def _build_rename_section(self, content: QVBoxLayout) -> None:
        """构建“批量重命名”表单与预览列表。"""
        card = Card(padding=(20, 18, 20, 18), spacing=14)
        card.add(
            SectionHeader(
                "批量重命名",
                "按顺序自动编号；执行前必须预览，绝不覆盖已有文件",
                icon="hash",
                parent=card,
            )
        )
        self.rename_path_picker = PathPicker(
            str(self.settings.organize_root or self.settings.download_dir),
            placeholder="选择需要重命名的文件夹",
            title="选择需要重命名的文件夹",
            parent=card,
        )
        card.add(self.rename_path_picker)

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(16)
        mode_label = QLabel("命名方式", card)
        mode_label.setProperty("role", "form-title")
        mode_row.addWidget(mode_label, 0)
        self.rename_mode_prefix = QRadioButton("名称 + 编号（照片0001.jpg）", card)
        self.rename_mode_prefix.setChecked(True)
        self.rename_mode_number = QRadioButton("纯编号（0001.jpg）", card)
        mode_row.addWidget(self.rename_mode_prefix, 0)
        mode_row.addWidget(self.rename_mode_number, 0)
        mode_row.addStretch(1)
        card.add_layout(mode_row)

        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)

        name_label = QLabel("名称", card)
        name_label.setProperty("role", "form-title")
        self.rename_prefix_edit = QLineEdit("照片", card)
        self.rename_prefix_edit.setPlaceholderText("例如：照片（纯编号模式下可留空）")
        form.addWidget(name_label, 0, 0)
        form.addWidget(self.rename_prefix_edit, 0, 1)

        start_label = QLabel("起始编号", card)
        start_label.setProperty("role", "form-title")
        self.rename_start_spin = QSpinBox(card)
        self.rename_start_spin.setRange(1, 1_000_000)
        self.rename_start_spin.setValue(1)
        form.addWidget(start_label, 0, 2)
        form.addWidget(self.rename_start_spin, 0, 3)

        padding_label = QLabel("编号位数", card)
        padding_label.setProperty("role", "form-title")
        self.rename_padding_spin = QSpinBox(card)
        self.rename_padding_spin.setRange(1, 10)
        self.rename_padding_spin.setValue(4)
        form.addWidget(padding_label, 1, 0)
        form.addWidget(self.rename_padding_spin, 1, 1)

        extension_label = QLabel("仅处理扩展名", card)
        extension_label.setProperty("role", "form-title")
        self.rename_extension_edit = QLineEdit(card)
        self.rename_extension_edit.setPlaceholderText("可选，例如 jpg,png（留空表示全部文件）")
        form.addWidget(extension_label, 1, 2)
        form.addWidget(self.rename_extension_edit, 1, 3)
        card.add_layout(form)

        self.rename_sort_row = ComboRow(
            "排序方式",
            [(key.value, key.label) for key in SortKey],
            description="编号顺序由排序结果决定，默认按文件名自然排序。",
            parent=card,
        )
        card.add(self.rename_sort_row)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 4, 0, 0)
        buttons.setSpacing(8)
        self.rename_preview_button = SecondaryButton("预览", icon_name="search", parent=card)
        self.rename_preview_button.clicked.connect(self._preview_rename)
        buttons.addWidget(self.rename_preview_button, 0)
        self.rename_execute_button = PrimaryButton("开始重命名", icon_name="check", parent=card)
        self.rename_execute_button.clicked.connect(self._execute_rename)
        self.rename_execute_button.setEnabled(False)
        buttons.addWidget(self.rename_execute_button, 0)
        self.rename_cancel_button = GhostButton("取消", icon_name="close", parent=card)
        self.rename_cancel_button.clicked.connect(self._cancel_rename)
        self.rename_cancel_button.setEnabled(False)
        buttons.addWidget(self.rename_cancel_button, 0)
        buttons.addStretch(1)
        card.add_layout(buttons)

        self.rename_progress_label = QLabel("", card)
        self.rename_progress_label.setProperty("role", "hint")
        self.rename_progress_label.setWordWrap(True)
        card.add(self.rename_progress_label)
        content.addWidget(card)

        self.rename_section = Section(
            "重命名预览", "原文件名 → 新文件名（预览不会修改任何文件）", icon="file", parent=self
        )
        self.rename_empty = EmptyState(
            "尚未生成重命名预览",
            "设置命名方式后点击“预览”，这里会列出每个文件的新旧名称与冲突情况。",
            icon="file",
        )
        self.rename_section.set_empty_widget(self.rename_empty)
        self.rename_summary_label = QLabel("", self)
        self.rename_summary_label.setProperty("role", "item-meta")
        self.rename_summary_label.setWordWrap(True)
        self.rename_summary_label.setVisible(False)
        self.rename_section.content.addWidget(self.rename_summary_label)
        content.addWidget(self.rename_section)

    def _connect_service(self) -> None:
        library = self.context.library
        library.organize_preview_ready.connect(self._on_preview_ready)
        library.organize_finished.connect(self._on_organize_finished)
        library.rename_preview_ready.connect(self._on_rename_preview_ready)
        library.rename_finished.connect(self._on_rename_finished)
        library.rename_progress.connect(self._on_rename_progress)
        library.failed.connect(self._on_failed)
        library.progress.connect(self._on_progress)

    # ------------------------------------------------------------------
    # 页面生命周期
    # ------------------------------------------------------------------
    def on_show(self) -> None:
        self._refresh_rules()

    # ------------------------------------------------------------------
    # 预览与执行
    # ------------------------------------------------------------------
    def _preview(self) -> None:
        text = self.path_picker.path()
        self.progress_label.setText("准备扫描…")
        if not text:
            self.toast("请先选择一个文件夹。", ToastLevel.WARNING)
            return
        root = Path(text)
        if not root.exists():
            self.toast("该文件夹不存在。", ToastLevel.WARNING)
            return
        self.settings.organize_root = root
        mode = self.mode_row.value()
        if mode == "rules" and not self.context.library.list_rules(enabled_only=True):
            self.toast("还没有启用中的自定义规则，请先添加规则。", ToastLevel.WARNING)
            return
        categories = [
            value for value, checkbox in self.category_checks.items() if checkbox.isChecked()
        ]
        self.preview_button.setEnabled(False)
        self.cancel_scan_button.setEnabled(True)
        self.progress_label.setText("正在扫描并生成整理计划…")
        self._worker = self.context.library.preview_organize(
            root,
            mode=mode,
            categories=[category for category in all_categories() if category.value in categories],
        )

    def _cancel_scan(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.progress_label.setText("正在取消…")

    def _on_progress(self, payload: object) -> None:
        if isinstance(payload, tuple) and len(payload) == 2:
            count, path = payload
            self.progress_label.setText(f"已扫描 {count} 个文件 · {truncate_text(str(path), 60)}")
        elif isinstance(payload, str):
            self.progress_label.setText(payload)

    def _on_preview_ready(self, plan: OrganizePlan) -> None:
        self._worker = None
        self.preview_button.setEnabled(True)
        self.cancel_scan_button.setEnabled(False)
        self.progress_label.setText(
            f"预览完成：扫描 {plan.scanned_files} 个文件，计划移动 {plan.move_count} 个。"
        )
        self._set_plan(plan)
        if plan.move_count == 0 and plan.conflict_count == 0:
            self.toast("该目录已整理完毕，没有需要移动的文件。", ToastLevel.INFO)
        else:
            self.toast(
                f"已生成整理计划：{plan.move_count} 个文件待移动"
                + (f"，{plan.conflict_count} 个冲突" if plan.conflict_count else ""),
                ToastLevel.SUCCESS,
            )

    def _execute(self) -> None:
        plan = self._plan
        if plan is None or plan.move_count == 0:
            self.toast("没有可执行的整理动作。", ToastLevel.WARNING)
            return
        if not confirm(
            self,
            "确认整理",
            f"将移动 {plan.move_count} 个文件（合计 {format_bytes(plan.total_bytes)}），"
            f"涉及 {len(plan.category_summary())} 个分类。\n"
            "目标位置已存在同名文件时会自动跳过，不会覆盖。",
            confirm_text="确认整理",
            danger=True,
        ):
            return
        self.execute_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.progress_label.setText("正在整理…")
        self._worker = self.context.library.execute_organize(plan)

    def _on_organize_finished(self, result) -> None:  # type: ignore[no-untyped-def]
        self._worker = None
        self.preview_button.setEnabled(True)
        self.progress_label.setText(
            f"整理完成：移动 {result.moved} 个，跳过 {result.skipped} 个，失败 {result.failed} 个。"
        )
        level = ToastLevel.SUCCESS if result.failed == 0 else ToastLevel.WARNING
        self.toast(
            f"整理完成：移动 {result.moved} 个文件"
            + (f"，{result.failed} 个失败" if result.failed else ""),
            level,
        )
        self._set_plan(None)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self.preview_button.setEnabled(True)
        self.cancel_scan_button.setEnabled(False)
        self.progress_label.setText("")
        self.toast(message, ToastLevel.ERROR)

    def _cancel_plan(self) -> None:
        self._set_plan(None)
        self.progress_label.setText("已取消整理计划。")

    # ------------------------------------------------------------------
    # 规则管理
    # ------------------------------------------------------------------
    def _refresh_rules(self) -> None:
        for widget in self._rule_widgets:
            self.rules_section.content.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._rule_widgets.clear()

        rules = self.context.library.list_rules()
        for rule in rules:
            card = self._make_rule_card(rule)
            self.rules_section.content.addWidget(card)
            self._rule_widgets.append(card)
        self.rules_section.set_count(len(rules))
        self.rules_empty.setVisible(not rules)

    def _make_rule_card(self, rule: OrganizeRule) -> QWidget:
        card = Card(self, padding=(14, 12, 14, 12), spacing=6, hoverable=True)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)
        name = QLabel(truncate_text(rule.name, 40), card)
        name.setProperty("role", "item-title")
        top.addWidget(name, 1)
        top.addWidget(
            StatusBadge("启用" if rule.enabled else "已停用", "success" if rule.enabled else "neutral"),
            0,
        )
        top.addWidget(StatusBadge(f"优先级 {rule.priority}", "neutral"), 0)
        card.add_layout(top)

        condition = QLabel(f"条件：{rule.condition_text}", card)
        condition.setProperty("role", "item-subtitle")
        card.add(condition)
        target = QLabel(f"目标：{rule.target_text}", card)
        target.setProperty("role", "item-meta")
        target.setToolTip(rule.target_text)
        card.add(target)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 4, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)
        edit = SecondaryButton("编辑", parent=card)
        edit.clicked.connect(lambda _checked=False, item=rule: self._edit_rule(item))
        actions.addWidget(edit, 0)
        toggle = GhostButton("停用" if rule.enabled else "启用", parent=card)
        toggle.clicked.connect(lambda _checked=False, item=rule: self._toggle_rule(item))
        actions.addWidget(toggle, 0)
        remove = GhostButton("删除", icon_name="trash", parent=card)
        remove.clicked.connect(lambda _checked=False, item=rule: self._delete_rule(item))
        actions.addWidget(remove, 0)
        card.add_layout(actions)
        return card

    def _add_rule(self) -> None:
        dialog = RuleDialog(default_target=self.settings.organize_root, parent=self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self.context.library.save_rule(dialog.result)
            self._refresh_rules()
            self.toast("规则已保存。", ToastLevel.SUCCESS)

    def _edit_rule(self, rule: OrganizeRule) -> None:
        dialog = RuleDialog(rule, parent=self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self.context.library.save_rule(dialog.result)
            self._refresh_rules()
            self.toast("规则已更新。", ToastLevel.SUCCESS)

    def _toggle_rule(self, rule: OrganizeRule) -> None:
        rule.enabled = not rule.enabled
        rule.touch()
        self.context.library.save_rule(rule)
        self._refresh_rules()

    def _delete_rule(self, rule: OrganizeRule) -> None:
        if not confirm(
            self,
            "删除规则",
            f"确定删除规则“{rule.name}”吗？已整理的文件不会受影响。",
            confirm_text="删除",
            danger=True,
        ):
            return
        self.context.library.delete_rule(rule.rule_id)
        self._refresh_rules()
        self.toast("规则已删除。", ToastLevel.SUCCESS)

    # ------------------------------------------------------------------
    # 计划展示
    # ------------------------------------------------------------------
    def _set_plan(self, plan: OrganizePlan | None) -> None:
        for widget in self._preview_widgets:
            self.preview_section.content.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._preview_widgets.clear()
        self._plan = plan

        if plan is None:
            self.summary_label.setText("")
            self.summary_label.setVisible(False)
            self.preview_empty.setVisible(True)
            self.execute_button.setEnabled(False)
            self.cancel_button.setEnabled(False)
            self.preview_section.set_count(0)
            return

        self.preview_empty.setVisible(False)
        summary = (
            f"共扫描 {plan.scanned_files} 个文件 · 计划移动 {plan.move_count} 个 · "
            f"冲突 {plan.conflict_count} 个 · 合计 {format_bytes(plan.total_bytes)}"
        )
        summary_by_category = plan.category_summary()
        if summary_by_category:
            detail = "、".join(
                f"{category_label(category)} {count} 个"
                for category, count in sorted(
                    summary_by_category.items(), key=lambda item: item[1], reverse=True
                )
            )
            summary = f"{summary}\n{detail}"
        self.summary_label.setText(summary)
        self.summary_label.setVisible(True)

        for action in plan.actions[:MAX_PREVIEW_ROWS]:
            card = FileCard(
                action.source,
                size=action.size,
                category=action.category,
                subtitle=f"→ {action.target.parent}",
                badge_text=action.status_label,
                badge_tone="success" if action.status.value == "ready" else "warning",
                parent=self,
            )
            self.preview_section.content.addWidget(card)
            self._preview_widgets.append(card)
        self.preview_section.set_count(plan.total)
        self.execute_button.setEnabled(plan.move_count > 0)
        self.cancel_button.setEnabled(True)

    def _open_target_folder(self) -> None:
        text = self.path_picker.path()
        if not text:
            self.toast("请先选择文件夹。", ToastLevel.WARNING)
            return
        target = Path(text)
        if not target.exists():
            self.toast("该文件夹不存在。", ToastLevel.WARNING)
            return
        open_in_explorer(target)

    # ------------------------------------------------------------------
    # 批量重命名：交互
    # ------------------------------------------------------------------
    def _rename_mode(self) -> RenameMode:
        return (
            RenameMode.NUMBER_ONLY
            if self.rename_mode_number.isChecked()
            else RenameMode.PREFIX_NUMBER
        )

    def _rename_extensions(self) -> list[str]:
        raw = self.rename_extension_edit.text().replace("，", ",")
        return [item.strip() for item in raw.split(",") if item.strip()]

    def _preview_rename(self) -> None:
        text = self.rename_path_picker.path()
        if not text:
            self.toast("请先选择需要重命名的文件夹。", ToastLevel.WARNING)
            return
        root = Path(text)
        if not root.exists():
            self.toast("该文件夹不存在。", ToastLevel.WARNING)
            return
        mode = self._rename_mode()
        if mode is RenameMode.PREFIX_NUMBER and not self.rename_prefix_edit.text().strip():
            self.toast("请填写名称，或改用“纯编号”模式。", ToastLevel.WARNING)
            return
        self.rename_preview_button.setEnabled(False)
        self.rename_execute_button.setEnabled(False)
        self.rename_progress_label.setText("正在生成重命名预览…")
        self._rename_worker = self.context.library.preview_rename(
            root,
            mode=mode,
            prefix=self.rename_prefix_edit.text(),
            start=self.rename_start_spin.value(),
            padding=self.rename_padding_spin.value(),
            sort_key=SortKey(self.rename_sort_row.value() or "name"),
            extensions=self._rename_extensions(),
        )

    def _on_rename_preview_ready(self, plan: RenamePlan) -> None:
        self._rename_worker = None
        self.rename_preview_button.setEnabled(True)
        self._set_rename_plan(plan)
        if plan.total == 0:
            self.rename_progress_label.setText(
                f"扫描了 {plan.scanned_files} 个文件，但没有需要重命名的文件。"
            )
            self.toast("没有找到需要重命名的文件。", ToastLevel.INFO)
            return
        self.rename_progress_label.setText(
            f"预览完成：共 {plan.total} 个文件，待重命名 {plan.ready_count} 个"
            + (f"，冲突 {plan.conflict_count} 个" if plan.conflict_count else "")
            + (f"，名称未变化 {plan.unchanged_count} 个" if plan.unchanged_count else "")
        )
        if plan.conflict_count:
            self.toast(
                f"有 {plan.conflict_count} 个文件的目标名称已存在，已阻止执行。请调整名称或起始编号。",
                ToastLevel.ERROR,
            )
        elif plan.ready_count:
            self.toast(f"预览完成：{plan.ready_count} 个文件待重命名。", ToastLevel.SUCCESS)

    def _set_rename_plan(self, plan: RenamePlan | None) -> None:
        for widget in self._rename_rows:
            self.rename_section.content.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._rename_rows.clear()
        self._rename_plan = plan

        if plan is None:
            self.rename_summary_label.setText("")
            self.rename_summary_label.setVisible(False)
            self.rename_empty.setVisible(True)
            self.rename_section.set_count(0)
            self.rename_execute_button.setEnabled(False)
            return

        self.rename_empty.setVisible(False)
        summary = (
            f"共 {plan.total} 个文件 · 待重命名 {plan.ready_count} 个 · "
            f"冲突 {plan.conflict_count} 个 · 未变化 {plan.unchanged_count} 个"
        )
        self.rename_summary_label.setText(summary)
        self.rename_summary_label.setVisible(True)

        for old_name, new_name, status in plan.preview_rows(MAX_RENAME_ROWS):
            row = Card(self, padding=(12, 8, 12, 8), spacing=2, hoverable=True)
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(10)
            original = QLabel(truncate_text(old_name, 46), row)
            original.setProperty("role", "item-subtitle")
            original.setToolTip(old_name)
            line.addWidget(original, 1)
            arrow = QLabel("→", row)
            arrow.setProperty("role", "item-meta")
            line.addWidget(arrow, 0)
            changed = QLabel(truncate_text(new_name, 46), row)
            changed.setProperty("role", "item-title")
            changed.setToolTip(new_name)
            line.addWidget(changed, 1)
            line.addWidget(StatusBadge(status.label, RENAME_STATUS_TONES.get(status, "neutral")), 0)
            row.add_layout(line)
            self.rename_section.content.addWidget(row)
            self._rename_rows.append(row)
        self.rename_section.set_count(plan.total)
        self.rename_execute_button.setEnabled(plan.can_execute)

    def _execute_rename(self) -> None:
        plan = self._rename_plan
        if plan is None or not plan.can_execute:
            self.toast("请先生成可执行的重命名预览。", ToastLevel.WARNING)
            return
        if not confirm(
            self,
            "确认批量重命名",
            f"将重命名 {plan.ready_count} 个文件：\n"
            f"目录：{plan.root}\n"
            f"目标名称示例：{plan.ready_actions[0].target.name}\n\n"
            "同名文件不会被覆盖；执行后可在历史记录中查看结果。",
            confirm_text="开始重命名",
            danger=True,
        ):
            return
        self.rename_execute_button.setEnabled(False)
        self.rename_preview_button.setEnabled(False)
        self.rename_cancel_button.setEnabled(True)
        self.rename_progress_label.setText("正在重命名…")
        self._rename_worker = self.context.library.execute_rename(plan)

    def _cancel_rename(self) -> None:
        if self._rename_worker is not None:
            self._rename_worker.cancel()
            self.rename_progress_label.setText("正在取消…")
            return
        self._set_rename_plan(None)
        self.rename_progress_label.setText("已清除重命名预览。")

    def _on_rename_progress(self, index: int, total: int, path: str) -> None:
        self.rename_progress_label.setText(
            f"正在重命名 {index} / {total} · {truncate_text(Path(path).name, 50)}"
        )

    def _on_rename_finished(self, result) -> None:  # type: ignore[no-untyped-def]
        self._rename_worker = None
        self.rename_preview_button.setEnabled(True)
        self.rename_cancel_button.setEnabled(False)
        self.rename_execute_button.setEnabled(False)
        self.rename_progress_label.setText(f"重命名完成：{result.summary}")
        self._set_rename_plan(None)
        level = ToastLevel.SUCCESS if result.success else ToastLevel.WARNING
        self.toast(f"重命名完成：{result.summary}", level)
        if result.errors:
            show_result_details(
                self,
                "部分文件重命名失败",
                f"成功 {result.renamed} 个，失败 {result.failed} 个，跳过 {result.skipped} 个",
                [(path.name, reason) for path, reason in result.errors],
            )
