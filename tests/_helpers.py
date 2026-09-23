"""测试公共工具。

**为什么需要它**：原来的测试用 `/api/session/clear` 开头清场，那等于把用户在界面上
存着的表全删了 —— 跑一次回归就丢一次数据，这是个隐蔽的破坏性行为。
这里改成「快照 + 只删自己建的」，用户的数据一个都不动。

另外 `/api/datasets` 的响应键是 `items`，不是 `datasets`。
有测试读错了键，拿到空列表后静默降级，assert 看着通过其实什么都没验 ——
统一走这里的 `datasets()`，别各写各的。
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def make_client():
    """建一个 TestClient（不需要真的起服务）。"""
    from fastapi.testclient import TestClient
    import app as server
    return TestClient(server.app)


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


def remove_file(path: str) -> None:
    """单个文件删除，自吞异常（某些受限环境 os.remove 会被守卫拦下）。"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except BaseException:
        pass


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗ 失败!'} {label}" + (f"  {detail}" if detail else ""))
    return bool(cond)
