"""并发验证：重活跑着的时候，轻量接口还通不通。

改造前，所有端点都是 async def，pandas / matplotlib / SQL 直接跑在事件循环里。
于是一个 /api/generate 期间，/api/status、静态资源、看板全部排队等着。

**必须用真实 HTTP 服务，不能用 TestClient。**
TestClient 靠 anyio 的 BlockingPortal 把请求送进事件循环，而 portal 的调用是
串行排队的：客户端这边不等上一个请求返回就发不出下一个。
于是 async 版和同步版测出来的延迟几乎一样（实测 190ms vs 152ms），
根本反映不出事件循环被占死这件事。测这个必须绕过 portal，
所以下面是把 uvicorn 真正跑起来、用 socket 发请求。

用法：
    .venv\\Scripts\\python.exe tests/concurrency.py            # 完整
    .venv\\Scripts\\python.exe tests/concurrency.py --quick    # 少跑几轮
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

QUICK = "--quick" in sys.argv
ROUNDS = 2 if QUICK else 4
ROWS = 400 if QUICK else 2000

# 样本要有足够体量，否则出图只要 0.1 秒，测不出「重活期间服务还能不能响应」。
PORT = 8791          # 挑个冷门端口，避开 8765（可能是用户正在跑的服务）


def _build_payload() -> str:
    regions = ["华东", "华北", "华南", "西南", "东北"]
    lines = ["日期,区域,产品线,销售额,成本,利润,订单数"]
    for i in range(ROWS):
        m, d = (i % 12) + 1, (i % 28) + 1
        r = regions[i % len(regions)]
        s = 1000 + (i * 37) % 9000
        lines.append(f"2026-{m:02d}-{d:02d},{r},产品线{i % 7},"
                     f"{s},{int(s * 0.62)},{s - int(s * 0.62)},{i % 50 + 1}")
    return "\n".join(lines)


def post(path: str, body: dict, timeout: float = 120) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path: str, timeout: float = 120) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}",
                                timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def start_server() -> subprocess.Popen:
    """把服务真正跑起来（独立进程，跟生产启动方式一致）。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    p = subprocess.Popen(
        [sys.executable, "-c",
         "import uvicorn,app;uvicorn.run(app.app,host='127.0.0.1',"
         f"port={PORT},log_level='error')"],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(120):
        time.sleep(0.5)
        if p.poll() is not None:
            out, err = p.communicate()
            raise SystemExit(f"服务起不来：\n{err.decode('utf-8', 'replace')[-2000:]}")
        try:
            get("/api/status", timeout=3)
            return p
        except Exception:
            continue
    p.kill()
    raise SystemExit("服务 60 秒内没就绪")


CHART_BODY = {
    "dataset_ids": [],       # 填入运行时的表 id
    "title": "并发验证报告",
    "merge_mode": "merge",
    "charts": [
        {"type": "bar", "x": "日期", "y": ["销售额"], "title": "各月销售额"},
        {"type": "line", "x": "日期", "y": ["利润"], "title": "各月利润走势"},
        {"type": "bar", "x": "区域", "y": ["销售额"], "title": "区域销售额"},
        {"type": "pie", "x": "区域", "y": ["利润"], "title": "区域利润占比"},
        {"type": "bar", "x": "产品线", "y": ["销售额"], "title": "产品线销售额"},
        {"type": "line", "x": "日期", "y": ["订单数"], "title": "订单数趋势"},
    ],
}


def heavy_generate(did: str) -> float:
    """跑一次真实的多图报告生成，返回耗时。

    注意 y 必须是**列表**：ChartSpec.y 的类型是 list[str]，传字符串会被
    build_blocks 当成「没有有效指标」，一张图都不画，generate 0.1 秒就跑完 ——
    那样测出来的延迟毫无意义（这个坑踩过一次）。
    """
    body = dict(CHART_BODY, dataset_ids=[did])
    t0 = time.perf_counter()
    j = post("/api/generate", body)
    dt = time.perf_counter() - t0
    if not j.get("ok") or not j.get("charts"):
        raise SystemExit(
            f"generate 没出图（ok={j.get('ok')} charts={j.get('charts')} "
            f"note={j.get('note')}）—— 样本或图表配置有问题，测不出真实负载")
    return dt


def light_probe(stop: threading.Event, out: list) -> None:
    """在重活期间不停打 /api/status，记录延迟。"""
    while not stop.is_set():
        t0 = time.perf_counter()
        try:
            get("/api/status", timeout=60)
        except Exception:
            out.append(float("inf"))
            continue
        out.append((time.perf_counter() - t0) * 1000)
        time.sleep(0.02)


def main() -> None:
    proc = start_server()
    did = ""
    try:
        r = post("/api/paste", {"text": _build_payload(), "name": "并发测试数据"})
        did = r["dataset"]["id"]
        print(f"样本数据表：{did}（{ROWS} 行）\n")

        # 预热：第一次出图要付 matplotlib 字体缓存的代价，不算进基线
        heavy_generate(did)
        print("预热完成（字体缓存已建立）\n")

        # 1) 空载延迟
        idle: list[float] = []
        stop = threading.Event()
        th = threading.Thread(target=light_probe, args=(stop, idle))
        th.start()
        time.sleep(1.5)
        stop.set()
        th.join()
        idle_p50 = statistics.median(idle) if idle else 0.0

        # 2) 出图期间的延迟
        busy: list[float] = []
        stop = threading.Event()
        th = threading.Thread(target=light_probe, args=(stop, busy))
        th.start()
        lasts = [heavy_generate(did) for _ in range(ROUNDS)]
        time.sleep(0.3)
        stop.set()
        th.join()
        busy_p50 = statistics.median(busy) if busy else 0.0
        busy_max = max(busy) if busy else 0.0

        print("轻量接口 /api/status 延迟")
        print(f"    空载 p50      {idle_p50:8.1f} ms")
        print(f"    出图期间 p50  {busy_p50:8.1f} ms")
        print(f"    出图期间 最大 {busy_max:8.1f} ms")
        print(f"    劣化倍数      {busy_p50 / max(idle_p50, 0.1):.1f}x")
        print(f"    单次出图耗时  {statistics.mean(lasts):.1f} s")

        # 3) 两个重活是否真并行
        results: list[float] = []
        t0 = time.perf_counter()
        threads = [threading.Thread(target=lambda: results.append(heavy_generate(did)))
                   for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wall = time.perf_counter() - t0
        serial = sum(results)
        ratio = wall / max(serial, 0.01)
        print()
        print("两个出图请求并发")
        print(f"    并行实际耗时  {wall:.1f} s")
        print(f"    串行理论耗时  {serial:.1f} s")
        print(f"    实际 / 串行   {ratio:.2f}（明显小于 1 才算真并行）")

        print()
        bad = []
        if busy_p50 > 1500:
            bad.append(f"出图期间轻量接口 p50 {busy_p50:.0f}ms，超过 1.5 秒 —— "
                       f"事件循环还在被堵")
        if ratio > 0.8:
            bad.append(f"两个重活接近串行（{ratio:.2f}），并发没生效")
        if bad:
            print("不通过：")
            for b in bad:
                print(f"    - {b}")
            sys.exit(1)
        print(f"通过：出图期间 /api/status p50 {busy_p50:.0f}ms（空载 "
              f"{idle_p50:.0f}ms），两个重活真并行")
    finally:
        if did:
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{PORT}/api/dataset/{did}", method="DELETE")
                urllib.request.urlopen(req, timeout=10).close()
            except Exception:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
