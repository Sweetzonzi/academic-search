"""M1.5 spike / step 20: is  uid = hash(title.lower())  viable?

The question is NOT "can a title find the paper online". It is: if two different
papers happen to have the same lowercase title, they get the same uid, and then
their blobs/<uid>/, translations, notes and citation edges collide and silently
overwrite each other.

So the measurement is: in a real corpus of this field, how often do two DIFFERENT
papers share a normalised title?  Two refinements that matter:
  * hashing does not help -- hash(x) collides whenever x collides, and truncating
    the hash can only add collisions.  The collision rate of the normalised
    string is therefore a LOWER BOUND for the hash's rate.
  * same title + same authors is usually a re-post or a duplicate submission;
    same title + different authors is unambiguously two different papers.

Corpus: recent eess.SY (the group's field), pulled from the free arXiv API.
"""
import collections
import os
import re
import sys
import time
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "_cache")
NS = {"a": "http://www.w3.org/2005/Atom"}
UA = {"User-Agent": "academic-search-spike/0.1 (uid collision test)"}
DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"


def title_key(t: str) -> str:
    """The rule the user proposed: everything down to lowercase + punctuation out."""
    t = unicodedata.normalize("NFKC", t or "")
    t = re.sub(r"<[^>]+>", " ", t)          # MathML/JATS that Crossref embeds
    t = re.sub(r"\$[^$]*\$", " ", t)        # maths
    t = re.sub(r"\\[A-Za-z]+\s*", " ", t)   # latex commands
    t = "".join("-" if c in DASHES else c for c in t).replace("&", " and ")
    t = t.lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def fetch(url: str, tries: int = 3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                        timeout=180) as r:
                return r.read()
        except Exception as e:
            print(f"  retry {i+1}: {e!r}")
            time.sleep(3 * (i + 1))
    return None


def corpus(n: int = 2000) -> list:
    """arXiv allows max_results<=2000 per call, so page with `start`."""
    cache = os.path.join(CACHE, f"eess_sy_{n}.json")
    os.makedirs(CACHE, exist_ok=True)
    if os.path.exists(cache):
        import json
        return json.load(open(cache, encoding="utf-8"))
    import json
    print(f"### 下载 eess.SY 最近 {n} 篇元数据（免费 arXiv API，分页）")
    out, start = [], 0
    while len(out) < n:
        want = min(2000, n - len(out))
        raw = fetch(f"http://export.arxiv.org/api/query?search_query=cat:eess.SY"
                    f"&sortBy=submittedDate&sortOrder=descending"
                    f"&start={start}&max_results={want}")
        if not raw:
            break
        batch = ET.fromstring(raw).findall("a:entry", NS)
        if not batch:
            break
        for e in batch:
            out.append({
                "id": e.findtext("a:id", "", NS).rsplit("/", 1)[-1],
                "title": " ".join((e.findtext("a:title", "", NS) or "").split()),
                "authors": sorted(a.findtext("a:name", "", NS)
                                  for a in e.findall("a:author", NS)),
            })
        print(f"  已取 {len(out)}")
        start += len(batch)
        time.sleep(3.2)          # arXiv asks for 3 s between calls
    json.dump(out, open(cache, "w", encoding="utf-8"), ensure_ascii=False)
    return out


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    rows = corpus(n)
    print(f"  取回 {len(rows)} 篇")

    groups = collections.defaultdict(list)
    for r in rows:
        groups[title_key(r["title"])].append(r)

    collided = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"\n### 归一化标题（= 你的 uid 的碰撞行为）")
    print(f"  记录数            : {len(rows)}")
    print(f"  不同标题 key 数    : {len(groups)}")
    print(f"  撞车组数          : {len(collided)}")
    print(f"  涉及记录数        : {sum(len(v) for v in collided.values())} "
          f"({100*sum(len(v) for v in collided.values())/len(rows):.2f}%)")

    diff_author = {k: v for k, v in collided.items()
                   if len({tuple(r["authors"]) for r in v}) > 1}
    print(f"  其中「标题相同但作者不同」= 确是不同论文: {len(diff_author)} 组")

    print("\n### 逐组明细")
    for k, v in sorted(collided.items(), key=lambda kv: -len(kv[1])):
        same = len({tuple(r["authors"]) for r in v}) == 1
        tag = "同作者(疑似重复投稿)" if same else "★不同作者=不同论文"
        print(f"  [{len(v)} 篇] {tag}  key={k!r}")
        for r in v:
            au = r["authors"][0].split()[-1] if r["authors"] else "?"
            print(f"      {r['id']:16s} {au:14s} {r['title'][:70]}")

    print("\n### 结论读数")
    print("  哈希不会降低碰撞：hash(key) 在 key 相同处必然相同，截断只会更多。")
    print(f"  所以上面这 {len(collided)} 组（{len(diff_author)} 组确为不同论文）"
          f"就是 hash(title.lower()) 当 uid 时的 uid 冲突数。")
    print("  在 2000 篇的语料里看比例不高，但冲突不是『比例问题』——一次冲突就让")
    print("  两篇论文的 blobs/<uid>/、翻译、笔记、引用边全部互相覆盖。")


if __name__ == "__main__":
    main()
