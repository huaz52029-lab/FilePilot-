"""验证打包产物是否可用。

用法::

    python tools/verify_build.py                     # 仅自检
    python tools/verify_build.py --network           # 额外验证真实 HTTP 探测
    python tools/verify_build.py --exe dist/FilePilot.exe

退出码 0 表示产物正常。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_EXE = REPO_ROOT / "dist" / "FilePilot.exe"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证 FilePilot 打包产物")
    parser.add_argument("--exe", default=str(DEFAULT_EXE), help="可执行文件路径")
    parser.add_argument("--network", action="store_true", help="额外执行真实 HTTP 探测")
    parser.add_argument("--timeout", type=float, default=180.0, help="单次自检超时（秒）")
    return parser.parse_args(argv)


def run_self_test(exe: Path, data_dir: Path, *, url: str | None, timeout: float) -> int:
    """运行 EXE 自检并返回退出码。"""
    args = [str(exe), "--self-test"]
    if url:
        args += ["--self-test-url", url]
    env = os.environ.copy()
    env["FILEPILOT_HOME"] = str(data_dir)
    env.pop("QT_QPA_PLATFORM", None)
    print(f"执行：{' '.join(args)}")
    completed = subprocess.run(
        args, env=env, timeout=timeout, check=False, capture_output=True, text=True
    )
    if completed.stdout:
        print(completed.stdout.strip())
    if completed.stderr:
        print(completed.stderr.strip())
    return completed.returncode


def tail_log(data_dir: Path, lines: int = 8) -> str:
    """读取最新日志尾部。"""
    logs = sorted((data_dir / "logs").glob("*.log")) if (data_dir / "logs").exists() else []
    if not logs:
        return "（未找到日志文件）"
    content = logs[-1].read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    exe = Path(args.exe)
    if not exe.exists():
        print(f"未找到可执行文件：{exe}\n请先运行 python build.py")
        return 1

    with tempfile.TemporaryDirectory(prefix="filepilot-verify-") as directory:
        data_dir = Path(directory)
        print(f"数据目录：{data_dir}")

        code = run_self_test(exe, data_dir, url=None, timeout=args.timeout)
        print(f"基础自检退出码：{code}")
        print("--- 日志 ---")
        print(tail_log(data_dir))
        if code != 0:
            print("打包产物基础自检失败。")
            return code

        if args.network:
            from tests.http_server import serve

            with serve() as server:
                payload = bytes(index % 251 for index in range(200_000))
                url = server.register("/verify.bin", payload, etag='"verify"')
                print(f"\n本地测试服务器：{url}")
                network_code = run_self_test(exe, data_dir, url=url, timeout=args.timeout)
                print(f"网络自检退出码：{network_code}")
                print("--- 日志 ---")
                print(tail_log(data_dir))
                if network_code != 0:
                    print("打包产物网络自检失败。")
                    return network_code

        print("\n打包产物验证通过。")
    _ = time.time()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
