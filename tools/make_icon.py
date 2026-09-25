"""生成 Windows 应用图标（ICO）。

从 ``app/resources/icons/logo.svg`` 渲染多尺寸位图，并封装为 PNG 压缩的
ICO 文件（Windows Vista 及以上原生支持），无需第三方依赖。
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
DEFAULT_COLOR = "#3B82F6"


def render_png(size: int, color: str, *, background: str | None = None) -> bytes:
    """渲染指定尺寸的 PNG 字节。"""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    from app.core.common.paths import icon_path

    svg_text = icon_path("logo").read_text(encoding="utf-8").replace("{color}", color)
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))

    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(QColor(background) if background else QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def write_ico(path: Path, images: list[tuple[int, bytes]]) -> None:
    """按 ICO 格式写出多尺寸图标。"""
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries = bytearray()
    payload = bytearray()
    for size, data in images:
        dimension = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII",
            dimension,
            dimension,
            0,
            0,
            1,
            32,
            len(data),
            offset + len(payload),
        )
        payload += data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + bytes(entries) + bytes(payload))


def build_icon(
    target: Path | None = None,
    *,
    color: str = DEFAULT_COLOR,
    background: str | None = None,
) -> Path:
    """生成 ICO 文件并返回路径。"""
    destination = target or REPO_ROOT / "app" / "resources" / "icons" / "filepilot.ico"
    images = [(size, render_png(size, color, background=background)) for size in SIZES]
    write_ico(destination, images)
    return destination


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    target = Path(args[0]) if args else None

    from app.main import create_application

    create_application(["filepilot-icon"])
    path = build_icon(target)
    print(f"已生成图标：{path}（{path.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
