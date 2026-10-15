"""M1.5 spike / step 1: list recent eess.SY papers from the arXiv API.

Plain stdlib only (urllib + xml.etree) — matches the "core pipeline = stdlib" rule.
"""
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

NS = {"a": "http://www.w3.org/2005/Atom"}
API = ("http://export.arxiv.org/api/query?"
       "search_query=cat:eess.SY"
       "&sortBy=submittedDate&sortOrder=descending&max_results={n}")


def fetch(url: str, tries: int = 3) -> bytes:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "academic-search-spike/0.1 (M1.5 textification test)"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # arXiv asks for a 3 s pause between calls
            last = e
            time.sleep(3 * (i + 1))
    raise SystemExit(f"fetch failed after {tries} tries: {last!r}")


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    raw = fetch(API.format(n=n))
    root = ET.fromstring(raw)
    for i, e in enumerate(root.findall("a:entry", NS), 1):
        aid = e.findtext("a:id", "", NS).rsplit("/", 1)[-1]
        title = " ".join((e.findtext("a:title", "", NS) or "").split())
        pub = (e.findtext("a:published", "", NS) or "")[:10]
        cats = [c.get("term") for c in e.findall("a:category", NS)]
        print(f"{i:2d}. {aid:16s} {pub}  {'/'.join(cats[:3]):24s} {title[:88]}")


if __name__ == "__main__":
    main()
