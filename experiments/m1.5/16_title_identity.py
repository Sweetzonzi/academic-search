"""M1.5 spike / step 17: is a title hash trustworthy as an identity?

Two measurements, both against free APIs (Crossref has no quota, arXiv 3s rule):

A. Cross-source title stability. For a set of arXiv papers, take the arXiv title
   and ask Crossref to find the published version by title. Report (a) whether a
   match is found at all, (b) exactly how the two title strings differ. This also
   doubles as a dry run of the dedup layer's "search by title" step.

B. False-positive rate. A title hash is only usable if different papers rarely
   collapse to the same normalised string. Measure that directly: for generic
   control/automation titles, pull Crossref results and count how many DISTINCT
   DOIs share one normalised title.

The normaliser is deliberately conservative and printed so the effect of each
rule is auditable.
"""
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "_cache")
UA = {"User-Agent": "academic-search-spike/0.1 (title-identity test)"}
ARXIV_NS = {"a": "http://www.w3.org/2005/Atom"}

# a mix: unpublished (2026-10-13), published-and-author-filled, published-but-not
SAMPLE = ["2610.18675", "2610.17929", "2610.18361", "2610.18758",
          "0901.0633", "1801.00516", "2001.00145", "2201.00137",
          "2401.00682", "1801.00416"]

DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
LEAD_ARTICLES = re.compile(r"^(?:a|an|the)\s+", re.I)


def norm(title: str, aggressive: bool = False) -> str:
    """Conservative: case, Unicode form, dashes, LaTeX, punctuation, space.

    aggressive additionally drops a leading article and any subtitle after a
    colon/dash -- the two rules most likely to cause false merges, so they are
    opt-in and measured separately.
    """
    t = title or ""
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"\$[^$]*\$", " ", t)              # inline maths
    t = re.sub(r"\\[A-Za-z]+\s*", " ", t)         # latex commands
    t = "".join("-" if c in DASHES else c for c in t)
    t = t.replace("&", " and ")
    t = t.lower()
    if aggressive:
        t = re.split(r"\s*[:\u2013\u2014-]\s+", t, maxsplit=1)[0]
        t = LEAD_ARTICLES.sub("", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def fetch(url: str, tries: int = 3, sleep: float = 2.0):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                        timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                return None
            time.sleep(sleep * (i + 1))
        except Exception:
            time.sleep(sleep * (i + 1))
    return None


def arxiv_titles(ids: list) -> dict:
    cache = os.path.join(CACHE, "arxiv_titles.json")
    os.makedirs(CACHE, exist_ok=True)
    known = json.load(open(cache, encoding="utf-8")) if os.path.exists(cache) else {}
    todo = [i for i in ids if i not in known]
    if todo:
        raw = fetch("http://export.arxiv.org/api/query?id_list="
                    + ",".join(todo) + f"&max_results={len(todo)}")
        if raw:
            for e in ET.fromstring(raw).findall("a:entry", ARXIV_NS):
                aid = e.findtext("a:id", "", ARXIV_NS).rsplit("/", 1)[-1]
                base = re.sub(r"v\d+$", "", aid)
                known[base] = {
                    "title": " ".join((e.findtext("a:title", "", ARXIV_NS) or "").split()),
                    "published": (e.findtext("a:published", "", ARXIV_NS) or "")[:10],
                    "doi": (e.findtext("{http://arxiv.org/schemas/atom}doi") or "").strip(),
                }
        json.dump(known, open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return known


def crossref_by_title(title: str, rows: int = 5) -> list:
    q = urllib.parse.quote(title[:300])
    raw = fetch(f"https://api.crossref.org/works?query.bibliographic={q}"
                f"&rows={rows}&select=DOI,title,issued,container-title,type")
    if not raw:
        return []
    try:
        items = json.loads(raw)["message"]["items"]
    except Exception:
        return []
    out = []
    for it in items:
        t = (it.get("title") or [""])[0]
        yr = None
        try:
            yr = it["issued"]["date-parts"][0][0]
        except Exception:
            pass
        out.append({"doi": it.get("DOI"), "title": " ".join(t.split()),
                    "year": yr, "venue": (it.get("container-title") or [""])[:1],
                    "type": it.get("type")})
    return out


def part_a() -> None:
    print("=" * 104)
    print("A) 同一篇论文，arXiv 标题 vs Crossref 按标题检索到的正式版")
    ids = arxiv_titles(SAMPLE)
    summary = []
    for pid in SAMPLE:
        rec = ids.get(pid)
        if not rec:
            print(f"--- {pid}: arXiv 元数据缺失")
            continue
        hits = crossref_by_title(rec["title"])
        na = norm(rec["title"])
        match = next((h for h in hits if norm(h["title"]) == na), None)
        if match is None:
            match = next((h for h in hits if norm(h["title"], True) == norm(rec["title"], True)), None)
            kind = "宽松命中" if match else "未命中"
        else:
            kind = "严格命中"
        summary.append((pid, kind, rec["published"]))
        print(f"--- {pid}  [{kind}]  arXiv published={rec['published']}")
        print(f"    arXiv   : {rec['title'][:96]}")
        if match:
            same = "标题字符串完全相同" if match["title"] == rec["title"] else "标题字符串不同"
            print(f"    Crossref: {match['title'][:96]}")
            print(f"              DOI={match['doi']}  year={match['year']}  ({same})")
        else:
            for h in hits[:2]:
                print(f"    候选?   : {h['title'][:80]}  ({h['year']}, {h['doi']})")
        time.sleep(1.2)
    print()
    hit = sum(1 for _p, k, _d in summary if k != "未命中")
    print(f"    小结：{hit}/{len(summary)} 篇能用标题在 Crossref 找回正式版")


def part_b() -> None:
    print("\n" + "=" * 104)
    print("B) 标题哈希的假阳性：泛化标题下，有多少个不同 DOI 会塌成同一个 key")
    queries = ["Model predictive control",
               "Robust model predictive control",
               "Reinforcement learning for control",
               "Adaptive control of nonlinear systems",
               "Fault diagnosis and fault-tolerant control",
               "Trajectory tracking control of mobile robots"]
    tot_doi = tot_collapsed = 0
    for q in queries:
        raw = fetch("https://api.crossref.org/works?query.bibliographic="
                    + urllib.parse.quote(q) + "&rows=40"
                    "&select=DOI,title,issued")
        if not raw:
            print(f"  {q!r}: 请求失败")
            continue
        items = json.loads(raw)["message"]["items"]
        groups = {}
        for it in items:
            t = (it.get("title") or [""])[0]
            if not t:
                continue
            groups.setdefault(norm(t, True), set()).add(it.get("DOI"))
            tot_doi += 1
        worst = max(groups.items(), key=lambda kv: len(kv[1]), default=("", set()))
        n_coll = sum(len(v) for v in groups.values() if len(v) > 1)
        tot_collapsed += n_coll
        print(f"  {q!r}")
        print(f"     返回 {len(items)} 条 → {len(groups)} 个规范化标题；"
              f"其中 {n_coll} 条的 key 与别的 DOI 撞车")
        if len(worst[1]) > 1:
            print(f"     最大撞车组：{len(worst[1])} 个不同 DOI 同为 {worst[0]!r}")
    print(f"\n    合计：{tot_doi} 条记录中出现 {tot_collapsed} 次 key 撞车"
          f"（{100*tot_collapsed/max(tot_doi,1):.0f}%）")


def part_c() -> None:
    print("\n" + "=" * 104)
    print("C) 归一化规则演示（同一标题在两端的差异如何被吃掉）")
    pairs = [
        ("Exploiting Symmetry for Discrete-Time Reachability Computations",
         "Exploiting symmetry for discrete-time reachability computations"),
        ("Optimal Decay Matrix Control Barrier Functions: Soundness and Completeness",
         "Optimal Decay Matrix Control Barrier Functions"),
        ("Safe Unicycle Navigation Among Multiple Obstacles Under Bounded Disturbances: A Learning-Based Approach",
         "Safe unicycle navigation among multiple obstacles under bounded disturbances"),
        ("Robust $\u211d^n$ Tracking with H\u221e Guarantees",
         "Robust Rn tracking with H-infinity guarantees"),
    ]
    for a, b in pairs:
        print(f"  A: {a[:78]}")
        print(f"  B: {b[:78]}")
        print(f"     严格归一化 相同? {norm(a) == norm(b)}    "
              f"宽松归一化 相同? {norm(a, True) == norm(b, True)}")
        if norm(a) != norm(b):
            print(f"     strict A={norm(a)[:70]!r}")
            print(f"     strict B={norm(b)[:70]!r}")


if __name__ == "__main__":
    part_a()
    part_b()
    part_c()
