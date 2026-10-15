# -*- coding: utf-8 -*-
"""25_abstract_truncation.py — search --abstract 的截断长度取舍实测

撑起：ADR-0059（默认从 240 调到 800）、EX-2026-10-16-摘要截断
用法：python 25_abstract_truncation.py [--db <sidecar.db>]

测法：读 sidecar 的 record.abstract（全文），统计长度分布；再用正则定位贡献句
（we propose / we introduce / …）起点，算「阈值 c 下方法句是否可见」。
样本是运行期本地库，条数随使用变化——数字是当时快照，脚本是复现路径。
"""
import argparse
import pathlib
import re
import sqlite3

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]

#: 贡献句线索：显式说「本文做了什么」的句式。
CUE = re.compile(
    r"(we (propose|introduce|present|develop|design|study|show|demonstrate)"
    r"|this (paper|work|article) (proposes|introduces|presents|studies))",
    re.I,
)
#: 认定「方法句可见」时，起点之后还要留出的字符数（够露出方法名）。
CUE_TAIL = 40


def pick(seq, p):
    return seq[min(len(seq) - 1, int(p * len(seq)))]


def load(db):
    con = sqlite3.connect(str(db))
    try:
        return [r[0] for r in con.execute(
            "SELECT abstract FROM record "
            "WHERE abstract IS NOT NULL AND length(abstract) > 0"
        )]
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "sidecar.db"))
    args = ap.parse_args()

    rows = load(args.db)
    n = len(rows)
    if not n:
        print("库里没有带摘要的 record：" + args.db)
        return
    lens = sorted(len(a) for a in rows)
    print("库：" + args.db)
    print("带摘要的 record：%d" % n)
    print("全文长度 min/p25/中位/p75/p90/max：%d / %d / %d / %d / %d / %d"
          % (lens[0], pick(lens, .25), pick(lens, .5), pick(lens, .75),
             pick(lens, .9), lens[-1]))
    print("平均全文长度：%.0f 字符" % (sum(lens) / n))
    print()

    pos = sorted(m.start() for a in rows if (m := CUE.search(a)))
    print("含贡献句的摘要：%d/%d = %.0f%%" % (len(pos), n, 100 * len(pos) / n))
    if pos:
        print("贡献句起点 min/p25/中位/p75/p90/max：%d / %d / %d / %d / %d / %d"
              % (pos[0], pick(pos, .25), pick(pos, .5), pick(pos, .75),
                 pick(pos, .9), pos[-1]))
    print()

    print("阈值取舍（载荷按 30 条、UTF-8 字节≈字符计）：")
    print("chars  30 条 abstract 载荷  平均保留文本  方法句可见  被截断条目")
    for c in (240, 320, 400, 500, 600, 800, 1000, 1200):
        payload = sum(min(len(a), c) for a in rows) / n * 30 / 1024
        kept = sum(min(len(a), c) for a in rows) / sum(lens)
        vis = sum(1 for x in pos if x + CUE_TAIL <= c) / len(pos) if pos else 0.0
        trunc = sum(1 for a in rows if len(a) > c) / n
        print("%5d  %14.1f KB  %11.0f%%  %8.0f%%  %8.0f%%"
              % (c, payload, kept * 100, vis * 100, trunc * 100))
    print()
    print("全文 30 条上界：%.1f KB" % (sum(lens) / n * 30 / 1024))


if __name__ == "__main__":
    main()