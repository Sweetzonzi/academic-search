"""M1.5 spike / step 8: collect the final numbers quoted in the experiment record."""
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))

TITLES = {
    "2610.17916v1": "Safe Unicycle Navigation Among Multiple Obstacles Under Bounded Disturbances",
    "2610.17929v1": "Enforcing a Strict Safety Margin in LLM Text Generation via Barrier Lyapunov Control",
    "2610.18290v1": "On Over-Conservatism Under Low-Probability Extreme Events (KL-Regularized Wasserstein)",
    "2610.18311v1": "BALLAST: Physics-Aware Moving Target Defense for Industrial Control Systems",
    "2610.18361v1": "Adaptive Handling of Deadline Misses via Stochastic MPC",
    "2610.18630v1": "Dynamic Programming for A Linear-Gaussian Dual Control Problem",
    "2610.18675v1": "Optimal Decay Matrix Control Barrier Functions",
    "2610.18758v1": "Compositional Sensitivity Analysis for Multiobjective System Co-Design",
}


def metrics(md_path: str) -> dict:
    t = open(md_path, encoding="utf-8").read()
    chars = len(t)
    return {
        "bytes": os.path.getsize(md_path),
        "chars": chars,
        "ktok_est": round(chars / 4 / 1000, 1),      # ~4 chars/token, rough
        "eqs": len(re.findall(r"^<!-- eq: \d+ -->$", t, re.M)),
        "figs": len(re.findall(r"^<!-- fig: \d+ -->$", t, re.M)),
        "secs": len(re.findall(r"^<!-- sec: ", t, re.M)),
        "tbls": len(re.findall(r"^<!-- tbl: \d+ -->$", t, re.M)),
        "imgs": len(re.findall(r"^!\[", t, re.M)),
        "xlinks": len(re.findall(r"\]\(#", t)),
    }


def main() -> None:
    for route, root in (("latex", "textified"), ("html", "textified-html")):
        print(f"\n########## route = {route} ##########")
        print(f"{'id':15s} {'KB':>7s} {'chars':>8s} {'kTok':>6s} {'sec':>4s} "
              f"{'eq':>4s} {'fig':>4s} {'tbl':>4s} {'xlink':>6s}")
        tot = {"bytes": 0, "figs": 0, "eqs": 0}
        for tag in sorted(TITLES):
            p = os.path.join(BASE, root, tag, "paper.md")
            if not os.path.exists(p):
                continue
            m = metrics(p)
            tot["bytes"] += m["bytes"]
            tot["figs"] += m["figs"]
            tot["eqs"] += m["eqs"]
            print(f"{tag:15s} {m['bytes']/1024:7.1f} {m['chars']:8d} "
                  f"{m['ktok_est']:6.1f} {m['secs']:4d} {m['eqs']:4d} {m['figs']:4d} "
                  f"{m['tbls']:4d} {m['xlinks']:6d}")
        print(f"{'TOTAL':15s} {tot['bytes']/1024:7.1f} {'':8s} {'':6s} {'':4s} "
              f"{tot['eqs']:4d} {tot['figs']:4d}")
        # figure assets
        n = svg = png = 0
        size = 0
        for tag in TITLES:
            fd = os.path.join(BASE, root, tag, "figures")
            if not os.path.isdir(fd):
                continue
            for f in os.listdir(fd):
                fp = os.path.join(fd, f)
                n += 1
                size += os.path.getsize(fp)
                svg += f.endswith(".svg")
                png += f.endswith(".png")
        print(f"figure assets: {n} files ({png} png, {svg} svg), {size/1024/1024:.1f} MB")

    # per-route meta roll-up
    print("\n########## per-paper meta summary ##########")
    for tag in sorted(TITLES):
        row = {"id": tag, "title": TITLES[tag][:58]}
        for route, root in (("latex", "textified"), ("html", "textified-html")):
            mp = os.path.join(BASE, root, tag, "meta.json")
            if os.path.exists(mp):
                m = json.load(open(mp, encoding="utf-8"))
                row[route + "_s"] = m.get("sec_convert")
                row[route + "_warn"] = m.get("pandoc_warnings", 0)
                row[route + "_unres"] = m.get("refs", {}).get("unresolved")
                row[route + "_res"] = m.get("refs", {}).get("resolved")
                if route == "latex":
                    row["thin"] = m["figure"].get("render_failed")
                    row["rawenv"] = m["math"]["kept_raw_env"]
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
