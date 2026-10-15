# -*- coding: utf-8 -*-
"""24_body_scan_bench.py — 正文检索走文件扫描的成本实测

撑起：ADR-0036（正文检索走 works/ 文件扫描，SQLite 的 FTS5 只管标题/摘要/翻译）
用法：python 24_body_scan_bench.py [--needle attention] [--repeat 5]

测法：把 textified*/**/paper.md 全部读进内存，做全量子串扫描，统计命中数与耗时。
这是"正文检索"在初版的真实成本上界——因为 ADR-0035 限定了只有被要过的论文才有全文。
"""
import argparse
import pathlib
import statistics
import time

HERE = pathlib.Path(__file__).resolve().parent
MISS = "zzq-nonexistent-needle"


def collect():
    files = sorted(HERE.glob("textified*/**/paper.md"))
    texts = [f.read_text(encoding="utf-8", errors="replace") for f in files]
    return files, texts


def scan(texts, needle):
    lo = needle.lower()
    hits = 0
    for t in texts:
        s = t.lower()
        i = s.find(lo)
        while i >= 0:
            hits += 1
            i = s.find(lo, i + 1)
    return hits


def timeit(texts, needle, repeat):
    ts = []
    hits = 0
    for _ in range(repeat):
        t0 = time.perf_counter()
        hits = scan(texts, needle)
        ts.append((time.perf_counter() - t0) * 1000)
    return hits, statistics.median(ts), min(ts), max(ts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--needle", default="attention")
    ap.add_argument("--repeat", type=int, default=5)
    args = ap.parse_args()

    files, texts = collect()
    total = sum(len(t.encode("utf-8")) for t in texts)
    print("语料：%d 篇 paper.md，%.2f MB" % (len(files), total / 1024 / 1024))
    if texts:
        sizes = sorted(len(t.encode("utf-8")) for t in texts)
        print("单篇字节：min %d / max %d / 均值 %d" % (sizes[0], sizes[-1], total // len(sizes)))

    med = {}
    for label, needle in (("命中", args.needle), ("无命中", MISS)):
        hits, m, lo, hi = timeit(texts, needle, args.repeat)
        med[label] = m
        print("%s扫描 %r：命中 %d 处，中位数 %.1f ms（%d 次；最快 %.1f / 最慢 %.1f）"
              % (label, needle, hits, m, args.repeat, lo, hi))

    if total:
        per_mb = med["无命中"] / (total / 1024 / 1024)
        print()
        print("外推（按无命中 %.2f ms/MB，单篇按 140 KB 计）：" % per_mb)
        for n in (100, 500, 1000):
            mb = n * 140 / 1024
            print("  %5d 篇 ≈ %6.1f MB ≈ %6.0f ms" % (n, mb, per_mb * mb))


if __name__ == "__main__":
    main()
