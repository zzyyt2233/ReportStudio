"""格式转换路由：Word <-> PDF 互转（docx / pdf）。"""
from __future__ import annotations

import os
import uuid
import zipfile
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from core.convert import convert_docx_to_pdf, convert_pdf_to_docx
from core.state import TEMP_DIR, _safe

try:
    from docx import Document
    from reportlab.platypus import SimpleDocTemplate
    import pdfplumber
except Exception:  # noqa: BLE001 - 依赖没装时路由仍要能注册，调用才报错
    Document = None
    SimpleDocTemplate = None
    pdfplumber = None

router = APIRouter(prefix="/api", tags=["convert"])


@router.post("/convert")
def convert(files: list[UploadFile] = File(...), to: str = Form(...)):
    """Word <-> PDF 互转，支持一次拖多份。

    单个文件时行为和原来一致（直接返回那个文件，前端不用改）；
    多个文件打包成 zip，失败的单独列在 X-Convert-Failed 头里带回去 ——
    一份转换失败不该让整批白跑，也不该静默少给一个文件。
    """
    if Document is None or SimpleDocTemplate is None or pdfplumber is None:
        raise HTTPException(500, "转换依赖未安装（python-docx / reportlab / pdfplumber）")
    if to not in ("docx", "pdf"):
        raise HTTPException(400, f"目标格式只支持 docx / pdf（收到：{to}）")
    sub = os.path.join(TEMP_DIR, uuid.uuid4().hex[:8])
    os.makedirs(sub, exist_ok=True)
    done: list[tuple[str, str]] = []   # (输出路径, 输出文件名)
    failed: list[str] = []             # 「文件名: 原因」，最后塞进响应头
    engines: set[str] = set()
    for idx, file in enumerate(files or []):
        name = file.filename or f"file{idx}"
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        if ext not in ("docx", "pdf") or ext == to:
            failed.append(f"{name}: 暂只支持 docx 与 pdf 互转"
                          f"（收到 {ext or '无扩展名'} → {to}）")
            continue
        src_path = os.path.join(sub, f"{idx}_" + (_safe(name) or ("file." + ext)))
        try:
            with open(src_path, "wb") as fp:
                while True:
                    chunk = file.file.read(1024 * 1024)
                    if not chunk:
                        break
                    fp.write(chunk)
            out_path = os.path.join(sub, f"{idx}_converted.{to}")
            if ext == "docx" and to == "pdf":
                engines.add(convert_docx_to_pdf(src_path, out_path))
            else:
                convert_pdf_to_docx(src_path, out_path)
                engines.add("python")
            done.append((out_path, os.path.splitext(name)[0] + "." + to))
        except Exception as e:  # noqa: BLE001 - 单份失败要记下来，不能中断整批
            failed.append(f"{name}: {type(e).__name__}: {e}")

    if not done:
        raise HTTPException(400, "没有转换成功的文件；" + "；".join(failed[:5]))

    headers = {"X-Convert-Engine": ",".join(sorted(engines)) or "python"}
    if failed:
        # 头里不能放中文裸字符，quote 一下，前端 decodeURIComponent 还原
        headers["X-Convert-Failed"] = quote("；".join(failed[:20]))

    if len(done) == 1:
        # 单文件：完全保持原有行为，避免为了批量把简单场景也改成 zip
        out_path, out_name = done[0]
        mt = ("application/pdf" if to == "pdf" else
              "application/vnd.openxmlformats-officedocument"
              ".wordprocessingml.document")
        return FileResponse(out_path, filename=out_name, media_type=mt, headers=headers)

    zip_path = os.path.join(sub, "converted.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        used: set[str] = set()
        for p, n in done:
            # 同名文件（不同目录下的 a.docx）会互相覆盖，加序号区分
            nm, i = n, 1
            while nm in used:
                stem, dot_ext = os.path.splitext(n)
                nm = f"{stem}_{i}{dot_ext}"
                i += 1
            used.add(nm)
            zf.write(p, nm)
    return FileResponse(zip_path, filename="converted.zip",
                        media_type="application/zip", headers=headers)
