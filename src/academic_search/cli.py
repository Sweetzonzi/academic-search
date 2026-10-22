"""参数解析、命令分派、信封组装与退出码。

契约：docs/03-接口契约.md §1、docs/接口/输出载荷.md。
不碰 SQL 与网络（DEV-CONV §4）；用例编排在 service.py。
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

from . import __version__, config, envelope, paths, service
from .sources import SearchSpec


def resolve_root(explicit: str | None = None) -> Path:
    """--root → ACADEMIC_SEARCH_ROOT → 当前目录（ADR-0055）。"""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("ACADEMIC_SEARCH_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="academic-search")
    parser.add_argument("--root", help="仓库根（含 config/ 与 data/）")
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="当场给的检索式 → 刷新（可选）→ 查库 → 结果列表")
    search.add_argument("--all-of", action="extend", nargs="+", default=[], metavar="词")
    search.add_argument("--any-of", action="extend", nargs="+", default=[], metavar="词")
    search.add_argument("--venue", action="extend", nargs="+", default=[], metavar="名")
    search.add_argument("--author", action="extend", nargs="+", default=[], metavar="名")
    search.add_argument("--since-years", type=int, default=None)
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--offset", type=int, default=0, metavar="N",
                        help="过滤排序后跳过前 N 个 work，只影响本地展示")
    search.add_argument("--source", action="append", default=[], metavar="源")
    search.add_argument("--refresh", choices=["auto", "true", "false"], default="auto")
    search.add_argument("--max-age", type=float, default=None, dest="max_age")
    search.add_argument("--in", choices=["meta", "body", "both"], default="meta", dest="in_scope")
    search.add_argument(
        "--sort", choices=["citations", "date"], default="date",
        help="日期或被引次数降序；arXiv 不提供引用指标，单独排序仍保留该源，"
             "无可用被引数据的结果排后；arXiv 采集仍按日期取数",
    )
    search.add_argument(
        "--min-citations", type=int, default=None, metavar="N",
        help="被引次数下限（>= N）；arXiv 不提供被引次数，启用时跳过该源采集；"
             "本地按聚合指标筛选，无指标的论文不通过",
    )
    search.add_argument(
        "--min-fwci", type=float, default=None, metavar="X",
        help="领域归一化引用影响力下限（>= X）；arXiv 不提供 FWCI，启用时跳过该源采集；"
             "本地无该指标的论文不通过，与 --min-citations 同时给出时取与",
    )
    search.add_argument(
        "--abstract", action="store_true",
        help="条目附上摘要，截断到 search.toml 的 abstract.chars（默认 800）",
    )

    lookup = sub.add_parser("lookup", help="按 DOI / arXiv ID / 标题取条目或候选")
    lookup.add_argument("--doi")
    lookup.add_argument("--arxiv")
    lookup.add_argument("--title")
    lookup.add_argument("--limit", type=int, default=20)
    lookup.add_argument("--refresh", choices=["auto", "true", "false"], default="auto")

    sync = sub.add_parser("sync", help="画像里的具名查询 → 采集 → 落候选池 → 报告")
    sync.add_argument("--query", action="append", default=[], metavar="名",
                      help="具名查询，可重复；缺省 = 画像里全部启用且未过期的查询")
    sync.add_argument("--refresh", choices=["auto", "true", "false"], default="auto")

    download = sub.add_parser(
        "download",
        help="取原始字节（arXiv LaTeX 源码 / HTML / PDF）落 bytes/ 并投影到 works/",
    )
    download.add_argument("--work", required=True, metavar="work_key")
    download.add_argument(
        "--source", choices=list(service.DOWNLOAD_SOURCES), default="auto",
        help="auto=LaTeX 优先、失败回退 HTML；pdf 只下载不解析；all=三者都取",
    )
    download.add_argument("--refresh", choices=["auto", "true", "false"], default="auto")

    read = sub.add_parser("read", help="从 arXiv LaTeX / HTML 物化正文，返回路径、章节与图件定位器")
    read.add_argument("--work", required=True, metavar="work_key")
    read.add_argument("--refresh", choices=["auto", "true", "false"], default="auto")

    profile = sub.add_parser("profile", help="画像的只读通道")
    profile_sub = profile.add_subparsers(dest="subcommand", required=True)
    profile_sub.add_parser("show")
    profile_sub.add_parser("check")
    profile_sub.add_parser("scaffold")

    verify = sub.add_parser("verify", help="抽样重算 bytes/ 的 sha256")
    verify.add_argument("--sample", type=int, default=20)

    sub.add_parser("version")
    return parser


def _dispatch(args, name: str):
    root = resolve_root(args.root)
    if name == "search":
        for option, value in (("--max-age", args.max_age), ("--min-fwci", args.min_fwci)):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise envelope.EnvelopeError(
                    envelope.INVALID_ARGS, option + " 必须是有限的非负数",
                )
        if args.min_citations is not None and args.min_citations < 0:
            raise envelope.EnvelopeError(envelope.INVALID_ARGS, "--min-citations 必须是非负整数")
        cfg = config.load_search(root)
        enabled = config.load_sources(root)
        requested = list(args.source) if args.source else config.active_sources(enabled)
        invalid = [s for s in requested if s not in config.SEARCH_SOURCES or not enabled.get(s)]
        if invalid or not requested:
            raise envelope.EnvelopeError(
                envelope.INVALID_ARGS,
                "search 来源未知、不支持或未启用：" + "、".join(invalid or ["没有启用的采集源"]),
            )
        sources = list(dict.fromkeys(requested))
        for option in ("all_of", "any_of", "venue", "author"):
            values = getattr(args, option)
            if any(not value.strip() for value in values):
                raise envelope.EnvelopeError(
                    envelope.INVALID_ARGS, "--" + option.replace("_", "-") + " 不能包含空值",
                )
        spec = SearchSpec(
            all_of=list(args.all_of),
            any_of=list(args.any_of),
            venues=list(args.venue),
            authors=list(args.author),
            since_years=args.since_years,
            min_citations=args.min_citations,
            min_fwci=args.min_fwci,
        )
        outcome = service.run_search(
            root, spec, limit=args.limit, offset=args.offset, sources=sources, refresh=args.refresh,
            max_age_hours=args.max_age if args.max_age else cfg.max_age_hours,
            sort=args.sort, in_scope=args.in_scope, venue_blacklist=cfg.venue_blacklist,
            abstract=args.abstract, abstract_chars=cfg.abstract_chars,
            max_candidates=cfg.local_max_candidates,
            quota_min_usd=cfg.min_remaining_usd,
        )
        return outcome.payload(), 0

    if name == "lookup":
        cfg = config.load_search(root)
        if not (args.doi or args.arxiv or args.title):
            raise envelope.EnvelopeError(envelope.INVALID_ARGS, "必须给 --doi / --arxiv / --title 之一")
        outcome = service.run_lookup(
            root, doi=args.doi, arxiv_id=args.arxiv, title=args.title,
            refresh=args.refresh, limit=args.limit, venue_blacklist=cfg.venue_blacklist,
            mailto=cfg.mailto,
        )
        return outcome.payload(), 0

    if name == "download":
        outcome = service.run_download(root, args.work, source=args.source, refresh=args.refresh)
        return outcome.payload(), 0

    if name == "read":
        return service.run_read(root, args.work, refresh=args.refresh), 0

    if name == "sync":
        outcome = service.run_sync(root, queries=list(args.query), refresh=args.refresh)
        return outcome.payload(), 0

    if name == "verify":
        mismatches = paths.verify_bytes(root, sample=args.sample)
        if mismatches:
            detail = "; ".join(m["path"] + " 期望 " + m["expected"] for m in mismatches)
            raise envelope.EnvelopeError(envelope.IO_ERROR, "sha256 不匹配：" + detail)
        return {"checked_sample": args.sample, "mismatches": []}, 0

    if name == "profile.show":
        profile, errors = config.load_profile(root)
        if profile is None:
            message = errors[0].message if errors else "没有 profile：" + str(config.config_dir(root) / config.PROFILE)
            raise envelope.EnvelopeError(
                envelope.PROFILE_INVALID if errors else envelope.NO_PROFILE, message
            )
        return {"queries": [q.as_dict() for q in profile.queries]}, 0

    if name == "profile.check":
        result = config.check_profile(root)
        error = result.get("error")
        if error and error.get("code") == envelope.NO_PROFILE:
            raise envelope.EnvelopeError(envelope.NO_PROFILE, error["message"])
        if not result["ok"]:
            messages = []
            for item in result["errors"]:
                prefix = ("第 " + str(item["line"]) + " 行：") if item.get("line") else ""
                messages.append(prefix + item["message"])
            raise envelope.EnvelopeError(envelope.PROFILE_INVALID, "; ".join(messages))
        return {"queries": result["queries"]}, 0

    if name == "profile.scaffold":
        return {"template": config.scaffold_profile()}, 0

    if name == "version":
        return {"version": __version__, "schema": envelope.SCHEMA_VERSION}, 0

    raise envelope.EnvelopeError(envelope.INVALID_ARGS, "未知命令：" + name)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    name = args.command if args.command != "profile" else "profile." + args.subcommand
    try:
        data, code = _dispatch(args, name)
    except envelope.EnvelopeError as exc:
        envelope.dump(envelope.failure(name, exc.code, exc.message))
        return 1
    except KeyboardInterrupt:
        envelope.dump(envelope.failure(name, envelope.IO_ERROR, "被中断"))
        return 1
    except Exception as exc:  # noqa: BLE001 - 兜底转 internal_error
        envelope.dump(envelope.failure(name, envelope.INTERNAL_ERROR, str(exc)))
        return 1
    envelope.dump(envelope.success(name, data))
    return code


if __name__ == "__main__":
    sys.exit(main())
