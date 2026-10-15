"""M1.5 spike / step 19: the uid minting rule, implemented with stdlib only.

Rule (policy):
  * a uid is MINTED exactly once, at the moment a work is first seen
  * it is never derived from content, never recomputed, never re-minted
  * exactly one function in the codebase mints (resolve_or_mint); nothing else
    may construct one
  * it carries NO semantics -- no source prefix, no date, no title fragment --
    because semantics in an id tempt someone to recompute it later

Format: ULID -- 26 chars, Crockford base32.
    01JQ8Z4M7T 9K2VQH3N5P8R1XYZ
    |--------| |--------------|
    48-bit ms  80-bit random
Sortable: lexicographic order == creation order, which makes it usable directly
as a filename prefix and as a stable tiebreaker in queries.
"""
import os
import time

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford: no I, L, O, U
DECODE = {c: i for i, c in enumerate(ALPHABET)}


def _b32(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_uid(timestamp_ms: int | None = None, monotonic: bool = True) -> str:
    """Mint one uid. 48-bit ms timestamp + 80-bit randomness.

    Monotonic mode follows the ULID spec: inside one millisecond the whole 80-bit
    random field is incremented as an integer, so ordering never depends on how
    the counter happens to wrap.  If that field overflows, step the timestamp.
    """
    global _last_ms, _last_rnd
    ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    if monotonic and ts == _last_ms:
        _last_rnd += 1
        if _last_rnd >> 80:                    # 80-bit overflow: bill it to the clock
            ts += 1
            _last_rnd = 0
    else:
        _last_rnd = int.from_bytes(os.urandom(10), "big")
    if monotonic and ts > _last_ms:
        _last_ms = ts
    elif not monotonic:
        pass
    else:
        _last_ms = max(_last_ms, ts)
    return _b32(ts, 10) + _b32(_last_rnd, 16)


def uid_time_ms(uid: str) -> int:
    ts = 0
    for c in uid[:10]:
        ts = (ts << 5) | DECODE[c]
    return ts


_last_ms, _last_rnd = 0, 0


def main() -> None:
    print("### 铸 8 个 uid（同一毫秒内连铸，验证单调性）")
    ids = [new_uid() for _ in range(8)]
    for u in ids:
        print(f"  {u}   len={len(u)}")

    print("\n### 性质检查")
    print(f"  长度都为 26            : {all(len(u) == 26 for u in ids)}")
    print(f"  字符集合法(无 I/L/O/U) : {all(all(c in ALPHABET for c in u) for u in ids)}")
    print(f"  无重复                 : {len(set(ids)) == len(ids)}")
    print(f"  字典序 == 生成序       : {ids == sorted(ids)}")
    print(f"  时间戳可解回           : {[uid_time_ms(u) for u in ids][:3]}")
    print(f"  当前毫秒               : {int(time.time()*1000)}")

    print("\n### 同一毫秒铸 10000 个（撞车与单调性压力测试）")
    ts = int(time.time() * 1000)
    batch = [new_uid(timestamp_ms=ts) for _ in range(10000)]
    print(f"  10000 个全部唯一       : {len(set(batch)) == 10000}")
    print(f"  字典序 == 生成序       : {batch == sorted(batch)}")

    print("\n### 文件系统友好性（uid 直接当目录名）")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        paths = [os.path.join(d, u) for u in ids[:3]]
        for p in paths:
            os.makedirs(p, exist_ok=True)
        print(f"  mkdir 成功             : {all(os.path.isdir(p) for p in paths)}")
        print(f"  样例路径               : blobs/{ids[0]}/paper.md")


if __name__ == "__main__":
    main()
