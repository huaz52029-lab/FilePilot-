"""本地测试服务器（手工验证下载功能时使用，仅开发用途）。

用法::

    python tools/dev_http_server.py --port 8931 --size 4MB
    # 然后可使用 http://127.0.0.1:8931/file.bin 测试下载

支持 Range 分段、ETag 与可选的故障注入（``--failures``）。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from tests.http_server import serve  # noqa: E402


def parse_size(text: str) -> int:
    units = {"kb": 1024, "mb": 1024**2, "gb": 1024**3}
    lowered = text.strip().lower()
    for suffix, multiplier in units.items():
        if lowered.endswith(suffix):
            return int(float(lowered[: -len(suffix)]) * multiplier)
    return int(lowered)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FilePilot 本地测试服务器")
    parser.add_argument("--port", type=int, default=0, help="端口（0 表示自动分配）")
    parser.add_argument("--path", default="/file.bin", help="URL 路径")
    parser.add_argument("--size", default="4MB", help="文件大小，例如 512KB / 4MB")
    parser.add_argument("--mode", default="range", help="服务器模式：range / norange / slow / lying")
    parser.add_argument("--failures", type=int, default=0, help="注入的临时故障次数")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    size = parse_size(args.size)
    payload = bytes(index % 251 for index in range(size))

    with serve() as server:
        if args.port:
            server.server_address = ("127.0.0.1", args.port)
        url = server.register(
            args.path,
            payload,
            mode=args.mode,
            etag='"dev"',
            modified=time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime()),
            failures=args.failures,
        )
        print(f"测试服务器已启动：{url}（{size} 字节，模式 {args.mode}）", flush=True)
        print("按 Ctrl+C 结束。", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("已停止。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
