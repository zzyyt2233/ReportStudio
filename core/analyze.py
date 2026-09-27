"""规则计算：聚合、排序、指标卡、模板化结论。

铁律：所有展示出来的数字都在这里由程序算出，大模型只负责润色措辞，不得改动数值。
"""

from __future__ import annotations

import re
from typing import Any

from .model import ChartSpec, Dataset

AGG_FUNCS = {
    "sum": lambda vals: sum(vals),
    "mean": lambda vals: sum(vals) / len(vals) if vals else 0,
    "max": max,
    "min": min,
    "count": lambda vals: len(vals),
}


def _fmt(n: float) -> str:
    if n is None:
        return "-"
    if abs(n) >= 1e8:
        return f"{n / 1e8:,.2f} 亿"
    if abs(n) >= 1e4:
        return f"{n / 1e4:,.2f} 万"
    if float(n).is_integer():
        return f"{int(n):,}"
    return f"{n:,.2f}"


def numeric_columns(ds: Dataset) -> list[str]:
    return [c.name for c in ds.columns if c.dtype == "number"]


SUMMARY_WORDS = ("合计", "总计", "小计", "平均", "汇总")


def drop_summary_rows(ds: Dataset) -> list[dict]:
    """排除首列含「合计/总计/小计」的行。"""
    if not ds.rows or not ds.columns:
        return ds.rows
    first = ds.columns[0].name
    out = []
    for r in ds.rows:
        v = r.get(first)
        if v is not None and any(w in str(v) for w in SUMMARY_WORDS):
            continue
        out.append(r)
    return out or ds.rows


def working_rows(ds: Dataset, exclude_summary: bool = True) -> list[dict]:
    """真正参与计算的行。是否剔除合计行由用户在界面上决定。"""
    return drop_summary_rows(ds) if exclude_summary else ds.rows


# ------------------------------------------------------- 明细数据表的排序

def _row_sort_key(v, dtype: str) -> tuple:
    """单行排序键。三段结构：(分组, 数值, 文本块序列)。

    分组是必须的：一列里可能混着数字和文字（导入时没认全的类型很常见），
    直接拿 v 去比会撞上「int 和 str 不能比」的 TypeError，整份报告当场崩。
    分三组后同一组内永远只有一种类型可比：

        0 = 数值（按数值大小）
        1 = 文本 / 日期（按自然序，"1月" 排在 "10月" 前面）
        2 = 空值 —— 无论升序降序都沉底

    为什么空值恒沉底：升序时空值在最前，用户第一眼看到的是一片空白行，
    会以为「表坏了 / 数据没了」。沉底至少保证前几行永远有内容。
    """
    if v is None or (isinstance(v, str) and not v.strip()):
        return (2, 0.0, "")
    if dtype == "number" and isinstance(v, (int, float)) and not isinstance(v, bool):
        return (0, float(v), [])
    # 文本/日期走自然序：纯字符串序会把「10月」排在「1月」前面
    return (1, 0.0, _natkey(v))


def sorted_detail_rows(rows: list[dict], col: str, order: str = "asc",
                       dtype: str = "text") -> list[dict]:
    """按一列给明细行排序。**返回新列表**，绝不原地改 ds.rows。

    原地改的代价很大：ds.rows 是会话里那张表本身，改了之后
    「数据预览」「导出这张表」「看板」看到的顺序全都跟着变，
    而用户只是想让**报告里**这一张表排个序。

    空值永远沉底：不管是升序还是降序，带空值的行都排在最末。
    做法把「空值」和「有值」拆成两段分别排，最后把空值段接在后面 ——
    不能靠 sorted(reverse=True) 一刀切，那样会把空值也一并翻到最前，
    和界面上「空值排最后」的提示对不上（也确实更难看）。
    """
    desc = str(order or "asc").lower() == "desc"
    nulls = [r for r in rows if r.get(col) is None
             or (isinstance(r.get(col), str) and not str(r.get(col)).strip())]
    real = [r for r in rows if r not in nulls]
    real.sort(key=lambda r: _row_sort_key(r.get(col), dtype))
    if desc:
        real.reverse()
    return real + nulls


def detail_rows(ds: Dataset, spec) -> list[dict]:
    """报告「明细数据」段要列出的行（已按 spec 排序）。

    排序列在这张表里不存在时**原样返回**，不排序也不报错。
    报告可能同时列出多张表（合并模式下更是拼成一张），
    列名对不上时静默保持原顺序，比抛异常或排出一个全空表都合理。
    """
    by = (getattr(spec, "detail_sort_by", "") or "").strip()
    if not by:
        return ds.rows
    col = next((c for c in ds.columns if c.name == by), None)
    if col is None:
        return ds.rows
    return sorted_detail_rows(ds.rows, by,
                              getattr(spec, "detail_sort_order", "asc") or "asc",
                              getattr(col, "dtype", "text") or "text")


def detail_sort_label(spec, columns=None) -> str:
    """给报告用的一句话说明，如「按「销售额」降序（空值排最后）」。没排序时返回空串。

    columns 传这一张表真实的列名集合：排序列在某张表里不存在时，detail_rows
    会原样返回（不排序），这里也得跟着返回空串 —— 否则会出现「界面说按X排了序、
    表里却没动」的假提示。columns 不传则退化为只看字段有没有填。
    """
    by = (getattr(spec, "detail_sort_by", "") or "").strip()
    if not by:
        return ""
    if columns is not None and by not in columns:
        return ""
    desc = str(getattr(spec, "detail_sort_order", "asc") or "asc").lower() == "desc"
    return f"按「{by}」{'降序' if desc else '升序'}（空值排最后）"


def aggregate(ds: Dataset, spec: ChartSpec) -> dict:
    """按 spec 聚合出 ECharts 需要的 categories + series。"""
    rows = working_rows(ds, getattr(spec, "exclude_summary", True))
    x = spec.x
    y_cols = [c for c in spec.y if c in {col.name for col in ds.columns}]

    # y 轴单位。**每个指标各算各的** —— 一张图里同时画「销售额」和「毛利率」时，
    # 只取第一个指标的单位会让百分比列按原样画（0.15 而不是 15），
    # 图上那根线贴着 0 看不出什么，鼠标悬停还显示 0.15，等于悄悄给错数。
    units: dict[str, str] = {}
    for col in ds.columns:
        if col.name in y_cols:
            units[col.name] = col.unit or ""
    # 保留 unit：单指标时的返回值，饼图/散点图和指标卡还在用它
    unit = units.get(y_cols[0], "") if y_cols else ""

    if not x or not y_cols:
        return {"categories": [], "series": [], "empty": True, "unit": unit,
                "units": units, "effective_type": spec.type, "pie_negative": []}

    # 散点图不做聚合，直接取点
    if spec.type == "scatter":
        pts = []
        for r in rows:
            xv, yv = r.get(x), r.get(y_cols[0])
            if isinstance(xv, (int, float)) and isinstance(yv, (int, float)):
                pts.append([xv, yv])
        return {"categories": [], "series": [{"name": y_cols[0], "data": pts}],
                "scatter": True, "unit": unit, "units": units,
                "effective_type": spec.type, "pie_negative": []}

    buckets: dict[str, list[dict]] = {}
    skipped_empty_cat = 0
    for r in rows:
        key = r.get(x)
        key = "" if key is None else str(key).strip()
        if key == "":
            # 分类列为空（空单元格 / 缺失值）的行，无法放到任何有意义的类别上，
            # 若当成「空」分组会出现一根凭空的柱子，且数字还是聚合出来的，极易误导。
            # 直接剔除，并记个数，最后在结论里提示用户。
            skipped_empty_cat += 1
            continue
        buckets.setdefault(key, []).append(r)

    func = AGG_FUNCS.get(spec.agg, AGG_FUNCS["sum"])
    categories = list(buckets.keys())
    series = []
    for y in y_cols:
        all_vals = [r.get(y) for r in rows if isinstance(r.get(y), (int, float))]
        # 标记列（985/211/双一流 填 1/2 那种）求和会把「否」也算进去，
        # 得出「江苏 321 所双一流」这种离谱数字。这里改成数「是」的条数。
        flag = is_flag_column(all_vals)
        data = []
        for cat in categories:
            vals = [r.get(y) for r in buckets[cat] if isinstance(r.get(y), (int, float))]
            if flag and spec.agg == "sum":
                data.append(sum(1 for v in vals if v == 1))
            else:
                data.append(round(func(vals), 4) if vals else 0)
        series.append({"name": y, "data": data, "flag": flag})

    # ---- 饼图不允许负值：图型在这里统一决策，两个渲染器都听这个结果 ----
    # 饼图表达的是「部分占整体」，负值没有对应的那一块扇形，硬画必然失真：
    #   · matplotlib：直接抛 "Wedge sizes 'x' must be non negative values"，导出 Word/PDF 当场崩
    #   · ECharts：不报错，但负值不参与圆周分配，扇区占比是错的
    #     —— 这个更危险，因为图看着一切正常，数字是错的
    # 取绝对值、或把负值那几行剔掉，都属于替用户改数字（占比会跟着失真）。
    # 所以这里动的是图型：柱状图能把负值如实画出来（向下伸）。
    effective_type = spec.type
    pie_negative: list[str] = []
    if spec.type == "pie" and series:
        pie_negative = [str(c) for c, v in zip(categories, series[0]["data"])
                        if isinstance(v, (int, float)) and v < 0]
        if pie_negative:
            effective_type = "bar"

    # 排序依据可能是一列**没被画出来**的指标。
    # 两种常见情形：
    #   1. 「按资产报酬率排序，但只画利润」—— 排序键不进 series
    #   2. 多指标拆成多张单指标图后，每张图的排序键仍是用户点名的那一列，
    #      而它在除第一张以外的图里都不属于 y
    # 不做这一步的话排序会**静默失效**：图照常出来，只是顺序不对，
    # 而「顺序不对」比「图没出来」难发现得多。
    sort_vals: list | None = None
    if spec.sort_by and spec.sort_by not in ("__x__", "") and spec.sort_by not in y_cols:
        num_names = {c.name for c in ds.columns if c.dtype == "number"}
        if spec.sort_by in num_names:
            sort_vals = []
            for cat in categories:
                vals_s = [r.get(spec.sort_by) for r in buckets[cat]
                          if isinstance(r.get(spec.sort_by), (int, float))]
                sort_vals.append(round(func(vals_s), 4) if vals_s else 0)

    # 排序
    if spec.sort_by:
        if spec.sort_by == "__x__":
            pairs = list(zip(categories, range(len(categories))))
            # 按横轴排时不能用纯字符串序：「1月 2月 … 10月」会被排成
            # 「10月 11月 12月 1月 2月」。有数字的走自然序，没有的才退回字符串序。
            if any(re.search(r"\d", str(c)) for c in categories):
                pairs.sort(key=lambda p: _natkey(p[0]),
                           reverse=(spec.sort_order == "desc"))
            else:
                pairs.sort(key=lambda p: str(p[0]),
                           reverse=(spec.sort_order == "desc"))
        else:
            if spec.sort_by in y_cols:
                si = y_cols.index(spec.sort_by)
                vals = series[si]["data"]
            elif sort_vals is not None:
                vals = sort_vals
            else:
                vals = [0] * len(categories)
            pairs = list(zip(categories, range(len(categories))))
            pairs.sort(key=lambda p: vals[p[1]], reverse=(spec.sort_order == "desc"))
        order = [p[1] for p in pairs]
        categories = [categories[i] for i in order]
        series = [{"name": s["name"], "data": [s["data"][i] for i in order]} for s in series]

    if spec.limit and spec.limit > 0:
        categories = categories[: spec.limit]
        series = [{"name": s["name"], "data": s["data"][: spec.limit]} for s in series]

    return {"categories": categories, "series": series, "unit": unit,
            "units": units,
            "skipped_empty_category": skipped_empty_cat, "x": x,
            "effective_type": effective_type, "pie_negative": pie_negative}


def _natkey(s: Any) -> list[tuple]:
    """自然排序键：把字符串切成「文字块 / 数字块」交替的序列，数字按数值比。

    「1月」 → [(1, 1, ''), (0, 0, '月')]
    「10月」→ [(1, 10, ''), (0, 0, '月')]
    这样 1月 会排在 10月 前面，而纯字符串序会反过来。
    「2025年3月」「Q1」「第3周」「2025-03」同理。
    """
    parts = re.split(r"(\d+)", str(s if s is not None else ""))
    return [(1, int(p), "") if p.isdigit() else (0, 0, p) for p in parts]


def is_flag_column(vals: list[float]) -> bool:
    """是不是「是/否」标记列（值只有 0/1 或 1/2 这种少量整数档位）。

    985、211、双一流 这类列求和得到「合计 2785」完全是废话，
    真正该看的是「有多少个 1」。
    """
    if not vals or len(vals) < 4:
        return False
    uniq = set(vals)
    return len(uniq) <= 3 and all(float(v).is_integer() and 0 <= v <= 2
                                  for v in uniq)


def compute_metrics(ds: Dataset, cols: list[str],
                    exclude_summary: bool = True) -> list[dict]:
    """指标卡数据。

    两种列不能当成普通数值去求和：
    - 百分比列（毛利率、复购率）→ 看均值
    - 0/1 标记列（985、211）→ 看「1」的个数
    """
    rows = working_rows(ds, exclude_summary)
    units = {c.name: c.unit for c in ds.columns}
    cards = []
    for col in cols:
        vals = [r.get(col) for r in rows if isinstance(r.get(col), (int, float))]
        if not vals:
            continue
        labels = [str(r.get(ds.columns[0].name, "")) for r in rows
                  if isinstance(r.get(col), (int, float))]
        mx, mn = max(vals), min(vals)
        total = sum(vals)
        mean = total / len(vals)
        is_pct = units.get(col) == "%"
        is_flag = is_flag_column(vals)

        if is_flag:
            ones = sum(1 for v in vals if v == 1)
            value = f"{ones:,}"
            unit_text = f"个 · 占 {ones / len(vals) * 100:.1f}%"
            detail = f"共 {len(vals)} 条记录，其中标记为 1 的有 {ones} 条"
            card = {"title": col, "value": value, "unit": unit_text,
                    "detail": detail,
                    "_raw": {"sum": total, "mean": mean, "max": mx, "min": mn,
                             "percent": False, "flag": True, "count": ones,
                             "n": len(vals)}}
            cards.append(card)
            continue

        if is_pct:
            value, unit_text = _fmt_pct(mean), "均值"
            detail = (f"最高 {_fmt_pct(mx)}（{labels[vals.index(mx)]}） · "
                      f"最低 {_fmt_pct(mn)}（{labels[vals.index(mn)]}） · "
                      f"共 {len(vals)} 个取值")
        else:
            value, unit_text = _fmt(total), "合计"
            detail = (f"均值 {_fmt(mean)} · "
                      f"最大 {_fmt(mx)}（{labels[vals.index(mx)]}） · "
                      f"最小 {_fmt(mn)}（{labels[vals.index(mn)]}）")
        card = {
            "title": col,
            "value": value,
            "unit": unit_text,
            "detail": detail,
            "_raw": {"sum": total, "mean": mean, "max": mx, "min": mn,
                     "percent": is_pct},
        }
        cards.append(card)
    return cards


def _fmt_pct(n: float) -> str:
    return f"{n * 100:.1f}%"


def mom_change(ds: Dataset, col: str, exclude_summary: bool = True
               ) -> tuple[float, float, float | None] | None:
    """末值、首值、环比变化率。用于结论文案。"""
    rows = working_rows(ds, exclude_summary)
    vals = [r.get(col) for r in rows if isinstance(r.get(col), (int, float))]
    if len(vals) < 2:
        return None
    first, last = vals[0], vals[-1]
    if first == 0:
        return last, first, None
    return last, first, (last - first) / abs(first)


def template_summary(ds: Dataset, chart_specs: list[ChartSpec],
                     metrics: list[dict]) -> str:
    """无大模型时的降级文案：全部由数字拼装，绝不臆测。"""
    # 参与计算的行数按实际口径算，别让用户拿行数对不上账
    used_rows = (working_rows(ds)
                 if any(getattr(s, "exclude_summary", True) for s in chart_specs)
                 or not chart_specs else ds.rows)
    lines = [f"数据共 {len(used_rows)} 行（总计 {len(ds.rows)} 行），来源于「{ds.name}」。"]
    seen: set[str] = set()
    pct_cols = {c["title"] for c in metrics if c.get("_raw", {}).get("percent")}
    excl = bool(chart_specs) and all(getattr(s, "exclude_summary", True)
                                     for s in chart_specs)

    # 分类列为空被剔除的行、图型被自动改掉，先收集提示文案（真正 emit 要等 add 定义后）
    _pre_notes: list[str] = []
    for spec in chart_specs:
        agg = aggregate(ds, spec)
        n = agg.get("skipped_empty_category", 0)
        if n:
            _pre_notes.append(
                f"有 {n} 行因「{agg.get('x', spec.x)}」为空未计入图表（分类列缺失无法归类）。"
            )
        neg = agg.get("pie_negative") or []
        if neg:
            s0 = (agg.get("series") or [{}])[0]
            yname = s0.get("name") or (spec.y[0] if spec.y else "该指标")
            show = "、".join(neg[:3]) + ("…" if len(neg) > 3 else "")
            _pre_notes.append(
                f"「{yname}」按「{agg.get('x', spec.x)}」分组时有 {len(neg)} 个分类为负值"
                f"（{show}）。饼图只能表达「部分占整体」，负值没有对应的扇形，"
                f"硬画出来的占比是错的，所以已自动改用柱状图 —— 负值在柱状图上往下伸，"
                f"数据一个不丢。想要饼图就得先把负值那些行筛掉或换成看占比的指标。"
            )

    def add(line: str) -> None:
        """同样的结论不重复写第二遍——多张图配得雷同时很容易出现。"""
        if line and line not in seen:
            seen.add(line)
            lines.append(line)

    for note in _pre_notes:
        add(note)

    for card in metrics:
        col = card["title"]
        raw = card.get("_raw", {})
        if raw.get("flag"):
            seg = (f"{col}共 {raw.get('count', 0):,} 条标记为是"
                   f"（占 {raw.get('count', 0) / max(raw.get('n', 1), 1) * 100:.1f}%）")
            add(seg + "。")
            continue
        mom = mom_change(ds, col, excl)
        if raw.get("percent"):
            seg = f"{col}均值 {card['value']}"
        else:
            seg = f"{col}合计 {card['value']}，均值 {_fmt(raw.get('mean', 0))}"
        if mom:
            last, first, rate = mom
            if rate is not None:
                arrow = "增长" if rate >= 0 else "下降"
                seg += f"，首末相比{arrow} {abs(rate) * 100:.1f}%"
        add(seg + "。")

    for spec in chart_specs:
        agg = aggregate(ds, spec)
        if agg.get("empty") or not agg["categories"]:
            continue
        s = agg["series"][0]
        pairs = list(zip(agg["categories"], s["data"]))
        top = max(pairs, key=lambda p: p[1])
        bottom = min(pairs, key=lambda p: p[1])
        pct = all(y in pct_cols for y in spec.y) if spec.y else False
        total = sum(v for _, v in pairs) or 1
        share = ""
        if not pct:
            share = f"，占 {top[1] / total * 100:.1f}%"
        add(
            f"按「{spec.x}」看{s['name']}：{top[0]}最高（{_fmt(top[1])}{share}），"
            f"{bottom[0]}最低（{_fmt(bottom[1])}）。"
        )
    return "".join(lines)
