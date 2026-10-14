"""文本打标（内化自 data-analyzer/tagger：taxonomy 关键词 + TF-IDF/KMeans 聚类）。

为什么内化而不是 import 另一个项目的 venv：跨项目 import 既脆又难测；这里对齐接口复刻，
算法本身已在 data-analyzer 跑通。纯 sklearn + jieba（jieba 可选，缺失则退化为字符 n-gram）。

输入是一串文本片段（如会议纪要按段/行切出的句子），输出与输入等长的标签列表 + 方法说明。
"""

from __future__ import annotations

from collections import Counter

TAG_COL_CANDIDATES = ['标签', '分类', '类别', '类目', 'tag', 'category', 'type', '类型',
                     '部门', '项目', '产品线', '业务线']


def _seg_texts(texts):
    out = []
    for t in texts:
        if not isinstance(t, str):
            continue
        t = t.strip()
        if t:
            out.append(t)
    return out


def apply_taxonomy(texts: list[str], taxonomy: dict) -> list[str]:
    """按关键词命中打标，未命中归「其他」。taxonomy: {标签: [关键词...]}。"""
    tags: list[str] = []
    for t in texts:
        hit = '其他'
        for label, kws in taxonomy.items():
            if any(kw and kw in t for kw in kws):
                hit = label
                break
        tags.append(hit)
    return tags


def _tokenizer():
    """中文优先用 jieba 分词；不可用时退化为字符 n-gram（不依赖任何外部库）。"""
    try:
        import jieba
        return (lambda s: ' '.join(jieba.lcut(s))), True
    except Exception:
        return (lambda s: s), False


def auto_cluster(texts: list[str], max_k: int = 6):
    """无 taxonomy 时的兜底：TF-IDF + KMeans，轮廓系数选 k，用每簇 Top TF-IDF 词命名。"""
    from sklearn.cluster import KMeans
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics import silhouette_score

    tokenize, use_jieba = _tokenizer()
    corpus = [tokenize(t) for t in texts]
    if use_jieba:
        # jieba 已用空格分词，按词切即可（\w 在 Unicode 下含中文）
        vec = TfidfVectorizer(min_df=1, token_pattern=r"(?u)\b\w+\b")
    else:
        # 无分词：字符 1~2 gram，对中文短文本也能拉开区分度
        vec = TfidfVectorizer(analyzer='char_wb', ngram_range=(1, 2), min_df=1)
    X = vec.fit_transform(corpus)
    n = len(texts)
    if n < 4:
        return ['全部'] * n, {'method': '样本过少归并'}

    best_k, best_s = 2, -1
    for k in range(2, min(max_k, n) + 1):
        km = KMeans(n_clusters=k, n_init=4, random_state=42).fit(X)
        if len(set(km.labels_)) > 1:
            try:
                s = silhouette_score(X, km.labels_)
            except Exception:
                s = -1
            if s > best_s:
                best_s, best_k = s, k
    km = KMeans(n_clusters=best_k, n_init=10, random_state=42).fit(X)
    labels = km.labels_
    terms = vec.get_feature_names_out()
    naming: list[str] = []
    for c in range(best_k):
        idx = [i for i in range(n) if labels[i] == c]
        if not idx:
            naming.append(f'类别{c + 1}')
            continue
        scores = X[idx].toarray().mean(axis=0)
        top = [terms[i] for i in sorted(range(len(scores)), key=lambda i: -scores[i])[:3]
               if scores[i] > 0]
        naming.append(''.join(top) if top else f'类别{c + 1}')
    mapping = {c: (naming[c] or f'类别{c + 1}') for c in range(best_k)}
    return [mapping[l] for l in labels], {
        'method': 'TF-IDF+KMeans', 'k': best_k,
        'silhouette': round(float(best_s), 3), 'jieba': use_jieba,
    }


def tag_texts(texts, taxonomy: dict | None = None):
    """给文本片段打标签。返回 (tags, meta)。

    - 给定 taxonomy：按关键词匹配
    - 否则：TF-IDF + KMeans 自动聚类
    """
    texts = _seg_texts(texts)
    if not texts:
        return [], {'method': 'empty'}
    if taxonomy:
        return apply_taxonomy(texts, taxonomy), {'method': 'taxonomy'}
    return auto_cluster(texts)
