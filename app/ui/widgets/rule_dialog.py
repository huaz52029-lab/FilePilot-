"""自定义整理规则编辑对话框。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from app.core.files.models import MATCH_FIELD_LABELS, MatchField
from app.core.files.rules import OrganizeRule
from app.ui.widgets.buttons import PrimaryButton, SecondaryButton
from app.ui.widgets.dialog import AppDialog
from app.ui.widgets.inputs import ComboRow, PathPicker, SpinRow, SwitchRow

_FIELD_HINTS: dict[MatchField, str] = {
    MatchField.EXTENSION: "例如：pdf 或 pdf,docx",
    MatchField.NAME_CONTAINS: "例如：Screenshot",
    MatchField.NAME_PREFIX: "例如：IMG_",
    MatchField.NAME_SUFFIX: "例如：_final.pdf",
    MatchField.SIZE_GREATER: "例如：500MB",
    MatchField.CATEGORY: "可选：image、video、document、audio、archive、program、code、other",
}


class RuleDialog(AppDialog):
    """新增 / 编辑整理规则。"""

    def __init__(
        self,
        rule: OrganizeRule | None = None,
        *,
        default_target: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            "编辑整理规则" if rule else "新建整理规则",
            subtitle="执行前会先生成预览，确认后才会移动文件",
            width=520,
            parent=parent,
        )
        self._rule = rule
        self._build(default_target=default_target)

    # ------------------------------------------------------------------
    def _build(self, *, default_target: Path | None) -> None:
        rule = self._rule

        name_row = QVBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(6)
        name_label = QLabel("规则名称", self)
        name_label.setProperty("role", "form-title")
        name_row.addWidget(name_label)
        self.name_edit = QLineEdit(rule.name if rule else "", self)
        self.name_edit.setPlaceholderText("例如：课程 PDF")
        name_row.addWidget(self.name_edit)
        self.add_layout(name_row)

        self.field_row = ComboRow(
            "匹配条件",
            [(field.value, MATCH_FIELD_LABELS[field]) for field in MatchField],
            parent=self,
        )
        if rule is not None:
            self.field_row.set_value(rule.field.value)
        self.field_row.changed.connect(self._update_hint)
        self.add_widget(self.field_row)

        value_row = QVBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(6)
        self.value_label = QLabel("条件取值", self)
        self.value_label.setProperty("role", "form-title")
        value_row.addWidget(self.value_label)
        self.value_edit = QLineEdit(rule.value if rule else "", self)
        value_row.addWidget(self.value_edit)
        self.hint_label = QLabel("", self)
        self.hint_label.setProperty("role", "form-description")
        self.hint_label.setWordWrap(True)
        value_row.addWidget(self.hint_label)
        self.add_layout(value_row)

        self.target_picker = PathPicker(
            str(rule.target_dir if rule else default_target or Path.home()),
            placeholder="选择目标文件夹",
            title="选择目标文件夹",
            parent=self,
        )
        target_row = QHBoxLayout()
        target_row.setContentsMargins(0, 0, 0, 0)
        target_label = QLabel("移动到", self)
        target_label.setProperty("role", "form-title")
        target_row.addWidget(target_label, 0)
        target_row.addWidget(self.target_picker, 1)
        self.add_layout(target_row)

        self.enabled_switch = SwitchRow(
            "启用该规则",
            "停用后规则不会参与匹配。",
            checked=rule.enabled if rule else True,
            parent=self,
        )
        self.add_widget(self.enabled_switch)

        self.priority_row = SpinRow(
            "优先级",
            minimum=1,
            maximum=999,
            value=rule.priority if rule else 100,
            description="数值越小越先匹配。",
            parent=self,
        )
        self.add_widget(self.priority_row)

        cancel = SecondaryButton("取消", parent=self)
        cancel.clicked.connect(self.reject)
        self.add_button(cancel)
        save = PrimaryButton("保存规则", icon_name="check", parent=self)
        save.setDefault(True)
        save.clicked.connect(self._on_save)
        self.add_button(save)
        self._update_hint(self.field_row.value())

    # ------------------------------------------------------------------
    def _update_hint(self, field_value: str) -> None:
        try:
            field = MatchField(field_value)
        except ValueError:  # pragma: no cover
            field = MatchField.EXTENSION
        self.hint_label.setText(_FIELD_HINTS.get(field, ""))
        self.value_edit.setPlaceholderText(_FIELD_HINTS.get(field, ""))

    def _on_save(self) -> None:
        rule = self.build_rule()
        error = rule.validate()
        if error:
            self.set_subtitle(error)
            return
        self._result = rule
        self.accept()

    def build_rule(self) -> OrganizeRule:
        """把界面输入转换为规则对象。"""
        try:
            field = MatchField(self.field_row.value())
        except ValueError:  # pragma: no cover
            field = MatchField.EXTENSION
        target = Path(self.target_picker.path()) if self.target_picker.path() else Path.home()
        if self._rule is not None:
            rule = self._rule
            rule.name = self.name_edit.text().strip()
            rule.field = field
            rule.value = self.value_edit.text().strip()
            rule.target_dir = target
            rule.enabled = self.enabled_switch.is_checked()
            rule.priority = self.priority_row.value()
            rule.touch()
            return rule
        return OrganizeRule(
            name=self.name_edit.text().strip(),
            field=field,
            value=self.value_edit.text().strip(),
            target_dir=target,
            enabled=self.enabled_switch.is_checked(),
            priority=self.priority_row.value(),
        )

    @property
    def result(self) -> OrganizeRule:
        return getattr(self, "_result", self.build_rule())
