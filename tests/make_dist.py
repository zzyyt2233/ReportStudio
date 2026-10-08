# -*- coding: utf-8 -*-
"""分发包排除规则：确认该进包的在、不该进包的一个都不在。

make_dist.py 的 SKIP_DIRS / SKIP_SUFFIX / SKIP_FILES 是一条明确的
安全承诺 —— db_connections.json 里有数据库地址和明文密码，
config.local.yaml 里有 API key，outputs/ session/ 里是真实业务数据。
把这些发出去就是泄漏。

但这条承诺此前从没被任何测试守过：collect_files() 是纯路径判断，
可测性很好，却只能靠人肉读代码确认。谁重构时手滑把 "outputs" 写成
"output"、或者把某个条目从 SKIP_FILES 里挪走，泄漏是静默的 ——
打包照常成功，只是包里多了不该有的东西。

所以这里造一棵「什么都有一点」的假目录树，正向验证业务文件确实进包、
反向验证每一类敏感文件都确实被挡在外面。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.make_dist import collect_files  # noqa: E402

_ok = 0
_fail = 0


def ok(label: str, cond: bool, info: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print("  ✓ " + label)
    else:
        _fail += 1
        print("  ✗ " + label + (f"  {info}" if info else ""))


def _touch(p: Path, content: str = "x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def build_tree(root: Path) -> None:
    """造一棵对比鲜明的树：左边该留、右边该扔。"""
    # --- 该进包的 ---
    _touch(root / "app.py")
    _touch(root / "requirements.txt")
    _touch(root / "README.md")
    _touch(root / "core" / "render" / "combine.py")
    _touch(root / "docs" / "方案设计.md")
    _touch(root / "samples" / "make_samples.py")      # 生成器脚本要保留

    # --- 目录级：一律不进 ---
    _touch(root / ".venv" / "pyvenv.cfg")
    _touch(root / ".venv" / "Scripts" / "python.exe", "bin")
    _touch(root / "venv" / "pyvenv.cfg")
    _touch(root / "env" / "pyvenv.cfg")
    _touch(root / "__pycache__" / "app.cpython-313.pyc")
    _touch(root / "core" / "__pycache__" / "x.pyc")
    _touch(root / ".git" / "config")
    _touch(root / "outputs" / "报告_20261008" / "chart.png", "img")
    _touch(root / "temp" / "fuzz" / "x.json")
    _touch(root / "logs" / "service.log")
    _touch(root / "session" / "snapshot.json")
    _touch(root / "session_trash" / "old.json")
    _touch(root / "dashboards" / "d1.json")
    _touch(root / "dist" / "pkg.zip")
    _touch(root / "build" / "x.tmp")
    _touch(root / ".idea" / "workspace.xml")
    _touch(root / ".vscode" / "settings.json")
    _touch(root / ".devtools" / "node_modules" / "jsdom" / "index.js")
    _touch(root / ".pytest_cache" / "v" / "cache.json")

    # --- 后缀级：不进 ---
    _touch(root / "app.pyc")
    _touch(root / "core" / "helper.pyo")
    _touch(root / "debug.log")
    _touch(root / "cache.db")
    _touch(root / "metrics.sqlite")
    _touch(root / "legacy.sqlite3")

    # --- 文件级：绝不进（凭据 / 真实数据 / 本机残留）---
    _touch(root / "db_connections.json", '{"password":"REAL"}')
    _touch(root / "config.local.yaml", "llm:\n  api_key: sk-real\n")
    _touch(root / ".env", "SECRET=1")
    _touch(root / "service.log")
    _touch(root / ".DS_Store")
    _touch(root / "desktop.ini")
    # 嵌套在任何地方都要挡住
    _touch(root / "core" / "db_connections.json", '{"password":"REAL"}')
    _touch(root / "docs" / "config.local.yaml", "x")

    # --- samples/ 特例：只留生成器，其余生成物都不进 ---
    _touch(root / "samples" / "sales.xlsx")
    _touch(root / "samples" / "demo.docx")
    _touch(root / "samples" / "sample.db")


def main() -> int:
    print("== 分发包排除规则 ==")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_tree(root)
        files = collect_files(root)
        names = {z for _, z in files}
        abspaths = {str(p) for p, _ in files}

        print("\n-- 该进包的必须都在 --")
        for keep in ("app.py", "requirements.txt", "README.md",
                     "core/render/combine.py", "docs/方案设计.md",
                     "samples/make_samples.py"):
            ok(f"保留 {keep}", keep in names, f"实际收集 {len(names)} 个")

        print("\n-- 目录级排除 --")
        for d in (".venv", "venv", "env", "__pycache__", ".git", "outputs",
                  "temp", "logs", "session", "session_trash", "dashboards",
                  "dist", "build", ".idea", ".vscode", ".devtools",
                  ".pytest_cache"):
            hit = [n for n in names if d in Path(n).parts]
            ok(f"不含 {d}/ 里的任何文件", not hit, f"漏了 {hit[:2]}")

        print("\n-- 后缀级排除 --")
        for suf in (".pyc", ".pyo", ".log", ".db", ".sqlite", ".sqlite3"):
            hit = [n for n in names if n.lower().endswith(suf)]
            ok(f"不含 *{suf}", not hit, f"漏了 {hit[:2]}")

        print("\n-- 凭据 / 隐私文件（安全承诺的核心）--")
        for f in ("db_connections.json", "config.local.yaml", ".env",
                  "service.log", ".DS_Store", "desktop.ini"):
            hit = [n for n in names if Path(n).name == f]
            ok(f"不含 {f}（含嵌套位置）", not hit, f"漏了 {hit[:2]}")

        # 这是本测试存在的全部理由：确认那两个最要命的文件真的不在。
        ok("整棵包里找不到 db_connections.json",
           not any(Path(n).name == "db_connections.json" for n in names))
        ok("整棵包里找不到 config.local.yaml",
           not any(Path(n).name == "config.local.yaml" for n in names))

        print("\n-- samples/ 特例 --")
        ok("保留 samples/make_samples.py",
           "samples/make_samples.py" in names)
        ok("排除 samples/ 下的生成物（xlsx/docx/db）",
           not any(n.startswith("samples/") and n != "samples/make_samples.py"
                   for n in names),
           f"漏了 {[n for n in names if n.startswith('samples/')][:3]}")

        print("\n-- 收集结果结构正确 --")
        ok("返回的是 (磁盘路径, zip 相对路径) 二元组",
           all(isinstance(p, Path) and isinstance(z, str)
               for p, z in files))
        ok("zip 内路径全用正斜杠（跨平台解压一致）",
           not any("\\" in z for _, z in files))
        ok("磁盘路径都是真实存在的文件",
           all(os.path.isfile(p) for p in abspaths))

    print(f"\n通过 {_ok} 项，失败 {_fail} 项")
    return 1 if _fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
