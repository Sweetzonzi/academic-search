"""安装副本的初始化验收；--live 另发一次 OpenAlex 主题请求，全部写入独立空库。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    key = os.environ.get("OPENALEX_API_KEY", "")
    if args.live and not key:
        parser.error("当前进程缺少 OPENALEX_API_KEY；未执行联网验证")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = Path(__file__).parent / "out" / "initialization-acceptance" / stamp
    repo = out / "isolated repo"
    bundle = out / "installed skill"
    shutil.copytree(ROOT / "src", repo / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "skills/academic-search", bundle,
                    ignore=shutil.ignore_patterns("root.txt", "__pycache__"))
    (bundle / "root.txt").write_text(str(repo) + "\n", encoding="utf-8")
    env = dict(os.environ, ACADEMIC_SEARCH_PYTHON=sys.executable,
               PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    env.pop("ACADEMIC_SEARCH_ROOT", None)
    env.pop("PYTHONPATH", None)
    checks = []
    for label, options, live in [
        ("diagnose", ["--diagnose"], False),
        ("local", ["search", "--refresh", "false", "--limit", "1"], False),
        *([("openalex", ["search", "--source", "openalex", "--all-of", "world model",
                         "--refresh", "true", "--limit", "1"], True)] if args.live else []),
    ]:
        current = dict(env)
        if live:
            current["OPENALEX_API_KEY"] = key
        else:
            current.pop("OPENALEX_API_KEY", None)
        if os.name == "nt":
            comspec = subprocess.list2cmdline([os.environ.get("COMSPEC", "cmd.exe")])
            invocation = subprocess.list2cmdline([str(bundle / "scripts/run.cmd"), *options])
            command = f'{comspec} /d /s /c "{invocation}"'
        else:
            command = ["/bin/sh", str(bundle / "scripts/run.sh"), *options]
        result = subprocess.run(command, cwd=out, env=current, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=90)
        stdout = result.stdout.replace(key, "REDACTED") if key else result.stdout
        stderr = result.stderr.replace(key, "REDACTED") if key else result.stderr
        (out / (label + ".stdout.txt")).write_text(stdout, encoding="utf-8")
        (out / (label + ".stderr.txt")).write_text(stderr, encoding="utf-8")
        check = {"probe": label, "exit_code": result.returncode, "passed": result.returncode == 0}
        if label == "diagnose":
            check["passed"] &= "stage=launcher_ready" in stdout and "openalex_key=missing" in stdout
            check["passed"] &= not (repo / "data").exists()
        else:
            payload = json.loads(stdout)
            data = payload.get("data", {})
            check.update(network=data.get("network"), cost=data.get("cost"),
                         failures=data.get("counts", {}).get("failures", []), error=payload.get("error"))
            check["passed"] &= payload["ok"] and data.get("network") is live
            if live:
                check["passed"] &= data.get("cost", {}).get("requests") == 1
                check["passed"] &= data.get("counts", {}).get("complete") is True and not check["failures"]
                check["passed"] &= not data.get("degraded")
        checks.append(check)
        print(json.dumps(check, ensure_ascii=False), flush=True)
    summary = {"at_utc": stamp, "live": args.live, "checks": checks}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("artifacts=" + str(out), flush=True)
    return 0 if all(check["passed"] for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
