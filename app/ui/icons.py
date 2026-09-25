"""SVG 图标加载与着色。

图标文件位于 ``app/resources/icons``，使用 ``{color}`` 占位符标记可着色描边。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from app.core.common.logger import get_logger
from app.core.common.paths import icon_path

_log = get_logger("ui.icons")

DEFAULT_COLOR = "#A3AAB5"


class IconProvider:
    """按名称与颜色提供矢量图标，自带缓存。"""

    def __init__(self, theme: object | None = None) -> None:
        self._renderers: dict[tuple[str, str], QSvgRenderer] = {}
        self._pixmaps: dict[tuple[str, str, int, int], QPixmap] = {}
        self._color = DEFAULT_COLOR
        self._theme: object | None = theme

    def bind_theme(self, theme: object) -> None:
        """绑定主题管理器，使默认颜色与令牌保持一致。"""
        self._theme = theme
        token = getattr(theme, "token", None)
        if callable(token):
            self._color = token("text_secondary", DEFAULT_COLOR) or DEFAULT_COLOR
        self._pixmaps.clear()

    def color_for(self, token: str, fallback: str = DEFAULT_COLOR) -> str:
        """按主题令牌取色（未绑定主题时返回回退色）。"""
        theme = self._theme
        token_fn = getattr(theme, "token", None)
        if callable(token_fn):
            value = token_fn(token, fallback)
            if isinstance(value, str) and value:
                return value
        return fallback

    def set_default_color(self, color: str) -> None:
        """设置默认描边颜色（主题切换时调用）。"""
        self._color = color
        self._pixmaps.clear()

    # -- 渲染 --------------------------------------------------------------
    def _svg_text(self, name: str, color: str) -> str:
        path = icon_path(name)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            _log.warning("图标缺失：%s", path)
            return ""
        return text.replace("{color}", color)

    def pixmap(
        self,
        name: str,
        *,
        color: str | None = None,
        size: int = 18,
        dpr: float = 1.0,
    ) -> QPixmap:
        """返回已着色的位图。"""
        stroke = color or self._color
        key = (name, stroke, size, max(1, int(round(dpr * 100))))
        cached = self._pixmaps.get(key)
        if cached is not None:
            return cached

        text = self._svg_text(name, stroke)
        pixmap = QPixmap(int(size * dpr), int(size * dpr))
        pixmap.fill(Qt.GlobalColor.transparent)
        if text:
            renderer_key = (name, stroke)
            renderer = self._renderers.get(renderer_key)
            if renderer is None:
                renderer = QSvgRenderer(QByteArray(text.encode("utf-8")))
                self._renderers[renderer_key] = renderer
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            renderer.render(painter, QRectF(0, 0, size * dpr, size * dpr))
            painter.end()
        pixmap.setDevicePixelRatio(dpr)
        self._pixmaps[key] = pixmap
        return pixmap

    def icon(self, name: str, *, color: str | None = None, size: int = 18) -> QIcon:
        """返回适用于按钮 / 菜单的 :class:`QIcon`。"""
        return QIcon(self.pixmap(name, color=color, size=size))

    def size(self, name: str, pixels: int) -> QSize:  # pragma: no cover - 便捷方法
        return QSize(pixels, pixels)


@lru_cache(maxsize=1)
def default_icon_provider() -> IconProvider:
    """进程级图标提供器。"""
    return IconProvider()


def available_icons() -> list[str]:
    """列出可用图标名称（测试与调试用）。"""
    directory = icon_path("logo").parent
    if not directory.exists():  # pragma: no cover
        return []
    return sorted(path.stem for path in Path(directory).glob("*.svg"))
