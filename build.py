"""打包脚本：使用 PyInstaller 生成 Windows 可执行文件。

用法::

    python build.py                # 单文件模式 → dist/FilePilot.exe
    python build.py --release      # 发布命名 → dist/FilePilot-v0.1.1-Windows-x64.exe
    python build.py --release --zip  # 额外生成便携版压缩包
    python build.py --onedir       # 目录模式（启动更快）→ dist/FilePilot/FilePilot.exe
    python build.py --console      # 保留控制台（排查启动问题）
    python build.py --skip-icon    # 跳过图标生成

输出：

    dist/FilePilot.exe

打包要求：Windows 10/11，Python 3.13+，无需目标机器安装 Python。
配置文件、数据库与日志始终写入用户目录（``%APPDATA%/FilePilot``），
不会写入程序安装目录。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
ENTRY_SCRIPT = REPO_ROOT / "run_filepilot.py"
ICON_PATH = REPO_ROOT / "app" / "resources" / "icons" / "filepilot.ico"
RESOURCE_DIR = REPO_ROOT / "app" / "resources"
APP_NAME = "FilePilot"


def parse_args(argv: list[str]) -> dict[str, object]:
    """解析命令行参数。"""
    name = ""
    if "--name" in argv:
        index = argv.index("--name")
        if index + 1 < len(argv):
            name = argv[index + 1]
    return {
        "onedir": "--onedir" in argv,
        "console": "--console" in argv,
        "skip_icon": "--skip-icon" in argv,
        "no_clean": "--no-clean" in argv,
        "release": "--release" in argv,
        "zip": "--zip" in argv,
        "name": name,
    }


def ensure_dependencies() -> None:
    """确认构建依赖已安装。"""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:  # pragma: no cover - 构建环境检查
        print("未检测到 PyInstaller，请先执行：python -m pip install -r requirements-dev.txt")
        raise SystemExit(1) from None


#: PATH 中出现这些片段时，其目录下的 DLL 可能是第三方工具链自带的同名库
#: （例如 poppler 的 icuuc.dll），一旦被打包会覆盖 Windows 系统组件，
#: 造成 “DLL load failed / 找不到指定的程序”。
SUSPICIOUS_PATH_TOKENS: tuple[str, ...] = (
    "codex-runtimes",
    "dependencies\\native",
    "dependencies/native",
    "poppler",
    "\\conda",
    "\\qt\\",
    "opencv",
)


def build_environment() -> dict[str, str]:
    """构造干净的构建环境变量。

    PyInstaller 会在 PATH 中搜索依赖 DLL，命中第三方运行时目录时会把同名库
    打进包里。这里过滤掉这些目录，保证只使用 Python 包自带与系统 DLL。
    """
    env = os.environ.copy()
    entries = env.get("PATH", "").split(os.pathsep)
    kept = [
        entry
        for entry in entries
        if entry and not any(token in entry.lower() for token in SUSPICIOUS_PATH_TOKENS)
    ]
    env["PATH"] = os.pathsep.join(kept)
    # 清理可能影响 PyInstaller 依赖收集的额外变量
    for variable in ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "PYTHONPATH"):
        env.pop(variable, None)
    return env


def ensure_icon() -> Path | None:
    """生成（或复用）应用图标。"""
    if ICON_PATH.exists():
        return ICON_PATH
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from tools.make_icon import build_icon

        return build_icon()
    except Exception as exc:  # noqa: BLE001 - 图标失败不应阻塞打包
        print(f"生成图标失败（将使用默认图标）：{exc}")
        return None


def resolve_exe_name(options: dict[str, object]) -> str:
    """计算可执行文件名。

    * ``--name``      显式指定；
    * ``--release``   使用发布命名 ``FilePilot-v<版本>-Windows-x64``；
    * 默认            ``FilePilot``。
    """
    explicit = str(options.get("name") or "").strip()
    if explicit:
        return explicit.removesuffix(".exe")
    if options.get("release"):
        from app.core.common.constants import APP_VERSION

        return f"{APP_NAME}-v{APP_VERSION}-Windows-x64"
    return APP_NAME


def write_version_file(exe_name: str) -> Path:
    """生成 PyInstaller 使用的版本资源文件。"""
    sys.path.insert(0, str(REPO_ROOT))
    from app.core.common.constants import APP_AUTHOR, APP_HOMEPAGE, APP_VERSION

    parts = [int(part) for part in APP_VERSION.split(".")[:3]]
    while len(parts) < 4:
        parts.append(0)
    content = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({parts[0]}, {parts[1]}, {parts[2]}, {parts[3]}),
    prodvers=({parts[0]}, {parts[1]}, {parts[2]}, {parts[3]}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404b0',
        [
          StringStruct('CompanyName', '{APP_AUTHOR}'),
          StringStruct('FileDescription', 'FilePilot 文件管理与智能下载工具'),
          StringStruct('FileVersion', '{APP_VERSION}'),
          StringStruct('InternalName', '{APP_NAME}'),
          StringStruct('OriginalFilename', '{exe_name}.exe'),
          StringStruct('ProductName', '{APP_NAME}'),
          StringStruct('ProductVersion', '{APP_VERSION}'),
          StringStruct('LegalCopyright', 'MIT License'),
          StringStruct('Comments', '{APP_HOMEPAGE}'),
        ],
      )
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])]),
  ],
)
"""
    path = REPO_ROOT / "build" / "version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def make_zip(exe_path: Path) -> Path:
    """生成便携版压缩包（可执行文件 + README + LICENSE）。"""
    archive = exe_path.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        bundle.write(exe_path, exe_path.name)
        for extra in ("README.md", "LICENSE"):
            source = REPO_ROOT / extra
            if source.exists():
                bundle.write(source, extra)
    return archive


def build(options: dict[str, object]) -> int:
    """执行打包。"""
    ensure_dependencies()
    icon = None if options["skip_icon"] else ensure_icon()
    exe_name = resolve_exe_name(options)
    version_file = write_version_file(exe_name)

    if not options["no_clean"]:
        for directory in (REPO_ROOT / "build" / exe_name, REPO_ROOT / "dist"):
            if directory.exists():
                shutil.rmtree(directory, ignore_errors=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--name",
        exe_name,
        "--add-data",
        f"{RESOURCE_DIR}{os.pathsep}app/resources",
        "--version-file",
        str(version_file),
        "--onedir" if options["onedir"] else "--onefile",
        "--console" if options["console"] else "--windowed",
    ]
    if icon is not None:
        command += ["--icon", str(icon)]
    command.append(str(ENTRY_SCRIPT))

    print("执行：", " ".join(command))
    result = subprocess.run(
        command, cwd=REPO_ROOT, env=build_environment(), check=False
    )
    if result.returncode != 0:
        print("打包失败，请查看上方 PyInstaller 输出。")
        return result.returncode

    target = (
        REPO_ROOT / "dist" / exe_name / f"{exe_name}.exe"
        if options["onedir"]
        else REPO_ROOT / "dist" / f"{exe_name}.exe"
    )
    if target.exists():
        size_mb = target.stat().st_size / 1024 / 1024
        print(f"\n构建完成：{target}（{size_mb:.1f} MB）")
        if options.get("zip") and not options["onedir"]:
            archive = make_zip(target)
            print(f"便携压缩包：{archive}（{archive.stat().st_size / 1024 / 1024:.1f} MB）")
        print(f"可执行自检：dist\\{target.name} --self-test")
    else:  # pragma: no cover - 构建产物缺失
        print("未找到打包产物，请检查 PyInstaller 输出。")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    options = parse_args(list(argv if argv is not None else sys.argv[1:]))
    return build(options)


if __name__ == "__main__":
    raise SystemExit(main())
