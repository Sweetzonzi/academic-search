"""M1.5 spike / step 10: verify that /html/<id> really serves a usable article.

The Atom feed advertises a text/html link for every entry -- including papers
from 2004 -- which is either great news or a lie.  Fetch them and look at what
actually comes back: is there an <article class="ltx_document">, how big is it,
and does it contain real sections/equations/figures?
"""
import re
import sys
import time
import urllib.error
import urllib.request

from lxml import html as LH

UA = {"User-Agent": "academic-search-spike/0.1 (M1.5 textification test)"}


def get(url: str, tries: int = 2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.status, r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return e.code, b"", ""
        except Exception as e:
            last = e
            time.sleep(3 * (i + 1))
    return None, b"", f"{last!r}"


def probe(aid: str) -> dict:
    st, blob, ct = get(f"https://arxiv.org/html/{aid}")
    row = {"id": aid, "status": st, "kb": round(len(blob) / 1024, 1), "ct": ct[:30]}
    if st != 200 or not blob:
        return row
    doc = LH.fromstring(blob.decode("utf-8", "replace"))
    art = doc.xpath("//article")
    row["article"] = bool(art)
    if art:
        a = art[0]
        row["chars"] = len(" ".join(a.text_content().split()))
        row["h2"] = len(a.xpath(".//h2"))
        row["eqs"] = len(a.xpath(".//*[contains(@class,'ltx_equation')]"))
        row["figs"] = len(a.xpath(".//figure"))
        row["imgs"] = len(a.xpath(".//img")) + len(a.xpath(".//object[@data]"))
        row["tables"] = len(a.xpath(".//table"))
    else:
        txt = " ".join(doc.text_content().split())[:200]
        row["notice"] = txt
    return row


def main() -> None:
    ids = sys.argv[1:]
    print(f"{'id':16s} {'st':>4s} {'KB':>7s} {'chars':>7s} {'h2':>4s} {'eq':>4s} "
          f"{'fig':>4s} {'img':>4s} {'tbl':>4s}  notice")
    for aid in ids:
        r = probe(aid)
        print(f"{r['id']:16s} {str(r['status']):>4s} {r['kb']:7.1f} "
              f"{r.get('chars', 0):7d} {r.get('h2', 0):4d} {r.get('eqs', 0):4d} "
              f"{r.get('figs', 0):4d} {r.get('imgs', 0):4d} {r.get('tables', 0):4d}  "
              f"{r.get('notice', '')[:60]}")
        time.sleep(3.2)


if __name__ == "__main__":
    main()
