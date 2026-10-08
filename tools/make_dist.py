"""打一个可以直接发给别人的分发包（zip）。

为什么不能直接把整个项目目录压缩发出去：
  1. .venv/ 里的 pyvenv.cfg 写死了本机的 Python 绝对路径。搬到别人机器上，
     venv 里的 python.exe 会直接报 "did not find executable"（退出码 103）。
  2. outputs/ session/ temp/ 里是真实业务数据和会话快照，db_connections.json
     里是数据库地址 + 明文密码 —— 发出去等于泄漏。
  3. 体积：整包 1GB+，干净包几十 MB。

两种产物：
    python tools/make_dist.py              # 代码包：对方需自己有 Python 3.10+，首次联网装依赖
    python tools/make_dist.py --portable   # 免安装版：Python 运行时 + 全部依赖一起打包

推荐发人的是 --portable：对方解压后双击 启动.bat 就能用，
不用装 Python、不用联网、不用装任何依赖。

免安装版是怎么做到免安装的：
  · 解释器用官方「嵌入式 Python」（embeddable package）：解压即用，
    不写注册表、不需要管理员权限、不干扰系统里已有的 Python；
  · 依赖直接从本机 .venv/Lib/site-packages 复制（剔除 pip / tests / 元数据），
    放进 runtime/py/site-packages，配合 python313._pth 加入 sys.path。
    因为 wheel 是 cp313-win_amd64 的，跨机器只要同架构就能直接跑。
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 官方嵌入式 Python（与本机 .venv 同为 3.13，wheel 的 ABI 才对得上）
EMBED_URL = "https://www.python.org/ftp/python/3.13.14/python-3.13.14-embed-amd64.zip"
# 本地加速：设环境变量 RS_EMBED_ZIP 指向已下好的 zip，可跳过下载
EMBED_CACHE = os.environ.get("RS_EMBED_ZIP", "")

# ---------- 代码包：哪些不进包 ----------

# 整个跳过的目录（运行产物 / 隐私数据 / 环境）
SKIP_DIRS = {
    ".venv", "venv", "env", "__pycache__", ".git", ".pytest_cache",
    "outputs", "temp", "logs", "session", "session_trash", "dashboards",
    "dist", "build", ".idea", ".vscode",
    ".devtools",          # 里面有 14MB 的 node_modules（前端测试用，对方不需要）
}
SKIP_SUFFIX = {".pyc", ".pyo", ".log", ".db", ".sqlite", ".sqlite3"}

# 无论在哪都绝不打进包的文件（密钥 / 真实数据 / 本机残留）
SKIP_FILES = {
    "db_connections.json",     # 数据库地址 + 用户名 + 明文密码
    "config.local.yaml",       # 私人配置：API key、输出目录、限额
    ".env",
    "service.log",
    ".DS_Store",
    "desktop.ini",
}

# ---------- 运行时：从 site-packages 里剔除什么 ----------

# 这些是「装包用的工具」和「开发期元数据」，运行时完全用不到
SP_SKIP_TOP = {"pip", "setuptools", "wheel", "pkg_resources", "__pycache__"}
SP_SKIP_SUFFIX = {".dist-info", ".pyc", ".pyo"}
# 包内自带的测试套件（pandas/tests 36MB、matplotlib/tests 6MB），运行时不需要
SP_SKIP_DIRS = {"tests", "test", "__pycache__", "_tests"}

README_TXT = """ReportStudio 多源数据图表报告工具 —— 第一次使用请读我
========================================================

【零、先看这里】
    解压后的文件夹里如果有  runtime  这个文件夹 —— 这是免安装版，
    Python 和所有依赖都已经打包在里面了。
    直接跳到【二、启动】，什么都不用装、也不用联网。

    没有 runtime 文件夹的话，才需要看【一】。


【一、只有非免安装版才需要：装 Python】
    1. 打开 https://www.python.org/downloads/ 下载 Python 3.10 或更高版本
    2. 安装时务必勾选最下面的  "Add Python to PATH"（添加到环境变量）
    3. 装完继续下一步

    怎么确认装好了？按住 Win+R，输入 cmd 回车，在黑窗口里输入：
        python --version
    能显示版本号（比如 Python 3.13.1）就说明装好了。


【二、启动】
    Windows 用户：双击  启动.bat
    macOS/Linux ：终端里运行  python3 app.py

    第一次启动（非免安装版）会自动建虚拟环境并下载依赖，
    约 100MB、几分钟，请保持联网、不要关窗口。

    看到浏览器自动打开 http://127.0.0.1:8765 就成功了。
    那个黑色窗口不要关，关掉就等于停止服务。


【三、可选：接入大模型（图片识别 / 智能分析更准）】
    不配也能用，只是图片解析会退化。
    要配的话：双击  配置向导.bat ，按提示填接口地址、API Key、模型名，
    向导会自己写好配置并测试连通性，配完重启服务生效。
    （也可以手动把 config.yaml 复制一份改名为 config.local.yaml 来填，
    两种写法效果一样。config.local.yaml 不会被上传、也不会被打包发出去。）

    如果分享者给你的是「已预置大模型」的包，这一步什么都不用做，
    开箱即用。


【四、常见问题】

    Q：双击 启动.bat 一闪而过 / 提示没有 Python
    A：Python 没装，或装的时候没勾 "Add Python to PATH"。重装并勾选即可。

    Q：提示 8765 端口被占用
    A：改 config.yaml 里的 port，换个数字（比如 8801）。

    Q：图片里的表格识别不出来
    A：图片结构化识别需要视觉模型，按【三】配一个就好。

    Q：我的数据会不会被上传到别处？
    A：不会。报告、图表、数据库查询结果全部只存在你本机的 outputs 目录里。
       只有你主动配置了大模型的情形下，才会把数据发给那个模型接口。
"""


def collect_files(root: Path) -> list[tuple[Path, str]]:
    """返回 [(磁盘路径, zip 内相对路径)]。"""
    out: list[tuple[Path, str]] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        parts = rel.parts
        if any(x in SKIP_DIRS for x in parts):
            continue
        if p.is_dir():
            continue
        if p.suffix.lower() in SKIP_SUFFIX:
            continue
        if p.name in SKIP_FILES:
            continue
        # samples/ 里的都是生成物（xlsx/docx/pdf/db），只保留生成器脚本
        if parts[0] == "samples" and p.name != "make_samples.py":
            continue
        out.append((p, "/".join(parts)))
    return out


def build_runtime(dest: Path) -> bool:
    """搭一个免安装的 Python 运行时。"""
    py_dir = dest / "py"
    sp = py_dir / "site-packages"

    # 1) 嵌入式解释器
    if not (py_dir / "python.exe").exists():
        py_dir.mkdir(parents=True, exist_ok=True)
        zf = dest / "py-embed.zip"
        src = Path(EMBED_CACHE) if EMBED_CACHE and Path(EMBED_CACHE).exists() else None
        if src:
            print(f"  复用本地缓存：{src}")
            shutil.copyfile(src, zf)
        else:
            print("正在下载嵌入式 Python（约 11MB）...")
            try:
                urllib.request.urlretrieve(EMBED_URL, zf)
            except Exception as exc:  # noqa: BLE001
                print(f"  下载失败：{type(exc).__name__}: {exc}")
                return False
        with zipfile.ZipFile(zf) as z:
            z.extractall(py_dir)
        print(f"  已解压到 {py_dir}")

    # 2) 依赖：从本机 .venv 复制
    src_sp = ROOT / ".venv" / "Lib" / "site-packages"
    if not src_sp.is_dir():
        print(f"  [错误] 找不到 {src_sp}，无法制作免安装版。")
        return False
    if not (sp / "pandas").exists():
        sp.mkdir(parents=True, exist_ok=True)
        n = 0
        for p in sorted(src_sp.rglob("*")):
            rel = p.relative_to(src_sp)
            parts = rel.parts
            if any(x in SP_SKIP_DIRS for x in parts):
                continue
            if p.is_dir():
                continue
            if parts[0] in SP_SKIP_TOP:
                continue
            if any(x.endswith(tuple(SP_SKIP_SUFFIX)) for x in parts):
                continue
            if p.suffix.lower() in SP_SKIP_SUFFIX:
                continue
            dst = sp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dst)
            n += 1
        print(f"  已复制依赖 {n} 个文件 -> {sp}")

    # 3) 嵌入式 Python 用 python3XX._pth 决定 sys.path，且【忽略 PYTHONPATH】，
    #    必须把 site-packages 写进去，否则依赖 import 不到。
    for pth in glob.glob(str(py_dir / "python3*._pth")):
        Path(pth).write_text(
            "python313.zip\n.\nsite-packages\n\n"
            "# 依赖已复制在 site-packages，不需要 pip（嵌入式版本本来也没有 pip）\n",
            encoding="utf-8")

    # 4) 自检：核心模块能不能 import
    exe = py_dir / "python.exe"
    probe = (
        "import sys,sqlite3,ssl,socket,json;"
        "import numpy,pandas,matplotlib,fastapi,uvicorn,yaml,requests;"
        "import openpyxl,docx,reportlab,pdfplumber;"
        "print('OK',sys.version.split()[0])"
    )
    r = subprocess.run([str(exe), "-c", probe], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(py_dir))
    if r.returncode != 0:
        print("运行时自检失败：")
        print((r.stdout or "")[-1000:])
        print((r.stderr or "")[-1000:])
        return False
    print("  运行时自检通过：", (r.stdout or "").strip())
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="打一个可发给别人的分发包")
    ap.add_argument("--portable", action="store_true",
                    help="免安装版：把 Python 运行时和依赖一起打进包里")
    ap.add_argument("--with-llm-config", action="store_true",
                    help="把本机 config.local.yaml（含 API Key）一起打进包："
                         "收件人开箱即用，但 Key 会随包流出，只发给信得过的人")
    ap.add_argument("--out", default="", help="输出目录，默认 dist/")
    ap.add_argument("--name", default="", help="压缩包名，默认 ReportStudio-日期")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else ROOT / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)

    name = args.name or f"ReportStudio-{time.strftime('%Y%m%d')}"
    zip_path = out_dir / f"{name}.zip"

    files = collect_files(ROOT)

    has_runtime = False
    if args.portable:
        print("正在准备免安装运行时...")
        has_runtime = build_runtime(out_dir / "_rt")

    TOP = "ReportStudio"
    total = 0
    rt_n = 0
    baked_llm = False
    if args.with_llm_config:
        local_cfg = ROOT / "config.local.yaml"
        if local_cfg.exists():
            baked_llm = True
            print("  [注意] 将把 config.local.yaml（含 API Key）打进包里，"
                  "收件人开箱即用，但 Key 会随包流出。")
        else:
            print("  [警告] 没有找到 config.local.yaml，--with-llm-config 不生效。")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for src, rel in files:
            z.write(src, f"{TOP}/{rel}")
            total += src.stat().st_size
        if baked_llm:
            z.write(local_cfg, f"{TOP}/config.local.yaml")
            total += local_cfg.stat().st_size
        if has_runtime:
            py_root = out_dir / "_rt" / "py"
            for p in sorted(py_root.rglob("*")):
                rel = p.relative_to(py_root)
                if any(x in SP_SKIP_DIRS for x in rel.parts):
                    continue
                if p.is_dir():
                    continue
                if p.suffix.lower() == ".pyc":
                    continue
                z.write(p, f"{TOP}/runtime/py/{rel.as_posix()}")
                total += p.stat().st_size
                rt_n += 1
        z.writestr(f"{TOP}/第一次用请读我.txt", README_TXT.encode("gbk"))

    print()
    print("=" * 60)
    print(f"  分发包已生成：{zip_path}")
    print(f"  压缩包体积：{zip_path.stat().st_size / 1024 / 1024:.2f} MB"
          f"（原始 {total / 1024 / 1024:.2f} MB）")
    print(f"  代码文件：{len(files)}    运行时文件：{rt_n}")
    print("  运行环境：" + ("免安装（内置 Python 3.13 + 全部依赖）"
                            if has_runtime else "需系统 Python 3.10+ 且首次联网装依赖"))
    print("=" * 60)
    print()
    if baked_llm:
        print("已预置：config.local.yaml（大模型配置，收件人开箱即用，注意 Key 随包流出）")
    print("已排除：.venv/、outputs/、session/、temp/、logs/、dashboards/、")
    print("        db_connections.json（数据库密码）" +
          ("、config.local.yaml（API key）" if not baked_llm else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
