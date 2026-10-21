"""核验 OpenAlex 的包含下界与作者 OR；需 OPENALEX_API_KEY，结果写 out/。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def main() -> int:
    key = os.environ.get("OPENALEX_API_KEY")
    if not key:
        print("需要 OPENALEX_API_KEY；不会打印或保存凭据。")
        return 1
    probes = [
        ("citations_equal", "cited_by_count:100", "cited_by_count:asc"),
        ("citations_min", "cited_by_count:100-", "cited_by_count:asc"),
        ("citations_gt", "cited_by_count:>100", "cited_by_count:asc"),
        ("fwci_equal", "fwci:2.5", "fwci:asc"),
        ("fwci_min", "fwci:2.5-", "fwci:asc"),
        ("fwci_gt", "fwci:>2.5", "fwci:asc"),
        ("authors_or", 'raw_author_name.search:"Richard S Sutton" OR "Michael L Littman"',
         "publication_date:desc"),
    ]
    rows = []
    for name, filters, sort in probes:
        row = {"name": name, "filter": filters, "sort": sort}
        params = {
            "filter": filters, "sort": sort, "per_page": 3,
            "select": "id,cited_by_count,fwci,authorships", "api_key": key,
        }
        request = Request("https://api.openalex.org/works?" + urlencode(params),
                          headers={"User-Agent": "academic-search-semantics-check/1"})
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
                row.update(status=response.status, count=payload.get("meta", {}).get("count"))
                row["items"] = [{
                    "id": work.get("id"), "cited_by_count": work.get("cited_by_count"),
                    "fwci": work.get("fwci"),
                    "authors": [a.get("raw_author_name") for a in work.get("authorships", [])],
                } for work in payload.get("results", [])]
                row["cost_usd"] = response.headers.get("x-ratelimit-cost-usd")
        except HTTPError as exc:
            row.update(status=exc.code,
                       error=exc.read().decode("utf-8", errors="replace").replace(key, "REDACTED")[:1000])
        except (URLError, TimeoutError, OSError) as exc:
            row.update(status=None, error=type(exc).__name__)
        rows.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "items"}, ensure_ascii=False))
    target = Path(__file__).parent / "out" / "query-semantics.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"measured_at": datetime.now(timezone.utc).isoformat(),
                                 "probes": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(row.get("status") == 200 for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
