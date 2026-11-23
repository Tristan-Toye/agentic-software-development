#!/usr/bin/env python3
"""Admit one fan-out wave in one call: lint every payload, gate every path, take the slots.

Before a wave the orchestrator ran `check_payload.py` once per payload,
`check_permission_maps.py` once per test author, and `spawn_admission.py
acquire` once — each a separate model step, each paying the whole context
again, and each fix round paying them all a second time. The recorded runs
spent 223 steps on this glue. This script runs the same checks, unchanged, in
one process and prints only what needs a decision: every DEFECT and WARNING,
one verdict per spawn, and the two build-log lines.

The checks themselves are not reimplemented here. Every verdict comes from the
script that owns the rule, so a rule changes in one place.

# The wave manifest

One line per spawn, `key=value` fields separated by spaces; `#` starts a
comment:

    kind=implementer payload=/abs/X/.agent-staging/payloads/P1.md
    kind=unit-test-author payload=/abs/.../UT1.md test-paths=tests/a_test.rs read-paths=tests/b_test.rs
    kind=integration-test-author payload=/abs/.../IT1.md test-paths=tests/f1.rs,tests/f2.rs expected-lines=300 flows=2 live-dossier=/abs/live.md

Keys: `kind` and `payload` (required); `allow-path` (repeatable, comma
list); `test-paths`, `read-paths`, `expected-lines`, `flows` (test authors;
they select the `check_permission_maps.py` gate). `live-dossier` (the
integration author).

# Usage

    prepare_wave.py MANIFEST --host opencode --root X --plugin-root P
        [--provider zai --model glm-5.3-flash --holder W-014-wave1]
        [--lint-only | --admit-only] [--wait-slot SECONDS [--slot-poll S=15]]
        [--log LIVE_DOSSIER]
    prepare_wave.py --admit-next --root X --provider P --model M --holder H
        [--max K] [--wait-slot SECONDS]
    prepare_wave.py --selftest

Without `--provider/--model/--holder` it only lints (a re-check after a fix).
With them, and only when no spawn has a defect, it acquires one slot per spawn
and prints the `ADMISSION:` line. `--log` appends `PAYLOAD-LINT:` (and
`ADMISSION:` when taken) to the dossier's `## Build log` through
`dossier_edit.py`, so the dossier never has to be opened for it.

`--lint-only` states the re-check explicitly: lint and gate, never acquire,
even when `--provider/--model/--holder` are also given — so a fix round can
re-run the exact command that admitted the wave. It is the one door for
re-checking a payload; there is no reason to run `check_payload.py` alone.

# Partial waves: admit what is free now, the rest queued

Admission takes `min(N, free)` slots (`spawn_admission.py acquire --partial`),
never waits for all N. The first k payloads of the manifest are ADMITTED, one
slot each; the rest are QUEUED. The call prints one line the orchestrator (and
the phase4-wave workflow) reads:

    ADMITTED {"holder": H, "admitted": [{"payload": P, "slot": 3}, ...], "queued": [P, ...]}

and records the same in `<root>/.agent-staging/wave-<holder>.slots.json`, the
slot-to-payload map: `verify_return.py --release H --slot N` frees exactly the
slot of the agent it verifies. Only zero free slots (or a deferral) refuses, and
only that case blocks on `--wait-slot`. A request for more than the cap is split
the same way: at most the cap is ever held, the rest queues.

`--admit-next` admits the next queued payloads when a return freed a slot: one
command, no manifest, prints

    ADMIT-NEXT {"status": "ok", "admitted": [{"payload": P, "slot": 2}], "queued": [P, ...]}

`status` is `refused` (exit 1, nothing admitted, the queue kept) while a
recorded reset deadline lies ahead or, after `--wait-slot`, while no slot is
free: never spawn into a deferral. `--max K` caps how many it admits. A payload
already issued once and re-run (a re-spawn) is re-admitted by `--admit-only`
with a manifest of just that payload. Same holder throughout: the ledger keeps
one wave per holder, and the learned ceiling rises when its LAST slot goes.

Admission waits on the machine, not on the orchestrator: with `--provider/
--model/--holder` the call blocks, quietly, up to `--wait-slot SECONDS`
(default 900, `0` returns at once) while `spawn_admission.py acquire` refuses
for lack of slots, polling every `--slot-poll` seconds and printing one line
when it starts waiting. A provider deferral whose reset deadline falls inside
the bound is waited out the same way. A refusal that waiting cannot cure
(asked more than the cap) and a deferral that outlasts the bound still return 1
at once, naming what to do.

`--admit-only` takes the slots first, before the payloads are finished: it
reads the manifest only for the number of spawns, never lints, and records the
grant in `<root>/.agent-staging/wave-<holder>.admitted`. The orchestrator then
hand-writes the judgement fields with the wait (if any) already over, and the
normal call that follows lints everything and reuses that grant instead of
acquiring again (the spawn count must match). The lint rules are the same.

A lint failure reports everything at once: each payload's defects as found,
then one FIX LIST over the whole wave, every defect with the exact command or
edit that cures it (a missing sentinel is cured by `--seal`, which goes LAST
because any later edit unseals the payload), so one correction round settles
the wave.

Every payload that passes lint has its sha256 recorded in
`<root>/.agent-staging/wave-<holder>.hashes` (`wave.hashes` without a holder),
one `<sha256>  <payload path>` line per payload, replaced on each lint. A spawn
can prove it ships the linted bytes by comparing `sha256sum` of the payload with
that line: a payload edited after its lint no longer matches it. Every hash is
the full 64 hex characters: copy it whole into a spawn or a ledger line, never
shortened with an ellipsis or typed from memory (a truncated hash is refused as
an integrity failure).

`PAYLOAD-LINT` counts the defects fixed since the wave was first linted: each
run records its defect count in `<root>/.agent-staging/wave-<holder>.lint`
(`wave.lint` without a holder), and a clean run reports their sum.

Exit codes: 0 every spawn passed (and admission granted, when asked); 1 a
defect, a gate refusal, or an admission refusal or deferral — nothing
acquired; 2 unusable input.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Any

DEFAULT_WAIT = 900
TEST_AUTHORS = ("unit-test-author", "integration-test-author")
KEYS = {"kind", "payload", "allow-path", "test-paths", "read-paths", "expected-lines", "flows", "live-dossier"}


def parse_manifest(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        row: dict[str, str] = {"line": str(number)}
        for token in shlex.split(line):
            key, sep, value = token.partition("=")
            if not sep or key not in KEYS:
                raise ValueError(f"manifest line {number}: unknown field {token!r}")
            row[key] = f"{row[key]},{value}" if key == "allow-path" and key in row else value
        if "kind" not in row or "payload" not in row:
            raise ValueError(f"manifest line {number}: kind= and payload= are required")
        rows.append(row)
    if not rows:
        raise ValueError("the manifest names no spawn")
    return rows


def run(cmd: list[str]) -> tuple[int, list[str]]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode, (proc.stdout + proc.stderr).splitlines()


def findings(lines: list[str]) -> list[str]:
    """The lines a decision needs: defects, warnings, refusals, notes."""
    keep = ("DEFECT", "WARNING", "REFUSED", "FAIL", "NOTE", "ERROR", "error:")
    return [l.strip() for l in lines if l.strip().startswith(keep) or " DEFECT " in l]


def lint_row(row: dict[str, str], args: argparse.Namespace) -> tuple[bool, list[str]]:
    scripts = os.path.join(args.plugin_root, "scripts")
    out: list[str] = []
    cmd = [sys.executable, os.path.join(scripts, "check_payload.py"), row["payload"],
           "--kind", row["kind"], "--host", args.host, "--worktree", args.root]
    for path in filter(None, row.get("allow-path", "").split(",")):
        cmd += ["--allow-path", path]
    if row.get("live-dossier"):
        cmd += ["--live-dossier", row["live-dossier"]]
    rc, lines = run(cmd)
    out += findings(lines) or ([] if rc == 0 else lines[-3:])
    ok = rc == 0

    if row["kind"] in TEST_AUTHORS and (row.get("test-paths") or row.get("read-paths")):
        agent_dir = "claude/agents" if args.host == "claude" else "sub-agents"
        gate = [sys.executable, os.path.join(scripts, "check_permission_maps.py"),
                "--agent", os.path.join(args.plugin_root, agent_dir, row["kind"] + ".md"),
                "--host", args.host, "--root", args.root,
                "--test-paths", row.get("test-paths", "")]
        for key in ("read-paths", "expected-lines", "flows"):
            if row.get(key):
                gate += ["--" + key, row[key]]
        rc, lines = run(gate)
        out += findings(lines) or ([] if rc == 0 else lines[-3:])
        ok = ok and rc == 0
    return ok, out


def fix_hint(defect: str, payload: str, plugin_root: str) -> str:
    """The exact cure for a defect when one command or edit is known; "" otherwise."""
    if "PAYLOAD-END" in defect:
        return "run LAST, after every other fix: python3 %s --seal %s" % (
            os.path.join(plugin_root, "scripts", "compose_payloads.py"), payload)
    left = sorted(set(re.findall(r"@@FILL_([A-Z0-9_]+)@@", defect)))
    if left:
        return "replace %s in %s with the real value, or recompose with --set %s='<value>'" % (
            ", ".join("@@FILL_%s@@" % x for x in left), payload, left[0])
    near = re.search(r"did you mean ([A-Z0-9_, ]+)\?", defect)
    if near:
        return "rename the field to %s in %s" % (near.group(1).strip(), payload)
    return ""


def fix_list(failures: list[tuple[str, list[str]]], plugin_root: str) -> list[str]:
    """One block naming every defect of every failed payload, the seal steps last."""
    items: list[tuple[int, str]] = []
    for payload, defects in failures:
        name = os.path.basename(payload)
        for defect in defects:
            text = re.sub(r"^(DEFECT|REFUSED|FAIL|ERROR|error:)\s*", "", defect).strip()
            hint = fix_hint(text, payload, plugin_root)
            line = "%s: %s" % (name, text)
            if hint and hint not in text:
                line += "\n          FIX: " + hint
            items.append((1 if "PAYLOAD-END" in text else 0, line))
    items.sort(key=lambda it: it[0])
    out = ["FIX LIST — %d defect(s) across %d payload(s); fix them all, then re-run once:" % (len(items), len(failures))]
    out += ["  %d. %s" % (i, line) for i, (_, line) in enumerate(items, 1)]
    return out


def ledger_path(root: str, holder: str | None) -> str:
    name = f"wave-{holder}.lint" if holder else "wave.lint"
    return os.path.join(root, ".agent-staging", name)


def defects_fixed(root: str, holder: str | None, defects_now: int) -> int:
    path = ledger_path(root, holder)
    history: list[int] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            history = [int(x) for x in fh.read().split() if x.isdigit()]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{defects_now}\n")
    return sum(history)


def hashes_path(root: str, holder: str | None) -> str:
    name = f"wave-{holder}.hashes" if holder else "wave.hashes"
    return os.path.join(root, ".agent-staging", name)


def record_hashes(root: str, holder: str | None, payloads: list[str]) -> str:
    """Record sha256 of each linted payload as `<sha256>  <path>`; a re-lint
    replaces that path's line, other paths' lines stay. Returns the file path."""
    path = hashes_path(root, holder)
    entries: dict[str, str] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh.read().splitlines():
                digest, _, name = line.partition("  ")
                if digest and name:
                    entries[name] = digest
    for payload in payloads:
        with open(payload, "rb") as fh:
            entries[payload] = hashlib.sha256(fh.read()).hexdigest()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(f"{digest}  {name}\n" for name, digest in entries.items()))
    return path


def deferral_seconds(lines: list[str], now: float) -> float | None:
    """Seconds until the reset a `deferred:` line names, or None when it names none."""
    text = next((l for l in lines if l.startswith("deferred:")), "")
    m = re.search(r"resets at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", text)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")) - now
    except (ValueError, OverflowError):
        return None


def acquire(args: argparse.Namespace, n: int, runner=run, sleep=time.sleep, clock=time.monotonic,
            wall=time.time, partial: bool = False) -> tuple[int, list[str]]:
    """spawn_admission.py acquire, polled while it refuses only for lack of
    slots (or defers to a reset inside the bound) and --wait-slot seconds
    remain. Returns (exit, output lines)."""
    cmd = [sys.executable, os.path.join(args.plugin_root, "scripts", "spawn_admission.py"),
           "acquire", "--provider", args.provider, "--model", args.model,
           "--n", str(n), "--holder", args.holder] + (["--partial"] if partial else [])
    deadline = clock() + (args.wait_slot or 0)
    announced = False
    while True:
        rc, lines = runner(cmd)
        if rc == 0:
            return rc, lines
        refused = next((l for l in lines if l.startswith("refused:")), "")
        m = re.search(r"(\d+) free of cap (\d+).*asked (\d+)", refused)
        curable = bool(m) and (partial or int(m.group(3)) <= int(m.group(2)))
        pause = max(min(args.slot_poll, deadline - clock()), 0.001)
        reason = refused
        if not curable:
            until = deferral_seconds(lines, wall())
            if until is not None and 0 <= until <= deadline - clock():
                curable, reason = True, next(l for l in lines if l.startswith("deferred:"))
                pause = max(min(until + 1, max(args.slot_poll, 1) * 4), 0.001)
        if not (args.wait_slot and curable) or clock() >= deadline:
            return rc, lines
        if not announced:
            print(f"      waiting up to {args.wait_slot:.0f}s for {n} free slot(s): {reason}")
            announced = True
        sleep(pause)


def admitted_path(root: str, holder: str | None) -> str:
    return os.path.join(root, ".agent-staging", f"wave-{holder}.admitted" if holder else "wave.admitted")


def slots_path(root: str, holder: str | None) -> str:
    return os.path.join(root, ".agent-staging", f"wave-{holder}.slots.json" if holder else "wave.slots.json")


def load_state(root: str, holder: str | None) -> dict | None:
    try:
        with open(slots_path(root, holder), encoding="utf-8") as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) and "payloads" in state else None
    except (OSError, ValueError):
        return None


def granted_slots(lines: list[str]) -> list[int]:
    text = next((l for l in lines if l.startswith("granted:")), "")
    m = re.search(r"slots ([\d,]+)", text)
    return [int(x) for x in m.group(1).split(",") if x] if m else []


def register_grant(args: argparse.Namespace, payloads: list[str], granted: list[int]) -> dict:
    """Record which payload got which slot, and which still wait: the first len(granted)
    of `payloads` take the slots in order, the rest queue. Merges into the holder's state."""
    state = load_state(args.root, args.holder) or {
        "holder": args.holder, "provider": args.provider, "model": args.model,
        "payloads": list(payloads), "slots": {}, "queue": [],
    }
    for payload in payloads:
        if payload not in state["payloads"]:
            state["payloads"].append(payload)
    taken = dict(zip(payloads, granted))
    state["slots"].update({p: n for p, n in taken.items()})
    waiting = [p for p in payloads if p not in taken]
    state["queue"] = [q for q in state["queue"] if q not in taken] + [p for p in waiting if p not in state["queue"]]
    save_state(args, state)
    return state


def save_state(args: argparse.Namespace, state: dict) -> None:
    path = slots_path(args.root, args.holder)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def admitted_json(state: dict, payloads: list[str], granted: list[int], tag: str = "ADMITTED") -> str:
    body = {"holder": state["holder"], "admitted": [{"payload": p, "slot": n} for p, n in zip(payloads, granted)],
            "queued": list(state["queue"])}
    if tag == "ADMIT-NEXT":
        body = {"status": "ok", **{k: v for k, v in body.items() if k != "holder"}}
    return tag + " " + json.dumps(body)


def current_admitted(state: dict) -> str:
    live = [p for p in state["payloads"] if p in state["slots"] and p not in state["queue"]]
    return admitted_json(state, live, [state["slots"][p] for p in live])


def admit_next(args: argparse.Namespace, acquirer=None) -> int:
    """--admit-next: a return freed a slot; admit the next queued payloads (min(queued, free,
    --max)), waiting only while zero are free. Serialised per holder by a file lock."""
    import fcntl
    path = slots_path(args.root, args.holder)
    if load_state(args.root, args.holder) is None:
        print(f"prepare_wave: no admission state at {path}; run --admit-only first", file=sys.stderr)
        return 2
    with open(path + ".lock", "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load_state(args.root, args.holder) or {}
        queue = list(state.get("queue", []))
        if not queue:
            print("ADMIT-NEXT " + json.dumps({"status": "ok", "admitted": [], "queued": []}))
            return 0
        n = min(len(queue), args.max) if args.max else len(queue)
        rc, lines = (acquirer or acquire)(args, n, partial=True)
        if rc != 0:
            reason = next((l for l in lines if l.startswith(("deferred:", "refused:"))), "refused")
            print("ADMIT-NEXT " + json.dumps({"status": "refused", "admitted": [], "queued": queue, "reason": reason[:200]}))
            return 1
        granted = granted_slots(lines)
        taken = queue[:len(granted)]
        state = register_grant(args, taken, granted)
        print(admitted_json(state, taken, granted, "ADMIT-NEXT"))
        return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("manifest", nargs="?")
    ap.add_argument("--host", choices=("opencode", "claude"))
    ap.add_argument("--root", help="the base worktree X the payload paths resolve against")
    ap.add_argument("--plugin-root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--provider")
    ap.add_argument("--model")
    ap.add_argument("--holder", help="e.g. W-014-wave1")
    ap.add_argument("--log", metavar="LIVE_DOSSIER", help="append the build-log lines to this dossier")
    ap.add_argument("--lint-only", action="store_true", help="lint and gate; never acquire")
    ap.add_argument("--admit-only", action="store_true",
                    help="take the slots now (no lint); the next normal call lints and reuses the grant")
    ap.add_argument("--admit-next", action="store_true",
                    help="admit the next queued payloads of this holder's partial wave (no manifest)")
    ap.add_argument("--max", type=int, default=0, metavar="K", help="with --admit-next: admit at most K")
    ap.add_argument("--wait-slot", type=float, default=None, metavar="SECONDS",
                    help="when admission is refused for lack of slots, block until free or this long "
                         "(default %d; 0 returns at once)" % DEFAULT_WAIT)
    ap.add_argument("--slot-poll", type=float, default=15, metavar="SECONDS")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.admit_next:
        if not (args.root and all([args.provider, args.model, args.holder])):
            ap.error("--admit-next needs --root, --provider, --model and --holder")
        if args.wait_slot is None:
            args.wait_slot = DEFAULT_WAIT
        return admit_next(args)
    if not (args.manifest and args.host and args.root):
        ap.error("MANIFEST, --host and --root are required unless --selftest")
    admit = [args.provider, args.model, args.holder]
    if any(admit) and not all(admit):
        ap.error("--provider, --model and --holder go together")
    if args.admit_only and (not all(admit) or args.lint_only):
        ap.error("--admit-only needs --provider, --model and --holder, and excludes --lint-only")
    if args.wait_slot is None:
        args.wait_slot = DEFAULT_WAIT if all(admit) and not args.lint_only else 0
    try:
        with open(args.manifest, encoding="utf-8") as fh:
            rows = parse_manifest(fh.read())
    except (OSError, ValueError) as err:
        print(f"prepare_wave: {err}", file=sys.stderr)
        return 2

    if args.admit_only:
        return admit_now(args, rows)

    failed: list[str] = []
    linted: list[str] = []
    failures: list[tuple[str, list[str]]] = []
    defect_count = 0
    for row in rows:
        ok, out = lint_row(row, args)
        name = os.path.basename(row["payload"])
        print(f"{'PASS' if ok else 'FAIL'}  {row['kind']}  {name}")
        for item in out:
            print(f"      {item}")
        if not ok:
            failed.append(name)
            failures.append((row["payload"], [i for i in out if i.startswith(("DEFECT", "REFUSED", "FAIL", "ERROR", "error:")) or " DEFECT " in i] or out))
            defect_count += max(1, sum(1 for item in out if "DEFECT" in item or "REFUSED" in item))
        elif os.path.isfile(row["payload"]):
            linted.append(row["payload"])
    if linted:
        print(f"      sha256 of {len(linted)} linted payload(s) recorded in {record_hashes(args.root, args.holder, linted)}")

    fixed = defects_fixed(args.root, args.holder, defect_count)
    ids = ", ".join(os.path.splitext(os.path.basename(r["payload"]))[0] for r in rows)
    if failed:
        for line in fix_list(failures, args.plugin_root):
            print(line)
        print(f"prepare_wave: {len(failed)} of {len(rows)} spawn(s) failed — fix them all and re-run; nothing acquired"
              + (" (a grant taken by --admit-only is kept)" if os.path.exists(admitted_path(args.root, args.holder)) else ""))
        return 1

    log_lines = [f"PAYLOAD-LINT: {len(rows)} payloads, {fixed} defects fixed — {ids}"]
    if all(admit) and not args.lint_only:
        held = read_admitted(args.root, args.holder)
        state = load_state(args.root, args.holder)
        payloads = [r["payload"] for r in rows]
        if held is not None and (set(state["payloads"]) != set(payloads) if state else held[0] != len(rows)):
            print(f"prepare_wave: --admit-only admitted a different wave for this holder than the manifest names ({len(rows)} spawn(s)); "
                  "release the holder with spawn_admission.py release and admit again")
            return 1
        rc, lines = (0, [held[1]]) if held is not None else acquire(args, len(rows), partial=True)
        if held is not None:
            print(f"      admission already held from --admit-only ({held[0]} slot(s))")
            if state:
                print("      " + current_admitted(state))
        if rc == 0 and held is None:
            granted = granted_slots(lines)
            if granted:
                state = register_grant(args, payloads, granted)
                lines.append(admitted_json(state, payloads[:len(granted)], granted))
        for line in lines:
            print(f"      {line}")
        if rc != 0:
            print("prepare_wave: admission refused or deferred — see Phase 4's ladder; nothing spawned")
            if args.log:
                append(args, log_lines)
            for line in log_lines:
                print(line)
            return 1
        log_lines.append(admission_line(len(state["slots"]) if state else len(rows), args.provider, args.model, lines,
                                        total=len(rows)))
    if args.log:
        append(args, log_lines)
    for line in log_lines:
        print(line)
    return 0


def read_admitted(root: str, holder: str | None) -> tuple[int, str] | None:
    try:
        with open(admitted_path(root, holder), encoding="utf-8") as fh:
            n, _, granted = fh.read().strip().partition(" ")
        return int(n), granted
    except (OSError, ValueError):
        return None


def admit_now(args: argparse.Namespace, rows: list[dict[str, str]]) -> int:
    """--admit-only: take len(rows) slots before the payloads are finished; no lint."""
    held = read_admitted(args.root, args.holder)
    payloads = [r["payload"] for r in rows]
    state = load_state(args.root, args.holder)
    # A manifest of payloads this holder was already issued is a re-spawn: admit them again.
    respawn = bool(state) and payloads != state["payloads"] and set(payloads) <= set(state["payloads"])
    if held is not None and not respawn:
        same = set(state["payloads"]) == set(payloads) if state else held[0] == len(rows)
        print(f"prepare_wave: {args.holder} already holds {held[0]} slot(s) from an earlier --admit-only")
        if state:
            print(current_admitted(state))
        return 0 if same else 1
    rc, lines = acquire(args, len(rows), partial=True)
    for line in lines:
        print(f"      {line}")
    if rc != 0:
        print("prepare_wave: admission refused or deferred — see the Phase 4 ladder; nothing spawned")
        return 1
    granted_line = next((l for l in lines if l.startswith("granted:")), "granted: ?")
    slots = granted_slots(lines)
    state = register_grant(args, payloads, slots)
    os.makedirs(os.path.dirname(admitted_path(args.root, args.holder)), exist_ok=True)
    with open(admitted_path(args.root, args.holder), "w", encoding="utf-8") as fh:
        fh.write(f"{len(slots)} {granted_line}\n")
    print(admission_line(len(slots), args.provider, args.model, lines, total=len(rows)))
    print(admitted_json(state, payloads[:len(slots)], slots))
    print("prepare_wave: slots held; write the judgement fields, then run this command again without --admit-only")
    return 0


def admission_line(n: int, provider: str, model: str, lines: list[str], total: int | None = None) -> str:
    """Phase 4's `ADMISSION: granted <N> slots — <provider>/<model>, cap <C>, ceiling <K>`,
    with C and K read from spawn_admission.py's `granted:` line."""
    granted = next((l for l in lines if l.startswith("granted:")), "")
    cap = re.search(r"of cap (\d+)", granted)
    ceiling = re.search(r"ceiling ([^)]+)\)", granted)
    of = f" of {total}" if total and total != n else ""
    return (f"ADMISSION: granted {n}{of} slots — {provider}/{model}, "
            f"cap {cap.group(1) if cap else '?'}, ceiling {ceiling.group(1) if ceiling else '?'}")


def append(args: argparse.Namespace, lines: list[str]) -> None:
    rc, out = run([sys.executable, os.path.join(args.plugin_root, "scripts", "dossier_edit.py"), "log", args.log, *lines])
    print(("      " + out[-1]) if out else f"      dossier_edit exit {rc}")


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append(name + (": " + detail.strip()[-300:] if detail else ""))

    rows = parse_manifest("# wave 1\nkind=implementer payload=/a/P1.md\n"
                          "kind=unit-test-author payload='/a/U T.md' test-paths=t/a.rs,t/b.rs allow-path=/vm/x allow-path=/vm/y\n")
    check("two rows", len(rows) == 2)
    check("quoted path", rows[1]["payload"] == "/a/U T.md")
    check("repeated allow-path joins", rows[1]["allow-path"] == "/vm/x,/vm/y")
    for bad in ("kind=implementer\n", "kind=x payload=/p bogus=1\n", "# only a comment\n"):
        try:
            parse_manifest(bad)
            check(f"manifest refused: {bad!r}", False)
        except ValueError:
            pass
    check("admission line shape", admission_line(3, "zai", "glm", ["granted: zai/glm slots 1,2,3 (held 3 of cap 4, learned; ceiling 6)"]) == "ADMISSION: granted 3 slots — zai/glm, cap 4, ceiling 6")
    check("findings keep defects", findings(["payload lint: x", "  DEFECT   OWNED_PATHS missing", "PASS — 0"]) == ["DEFECT   OWNED_PATHS missing"])

    with tempfile.TemporaryDirectory() as tmp:
        check("first run fixes nothing", defects_fixed(tmp, "W-1-wave1", 3) == 0)
        check("second run counts the earlier defects", defects_fixed(tmp, "W-1-wave1", 0) == 3)

        # End to end against the real checker: a payload missing every field fails.
        payload = os.path.join(tmp, "P1.md")
        with open(payload, "w", encoding="utf-8") as fh:
            fh.write("WORKTREE_DIR: /nowhere\n")
        manifest = os.path.join(tmp, "wave.txt")
        with open(manifest, "w", encoding="utf-8") as fh:
            fh.write(f"kind=implementer payload={payload}\n")
        rc = main([manifest, "--host", "opencode", "--root", tmp])
        check("a defective payload fails the wave", rc == 1)
        check("a defective payload records no hash", not os.path.exists(hashes_path(tmp, None)))

        # #465: a hand-written payload of any kind (here a document-drafter) without its sentinel fails the wave.
        draft = os.path.join(tmp, "DD1.md")
        body = f"MODE: adr\nTARGET_PATHS: {tmp}/docs/adr-1.md\nFORMAT: |\n  adr\nSCRUB: |\n  ids\nDECISIONS: |\n  d1\n"
        with open(draft, "w", encoding="utf-8") as fh:
            fh.write(body)
        manifest_dd = os.path.join(tmp, "wave-dd.txt")
        with open(manifest_dd, "w", encoding="utf-8") as fh:
            fh.write(f"kind=document-drafter payload={draft}\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_unsealed = main([manifest_dd, "--host", "claude", "--root", tmp, "--lint-only"])
        check("#465: a payload with no PAYLOAD-END sentinel fails --lint-only, naming it", rc_unsealed == 1 and "PAYLOAD-END" in buf.getvalue())
        subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "compose_payloads.py"), "--seal", draft],
                       capture_output=True, text=True, check=True)
        with contextlib.redirect_stdout(io.StringIO()):
            rc_sealed = main([manifest_dd, "--host", "claude", "--root", tmp, "--lint-only"])
        check("#465: the same payload sealed with compose_payloads.py --seal passes --lint-only", rc_sealed == 0)

        # sha256 ledger: recorded, merged across calls, replaced on re-lint.
        other = os.path.join(tmp, "P2.md")
        with open(other, "w", encoding="utf-8") as fh:
            fh.write("a\n")
        path = record_hashes(tmp, "W-1-wave1", [payload, other])
        check("hashes file is wave-<holder>.hashes", path.endswith(os.path.join(".agent-staging", "wave-W-1-wave1.hashes")))
        entries = dict((l.split("  ", 1)[1], l.split("  ", 1)[0]) for l in open(path, encoding="utf-8").read().splitlines())
        check("hash is the payload's sha256", entries[other] == hashlib.sha256(b"a\n").hexdigest())
        check("every recorded hash is the full 64 hex characters", all(len(h) == 64 for h in entries.values()))
        with open(other, "w", encoding="utf-8") as fh:
            fh.write("b\n")
        record_hashes(tmp, "W-1-wave1", [other])
        entries = dict((l.split("  ", 1)[1], l.split("  ", 1)[0]) for l in open(path, encoding="utf-8").read().splitlines())
        check("a re-lint replaces its line and keeps the others", entries[other] == hashlib.sha256(b"b\n").hexdigest() and payload in entries)

        # --lint-only never acquires, even with provider/model/holder; a plain run does.
        module: Any = sys.modules[__name__]
        real_lint, real_acquire = module.lint_row, module.acquire
        calls: list[int] = []
        module.lint_row = lambda row, a: (True, [])
        module.acquire = lambda a, n, **kw: (calls.append(n) or (0, ["granted: p/m slots 1 (held 1 of cap 4, learned; ceiling 6)"]))
        try:
            admit = ["--provider", "p", "--model", "m", "--holder", "W-2-wave1"]
            rc = main([manifest, "--host", "opencode", "--root", tmp, *admit, "--lint-only"])
            check("--lint-only passes and acquires nothing", rc == 0 and calls == [])
            check("--lint-only still records the hash", os.path.exists(hashes_path(tmp, "W-2-wave1")))
            rc = main([manifest, "--host", "opencode", "--root", tmp, *admit])
            check("without --lint-only the same call acquires", rc == 0 and calls == [1])
        finally:
            module.lint_row, module.acquire = real_lint, real_acquire

        # --wait-slot polls a slots-only refusal, never a hopeless one.
        ns = argparse.Namespace(plugin_root=tmp, provider="p", model="m", holder="h", wait_slot=60, slot_poll=5)
        script = {"n": 0}

        def fake_run(cmd: list[str]) -> tuple[int, list[str]]:
            script["n"] += 1
            if script["n"] < 3:
                return 1, ["refused: 0 free of cap 2 (learned), asked 1; wait"]
            return 0, ["granted: p/m slots 1 (held 1 of cap 2, learned; ceiling 4)"]

        clock = {"t": 0.0}
        slept: list[float] = []
        rc, lines = acquire(ns, 1, runner=fake_run, sleep=lambda s: (slept.append(s), clock.__setitem__("t", clock["t"] + s)),
                            clock=lambda: clock["t"])
        check("--wait-slot polls until the slot frees", rc == 0 and script["n"] == 3 and slept == [5, 5])
        script["n"] = 0
        ns.wait_slot = 8
        rc, _ = acquire(ns, 1, runner=lambda c: (1, ["refused: 0 free of cap 2 (learned), asked 1; wait"]),
                        sleep=lambda s: clock.__setitem__("t", clock["t"] + s), clock=lambda: clock["t"])
        check("--wait-slot gives up after its seconds with 1", rc == 1)
        calls2: list[int] = []
        rc, _ = acquire(ns, 3, runner=lambda c: (calls2.append(1) or (1, ["refused: 2 free of cap 2 (learned), asked 3; split"])),
                        sleep=lambda s: calls2.append(99), clock=lambda: 0.0)
        check("asking more than the cap is not waited for", rc == 1 and calls2 == [1])
        ns.wait_slot = 0
        calls3: list[int] = []
        rc, _ = acquire(ns, 1, runner=lambda c: (calls3.append(1) or (1, ["refused: 0 free of cap 2 (learned), asked 1"])),
                        sleep=lambda s: calls3.append(99), clock=lambda: 0.0)
        check("without --wait-slot a refusal returns 1 at once", rc == 1 and calls3 == [1])
        ns.wait_slot = 60
        rc, _ = acquire(ns, 1, runner=lambda c: (1, ["deferred: p/m resets at 10:00"]),
                        sleep=lambda s: calls3.append(99), clock=lambda: 0.0)
        check("a deferral naming no parsable reset is never waited for", rc == 1 and 99 not in calls3)
        far = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + 7200))
        rc, _ = acquire(ns, 1, runner=lambda c: (1, [f"deferred: p/m resets at {far} (in 120 min)"]),
                        sleep=lambda s: calls3.append(99), clock=lambda: 0.0)
        check("a deferral past the bound returns 1 at once", rc == 1 and 99 not in calls3)
        soon = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + 20))
        runs = {"n": 0}

        def defer_then_grant(cmd: list[str]) -> tuple[int, list[str]]:
            runs["n"] += 1
            return (1, [f"deferred: p/m resets at {soon} (in 0 min)"]) if runs["n"] < 2 else (0, ["granted: p/m slots 1 (held 1 of cap 2, learned; ceiling 4)"])

        slept2: list[float] = []
        with contextlib.redirect_stdout(io.StringIO()):
            rc, _ = acquire(ns, 1, runner=defer_then_grant, sleep=slept2.append, clock=lambda: 0.0)
        check("a deferral resetting inside the bound is waited out", rc == 0 and runs["n"] == 2 and bool(slept2))

        # --admit-only takes the slots before lint; the normal call reuses the grant.
        module.acquire = lambda a, n, **kw: (calls.append(n) or (0, ["granted: p/m slots 1 (held 1 of cap 4, learned; ceiling 6)"]))
        module.lint_row = lambda row, a: (True, [])
        calls.clear()
        try:
            admit2 = ["--provider", "p", "--model", "m", "--holder", "W-3-wave1"]
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main([manifest, "--host", "opencode", "--root", tmp, *admit2, "--admit-only"])
            check("--admit-only acquires without linting", rc == 0 and calls == [1] and os.path.exists(admitted_path(tmp, "W-3-wave1")))
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main([manifest, "--host", "opencode", "--root", tmp, *admit2])
            check("the next normal call reuses the grant, never acquires again", rc == 0 and calls == [1])
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main([manifest, "--host", "opencode", "--root", tmp, "--admit-only", *admit2])
            check("a second --admit-only for the same holder does not acquire", calls == [1])
        finally:
            module.lint_row, module.acquire = real_lint, real_acquire

        # Partial admission, --admit-next and a deferral, against a real temporary admission dir.
        saved_dir = os.environ.get("ASD_ADMISSION_DIR")
        os.environ["ASD_ADMISSION_DIR"] = os.path.join(tmp, "admission")
        try:
            sa_cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "spawn_admission.py")]

            def sa(*a: str) -> tuple[int, str]:
                r = subprocess.run(sa_cmd + list(a), capture_output=True, text=True)
                return r.returncode, r.stdout

            def go(argv: list[str]) -> tuple[int, str, dict]:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    code = main(argv)
                text = buf.getvalue()
                found = {}
                for line in text.splitlines():
                    for tag in ("ADMITTED ", "ADMIT-NEXT "):
                        if line.strip().startswith(tag):
                            found = json.loads(line.strip()[len(tag):])
                return code, text, found

            pm = ["--provider", "pp", "--model", "mm"]
            four = os.path.join(tmp, "four.manifest")
            pls = [os.path.join(tmp, f"Q{i}.md") for i in range(1, 5)]
            with open(four, "w", encoding="utf-8") as fh:
                fh.write("".join(f"kind=implementer payload={q}\n" for q in pls))
            sa("acquire", *pm, "--n", "2", "--holder", "other")
            base = [four, "--host", "opencode", "--root", tmp, *pm, "--holder", "W-4-wave1", "--wait-slot", "0"]
            rc, text, got = go(base + ["--admit-only"])
            check("partial: 2 of 4 admitted now, the rest queued",
                  rc == 0 and [a["payload"] for a in got.get("admitted", [])] == pls[:2] and got.get("queued") == pls[2:], text)
            check("partial: one distinct slot per admitted payload", len({a["slot"] for a in got.get("admitted", [])}) == 2)
            check("partial: the ADMISSION line says 2 of 4", "granted 2 of 4 slots" in text, text)
            state = load_state(tmp, "W-4-wave1") or {}
            check("the slot map is recorded on disk", sorted(state.get("slots", {}).values()) == sorted(a["slot"] for a in got.get("admitted", [])) and state.get("queue") == pls[2:])
            rc, text, got2 = go(base + ["--admit-only"])
            check("a repeated --admit-only is idempotent and acquires nothing", rc == 0 and len(os.listdir(os.path.join(tmp, "admission", "pp__mm", "slots"))) == 4 and got2.get("queued") == pls[2:], text)
            nxt = ["--admit-next", "--root", tmp, *pm, "--holder", "W-4-wave1", "--wait-slot", "0"]
            rc, text, got3 = go(nxt)
            check("admit-next with zero free slots refuses and keeps the queue", rc == 1 and got3.get("status") == "refused" and got3.get("queued") == pls[2:], text)
            freed = sorted(state["slots"].values())[0]
            sa("release", *pm, "--holder", "W-4-wave1", "--slots", str(freed))
            rc, text, got3 = go(nxt)
            check("admit-next admits the next payload into the freed slot",
                  rc == 0 and got3.get("admitted") == [{"payload": pls[2], "slot": freed}] and got3.get("queued") == pls[3:], text)
            sa("release", *pm, "--holder", "other")
            rc, text, got3 = go(nxt + ["--max", "5"])
            check("admit-next drains the rest, never beyond the queue", rc == 0 and [a["payload"] for a in got3.get("admitted", [])] == [pls[3]] and got3.get("queued") == [], text)
            rc, text, got3 = go(nxt)
            check("admit-next on an empty queue is a no-op", rc == 0 and got3 == {"status": "ok", "admitted": [], "queued": []}, text)
            sa("release", *pm, "--holder", "W-4-wave1")
            # a deferral blocks admit-next: never spawn into a recorded reset window
            pd = ["--provider", "pd", "--model", "md"]
            sa("acquire", *pd, "--n", "3", "--holder", "other")
            rc, text, got = go([four, "--host", "opencode", "--root", tmp, *pd, "--holder", "W-5-wave1", "--wait-slot", "0", "--admit-only"])
            check("deferral setup: 1 admitted, 3 queued", rc == 0 and len(got.get("admitted", [])) == 1 and len(got.get("queued", [])) == 3, text)
            sa("record-error", *pd, "Limit Exhausted. Your limit will reset in 3 hours")
            sa("release", *pd, "--holder", "other")
            rc, text, got = go(["--admit-next", "--root", tmp, *pd, "--holder", "W-5-wave1", "--wait-slot", "0"])
            check("a deferral blocks admit-next even with free slots", rc == 1 and got.get("status") == "refused" and "deferred" in got.get("reason", "") and len(got.get("queued", [])) == 3, text)
            check("nothing was acquired by the blocked admit-next", len(os.listdir(os.path.join(tmp, "admission", "pd__md", "slots"))) == 1)
            # a request above the cap splits: never more than the cap is held
            pc = ["--provider", "pc", "--model", "mc"]
            six = os.path.join(tmp, "six.manifest")
            with open(six, "w", encoding="utf-8") as fh:
                fh.write("".join(f"kind=implementer payload={os.path.join(tmp, f'S{i}.md')}\n" for i in range(6)))
            rc, text, got = go([six, "--host", "opencode", "--root", tmp, *pc, "--holder", "W-6-wave1", "--wait-slot", "0", "--admit-only"])
            check("six asked above the cap of 4: 4 admitted, 2 queued", rc == 0 and len(got.get("admitted", [])) == 4 and len(got.get("queued", [])) == 2, text)
            # a re-spawn re-admits just that payload
            sa("release", *pc, "--holder", "W-6-wave1", "--slots", str(got["admitted"][0]["slot"]))
            one = os.path.join(tmp, "one.manifest")
            with open(one, "w", encoding="utf-8") as fh:
                fh.write(f"kind=implementer payload={got['admitted'][0]['payload']}\n")
            rc, text, got4 = go([one, "--host", "opencode", "--root", tmp, *pc, "--holder", "W-6-wave1", "--wait-slot", "0", "--admit-only"])
            check("a re-spawn is re-admitted into a free slot", rc == 0 and [a["payload"] for a in got4.get("admitted", [])] == [got["admitted"][0]["payload"]], text)
        finally:
            if saved_dir is None:
                os.environ.pop("ASD_ADMISSION_DIR", None)
            else:
                os.environ["ASD_ADMISSION_DIR"] = saved_dir

        # A failed wave prints one FIX LIST naming every defect, the seal step last.
        fl = fix_list([("/w/P1.md", ["DEFECT   the last non-empty line is not `PAYLOAD-END: P1 <8-hex hash>`: x; seal it with `compose_payloads.py --seal /w/P1.md`.",
                                      "DEFECT   TEST_COMMAND is still @@FILL_TEST_COMMAND@@"]),
                       ("/w/P2.md", ["DEFECT   line 3: 'OWNED_PATH' is not a field of implementer. did you mean OWNED_PATHS?"])], "/plug")
        check("fix list counts every defect of every payload", "3 defect(s) across 2 payload(s)" in fl[0])
        check("fix list puts the seal step last", "PAYLOAD-END" in fl[-1])
        check("fix list gives the exact cure", any("--set TEST_COMMAND=" in l for l in fl) and any("rename the field to OWNED_PATHS" in l for l in fl))

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
