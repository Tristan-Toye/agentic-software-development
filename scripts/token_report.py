#!/usr/bin/env python3
"""Measure where an opencode setup spends its tokens, for one time window.

Every claim in references/token-spend-ledger.md is checked with this script: it
reads the opencode store (read-only) and the plugins' own logs, and prints the
metrics each ledger hypothesis names. Run it for the window before a change and
the window after, and compare — `--json` writes the numbers, `--compare` prints
the difference against an earlier `--json` file.

# Usage

    token_report.py --since 2026-10-05 [--until 2026-10-12] [--json out.json] [--compare before.json]
    token_report.py --selftest

`--since` / `--until` take an ISO date or datetime (local time) or epoch
milliseconds. The defaults read ~/.local/share/opencode/opencode.db and the
plugin state under ~/.local/share/opencode/ (override with --db / --data-dir).

# What it reports

- **Totals**: model requests (steps), credits, credits per day.
- **Per agent**: sessions, steps, credits, share, average and peak context, and
  the context of each session's first step (the fixed prompt baseline).
- **Per orchestrator run** (top-level `work-on` sessions): steps, credits,
  credits per step, average and peak context, phase checkpoints, mask moves.
- **Carried context of the orchestrator**: which kinds of parts (reasoning,
  read output, bash output, tool inputs, …) the requests carried, by share.
- **Tool adoption and bypasses**: calls of each mechanical tool and of each
  known wasteful shape (whole-file reads over 20 KB, `sed`/`cat` reads, raw
  `limactl … cargo test`, dossier reads, grep chains).
- **Plugin events**: phase-checkpoint and context-guard log lines in the window.

Credits use the Z.ai coding-plan multipliers (per 10,000 tokens): glm-5.3 input
6.9, cached input 1.7, output 24; glm-5.3-flash 2.3 / 0.56 / 8. Reasoning counts
as output. Unknown models are counted with the glm-5.3 rates and flagged.

Exit codes: 0 report printed; 2 unusable input (no store, bad window).

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections import defaultdict
from datetime import datetime

RATES = {"glm-5.3": (6.9, 1.7, 24.0), "glm-5.3-flash": (2.3, 0.56, 8.0)}
MECHANICAL = (
    "dossier_edit", "prepare_wave", "compose_payloads", "run_tests", "wait_ci", "git_state",
    "run_gates", "gate_commit", "verify_return", "code_nav", "validate_pipeline",
)
CODE_TOOLS = ("code_outline", "code_item", "code_find", "doc_section", "code_replace", "phase_checkpoint")
BIG_READ = 20000


def to_ms(text: str | None) -> int | None:
    if text is None:
        return None
    if text.isdigit():
        return int(text)
    return int(datetime.fromisoformat(text).timestamp() * 1000)


def credits(model: str, inp: int, cached: int, out: int) -> float:
    a, b, c = RATES.get(model, RATES["glm-5.3"])
    return (inp * a + cached * b + out * c) / 10000


def open_db(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def steps(con: sqlite3.Connection, since: int, until: int) -> list[dict]:
    rows = con.execute(
        """select p.session_id, p.message_id, p.time_created,
                  json_extract(m.data,'$.agent'), json_extract(m.data,'$.modelID'),
                  coalesce(json_extract(p.data,'$.tokens.input'),0),
                  coalesce(json_extract(p.data,'$.tokens.cache.read'),0),
                  coalesce(json_extract(p.data,'$.tokens.output'),0) + coalesce(json_extract(p.data,'$.tokens.reasoning'),0)
           from part p join message m on m.id = p.message_id
           where json_extract(p.data,'$.type')='step-finish' and p.time_created >= ? and p.time_created < ?
           order by p.time_created""", (since, until)).fetchall()
    return [dict(sid=r[0], mid=r[1], t=r[2], agent=r[3] or "?", model=r[4] or "?", inp=r[5], cached=r[6], out=r[7],
                 ctx=r[5] + r[6], cr=credits(r[4] or "?", r[5], r[6], r[7])) for r in rows]


def report(con: sqlite3.Connection, since: int, until: int, data_dir: str) -> dict:
    st = steps(con, since, until)
    out: dict = {"window": [since, until], "unknown_models": sorted({s["model"] for s in st if s["model"] not in RATES})}
    total = sum(s["cr"] for s in st) or 1.0
    out["totals"] = {"steps": len(st), "credits": round(total)}
    days: dict[str, float] = defaultdict(float)
    for s in st:
        days[datetime.fromtimestamp(s["t"] / 1000).strftime("%Y-%m-%d")] += s["cr"]
    out["per_day"] = {d: round(v) for d, v in sorted(days.items())}

    by_agent: dict[str, dict] = {}
    first: dict[str, int] = {}
    for s in st:
        a = by_agent.setdefault(s["agent"], {"sessions": set(), "steps": 0, "credits": 0.0, "ctx_sum": 0, "ctx_max": 0, "first": []})
        a["sessions"].add(s["sid"]); a["steps"] += 1; a["credits"] += s["cr"]
        a["ctx_sum"] += s["ctx"]; a["ctx_max"] = max(a["ctx_max"], s["ctx"])
        if s["sid"] not in first:
            first[s["sid"]] = s["ctx"]; a["first"].append(s["ctx"])
    out["agents"] = {
        k: {"sessions": len(v["sessions"]), "steps": v["steps"], "credits": round(v["credits"]),
            "share_pct": round(100 * v["credits"] / total, 1), "avg_ctx_k": round(v["ctx_sum"] / v["steps"] / 1000),
            "max_ctx_k": round(v["ctx_max"] / 1000),
            "first_step_ctx_k": round(sum(v["first"]) / len(v["first"]) / 1000, 1) if v["first"] else None}
        for k, v in sorted(by_agent.items(), key=lambda kv: -kv[1]["credits"])}

    top = {r[0]: r[1] for r in con.execute("select id, title from session where parent_id is null")}
    runs: dict[str, dict] = {}
    for s in st:
        if s["agent"] != "work-on" or s["sid"] not in top:
            continue
        r = runs.setdefault(s["sid"], {"title": top[s["sid"]], "steps": 0, "credits": 0.0, "ctx_sum": 0, "ctx_max": 0})
        r["steps"] += 1; r["credits"] += s["cr"]; r["ctx_sum"] += s["ctx"]; r["ctx_max"] = max(r["ctx_max"], s["ctx"])
    cp_dir = os.path.join(data_dir, "phase-checkpoint")
    guard_dir = os.path.join(data_dir, "context-guard")
    for sid, r in runs.items():
        cps = []
        p = os.path.join(cp_dir, f"{sid}.json")
        if os.path.exists(p):
            cps = json.load(open(p))
        masks = 0
        gp = os.path.join(guard_dir, "context-guard.log")
        if os.path.exists(gp):
            masks = sum(1 for line in open(gp) if f" mask {sid} " in line)
        r.update(credits=round(r["credits"]), credits_per_step=round(r["credits"] / r["steps"], 1),
                 avg_ctx_k=round(r.pop("ctx_sum") / r["steps"] / 1000), max_ctx_k=round(r.pop("ctx_max") / 1000),
                 checkpoints=len(cps), mask_moves=masks)
    out["runs"] = runs

    run_ids = list(runs)
    out["carried"] = carried(con, run_ids, cp_dir) if run_ids else {}
    out["tools"] = tool_use(con, since, until, run_ids)
    out["plugin_events"] = plugin_events(data_dir, since, until)
    return out


def carried(con: sqlite3.Connection, run_ids: list[str], cp_dir: str) -> dict:
    """Share of the orchestrator's carried context by part kind (chars x later steps in the phase)."""
    import bisect
    acc: dict[str, float] = defaultdict(float)
    for sid in run_ids:
        p = os.path.join(cp_dir, f"{sid}.json")
        cuts = sorted(to_ms(cp["at"].replace("Z", "+00:00")) for cp in json.load(open(p))) if os.path.exists(p) else []
        parts = con.execute(
            """select time_created, json_extract(data,'$.type'), json_extract(data,'$.tool'),
                      length(coalesce(json_extract(data,'$.text'),'')),
                      length(coalesce(json_extract(data,'$.state.input'),'')),
                      length(coalesce(json_extract(data,'$.state.output'),''))
               from part where session_id=? order by time_created""", (sid,)).fetchall()
        st = [t for t, typ, *_ in parts if typ == "step-finish"]
        bounds = cuts + [10**15]
        for t, typ, tool, tl, il, ol in parts:
            end = next(b for b in bounds if b > t)
            n = bisect.bisect_left(st, end) - bisect.bisect_right(st, t)
            if typ == "reasoning":
                acc["reasoning"] += tl * n
            elif typ == "text":
                acc["text"] += tl * n
            elif typ == "tool":
                name = tool if tool in ("read", "bash", "task") else "other"
                acc[f"{name} input"] += il * n
                acc[f"{name} output"] += ol * n
    tot = sum(acc.values()) or 1.0
    return {k: round(100 * v / tot, 1) for k, v in sorted(acc.items(), key=lambda kv: -kv[1])}


def tool_use(con: sqlite3.Connection, since: int, until: int, run_ids: list[str]) -> dict:
    if not run_ids:
        return {}
    q = ",".join("?" * len(run_ids))
    rows = con.execute(f"""
        select json_extract(data,'$.tool'), coalesce(json_extract(data,'$.state.input.command'),''),
               coalesce(json_extract(data,'$.state.input.filePath'),''), json_extract(data,'$.state.input.limit'),
               length(coalesce(json_extract(data,'$.state.output'),'')), json_extract(data,'$.state.status')
        from part where session_id in ({q}) and json_extract(data,'$.type')='tool'
          and time_created >= ? and time_created < ?""", (*run_ids, since, until)).fetchall()
    c: dict[str, int] = defaultdict(int)
    for tool, cmd, fp, lim, olen, status in rows:
        c["calls"] += 1
        if tool in CODE_TOOLS:
            c[f"tool:{tool}"] += 1
        if status == "error":
            c["errors"] += 1
        if tool == "bash":
            for name in MECHANICAL:
                if f"{name}.py" in cmd:
                    c[f"script:{name}"] += 1
            if re.search(r"(^|[;&|]\s*)(rtk\s+)?(sed|cat|head|tail|nl)\b", cmd):
                c["bypass:sed/cat/head/tail reads"] += 1
            if "limactl" in cmd and re.search(r"cargo (test|nextest)", cmd) and "run_tests.py" not in cmd:
                c["bypass:raw limactl cargo test"] += 1
            if re.search(r"\b(grep|rg)\b", cmd):
                c["bash grep/rg"] += 1
        if tool == "read":
            c["read calls"] += 1
            if lim is None and olen > BIG_READ:
                c["bypass:whole-file read > 20 KB"] += 1
            if lim is None and "/.discovery/dossiers/" in fp:
                c["bypass:whole dossier read"] += 1
        if tool == "grep":
            c["grep tool"] += 1
    return dict(sorted(c.items()))


def plugin_events(data_dir: str, since: int, until: int) -> dict:
    ev: dict[str, int] = defaultdict(int)
    for name in ("phase-checkpoint/phase-checkpoint.log", "context-guard/context-guard.log"):
        p = os.path.join(data_dir, name)
        if not os.path.exists(p):
            continue
        tag = name.split("/")[0]
        for line in open(p):
            parts = line.split(" ", 2)
            if len(parts) < 2:
                continue
            try:
                t = int(datetime.fromisoformat(parts[0].replace("Z", "+00:00")).timestamp() * 1000)
            except ValueError:
                continue
            if since <= t < until:
                ev[f"{tag}:{parts[1]}"] += 1
    return dict(sorted(ev.items()))


def render(r: dict) -> str:
    L = [f"window {datetime.fromtimestamp(r['window'][0]/1000):%Y-%m-%d %H:%M} .. "
         f"{datetime.fromtimestamp(min(r['window'][1], 4102444800000)/1000):%Y-%m-%d %H:%M}",
         f"steps {r['totals']['steps']}, credits {r['totals']['credits']:,}  per day {r['per_day']}"]
    if r["unknown_models"]:
        L.append(f"unknown models priced as glm-5.3: {r['unknown_models']}")
    L.append("\nagent                     sess  steps  credits  share  avg ctx  max ctx  first-step ctx")
    for k, v in r["agents"].items():
        L.append(f"{k:<25} {v['sessions']:>4} {v['steps']:>6} {v['credits']:>8,} {v['share_pct']:>5}% "
                 f"{v['avg_ctx_k']:>6}k {v['max_ctx_k']:>7}k {v['first_step_ctx_k']:>9}k")
    L.append("\norchestrator runs: title, steps, credits, credits/step, avg ctx, max ctx, checkpoints, mask moves")
    for v in r["runs"].values():
        L.append(f"  {v['title'][:30]:<30} {v['steps']:>5} {v['credits']:>7,} {v['credits_per_step']:>6} "
                 f"{v['avg_ctx_k']:>5}k {v['max_ctx_k']:>5}k {v['checkpoints']:>3} {v['mask_moves']:>3}")
    if r["carried"]:
        L.append("\norchestrator carried context by kind: " + ", ".join(f"{k} {v}%" for k, v in r["carried"].items()))
    if r["tools"]:
        L.append("\ntools and bypasses (orchestrator): " + ", ".join(f"{k} {v}" for k, v in r["tools"].items()))
    if r["plugin_events"]:
        L.append("\nplugin events: " + ", ".join(f"{k} {v}" for k, v in r["plugin_events"].items()))
    return "\n".join(L)


MIN_RUN_STEPS = 50  # shorter top-level sessions are false starts, not runs


def compare(before: dict, after: dict) -> str:
    def per(r: dict, key: str) -> float:
        runs = [x for x in r.get("runs", {}).values() if x["steps"] >= MIN_RUN_STEPS]
        return sum(x[key] for x in runs) / len(runs) if runs else 0.0

    rows = [
        ("credits per orchestrator run", per(before, "credits"), per(after, "credits")),
        ("steps per orchestrator run", per(before, "steps"), per(after, "steps")),
        ("credits per orchestrator step", per(before, "credits_per_step"), per(after, "credits_per_step")),
        ("orchestrator avg context (k)", per(before, "avg_ctx_k"), per(after, "avg_ctx_k")),
        ("orchestrator max context (k)", per(before, "max_ctx_k"), per(after, "max_ctx_k")),
    ]
    out = ["metric                              before      after    change"]
    for name, b, a in rows:
        ch = f"{100 * (a - b) / b:+.0f}%" if b else "n/a"
        out.append(f"{name:<34} {b:>9.1f} {a:>10.1f} {ch:>9}")
    return "\n".join(out)


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "o.db")
        con = sqlite3.connect(db)
        con.execute("create table session(id text, parent_id text, title text)")
        con.execute("create table message(id text, session_id text, time_created int, data text)")
        con.execute("create table part(id text, message_id text, session_id text, time_created int, data text)")
        con.execute("insert into session values ('s1', null, 'W-001')")
        con.execute("insert into session values ('s2', 's1', 'impl')")
        n = 0
        for i in range(4):
            n += 1
            con.execute("insert into message values (?, 's1', ?, ?)",
                        (f"m{i}", 1000 + i * 10, json.dumps({"agent": "work-on", "modelID": "glm-5.3"})))
            con.execute("insert into part values (?, ?, 's1', ?, ?)",
                        (f"p{n}", f"m{i}", 1001 + i * 10, json.dumps({"type": "reasoning", "text": "x" * 400})))
            n += 1
            con.execute("insert into part values (?, ?, 's1', ?, ?)",
                        (f"p{n}", f"m{i}", 1002 + i * 10, json.dumps({"type": "tool", "tool": "read",
                         "state": {"input": {"filePath": "/r/.discovery/dossiers/W-001-a.md"}, "output": "y" * 30000, "status": "completed"}})))
            n += 1
            con.execute("insert into part values (?, ?, 's1', ?, ?)",
                        (f"p{n}", f"m{i}", 1003 + i * 10, json.dumps({"type": "tool", "tool": "bash",
                         "state": {"input": {"command": "python3 scripts/run_tests.py -- cargo test"}, "output": "ok", "status": "completed"}})))
            n += 1
            con.execute("insert into part values (?, ?, 's1', ?, ?)",
                        (f"p{n}", f"m{i}", 1004 + i * 10, json.dumps({"type": "step-finish",
                         "tokens": {"input": 1000, "output": 100, "reasoning": 50, "cache": {"read": 10000 * (i + 1)}}})))
        con.execute("insert into message values ('mi', 's2', 1100, ?)", (json.dumps({"agent": "implementer", "modelID": "glm-5.3-flash"}),))
        con.execute("insert into part values ('pi', 'mi', 's2', 1101, ?)",
                    (json.dumps({"type": "step-finish", "tokens": {"input": 500, "output": 10, "reasoning": 0, "cache": {"read": 2000}}}),))
        con.commit()
        con.close()
        data = os.path.join(tmp, "data")
        os.makedirs(os.path.join(data, "phase-checkpoint"))
        os.makedirs(os.path.join(data, "context-guard"))
        with open(os.path.join(data, "context-guard", "context-guard.log"), "w") as fh:
            fh.write("1970-01-01T00:00:01.020Z mask s1 watermark m2: ~40k -> ~12k tokens, 2 outputs, 2 reasoning\n")
        r = report(open_db(db), 0, 10**13, data)
        check("4 + 1 steps", r["totals"]["steps"] == 5)
        want = round(sum(credits("glm-5.3", 1000, 10000 * (i + 1), 150) for i in range(4)) + credits("glm-5.3-flash", 500, 2000, 10))
        check(f"credits {r['totals']['credits']} == {want}", r["totals"]["credits"] == want)
        check("first-step ctx of work-on", r["agents"]["work-on"]["first_step_ctx_k"] == 11.0)
        run = r["runs"]["s1"]
        check("run steps and max ctx", run["steps"] == 4 and run["max_ctx_k"] == 41)
        check("mask moves read from the guard log", run["mask_moves"] == 1)
        check("whole dossier read counted", r["tools"].get("bypass:whole dossier read") == 4)
        check("run_tests adoption counted", r["tools"].get("script:run_tests") == 4)
        check("carried shares sum to ~100", 99 <= sum(r["carried"].values()) <= 101)
        check("plugin event counted", r["plugin_events"].get("context-guard:mask") == 1)
        check("compare renders", "credits per orchestrator run" in compare(r, r))
        check("render renders", "orchestrator runs" in render(r))
    for f in failures:
        print("SELFTEST FAIL  " + f)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--db", default=os.path.expanduser("~/.local/share/opencode/opencode.db"))
    ap.add_argument("--data-dir", default=os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "opencode"))
    ap.add_argument("--json", help="write the metrics to this file")
    ap.add_argument("--compare", help="an earlier --json file to compare against")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    try:
        since = to_ms(args.since) or 0
        until = to_ms(args.until) or 10**13
        con = open_db(args.db)
    except (ValueError, FileNotFoundError) as exc:
        print(f"token_report: {exc}", file=sys.stderr)
        return 2
    r = report(con, since, until, args.data_dir)
    print(render(r))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(r, fh, indent=1, default=list)
    if args.compare:
        print("\n" + compare(json.load(open(args.compare)), r))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
