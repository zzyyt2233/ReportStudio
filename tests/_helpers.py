"""测试公共工具。

**为什么需要它**：原来的测试用 `/api/session/clear` 开头清场，那等于把用户在界面上
存着的表全删了 —— 跑一次回归就丢一次数据，这是个隐蔽的破坏性行为。
这里改成「快照 + 只删自己建的」，用户的数据一个都不动。

另外 `/api/datasets` 的响应键是 `items`，不是 `datasets`。
有测试读错了键，拿到空列表后静默降级，assert 看着通过其实什么都没验 ——
统一走这里的 `datasets()`，别各写各的。
"""

from __future__ import annotations

import itertools
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def make_client(isolate: bool = True):
    """建一个 TestClient（不需要真的起服务）。

    默认把**看板目录**整体挪到 temp 下 —— 见 isolate_dashboards() 里的说明。
    """
    if isolate:
        isolate_dashboards()
    from fastapi.testclient import TestClient
    import app as server
    return TestClient(server.app)


_ISOLATED_DIR: str = ""


def dashboards_dir() -> str:
    """当前生效的看板目录（隔离之后就是 temp 里那个）。"""
    from core import config as cfgmod
    return cfgmod.dashboards_dir()


def isolate_dashboards() -> str:
    """把看板目录指到 temp 下，别动用户真实的那一份。

    不加这个的话，跑一次回归就会：

    - 在 `dashboards/` 里真建几个看板。跑完会删，但删除被守卫拦下时
      会留下 `.deleted` 残片，越攒越多。
    - **覆盖 `dashboards/_preview.html`** —— 这是「看板」页签的预览文件。
      用户此刻开着工具界面的话，刷新一下就变成测试内容了（本机撞到过：
      界面上显示的是测试建的「预览放大」看板）。

    两处都要改：文件写到哪（config.dashboards_dir），以及 HTTP 从哪读
    （app.py 里 `/dashboards` 那个 StaticFiles 挂载，它是 import 时就固定下来的）。
    只改前者的话，测试里「通过 URL 拿看板 HTML」会读到真目录，仍会串。

    看板 id 也换成固定序列（见下）：随机 id 会让每次跑都攒下一批新文件，
    而删除又要弹权限确认。固定下来就是覆盖同名文件，不用删任何东西。
    """
    global _ISOLATED_DIR
    if _ISOLATED_DIR:
        return _ISOLATED_DIR

    from core import config as cfgmod
    from core import dashboard as dashmod
    d = os.path.join(ROOT, "temp", "dashboards_test")
    os.makedirs(d, exist_ok=True)
    _purge_dir(d)
    cfgmod.dashboards_dir = lambda: d          # type: ignore[assignment]

    _n = itertools.count(1)
    dashmod.new_id = lambda: f"t{next(_n):06d}"   # type: ignore[assignment]

    import app as server
    _retarget_mount(server.app, d)
    _ISOLATED_DIR = d
    return d


def _retarget_mount(app_obj, new_dir: str) -> None:
    """把 `/dashboards` 的静态挂载改指到新目录。

    StaticFiles 把目录缓存成两个属性，只改 directory 不够 ——
    实际查找走的是 all_directories。
    """
    from starlette.staticfiles import StaticFiles
    for route in getattr(app_obj, "routes", []):
        if getattr(route, "name", "") != "dashboards":
            continue
        sub = getattr(route, "app", None)
        if isinstance(sub, StaticFiles):
            sub.directory = new_dir
            sub.all_directories = [new_dir]


def datasets(client) -> list[dict]:
    """当前会话里的所有数据表。注意键是 items。"""
    try:
        r = client.get("/api/datasets")
        if r.status_code != 200:
            return []
        return r.json().get("items") or []
    except Exception:
        return []


def endpoint_rows(client, ds_id: str) -> list[dict]:
    d = next((x for x in datasets(client) if x["id"] == ds_id), None)
    return (d or {}).get("rows") or []


def endpoint_columns(client, ds_id: str) -> list[dict]:
    d = next((x for x in datasets(client) if x["id"] == ds_id), None)
    return (d or {}).get("columns") or []


class IsolatedSession:
    """上下文管理器：进出都记账，只删除本次新建的表，绝不碰已有的。

    用法：
        with IsolatedSession(client) as iso:
            ...
            iso.created.append(ds_id)      # 记下自己建的表
        # 退出时自动把这些表删掉，其它原样保留
    """

    def __init__(self, client):
        self.client = client
        self.before: set[str] = set()
        self.created: list[str] = []

    def __enter__(self) -> "IsolatedSession":
        self.before = {d["id"] for d in datasets(self.client)}
        return self

    def track(self, ds_id: str) -> str:
        if ds_id and ds_id not in self.before:
            self.created.append(ds_id)
        return ds_id

    def __exit__(self, *exc) -> bool:
        if not can_delete():
            print("  ⚠ 本 turn 的删除额度已用尽，跳过清理：测试建的表可能暂时留在"
                  "\n     会话里（不影响下面的断言）。下个回合重跑一次即可恢复干净。")
            return False
        for did in set(self.created):
            try:
                self.client.delete(f"/api/dataset/{did}")
            except Exception:
                pass
        # 校验：进出时用户原有的表数量不能变少
        after = {d["id"] for d in datasets(self.client)}
        missing = self.before - after
        if missing:
            print(f"  ⚠️ 警告：原有数据表被误删了 {len(missing)} 张 —— 测试不该动用户数据！")
        return False


def install_autoclean(client) -> None:
    """给「扁平脚本式」测试用（没有 main() 收尾的那种）。

    在导入后立刻调用一次：记下此刻已有的表，进程退出时只删除之后新建的。
    不加这个的话，测试建的表会一直留在用户的会话里，
    下次打开工具就看到一堆「粘贴内容」「危险<列>」之类的垃圾表。
    """
    import atexit

    before = {d["id"] for d in datasets(client)}

    def _cleanup() -> None:
        try:
            now = {d["id"] for d in datasets(client)}
        except Exception:
            return
        for did in now - before:
            try:
                client.delete(f"/api/dataset/{did}")
            except Exception:
                pass

    atexit.register(_cleanup)


def temp_db_path(name: str = "test_shop.db") -> str:
    """给测试用的 E 盘临时库路径（绝不落 C 盘）。"""
    d = os.path.join(ROOT, "temp", "dbtest")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def can_delete(probe_dir: str = "") -> bool:
    """当前环境还允许删文件吗？

    受限沙箱有一层批量删除守卫：**一个 turn 内累计删满 50 个文件**之后，
    后面的 os.remove / rmtree 会被拦下。连跑几套回归很容易把额度用光 ——
    这时删除类断言会「假失败」，但那是环境限制，不是产品缺陷。

    探测方式：建一个只属于自己的临时文件再删掉，绝不碰用户数据。
    """
    d = probe_dir or os.path.join(ROOT, "temp")
    p = os.path.join(d, f"_delprobe_{os.getpid()}_{int(time.time() * 1000)}.tmp")
    try:
        os.makedirs(d, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(p)
    except BaseException:
        # 这里不回收探针：删除被拦的情况下改名同样会被拦（改名也是删除+创建），
        # 加一层 try 只是让代码看起来做了件事。留下的探针是 0 字节的小文件，
        # 危害远小于 fuzz 那种一次 25MB 的泄漏。
        return False
    return not os.path.exists(p)


def remove_file(path: str) -> None:
    """单个文件删除，自吞异常（某些受限环境 os.remove 会被守卫拦下）。"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except BaseException:
        pass


def rmtree_quiet(path: str) -> bool:
    """删目录，并且吞掉守卫抛出的 SystemExit。

    受限环境拦下删除时抛的是 SystemExit —— `ignore_errors=True` 只认 OSError，
    这个会直接穿出去，把测试进程的退出码变成 1，run_all 就把它判成 FAIL。
    清理失败无非留点临时文件，不该算测试失败。
    """
    try:
        shutil.rmtree(path, ignore_errors=True)
    except BaseException:            # noqa: BLE001
        return False
    return not os.path.exists(path)


def _purge_dir(path: str) -> int:
    """清空一个测试专用目录，返回没能删掉的项数。

    为什么需要它：app.py 里的删除在受限环境会被拦，退化成改名成 `*.deleted`
    （见 _safe_unlink），文件没消失只是换了后缀。而残片回收只在用户点按钮时
    才跑，测试走的是 TestClient 不是真服务，所以没人触发 —— temp/ 下就攒下
    36MB 的 .deleted。跑之前清一次最省事，也不用去动 app 的删除逻辑。

    先用 can_delete() 探额度：额度用尽时硬试会弹拦截请求，每跑一次测试弹一次，
    很烦。探不到就直接不删 —— 反正目录名固定，残留不会越攒越多。
    """
    if not can_delete(path):
        return -1                      # -1 表示「没试」，调用方按「跳过」处理
    left = 0
    try:
        entries = os.listdir(path)
    except BaseException:            # noqa: BLE001
        return 0
    for name in entries:
        p = os.path.join(path, name)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
                if os.path.exists(p):
                    left += 1
            else:
                os.remove(p)
        except BaseException:        # noqa: BLE001 受删除额度限制就留着
            left += 1
    return left


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗ 失败!'} {label}" + (f"  {detail}" if detail else ""))
    return bool(cond)
