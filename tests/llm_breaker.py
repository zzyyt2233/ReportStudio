"""LLM 熔断器回归：服务挂了时不能让每个请求都各等一遍超时。

背景：出图线程池只有 4 个坑。LLM 服务挂 / 网络黑洞时，没有熔断的话
每个 /api/generate 都要各自等满读超时（默认 60 秒），四个坑很快全被
等超时的请求占满，服务退化。有熔断后：第一个请求付一次超时代价，
之后 5 分钟内所有 LLM 调用 0ms 直接降级本地摘要。

不发真实网络请求（指向没监听的本地端口 + monkeypatch load）。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import llm

_fail = 0


def _broken_cfg():
    """指向一个没有监听的端口：连接立刻被拒绝，测试不用等 3 秒。"""
    return {"llm": {"enabled": True, "base_url": "http://127.0.0.1:59999",
                    "api_key": "k", "model": "m", "timeout": 60}}


def ok(label, cond):
    global _fail
    print(f"  {'✓' if cond else '✗ 失败!'} {label}")
    if not cond:
        _fail += 1
    return cond


def main():
    real_load = llm.load
    llm.load = _broken_cfg
    # 熔断状态是模块级的，先清干净，别被同进程其它测试污染
    llm._breaker.update({"until": 0.0, "fingerprint": ""})
    try:
        # 1) 失败一次应触发熔断
        t0 = time.perf_counter()
        try:
            llm.chat("s", "u")
            ok("第一次调用应该失败", False)
            return finish()
        except llm.LLMUnavailable:
            pass
        ok("第一次调用失败（连接拒绝）", True)
        ok(f"熔断已激活（until > now：{llm._breaker['until'] > time.time()}）",
           llm._breaker["until"] > time.time())

        # 2) 熔断期间：毫秒级直接拒绝，不再碰网络
        t0 = time.perf_counter()
        try:
            llm.chat("s", "u")
            ok("熔断期间应该直接拒绝", False)
            return finish()
        except llm.LLMUnavailable as e:
            ok(f"熔断期间直接拒绝（{time.perf_counter() - t0:.4f}s，提示含改配置指引）",
               "config" in str(e))
        dt = time.perf_counter() - t0
        ok(f"熔断拒绝耗时 {dt * 1000:.1f}ms（< 50ms 才算没碰网络）", dt < 0.05)

        # 3) vision 也走同一个熔断器
        try:
            llm.vision(os.path.join(ROOT, "web", "favicon.ico")
                       if os.path.exists(os.path.join(ROOT, "web", "favicon.ico"))
                       else __file__, "prompt")
            ok("vision 在熔断期间应该直接拒绝", False)
            return finish()
        except llm.LLMUnavailable:
            ok("vision 在熔断期间也直接拒绝", True)

        # 4) 改配置（指纹变化）→ 熔断立即解除
        llm.load = lambda: {"llm": {"enabled": True,
                                    "base_url": "http://127.0.0.1:59998",
                                    "api_key": "k2", "model": "m2",
                                    "timeout": 60}}
        try:
            llm.chat("s", "u")       # 会再失败一次（新地址也不通）
        except llm.LLMUnavailable:
            pass
        # 旧指纹的熔断不应该挡住新配置……新配置自己失败又触发新熔断，
        # 但那次失败是真的网络失败（不是被旧熔断挡的）。
        # 验证方式：熔断的指纹确实换成了新配置的。
        ok("改配置后熔断指纹跟着换（旧熔断不残留）",
           llm._breaker["fingerprint"] == "http://127.0.0.1:59998|k2|m2")

        # 5) polish_summary 遇到熔断应该静默返回原文（降级不打断出报告）
        llm.load = _broken_cfg
        llm._breaker.update({"until": 0.0, "fingerprint": ""})
        summary = "华东销售额 42000，环比增长 12%。"
        out = llm.polish_summary(summary)
        ok(f"polish_summary 失败时返回原文", out == summary)
    finally:
        llm.load = real_load
        llm._breaker.update({"until": 0.0, "fingerprint": ""})
    return finish()


def finish():
    print()
    if _fail:
        print(f"llm_breaker：{_fail} 项失败")
        sys.exit(1)
    print("llm_breaker 全部通过 ✅")


if __name__ == "__main__":
    main()
