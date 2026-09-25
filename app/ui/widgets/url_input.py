"""下载链接输入框（支持 Ctrl+V 与拖拽）。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QWidget

from app.core.common.helpers import is_http_url, normalize_url
from app.ui.icons import default_icon_provider
from app.ui.widgets.buttons import PrimaryButton


class UrlInputBox(QFrame):
    """粘贴 / 输入下载链接并提交分析。"""

    submitted = Signal(str)
    text_changed = Signal(str)

    def __init__(
        self,
        *,
        placeholder: str = "粘贴下载链接，例如 https://example.com/file.zip",
        button_text: str = "分析并下载",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("UrlBox")
        self.setAcceptDrops(True)
        self._icons = default_icon_provider()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 10, 10)
        layout.setSpacing(10)

        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText(placeholder)
        self._edit.setClearButtonEnabled(True)
        self._edit.setObjectName("UrlLineEdit")
        self._edit.returnPressed.connect(self._submit)
        self._edit.textChanged.connect(self.text_changed.emit)
        layout.addWidget(self._edit, 1)

        self._button = PrimaryButton(button_text, icon_name="download", parent=self)
        self._button.clicked.connect(self._submit)
        layout.addWidget(self._button, 0)

    # -- API ---------------------------------------------------------------
    def text(self) -> str:
        return self._edit.text().strip()

    def set_text(self, value: str) -> None:
        self._edit.setText(value)
        self._edit.setFocus()
        self._edit.setCursorPosition(len(value))

    def clear(self) -> None:
        self._edit.clear()

    def focus_input(self) -> None:
        self._edit.setFocus()

    def set_button_text(self, text: str) -> None:
        self._button.setText(text)

    def set_enabled_input(self, enabled: bool) -> None:
        self._edit.setEnabled(enabled)
        self._button.setEnabled(enabled)

    # -- 内部 --------------------------------------------------------------
    def _submit(self) -> None:
        raw = self.text()
        if not raw:
            return
        url = normalize_url(raw)
        if url is None:
            self._edit.setProperty("invalid", True)
            from app.ui.theme import repolish

            repolish(self._edit)
            return
        self._edit.setProperty("invalid", False)
        from app.ui.theme import repolish

        repolish(self._edit)
        self.submitted.emit(url)

    # -- 拖拽 --------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime.hasText() or mime.hasUrls():
            text = mime.text().strip() if mime.hasText() else ""
            if not text and mime.hasUrls():
                urls = mime.urls()
                text = urls[0].toString() if urls else ""
            if is_http_url(text) or text.startswith(("http://", "https://")):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        mime = event.mimeData()
        text = mime.text().strip() if mime.hasText() else ""
        if not text and mime.hasUrls():
            urls = mime.urls()
            text = urls[0].toString() if urls else ""
        if text:
            self.set_text(text.strip())
            event.acceptProposedAction()
