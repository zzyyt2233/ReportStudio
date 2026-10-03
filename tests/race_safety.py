"""并发安全回归：SESSION / REPORTS 有没有被并发写坏。

改成同步 def 之后，端点跑在 anyio 线程池里，SESSION 和 REPORTS 从此
可能被多个请求同时读写。CPython 的 dict 在「遍历过程中被改」时会抛
RuntimeError: dictionary changed size during iteration —— 这是本套件要抓的主要故障。

覆盖：
- 一边导入数据表（写 SESSION），一边读 /api/datasets（遍历 SESSION）
- 一边生成报告（写 REPORTS），一边删报告（写 REPORTS）
- 一边 /api/fix 改表，一边把那张表删掉
- 任何一个请求返回 5xx 或 RuntimeError 就判失败

**必须用真实 HTTP 服务**：TestClient 的 portal 串行排队，
根本制造不出并发，跑一百遍也是假通过。

用法：
    .venv\\Scripts\\python.exe tests/race_safety.py
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PORT = 8792
QUICK = "--quick" in sys.argv
DURATION = 5.0 if QUICK else float(os.environ.get("RACE_SECONDS", "12"))

CSV = """日期,区域,销售额,利润
2026-01-01,华东,12000,3200
2026-01-01,华北,9800,2400
2026-01-02,华东,13100,3500
2026-01-02,华南,15300,4100
2026-02-01,华东,14200,3800
2026-02-01,华北,10100,2600
2026-02-02,华南,16800,4500
2026-03-01,华东,15900,4200
2026-03-01,东北,11300,2900
2026-03-02,西南,18200,4900
"""

ERRORS: list[str] = []
LOCKED = threading.Lock()


def call(path: str, body: dict | None = None, method: str = "GET",
         timeout: float = 90) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 4xx/5xx 的响应体里也有内容（detail / error），别扔掉 ——
        # 扔了的话「文案对不对」就没法验了。
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {}
        return e.code, body


def note(msg: str) -> None:
    with LOCKED:
        ERRORS.append(msg)


def check(cond: bool, msg: str) -> None:
    if not cond:
        note(msg)


def start_server() -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    p = subprocess.Popen(
        [sys.executable, "-c",
         "import uvicorn,app;uvicorn.run(app.app,host='127.0.0.1',"
         f"port={PORT},log_level='error')"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(120):
        time.sleep(0.5)
        if p.poll() is not None:
            _, err = p.communicate()
            raise SystemExit(f"服务起不来：\n{err.decode('utf-8', 'replace')[-2000:]}")
        try:
            call("/api/status", timeout=3)
            return p
        except Exception:
            continue
    p.kill()
    raise SystemExit("服务 60 秒内没就绪")


def worker_importer(stop: threading.Event, made: list) -> None:
    """持续导入数据表，同时制造写 SESSION 的压力。"""
    while not stop.is_set():
        st, j = call("/api/paste", {"text": CSV, "name": f"竞态测试{random.randint(1, 9999)}"},
                     "POST")
        check(st == 200, f"/api/paste 返回 {st}")
        if st == 200 and j.get("dataset"):
            made.append(j["dataset"]["id"])
        # 留到 60 张，让 SESSION 有点体积。
        # 注意别指望靠这个撞出 RuntimeError：CPython 的 GIL 下，
        # 纯 Python 的 dict 遍历很难被另一个线程插进来 ——
        # 实测去掉快照保护跑 20 秒也照样通过。
        # 那个缺陷由 tests/session_race_unit.py 在进程内确定性地验证。
        # 本脚本负责的是「真实 HTTP 并发下没有 5xx、接口语义正确」。
        made[:] = made[-60:]
        time.sleep(0.3)


def worker_reader(stop: threading.Event) -> None:
    """持续遍历 SESSION —— 这是最容易撞 RuntimeError 的地方。"""
    while not stop.is_set():
        st, j = call("/api/datasets")
        check(st == 200, f"/api/datasets 返回 {st}")
        check("items" in j, f"/api/datasets 缺 items 键：{list(j)}")
        st, j = call("/api/status")
        check(st == 200, f"/api/status 返回 {st}")


def worker_deleter(stop: threading.Event, made: list) -> None:
    """删掉别的 worker 刚建的表，制造「边用边删」。"""
    while not stop.is_set():
        if not made:
            time.sleep(0.05)
            continue
        did = random.choice(made)
        call(f"/api/dataset/{did}", method="DELETE")
        try:
            made.remove(did)
        except ValueError:
            pass


def worker_fixer(stop: threading.Event, made: list) -> None:
    """一边改表一边可能被别人删掉。"""
    while not stop.is_set():
        if not made:
            time.sleep(0.05)
            continue
        did = random.choice(made)
        st, j = call("/api/fix", {"id": did, "header_row": 1, "types": {}}, "POST")
        # 404 是合理结果（表刚被删了），500 不是
        check(st in (200, 404), f"/api/fix 返回 {st}（允许 200/404）")


def worker_reporter(stop: threading.Event, reports: list) -> None:
    """一边生成报告（写 REPORTS）一边删报告（也写 REPORTS）。"""
    while not stop.is_set():
        items = call("/api/datasets")[1].get("items") or []
        if not items:
            time.sleep(0.05)
            continue
        st, j = call("/api/generate", {
            "dataset_ids": [items[0]["id"]],
            "title": "竞态报告",
            "merge_mode": "merge",
            "charts": [{"type": "bar", "x": "日期", "y": ["销售额"], "title": "销售额"}],
        }, "POST")
        # 400 / 404 都是合理结果：
        #   400 = 一张表都没传（不该发生，但要显式指出）
        #   404 = 选的表在请求发出后被删了 —— 这是竞态，不是缺陷
        # 500 才是问题。
        check(st in (200, 400, 404), f"/api/generate 返回 {st}（允许 200/400/404）")
        if st == 200:
            check(j.get("ok"), f"/api/generate 返回 ok=False：{j.get('error')}")
            if j.get("dir"):
                reports.append(j["dir"])
                reports[:] = reports[-6:]
                call("/api/history", method="DELETE", body={"dir": j["dir"]})


def check_deleted_table_message() -> None:
    """确定性地验一下：用一张已被删掉的表去出图，回的是 404 和说得清楚的提示。

    压测里这个场景靠撞，撞到了也只记一个状态码。这里单独构造一次，
    把「文案是否具体」也钉住 —— 以前一律回「请先导入数据」，
    用户明明导入过，却被告知去导入。
    """
    st, j = call("/api/paste", {"text": CSV, "name": "待删除"}, "POST")
    if st != 200 or not j.get("dataset"):
        note(f"准备阶段 /api/paste 返回 {st}")
        return
    did = j["dataset"]["id"]
    call(f"/api/dataset/{did}", method="DELETE")

    st, j = call("/api/generate", {
        "dataset_ids": [did], "title": "x", "merge_mode": "merge",
        "charts": [{"type": "bar", "x": "日期", "y": ["销售额"], "title": "a"}],
    }, "POST")
    check(st == 404, f"用已删除的表出图应回 404，实际 {st}")
    detail = j.get("detail") or j.get("error") or ""
    check("请先导入数据" not in detail,
          f"提示文案不该再说「请先导入数据」，实际：{detail!r}")
    check("数据表" in detail, f"提示文案应说明是数据表的问题，实际：{detail!r}")


def main() -> None:
    proc = start_server()
    made: list[str] = []
    reports: list[str] = []
    try:
        check_deleted_table_message()

        stop = threading.Event()
        workers = [
            ("导入", lambda: worker_importer(stop, made)),
            ("读 SESSION ×2", lambda: worker_reader(stop)),
            ("读 SESSION ×2b", lambda: worker_reader(stop)),
            ("删表", lambda: worker_deleter(stop, made)),
            ("改表", lambda: worker_fixer(stop, made)),
            ("删表 b", lambda: worker_deleter(stop, made)),
            ("生成+删报告", lambda: worker_reporter(stop, reports)),
        ]
        threads = [threading.Thread(target=fn, daemon=True) for _, fn in workers]
        for t in threads:
            t.start()
        print(f"并发压 {DURATION:.0f} 秒，{len(threads)} 个 worker "
              f"（导入 / 遍历 / 删表 / 改表 / 出报告）...")
        time.sleep(DURATION)
        stop.set()
        for t in threads:
            t.join(timeout=30)

        st, j = call("/api/status")
        check(st == 200, f"压测后 /api/status 返回 {st}")
        check(j.get("ok"), "压测后服务状态异常")

        # 服务必须还活着：再打一次出图
        st, j = call("/api/datasets")
        check(st == 200, "压测后 /api/datasets 失败")

        print()
        if ERRORS:
            uniq = list(dict.fromkeys(ERRORS))
            print(f"不通过：捕获 {len(ERRORS)} 次异常，去重后 {len(uniq)} 种")
            for e in uniq[:20]:
                print(f"    - {e}")
            sys.exit(1)
        print("通过：并发压测期间没有 5xx、没有 RuntimeError，服务压测后仍正常")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
