"""outputs 存量管理回归：/api/history 的总量字段 + /api/history/cleanup 批量清理。

为什么需要：outputs 只进不出，存量 1640 份 / 1.8GB 时没有入口清。
清理接口删的是正常报告、不可逆，所以重点测三件事：
1. days 下限 7 天——防手滑把刚生成的报告批量清掉；
2. 没有标准时间戳的目录绝不能碰（老格式 / 手动建的目录分不清新旧）；
3. apply=False 只统计不删，真删必须走 apply=True。
全程用临时目录，不碰真实 outputs。
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as rs

_fail = 0


def ok(label, cond):
    global _fail
    print(f"  {'✓' if cond else '✗ 失败!'} {label}")
    if not cond:
        _fail += 1
    return cond


def _mk_report(parent: str, name: str, with_chart=False) -> str:
    d = os.path.join(parent, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "report.html"), "w", encoding="utf-8") as f:
        f.write("<html>" + name + "</html>")
    if with_chart:
        os.makedirs(os.path.join(d, "charts"), exist_ok=True)
        with open(os.path.join(d, "charts", "a.png"), "wb") as f:
            f.write(b"\x89PNG" * 64)      # 256 字节，让 _dir_size 有东西可算
    return d


def _stamp(days_ago: int) -> str:
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y%m%d_%H%M%S")


def main():
    # 临时目录必须和项目同盘：_history_entry 里 os.path.relpath(d, ROOT)
    # 跨盘符（C: 临时目录 vs E: 项目）会直接 ValueError。
    os.makedirs(os.path.join(ROOT, "temp"), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="hist_cleanup_", dir=os.path.join(ROOT, "temp"))
    real_out = rs.OUT_DIR
    rs.OUT_DIR = tmp
    rs._HIST_CACHE.clear()
    try:
        old_dir = _mk_report(tmp, f"销售分析_{_stamp(100)}", with_chart=True)
        new_dir = _mk_report(tmp, f"周报_{_stamp(1)}")
        manual_dir = _mk_report(tmp, "manual_notes")   # 没有时间戳 → cleanup 不许碰
        os.makedirs(os.path.join(tmp, "残片_20250101_000000.deleted"), exist_ok=True)
        with open(os.path.join(tmp, "loose_file.txt"), "w") as f:
            f.write("x")

        # ——— 1. /api/history 的 grand_* ———
        h = rs.history(q="", limit=50)
        ok(f"grand_total = 3（旧+新+无时间戳，残片和散文件不算）→ {h['grand_total']}",
           h["grand_total"] == 3)
        ok("grand_size > 0（含旧报告 charts 里的 PNG）", h["grand_size"] > 0)
        hq = rs.history(q="周报", limit=50)
        ok(f"搜索过滤不影响 grand_total（3）→ {hq['grand_total']}，total=1 → {hq['total']}",
           hq["grand_total"] == 3 and hq["total"] == 1)

        # ——— 2. days 下限 ———
        r = rs.history_cleanup({"days": 3, "apply": True})
        ok("days=3 被拒绝", not r.get("ok") and "7" in str(r.get("error", "")))
        r = rs.history_cleanup({"days": "abc", "apply": True})
        ok("days 非数字被拒绝", not r.get("ok"))

        # ——— 3. apply=False 只统计不删 ———
        r = rs.history_cleanup({"days": 30, "apply": False})
        ok(f"统计出 1 份旧报告 → {r.get('removed')}", r.get("removed") == 1)
        ok(f"统计出体积 > 0 → {r.get('freed')}", r.get("freed", 0) > 0)
        ok("统计模式没真删（旧目录还在）", os.path.isdir(old_dir))

        # ——— 4. apply=True 真删 ———
        rs._HIST_CACHE.clear()     # 模拟缓存里有旧条目
        rs._HIST_CACHE[os.path.basename(old_dir)] = {"mtime": 0, "entry": {}}
        r = rs.history_cleanup({"days": 30, "apply": True})
        ok(f"删掉 1 份 → {r.get('removed')}", r.get("removed") == 1)
        ok("旧报告目录没了", not os.path.exists(old_dir))
        ok("缓存里的旧条目被清掉", os.path.basename(old_dir) not in rs._HIST_CACHE)

        # ——— 5. 不能误伤 ———
        ok("一天前的新报告还在", os.path.isdir(new_dir))
        ok("没有时间戳的目录还在（分不清新旧，宁留勿删）", os.path.isdir(manual_dir))

        # ——— 6. 删完之后 grand_* 跟着变 ———
        h2 = rs.history(q="", limit=50)
        ok(f"清理后 grand_total = 2 → {h2['grand_total']}", h2["grand_total"] == 2)
    finally:
        rs.OUT_DIR = real_out
        rs._HIST_CACHE.clear()
        # 测试目录自己收尾
        import shutil
        try:
            shutil.rmtree(tmp)
        except BaseException:
            pass
    return finish()


def finish():
    print()
    if _fail:
        print(f"history_cleanup：{_fail} 项失败")
        sys.exit(1)
    print("history_cleanup 全部通过 ✅")


if __name__ == "__main__":
    main()
