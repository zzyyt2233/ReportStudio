# -*- coding: utf-8 -*-
"""图表图片识别升级回归：vision 返回结构化结果后，parse_image 的产出必须
「横轴是维度、系列是度量」，前端才能直接做 X/Y 切换。

不真发网络请求：monkeypatch core.llm.vision，喂两类返回值——
1. 图表图：x_axis + 多系列，验证第一列换成真实轴名、系列列判成数值型、
   roles 里横轴进维度、系列进度量。
2. 表格图：无 x_axis，行为与升级前一致（表头原样保留）。
3. 模型说读不出（空表 + note）：落到文字数据集，不崩。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import llm  # noqa: E402
from core.model import classify_columns  # noqa: E402
from core.parsers import image as img_parser  # noqa: E402
from tests._helpers import ok  # noqa: E402

PASS = FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if ok(cond, msg):
        PASS += 1
    else:
        FAIL += 1


def _fake_image() -> str:
    """vision 只负责发请求，图片内容不参与断言，给个存在的文件即可。"""
    fd, p = tempfile.mkstemp(suffix=".png")
    os.write(fd, b"\x89PNG\r\n\x1a\n")
    os.close(fd)
    return p


def main():
    path = _fake_image()
    orig = llm.vision
    try:
        # ---- 1. 图表图：双系列柱状图 ----
        def fake_vision_chart(p, prompt, want_json=True):
            return {"title": "月度销售对比", "note": "单位：万元，数值为图上估读",
                    "x_axis": "月份", "series": ["销售额", "利润"],
                    "header": ["类目", "销售额", "利润"],
                    "rows": [["1月", "120", "12"], ["2月", "150", "15"],
                             ["3月", "98", "9"]]}
        llm.vision = fake_vision_chart
        ds = img_parser.parse_image(path)
        cols = [c.name for c in ds.columns]
        check(cols == ["月份", "销售额", "利润"],
              f"第一列表头换成真实横轴名（实际 {cols}）")
        dtypes = {c.name: c.dtype for c in ds.columns}
        check(dtypes["销售额"] == "number" and dtypes["利润"] == "number",
              f"系列列判成数值型（实际 {dtypes}）")
        roles = classify_columns(ds)
        check("月份" in roles["dimensions"], f"横轴进维度（{roles['dimensions']}）")
        check("销售额" in roles["measures"] and "利润" in roles["measures"],
              f"两个系列都进度量（{roles['measures']}）")
        check(ds.source_type == "image", "source_type 是 image（前端据此默认多选系列）")
        check(ds.name == "月度销售对比", f"用图的标题做数据表名（{ds.name}）")
        check(any("估读" in w for w in ds.warnings), "估读说明进了 warnings")

        # ---- 2. 表格图：无 x_axis，表头原样 ----
        def fake_vision_table(p, prompt, want_json=True):
            return {"title": "", "note": "", "x_axis": "", "series": [],
                    "header": ["地区", "金额"],
                    "rows": [["华东", "1,200"], ["华北", "980"]]}
        llm.vision = fake_vision_table
        ds2 = img_parser.parse_image(path)
        check([c.name for c in ds2.columns] == ["地区", "金额"],
              "表格图表头原样保留")
        check({c.name: c.dtype for c in ds2.columns}["金额"] == "number",
              "千分位数字判成数值型")

        # ---- 3. 读不出：空表 + note → 文字数据集 ----
        def fake_vision_empty(p, prompt, want_json=True):
            return {"title": "", "note": "图片过于模糊", "x_axis": "",
                    "series": [], "header": [], "rows": []}
        llm.vision = fake_vision_empty
        ds3 = img_parser.parse_image(path)
        check(ds3.total_rows >= 0 and ds3.columns is not None,
              "读不出时不崩，返回合法 Dataset")
    finally:
        llm.vision = orig
        os.unlink(path)

    print(f"\n{'=' * 40}\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
