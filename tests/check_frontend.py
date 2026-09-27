"""前端静态自检：把 app.js 里引用的 DOM 选择器和 index.html 里的真实元素对一遍。

node --check 只能验语法，抓不到「引用了不存在的元素」这类错误 —— 而这正是
纯手写 DOM 代码最容易翻车、且一翻车整个界面就瘫痪的地方。
"""

from __future__ import annotations

import os
import re
import sys

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


def main() -> int:
    html = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
    js = open(os.path.join(WEB, "app.js"), encoding="utf-8").read()

    html_ids = set(re.findall(r'id="([^"]+)"', html))
    js_ids = set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", js))
    js_ids |= set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", js))
    # JS 模板字符串里动态生成的 id 不算引用
    dyn_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', js))

    print(f"HTML 元素 id: {len(html_ids)} 个")
    print(f"JS 引用 id:   {len(js_ids)} 个")

    missing = sorted(js_ids - html_ids - dyn_ids)
    if missing:
        print("\n[x] JS 引用了但 HTML 里不存在的 id（会导致运行时报错）:")
        for m in missing:
            print("    -", m)
    else:
        print("\n[v] 所有引用的 id 都存在于 HTML")

    # CSS 类名：JS 里查的 class 至少得在 HTML 或 JS 模板里出现过
    js_classes = set(re.findall(r"\$\$?\('\.([A-Za-z0-9_-]+)", js))
    html_class_set = {c for group in re.findall(r'class="([^"]+)"', html)
                      for c in group.split()}
    # JS 里三种产生 class 的写法都要算：class="..."、className='...'、classList.add('...')
    html_class_set |= {c for group in re.findall(r'class="([^"]+)"', js)
                       for c in group.split()}
    html_class_set |= {c for g in re.findall(r"className\s*=\s*'([^']+)'", js)
                       for c in g.split()}
    html_class_set |= set(re.findall(r"classList\.add\('([^']+)'\)", js))
    unknown = sorted(c for c in js_classes if c not in html_class_set)
    print("\n[v] JS 用到的 class 都有出处" if not unknown
          else f"\n[!] 以下 class 在 HTML 与 JS 模板里都没出现: {unknown}")

    # hidden 属性：浏览器的 [hidden]{display:none} 属于 UA 样式表，
    # 作者样式里任何一条 display 都能把它压掉（与优先级无关）。本文件里
    # .row / .vacts / .c-ytypes 都写了 display，于是「设了 hidden 却照常显示」：
    # 报告页签下冒出看板按钮、只剩一个指标时「多指标」那行还挂着。
    # 必须有一条全局 !important 兜底，这里把它钉住，免得以后又被顺手删掉。
    css = open(os.path.join(WEB, "style.css"), encoding="utf-8").read()
    has_guard = bool(re.search(
        r"\[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important", css))
    print("\n[v] hidden 属性有全局兜底（不会被 class 上的 display 盖掉）" if has_guard
          else "\n[x] style.css 缺少 [hidden]{display:none !important} —— "
               "带 display 的 class 会让 hidden 失效，该藏的控件藏不住")

    # 看板 HTML 的样式是**另一份**（core/render/dashboard.py 里的 CSS 常量），
    # 上面这条管不到它。放大层的 .zb 写了 display:flex 又带 hidden 属性，
    # 少了兜底的话页面一打开就是一层灰遮罩盖住整个看板。
    root = os.path.dirname(WEB)
    sys.path.insert(0, root)
    from core.render import dashboard as _dash  # noqa: E402
    dash_guard = bool(re.search(
        r"\[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important", _dash.CSS))
    print("[v] 看板 CSS 同样有 hidden 兜底" if dash_guard
          else "[x] 看板 CSS（core/render/dashboard.py）缺少 "
               "[hidden]{display:none !important} —— 放大层的遮罩会一直显示")
    has_guard = has_guard and dash_guard

    # 后端返回字段：JS 读的字段应在后端 to_dict / 接口里有
    print("\n---- 后端字段抽查 ----")
    fields = ["total_rows", "source_type", "warnings", "columns", "rows",
              "suggestion", "group_names", "report_url", "md_url", "dir",
              "charts", "metrics", "summary", "dataset", "results", "ok",
              "name", "id", "url", "path", "items", "files", "y_types"]
    backend = ""
    root = os.path.dirname(WEB)
    for f in ("app.py", os.path.join("core", "model.py"),
              os.path.join("core", "merge.py")):
        p = os.path.join(root, f)
        if os.path.exists(p):
            backend += open(p, encoding="utf-8").read()
    miss_field = [x for x in fields if x not in backend]
    print("[v] JS 读的字段后端都有" if not miss_field
          else f"[!] 后端找不到这些字段: {miss_field}")

    return 1 if (missing or miss_field or not has_guard) else 0


if __name__ == "__main__":
    sys.exit(main())
