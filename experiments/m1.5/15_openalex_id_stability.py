"""M1.5 spike / step 16: verify the decisive claim independently.

Claim under test: a merged-away or deleted OpenAlex Work ID returns HTTP 404
with no forward pointer (the docs say both "301 redirect" and "404", so the
docs cannot settle it).

Cost accounting that makes this test possible without burning the daily budget:
single-entity GETs are documented as free, and 4xx/5xx responses are free, so
this whole probe costs nothing -- unlike the filter/search calls that exhausted
the keyless per-IP budget earlier today.

The test set is taken from OpenAlex's own published deletion log, so these are
IDs OpenAlex itself says are gone -- not invented ones.
"""
import csv
import gzip
import io
import os
import re
import sys
import urllib.error
import urllib.request

LOG_URL = "https://openalex.s3.amazonaws.com/data/jsonl/works/deleted_ids.csv.gz"
UA = {"User-Agent": "academic-search-spike/0.1 (identity verification)"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """We must see the raw status: a 301 would mean the docs' first story is true."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def head(url: str):
    op = urllib.request.build_opener(NoRedirect)
    try:
        with op.open(urllib.request.Request(url, headers=UA, method="GET"),
                     timeout=45) as r:
            return r.status, r.headers.get("Location"), len(r.read(200))
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location") if e.headers else None, 0
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", 0


def main() -> None:
    cache = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "_cache", "deleted_work_ids.csv.gz")
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    if os.path.exists(cache):
        print(f"### using cached deletion log ({os.path.getsize(cache)/1024/1024:.1f} MB)")
        raw = open(cache, "rb").read()
    else:
        print(f"### downloading deletion log: {LOG_URL}")
        try:
            with urllib.request.urlopen(urllib.request.Request(LOG_URL, headers=UA),
                                        timeout=300) as r:
                raw = r.read()
        except Exception as e:
            print(f"  FAILED: {e!r}")
            return
        open(cache, "wb").write(raw)
        print(f"  {len(raw)/1024/1024:.1f} MB compressed")

    rows = list(csv.reader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)),
                                            encoding="utf-8")))
    header, body = rows[0], rows[1:]
    print(f"  header: {header}")
    print(f"  rows  : {len(body):,}")

    def as_wid(v: str) -> str:
        """The log stores full URLs in the work_id column; keep the W-number."""
        m = re.search(r"(W\d+)$", v.strip())
        return m.group(1) if m else v.strip()

    works = [as_wid(r[0]) for r in body if r and r[0].strip()]
    print(f"  sample deleted work ids: {works[:5]}")
    print(f"  newest deleted_date    : {max(r[1] for r in body if len(r) > 1)}")

    print("\n### probing deleted work IDs (expect 404, no Location)")
    picks = works[:3] + works[len(works) // 2:len(works) // 2 + 1] + works[-1:]
    for wid in picks:
        st, loc, n = head(f"https://api.openalex.org/works/{wid}")
        print(f"  {wid:14s} status={st}  location={loc}  body~{n}B")

    print("\n### control: a live work ID (expect 200)")
    st, loc, n = head("https://api.openalex.org/works/W2146292423")
    print(f"  W2146292423    status={st}  location={loc}  body~{n}B")


if __name__ == "__main__":
    sys.exit(main())
