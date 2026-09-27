"""表格解析：xlsx / xls / csv。

核心是两件容错的事：
1. 表头行自动探测（前 15 行打分），界面上可手动改
2. 列类型推断（>80% 可转数字才算数值列），界面上可手动改
"""

from __future__ import annotations

import csv
import re
import uuid
from typing import Any

from ..model import Column, Dataset

DATE_PATTERNS = [
    r"^\d{4}[-/年]\d{1,2}([-/月]\d{1,2})?日?$",
    r"^\d{4}[-/]\d{1,2}$",
    r"^\d{1,2}[-/月]\d{1,2}日?$",
    r"^\d{4}年\d{1,2}季度$",
    r"^\d{4}Q[1-4]$",
    r"^\d{4}\s?[上下]半年$",
]

SUMMARY_WORDS = ("合计", "总计", "小计", "平均", "汇总")

# 缺失值占位符：真实数据里到处都是 N/A、-、—、/、空单元格、无、暂无……
# 这些不是「有效数值」，但也不是「有效文本」——列类型推断时若把它们算进
# 分母，会让一个 90% 是数字的列被误判成文本，整列出不了图。所以统一识别、
# 统一排除。
_BLANK_TOKENS = {
    "", "-", "--", "---", "—", "–", "/", "\\", "／", "－",
    "n/a", "na", "n.a.", "null", "none", "nan", "nil", "nd", "n.d.",
    "无", "空", "暂无", "未知", "缺失", "不详", "未填",
}


def _is_blank_token(v: Any) -> bool:
    """识别常见的「缺失值」占位符：N/A、-、—、/、无、空、null……

    返回 True 时表示该格没有有效信息，列类型推断/标志列识别时应像空单元格
    一样被排除，而不是当成一个奇怪的文本值。
    """
    if v is None:
        return True
    s = _to_half_width(str(v)).strip().lower()
    return s in _BLANK_TOKENS


# 带修饰词的数字：约1.2万 / 近3亿 / 超5000 —— 这类值 _is_number 会判成非数字，
# 若整列都是这种，infer_dtype 会把整列误判成文本、出不了图且无提示。
_RECOVERABLE_NUM = re.compile(
    r"(约|近|超|大约|大概|approx)\s*[-+]?\d[\d,]*\.?\d*\s*(千|万|亿)?",
    re.IGNORECASE,
)


def _looks_recoverable_numeric(v: Any) -> bool:
    """值里是否含有『带修饰词的数字』（约1.2万、近3亿、超5000）。

    纯数字、或带 千/万/亿 但无修饰词（如「1.2万」）本身能被 _is_number 识别，不算；
    只有带「约/近/超」这类修饰、会被误判文本的情况才命中，用来发提示而非硬凑数字。
    """
    if v is None or _is_blank_token(v):
        return False
    s = _to_half_width(str(v).strip())
    return bool(_RECOVERABLE_NUM.search(s))


# 中文量级单位：数字后紧跟这些字时，按倍率换算（「1.2万」→ 12000）
_CN_UNIT = {"千": 1e3, "万": 1e4, "亿": 1e8}


def _to_half_width(s: str) -> str:
    """全角数字/符号转半角：『３５６．５』→『356.5』。全角百分号也会转成 %。"""
    out = []
    for ch in s:
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:        # 全角 ASCII 可视区
            out.append(chr(o - 0xFEE0))
        elif ch == "\u3000":             # 全角空格
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _clean_num_str(s: str) -> tuple[str, bool] | None:
    """把各种脏数字字符串清理成 (可 float 的字符串, 是否百分比)。

    处理：全角→半角、Unicode 减号、中文量级单位、货币符号、千分位、括号负数。
    含无法识别的文字（如「约」「元」「人」）时返回 None，交由上层判为文本。
    """
    s = _to_half_width(str(s).strip())
    s = s.replace("\u2212", "-").replace("\u00a0", "")   # − 和 NBSP
    # 「数字 + 千/万/亿」→ 数字 × 倍率
    def _repl(m: "re.Match") -> str:
        num = m.group(1).replace(",", "").replace("，", "")
        try:
            return str(float(num) * _CN_UNIT[m.group(2)])
        except ValueError:
            return m.group(0)
    s = re.sub(r"([-+]?\d[\d,]*\.?\d*)\s*(千|万|亿)", _repl, s)
    pct = s.endswith("%") or s.endswith("％")
    s = s.replace("%", "").replace("％", "")
    s = s.replace("¥", "").replace("￥", "").replace("$", "")
    s = s.replace(",", "").replace("，", "")
    if s.startswith("(") and s.endswith(")"):      # 财务负数写法
        s = "-" + s[1:-1]
    s = s.strip()
    if s in ("", "-", "+", "."):
        return None
    return s, pct


def _is_number(v: Any) -> bool:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return True
    if v is None:
        return False
    res = _clean_num_str(v)
    if res is None:
        return False
    try:
        float(res[0])
        return True
    except ValueError:
        return False


def _to_number(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if v is None:
        return None
    res = _clean_num_str(v)
    if res is None:
        return None
    s, pct = res
    try:
        n = float(s)
    except ValueError:
        return None
    return n / 100 if pct else n


def _is_date(v: Any) -> bool:
    s = str(v).strip()
    if not s:
        return False
    return any(re.match(p, s) for p in DATE_PATTERNS)


def _clean(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v if v else None
    return v


def read_grid(path: str) -> list[list[Any]]:
    """把文件读成二维原始网格，不做任何解释。默认取第一张表。"""
    sheets = read_sheets(path)
    sheets = [(n, g) for n, g in sheets if g and any(any(v is not None for v in r) for r in g)]
    return sheets[0][1] if sheets else []


def read_sheets(path: str) -> list[tuple[str, list[list[Any]]]]:
    """Excel 返回所有非空 Sheet；CSV/TSV 只有一张。

    返回 [(sheet_name, grid), ...]，界面上可以勾选要哪几张。
    """
    lower = path.lower()
    if lower.endswith((".xlsx", ".xlsm")):
        return _read_xlsx(path)
    if lower.endswith(".xls"):
        return _read_legacy_xls(path)
    if lower.endswith(".csv"):
        return [("Sheet1", _read_delimited(path, ","))]
    if lower.endswith(".tsv"):
        return [("Sheet1", _read_delimited(path, "\t"))]
    raise ValueError(f"不支持的表格格式：{path}")


def _read_xlsx(path: str) -> list[tuple[str, list[list[Any]]]]:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
    out = []
    for name in wb.sheetnames:
        ws = wb[name]
        grid = [[_clean(c) for c in row] for row in ws.iter_rows(values_only=True)]
        grid = _fill_merged(grid)
        grid = [r for r in grid if any(v is not None for v in r)]
        out.append((name, grid))
    return out


def _read_legacy_xls(path: str) -> list[tuple[str, list[list[Any]]]]:
    """.xls 是老格式，openpyxl 打不开，走 xlrd。"""
    try:
        import xlrd
    except ImportError:
        raise ValueError("无法读取 .xls（缺少 xlrd），请另存为 .xlsx 后重试")
    book = xlrd.open_workbook(path)
    out = []
    for sh in book.sheets():
        grid = []
        for r in range(sh.nrows):
            grid.append([_clean(sh.cell_value(r, c)) for c in range(sh.ncols)])
        grid = _fill_merged(grid)
        out.append((sh.name, grid))
    return out


def _read_delimited(path: str, default_delim: str) -> list[list[Any]]:
    """文本文件：先试 UTF-8，乱码就退回 GBK，分隔符自动嗅探。"""
    text = _read_text_any_encoding(path)
    if not text.strip():
        return []

    delim = default_delim
    first = text.strip().splitlines()[0]
    for cand in ("\t", ";", "|", ","):
        if cand in first:
            counts = {c: first.count(c) for c in ("\t", ";", "|", ",") if c in first}
            delim = max(counts, key=counts.get)
            break

    rows: list[list[Any]] = []
    for row in csv.reader(text.splitlines(), delimiter=delim):
        rows.append([_clean(c) for c in row])
    return rows


def _read_text_any_encoding(path: str) -> str:
    for enc in ("utf-8-sig", "gbk", "gb18030", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _fill_merged(grid: list[list[Any]]) -> list[list[Any]]:
    """把 None 用左侧/上方的非空值填充，缓解合并单元格带来的空洞。"""
    if not grid:
        return grid
    for r in range(len(grid)):
        for c in range(1, len(grid[r])):
            if grid[r][c] is None and grid[r][c - 1] is not None:
                grid[r][c] = grid[r][c - 1]
    return grid


def detect_header_row(grid: list[list[Any]], scan: int = 15) -> int:
    """给前 scan 行打分，挑最像表头的一行。"""
    best_idx, best_score = 0, -1e9
    limit = min(scan, len(grid))
    for i in range(limit):
        row = grid[i]
        vals = [v for v in row if not _is_blank_token(v)]
        if len(vals) < 2:
            continue
        fill_ratio = len(vals) / max(len(row), 1)
        unique_ratio = len({str(v) for v in vals}) / len(vals)
        text_ratio = sum(0 if _is_number(v) or _is_date(v) else 1 for v in vals) / len(vals)
        score = fill_ratio * 2 + unique_ratio * 2 + text_ratio * 2
        if any(w in str(v) for v in vals for w in SUMMARY_WORDS):
            score -= 3
        if i + 1 < len(grid):
            nxt = [v for v in grid[i + 1] if v is not None and str(v).strip() != ""]
            if nxt:
                num_ratio = sum(1 if _is_number(v) else 0 for v in nxt) / len(nxt)
                score += num_ratio * 1.5
        if score > best_score:
            best_score, best_idx = score, i
    return best_idx


RATE_WORDS = ("率", "占比", "比重", "比率", "渗透", "份额")


def infer_dtype(values: list[Any], name: str = "") -> tuple[str, str | None]:
    vals = [v for v in values if not _is_blank_token(v)]
    if not vals:
        return "text", None
    n = len(vals)
    if sum(1 for v in vals if _is_number(v)) / n >= 0.8:
        if sum(1 for v in vals if str(v).strip().endswith(("%", "％"))) / n >= 0.8:
            return "number", "%"
        # 「毛利率」这列名写着率、值却全落在 0~1 之间，那就是小数形式的百分比。
        # 不认出来的话后面会被当成普通数值去求和，得出「毛利率合计 4.60」这种废话。
        if any(w in str(name) for w in RATE_WORDS):
            nums = [_to_number(v) for v in vals]
            nums = [x for x in nums if x is not None]
            if nums and all(-1.5 <= x <= 1.5 for x in nums):
                return "number", "%"
        return "number", None
    if sum(1 for v in vals if _is_date(v)) / n >= 0.8:
        return "date", None
    return "text", None


def resolve_flag_column(values: list[Any],
                        name: str = "") -> tuple[dict[str, float], str] | None:
    """识别「用文字代替 1」的标记列。

    国内表格里很常见：985/211/双一流 这类列，多数行填 1/0 或 1/2，
    少数行直接写「双一流」「是」这种字。直接丢掉就少统计一批数据。

    只在很窄的条件下才敢这么猜，避免误伤。满足其一即可：
    A. 数字部分全部是 0 或 1 —— 典型布尔标记列
    B. 数字部分只有 1 和 2（1=是、2=否 的编码），
       且那个词**恰好就是列名本身**（填「双一流」= 这所大学是双一流）
    另外还要求：非数字部分只有一种取值，且这个词不长于 12 个字符。
    """
    vals = [v for v in values if not _is_blank_token(v)]
    if not vals:
        return None
    nums = [_to_number(v) for v in vals if _is_number(v)]
    if not nums:
        return None
    bad = [str(v).strip() for v in vals if not _is_number(v)]
    distinct = set(bad)
    if len(distinct) != 1:
        return None
    token = distinct.pop()
    if not token or len(token) > 12:
        return None

    num_set = set(nums)
    case_a = num_set <= {0.0, 1.0}
    case_b = num_set <= {1.0, 2.0} and token == str(name).strip()
    if not (case_a or case_b):
        return None
    return {token: 1.0}, token


def build_dataset(grid: list[list[Any]], name: str, source_type: str,
                  header_row: int | None = None,
                  type_overrides: dict[str, str] | None = None,
                  dataset_id: str | None = None) -> Dataset:
    """从原始网格构造 Dataset。header_row/type_overrides 可由界面指定。"""
    warnings: list[str] = []
    flag_maps: dict[int, dict[str, float]] = {}
    if not grid:
        return Dataset(id=dataset_id or uuid.uuid4().hex[:8], name=name,
                       source_type=source_type, columns=[], rows=[],
                       warnings=["文件是空的"], total_rows=0)

    if header_row is None:
        header_row = detect_header_row(grid)
    header_row = max(0, min(header_row, len(grid) - 1))

    raw_header = grid[header_row]
    width = max(len(r) for r in grid)
    header: list[str] = []
    used: set[str] = set()
    for i in range(width):
        h = raw_header[i] if i < len(raw_header) else None
        h = str(h).strip() if h is not None and str(h).strip() != "" else f"列{i + 1}"
        # 重名列必须改名，否则行数据按列名做键时会互相覆盖、静默丢数。
        # 而且改名后要接着查重：像「金额_2 / 金额 / 金额」这种情况，
        # 只改一次会造出第二个「金额_2」，照样撞名。
        if h in used:
            k = 1
            while f"{h}_{k}" in used:
                k += 1
            h = f"{h}_{k}"
        used.add(h)
        header.append(h)

    body = grid[header_row + 1:]
    body = [r for r in body if any(v is not None and str(v).strip() != "" for v in r)]

    columns: list[Column] = []
    for i, hname in enumerate(header):
        col_vals = [r[i] if i < len(r) else None for r in body]
        dtype, unit = infer_dtype(col_vals, hname)
        inferred_dtype = dtype
        if type_overrides and hname in type_overrides:
            dtype = type_overrides[hname]
            unit = None
        # ② 带修饰词的数字（约1.2万 等）若占多数，整列会被 infer_dtype 误判成文本、
        # 出不了图且无提示。这里显式告警，让用户决定要不要改列类型（不擅自硬凑数字）。
        if inferred_dtype == "text" and dtype == "text":
            nonblank = [v for v in col_vals if not _is_blank_token(v)]
            if nonblank:
                rec = sum(1 for v in nonblank if _looks_recoverable_numeric(v))
                if rec >= max(1, int(0.5 * len(nonblank))):
                    warnings.append(
                        f"「{hname}」有 {rec} 个像「约1.2万」这样带修饰词的数字，"
                        f"被识别为文本、相关图表可能不出数；如需计入可在导入前去掉「约/近/超」等词，"
                        f"或手动把该列类型改为数值")
        # 标记列兜底：把「双一流」这类文字标记还原成 1，并在报告里说明
        flag_map: dict[str, float] = {}
        if dtype == "number":
            bad_vals = [v for v in col_vals
                        if not _is_blank_token(v) and not _is_number(v)]
            if bad_vals:
                got = resolve_flag_column(col_vals, hname)
                if got:
                    flag_map, token = got
                    warnings.append(
                        f"「{hname}」有 {len(bad_vals)} 处填的是「{token}」而不是数字，"
                        f"已按 1 处理（0/1 标记列的常见写法）")
                else:
                    warnings.append(f"「{hname}」有 {len(bad_vals)} 个非数字值，已按空值处理")
        columns.append(Column(name=hname, dtype=dtype, unit=unit))
        if flag_map:
            flag_maps[i] = flag_map

    rows: list[dict] = []
    for r in body:
        rec: dict[str, Any] = {}
        for i, col in enumerate(columns):
            raw = r[i] if i < len(r) else None
            if col.dtype == "number":
                rec[col.name] = _to_number(raw)
                if rec[col.name] is None and raw is not None:
                    key = str(raw).strip()
                    if key in flag_maps.get(i, {}):
                        rec[col.name] = flag_maps[i][key]
            else:
                rec[col.name] = str(raw).strip() if raw is not None else None
        rows.append(rec)

    # 末行是合计行的话，提示但不删（用户可能就是要它）
    last = [v for v in (body[-1] if body else []) if v is not None]
    if last and any(w in str(last[0]) for w in SUMMARY_WORDS):
        warnings.append("最后一行疑似合计/总计行，默认已从图表和指标中剔除"
                        "（可在「多文件怎么整合」下方勾选包含它）")

    return Dataset(
        id=dataset_id or uuid.uuid4().hex[:8],
        name=name,
        source_type=source_type,
        columns=columns,
        rows=rows,
        raw_text="\n".join(
            "\t".join("" if v is None else str(v) for v in r) for r in grid[:50]
        ),
        warnings=warnings,
        total_rows=len(rows),
    )


_EXT_ST = {".csv": "csv", ".tsv": "csv"}


def _file_ext(path: str) -> str:
    return path.lower().rsplit(".", 1)[-1]


def _short_name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def parse_table_file(path: str, dataset_id: str | None = None
                     ) -> tuple[Dataset, list[list[Any]]]:
    """兼容旧调用：只返回第一张表。"""
    sheets = read_sheets(path)
    name = _short_name(path)
    st = _EXT_ST.get("." + _file_ext(path), "excel")
    if not sheets:
        return Dataset(id=dataset_id or uuid.uuid4().hex[:8], name=name,
                       source_type=st, columns=[], rows=[],
                       warnings=["文件里没有数据"], total_rows=0), []
    sname, grid = sheets[0]
    full = name if len(sheets) == 1 else f"{name}｜{sname}"
    ds = build_dataset(grid, name=full, source_type=st, dataset_id=dataset_id)
    return ds, grid


def parse_table_all_sheets(path: str) -> list[tuple[Dataset, list[list[Any]]]]:
    """每个 Sheet 单独出一张 Dataset。多 sheet 时名字带上表名。"""
    sheets = read_sheets(path)
    name = _short_name(path)
    st = _EXT_ST.get("." + _file_ext(path), "excel")
    out = []
    for sname, grid in sheets:
        if not grid:
            continue
        full = name if len(sheets) == 1 else f"{name}｜{sname}"
        out.append((build_dataset(grid, name=full, source_type=st), grid))
    if not out:
        ds = Dataset(id=uuid.uuid4().hex[:8], name=name, source_type=st,
                     columns=[], rows=[], warnings=["文件里没有数据"],
                     total_rows=0)
        out.append((ds, []))
    return out
