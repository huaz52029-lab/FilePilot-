"""界面布局回归测试。

历史缺陷：某个控件以 ``parent=card`` 创建后忘记加入布局，会浮在卡片左上角
(0, 0)，既遮挡其它文字，又让用户完全点不到它（v0.1.1 的“空间分析 → 排序方式”）。

该测试遍历全部页面，检查是否存在**未纳入任何布局**的自家控件。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QMenu,
    QWidget,
)

from app.ui.navigation import PageId

#: Qt 内部自行管理子控件的父类（滚动区视口、下拉弹层、微调控件等）
INTERNAL_PARENT_TYPES = (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QLineEdit,
    QComboBox,
    QAbstractItemView,
    QMenu,
)

#: 有意脱离布局、由代码手动定位的浮层
ALLOWED_ORPHAN_CLASSES = {"ToastHost", "Toast"}


def _layout_widgets(layout) -> list[QWidget]:  # type: ignore[no-untyped-def]
    """递归收集布局（含嵌套布局）中的全部控件。"""
    found: list[QWidget] = []
    if layout is None:
        return found
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget()
        if widget is not None:
            found.append(widget)
        nested = item.layout()
        if nested is not None:
            found.extend(_layout_widgets(nested))
    return found


def _is_internal(child: QWidget, parent: QWidget) -> bool:
    """是否为 Qt 内部控件（无需参与布局检查）。"""
    if child.objectName().startswith("qt_"):
        return True
    if parent.objectName().startswith("qt_scrollarea"):
        return True
    return isinstance(parent, INTERNAL_PARENT_TYPES)


@pytest.fixture()
def window(qapp, data_dir: Path):  # type: ignore[no-untyped-def]
    from app.services.app_context import AppContext
    from app.ui.main_window import MainWindow
    from tests.ui_utils import dispose_window

    context = AppContext(console_log=False)
    win = MainWindow(context, qapp)
    win.resize(1280, 800)
    win.show()
    qapp.processEvents()
    try:
        yield win
    finally:
        dispose_window(qapp, win, context)


def test_no_widget_escapes_layout(window, qapp) -> None:  # type: ignore[no-untyped-def]
    """任何自家控件都必须处在布局中，否则会出现重叠且无法点击。"""
    problems: list[str] = []
    for page_id in PageId:
        window._switch_page(page_id)  # noqa: SLF001 - 布局检查
        for _ in range(3):
            qapp.processEvents()
        page = window.pages[page_id]
        for child in page.findChildren(QWidget):
            if child.isWindow() or child.__class__.__name__ in ALLOWED_ORPHAN_CLASSES:
                continue
            parent = child.parentWidget()
            if parent is None or _is_internal(child, parent):
                continue
            # 侧边栏的下载计数徽标由代码手动定位
            if parent.objectName() == "Sidebar" and child.__class__.__name__ == "StatusBadge":
                continue
            layout = parent.layout()
            if layout is None or child not in _layout_widgets(layout):
                problems.append(
                    f"{page_id.value}: {child.__class__.__name__}"
                    f"(parent={parent.__class__.__name__}, pos=({child.x()},{child.y()}))"
                )
    assert not problems, "存在脱离布局的控件（会重叠且无法交互）：\n" + "\n".join(problems)
