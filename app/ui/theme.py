"""主题系统。

样式表文件（``resources/styles/*.qss``）使用 ``string.Template`` 占位符
（``$token``），由 :class:`ThemeManager` 注入设计令牌后应用到整个应用。
"""

from __future__ import annotations

import os
from string import Template
from typing import Final

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from app.core.common.logger import get_logger
from app.core.common.paths import resource_path, stylesheet_path
from app.services.settings_service import AppSettings, ThemeMode

_log = get_logger("ui.theme")

#: 深色主题（默认）
TOKENS_DARK: Final[dict[str, str]] = {
    "window_bg": "#1A1C20",
    "surface": "#1F2227",
    "sidebar_bg": "#1B1E22",
    "titlebar_bg": "#1F2227",
    "card_bg": "#24272C",
    "card_bg_hover": "#2A2E34",
    "card_border": "#31353C",
    "divider": "#2C3036",
    "text": "#E9EBEF",
    "text_secondary": "#A3AAB5",
    "text_muted": "#767D89",
    "text_disabled": "#5A606B",
    "accent": "#3B82F6",
    "accent_hover": "#5590F8",
    "accent_pressed": "#2F6FD8",
    "accent_soft": "#1E3355",
    "accent_text": "#FFFFFF",
    "success": "#3FBF7F",
    "success_soft": "#17352A",
    "warning": "#E0A33E",
    "warning_soft": "#3A2E15",
    "error": "#E25765",
    "error_soft": "#3A1D22",
    "info": "#4C9EF5",
    "input_bg": "#1D2024",
    "input_border": "#343941",
    "input_focus": "#3B82F6",
    "hover_bg": "#2A2E34",
    "pressed_bg": "#31353C",
    "selected_bg": "#233A5E",
    "scrollbar": "#3A3F47",
    "scrollbar_hover": "#4A505A",
    "shadow": "#101114",
    "track": "#2E3238",
}

#: 浅色主题
TOKENS_LIGHT: Final[dict[str, str]] = {
    "window_bg": "#F3F5F8",
    "surface": "#FFFFFF",
    "sidebar_bg": "#FFFFFF",
    "titlebar_bg": "#FFFFFF",
    "card_bg": "#FFFFFF",
    "card_bg_hover": "#F7F9FC",
    "card_border": "#E2E6EC",
    "divider": "#EDF0F4",
    "text": "#1B1F26",
    "text_secondary": "#5A6472",
    "text_muted": "#8A93A0",
    "text_disabled": "#AEB6C2",
    "accent": "#2563EB",
    "accent_hover": "#3B7BF0",
    "accent_pressed": "#1D4FD1",
    "accent_soft": "#E4EDFD",
    "accent_text": "#FFFFFF",
    "success": "#1F9D63",
    "success_soft": "#E4F5EC",
    "warning": "#C07C13",
    "warning_soft": "#FBF0DC",
    "error": "#D03A48",
    "error_soft": "#FBE7E9",
    "info": "#2A7FE0",
    "input_bg": "#FFFFFF",
    "input_border": "#D8DDE5",
    "input_focus": "#2563EB",
    "hover_bg": "#F1F4F9",
    "pressed_bg": "#E6EAF1",
    "selected_bg": "#DCE8FD",
    "scrollbar": "#C9CFD8",
    "scrollbar_hover": "#AEB6C2",
    "shadow": "#C7CDD6",
    "track": "#E8ECF1",
}

UI_FONT_FAMILIES: Final[tuple[str, ...]] = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Segoe UI Variable Text",
    "Segoe UI",
)


def resolve_system_theme() -> str:
    """读取 Windows 应用主题（浅色 / 深色），失败时回退深色。"""
    if os.name != "nt":
        return "dark"
    try:
        import winreg  # type: ignore[import-not-found]

        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if int(value) == 1 else "dark"
    except Exception:  # noqa: BLE001 - 注册表不可用时回退默认主题
        return "dark"


class ThemeManager(QObject):
    """负责加载、切换与应用主题。"""

    theme_changed = Signal(str)

    def __init__(
        self,
        app: QApplication,
        settings: AppSettings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._app = app
        self._settings = settings
        self._mode = settings.theme
        self._effective = self._resolve(self._mode)
        self._tokens: dict[str, str] = dict(
            TOKENS_DARK if self._effective == "dark" else TOKENS_LIGHT
        )

    # -- 状态 --------------------------------------------------------------
    @property
    def mode(self) -> ThemeMode:
        return self._mode

    @property
    def effective_mode(self) -> str:
        """实际生效的主题：``dark`` 或 ``light``。"""
        return self._effective

    @property
    def tokens(self) -> dict[str, str]:
        return self._tokens

    @property
    def is_dark(self) -> bool:
        return self._effective == "dark"

    def color(self, token: str, fallback: str = "#FFFFFF") -> QColor:
        """把令牌名转换为 :class:`QColor`。"""
        return QColor(self._tokens.get(token, fallback))

    def token(self, name: str, fallback: str = "") -> str:
        return self._tokens.get(name, fallback)

    # -- 切换 --------------------------------------------------------------
    def set_mode(self, mode: ThemeMode | str, *, persist: bool = True) -> None:
        """切换主题（可持久化到设置）。"""
        new_mode = ThemeMode(mode)
        if new_mode is self._mode and self._effective == self._resolve(new_mode):
            return
        self._mode = new_mode
        if persist:
            self._settings.theme = new_mode
        self.apply()

    def apply(self, *, persist: bool = False) -> None:
        """应用当前主题。"""
        if persist:
            self._settings.theme = self._mode
        self._effective = self._resolve(self._mode)
        self._tokens = dict(TOKENS_DARK if self._effective == "dark" else TOKENS_LIGHT)
        self._apply_palette()
        self._app.setStyleSheet(self._load_stylesheet(self._effective))
        self.theme_changed.emit(self._effective)
        _log.debug("主题已切换为 %s（模式 %s）", self._effective, self._mode.value)

    def reload(self) -> None:
        """重新读取样式表文件并应用（开发调试用）。"""
        self.apply()

    def follow_system_changed(self) -> None:
        """系统主题变化时调用。"""
        if self._mode is ThemeMode.SYSTEM:
            self.apply()

    # -- 内部 --------------------------------------------------------------
    def _resolve(self, mode: ThemeMode) -> str:
        if mode is ThemeMode.SYSTEM:
            return resolve_system_theme()
        return mode.value

    def _load_stylesheet(self, name: str) -> str:
        """加载基础模板 + 主题覆盖，并注入设计令牌。"""
        parts: list[str] = []
        for path in (stylesheet_path("base"), stylesheet_path(name)):
            try:
                parts.append(path.read_text(encoding="utf-8"))
            except OSError as exc:  # pragma: no cover - 资源缺失时给出兜底
                _log.error("加载样式表失败 %s：%s", path, exc)
        if not parts:
            return ""
        template = Template("\n".join(parts))
        tokens = dict(self._tokens)
        icon_dir = str(resource_path("icons")).replace("\\", "/")
        flavor = "dark" if self._effective == "dark" else "light"
        tokens["icon_dir"] = icon_dir
        tokens["arrow_down"] = f"{icon_dir}/arrow-down-{flavor}.svg"
        tokens["arrow_up"] = f"{icon_dir}/arrow-up-{flavor}.svg"
        return template.safe_substitute(tokens)

    def _apply_palette(self) -> None:
        """设置基础调色板与字体，保证原生控件风格一致。"""
        palette = QPalette()
        background = self.color("window_bg")
        surface = self.color("surface")
        text = self.color("text")
        secondary = self.color("text_secondary")
        accent = self.color("accent")

        palette.setColor(QPalette.ColorRole.Window, background)
        palette.setColor(QPalette.ColorRole.WindowText, text)
        palette.setColor(QPalette.ColorRole.Base, surface)
        palette.setColor(QPalette.ColorRole.AlternateBase, self.color("card_bg"))
        palette.setColor(QPalette.ColorRole.Text, text)
        palette.setColor(QPalette.ColorRole.Button, surface)
        palette.setColor(QPalette.ColorRole.ButtonText, text)
        palette.setColor(QPalette.ColorRole.ToolTipBase, surface)
        palette.setColor(QPalette.ColorRole.ToolTipText, text)
        palette.setColor(QPalette.ColorRole.Highlight, accent)
        palette.setColor(QPalette.ColorRole.HighlightedText, self.color("accent_text"))
        palette.setColor(QPalette.ColorRole.PlaceholderText, secondary)
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, self.color("text_disabled"))
        palette.setColor(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, self.color("text_disabled")
        )
        self._app.setPalette(palette)

        font = QFont(UI_FONT_FAMILIES[0], 10)
        font.setFamilies(list(UI_FONT_FAMILIES))
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        self._app.setFont(font)


def repolish(widget: QWidget) -> None:
    """修改动态属性后重新应用样式。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_variant(widget: QWidget, variant: str) -> None:
    """设置 QSS 动态属性 ``variant`` 并刷新样式。"""
    if widget.property("variant") == variant:
        return
    widget.setProperty("variant", variant)
    repolish(widget)


# ---------------------------------------------------------------------------
# 活动主题注册表
# ---------------------------------------------------------------------------
#: 自定义控件需要在绘制时读取主题令牌。为避免把 ThemeManager 透传到每个控件
#: 构造函数，这里保存当前活动主题（仅 UI 层使用，切换主题时更新）。
_active_theme: ThemeManager | None = None


def set_active_theme(theme: ThemeManager | None) -> None:
    """登记当前活动主题。"""
    global _active_theme
    _active_theme = theme


def active_theme() -> ThemeManager | None:
    """返回当前活动主题（未初始化时为 ``None``）。"""
    return _active_theme


def token(name: str, fallback: str = "#FFFFFF") -> str:
    """读取主题令牌颜色，未初始化时返回回退值。"""
    theme = _active_theme
    if theme is None:
        return fallback
    return theme.token(name, fallback) or fallback


def is_dark() -> bool:
    """当前是否为深色主题。"""
    theme = _active_theme
    return True if theme is None else theme.is_dark
