"""只读 SQL 闸门 —— 数据库功能的安全核心。

只允许 SELECT / WITH 查询。任何写操作、DDL、多语句、文件/系统读写都在执行前拦下。

校验顺序（顺序本身是安全的一部分）：
1. 剥注释 —— 否则 `DROP/**/TABLE` 这类写法会躲过关键字扫描
2. 屏蔽字符串/标识符字面量 —— 否则 `SELECT '请勿 DROP'` 会被误报
3. 数语句 —— 分号最多一个且只能在末尾，堵住 `SELECT 1; DROP TABLE t`
4. 首关键字必须是 SELECT / WITH，然后全句扫禁用词

第 4 步对 WITH 同样生效：PostgreSQL 允许可写 CTE，
    WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x
开头是合法的 WITH，真正的写动作藏在括号里 —— 所以不能只看第一个词。

注意：这一层是「尽可能拦住」，真正的兜底在连接层 ——
SQLite 以 mode=ro 打开、网络库连上即设 READ ONLY 会话，即使这里被绕过也写不进去。
"""

from __future__ import annotations

import re

# 只允许这两种开头
_ALLOWED_HEAD = {"SELECT", "WITH"}

# 整词禁用：任何位置出现就拒绝。值是给用户看的原因分类。
_FORBIDDEN_WORDS: dict[str, str] = {
    # —— 写数据 ——
    "INSERT": "写数据", "UPDATE": "写数据", "DELETE": "写数据", "MERGE": "写数据",
    "REPLACE": "写数据", "UPSERT": "写数据", "TRUNCATE": "清空表",
    # —— 改结构 ——
    "CREATE": "改表结构", "ALTER": "改表结构", "DROP": "删表/删库", "RENAME": "改表结构",
    "COMMENT": "改表结构", "VACUUM": "改库", "REINDEX": "改库", "ANALYZE": "改库",
    # —— 权限 / 事务 / 会话 ——
    "GRANT": "改权限", "REVOKE": "改权限", "COMMIT": "事务控制", "ROLLBACK": "事务控制",
    "BEGIN": "事务控制", "SAVEPOINT": "事务控制", "SET": "改会话设置",
    # —— 文件 / 系统 / 跨库 ——
    "ATTACH": "挂载外部库文件", "DETACH": "挂载外部库文件", "PRAGMA": "改库设置",
    "OUTFILE": "往服务器写文件", "DUMPFILE": "往服务器写文件",
    "LOAD_FILE": "读服务器文件", "READFILE": "读服务器文件",
    "WRITEFILE": "写服务器文件", "LOAD_EXTENSION": "加载动态库",
    "COPY": "服务器端导入导出", "CALL": "调存储过程", "DO": "执行代码块",
    "EXEC": "执行命令", "EXECUTE": "执行命令", "PREPARE": "动态拼接 SQL",
    "SHUTDOWN": "关库", "KILL": "杀会话", "BULK": "批量导入",
    "BACKUP": "备份/还原库", "RESTORE": "备份/还原库",
    "OPENROWSET": "跨库读文件", "OPENQUERY": "跨库查询", "OPENDATASOURCE": "跨库连接",
    "DBLINK": "跨库执行", "LO_IMPORT": "大对象导入", "LO_EXPORT": "大对象导出",
    "UTL_FILE": "写服务器文件", "UTL_HTTP": "外联网络", "UTL_TCP": "外联网络",
    "XP_CMDSHELL": "执行系统命令",
    # SELECT ... INTO 在 SQL Server / PostgreSQL 里会建表，MySQL 里能写文件
    "INTO": "SELECT INTO 会建表或写文件",
    # —— 系统过程/包扫描标记（配合函数名判断）——
    "SP_EXECUTESQL": "执行动态 SQL", "DBMS_SQL": "动态 SQL 包",
    "DBMS_LOCK": "调系统锁包", "DBMS_FILE_TRANSFER": "跨库传文件",
    "PG_READ_FILE": "读服务器文件", "PG_READ_BINARY_FILE": "读服务器文件",
    "PG_LS_DIR": "列服务器目录", "PG_STAT_FILE": "读服务器文件",
    "PG_WRITE_FILE": "写服务器文件", "PG_SLEEP": "阻塞数据库",
    "SYS_EXEC": "执行系统命令", "SYS_EVAL": "执行系统命令",
}

# 需要成对出现的写法（单独的词可能只是列名，避免误伤）
_FORBIDDEN_PHRASES: dict[str, str] = {
    "START TRANSACTION": "事务控制",
    "LOCK TABLE": "加表锁",
    "FLUSH TABLES": "改库状态",
    "RESET MASTER": "改库状态",
    "RESET SLAVE": "改库状态",
    "ALTER SESSION": "改会话",
    "ALTER SYSTEM": "改系统参数",
}

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_DOLLAR_TAG_RE = re.compile(r"\$([A-Za-z_]\w*)?\$")


class SQLRejected(Exception):
    """SQL 被只读闸门拦下。message 直接给用户看。"""

    def __init__(self, message: str, keyword: str = ""):
        super().__init__(message)
        self.message = message
        self.keyword = keyword


def _mask(sql: str) -> str:
    """把注释与字符串/标识符字面量替换掉，只留下结构性 token。

    - 注释 → 单个空格。**保留分隔符**很关键：`DROP/**/TABLE` 会变成 `DROP TABLE`
      仍然被扫到；反过来要是拼成一个词，反而可能漏网。
    - 字符串/标识符引用 → `''` 占位。`SELECT '请勿 DROP'` 才不会误报。
    - 未闭合的引号或块注释 → 直接拒绝（源本身就是坏的 SQL，不该放行）。
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        two = sql[i:i + 2]

        # 行注释：-- 或 MySQL 的 #
        if two == "--" or (ch == "#" and two != "#>"):
            j = sql.find("\n", i)
            if j < 0:
                out.append(" ")
                break
            out.append(" ")
            i = j + 1
            continue

        # 块注释：支持嵌套（PostgreSQL / MySQL 都允许）
        if two == "/*":
            depth, j = 1, i + 2
            while j < n and depth:
                if sql[j:j + 2] == "/*":
                    depth += 1
                    j += 2
                elif sql[j:j + 2] == "*/":
                    depth -= 1
                    j += 2
                else:
                    j += 1
            if depth:
                raise SQLRejected("SQL 里有没闭合的块注释 `/* ... */`，请检查后重试。")
            out.append(" ")
            i = j
            continue

        # 单引号字符串：SQL 标准里 '' 是转义；MySQL 还认 \'，一并处理
        if ch == "'":
            j, closed = i + 1, False
            while j < n:
                if sql[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    closed = True
                    j += 1
                    break
                j += 1
            if not closed:
                raise SQLRejected("SQL 里有没闭合的单引号字符串，请检查后重试。")
            out.append("''")
            i = j
            continue

        # 双引号 / 反引号 / SQL Server 方括号：标识符引用，做遮蔽
        # 找不到配对的就当普通字符（PostgreSQL 的 arr[1] 这类别误伤）
        if ch in ('"', "`", "["):
            closer = "]" if ch == "[" else ch
            j = sql.find(closer, i + 1)
            if j > 0:
                out.append("''")
                i = j + 1
                continue

        # PostgreSQL 美元引用：$$...$$ 或 $tag$...$tag$
        if ch == "$":
            m = _DOLLAR_TAG_RE.match(sql, i)
            if m:
                tag = m.group(0)
                j = sql.find(tag, m.end())
                if j > 0:
                    out.append("''")
                    i = j + len(tag)
                    continue

        out.append(ch)
        i += 1
    return "".join(out)


def check(sql: str) -> str:
    """校验 SQL 只读。通过则原样返回；违反策略抛 SQLRejected。

    这里**不改写**用户的 SQL —— 改写容易在方言差异上出错，
    拦截交给这一层，兜底交给连接层的 READ ONLY 会话。
    """
    if not sql or not sql.strip():
        raise SQLRejected("SQL 是空的，先写一条查询。")

    masked = _mask(sql)

    # 语句数：分号最多一个，且后面不能再有内容
    parts = masked.split(";")
    if len(parts) > 2 or (len(parts) == 2 and parts[1].strip()):
        raise SQLRejected(
            "只允许执行一条查询，检测到多条语句（分号后面还有内容）。"
            "请把多余的语句删掉，或一次只跑一条。",
            keyword=";",
        )

    tokens = _WORD_RE.findall(masked)
    if not tokens:
        raise SQLRejected("这条内容里没有可执行的 SQL。")

    head = tokens[0].upper()
    if head not in _ALLOWED_HEAD:
        raise SQLRejected(
            f"这里只允许查数（SELECT / WITH），不执行 `{head}` 这类语句。"
            "当前连接是只读模式，写操作、改表结构、改权限都不会执行。",
            keyword=head,
        )

    upper_words = {t.upper() for t in tokens}
    for word, why in _FORBIDDEN_WORDS.items():
        if word in upper_words:
            raise SQLRejected(
                f"语句里出现了 `{word}`（{why}），只读模式不会执行。"
                "如果确实需要写库，请改用支持写操作的数据库工具，"
                "并给这个工具单独开一个只读账号。",
                keyword=word,
            )

    upper_text = masked.upper()
    for phrase, why in _FORBIDDEN_PHRASES.items():
        if phrase in upper_text:
            raise SQLRejected(
                f"语句里出现了 `{phrase}`（{why}），只读模式不会执行。",
                keyword=phrase,
            )

    return sql.strip()


def is_readonly(sql: str) -> tuple[bool, str]:
    """给界面做即时提示用：返回 (是否允许, 不允许的原因)。不抛异常。"""
    try:
        check(sql)
        return True, ""
    except SQLRejected as e:
        return False, e.message
