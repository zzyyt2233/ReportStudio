"""检查：所有顶层 import 的第三方包是否都在 requirements.txt 里声明。

为什么需要这条：
    Pillow 曾经只靠 matplotlib / reportlab 间接带进来 —— core/render/combine.py
    在模块顶层 import 它，requirements.txt 里却一行没写。间接依赖一旦断链，
    用户就会遇到「照文档装完依赖，服务还是起不来」。

只扫**模块顶层**的 import。函数体内的延迟 import 是刻意的可选依赖
（数据库驱动就是这么做的），不算漏声明。

用法：
    python tools/check_declared_deps.py
退出码非 0 表示有未声明的依赖。
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# pip 包名和 import 名经常对不上，这里手工列映射。
# 加新依赖时如果包名和 import 名不同，记得在这里补一条。
ALIAS = {
    "pillow": "PIL",
    "pyyaml": "yaml",
    "python-docx": "docx",
    "python-multipart": "multipart",
    "pymysql": "pymysql",
    "pg8000": "pg8000",
    "pyodbc": "pyodbc",
    "oracledb": "oracledb",
    "openpyxl": "openpyxl",
    "xlrd": "xlrd",
    "pdfplumber": "pdfplumber",
    "reportlab": "reportlab",
    "matplotlib": "matplotlib",
    "pandas": "pandas",
    "requests": "requests",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
}


def declared_names(root: pathlib.Path) -> set[str]:
    """收集 requirements 里声明的包名。

    依赖拆成了两份（requirements.txt 核心 + requirements-db.txt 可选驱动），
    两份都要读。`-r xxx.txt` 这种引用行跟着展开，别把它当成包名。
    """
    names: set[str] = set()
    seen_files: set[str] = set()

    def read(req: pathlib.Path) -> None:
        if req.name in seen_files or not req.exists():
            return
        seen_files.add(req.name)
        for line in req.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("-r "):
                read(req.parent / line[3:].strip())
                continue
            # 去掉注释和版本约束，只留包名
            head = re.split(r"[#<>=!~\[;]", line)[0].strip()
            if head and not head.startswith("-"):
                names.add(ALIAS.get(head.lower(), head.replace("-", "_")))

    read(root / "requirements.txt")
    return names


def local_module_names(root: pathlib.Path) -> set[str]:
    """项目自己的模块名。

    core/parsers/pdf.py 里写的是 `from model import Dataset`（同包兄弟模块），
    AST 里 module 就是 "model"，光看名字会误判成第三方包。
    这里把所有 .py 文件名和包目录名都收进来当白名单。
    """
    names: set[str] = {"core", "tests", "tools", "app"}
    for p in root.rglob("*.py"):
        if any(part in {".venv", "venv", "__pycache__", "temp", "dist",
                        "outputs", "samples"} for part in p.parts):
            continue
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        names.add(p.stem)
        for part in p.relative_to(root).parts[:-1]:
            names.add(part)
    return names


def top_level_imports(py: pathlib.Path) -> list[str]:
    tree = ast.parse(py.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in tree.body:                     # 只看模块顶层
        if isinstance(node, ast.Import):
            out += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                     # 相对导入，一定是本项目的
                continue
            out.append((node.module or "").split(".")[0])
    return out


def main() -> int:
    declared = declared_names(ROOT)
    known_local = local_module_names(ROOT)
    stdlib = set(sys.stdlib_module_names)

    scan = [p for p in list(ROOT.glob("core/**/*.py")) +
            list(ROOT.glob("*.py")) + list(ROOT.glob("tools/**/*.py"))]
    scan = [p for p in scan if "__pycache__" not in p.parts]

    offenders: list[str] = []
    for py in sorted(scan):
        for name in top_level_imports(py):
            if not name or name.startswith("_"):
                continue
            if name in stdlib or name in known_local:
                continue
            if name not in declared:
                offenders.append(f"{py.relative_to(ROOT)}: {name}")

    if offenders:
        print("以下顶层 import 的第三方包没有写进 requirements.txt：")
        for o in offenders:
            print("  " + o)
        return 1
    print(f"OK：{len(scan)} 个文件顶层 import 的第三方包均已声明")
    return 0


if __name__ == "__main__":
    sys.exit(main())
