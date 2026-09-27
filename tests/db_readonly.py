"""数据库取数（只读）回归测试。

覆盖三件事，按重要性排序：
1. **只读闸门真的拦得住** —— 写操作、DDL、多语句、注释绕过、可写 CTE、文件读写函数
2. **闸门被绕过也写不进去** —— 直连 SQLite 做 INSERT/DROP，靠 mode=ro 兜住
3. **取数能走通出图链路** —— SQL 结果转 Dataset 后能配图、生成报告、导出

外加：接口在畸形输入下不许 5xx；测试不许破坏用户已有的数据表。
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _helpers import (ROOT, IsolatedSession, make_client, remove_file,  # noqa: E402
                      temp_db_path)

from core.db.connect import DBConfig, connect  # noqa: E402
from core.db.guard import SQLRejected, is_readonly  # noqa: E402
from core.db.query import describe_table, list_tables, run_to_dataset  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ 失败! ") + msg)
    if not cond:
        FAILS.append(msg)


def hr(t: str) -> None:
    print("\n" + "=" * 66)
    print(t)
    print("=" * 66)


def build_shop_db() -> str:
    """造一个小型业务库当靶子。

    刻意用每次运行唯一的文件名：受限环境里 os.remove 会被批量删除守卫拦下，
    如果沿用固定文件名，上一次跑的库会残留、建表就报 already exists。
    不依赖「能删文件」这个前提，测试才稳。
    """
    db = temp_db_path(f"shop_{os.getpid()}_{int(time.time() * 1000) % 100000}.db")
    remove_file(db)
    c = sqlite3.connect(db)
    c.executescript("""
    CREATE TABLE sales(
        id INTEGER PRIMARY KEY, province TEXT, channel TEXT,
        amount REAL, gross_rate REAL, month TEXT);
    INSERT INTO sales VALUES
     (1,'广东','线上',1200.5,0.42,'1月'),(2,'江苏','线上',980.0,0.38,'1月'),
     (3,'广东','线下',760.0,0.35,'2月'),(4,'山东','线上',1500.0,0.51,'2月'),
     (5,'江苏','线下',430.0,0.29,'3月'),(6,'广东','线上',2100.0,0.55,'3月'),
     (7,'山东','线下',890.0,0.33,'3月');
    CREATE TABLE dim_channel(name TEXT, note TEXT);
    INSERT INTO dim_channel VALUES ('线上','自营'),('线下','门店');
    """)
    c.commit()
    c.close()
    return db


# ═══════════════════════ 1. 只读闸门 ═══════════════════════

def test_guard() -> None:
    hr("1 · 只读闸门：写操作 / 绕过写法必须全部拦下")

    must_block = [
        ("DROP TABLE users", "删表"),
        ("DELETE FROM users", "删数据"),
        ("UPDATE t SET a=1", "改数据"),
        ("INSERT INTO t VALUES (1)", "插数据"),
        ("TRUNCATE TABLE t", "清空"),
        ("ALTER TABLE t ADD c int", "改结构"),
        ("CREATE TABLE t (a int)", "建表"),
        ("SELECT 1; DROP TABLE t", "多语句夹带"),
        ("SELECT * FROM t; DELETE FROM t;", "多语句夹带"),
        ("SELECT 1; -- 注释\nDELETE FROM t", "注释掩护的多语句"),
        ("DR/**/OP TABLE t", "注释拆分关键字"),
        ("PRAGMA writable_schema=1", "改库设置"),
        ("ATTACH DATABASE 'e:/x.db' AS x", "挂载外部库"),
        ("SELECT * FROM t INTO OUTFILE '/tmp/x'", "往服务器写文件"),
        ("SELECT load_extension('evil')", "加载动态库"),
        ("SELECT readfile('/etc/passwd')", "读服务器文件"),
        ("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", "可写 CTE"),
        ("SELECT pg_read_file('/etc/passwd')", "读服务器文件"),
        ("VACUUM", "改库"),
        ("SELECT * FROM t FOR UPDATE", "加锁写"),
        ("GRANT ALL ON t TO x", "改权限"),
        ("CALL do_something()", "调存储过程"),
        ("EXEC xp_cmdshell 'dir'", "执行系统命令"),
        ("EXPLAIN ANALYZE SELECT 1", "EXPLAIN 可能触发写"),
        ("SET SESSION TRANSACTION READ ONLY", "改会话"),
        ("BEGIN; SELECT 1", "事务包裹"),
        ("SELECT 1 WHERE 1=1; /* */ DELETE FROM t", "块注释掩护的多语句"),
        ("REPLACE INTO t VALUES (1)", "REPLACE 写"),
        ("SELECT * FROM t LOCK TABLE", "加表锁"),
    ]
    leaked = 0
    for sql, why in must_block:
        allowed, _ = is_readonly(sql)
        if allowed:
            leaked += 1
            print(f"    ✗ 漏网（{why}）: {sql}")
    check(leaked == 0, f"{len(must_block)} 条危险写法全部拦下（漏网 {leaked} 条）")

    must_allow = [
        "SELECT 1",
        "select * from t",
        "SELECT a, SUM(b) FROM t GROUP BY a ORDER BY 2 DESC",
        "WITH s AS (SELECT a, SUM(b) b FROM t GROUP BY a) SELECT * FROM s",
        "SELECT '请勿 DROP 这张表' AS note",
        "SELECT `delete` FROM t",
        'SELECT "update" FROM t',
        "SELECT * FROM t;",
        "SELECT * FROM t -- 随便注释\n",
        "SELECT * FROM t /* DROP */",
        "SELECT $$DROP TABLE t$$ AS s",
        "SELECT (a)[1] FROM t",
        "SELECT CASE WHEN a > 1 THEN 'x' ELSE 'y' END FROM t",
        "SELECT strftime('%Y-%m', d) m, count(*) FROM t GROUP BY m",
    ]
    blocked = 0
    for sql in must_allow:
        allowed, _ = is_readonly(sql)
        if not allowed:
            blocked += 1
            print(f"    ✗ 误拦: {sql}")
    check(blocked == 0, f"{len(must_allow)} 条正常查询全部放行（误拦 {blocked} 条）")


# ═══════════════════════ 2. 连接层兜底 ═══════════════════════

def test_connection_fallback(db: str) -> None:
    hr("2 · 连接层兜底：绕过闸门直连，也必须写不进去")

    cfg = DBConfig(kind="sqlite", path=db, name="兜底测试")
    conn = connect(cfg)
    check(conn.readonly_enforced, "SQLite 以 mode=ro 只读打开（readonly_enforced=True）")

    writes = [
        "INSERT INTO sales VALUES (99,'X','Y',1,1,'1月')",
        "UPDATE sales SET amount=0",
        "DELETE FROM sales",
        "DROP TABLE dim_channel",
        "CREATE TABLE hack(a int)",
        "ALTER TABLE sales ADD COL c int",
    ]
    wrote = 0
    for stmt in writes:
        try:
            conn.raw.execute(stmt)
            conn.raw.commit()
            wrote += 1
            print(f"    ✗ 竟然写进去了: {stmt}")
        except Exception:
            pass
    check(wrote == 0, f"{len(writes)} 条写语句在连接层被数据库拒绝")

    # 数据确实没被动过
    got = conn.raw.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    check(got == 7, f"sales 仍是 7 行（实际 {got}）")
    remaining = [r[0] for r in conn.raw.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    check("dim_channel" in remaining and "hack" not in remaining,
          f"表结构未被改动：{remaining}")
    conn.close()


# ═══════════════════════ 3. 取数 → Dataset ═══════════════════════

def test_query_to_dataset(db: str) -> None:
    hr("3 · 取数转 Dataset（字段类型/百分比列要自动识别）")

    cfg = DBConfig(kind="sqlite", path=db, name="取数测试")
    sql = ("SELECT province AS 省份, SUM(amount) AS 销售额, AVG(gross_rate) AS 毛利率 "
           "FROM sales GROUP BY province ORDER BY 销售额 DESC")
    ds, grid, meta = run_to_dataset(cfg, sql, name="按省份汇总")

    check(ds.total_rows == 3, f"取回 3 行（实际 {ds.total_rows}）")
    types = {c.name: c.dtype for c in ds.columns}
    check(types.get("省份") == "text", f"省份 识别为文本（{types.get('省份')}）")
    check(types.get("销售额") == "number", f"销售额 识别为数值（{types.get('销售额')}）")
    unit = next((c.unit for c in ds.columns if c.name == "毛利率"), None)
    check(unit == "%", f"毛利率 识别为百分比列（unit={unit}）—— 沿用文件导入的加固")
    check(grid[0] == ["省份", "销售额", "毛利率"], f"网格表头正确：{grid[0]}")

    # 聚合数字要对：广东 1200.5+760+2100 = 4060.5
    gd = next((r for r in ds.rows if r["省份"] == "广东"), None)
    check(gd is not None and abs(gd["销售额"] - 4060.5) < 1e-6,
          f"广东销售额 = 4060.5（实际 {gd and gd['销售额']}）")

    # 表结构内省
    conn = connect(cfg)
    tables = [t["name"] for t in list_tables(conn)]
    check("sales" in tables and "dim_channel" in tables, f"表清单读到 {tables}")
    cols = describe_table(conn, "sales")
    check(any(c["name"] == "province" for c in cols), f"列定义读到 {len(cols)} 列")
    conn.close()


# ═══════════════════════ 4. 接口层 ═══════════════════════

def test_api(db: str, client) -> None:
    hr("4 · 接口层：连接/浏览/取数/导入/出图 全链路")

    r = client.get("/api/db/kinds").json()
    kinds = {k["kind"] for k in r.get("kinds", [])}
    check({"sqlite", "mysql", "postgres", "mssql", "oracle"} <= kinds,
          f"支持的库类型：{sorted(kinds)}")
    check(r.get("readonly") is True, "接口明确声明只读模式")

    conn = {"kind": "sqlite", "path": db, "name": "DB测试连接"}
    t = client.post("/api/db/test", json={"connection": conn}).json()
    check(t.get("ok") is True, f"连接测试通过：{t.get('source')}")
    check(t.get("readonly_enforced") is True, "连接测试报告只读已生效")
    check(t.get("table_count") == 2, f"连接测试数到 2 张表（{t.get('table_count')}）")

    t2 = client.post("/api/db/test", json={"connection": {
        "kind": "sqlite", "path": os.path.join(ROOT, "temp", "根本不存在.db")}}).json()
    check(t2.get("ok") is False and "找不到" in (t2.get("error") or ""),
          f"库文件不存在时给白话原因：{t2.get('error')}")

    s = client.post("/api/db/scan", json={"connection": conn}).json()
    check(s.get("ok") is True and len(s.get("tables") or []) == 2,
          f"浏览表清单：{[x['name'] for x in (s.get('tables') or [])]}")

    c = client.post("/api/db/columns", json={"connection": conn,
                                            "table": "sales"}).json()
    check(c.get("ok") is True and len(c.get("columns") or []) == 6,
          f"读列定义 6 列：{[x['name'] for x in (c.get('columns') or [])]}")
    check("SELECT" in (c.get("suggest_sql") or ""), "顺带给出可直接跑的 SELECT")

    b = client.post("/api/db/build_sql", json={
        "kind": "sqlite", "table": "sales",
        "columns": ["province", "SUM(amount)"]}).json()
    check(b.get("ok") is True and '"province"' in b.get("sql", ""),
          f"按列生成 SQL（标识符加了引号）：{b.get('sql', '').splitlines()[:1]}")

    # 取数并导入成数据表
    sql = ("SELECT province AS 省份, SUM(amount) AS 销售额, AVG(gross_rate) AS 毛利率 "
           "FROM sales GROUP BY province")
    q = client.post("/api/db/query", json={"connection": conn, "sql": sql,
                                          "name": "SQL·按省份"}).json()
    check(q.get("ok") is True, "取数成功")
    check(q.get("imported") is True and q.get("dataset"), "结果已登记为数据表")
    check(q.get("row_count") == 3, f"取回 3 行（{q.get('row_count')}）")
    check(q.get("readonly_enforced") is True, "返回体标明只读已生效")
    check(len(q.get("suggest_charts") or []) == 1, "给出图表配置建议（前端可一键预填）")
    ds_id = (q.get("dataset") or {}).get("id")
    check(bool(ds_id), f"拿到数据表 id：{ds_id}")

    # 危险 SQL 必须被接口拦下，且不产生数据表
    for bad in ["DROP TABLE sales", "DELETE FROM sales",
                "SELECT 1; DROP TABLE sales",
                "UPDATE sales SET amount=0"]:
        r = client.post("/api/db/query", json={"connection": conn, "sql": bad}).json()
        check(r.get("ok") is False and r.get("blocked") is True,
              f"接口拦下 `{bad[:34]}`")

    # 只预览不导入
    q2 = client.post("/api/db/query", json={
        "connection": conn, "sql": "SELECT * FROM sales", "import": False}).json()
    check(q2.get("ok") is True and q2.get("imported") is False,
          "import=false 时只回预览、不建表")
    check(("dataset" not in q2), "只预览时返回体里没有 dataset")

    # 取数结果能走通出图 → 生成报告
    gen_dir = None
    if ds_id and q.get("suggest_charts"):
        g = client.post("/api/generate", json={
            "dataset_ids": [ds_id], "merge_mode": "merge", "title": "SQL 取数出图",
            "template": "simple", "charts": q["suggest_charts"],
            "metrics": ["销售额"]}).json()
        check(g.get("ok") is True and g.get("charts") == 1,
              f"SQL 取数结果生成了报告，图数={g.get('charts')}")
        gen_dir = g.get("dir")
        if gen_dir:
            html = os.path.join(gen_dir, "report.html")
            try:
                body = open(html, encoding="utf-8").read()
                check("数据来源" in body, "报告里写明了数据来源")
                check("SQLite" in body or str(db) in body,
                      "报告里含库文件来源信息（精简模板也必须显示提示）")
            except Exception as e:
                check(False, f"读报告失败：{e}")

    # 导出的四种格式也要能出（docx/pdf 走接口，html/md 生成时已落盘）
    # 注意 /api/export 要传生成时返回的 dir，它从内存注册表里取那份报告的上下文
    if gen_dir:
        for kind in ("docx", "pdf"):
            e = client.post("/api/export", json={"kind": kind, "dir": gen_dir}).json()
            check(e.get("ok") is True,
                  f"导出 {kind} 成功" + ("" if e.get("ok") else f"：{e.get('error')}"))
        md = os.path.join(gen_dir, "report.md")
        try:
            md_body = open(md, encoding="utf-8").read()
            check("SQLite" in md_body or str(db) in md_body, "Markdown 里也有来源提示")
        except Exception as e:
            check(False, f"读 report.md 失败：{e}")

        # 回归：报告注册表曾经直接拿路径字符串当键，`E:\a\b` 和 `E:/a/b` 会算成
        # 两份不同的报告 —— 调用方把分隔符换个写法，导出就报「报告已过期」，
        # 而这跟「真的过期」长得一模一样，极难排查。现在键做了归一化。
        alt = gen_dir.replace("\\", "/") if "\\" in gen_dir else gen_dir.replace("/", "\\")
        if alt != gen_dir:
            e2 = client.post("/api/export", json={"kind": "docx", "dir": alt}).json()
            check(e2.get("ok") is True,
                  "换个分隔符写法的 dir 也能命中同一份报告（注册表键已归一化）"
                  + ("" if e2.get("ok") else f"：{e2.get('error')}"))


def test_upload_api(client, db: str) -> None:
    hr("5 · 上传 SQLite 库文件（含非库文件必须被识破）")

    with open(db, "rb") as f:
        r = client.post("/api/db/upload",
                        files={"file": ("shop.db", f.read(), "application/octet-stream")}).json()
    check(r.get("ok") is True, f"上传 .db 成功 -> {r.get('path')}")
    if r.get("ok"):
        c2 = {"kind": "sqlite", "path": r["path"], "name": "上传的库"}
        t = client.post("/api/db/test", json={"connection": c2}).json()
        check(t.get("ok") is True, "上传后的库能正常连上取数")

    # 假装是库文件的 Excel → 必须被文件头校验识破
    xlsx = os.path.join(ROOT, "samples", "销售明细（带合并标题）.xlsx")
    if os.path.exists(xlsx):
        with open(xlsx, "rb") as f:
            r2 = client.post("/api/db/upload",
                             files={"file": ("假的.db", f.read(), "application/octet-stream")}).json()
        check(r2.get("ok") is False and "文件头" in (r2.get("error") or ""),
              f"把 Excel 改名成 .db 会被识破：{r2.get('error')}")

    with open(db, "rb") as f:
        r3 = client.post("/api/db/upload",
                         files={"file": ("x.txt", f.read(), "text/plain")}).json()
    check(r3.get("ok") is False, "非 .db 扩展名直接拒绝")


# ═══════════════════════ 6. 连接配置管理 ═══════════════════════

def test_connection_store(db: str, client) -> None:
    hr("6 · 连接配置：保存/列表/密码不回显/删除")

    # 先清掉上次跑崩可能残留的测试连接，再取「用户原有连接」基线
    for c in (client.get("/api/db/connections").json().get("items") or []):
        if str(c.get("name", "")).startswith("ZZ_测试连接"):
            client.delete(f"/api/db/connections?name={c['name']}")
    before = {c["name"] for c in (client.get("/api/db/connections").json().get("items") or [])}

    name = f"ZZ_测试连接_{os.getpid()}"
    s = client.post("/api/db/connections", json={
        "kind": "sqlite", "path": db, "name": name}).json()
    check(s.get("ok") is True, f"保存连接：{s.get('note')}")
    check("password" not in (s.get("connection") or {}),
          "保存响应里不含 password 字段")

    items = client.get("/api/db/connections").json().get("items") or []
    mine = next((c for c in items if c["name"] == name), None)
    check(mine is not None, "列表里能查到刚保存的连接")
    check(all("password" not in c for c in items), "整个列表都不回显密码")

    # 按名字直接取数（不用再传连接串）
    q = client.post("/api/db/query", json={"connection_name": name,
                                          "sql": "SELECT COUNT(*) AS 行数 FROM sales",
                                          "import": False}).json()
    check(q.get("ok") is True, "用已保存的连接名直接取数成功")

    d = client.delete(f"/api/db/connections?name={name}").json()
    check(d.get("ok") is True, f"删除连接：{d.get('note')}")
    after = {c["name"] for c in (client.get("/api/db/connections").json().get("items") or [])}
    check(after == before, "删除后连接清单回到原样（没污染用户已存的连接）")


# ═══════════════════════ 7. 畸形输入不许 5xx ═══════════════════════

def test_no_5xx(client, db: str) -> None:
    hr("7 · 畸形输入：任何情况都不许 5xx")

    casos = [
        ("query", {}),
        ("query", {"sql": ""}),
        ("query", {"sql": "   "}),
        ("query", {"sql": "SELECT 1", "connection": {}}),
        ("query", {"sql": "SELECT 1", "connection": {"kind": "不存在的库"}}),
        ("query", {"sql": "SELECT 1", "connection": {"kind": "sqlite", "path": ""}}),
        ("query", {"sql": "SELECT 1", "connection": {"kind": "mysql", "host": "127.0.0.1",
                                                     "port": 1, "user": "x"}}),
        ("query", {"sql": "SELECT * FROM 不存在的表",
                   "connection": {"kind": "sqlite", "path": db}}),
        ("query", {"sql": "SELECT 1", "connection_name": "根本没这个连接"}),
        ("query", {"sql": "SELECT 1", "connection": None, "connection_name": None}),
        ("query", {"sql": "SELECT 1" * 500}),
        ("test", {}),
        ("test", {"connection": {"kind": "sqlite"}}),
        ("test", {"connection": {"kind": "postgres", "host": "127.0.0.1", "port": 1}}),
        ("scan", {}),
        ("scan", {"connection": {"kind": "sqlite", "path": os.devnull}}),
        ("columns", {"connection": {"kind": "sqlite", "path": db}}),
        ("columns", {"connection": {"kind": "sqlite", "path": db},
                     "table": "'; DROP TABLE sales;--"}),
        ("columns", {"connection": {"kind": "sqlite", "path": db},
                     "table": "不存在的表"}),
        ("build_sql", {}),
        ("build_sql", {"table": "t", "columns": [None, 1, ""]}),
        ("connections", {"kind": "sqlite"}),
        ("connections", {"kind": "mysql", "host": "h", "name": ""}),
    ]
    bad = []
    for ep, payload in casos:
        if ep == "connections":
            r = client.post("/api/db/connections", json=payload)
        else:
            r = client.post(f"/api/db/{ep}", json=payload)
        if r.status_code >= 500:
            bad.append((ep, payload, r.status_code))
            print(f"    ✗ {ep} {str(payload)[:60]} -> HTTP {r.status_code}")
    check(not bad, f"{len(casos)} 组畸形输入无 5xx（异常 {len(bad)} 组）")

    # 清掉刚才误存进去的测试连接
    client.delete("/api/db/connections?name=")
    for c in (client.get("/api/db/connections").json().get("items") or []):
        if c["name"] in ("", "MySQL / MariaDB · h"):
            client.delete(f"/api/db/connections?name={c['name']}")


def test_sqlite_purity() -> None:
    hr("8 · 产物落盘位置：库文件与配置都必须在 E 盘")
    from core.db.store import _path as conn_path
    p = conn_path()
    check(os.path.abspath(p).upper().startswith("E:"),
          f"连接配置存在 {p}")
    from _helpers import ROOT as R
    check(os.path.abspath(R).upper().startswith("E:"),
          f"项目根在 {R}")


# ═══════════════════════ main ═══════════════════════

def main() -> int:
    db = build_shop_db()
    print(f"靶子库：{db}")

    test_guard()
    test_connection_fallback(db)
    test_query_to_dataset(db)

    client = make_client()
    with IsolatedSession(client) as iso:
        test_api(db, client)
        test_upload_api(client, db)
        test_connection_store(db, client)
        for d in client.get("/api/datasets").json().get("items", []):
            iso.track(d["id"])
        test_no_5xx(client, db)

    test_sqlite_purity()

    print("\n" + "=" * 66)
    if FAILS:
        print(f"数据库只读功能测试失败 {len(FAILS)} 项 ❌")
        for f in FAILS[:20]:
            print(f"  - {f}")
        return 1
    print("数据库只读功能测试全部通过 ✅（含绕过尝试与出图链路）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
