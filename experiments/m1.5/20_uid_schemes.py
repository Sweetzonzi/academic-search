"""M1.5 spike / step 21: opaque uid vs derived uid -- what each one actually buys.

The user's instinct: a uid you can RECOMPUTE from a network record lets you do
"fetch -> compute -> is it already recorded?" with one indexed probe.  That is a
real and desirable property.  The question is only whether the recomputable value
has to BE the primary key, or whether it can be an indexed alias of an opaque key.

Schemes compared:
  A. derived   uid = base32(sha256(kind:value))[:26]      -- reproducible
  B. opaque    uid = ULID                                  -- not reproducible
  B'. slug     uid = raw identifier with '/' escaped       -- reproducible, readable
And, for both, the lookup statement that answers "already recorded?".
"""
import base64
import hashlib
import os
import re
import time
import unicodedata

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def b32(data: bytes | int, length: int) -> str:
    n = data if isinstance(data, int) else int.from_bytes(data, "big")
    out = []
    for _ in range(length):
        out.append(ALPHABET[n & 31])
        n >>= 5
    return "".join(reversed(out))


def derive_uid(kind: str, value: str) -> str:
    """Scheme A: deterministic, fixed length, filesystem-safe, no escaping needed."""
    v = unicodedata.normalize("NFKC", value).strip().lower()
    # DOIs are case-insensitive; arXiv ids keep their path form (math/0601001)
    return b32(hashlib.sha256(f"{kind}:{v}".encode()).digest(), 26)


def opaque_uid() -> str:
    """Scheme B: time + randomness. Reproducible only by looking it up."""
    ts = int(time.time() * 1000)
    return b32(ts, 10) + b32(os.urandom(10), 16)


def raw_slug(kind: str, value: str) -> str:
    """Scheme B': keep the identifier readable -- needs escaping for a path."""
    return f"{kind}-" + re.sub(r"[^A-Za-z0-9._-]", "_", value)


def main() -> None:
    # the same paper, as four different sources would hand it to us
    sightings = [
        ("arXiv API",    "arxiv",  "2610.17929"),
        ("OpenAlex",     "openalex", "W4412345678"),
        ("S2 (by arXiv)", "doi",   "10.48550/arXiv.2610.17929"),
        ("its journal",  "doi",    "10.1109/LCSYS.2026.1234567"),
    ]
    print("### 同一篇论文从四个来源到手，两种方案各铸出什么")
    print(f"{'来源':14s} {'标识':34s} {'A 派生 uid':28s} {'B ULID(26)':28s} B' raw slug")
    for src, kind, val in sightings:
        print(f"{src:14s} {kind+':'+val:34s} {derive_uid(kind, val):28s} "
              f"{opaque_uid():28s} {raw_slug(kind, val)}")

    print("\n### A 的可重现性（模拟'另一台机器'重新算出同一个值）")
    for kind, val in (("arxiv", "2610.17929"), ("doi", "10.1109/LCSYS.2026.1234567")):
        a, b = derive_uid(kind, val), derive_uid(kind, val)
        print(f"  {kind}:{val:34s} 两次相同? {a == b}   {a}")
    print("  DOI 大小写/空白归一化后仍相同? "
          f"{derive_uid('doi', ' 10.1109/LCSYS.2026.1234567 ') == derive_uid('doi', '10.1109/lcsys.2026.1234567')}")

    print("\n### A 的致命处：来源不同 → uid 不同（同一篇论文两个 uid）")
    u_ax = derive_uid("arxiv", "2610.17929")
    u_oa = derive_uid("openalex", "W4412345678")
    u_doi = derive_uid("doi", "10.48550/arXiv.2610.17929")
    print(f"  arxiv:{u_ax}")
    print(f"  openalex:{u_oa}")
    print(f"  doi:{u_doi}")
    print(f"  三者互不相同 → 仍然需要别名表把它们缝起来: {len({u_ax,u_oa,u_doi}) == 3}")

    print("\n### B' 的可读性与歧义（直接拿标识符当目录名）")
    print(f"  doi:10.1002/9781119471455.ch10   ->  {raw_slug('doi','10.1002/9781119471455.ch10')}")
    print(f"  doi:10.1234/a/b 与 doi:10.1234/a_b 是两个不同的 DOI，转义后:")
    print(f"      {raw_slug('doi','10.1234/a/b')}")
    print(f"      {raw_slug('doi','10.1234/a_b')}")
    print(f"      是否相同（会撞目录）? {raw_slug('doi','10.1234/a/b') == raw_slug('doi','10.1234/a_b')}")
    print(f"  arXiv 老式 id: arxiv:math/0601001 -> {raw_slug('arxiv','math/0601001')}")
    print(f"  Windows 大小写不敏感：'doi-10.1109/LCSYS' 与 'doi-10.1109/lcsys' 同目录? "
          f"{raw_slug('doi','10.1109/LCSYS').lower() == raw_slug('doi','10.1109/lcsys').lower()}")

    print("\n### 查询语句：两种方案各自怎么回答『这条网络结果记过没有？』")
    print("  A 派生 uid 当主键:   SELECT 1 FROM work WHERE work_uid = :derived")
    print("  B 不透明 uid + 别名: SELECT work_uid FROM work_alias WHERE ns=:kind AND value=:val")
    print("  → 都是一次索引命中、一行代码。区别只在这个值能不能变、以及它在不在主键里。")


if __name__ == "__main__":
    main()
