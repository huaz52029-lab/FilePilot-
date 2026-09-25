"""支持 ``python -m app`` 启动。"""

from __future__ import annotations

from app.main import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
