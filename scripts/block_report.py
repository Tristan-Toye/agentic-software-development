#!/usr/bin/env python3
"""Every call auto mode refused, grouped by command shape: which allow rule would pay for itself.

The PermissionDenied hook logs each refusal to `~/.agentic/blocks.jsonl`
(`ASD_BLOCK_LOG`), in runs and out of them, and the dashboard logs each ruling
on one there too (`block-ruling`: once, always or deny, with the rule). Older
refusals live only in the per-run logs (`~/.agentic/runs/*.jsonl`, `blocked`
events); `--runs` folds those in as well.

A shape is the command with its `cd …&&` prefix and leading `VAR=…`
assignments dropped, cut to its first words (paths reduced to their last
part), so `gh pr view 12` and `gh pr view 40 --json x` count as one row. A
row lists how often, when last, how it was ruled, and one example.

# Usage

    block_report.py [--since YYYY-MM-DD] [--runs] [--json] [--words N]
    block_report.py --selftest
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import tempfile
from collections import Counter

HOME = os.path.expanduser("~")


def log_file() -> str:
    return os.environ.get("ASD_BLOCK_LOG") or os.path.join(HOME, ".agentic", "blocks.jsonl")


def read_jsonl(path: str) -> list[dict]:
    out = []
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if isinstance(o, dict):
                    out.append(o)
    except OSError:
        pass
    return out


def shape(tool: str, target: str, words: int = 3) -> str:
    """The command shape a refusal counts under."""
    t = (target or "").strip()
    if tool != "Bash":
        if t.startswith("{"):
            try:
                o = json.loads(t)
                code = o.get("code") or o.get("command") if isinstance(o, dict) else None
                if isinstance(code, str):
                    return f"{tool.rsplit('__', 1)[-1]} > " + shape("Bash", code, words).removeprefix("Bash ")
            except ValueError:
                pass
            return tool.rsplit("__", 1)[-1]
        return f"{tool} {os.path.dirname(t) or t}".strip()
    # the first subcommand that is not a cd, a bare assignment or an echo is the one that counts
    first = ""
    for sub in re.split(r"\s*(?:&&|\|\||;|\n)\s*", t.split("<<", 1)[0]):
        sub = re.sub(r"^(\s*[A-Za-z_][A-Za-z0-9_]*=\S*\s*)+", "", sub).strip()
        sub = re.sub(r"^(rtk|command|env)\s+", "", sub)
        if sub and not re.match(r"(cd|echo|export|set)\b", sub):
            first = sub
            break
    parts: list[str] = []
    for w in first.split():
        if w in ("|", "|&") or (w.startswith("-") and parts) or w[:1] in "\"'$(":
            break
        if w.startswith(">"):
            parts.append(">")
            break
        parts.append(os.path.basename(w.rstrip("/")) if "/" in w else w)
        if len(parts) >= words:
            break
    return "Bash " + (" ".join(parts) or t[:40])


def collect(since: str | None, runs: bool) -> list[dict]:
    events = read_jsonl(log_file())
    if runs:
        seen = {e.get("tool_use_id") for e in events if e.get("tool_use_id")}
        for p in glob.glob(os.path.join(os.environ.get("ASD_RUNS_DIR") or os.path.join(HOME, ".agentic", "runs"), "*.jsonl")):
            run = os.path.basename(p)[:-6]
            for e in read_jsonl(p):
                if e.get("event") not in ("blocked", "block-ruling") or e.get("tool_use_id") in seen:
                    continue  # the central log already holds it
                e.setdefault("run", run)
                events.append(e)
    if since:
        events = [e for e in events if str(e.get("ts") or "") >= since]
    return events


def report(events: list[dict], words: int) -> list[dict]:
    blocked = [e for e in events if e.get("event") == "blocked"]
    rulings = {e.get("tool_use_id"): e for e in events if e.get("event") == "block-ruling"}
    rows: dict[str, dict] = {}
    for e in blocked:
        k = shape(str(e.get("tool") or ""), str(e.get("target") or ""), words)
        r = rows.setdefault(k, {"shape": k, "count": 0, "last": "", "runs": set(), "rulings": Counter(), "example": "", "reason": ""})
        r["count"] += 1
        if str(e.get("ts") or "") >= r["last"]:
            r["last"] = str(e.get("ts") or "")
            r["example"] = " ".join(str(e.get("target") or "").split())[:200]
            r["reason"] = str(e.get("reason") or "")[:200]
        if e.get("run"):
            r["runs"].add(str(e["run"]))
        ru = rulings.get(e.get("tool_use_id"))
        r["rulings"][ru.get("scope") or ru.get("action") or "?" if ru else "unruled"] += 1
    out = sorted(rows.values(), key=lambda r: (-r["count"], r["shape"]))
    for r in out:
        r["runs"] = sorted(r["runs"])
        r["rulings"] = dict(r["rulings"])
    return out


def selftest() -> int:
    fails = 0

    def check(name: str, ok: bool) -> None:
        nonlocal fails
        fails += not ok
        print(("ok   " if ok else "FAIL ") + name)

    check("shape drops the cd prefix and assignments", shape("Bash", "cd /x/y && FOO=1 gh pr view 12 --json a") == "Bash gh pr view")
    check("shape reduces paths to their last part", shape("Bash", "python3 /a/b/scripts/remove_worktree.py --root r p") == "Bash python3 remove_worktree.py")
    check("shape of a file tool is its directory", shape("Write", "/r/src/a.py") == "Write /r/src")
    check("shape skips X=…; and cd; prefixes, rtk, and heredoc targets",
          shape("Bash", "P=/a/b; X=/c; cd $X; limactl shell nightwatch -- bash") == "Bash limactl shell nightwatch"
          and shape("Bash", "rtk gh pr merge 465 --merge") == "Bash gh pr merge"
          and shape("Bash", "cat > /tmp/x.md <<'EOF'\nbody\nEOF") == "Bash cat >")
    check("shape of an MCP call with code is the code's shape",
          shape("mcp__p__ctx_execute", json.dumps({"code": "cd /r; git worktree remove --force ../x"})) == "ctx_execute > git worktree remove")
    with tempfile.TemporaryDirectory() as t:
        log = os.path.join(t, "blocks.jsonl")
        runs = os.path.join(t, "runs")
        os.makedirs(runs)
        ev = [
            {"ts": "2026-11-08T10:00:00Z", "event": "blocked", "run": "r.W-1", "tool": "Bash", "target": "gh pr view 1", "tool_use_id": "a"},
            {"ts": "2026-11-09T10:00:00Z", "event": "blocked", "run": "r.W-2", "tool": "Bash", "target": "gh pr view 2 --json x", "tool_use_id": "b"},
            {"ts": "2026-11-09T10:01:00Z", "event": "block-ruling", "run": "r.W-2", "tool_use_id": "b", "scope": "always", "rule": "Bash(gh pr view:*)"},
            {"ts": "2026-11-09T11:00:00Z", "event": "blocked", "tool": "Bash", "target": "rm -rf build", "tool_use_id": "c"},
        ]
        with open(log, "w") as fh:
            fh.write("".join(json.dumps(e) + "\n" for e in ev))
        with open(os.path.join(runs, "r.W-3.jsonl"), "w") as fh:
            fh.write(json.dumps({"ts": "2026-11-01T00:00:00Z", "event": "blocked", "tool": "Bash", "target": "gh pr view 9", "tool_use_id": "old"}) + "\n")
            fh.write(json.dumps({"ts": "2026-11-08T10:00:00Z", "event": "blocked", "tool": "Bash", "target": "gh pr view 1", "tool_use_id": "a"}) + "\n")
        os.environ["ASD_BLOCK_LOG"], os.environ["ASD_RUNS_DIR"] = log, runs
        rows = report(collect(None, False), 3)
        top = rows[0]
        check("refusals of one shape are one row, most frequent first", top["shape"] == "Bash gh pr view" and top["count"] == 2)
        check("a row carries its rulings and runs", top["rulings"] == {"unruled": 1, "always": 1} and top["runs"] == ["r.W-1", "r.W-2"])
        check("--since drops older refusals", [r["count"] for r in report(collect("2026-11-09", False), 3) if r["shape"] == "Bash gh pr view"] == [1])
        rows = report(collect(None, True), 3)
        check("--runs folds in per-run refusals once each", next(r for r in rows if r["shape"] == "Bash gh pr view")["count"] == 3)
    print("all ok" if not fails else f"{fails} failed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--since", help="only refusals on or after this date (YYYY-MM-DD)")
    ap.add_argument("--runs", action="store_true", help="also read the per-run logs' blocked events")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--words", type=int, default=3, help="words a command shape keeps (default 3)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    rows = report(collect(a.since, a.runs), a.words)
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0
    if not rows:
        print("no refusals logged")
        return 0
    for r in rows:
        rul = ", ".join(f"{k} {v}" for k, v in sorted(r["rulings"].items()))
        print(f"{r['count']:>4}  {r['shape']}  (last {r['last'][:16]}; {rul}; {len(r['runs'])} run(s))")
        print(f"      e.g. {r['example']}")
        if r["reason"]:
            print(f"      why: {r['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
