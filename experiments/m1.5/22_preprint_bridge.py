"""M1.5 spike / step 23: the preprint <-> published bridge, measured end to end.

Scenario: a preprint is already cached (we hold `arxiv:XXXX`).  Later we meet the
published version, which may hand us nothing but a journal DOI.  Question: from
that DOI alone, can we get back to the preprint?

Three candidate bridges, cheapest first:
  1. the published record's OWN metadata mentions the arXiv id
  2. OpenAlex: the DOI's Work carries both locations (preprint + published)
  3. Semantic Scholar: /paper/DOI:<doi> exposes externalIds.ArXiv

Note on cost: single-entity GETs on OpenAlex are free, so testing a DOI by
  /works/doi:<doi>
costs nothing even though the daily filter budget is exhausted.  Good to know for
the design: resolution lookups can be made quota-free.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

UA = {"User-Agent": "academic-search-spike/0.1 (preprint bridge test)"}

# (arxiv id, its published DOI) -- real pairs verified earlier today
PAIRS = [
    ("0901.0633", "10.1007/s10994-012-5278-7"),            # 2009 -> 2012 journal
    ("1801.00516", "10.1109/LCSYS.2018.2800125"),          # 2018 -> 2018 journal
    ("2401.00682", "10.1109/ICCAIS59597.2023.10382267"),   # 2024 preprint -> 2023 conference
]

ARXIV_IN_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/([^/\s?#]+)", re.I)


def get(url, tries=2):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                        timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None
        except Exception:
            time.sleep(2 * (i + 1))
    return None, None


def openalex_by_doi(doi):
    sel = "id,doi,ids,locations,primary_location,publication_date,type"
    return get("https://api.openalex.org/works/doi:"
               + urllib.parse.quote(doi) + f"?select={sel}")


def s2_by_doi(doi):
    return get("https://api.semanticscholar.org/graph/v1/paper/DOI:"
               + urllib.parse.quote(doi) + "?fields=title,year,externalIds,publicationTypes")


def main():
    for ax, doi in PAIRS:
        print("=" * 100)
        print(f"预印本 arXiv:{ax}    正式版 DOI:{doi}")
        print(f"  arXiv 自己的 DataCite DOI（可构造，不需要查询）: 10.48550/arXiv.{ax}")

        # ---- bridge 2: OpenAlex, reached by DOI only
        st, j = openalex_by_doi(doi)
        print(f"\n  [桥 2] OpenAlex GET /works/doi:{doi}")
        print(f"         状态 {st}（单实体 GET，免费）")
        if j:
            print(f"         同一 Work: {j['id']}  type={j.get('type')} "
                  f"publication_date={j.get('publication_date')}")
            print(f"         ids = {json.dumps(j.get('ids'), ensure_ascii=False)}")
            found = None
            for loc in j.get("locations") or []:
                url = (loc.get("landing_page_url") or "")
                m = ARXIV_IN_URL.search(url)
                src = (loc.get("source") or {}).get("display_name") or "?"
                mk = json.dumps(loc.get("merge_key") or {}, ensure_ascii=False)
                ver = loc.get("version")
                print(f"           - {src:32s} ver={str(ver):18s} {url[:58]}")
                if mk and mk != "{}":
                    print(f"             merge_key = {mk}")
                if m:
                    found = m.group(1)
            print(f"         → 从 DOI 找到的 arXiv id: {found}   "
                  f"{'★ 桥通' if found and found.startswith(ax) else ('≠ 预期 ' + ax if found else '未找到')}")
        time.sleep(1.5)

        # ---- bridge 3: Semantic Scholar by DOI
        st, j = s2_by_doi(doi)
        print(f"\n  [桥 3] Semantic Scholar /paper/DOI:{doi}")
        print(f"         状态 {st}")
        if j:
            print(f"         externalIds = {json.dumps(j.get('externalIds'), ensure_ascii=False)}")
            got = (j.get("externalIds") or {}).get("ArXiv")
            print(f"         → externalIds.ArXiv = {got}   "
                  f"{'★ 桥通' if got and got.startswith(ax) else '未找到'}")
        time.sleep(2.5)


if __name__ == "__main__":
    main()
