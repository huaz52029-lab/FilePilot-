"""设置页使用的表单行组件。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.ui.widgets.buttons import SecondaryButton


class LabeledRow(QWidget):
    """左标题 + 右控件的通用设置行。"""

    def __init__(
        self,
        title: str,
        description: str = "",
        *,
        control: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        title_label = QLabel(title, self)
        title_label.setProperty("role", "form-title")
        text_column.addWidget(title_label)
        if description:
            description_label = QLabel(description, self)
            description_label.setProperty("role", "form-description")
            description_label.setWordWrap(True)
            text_column.addWidget(description_label)
        layout.addLayout(text_column, 1)

        if control is not None:
            control.setParent(self)
            layout.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._control = control

    @property
    def control(self) -> QWidget | None:
        return self._control


class SwitchRow(QWidget):
    """复选开关行（用例：自动恢复下载）。"""

    toggled = Signal(bool)

    def __init__(
        self,
        title: str,
        description: str = "",
        *,
        checked: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        self._title = QLabel(title, self)
        self._title.setProperty("role", "form-title")
        text_column.addWidget(self._title)
        self._description = QLabel(description, self)
        self._description.setProperty("role", "form-description")
        self._description.setWordWrap(True)
        self._description.setVisible(bool(description))
        text_column.addWidget(self._description)
        layout.addLayout(text_column, 1)

        self._checkbox = QCheckBox(self)
        self._checkbox.setChecked(checked)
        self._checkbox.toggled.connect(self.toggled.emit)
        layout.addWidget(self._checkbox, 0, Qt.AlignmentFlag.AlignRight)

    def is_checked(self) -> bool:
        return self._checkbox.isChecked()

    def set_checked(self, value: bool) -> None:
        self._checkbox.blockSignals(True)
        self._checkbox.setChecked(bool(value))
        self._checkbox.blockSignals(False)

    def set_description(self, text: str) -> None:
        self._description.setText(text)
        self._description.setVisible(bool(text))


class ComboRow(LabeledRow):
    """下拉选择行。"""

    changed = Signal(str)

    def __init__(
        self,
        title: str,
        options: list[tuple[str, str]],
        *,
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        combo = QComboBox()
        combo.setMinimumWidth(200)
        for value, label in options:
            combo.addItem(label, value)
        combo.currentIndexChanged.connect(
            lambda _index: self.changed.emit(self.value())
        )
        super().__init__(title, description, control=combo, parent=parent)
        self._combo = combo

    def value(self) -> str:
        return str(self._combo.currentData())

    def set_value(self, value: str) -> None:
        index = self._combo.findData(value)
        if index >= 0:
            self._combo.blockSignals(True)
            self._combo.setCurrentIndex(index)
            self._combo.blockSignals(False)


class SpinRow(LabeledRow):
    """整数输入行。"""

    changed = Signal(int)

    def __init__(
        self,
        title: str,
        *,
        minimum: int,
        maximum: int,
        value: int,
        suffix: str = "",
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(suffix)
        spin.setMinimumWidth(120)
        super().__init__(title, description, control=spin, parent=parent)
        self._spin = spin
        # 必须在 QWidget 初始化完成后再连接自定义信号
        spin.valueChanged.connect(self.changed.emit)

    def value(self) -> int:
        return int(self._spin.value())

    def set_value(self, value: int) -> None:
        self._spin.blockSignals(True)
        self._spin.setValue(int(value))
        self._spin.blockSignals(False)


class PathPicker(QWidget):
    """目录选择控件：只读输入框 + 浏览按钮。"""

    changed = Signal(str)

    def __init__(
        self,
        path: str = "",
        *,
        placeholder: str = "请选择目录",
        title: str = "选择文件夹",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._edit = QLineEdit(path, self)
        self._edit.setPlaceholderText(placeholder)
        self._edit.setReadOnly(True)
        self._edit.setMinimumWidth(260)
        layout.addWidget(self._edit, 1)
        self._button = SecondaryButton("浏览…", icon_name="folder-open", parent=self)
        self._button.clicked.connect(self._browse)
        layout.addWidget(self._button, 0)

    def path(self) -> str:
        return self._edit.text()

    def set_path(self, value: str | Path) -> None:
        text = str(value)
        if text == self._edit.text():
            return
        self._edit.setText(text)
        self.changed.emit(text)

    def set_title(self, title: str) -> None:
        self._title = title

    def _browse(self) -> None:
        start = self._edit.text() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, self._title, start)
        if selected:
            self.set_path(selected)
