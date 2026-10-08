"""一次跑完全部回归套件。

套件已经到两位数了，逐个手敲既累又容易漏，尤其容易漏掉不在 Python 里的那个
（前端逻辑要 node 跑、接口 fuzz 最慢所以最常被跳过）。

用法：
    .venv\\Scripts\\python.exe tests/run_all.py          # 全跑
    .venv\\Scripts\\python.exe tests/run_all.py --quick   # 跳过最慢的 no500
    .venv\\Scripts\\python.exe tests/run_all.py db_readonly frontend_logic

注意：所有套件都做了「不破坏用户数据」的处理 —— 只删自己建的表，
不会把你在界面上存着的会话数据清掉。
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (名字, 命令, 是否慢)  —— 命令为 None 表示用 node 跑
SUITES: list[tuple[str, list[str] | None, bool]] = [
    # 放最前：归档验证要先删掉最老的那份备份，而受限环境的删除额度是按 turn
    # 累计的，排到后面会被前面的套件用光（同下面 history_export 那条注释）。
    ("log_rotate", ["tests/log_rotate.py"], False),
    ("smoke", ["tests/smoke.py"], False),
    ("compat", ["tests/compat.py"], False),
    ("missing_value", ["tests/missing_value.py"], False),
    ("category_missing", ["tests/category_missing.py"], False),
    ("merge_conflict", ["tests/merge_conflict.py"], False),
    # 排在靠前：这一套要验证「删除」行为，而受限环境的批量删除守卫是
    # 一个 turn 内删满 50 个文件就开始拦 —— 排到后面会被前面的套件把额度用光。
    ("history_export", ["tests/history_export.py"], False),
    ("recoverable_numeric", ["tests/recoverable_numeric.py"], False),
    ("dup_columns", ["tests/dup_columns.py"], False),
    ("type_matrix", ["tests/type_matrix.py"], False),
    ("sort_semantics", ["tests/sort_semantics.py"], False),
    ("pie_negative", ["tests/pie_negative.py"], False),
    ("per_metric_type", ["tests/per_metric_type.py"], False),
    ("detail_sort", ["tests/detail_sort.py"], False),
    ("demo_data", ["tests/demo_data.py"], False),
    ("pdf_extract", ["tests/pdf_extract.py"], False),
    ("vision_config", ["tests/vision_config.py"], False),
    # 配置向导生成的 YAML 必须能被服务端原样读回：向导拼 YAML 的转义、
    # yaml 解析、core.config.load 三方对账。api_key 带引号/反斜杠/=/# 等
    # 特殊字符逐一验过。全程写项目 temp/ 下的临时目录，不碰真实配置。
    ("setup_wizard", ["tests/setup_wizard.py"], False),
    # LLM 熔断器回归：服务挂了不能让每个请求各等一遍超时把线程池占满。
    # 不发真实网络（指向没监听的本地端口 + monkeypatch load），纯逻辑，秒级。
    # 以前漏收进 SUITES —— 等于这个容错路径从没被回归守过，现在补上。
    ("llm_breaker", ["tests/llm_breaker.py"], False),
    ("axis_recognize", ["tests/axis_recognize.py"], False),
    ("multi_chart", ["tests/multi_chart.py"], False),
    ("db_readonly", ["tests/db_readonly.py"], False),
    ("dashboard", ["tests/dashboard.py"], False),
    ("check_frontend", ["tests/check_frontend.py"], False),
    # 顶层 import 的第三方包必须写进 requirements.txt。纯静态检查，
    # 秒级完成，但挡住的是「照文档装完依赖服务仍起不来」这类问题。
    ("check_deps", ["tools/check_declared_deps.py"], False),
    # 分发包的排除规则：db_connections.json 有数据库密码、config.local.yaml
    # 有 API key、outputs/ 是真实业务数据，都不该被打进包发出去。纯路径
    # 判断、造临时目录即跑即删，秒级。此前这条安全承诺没任何测试守着。
    ("make_dist", ["tests/make_dist.py"], False),
    ("frontend_logic", None, False),        # node + jsdom
    # 不标 slow：它们只建表不删文件，而且恰恰是最该常跑的几环 ——
    # 「重活跑着的时候服务还通不通」「SESSION 遍历有没有竞态」
    # 这类问题，其他套件全绿也照样可能存在。
    # 轮次由脚本自己按 --quick 减半。
    ("session_race_unit", ["tests/session_race_unit.py"], False),
    ("history_cleanup", ["tests/history_cleanup.py"], False),
    ("history_fp_cache", ["tests/history_fp_cache.py"], False),
    ("secret_store", ["tests/secret_store.py"], False),
    ("image_recognize", ["tests/image_recognize.py"], False),
    ("race_safety", ["tests/race_safety.py"], False),
    ("concurrency", ["tests/concurrency.py"], False),
    ("no500", ["tests/no500.py"], True),
]


def find_node() -> str:
    """找一个真能执行代码的 node（优先托管版本）。

    先试 PATH 上的 node —— Linux / CI 上就这么装。Windows 上是托管版本优先，
    因为系统里那个可能是坏的或版本过低。
    """
    cands: list[str] = []

    # PATH 上的（CI、以及把 node 装到系统路径的机器）
    for name in ("node", "nodejs"):
        found = shutil.which(name)
        if found:
            cands.append(found)

    # Windows：托管版本优先，路径最深的（版本号最大）排前面
    for pat in [
        os.path.expanduser(r"~\.workbuddy\binaries\node\versions\*\node.exe"),
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Users\*\AppData\Local\Programs\nodejs\node.exe",
    ]:
        cands += sorted(glob.glob(pat), reverse=True)

    seen = set()
    for p in cands:
        if p in seen:
            continue
        seen.add(p)
        try:
            r = subprocess.run([p, "-e", "console.log('ok')"],
                               capture_output=True, timeout=25)
            if r.returncode == 0 and b"ok" in r.stdout:
                return p
        except Exception:
            continue
    return ""


def run_one(run: list[str], timeout: int = 900) -> tuple[bool, str, str, float]:
    t0 = time.time()
    try:
        r = subprocess.run(run, cwd=ROOT, capture_output=True, timeout=timeout)
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        return r.returncode == 0, out, err, time.time() - t0
    except subprocess.TimeoutExpired:
        return False, "", f"超时（>{timeout}s）", time.time() - t0


def main() -> int:
    quick = "--quick" in sys.argv
    picked = [a for a in sys.argv[1:] if not a.startswith("--")]
    py = sys.executable
    node = find_node()

    results: list[tuple[str, bool, float, str, str]] = []
    for name, cmd, slow in SUITES:
        if picked and name not in picked:
            continue
        if slow and quick:
            print(f"{name:<20} 跳过（--quick）")
            continue
        if cmd is None:
            if not node:
                print(f"{name:<20} 跳过（没找到可用的 node）")
                continue
            run = [node, "tests/frontend_logic.js"]
        else:
            run = [py] + cmd
            # 这两个自己认识 --quick：轮次 / 压测时长减半，
            # 跑得快一点但结论仍然成立
            if name == "concurrency" and quick:
                run.append("--quick")
            if name == "race_safety" and quick:
                run.append("--quick")

        ok, out, err, dt = run_one(run)
        tail = ""
        for line in reversed((out + "\n" + err).strip().splitlines()):
            if line.strip():
                tail = line.strip()
                break
        results.append((name, ok, dt, tail, out + "\n" + err))
        print(f"{name:<20} {'PASS' if ok else 'FAIL'}  {dt:5.1f}s  {tail[:64]}")

    bad = [r for r in results if not r[1]]
    print("\n" + "=" * 72)
    if bad:
        print(f"失败 {len(bad)} / {len(results)} 套：")
        for name, _, _, tail, full in bad:
            print(f"\n--- {name} ---")
            print(f"  末行: {tail[:160]}")
            lines = [ln for ln in full.strip().splitlines() if ln.strip()]
            for ln in lines[-18:]:
                print("   " + ln[:160])
        return 1
    print(f"全部 {len(results)} 套回归通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
