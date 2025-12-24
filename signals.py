# signals.py
import re
from urllib.parse import urlparse

STOPWORDS = set([
    "the","a","an","and","or","to","of","in","on","for","with",
    "this","that","is","are","was","were","game","games","play"
])

def normalize_keyword(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[\[\]\(\)\{\}<>\"'“”‘’]", " ", s)
    s = re.sub(r"[^a-z0-9\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def extract_candidates_from_title(title: str):
    """
    极简提词：
    - 从标题里找可能的“游戏名片段”
    - 先用大写开头词/连续词做候选（对英文更友好）
    - 也保留整句的清洗版本作为兜底
    """
    if not title:
        return []

    # 兜底：整句（清洗后）
    base = normalize_keyword(title)
    candidates = [base] if base else []

    # 尝试：从原句抓 “连续大写开头词” （比如 "Crazy Cattle 3D"）
    caps = re.findall(r"\b([A-Z][a-z0-9]+(?:\s+[A-Z0-9][a-z0-9]+){0,3})\b", title)
    for c in caps:
        cc = normalize_keyword(c)
        if cc and len(cc) >= 3:
            candidates.append(cc)

    # 去重
    seen = set()
    out = []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out

def score_item(text: str, rules: dict) -> int:
    t = (text or "").lower()
    score = 0

    deny = [x.lower() for x in rules.get("deny_keywords", [])]
    boost = [x.lower() for x in rules.get("boost_keywords", [])]

    if any(k in t for k in deny):
        return -999  # 直接淘汰

    for k in boost:
        if k in t:
            score += 3

    # 标题里含 “play online / browser” 这类词加分
    if "play online" in t or "browser" in t:
        score += 2

    # “what game is this” 类强意图加分
    if "what game is this" in t or "name of the game" in t:
        score += 4

    return score

def build_signal_candidates(items, rules):
    """
    输出候选结构：
    {
      keyword, score, source, evidence_title, evidence_link
    }
    """
    threshold = rules.get("score_threshold", 6)
    out = []

    for it in items:
        title = it.get("title", "")
        link = it.get("link", "")
        s = score_item(title, rules)
        if s < 0:
            continue

        for kw in extract_candidates_from_title(title):
            # 很短/停用词过滤
            if len(kw) < 6:
                continue
            tokens = [x for x in kw.split() if x not in STOPWORDS]
            if len(tokens) < 1:
                continue

            out.append({
                "keyword": kw,
                "score": s,
                "source": it.get("source"),
                "evidence_title": title[:160],
                "evidence_link": link
            })

    # 合并同词：取最高分 + 合并来源
    merged = {}
    for c in out:
        k = c["keyword"]
        if k not in merged:
            merged[k] = {**c, "sources": {c["source"]}}
        else:
            merged[k]["sources"].add(c["source"])
            if c["score"] > merged[k]["score"]:
                merged[k].update({**c})
    final = []
    for k, v in merged.items():
        v["sources"] = sorted(list(v["sources"]))
        # 多来源命中加权（更像趋势）
        if len(v["sources"]) >= 2:
            v["score"] += 2
        if v["score"] >= threshold:
            final.append(v)

    # 分数高的先推
    final.sort(key=lambda x: x["score"], reverse=True)
    return final
