"""自绘进度条（平滑变化，不使用假数据）。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.ui.theme import token


class ProgressBar(QWidget):
    """圆角进度条，数值变化带轻微过渡动画。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        height: int = 8,
        animated: bool = True,
    ) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._display = 0.0
        self._animated = animated
        self._color: str | None = None
        self._radius = height // 2
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(240)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._on_animation_value)

    # -- API ---------------------------------------------------------------
    def value(self) -> float:
        """当前目标进度（0–100）。"""
        return self._value

    def set_value(self, percent: float, *, animated: bool | None = None) -> None:
        """设置进度百分比。"""
        target = max(0.0, min(100.0, float(percent)))
        self._value = target
        use_animation = self._animated if animated is None else animated
        if not use_animation or not self.isVisible():
            self._animation.stop()
            self._display = target
            self.update()
            return
        if abs(target - self._display) < 0.05:
            return
        self._animation.stop()
        self._animation.setStartValue(self._display)
        self._animation.setEndValue(target)
        self._animation.start()

    def set_color(self, color: str | None) -> None:
        """自定义进度颜色（``None`` 使用主题强调色）。"""
        self._color = color
        self.update()

    def set_radius(self, radius: int) -> None:
        self._radius = max(1, radius)
        self.update()

    def _on_animation_value(self, value: object) -> None:
        try:
            self._display = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):  # pragma: no cover
            return
        self.update()

    # -- 绘制 --------------------------------------------------------------
    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0, 0, self.width(), self.height())
        radius = min(self._radius, rect.height() / 2)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(token("track", "#2E3238")))
        painter.drawRoundedRect(rect, radius, radius)

        if self._display > 0:
            width = rect.width() * (self._display / 100.0)
            width = max(width, rect.height())
            fill = QRectF(0, 0, min(width, rect.width()), rect.height())
            painter.setBrush(QColor(self._color or token("accent", "#3B82F6")))
            painter.drawRoundedRect(fill, radius, radius)
        painter.end()
