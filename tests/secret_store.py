"""凭据加密的回归：core/secret.py 与 core/db/store.py 的加密落盘。

覆盖：加解密往返、盘上确实是密文、读回内存是明文、兼容老的明文文件、
密文解不开时的降级（换机器 / 换 Windows 账户）。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import secret  # noqa: E402
from core.db import store  # noqa: E402
from tests._helpers import ok, rmtree_quiet  # noqa: E402


def main():
    # --- 1. 加解密往返 ---
    for s in ("", "p@ssw0rd", "带中文的密码·测试", "x" * 300,
              'ab#c=def"ghi$j\n换行', " 前后有空格 "):
        got = secret.unprotect(secret.protect(s))
        ok(f"往返一致: {s[:14]!r}", got == s)
    ok("空串不加密（省得盘上一堆空密文）", secret.protect("") == "")
    ok("没前缀的当明文（兼容老文件）", secret.unprotect("plain-old") == "plain-old")
    ok("密文能被识别出来", secret.is_encrypted(secret.protect("a"))
       and not secret.is_encrypted("a"))
    # 配置文件是人也会手改的，值不保证是字符串。非字符串一律当明文原样放行，
    # 不能在读配置这一步就抛 AttributeError 把连接列表整个弄没。
    for v in (25, 0, True, None, ["a"], {"k": "v"}):
        ok(f"非字符串 {v!r} 原样穿过（不加密）", secret.protect(v) == v)
        ok(f"非字符串 {v!r} 原样返回（不解密）", secret.unprotect(v) == v)
        ok(f"非字符串 {v!r} 不算密文", secret.is_encrypted(v) is False)

    # --- 2. 落盘形态 ---
    # 每次开新目录（同 log_rotate 的说明）：不依赖上一轮清干净
    base = ROOT / "temp"
    base.mkdir(parents=True, exist_ok=True)
    d = pathlib.Path(tempfile.mkdtemp(prefix="secret_store_", dir=str(base)))
    f = d / "db_connections.json"
    orig_path = store._path
    store._path = lambda: str(f)
    try:
        saved, note, pub = store.save({
            "kind": "mysql", "name": "测试库", "host": "10.0.0.5",
            "user": "root", "password": "SuperSecret123", "remember_password": True,
        })
        ok("保存成功", saved, note)
        raw = f.read_text(encoding="utf-8")

        cfg = store.get("测试库")
        ok("能按名字取回配置", cfg is not None)
        ok("内存里的密码是明文（能拿去连库）", cfg is not None
           and cfg.password == "SuperSecret123")

        # 落盘形态按平台分开断言：DPAPI 是 Windows 自带的，其它平台上
        # secret.protect 原样返回，所以「盘上必须是密文」这条只在 Windows 成立。
        # 这里不整组跳过 —— 跳过就意味着非 Windows 的 CI 在这块是空白门禁。
        if secret._IS_WINDOWS:
            ok("盘上没有明文密码", "SuperSecret123" not in raw)
            ok("盘上确实带密文前缀", secret.PREFIX in raw)
        else:
            ok("非 Windows 平台不加密（原样落盘）", "SuperSecret123" in raw)
            ok("非 Windows 平台也不加前缀", secret.PREFIX not in raw)
            ok("非 Windows 平台读回来仍是完整明文", cfg is not None
               and cfg.password == "SuperSecret123")

        pub2 = store.list_public()
        ok("公开清单只报「有没有密码」",
           pub2 and pub2[0]["has_password"] is True and "password" not in pub2[0])

        # 改端口时不带密码 —— 应沿用已存的那份（加密后这条规则不能坏）
        store.save({"kind": "mysql", "name": "测试库", "host": "10.0.0.5",
                    "user": "root", "port": 3307, "password": "",
                    "remember_password": True})
        cfg2 = store.get("测试库")
        ok("改配置时密码不被洗掉", cfg2 is not None and cfg2.password == "SuperSecret123")
        ok("端口确实改了", cfg2 is not None and cfg2.port == 3307)

        # --- 3. 兼容加密之前存的明文文件 ---
        f.write_text(json.dumps({"connections": [
            {"kind": "sqlite", "name": "老配置", "password": "plain-old-pass"}
        ]}, ensure_ascii=False), encoding="utf-8")
        old = store.get("老配置")
        ok("老的明文文件照样能读", old is not None and old.password == "plain-old-pass")

        # --- 4. 密文解不开（换机器 / 换 Windows 账户）---
        f.write_text(json.dumps({"connections": [
            {"kind": "mysql", "name": "外来配置",
             "password": secret.PREFIX + "bm90LWEtcmVhbC1ibG9i",
             "remember_password": True}
        ]}, ensure_ascii=False), encoding="utf-8")
        items = store.list_public()
        ok("解不开的密文标成「密码丢失」", bool(items and items[0].get("password_lost")))
        ok("同时不再声称「已记住密码」",
           bool(items and items[0]["remember_password"] is False))
        ok("给出重新填写的提示",
           bool(items and "重新填写" in (items[0].get("password_note") or "")))
        ok("密码位是空的，不会拿乱码去连库",
           store.get("外来配置").password == "")
    finally:
        store._path = orig_path
        rmtree_quiet(d)

    print("\n['secret_store'] 完成")


if __name__ == "__main__":
    main()
