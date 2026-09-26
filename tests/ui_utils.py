"""GUI 测试公共工具：事件泵与确定性的窗口释放。"""

from __future__ import annotations

import gc
import time
from collections.abc import Callable


def pump_until(
    qapp,  # type: ignore[no-untyped-def]
    predicate: Callable[[], bool],
    *,
    timeout: float = 30.0,
    interval: float = 0.03,
) -> bool:
    """在保持 Qt 事件循环运转的前提下等待条件成立。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    return False


def dispose_window(qapp, window, context) -> None:  # type: ignore[no-untyped-def]
    """确定性释放窗口与应用上下文。

    Qt 对象必须在 ``QApplication`` 之前销毁，否则解释器退出阶段可能出现
    访问冲突 / 堆损坏。这里显式关闭窗口、触发 deleteLater 并回收垃圾。
    """
    try:
        window.close()
        window.deleteLater()
        for _ in range(3):
            qapp.processEvents()
    finally:
        context.shutdown()
    gc.collect()
