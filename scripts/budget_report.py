#!/usr/bin/env python3
"""What one /work-on dossier costs on each host, as a share of each subscription's week.

token_report.py explains WHERE an opencode run spends; this script answers HOW MUCH of a
plan one dossier takes, on opencode (GLM coding plan) and on Claude Code (a claude.ai
seat), side by side, so a plan change or a token-saving change can be compared across
hosts. Plan prices and quotas live in ledger/plans.json — the one place to edit them.

# Usage

    budget_report.py [--since 2026-09-01] [--until 2026-10-14] [--json out.json] [--compare before.json]
    budget_report.py --selftest

# What it reports, per dossier run (a W-<n> id)

- **opencode**: the root session titled `W-<n>…` plus every descendant session. Credits at
  the standard rate, and `billed`: the off-peak half rate applied per request (Z.ai bills
  half outside Mon-Fri 06:00-10:00 UTC). Shares use `billed`.
- **claude**: every Claude Code session that ran `/work-on` for W-<n> (resumes included),
  plus its subagent transcripts, priced at API rates per model. The share of the seat's
  week uses the week's API-equivalent size: calibrated from ~/.claude/usage-log.jsonl (the
  status-line sampler logs the account's weekly %) when that log covers a rise of at least
  `MIN_CALIBRATION_PCT` points within one weekly window, else `week_usd` from plans.json.
- Both: requests, input and output tokens, cache-read share, average request size.

The calibration prices every Claude Code request on this machine between the first and
last logged reading of a window; use on claude.ai or another machine counts toward the
account's % but not toward that price, so the calibrated week is a lower bound and each
share an upper bound.

Exit codes: 0 report printed; 2 unusable input.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import statistics
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PLANS = os.path.join(os.path.dirname(HERE), "ledger", "plans.json")
DB = os.path.expanduser("~/.local/share/opencode/opencode.db")
PROJECTS = os.path.expanduser("~/.claude/projects")
USAGE_LOG = os.path.expanduser("~/.claude/usage-log.jsonl")
RUN_TITLE = re.compile(r"^(?:error\s+)?(w-\d+)", re.I)
WORK_ON = re.compile(r"agentic-software-development:work-on|<command-name>/work-on")
# The dossier id of a /work-on invocation: its command args, or the echoed `/…:work-on W-<n>`.
CLAUDE_RUN = re.compile(r"work-on</command-name>.{0,200}?<command-args>\s*(W-\d+)|work-on\s+(W-\d+)", re.S)
MIN_CALIBRATION_PCT = 5
MIN_RUN_COST = {"opencode": 500.0, "opencode-openai": 50.0, "claude": 2.0}  # below this a session is a probe, not a dossier
# A dossier build makes hundreds of requests; fewer means an inquiry or a run still in progress.
# Such runs are listed but left out of the medians.
MIN_FULL_REQUESTS = 300


def iso_ms(text: str | None, default: int) -> int:
    if not text:
        return default
    if text.isdigit():
        return int(text)
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def ts_ms(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def peak(ms: int, glm: dict) -> bool:
    t = datetime.fromtimestamp(ms / 1000, timezone.utc)
    p = glm["peak_utc"]
    return t.weekday() in p["weekdays"] and p["start_hour"] <= t.hour < p["end_hour"]


# ---------------------------------------------------------------- opencode (GLM) runs

def opencode_runs(db: str, since: int, until: int, glm: dict, codex: dict | None = None) -> list[dict]:
    if not os.path.exists(db):
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    sessions = {r[0]: (r[1], r[2]) for r in con.execute("select id, parent_id, title from session")}
    children: dict[str, list[str]] = defaultdict(list)
    for sid, (parent, _) in sessions.items():
        if parent:
            children[parent].append(sid)
    per = defaultdict(lambda: defaultdict(float))
    query = ("select p.session_id, m.data, p.data, m.time_created from part p join message m on m.id = p.message_id "
             "where json_extract(p.data, '$.type') = 'step-finish' and m.time_created between ? and ?")
    for sid, mdata, pdata, created in con.execute(query, (since, until)):
        model = json.loads(mdata).get("modelID", "?")
        tok = json.loads(pdata).get("tokens") or {}
        read = (tok.get("cache") or {}).get("read", 0) or 0
        fresh = tok.get("input", 0) or 0
        out = (tok.get("output", 0) or 0) + (tok.get("reasoning", 0) or 0)
        s = per[sid]
        codex_rate = (codex or {}).get(model)
        if codex_rate:  # an OpenAI model (opencode-openai): Codex credits per million tokens
            s["codex"] += (fresh * codex_rate["input"] + read * codex_rate["cache_read"] + out * codex_rate["output"]) / 1e6
        else:
            rate = glm["rates_per_10k_tokens"].get(model, glm["rates_per_10k_tokens"]["glm-5.3"])
            credits = (fresh * rate["input"] + read * rate["cache_read"] + out * rate["output"]) / 10000
            s["credits"] += credits
            s["billed"] += credits if peak(created, glm) else credits * glm["offpeak_factor"]
        s["requests"] += 1
        s["tok_in"] += fresh + read
        s["tok_read"] += read
        s["tok_out"] += out
        s["first"] = min(s["first"] or created, created)
        s["last"] = max(s["last"], created)
    by_run: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    titles: dict[str, list[str]] = defaultdict(list)
    for sid, (parent, title) in sessions.items():
        match = RUN_TITLE.match(title or "")
        if parent or not match:
            continue
        rid = match.group(1).upper()
        stack = [sid]
        while stack:
            cur = stack.pop()
            stack.extend(children[cur])
            for key, value in per[cur].items():
                agg = by_run[rid]
                if key == "first":
                    agg[key] = min(agg[key] or value, value) if value else agg[key]
                elif key == "last":
                    agg[key] = max(agg[key], value)
                else:
                    agg[key] += value
        by_run[rid]["main_credits"] += per[sid]["credits"]
        titles[rid].append(title)
    runs = []
    for rid, agg in by_run.items():
        if agg["codex"] > agg["credits"]:  # the run was on the openai profile
            if agg["codex"] < MIN_RUN_COST["opencode-openai"]:
                continue
            runs.append({"host": "opencode-openai", "run": rid, "titles": sorted(titles[rid]),
                         "cost": round(agg["codex"]), "unit": "codex credits", **common(agg)})
            continue
        if agg["credits"] < MIN_RUN_COST["opencode"]:
            continue
        runs.append({
            "host": "opencode", "run": rid, "titles": sorted(titles[rid]),
            "cost": round(agg["billed"]), "unit": "credits billed", "credits_standard": round(agg["credits"]),
            "orchestrator_share_pct": round(100 * agg["main_credits"] / agg["credits"], 1),
            **common(agg),
        })
    return runs


def common(agg: dict) -> dict:
    return {
        "start": datetime.fromtimestamp(agg["first"] / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if agg["first"] else None,
        "end": datetime.fromtimestamp(agg["last"] / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if agg["last"] else None,
        "requests": int(agg["requests"]),
        "mtok_in": round(agg["tok_in"] / 1e6, 1), "mtok_out": round(agg["tok_out"] / 1e6, 2),
        "cache_read_pct": round(100 * agg["tok_read"] / max(agg["tok_in"], 1), 1),
        "avg_request_k": round(agg["tok_in"] / max(agg["requests"], 1) / 1000),
        "full": agg["requests"] >= MIN_FULL_REQUESTS,
    }


# ---------------------------------------------------------------- Claude Code runs

def claude_requests(path: str):
    """(epoch ms, model, usage) per API response; a response split over lines counts once."""
    seen = set()
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            msg = d.get("message")
            if d.get("type") != "assistant" or not isinstance(msg, dict) or not msg.get("usage"):
                continue
            if msg.get("model") == "<synthetic>" or msg.get("id") in seen or not d.get("timestamp"):
                continue
            seen.add(msg.get("id"))
            yield ts_ms(d["timestamp"]), msg.get("model", "?"), msg["usage"]


def claude_usd(model: str, usage: dict, prices: dict) -> float | None:
    p = prices.get(model.split("[")[0])
    if not p:
        return None
    cc = usage.get("cache_creation") or {}
    w5, w1 = cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens")
    if w5 is None and w1 is None:
        w5, w1 = usage.get("cache_creation_input_tokens", 0) or 0, 0
    return ((usage.get("input_tokens", 0) or 0) * p["input"] + (usage.get("output_tokens", 0) or 0) * p["output"]
            + (w5 or 0) * p["write_5m"] + (w1 or 0) * p["write_1h"]
            + (usage.get("cache_read_input_tokens", 0) or 0) * p["cache_read"]) / 1e6


def claude_runs(projects: str, since: int, until: int, prices: dict) -> tuple[list[dict], list[tuple[int, float]], dict]:
    """Per-dossier runs, every priced request on the machine (for calibration), unpriced models."""
    agg: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    stream: list[tuple[int, float]] = []
    unpriced: dict[str, int] = defaultdict(int)
    for path in glob.glob(os.path.join(projects, "**", "*.jsonl"), recursive=True):
        rid = None
        if "/subagents/" not in path:
            with open(path, errors="replace") as fh:
                head = fh.read(400000)
            match = CLAUDE_RUN.search(head) if WORK_ON.search(head) else None
            rid = (match.group(1) or match.group(2)) if match else None
        files = [path]
        if rid:
            sid = os.path.basename(path)[:-6]
            files += glob.glob(os.path.join(os.path.dirname(path), sid, "subagents", "*.jsonl"))
        for f in files:
            counted = f == path  # subagent files are also walked on their own: count them for the stream once
            for ms, model, usage in claude_requests(f):
                usd = claude_usd(model, usage, prices)
                if usd is None:
                    unpriced[model] += counted
                    continue
                if counted:
                    stream.append((ms, usd))
                if not rid or not since <= ms <= until:
                    continue
                a = agg[rid]
                a["usd"] += usd
                a["requests"] += 1
                read = usage.get("cache_read_input_tokens", 0) or 0
                a["tok_in"] += (usage.get("input_tokens", 0) or 0) + read + (usage.get("cache_creation_input_tokens", 0) or 0)
                a["tok_read"] += read
                a["tok_out"] += usage.get("output_tokens", 0) or 0
                a["first"] = min(a["first"] or ms, ms)
                a["last"] = max(a["last"], ms)
                if f == path:
                    a["main_usd"] += usd
    runs = [{"host": "claude", "run": rid, "cost": round(a["usd"], 2), "unit": "usd api-equivalent",
             "orchestrator_share_pct": round(100 * a["main_usd"] / a["usd"], 1), **common(a)}
            for rid, a in agg.items() if a["usd"] >= MIN_RUN_COST["claude"]]
    return runs, stream, dict(unpriced)


def calibrate(log: str, stream: list[tuple[int, float]]) -> dict | None:
    """The weekly window's API-equivalent size from the logged % rise and the local spend over it."""
    if not os.path.exists(log):
        return None
    windows: dict[int, list[tuple[int, float]]] = defaultdict(list)
    with open(log) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("week_pct") is not None and r.get("week_resets_at"):
                windows[r["week_resets_at"]].append((ts_ms(r["ts"]), float(r["week_pct"])))
    best = None
    for resets, points in windows.items():
        points.sort()
        rise = points[-1][1] - points[0][1]
        if rise < MIN_CALIBRATION_PCT:
            continue
        spent = sum(usd for ms, usd in stream if points[0][0] < ms <= points[-1][0])
        cand = {"week_usd": round(spent / rise * 100, 2), "rise_pct": rise, "spent_usd": round(spent, 2),
                "from": points[0][0], "to": points[-1][0], "resets_at": resets}
        if best is None or rise > best["rise_pct"]:
            best = cand
    return best


# ---------------------------------------------------------------- report

def shares(runs: list[dict], plans: dict, claude_week: float) -> None:
    for r in runs:
        if r["host"] == "opencode":
            r["share_pct"] = {name: round(100 * r["cost"] / p["week"], 1) for name, p in plans["glm"]["plans"].items()}
        elif r["host"] == "opencode-openai":
            # None until a plan's week_credits is measured (see plans.json)
            r["share_pct"] = {name: (round(100 * r["cost"] / p["week_credits"], 1) if p.get("week_credits") else None)
                              for name, p in plans.get("codex", {}).get("plans", {}).items()}
        else:
            r["share_pct"] = {name: round(100 * r["cost"] / claude_week, 1) for name in plans["claude"]["plans"]}


def summary(runs: list[dict]) -> dict:
    out = {}
    for host in ("opencode", "opencode-openai", "claude"):
        rs = [r for r in runs if r["host"] == host and r["full"]]
        if not rs:
            continue
        plans = rs[0]["share_pct"].keys()
        out[host] = {"runs": len(rs), "median_cost": statistics.median(r["cost"] for r in rs),
                     "median_mtok_in": statistics.median(r["mtok_in"] for r in rs),
                     "median_avg_request_k": statistics.median(r["avg_request_k"] for r in rs),
                     "median_share_pct": {p: (statistics.median(r["share_pct"][p] for r in rs)
                                              if all(r["share_pct"][p] is not None for r in rs) else None)
                                          for p in plans}}
        out[host]["dossiers_per_week"] = {p: round(100 / v, 1) if v else None
                                          for p, v in out[host]["median_share_pct"].items()}
    return out


def build(args, plans: dict) -> dict:
    since, until = iso_ms(args.since, 0), iso_ms(args.until, 2**62)
    oc = opencode_runs(args.db, since, until, plans["glm"], plans.get("codex", {}).get("credits_per_mtok"))
    cc, stream, unpriced = claude_runs(args.projects, since, until, plans["claude"]["prices_per_mtok"])
    seat = next(iter(plans["claude"]["plans"].values()))
    cal = calibrate(args.usage_log, stream)
    claude_week = cal["week_usd"] if cal else seat["week_usd"]
    runs = sorted(oc + cc, key=lambda r: (r["host"], r["start"] or ""))
    shares(runs, plans, claude_week)
    return {"window": {"since": args.since, "until": args.until},
            "claude_week_usd": claude_week,
            "claude_week_basis": "calibrated from usage log" if cal else "plans.json week_usd",
            "calibration": cal, "unpriced_claude_models": unpriced,
            "summary": summary(runs), "runs": runs}


def print_report(rep: dict) -> None:
    print(f"claude seat week: ${rep['claude_week_usd']} API-equivalent ({rep['claude_week_basis']})")
    for host, s in rep["summary"].items():
        print(f"\n{host}: {s['runs']} runs, median cost {s['median_cost']}, median {s['median_mtok_in']} Mtok in, "
              f"avg request {s['median_avg_request_k']}k")
        for plan, pct in s["median_share_pct"].items():
            if pct is None:
                print(f"  {plan}: week unmeasured (set week_credits in plans.json from one measured dossier)")
            else:
                print(f"  {plan}: median {pct}% of a week per dossier, ~{s['dossiers_per_week'][plan]} dossiers/week")
    print(f"\n* fewer than {MIN_FULL_REQUESTS} requests: an inquiry or a run in progress, not in the medians")
    print("host      run     start              cost         Mtok in  req   avg k  read%  share of week")
    for r in rep["runs"]:
        share = ", ".join(f"{k.split(' ')[0]} {v}%" for k, v in r["share_pct"].items() if v is not None) or "n/a"
        print(f"{r['host']:<9} {r['run'] + ('' if r['full'] else '*'):<7} {r['start'] or '':<18} {r['cost']:>9} {r['unit'][:7]:<7} "
              f"{r['mtok_in']:>6} {r['requests']:>5} {r['avg_request_k']:>5} {r['cache_read_pct']:>5}  {share}")


def compare(rep: dict, before: dict) -> None:
    print("\ncompare (median share of a week per dossier):")
    for host, s in rep["summary"].items():
        old = before.get("summary", {}).get(host)
        for plan, pct in s["median_share_pct"].items():
            prev = old["median_share_pct"].get(plan) if old else None
            delta = f"{pct - prev:+.1f}" if prev is not None else "n/a"
            print(f"  {host} {plan}: {prev} -> {pct} ({delta} points)")


# ---------------------------------------------------------------- selftest

def selftest() -> int:
    failures = []

    def check(name, cond, detail=""):
        print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"  {detail}"))
        if not cond:
            failures.append(name)

    plans = json.load(open(PLANS))
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "oc.db")
        con = sqlite3.connect(db)
        con.executescript("create table session (id text, parent_id text, title text);"
                          "create table message (id text, session_id text, data text, time_created int);"
                          "create table part (id text, message_id text, session_id text, data text);")
        peak_ms = iso_ms("2026-10-12T07:00:00", 0)    # a Monday, 07:00 UTC: peak
        off_ms = iso_ms("2026-10-12T20:00:00", 0)     # Monday evening: off-peak
        con.execute("insert into session values ('r', null, 'W-123 build')")
        con.execute("insert into session values ('c', 'r', 'child')")
        con.execute("insert into session values ('x', null, 'unrelated')")
        con.execute("insert into session values ('o', null, 'W-555 on openai')")
        step = {"type": "step-finish", "tokens": {"input": 10000, "output": 10000, "reasoning": 0, "cache": {"read": 1000000}}}
        for i, (sid, ms) in enumerate((("r", peak_ms), ("c", off_ms), ("x", off_ms))):
            con.execute("insert into message values (?, ?, ?, ?)", (f"m{i}", sid, json.dumps({"modelID": "glm-5.3"}), ms))
            con.execute("insert into part values (?, ?, ?, ?)", (f"p{i}", f"m{i}", sid, json.dumps(step)))
        con.execute("insert into message values ('mo', 'o', ?, ?)", (json.dumps({"modelID": "gpt-6.1-sol"}), off_ms))
        con.execute("insert into part values ('po', 'mo', 'o', ?)", (json.dumps(step),))
        con.commit()
        con.close()
        # one step: (10k*6.9 + 1M*1.7 + 10k*24)/10k = 6.9 + 170 + 24 = 200.9 credits
        saved_min = dict(MIN_RUN_COST)
        MIN_RUN_COST.update({"opencode": 1, "opencode-openai": 1})
        both = opencode_runs(db, 0, 2**62, plans["glm"], plans["codex"]["credits_per_mtok"])
        MIN_RUN_COST.update(saved_min)
        oc = [r for r in both if r["host"] == "opencode"]
        oa = [r for r in both if r["host"] == "opencode-openai"]
        check("one opencode run, children included, unrelated sessions out", [r["run"] for r in oc] == ["W-123"], oc)
        # (10k * 50 + 1M * 2.5 + 10k * 250) / 1M = 5.5 Codex credits
        check("an openai-profile run is priced in codex credits", [(r["run"], r["cost"]) for r in oa] == [("W-555", 6)], oa)
        check("standard credits sum both steps", oc and oc[0]["credits_standard"] == 402, oc)
        check("billed halves only the off-peak step", oc and oc[0]["cost"] == round(200.9 + 100.45), oc)
        check("orchestrator share is the root's part", oc and oc[0]["orchestrator_share_pct"] == 50.0, oc)

        proj = os.path.join(tmp, "projects", "p")
        os.makedirs(os.path.join(proj, "s1", "subagents"))

        def line(ts, mid, model, out_tok, read):
            return json.dumps({"type": "assistant", "timestamp": ts, "message": {"id": mid, "model": model, "usage": {
                "input_tokens": 0, "output_tokens": out_tok, "cache_read_input_tokens": read,
                "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}}}}) + "\n"

        with open(os.path.join(proj, "s1.jsonl"), "w") as fh:
            fh.write('{"type":"user","message":{"content":"<command-name>/agentic-software-development:work-on</command-name>'
                     '\\n<command-message>work-on</command-message>\\n<command-args>W-777</command-args>"}}\n')
            fh.write(line("2026-10-12T10:00:00Z", "a", "claude-opus-5-5", 100000, 0))  # $2.00
            fh.write(line("2026-10-12T10:00:00Z", "a", "claude-opus-5-5", 100000, 0))  # same response: once
            fh.write(line("2026-10-12T11:00:00Z", "b", "<synthetic>", 1, 0))
        with open(os.path.join(proj, "s1", "subagents", "x.jsonl"), "w") as fh:
            fh.write(line("2026-10-12T10:30:00Z", "c", "claude-sonnet-5-5", 0, 10000000))  # $1.00
        with open(os.path.join(proj, "other.jsonl"), "w") as fh:
            fh.write(line("2026-10-12T12:00:00Z", "d", "claude-opus-5-5", 50000, 0))  # $1.00, not a dossier
        cc, stream, unpriced = claude_runs(os.path.join(tmp, "projects"), 0, 2**62, plans["claude"]["prices_per_mtok"])
        check("one claude run with its subagent", [(r["run"], r["cost"]) for r in cc] == [("W-777", 3.0)], cc)
        check("orchestrator share of the claude run", cc and cc[0]["orchestrator_share_pct"] == 66.7, cc)
        check("the calibration stream counts every request once", round(sum(u for _, u in stream), 2) == 4.0, stream)

        log = os.path.join(tmp, "usage-log.jsonl")
        with open(log, "w") as fh:
            fh.write(json.dumps({"ts": "2026-10-12T09:00:00Z", "week_pct": 10, "week_resets_at": 1}) + "\n")
            fh.write(json.dumps({"ts": "2026-10-12T13:00:00Z", "week_pct": 12, "week_resets_at": 1}) + "\n")
        check("a rise under the minimum does not calibrate", calibrate(log, stream) is None)
        with open(log, "a") as fh:
            fh.write(json.dumps({"ts": "2026-10-12T13:00:01Z", "week_pct": 50, "week_resets_at": 1}) + "\n")
        cal = calibrate(log, stream)
        check("calibration: $4 over 40 points is a $10 week", cal and cal["week_usd"] == 10.0, cal)

        runs = oc + cc + oa
        shares(runs, plans, 10.0)
        check("an unmeasured codex week gives no share", oa and oa[0]["share_pct"]["Business seat"] is None, oa)
        check("claude share uses the calibrated week", cc[0]["share_pct"]["Team Premium seat (Max 5x tier)"] == 30.0, cc)
        check("glm share per plan", oc[0]["share_pct"]["Max"] == round(100 * oc[0]["cost"] / 140000, 1), oc)
        check("a short run stays out of the medians", summary(runs) == {})
        global MIN_FULL_REQUESTS
        saved, MIN_FULL_REQUESTS = MIN_FULL_REQUESTS, 1
        try:
            for r in runs:
                r["full"] = r["requests"] >= MIN_FULL_REQUESTS
            s = summary(runs)
        finally:
            MIN_FULL_REQUESTS = saved
        check("summary has every host", set(s) == {"opencode", "opencode-openai", "claude"}, s)
        check("an unmeasured week has no dossiers-per-week", s["opencode-openai"]["dossiers_per_week"]["Business seat"] is None, s)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--projects", default=PROJECTS)
    ap.add_argument("--usage-log", default=USAGE_LOG)
    ap.add_argument("--plans", default=PLANS)
    ap.add_argument("--json", help="write the report here")
    ap.add_argument("--compare", help="an earlier --json file")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    try:
        plans = json.load(open(args.plans))
        rep = build(args, plans)
    except (OSError, ValueError) as err:
        print(f"budget_report: {err}", file=sys.stderr)
        return 2
    print_report(rep)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rep, fh, indent=1)
            fh.write("\n")
    if args.compare:
        compare(rep, json.load(open(args.compare)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
