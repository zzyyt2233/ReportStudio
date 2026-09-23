"""带修饰词的数字（约1.2万 类）被整列误判文本的回归测试。

修复前：整列都是「约1.2万」「近3亿」这种值时，infer_dtype 因非纯数字占比高把整列判成 text，
出不了图且无提示。修复后：detect 这类带修饰词的数字列并显式告警（不擅自硬凑数字）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.parsers.tabular import _looks_recoverable_numeric, build_dataset


def _grid(header, rows):
    return [header] + rows


def test_helper_detects_qualifier():
    assert _looks_recoverable_numeric("约1.2万")
    assert _looks_recoverable_numeric("近3亿")
    assert _looks_recoverable_numeric("超5000")
    assert _looks_recoverable_numeric("大约 200")
    # 纯数字 / 带单位但无修饰词 不算（这些 _is_number 本就能识别）
    assert not _looks_recoverable_numeric("1.2万")
    assert not _looks_recoverable_numeric("1200")
    # 缺失占位符不算
    assert not _looks_recoverable_numeric("N/A")
    assert not _looks_recoverable_numeric(None)
    print("✓ _looks_recoverable_numeric 识别正确")


def test_column_of_recoverable_numbers_warns():
    # 整列都是「约X万」→ 应被识别为 text 但给出提示
    rows = [["约1.2万"], ["近3亿"], ["超5000"], ["约800"], ["大约 200"]]
    ds = build_dataset(_grid(["金额"], rows), name="t", source_type="csv")
    col = ds.columns[0]
    assert col.dtype == "text", f"整列约X万应判文本（不过度猜测），实际 {col.dtype}"
    assert any("约1.2万" in w and "带修饰词" in w for w in ds.warnings), ds.warnings
    print(f"✓ 整列带修饰词数字：dtype=text 且已告警：{ds.warnings[-1]}")


def test_normal_text_column_no_false_warn():
    # 普通文本列（地名）不应误报
    rows = [["北京"], ["上海"], ["广州"], ["深圳"], ["杭州"]]
    ds = build_dataset(_grid(["城市"], rows), name="t", source_type="csv")
    assert not any("带修饰词" in w for w in ds.warnings), ds.warnings
    print("✓ 普通文本列不误报")


def test_clean_number_column_no_warn():
    # 干净数字列（个别约X万 混入）应判 number，且不触发「带修饰词」整列告警
    rows = [["100"], ["200"], ["约1.2万"], ["300"], ["400"]]
    ds = build_dataset(_grid(["金额"], rows), name="t", source_type="csv")
    col = ds.columns[0]
    assert col.dtype == "number", f"多数干净数字应判 number，实际 {col.dtype}"
    # 这里不应有「带修饰词」整列告警（那是给整列都是修饰词用的）
    assert not any("带修饰词" in w for w in ds.warnings), ds.warnings
    print("✓ 干净数字列（个别约X万）判 number，无整列告警")


if __name__ == "__main__":
    test_helper_detects_qualifier()
    test_column_of_recoverable_numbers_warns()
    test_normal_text_column_no_false_warn()
    test_clean_number_column_no_warn()
    print("\n带修饰词数字回归测试全部通过 ✅")
