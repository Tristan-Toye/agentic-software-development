#!/usr/bin/env python3
"""Measure where the /work-on Opus orchestrator spends cache-read tokens, per phase.

Read-only. Scans ~/.claude-main/projects and ~/.claude-gmail/projects (*.jsonl, top level only, so
sub-agent records are skipped). An orchestrator session is one whose first user message is the
runner's stage command `/agentic-software-development:work-on <ID>` (run_dossier.py:1303).

Per request (an assistant message id, de-duplicated; usage = its last record):
  cr        cache-read input tokens
  kind      mechanical  = only tool_use blocks that are Bash/Read/ref_section/checkpoint/etc, text < TEXT_MIN chars
            scripted    = the same, and every Bash command is a known plugin script (run_tests, run_gates, ...)
            judgement   = a text block >= TEXT_MIN chars, or an Edit/Write of a dossier or doc
            spawn       = Workflow / Agent / SendMessage call
  phase     the phase named by the NEXT phase_checkpoint call (a checkpoint closes the work before it);
            after the last checkpoint: the last phase file the session read. Approximate by design.
Usage: measure_orchestrator_phases.py [--since 2026-11-09] [--json out.json]
"""
import argparse, glob, json, os, re, sys
from collections import defaultdict

HOMES = ["~/.claude-main/projects", "~/.claude-gmail/projects"]
TEXT_MIN = 200
SCRIPTS = re.compile(r"(run_tests|run_gates|validate_pipeline|compose_payloads|prepare_wave|verify_return|git_state|"
                     r"check_payload|review_signals|flake_risk|spawn_admission|remove_worktree|safe_revert|"
                     r"ask_owner|payload_hash|gate_commit|wait_ci|stub_target|dossier_edit|disk_guard|code_nav)\.py")
MECH_TOOLS = {"Bash", "Read", "Grep", "Glob", "TaskOutput", "BashOutput", "Monitor", "ToolSearch"}
SPAWN = {"Workflow", "Agent", "Task", "SendMessage"}
PH = re.compile(r"Phase\s*(\d+)", re.I)
PHF = re.compile(r"phase-(\d)")


def tool_phase_hint(b):
    s = json.dumps(b.get("input", ""))
    m = PHF.search(s)
    return int(m.group(1)) if m else None


def head(blocks):
    """A short label per tool call: the plugin script name, the git verb, or the tool name."""
    out = []
    for t in blocks:
        if t.get("type") != "tool_use":
            continue
        if t["name"] != "Bash":
            out.append(t["name"].split("__")[-1])
            continue
        c = t.get("input", {}).get("command", "")
        m = SCRIPTS.search(c)
        if m:
            out.append(m.group(1))
            continue
        c = re.sub(r"^\s*(cd\s+\S+\s*&&\s*)?(rtk\s+)?(/usr/bin/)?", "", c)
        w = c.split()[:2]
        out.append(" ".join(w) if w and w[0] == "git" else (w[0] if w else "?"))
    return out


def classify(blocks):
    texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    tools = [b for b in blocks if b.get("type") == "tool_use"]
    if any(t["name"] in SPAWN or t["name"].endswith("Workflow") for t in tools):
        return "spawn"
    if any(len(x) >= TEXT_MIN for x in texts):
        return "judgement"
    if any(t["name"] in ("Edit", "Write") for t in tools):
        return "judgement"
    if not tools:
        return "text-only"
    cmds = [t.get("input", {}).get("command", "") for t in tools if t["name"] == "Bash"]
    if cmds and all(SCRIPTS.search(c) or re.match(r"\s*(rtk\s+)?(/usr/bin/)?git\b", c) for c in cmds) \
            and all(t["name"] == "Bash" or t["name"].startswith("mcp__") for t in tools):
        return "scripted"
    return "mechanical"


def session(path, since):
    reqs, order, first, cps = {}, [], None, []
    hint = None
    for ln in open(path, errors="replace"):
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        m = o.get("message") or {}
        if o.get("type") == "user" and first is None:
            c = m.get("content")
            first = c if isinstance(c, str) else json.dumps(c)
            if "work-on" not in first[:400]:
                return None
        if o.get("type") != "assistant" or not isinstance(m.get("content"), list):
            continue
        u = m.get("usage") or {}
        mid = m.get("id") or o.get("uuid")
        blocks = m["content"]
        for b in blocks:
            if b.get("type") == "tool_use":
                h = tool_phase_hint(b)
                if h is not None:
                    hint = h
                if b["name"].endswith("phase_checkpoint"):
                    mm = PH.search(str(b.get("input", {}).get("phase", "")))
                    cps.append((len(order) if mid not in reqs else order.index(mid), int(mm.group(1)) if mm else None))
        if mid not in reqs:
            order.append(mid)
        reqs[mid] = dict(mid=mid, ts=o.get("timestamp", ""), cr=u.get("cache_read_input_tokens", 0) or 0,
                         cc=u.get("cache_creation_input_tokens", 0) or 0, out=u.get("output_tokens", 0) or 0,
                         kind=classify(blocks), heads=head(blocks), hint=hint, model=m.get("model", ""))
    if first is None or not reqs or max(r["ts"] for r in reqs.values()) < since:
        return None
    # phase by next checkpoint
    cps = [c for c in cps if c[1] is not None]
    for i, mid in enumerate(order):
        nxt = next((p for idx, p in cps if idx >= i), None)
        reqs[mid]["phase"] = nxt if nxt is not None else reqs[mid]["hint"]
    return [reqs[m] for m in order]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-11-09")
    ap.add_argument("--json")
    a = ap.parse_args()
    agg = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))  # phase -> kind -> [n, cr, out]
    nsess = 0
    span = ["9", ""]
    heads = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    seen = set()  # a resumed session copies earlier records into a new file: count each message id once
    for home in HOMES:
        for p in glob.glob(os.path.join(os.path.expanduser(home), "*", "*.jsonl")):
            rs = session(p, a.since)
            if not rs:
                continue
            nsess += 1
            for r in rs:
                if r["ts"] < a.since or "opus" not in r["model"] or r["mid"] in seen:
                    continue
                seen.add(r["mid"])
                span[0] = min(span[0], r["ts"]); span[1] = max(span[1], r["ts"])
                if r["kind"] in ("scripted", "mechanical"):
                    for h in r["heads"][:1]:
                        heads[r["phase"]][h][0] += 1; heads[r["phase"]][h][1] += r["cr"]
                c = agg[r["phase"]][r["kind"]]
                c[0] += 1; c[1] += r["cr"]; c[2] += r["out"]
    kinds = ["scripted", "mechanical", "spawn", "judgement", "text-only"]
    print(f"window: {span[0]} .. {span[1]}")
    print(f"sessions: {nsess} (opus requests since {a.since})")
    print("phase | reqs | cr(M) | " + " | ".join(f"{k} n / cr(M)" for k in kinds))
    for ph in sorted(agg, key=lambda x: (x is None, x)):
        v = agg[ph]
        n = sum(x[0] for x in v.values()); cr = sum(x[1] for x in v.values())
        print(f"P{ph} | {n} | {cr/1e6:.0f} | " + " | ".join(f"{v[k][0]} / {v[k][1]/1e6:.0f}" for k in kinds))
    for ph in (2, 3, 5, 6, 8, 9):
        top = sorted(heads[ph].items(), key=lambda kv: -kv[1][1])[:10]
        print(f"top first-tool heads P{ph}: " + ", ".join(f"{k} {v[0]}x/{v[1]/1e6:.0f}M" for k, v in top))
    book = ("dossier_edit", "ask_owner", "phase_checkpoint")  # bookkeeping: build-log lines, owner notes, checkpoints
    print("bookkeeping-first requests (dossier_edit/ask_owner/phase_checkpoint) and average context per request:")
    for ph in sorted(agg, key=lambda x: (x is None, x)):
        bn = sum(heads[ph][h][0] for h in book); bc = sum(heads[ph][h][1] for h in book)
        n = sum(x[0] for x in agg[ph].values()); cr = sum(x[1] for x in agg[ph].values())
        print(f"P{ph}: {bn} of {n} requests, {bc/1e6:.0f}M of {cr/1e6:.0f}M cr ({100*bc/max(cr,1):.0f}%), avg ctx {cr/max(n,1)/1e3:.0f}k")
    if a.json:
        json.dump({str(k): {kk: vv for kk, vv in v.items()} for k, v in agg.items()}, open(a.json, "w"))


if __name__ == "__main__":
    sys.exit(main())
