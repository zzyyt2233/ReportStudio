"""SESSION 遍历的竞态检测（进程内，确定性）。

**为什么不用压测撞**：CPython 的 GIL 保证同一时刻只有一个字节码在跑，
`for k in SESSION.values()` 这样的纯 Python 循环在多数情况下不会在中途
被另一个线程改到 —— 就算去掉快照保护，跑 20 秒压测也是照样通过（实测）。
所以这个缺陷靠压力测试抓不住，得在同进程里主动制造。

做法：一个线程持续遍历 SESSION（模拟 /api/datasets），
主线程在遍历进行中往 SESSION 里塞 / 删键。
只要遍历期间 dict 真的被改，CPython 立刻抛
RuntimeError: dictionary changed size during iteration。

这个脚本**不启动服务**，直接对 app 模块的 SESSION 做文章 ——
它验的是「保护机制本身有效」，不是「线上没出事」。

用法：.venv\\Scripts\\python.exe tests/session_race_unit.py
"""
from __future__ import annotations

import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A  # noqa: E402


def trial(direct: bool, seconds: float = 1.5) -> str:
    """跑一次竞态尝试，返回撞到的异常（没撞到返回空串）。

    direct=True 时模拟「没有快照保护」的老写法：直接遍历 SESSION.values()。
    direct=False 时走 _session_snapshot()，这才是现在的实现。
    """
    A.SESSION.clear()
    for i in range(400):
        A.SESSION[f"seed{i:04d}"] = {"dataset": None, "grid": [[i]]}

    stop = threading.Event()
    hit: list[str] = []

    def reader() -> None:
        t0 = time.perf_counter()
        while not stop.is_set() and time.perf_counter() - t0 < seconds:
            try:
                if direct:
                    # 老写法：遍历期间别人一改就炸
                    for _ in A.SESSION.values():
                        pass
                else:
                    for _ in A._session_snapshot():
                        pass
            except RuntimeError as e:
                hit.append(str(e))
                return

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    # 主线程扮演「同时在导入数据」的另一个请求
    i = 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds and not hit:
        A.SESSION[f"live{i:06d}"] = {"dataset": None, "grid": []}
        if i % 3 == 0 and len(A.SESSION) > 50:
            A.SESSION.pop(next(iter(A.SESSION)), None)
        i += 1
        if i % 40 == 0:
            time.sleep(0)      # 让出 GIL，保证读者有机会跑起来
    stop.set()
    th.join(timeout=5)
    A.SESSION.clear()
    return hit[0] if hit else ""


def main() -> None:
    print("先确认这个竞态真的存在（模拟去掉快照保护的老写法）")
    e = trial(direct=True)
    if not e:
        print("  没撞到 —— 说明本机 CPython 的 GIL 行为跟预期不同，"
              "下面的对照就没有意义了")
        print("  （不影响 _session_snapshot() 本身的正确性，但这一套就当跳过）")
        sys.exit(0)
    print(f"  撞到了：{e}")

    print()
    print("再看现在的实现（走 _session_snapshot）")
    e2 = trial(direct=False, seconds=1.5)
    if e2:
        print(f"  不通过：仍然撞到 {e2}")
        sys.exit(1)
    print("  通过：同样压力下一次都没撞到")


if __name__ == "__main__":
    main()
