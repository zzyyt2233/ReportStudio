"""需求解析：把一句话翻译成 ReportSpec。

两条路都汇到这里：
- 有大模型：走 rules 兜底 + 模型理解，模型失败自动降级
- 没大模型：纯关键词规则，够应付「按X统计Y画柱状图降序」这类句式
"""

from __future__ import annotations

import re

from .model import ChartSpec, Dataset, ReportSpec

CHART_KEYWORDS = [
    ("combo", ("双轴", "柱线", "组合图")),
    ("stack_bar", ("堆叠", "堆积", "堆叠柱")),
    ("pie", ("饼图", "饼状", "占比", "环形", "圆环")),
    ("scatter", ("散点", "相关性", "分布点")),
    ("line", ("折线", "趋势", "走势", "时间序列")),
    ("bar", ("柱状", "柱形", "条形", "柱图", "直方图")),
]

AGG_KEYWORDS = [
    ("mean", ("平均", "均值")),
    ("count", ("计数", "个数", "数量统计", "笔数")),
    ("max", ("最大", "最高", "峰值")),
    ("min", ("最小", "最低")),
    ("sum", ("合计", "求和", "总计", "总额")),
]

DESC_WORDS = ("降序", "从大到小", "由大到小", "从高到低", "由高到低",
              "从多到少", "由多到少", "倒序", "逆序")
ASC_WORDS = ("升序", "从小到大", "由小到大", "从低到高", "由低到高",
             "从少到多", "由少到多", "正序")


def _pick(text: str, table: list[tuple[str, tuple[str, ...]]]) -> str | None:
    for key, words in table:
        if any(w in text for w in words):
            return key
    return None


def _find_column(text: str, columns: list[str]) -> str | None:
    """在候选列名里找被提到的那个。

    用户说话不会照抄列名 —— 列名「月份」，他说「按月」；
    列名「销售额」，他说「销售额」或「销售」。所以逐级放宽匹配：
    完全命中 → 去后缀后命中 → 列名去掉修饰词后的主干出现在句子里。
    """
    cands = sorted([c for c in columns if c], key=len, reverse=True)
    for level in (0, 1, 2):
        hits = []
        for c in cands:
            key = _alias(c, level)
            if not key or key not in text:
                continue
            hits.append(c)
        if hits:
            return max(hits, key=len)
    return None


# 列名里常见的前后缀装饰，匹配时逐级剥掉
_ALIAS_HEAD = ("所属", "统计")
_ALIAS_TAIL = ("份", "名称", "时间", "日期", "编号", "代码")


def _alias(name: str, level: int) -> str:
    s = str(name).strip()
    if level >= 1:
        for t in _ALIAS_TAIL:
            if s.endswith(t) and len(s) > len(t):
                s = s[: -len(t)]
                break
        for h in _ALIAS_HEAD:
            if s.startswith(h) and len(s) > len(h):
                s = s[len(h):]
                break
    if level >= 2 and len(s) > 2:
        s = s[:2]
    return s


def _mentions(text: str, col: str, loose: bool = False) -> bool:
    """判断这句话有没有点到这一列。loose =True 时允许剥掉后缀后命中。"""
    if col and col in text:
        return True
    return loose and bool(_alias(col, 1)) and _alias(col, 1) in text


def rule_parse(text: str, ds: Dataset) -> ReportSpec | None:
    """纯规则解析。够不上就返回 None，交给上层决定怎么办。"""
    cols = [c.name for c in ds.columns]
    num_cols = [c.name for c in ds.columns if c.dtype == "number"]
    text = (text or "").strip()
    if not text or not cols:
        return None

    ctype = _pick(text, CHART_KEYWORDS)
    agg = _pick(text, AGG_KEYWORDS) or "sum"

    text_cols = [c for c in cols if c not in num_cols]

    # 认指标：被点名的数值列，就是要统计的那个「数」。
    ys = [c for c in num_cols if _mentions(text, c)]
    if not ys:
        ys = [c for c in num_cols if _mentions(text, c, True)]

    # 认横轴：**只在文本列里找**。
    #
    # 这里以前是「先在文本列里找，找不到就退到所有列」，于是用户说
    # 「按资产报酬率降序」，资产报酬率被抢去当了横轴；紧接着下面那句
    # 「横轴不能等于数值」的保护又把它换成另一个指标 —— 用户想看的那个数
    # 在图上一次都没出现，而且没有任何报错。指标永远只是指标，不当横轴。
    x = _find_column(text, text_cols)
    if not x and not ys:
        # 一个指标都没提到，那用户指的可能是某一列本身（「按代码画条形图」）
        x = _find_column(text, cols)
    if not x:
        x = text_cols[0] if text_cols else (cols[0] if cols else None)

    # 散点图是例外：横轴本来就该是另一个数值列
    if ctype == "scatter" and len(ys) >= 2:
        x, ys = ys[0], ys[1:]

    if not ys:
        # 用户一个指标都没点名 → 只挑一个画，别一股脑挂满所有数列（图会糊成一团）
        ys = [c for c in num_cols if c != x][:1] or num_cols[:1]
    if not ys:
        return None

    # 排序：先定方向，再定「按哪一列」。
    # 一句话里可能提了两个指标（「资产报酬率和资产负债率降序」），
    # 所以取离「降序」这个词最近的那个被点名的指标，而不是无脑取第一个。
    sort_by, order = "", "asc"
    word = _pick(text, [(w, (w,)) for w in DESC_WORDS] +
                       [(w, (w,)) for w in ASC_WORDS])
    if word:
        order = "desc" if word in DESC_WORDS else "asc"
        pos = text.rfind(word)
        named = [c for c in ys if c in text]
        if named:
            sort_by = min(named, key=lambda c: abs(text.find(c) - pos))
        else:
            sort_by = ys[0]

    m = re.search(r"(?:前|top|TOP)\s*(\d{1,3})", text)
    limit = int(m.group(1)) if m else None

    title_match = re.match(r"^(.{0,20}?)(?:的|之)?(?:图表|图|分析|报告)$", text)
    title = title_match.group(1) if title_match else (text[:20] or "数据分析")

    return ReportSpec(
        title=title.strip() or "数据分析报告",
        charts=[ChartSpec(type=ctype or "bar", title=f"{ys[0]} 按 {x}",
                          x=x, y=ys, agg=agg, sort_by=sort_by,
                          sort_order=order, limit=limit)],
        metrics=ys[:4],
    )


def parse(text: str, ds: Dataset) -> tuple[ReportSpec, str]:
    """返回 (spec, 来源说明)。模型失败会静默降级到规则。"""
    fallback = rule_parse(text, ds)

    try:
        from .llm import LLMUnavailable, NL2SPEC_PROMPT, chat
    except ImportError:
        return (fallback or ReportSpec()), _confidence(text, ds, fallback)

    cols = [c.name for c in ds.columns]
    num_cols = [c.name for c in ds.columns if c.dtype == "number"]
    user = (f"候选列名（必须从中挑选，不得自创）：{cols}\n"
            f"其中数值列：{num_cols}\n"
            f"用户需求：{text}")
    try:
        data = chat(NL2SPEC_PROMPT, user, want_json=True)
    except Exception:
        return (fallback or ReportSpec()), _confidence(text, ds, fallback)

    spec = _spec_from_json(data, ds)
    if spec is None or not spec.charts:
        return (fallback or ReportSpec()), _confidence(text, ds, fallback)
    return spec, "大模型解析"


def _confidence(text: str, ds: Dataset, spec: ReportSpec | None) -> str:
    """规则解析到底有没有真的听懂。没听明白就明说，别让用户以为是精确的。"""
    if spec is None:
        return "未能理解，已按表格结构猜测，请检查下方配置"
    hit_chart = _pick(text, CHART_KEYWORDS) is not None
    hit_col = any(_mentions(text, c.name) for c in ds.columns)
    if hit_chart or hit_col:
        return "规则解析"
    return "没太看懂这句话，已按表格结构猜测，请检查下方配置"


def _spec_from_json(data: dict, ds: Dataset) -> ReportSpec | None:
    """校验模型给的 JSON：列名必须真实存在，不存在就丢掉这条图。"""
    cols = {c.name for c in ds.columns}
    charts = []
    for c in (data.get("charts") or []):
        if not isinstance(c, dict):
            continue
        x = c.get("x")
        ys = [y for y in (c.get("y") or []) if y in cols]
        if x not in cols or not ys:
            continue
        charts.append(ChartSpec(
            type=c.get("type") if c.get("type") in
                                  ("bar", "line", "pie", "scatter",
                                   "stack_bar", "combo") else "bar",
            title=c.get("title") or f"{ys[0]} 按 {x}",
            x=x, y=ys,
            agg=c.get("agg") if c.get("agg") in
                                ("sum", "mean", "count", "max", "min") else "sum",
            sort_by=c.get("sort_by") or "",
            sort_order=c.get("sort_order") or "asc",
            limit=c.get("limit") if isinstance(c.get("limit"), int) else None,
        ))
    if not charts:
        return None
    metrics = [m for m in (data.get("metrics") or []) if m in cols]
    if not metrics:
        metrics = charts[0].y[:4]
    return ReportSpec(title=data.get("title") or "数据分析报告",
                      charts=charts, metrics=metrics)
