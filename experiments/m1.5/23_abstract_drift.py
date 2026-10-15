"""M1.5 spike / step 24: does the ABSTRACT change between preprint and published?

The proposal under test: store every source record separately, and for parts that
did not change (e.g. the translated abstract) point them at the earliest record
instead of duplicating the work.

That shortcut is only safe if the source text is in fact unchanged.  If abstracts
drift, "point at the earliest" silently serves a translation of different text.
Measured on eess.SY papers that carry an author-supplied journal DOI, comparing
the arXiv abstract with the Crossref abstract of the published version.
"""
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "_cache")
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
UA = {"User-Agent": "academic-search-spike/0.1 (abstract drift test)"}
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 40


def fetch(url, tries=2):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                        timeout=60) as r:
                return r.read()
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def norm(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t or "")
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"\$[^$]*\$", " math ", t)
    t = re.sub(r"\\[A-Za-z]+\s*", " ", t)
    t = t.lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


def jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    return len(sa & sb) / max(len(sa | sb), 1)


def main() -> None:
    raw_path = os.path.join(CACHE, "eess_sy_raw_recent.xml")
    if not os.path.exists(raw_path):
        sys.exit("先跑 21_title_drift.py 生成缓存的 Atom")
    root = ET.fromstring(open(raw_path, "rb").read())

    pairs = []
    for e in root.findall("a:entry", NS):
        doi = (e.findtext("arxiv:doi", "", NS) or "").strip()
        summ = " ".join((e.findtext("a:summary", "", NS) or "").split())
        aid = e.findtext("a:id", "", NS).rsplit("/", 1)[-1]
        if doi and summ:
            pairs.append((aid, summ, doi))
    print(f"候选（有作者手填 DOI 且有摘要）：{len(pairs)} 篇，取前 {LIMIT} 篇")
    pairs = pairs[:LIMIT]

    tally = {"identical": 0, "minor": 0, "changed": 0, "no_abstract": 0, "no_record": 0}
    rows = []
    for aid, aabs, doi in pairs:
        got = fetch("https://api.crossref.org/works/" + urllib.parse.quote(doi))
        if not got:
            tally["no_record"] += 1
            time.sleep(0.8)
            continue
        try:
            msg = json.loads(got)["message"]
        except Exception:
            tally["no_record"] += 1
            continue
        cabs = " ".join(re.sub(r"<[^>]+>", " ", msg.get("abstract") or "").split())
        if not cabs:
            tally["no_abstract"] += 1
            time.sleep(0.8)
            continue
        na, nc = norm(aabs), norm(cabs)
        if na == nc:
            kind, j = "identical", 1.0
        else:
            j = jaccard(na, nc)
            kind = "minor" if j >= 0.95 else "changed"
        tally[kind] += 1
        rows.append((aid, kind, j, len(na.split()), len(nc.split())))
        time.sleep(0.8)

    print(f"\n{'arXiv':16s} {'判定':10s} {'Jaccard':>8s} {'arXiv词':>8s} {'正式版词':>8s}")
    for aid, kind, j, la, lc in rows:
        flag = "  " if kind == "identical" else "**"
        print(f"{flag}{aid:16s} {kind:10s} {j:8.2f} {la:8d} {lc:8d}")

    print("\n### 汇总")
    tot = sum(tally.values())
    for k, v in tally.items():
        print(f"  {k:14s} {v:3d}/{tot}  ({100*v/max(tot,1):.0f}%)")
    got_both = tally["identical"] + tally["minor"] + tally["changed"]
    if got_both:
        print(f"\n  两边都有摘要的 {got_both} 篇里：")
        print(f"    完全相同      {tally['identical']:3d}  "
              f"({100*tally['identical']/got_both:.0f}%)  → 可安全共享派生数据")
        print(f"    轻微差异(≥.95) {tally['minor']:3d}  "
              f"({100*tally['minor']/got_both:.0f}%)")
        print(f"    实质变化      {tally['changed']:3d}  "
              f"({100*tally['changed']/got_both:.0f}%)  → 「指向最早那个」会给出错误译文")


if __name__ == "__main__":
    main()
