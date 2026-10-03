"""日志落盘与轮转的回归。

覆盖 core/logsetup.py：归档顺序与保留份数、Tee 双写、写满自动归档、install 幂等。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import logsetup  # noqa: E402
from tests._helpers import can_delete, ok, rmtree_quiet  # noqa: E402


class _FakeConsole:
    """替身控制台：只记下写了什么，不真往终端打。"""

    encoding = "utf-8"

    def __init__(self):
        self.buf: list[str] = []

    def write(self, s):
        self.buf.append(s)

    def flush(self):
        pass


def main():
    # 每次都开一个新目录：上一轮的残留可能删不掉（受限环境的删除守卫按额度拦），
    # 复用固定目录名会让「起始状态干净」这个前提不成立，断言就假失败。
    base = ROOT / "temp"
    base.mkdir(parents=True, exist_ok=True)
    d = pathlib.Path(tempfile.mkdtemp(prefix="log_rotate_", dir=str(base)))
    LF = d / "app.log"

    # 归档要先删掉最老的那一份备份。受限环境的批量删除守卫按「一个 turn 内累计
    # 50 个文件」计额度，这套跑在后面时额度可能已经用光，归档本身做不成 ——
    # 那就跳过归档类断言，别把环境限制报成产品问题。
    # （run_all 里这套已排在最前，正常情况下额度是够的。）
    can_rotate = can_delete(d)
    if not can_rotate:
        print("  ⚠ 本 turn 的删除额度已用尽，跳过归档类断言（环境限制，不是产品问题）")

    # --- 归档阈值判定：不需要删文件，任何情况都能验 ---
    LF.write_text("x" * 100, encoding="utf-8")
    logsetup.rotate(LF, max_bytes=1000, keep=3)
    ok("没写满时不动日志", LF.exists() and not (d / "app.1.log").exists())

    if can_rotate:
        LF.write_text("y" * 2000, encoding="utf-8")
        logsetup.rotate(LF, max_bytes=1000, keep=3)
        ok("写满后归档为 app.1.log", (d / "app.1.log").exists() and not LF.exists())
        ok("归档内容原样保留",
           (d / "app.1.log").read_text(encoding="utf-8") == "y" * 2000)

        (d / "app.1.log").write_text("第一代", encoding="utf-8")
        LF.write_text("z" * 2000, encoding="utf-8")
        logsetup.rotate(LF, max_bytes=1000, keep=3)
        ok("旧 .1 顺移成 .2", (d / "app.2.log").read_text(encoding="utf-8") == "第一代")
        ok("新的顶到 .1", (d / "app.1.log").read_text(encoding="utf-8") == "z" * 2000)

        (d / "app.1.log").write_text("A", encoding="utf-8")
        (d / "app.2.log").write_text("B", encoding="utf-8")
        (d / "app.3.log").write_text("C", encoding="utf-8")
        LF.write_text("N" * 2000, encoding="utf-8")
        logsetup.rotate(LF, max_bytes=1000, keep=3)
        ok("超过保留份数不再往后堆",
           not (d / "app.4.log").exists()
           and (d / "app.3.log").read_text(encoding="utf-8") == "B")
        ok("最新的一份留在 .1", (d / "app.1.log").read_text(encoding="utf-8") == "N" * 2000)

    # --- Tee：双写与属性转发（不涉及删文件）---
    sink = logsetup._Sink(d / "tee.log")
    console = _FakeConsole()
    tee = logsetup._Tee(console, sink)
    tee.write("你好")
    sink.flush()
    ok("Tee 照常写控制台", "".join(console.buf) == "你好")
    ok("Tee 同时落盘", (d / "tee.log").read_text(encoding="utf-8") == "你好")
    ok("Tee 转发 encoding 属性", tee.encoding == "utf-8")
    ok("Tee 不冒充终端（isatty=False）", tee.isatty() is False)

    # --- 写满自动归档（把阈值压小，免得真写 5MB）---
    if can_rotate:
        old_max = logsetup.MAX_BYTES
        logsetup.MAX_BYTES = 50
        try:
            auto = logsetup._Sink(d / "auto.log")
            a_tee = logsetup._Tee(None, auto)
            a_tee.write("a" * 40)
            ok("没到阈值不归档", not (d / "auto.1.log").exists())
            a_tee.write("b" * 40)                  # 累计 80 > 50，触发归档
            ok("写满自动归档", (d / "auto.1.log").exists()
               and (d / "auto.1.log").stat().st_size == 80)
            ok("归档后新文件从头开始", (d / "auto.log").exists()
               and (d / "auto.log").stat().st_size == 0)
        finally:
            logsetup.MAX_BYTES = old_max

    # --- install：替换 stdout/stderr 且幂等（跑完必须还回去）---
    old_out, old_err = sys.stdout, sys.stderr
    try:
        target = logsetup.install(d / "inst.log")
        ok("install 返回实际日志路径", target == d / "inst.log")
        cur = sys.stdout
        again = logsetup.install(d / "inst2.log")
        ok("重复 install 不再生效", again is None and sys.stdout is cur)
        print("安装之后 print 应该也留一份")
        sys.stdout.flush()
        body = (d / "inst.log").read_text(encoding="utf-8")
        ok("install 后 print 落进日志", "安装之后 print 应该也留一份" in body)
        ok("stdout 已被换成双写包装",
           isinstance(cur, logsetup._Tee) and cur._console is not None)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        logsetup._installed = False

    rmtree_quiet(d)
    print("\n['log_rotate'] 完成")


if __name__ == "__main__":
    main()
