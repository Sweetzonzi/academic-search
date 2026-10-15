"""M1.5 spike / step 22: how much does a title drift between preprint and journal?

A title-derived uid fails in two opposite directions.  Direction 2 (two different
papers collapsing into one uid) was measured earlier: 16% under cross-source
normalisation, max 7 works sharing one key.  This measures direction 1: the SAME
paper getting two different title strings, hence two different uids.

Method: take arXiv papers that carry an author-supplied journal DOI, fetch the
published record from Crossref, and classify the difference.  Classification is
deliberately graded, because "differs" is not the same as "differs materially":

  identical      -- byte for byte
  case           -- only letter case
  punctuation    -- dashes / colons / spacing / markup only
  substantive    -- words added, removed or reordered  (this is the damaging one)
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
UA = {"User-Agent": "academic-search-spike/0.1 (title drift test)"}
DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 30


def fetch(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                        timeout=60) as r:
                return r.read()
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def clean(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t or "")
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"\$[^$]*\$", " ", t)
    t = re.sub(r"\\[A-Za-z]+\s*", " ", t)
    t = "".join("-" if c in DASHES else c for c in t).replace("&", " and ")
    return " ".join(t.split())


def letters_only(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean(t).lower())


def words(t: str) -> list:
    return re.findall(r"[a-z0-9]+", clean(t).lower())


def classify(a: str, b: str) -> str:
    if a == b:
        return "identical"
    if letters_only(a) == letters_only(b):
        return "case/punctuation"
    wa, wb = words(a), words(b)
    if wa == wb:
        return "case/punctuation"
    # substantive: how many words differ
    sa, sb = set(wa), set(wb)
    jac = len(sa & sb) / max(len(sa | sb), 1)
    return f"substantive (Jaccard {jac:.2f})"


def main() -> None:
    raw_cache = os.path.join(CACHE, "eess_sy_raw_recent.xml")
    os.makedirs(CACHE, exist_ok=True)
    if os.path.exists(raw_cache):
        raw = open(raw_cache, "rb").read()
    else:
        print("### 拉取 eess.SY 最近 2000 篇原始 Atom")
        raw = fetch("http://export.arxiv.org/api/query?search_query=cat:eess.SY"
                    "&sortBy=submittedDate&sortOrder=descending&max_results=2000")
        if not raw:
            sys.exit("失败")
        open(raw_cache, "wb").write(raw)

    pairs = []
    for e in ET.fromstring(raw).findall("a:entry", NS):
        doi = (e.findtext("arxiv:doi", "", NS) or "").strip()
        title = " ".join((e.findtext("a:title", "", NS) or "").split())
        aid = e.findtext("a:id", "", NS).rsplit("/", 1)[-1]
        if doi and title:
            pairs.append((aid, title, doi))
    print(f"  2000 篇里有作者手填期刊 DOI 的：{len(pairs)} 篇"
          f"（占 {100*len(pairs)/2000:.0f}%）")
    pairs = pairs[:LIMIT]

    print(f"\n### 逐个取正式版标题对比（最多 {len(pairs)} 篇）")
    tally = {}
    for aid, at, doi in pairs:
        got = fetch("https://api.crossref.org/works/" + urllib.parse.quote(doi))
        if not got:
            print(f"  {aid:16s} Crossref 取不到 {doi}")
            time.sleep(1.0)
            continue
        try:
            ct = " ".join((json.loads(got)["message"]["title"] or [""])[0].split())
        except Exception:
            ct = ""
        if not ct:
            continue
        kind = classify(at, ct)
        tally[kind] = tally.get(kind, 0) + 1
        flag = "  " if kind == "identical" else "**"
        print(f"  {flag}{aid:16s} {kind}")
        if kind != "identical":
            print(f"      arXiv   : {at[:88]}")
            print(f"      Crossref: {ct[:88]}")
        time.sleep(1.0)

    print("\n### 汇总")
    total = sum(tally.values())
    for k, v in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {k:34s} {v:3d}/{total}  ({100*v/max(total,1):.0f}%)")


if __name__ == "__main__":
    main()
