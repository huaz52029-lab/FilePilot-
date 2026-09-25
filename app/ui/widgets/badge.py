"""状态徽标。"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

from app.ui.theme import repolish

TONES = ("neutral", "accent", "success", "warning", "error", "info")


class StatusBadge(QLabel):
    """小尺寸彩色标签，用于展示任务状态、能力标记等。"""

    def __init__(
        self,
        text: str = "",
        tone: str = "neutral",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("Badge")
        self.setProperty("tone", tone if tone in TONES else "neutral")
        self.setAlignment(self.alignment())

    def set_tone(self, tone: str) -> None:
        normalized = tone if tone in TONES else "neutral"
        if self.property("tone") == normalized:
            return
        self.setProperty("tone", normalized)
        repolish(self)

    def set_state(self, text: str, tone: str) -> None:
        """同时更新文本与色调。"""
        self.setText(text)
        self.set_tone(tone)
