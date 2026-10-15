"""M1.5 spike / step 9: can arXiv HTML be the primary route?

Two questions, both answerable from the arXiv API:
  1. Does the API announce HTML availability per paper? (one cheap call instead
     of probing /html/<id> one by one)
  2. How far back does HTML availability go?  Sample eess.SY across years.
"""
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

NS = {"a": "http://www.w3.org/2005/Atom"}
UA = {"User-Agent": "academic-search-spike/0.1 (M1.5 textification test)"}


def fetch(url: str, tries: int = 3) -> bytes:
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                        timeout=90) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(3 * (i + 1))
    raise SystemExit(f"fetch failed: {last!r}")


def query(q: str, n: int, sort: str = "submittedDate", order: str = "descending"):
    url = (f"http://export.arxiv.org/api/query?search_query={q}"
           f"&sortBy={sort}&sortOrder={order}&max_results={n}")
    return parse(fetch(url))


def query_ids(ids: list):
    url = ("http://export.arxiv.org/api/query?id_list=" + ",".join(ids)
           + f"&max_results={len(ids)}")
    return parse(fetch(url))


def parse(raw: bytes) -> list:
    root = ET.fromstring(raw)
    out = []
    for e in root.findall("a:entry", NS):
        aid = e.findtext("a:id", "", NS).rsplit("/", 1)[-1]
        links = [(l.get("rel"), l.get("type"), l.get("title"), l.get("href"))
                 for l in e.findall("a:link", NS)]
        out.append({
            "id": aid,
            "published": (e.findtext("a:published", "", NS) or "")[:10],
            "title": " ".join((e.findtext("a:title", "", NS) or "").split())[:60],
            "html": any(t == "text/html" for _r, t, _ti, _h in links),
            "links": [t for _r, t, _ti, _h in links if t],
        })
    return out


def report(label: str, rows: list) -> None:
    n = len(rows)
    h = sum(1 for r in rows if r["html"])
    print(f"\n### {label}:  HTML 可用 {h}/{n}  ({100*h/n:.0f}%)")
    for r in rows:
        print(f"   {'HTML ' if r['html'] else '  -- '} {r['id']:16s} "
              f"{r['published']}  {r['title']}")


def main() -> None:
    # 0) sanity check: the 8 papers we already know have HTML
    known = ["2610.17916", "2610.17929", "2610.18290", "2610.18311",
             "2610.18361", "2610.18630", "2610.18675", "2610.18758"]
    report("sanity: 本次 8 篇样本", query_ids(known))
    time.sleep(3.5)

    report("最老的 eess.SY", query("cat:eess.SY", 15, order="ascending"))
    time.sleep(3.5)

    for year in ("2018", "2020", "2022", "2024"):
        q = (f"cat:eess.SY+AND+submittedDate:[{year}01010000+TO+{year}12312359]")
        report(f"{year} 年的 eess.SY", query(q, 10, order="ascending"))
        time.sleep(3.5)


if __name__ == "__main__":
    main()
