"""页面截图工具。

用途：

* 为 README 生成界面截图；
* 开发期的视觉回归检查（无需真实显示器，使用 offscreen 平台）。

用法::

    python tools/screenshot.py --out docs/screenshots --theme dark
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: 截图统一使用中性的演示数据目录，避免把开发者本机路径（含用户名）暴露在 README 中
DEMO_HOME = Path("C:/Users/Public/FilePilot")
os.environ.setdefault("FILEPILOT_HOME", str(DEMO_HOME))

from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.settings_service import ThemeMode  # noqa: E402
from app.ui.navigation import PageId  # noqa: E402

#: 常见中文字体（沙箱或精简系统中 Qt 无法枚举字体时手动注册）
CJK_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/Deng.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


def register_cjk_font() -> str | None:
    """在字体库为空时手动注册一个中文字体。"""
    if QFontDatabase.families():
        return None
    for candidate in CJK_FONT_CANDIDATES:
        path = Path(candidate)
        if not path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            return families[0]
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 FilePilot 界面截图")
    parser.add_argument("--out", default=str(REPO_ROOT / "docs" / "images"), help="输出目录")
    parser.add_argument("--theme", choices=[mode.value for mode in ThemeMode], default="dark")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--page", default="all", help="页面标识，或 all")
    parser.add_argument("--scroll-bottom", action="store_true", help="滚动到页面底部再截图")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.makedirs(os.environ["FILEPILOT_HOME"], exist_ok=True)

    from app.main import create_application
    from app.services.app_context import AppContext
    from app.ui.main_window import MainWindow

    app: QApplication = create_application(["filepilot-screenshot"])
    font_family = register_cjk_font()
    if font_family:
        font = app.font()
        font.setFamilies([font_family])
        app.setFont(font)

    context = AppContext(console_log=False)
    # 演示用的下载 / 整理目录（保证路径显示中性且真实存在）
    demo_downloads = Path(os.environ["FILEPILOT_HOME"]) / "Downloads"
    demo_downloads.mkdir(parents=True, exist_ok=True)
    (demo_downloads / "文档").mkdir(exist_ok=True)
    (demo_downloads / "安装包").mkdir(exist_ok=True)
    context.settings.download_dir = demo_downloads
    context.settings.organize_root = demo_downloads
    context.settings.theme = ThemeMode(args.theme)
    window = MainWindow(context, app)
    window.resize(args.width, args.height)
    window.show()
    app.processEvents()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = list(PageId) if args.page == "all" else [PageId(args.page)]
    for page_id in pages:
        window._switch_page(page_id)  # noqa: SLF001 - 截图工具内部调用
        for _ in range(3):
            app.processEvents()
        if args.scroll_bottom:
            page = window.pages[page_id]
            scroll_area = getattr(page, "scroll_area", None)
            if scroll_area is not None:
                bar = scroll_area.verticalScrollBar()
                bar.setValue(bar.maximum())
                for _ in range(3):
                    app.processEvents()
        suffix = "-bottom" if args.scroll_bottom else ""
        target = out_dir / f"{args.theme}-{page_id.value}{suffix}.png"
        if window.grab().save(str(target)):
            print(f"已保存 {target}")
        else:  # pragma: no cover - 保存失败属于环境问题
            print(f"保存失败：{target}", file=sys.stderr)
    context.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
