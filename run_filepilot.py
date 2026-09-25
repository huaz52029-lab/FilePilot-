"""开发与打包入口。

* 开发： ``python run_filepilot.py``
* 打包： ``python build.py``（PyInstaller 使用本文件作为入口）
"""

from __future__ import annotations

from app.main import main

if __name__ == "__main__":
    raise SystemExit(main())
