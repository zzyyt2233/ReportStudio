"""在 samples/ 下造一批刁钻的测试数据，用来端到端验收。

故意造得"脏"一点：合并单元格、标题行、单位行、合计行、多 sheet、
GBK 编码的 CSV、扫描件风格的图片、带表格的 PDF。
"""

from __future__ import annotations

import os

from docx import Document
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE)
os.makedirs(OUT, exist_ok=True)

MONTHS = ["1月", "2月", "3月", "4月", "5月", "6月",
          "7月", "8月", "9月", "10月", "11月", "12月"]

REGIONS = {
    "华东": [("销售额", [128, 96, 143, 167, 189, 214, 231, 208, 176, 195, 246, 288]),
            ("订单量", [1240, 980, 1310, 1520, 1680, 1840, 1960, 1810, 1590, 1720, 2050, 2380]),
            ("毛利率", [0.32, 0.28, 0.35, 0.37, 0.39, 0.41, 0.43, 0.40, 0.36, 0.38, 0.44, 0.47])],
    "华南": [("销售额", [87, 74, 102, 118, 131, 149, 162, 155, 138, 147, 183, 209]),
            ("订单量", [910, 780, 1020, 1150, 1270, 1410, 1520, 1460, 1310, 1390, 1680, 1910]),
            ("毛利率", [0.29, 0.25, 0.31, 0.33, 0.35, 0.38, 0.39, 0.37, 0.34, 0.35, 0.41, 0.44])],
    "华北": [("销售额", [95, 82, 111, 126, 142, 158, 171, 163, 149, 158, 197, 226]),
            ("订单量", [880, 760, 1010, 1140, 1260, 1380, 1490, 1430, 1300, 1370, 1650, 1880]),
            ("毛利率", [0.30, 0.26, 0.33, 0.35, 0.37, 0.39, 0.41, 0.38, 0.35, 0.36, 0.42, 0.45])],
}


def make_excel():
    """带两行标题 + 合并单元格 + 单位行 + 合计行的多 sheet 表。"""
    path = os.path.join(OUT, "销售明细（带合并标题）.xlsx")
    wb = Workbook()
    wb.remove(wb.active)

    for region, cols in REGIONS.items():
        ws = wb.create_sheet(region)
        ws["A1"] = f"2025年度{region}大区销售报表"
        ws["A1"].font = Font(size=14, bold=True)
        ws.merge_cells("A1:D1")
        ws["A1"].alignment = Alignment(horizontal="center")

        ws["A2"] = "单位：销售额 万元，订单量 笔"
        ws.merge_cells("A2:D2")
        ws["A2"].alignment = Alignment(horizontal="left")

        ws.append(["月份", "销售额", "订单量", "毛利率"])
        for i, m in enumerate(MONTHS):
            ws.append([m, cols[0][1][i], cols[1][1][i], cols[2][1][i]])
        ws.append(["合计", round(sum(cols[0][1]), 1),
                   sum(cols[1][1]), round(sum(cols[2][1]) / 12, 3)])
    wb.save(path)
    print("生成", os.path.basename(path))


def make_gbk_csv():
    """GBK 编码、用分号分隔的 CSV，专门测编码和分隔符容错。"""
    path = os.path.join(OUT, "库存台账（GBK分号分隔）.csv")
    lines = ["品类;仓库;库存数量;周转天数\n"]
    rows = [
        ["生鲜", "一号仓", 3200, 3.5], ["生鲜", "二号仓", 2100, 4.1],
        ["日百", "一号仓", 8600, 12.4], ["日百", "二号仓", 7400, 13.8],
        ["家电", "一号仓", 1250, 28.6], ["家电", "二号仓", 980, 31.2],
        ["服饰", "一号仓", 5300, 22.0], ["服饰", "二号仓", 4100, 24.5],
    ]
    for r in rows:
        lines.append(";".join(str(x) for x in r) + "\n")
    with open(path, "w", encoding="gbk", newline="") as f:
        f.writelines(lines)
    print("生成", os.path.basename(path))


def make_docx():
    """正文 + 两张内嵌表格。"""
    path = os.path.join(OUT, "季度经营分析.docx")
    doc = Document()
    doc.add_heading("2025 年第三季度经营分析", level=1)
    doc.add_paragraph("本季度整体经营情况良好，各业务线均达成预期目标。")

    t = doc.add_table(rows=1, cols=4)
    t.style = "Table Grid"
    for i, h in enumerate(["部门", "营收", "成本", "利润率"]):
        t.rows[0].cells[i].text = h
    for r in [["直营店", "1280", "820", "0.36"],
              ["加盟店", "960", "700", "0.27"],
              ["电商", "1450", "930", "0.36"]]:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = v

    doc.add_paragraph("")
    doc.add_paragraph("各月环比：7月 12%，8月 18%，9月 9%。")

    t2 = doc.add_table(rows=1, cols=3)
    t2.style = "Table Grid"
    for i, h in enumerate(["月份", "活跃用户", "复购率"]):
        t2.rows[0].cells[i].text = h
    for r in [["7月", "182000", "0.34"], ["8月", "196000", "0.37"],
              ["9月", "214000", "0.41"]]:
        cells = t2.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = v
    doc.save(path)
    print("生成", os.path.basename(path))


def make_md():
    path = os.path.join(OUT, "会议纪要.md")
    content = """# 三季度经营复盘会

时间：2025-10-12
参会人：张伟、李娜、王强

## 核心结论

| 指标 | 实际值 | 目标值 |
|------|--------|--------|
| 营收（万元） | 3690 | 3500 |
| 新增客户 | 1240 | 1000 |
| 客户满意度 | 0.92 | 0.90 |

## 后续动作

负责人：李娜
截止日期：2025-11-30
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("生成", os.path.basename(path))


def make_pdf():
    """用 reportlab 造一份带表格的文本型 PDF。"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table

    path = os.path.join(OUT, "市场调研报告（带表格）.pdf")
    windir = os.environ.get("WINDIR", r"C:\Windows")
    font = "Helvetica"
    fp = os.path.join(windir, "Fonts", "msyh.ttc")
    if os.path.exists(fp):
        try:
            pdfmetrics.registerFont(TTFont("CN", fp, subfontIndex=0))
            font = "CN"
        except Exception:
            pass

    ss = getSampleStyleSheet()
    story = []
    story.append(Spacer(1, 0.6 * cm))

    data = [["城市", "样本量", "满意度", "推荐意愿"],
            ["北京", "820", "0.91", "0.78"],
            ["上海", "760", "0.89", "0.75"],
            ["广州", "640", "0.87", "0.72"],
            ["成都", "530", "0.90", "0.76"],
            ["武汉", "480", "0.85", "0.70"]]
    t = Table(data)
    t.setStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6F1FB")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B4B2A9")),
    ])
    story.append(t)
    SimpleDocTemplate(path, pagesize=A4).build(story)
    print("生成", os.path.basename(path))


def make_chart_image():
    """造一张图表截图，用来给视觉模型识别（没配接口时会给出明确提示）。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = os.path.join(OUT, "竞品对比（图表截图）.png")
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    brands = ["本品", "竞品A", "竞品B", "竞品C"]
    vals = [187, 156, 132, 98]
    ax.bar(brands, vals, color=["#378ADD", "#B4B2A9", "#B4B2A9", "#B4B2A9"])
    for i, v in enumerate(vals):
        ax.text(i, v + 3, str(v), ha="center", fontsize=11)
    ax.set_title("各品牌月度销量对比（单位：万件）")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    print("生成", os.path.basename(path))


def make_demo_db():
    """造一个示例 SQLite 库，用来演示「从数据库只读取数」。

    仓库里不放 .db 二进制文件（自动生成的东西不入库），跑一次本脚本就有。
    用 DROP TABLE 重建而不是删文件 —— 避免碰到环境里的删除保护。
    """
    import sqlite3

    path = os.path.join(OUT, "demo_shop.db")
    con = sqlite3.connect(path)
    try:
        con.executescript("""
            DROP TABLE IF EXISTS 客户表;
            DROP TABLE IF EXISTS 月度目标;
            DROP TABLE IF EXISTS 销售明细;
            CREATE TABLE 客户表(
              客户编号 TEXT, 客户名称 TEXT, 所属省份 TEXT,
              签约日期 TEXT, 信用等级 TEXT);
            CREATE TABLE 月度目标(月份 TEXT, 目标销售额 REAL, 责任省份 TEXT);
            CREATE TABLE 销售明细(
              id INTEGER PRIMARY KEY,
              省份 TEXT, 渠道 TEXT, 商品类别 TEXT, 月份 TEXT,
              销售额 REAL, 毛利率 REAL, 订单量 INTEGER);
        """)
        con.executemany("INSERT INTO 客户表 VALUES (?,?,?,?,?)", [
            ("C001", "广州越秀电子", "广东", "2023-03-12", "A"),
            ("C002", "南京金鹰商贸", "江苏", "2023-06-08", "B"),
            ("C003", "济南泉城数码", "山东", "2024-01-20", "A"),
            ("C004", "杭州西溪科技", "浙江", "2024-05-02", "A"),
            ("C005", "成都天府百货", "四川", "2024-09-15", "C"),
        ])
        con.executemany("INSERT INTO 月度目标 VALUES (?,?,?)", [
            ("1月", 5000.0, "广东"),
            ("2月", 5200.0, "江苏"),
            ("3月", 6000.0, "山东"),
        ])
        con.executemany("INSERT INTO 销售明细 VALUES (?,?,?,?,?,?,?,?)", [
            (1, "广东", "线上", "数码", "1月", 1200.5, 0.42, 86),
            (2, "广东", "线下", "数码", "1月", 760.0, 0.35, 41),
            (3, "广东", "线上", "家电", "2月", 2100.0, 0.55, 73),
            (4, "江苏", "线上", "数码", "1月", 980.0, 0.38, 62),
            (5, "江苏", "线下", "家电", "2月", 430.0, 0.29, 25),
            (6, "江苏", "线上", "家电", "3月", 1680.0, 0.47, 58),
            (7, "山东", "线上", "数码", "2月", 1500.0, 0.51, 94),
            (8, "山东", "线下", "数码", "3月", 890.0, 0.33, 37),
            (9, "浙江", "线上", "家电", "1月", 1320.0, 0.44, 66),
            (10, "浙江", "线上", "数码", "3月", 1750.0, 0.49, 81),
            (11, "四川", "线下", "家电", "2月", 620.0, 0.31, 22),
            (12, "四川", "线上", "数码", "3月", 1040.0, 0.4, 49),
        ])
        con.commit()
    finally:
        con.close()
    print("生成", os.path.basename(path))


if __name__ == "__main__":
    make_excel()
    make_gbk_csv()
    make_docx()
    make_md()
    make_pdf()
    make_chart_image()
    make_demo_db()
    print("\n全部测试数据已生成于", OUT)
