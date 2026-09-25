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
    parser.add_argument(
        "--download",
        action="store_true",
        help="额外执行真实下载链路验证（下载 / 暂停 / 继续 / 取消 / SHA-256）",
    )
    parser.add_argument("--timeout", type=float, default=180.0, help="单次自检超时（秒）")
    return parser.parse_args(argv)


def run_self_test(
    exe: Path,
    data_dir: Path,
    *,
    url: str | None = None,
    download_url: str | None = None,
    slow_url: str | None = None,
    workdir: str | None = None,
    timeout: float,
) -> int:
    """运行 EXE 自检并返回退出码。"""
    args = [str(exe), "--self-test"]
    if url:
        args += ["--self-test-url", url]
    if download_url:
        args += ["--self-test-download", download_url]
    if slow_url:
        args += ["--self-test-slow", slow_url]
    if workdir:
        args += ["--self-test-workdir", workdir]
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


def full_log(data_dir: Path) -> str:
    """读取最新日志全文。"""
    logs = sorted((data_dir / "logs").glob("*.log")) if (data_dir / "logs").exists() else []
    if not logs:
        return ""
    return logs[-1].read_text(encoding="utf-8", errors="replace")


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

        if args.download:
            from tests.http_server import serve

            with serve() as server:
                fast_payload = bytes(index % 251 for index in range(900_000))
                slow_payload = bytes(index % 253 for index in range(1_200_000))
                fast_url = server.register("/verify.bin", fast_payload, etag='"verify"')
                slow_url = server.register("/slow.bin", slow_payload, mode="slow", etag='"slow"')
                workdir = data_dir / "downloads"
                print(f"\n本地测试服务器：{fast_url} / {slow_url}（慢速用于暂停测试）")
                code = run_self_test(
                    exe,
                    data_dir,
                    download_url=fast_url,
                    slow_url=slow_url,
                    workdir=str(workdir),
                    timeout=max(args.timeout, 300.0),
                )
                print(f"下载链路自检退出码：{code}")
                print("--- 日志 ---")
                print(tail_log(data_dir, lines=14))

                log_text = full_log(data_dir)
                checks = {
                    "下载完成（含进度与 SHA-256）": "下载自检 · 下载完成" in log_text,
                    "暂停 / 继续（断点续传）": "下载自检 · 暂停/继续" in log_text,
                    "取消任务": "下载自检 · 取消" in log_text,
                    "自检报告完整": "下载链路自检通过" in log_text,
                    "文件已落盘（完整下载）": (workdir / "verify.bin").exists()
                    and (workdir / "verify.bin").stat().st_size == len(fast_payload),
                    "文件已落盘（续传）": (workdir / "slow.bin").exists()
                    and (workdir / "slow.bin").stat().st_size == len(slow_payload),
                    "取消后未生成成品文件": not (workdir / "cancel" / "slow.bin").exists(),
                }
                print("\n--- 下载链路检查项 ---")
                for name, ok in checks.items():
                    print(f"  [{'OK' if ok else 'FAIL'}] {name}")
                if code != 0 or not all(checks.values()):
                    print("打包产物下载链路验证失败。")
                    return 1

        print("\n打包产物验证通过。")
    _ = time.time()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
