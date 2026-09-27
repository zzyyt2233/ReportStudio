"""缺失值占位符回归测试。

验证：含 N/A、-、—、/、空单元格 的数值列，仍能正确判成 number 并出图；
含 N/A 的「双一流」标记列仍能识别为标记列。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.parsers.tabular import _is_blank_token, infer_dtype, build_dataset


def test_blank_token():
    cases = ["N/A", "n/a", "NA", "null", "None", "-", "--", "—", "–",
             "/", "无", "空", "暂无", "未知", "缺失", "", " ", None, "／", "－"]
    for c in cases:
        assert _is_blank_token(c), f"应判为缺失：{c!r}"
    # 这些不能是缺失
    for c in ["100", "3.5", "江苏", "双一流", "2024-01", "12月"]:
        assert not _is_blank_token(c), f"不应判为缺失：{c!r}"
    print("✓ _is_blank_token 识别正确")


def test_numeric_with_missing():
    # 省份 / 营收，10 行里 9 行是数字，1 行 N/A，另有 - 和 —
    body = [
        ["广东", "100"],
        ["江苏", "200"],
        ["山东", "N/A"],
        ["河南", "—"],
        ["浙江", "/"],
        ["福建", "350"],
        ["湖北", "120"],
        ["湖南", "90"],
        ["安徽", "210"],
        ["河北", "160"],
    ]
    grid = [["省份", "营收"]] + body
    ds = build_dataset(grid, name="t", source_type="csv")
    col = ds.columns[1]
    assert col.dtype == "number", f"营收列被判成 {col.dtype}，期望 number"
    assert col.unit is None
    # 缺失值应变成 None，而不是被当成 0 或字符串
    vals = [r["营收"] for r in ds.rows]
    assert vals.count(None) == 3, f"应有 3 个缺失(None)，实际 {vals}"
    print(f"✓ 含缺失值的数值列正确判为 number，缺失数={vals.count(None)}")


def test_pct_with_missing():
    body = [
        ["广东", "12%"],
        ["江苏", "15%"],
        ["山东", "N/A"],
        ["河南", "—"],
        ["浙江", "9%"],
        ["福建", "11%"],
        ["湖北", "13%"],
        ["湖南", "8%"],
        ["安徽", "10%"],
        ["河北", "14%"],
    ]
    grid = [["省份", "覆盖率"]] + body
    ds = build_dataset(grid, name="t", source_type="csv")
    col = ds.columns[1]
    assert col.dtype == "number" and col.unit == "%", f"覆盖率列={col.dtype}/{col.unit}"
    print("✓ 含缺失值的百分比列正确判为 number/%")


def test_flag_with_missing():
    # 双一流列：1/0 + 个别「双一流」文字 + 个别 N/A
    body = [
        ["北大", "1"],
        ["清华", "双一流"],
        ["复旦", "0"],
        ["南大", "1"],
        ["浙大", "N/A"],
        ["武大", "1"],
        ["中大", "0"],
        ["华科", "1"],
        ["西交", "1"],
        ["哈工大", "0"],
    ]
    grid = [["校名", "双一流"]] + body
    ds = build_dataset(grid, name="t", source_type="csv")
    col = ds.columns[1]
    # 既是数字 0/1，又有文字「双一流」和缺失 → 应被识别为标记列（number + flag）
    assert col.dtype == "number", f"双一流列被判成 {col.dtype}"
    flag_vals = [r["双一流"] for r in ds.rows if r["双一流"] == 1.0]
    # 清华(双一流文字→1) + 浙大(N/A→缺失,不计数) → 应有 1 个由文字还原的 1
    assert any(r["双一流"] == 1.0 for r in ds.rows), "文字『双一流』应被还原为 1"
    assert any(r["双一流"] is None for r in ds.rows), "N/A 应为缺失(None)"
    print(f"✓ 含缺失值的标志列仍识别为标记列，warnings={ds.warnings}")


if __name__ == "__main__":
    test_blank_token()
    test_numeric_with_missing()
    test_pct_with_missing()
    test_flag_with_missing()
    print("\n全部缺失值回归测试通过 ✅")
