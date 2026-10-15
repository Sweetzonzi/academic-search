"""M1.5 spike / step 18: how much does adding author+year buy over a bare title key?

Part B showed 12% of Crossref hits collapse onto a shared normalised title and
that 'robust model predictive control' was shared by 7 distinct DOIs.  That makes
a bare title hash unusable as a decision.  This measures the fix: strengthen the
key with year and first-author surname and see the collision rate fall.

Also checks the markup trap: Crossref titles can carry embedded MathML/JATS XML
rather than plain text (observed on 1801.00416), which silently breaks any
string comparison that only knows about LaTeX.
"""
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "academic-search-spike/0.1 (title-identity test)"}
DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
QUERIES = ["Model predictive control", "Robust model predictive control",
           "Reinforcement learning for control", "Adaptive control of nonlinear systems",
           "Fault diagnosis and fault-tolerant control",
           "Trajectory tracking control of mobile robots",
           "Optimal control", "Sliding mode control", "State estimation",
           "Consensus of multi-agent systems", "Path planning", "Visual servoing"]


def strip_markup(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t)                 # MathML / JATS in Crossref titles
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"\$[^$]*\$", " ", t)
    t = re.sub(r"\\[A-Za-z]+\s*", " ", t)
    t = "".join("-" if c in DASHES else c for c in t).replace("&", " and ")
    return re.sub(r"\s+", " ", t).strip()


def key(title: str, agg=True) -> str:
    t = strip_markup(title).lower()
    if agg:
        t = re.split(r"\s*[:\u2013\u2014-]\s+", t, maxsplit=1)[0]
        t = re.sub(r"^(?:a|an|the)\s+", "", t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


def fetch(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                        timeout=60) as r:
                return r.read()
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def norm_doi(d):
    return (d or "").lower()


def main() -> None:
    rows = []
    for q in QUERIES:
        raw = fetch("https://api.crossref.org/works?query.bibliographic="
                    + urllib.parse.quote(q) + "&rows=60"
                    "&select=DOI,title,issued,author,type")
        if not raw:
            print(f"  {q!r}: 请求失败")
            continue
        for it in json.loads(raw)["message"]["items"]:
            t = (it.get("title") or [""])[0]
            if not t:
                continue
            try:
                yr = it["issued"]["date-parts"][0][0]
            except Exception:
                yr = None
            au = (it.get("author") or [{}])[0]
            surname = (au.get("family") or au.get("name") or "").strip().lower()
            rows.append({"doi": norm_doi(it.get("DOI")), "title": t, "year": yr,
                         "surname": surname, "type": it.get("type")})
        time.sleep(1.0)

    print(f"样本：{len(rows)} 条 Crossref 记录（{len(QUERIES)} 个泛化查询）\n")

    def collisions(keyfn, label):
        g = {}
        for r in rows:
            k = keyfn(r)
            if k:
                g.setdefault(k, set()).add(r["doi"])
        collided = sum(len(v) for v in g.values() if len(v) > 1)
        worst = max((len(v) for v in g.values()), default=0)
        groups = sum(1 for v in g.values() if len(v) > 1)
        print(f"  {label:42s} 撞车记录 {collided:3d}/{len(rows)} "
              f"({100*collided/len(rows):4.0f}%)   撞车组 {groups:3d}   "
              f"最大一组 {worst} 个不同 DOI")
        return collided

    print("### 候选判据的假阳性（同一 key 下出现多个不同 DOI）")
    collisions(lambda r: key(r["title"]), "① title_key（宽松归一化）")
    collisions(lambda r: key(r["title"], False), "①' title_key（严格归一化，不砍副标题）")
    collisions(lambda r: f"{key(r['title'])}|{r['year']}", "② title_key + 年份")
    collisions(lambda r: f"{key(r['title'])}|{r['surname']}", "③ title_key + 一作姓氏")
    collisions(lambda r: f"{key(r['title'])}|{r['surname']}|{r['year']}",
               "④ title_key + 一作姓氏 + 年份  ← 建议判据")
    # same paper, different records: online-first vs issue year differ by a year
    collisions(lambda r: f"{key(r['title'])}|{r['surname']}|"
                         f"{(r['year'] or 0)//2}", "⑤ ④ 但年份放宽到两年一组")

    print("\n### ④ 仍撞车的组（已按 DOI 去重）")
    g = {}
    for r in rows:
        g.setdefault(f"{key(r['title'])}|{r['surname']}|{r['year']}", {})[r["doi"]] = r
    shown = 0
    for k, v in g.items():
        if len(v) > 1 and shown < 4:
            shown += 1
            print(f"  key = {k}")
            for r in v.values():
                print(f"      {r['doi']:34s} {r['year']}  {r['title'][:70]}")
    if shown == 0:
        print("  （无）")

    print("\n### 标题里的标记污染：真实例子")
    for r in rows:
        if "<" in r["title"] and ">" in r["title"]:
            print(f"  {r['doi']}")
            print(f"    原始: {r['title'][:110]}")
            print(f"    剥标记后: {strip_markup(r['title'])[:110]}")
            break
    else:
        print("  本次样本未复现（先前 1801.00416 的正式版标题带 <mml:math>）")


if __name__ == "__main__":
    main()
