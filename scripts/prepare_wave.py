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
        [--lint-only] [--wait-slot SECONDS [--slot-poll S=15]]
        [--log LIVE_DOSSIER]
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

`--wait-slot SECONDS` changes what a refusal for lack of slots means: instead
of returning 1 at once, it polls `spawn_admission.py acquire` every
`--slot-poll` seconds until the slots are free or SECONDS pass, printing one
line when it starts waiting. A refusal that waiting cannot cure (asked more
than the cap) and a provider deferral still return 1 immediately.

Every payload that passes lint has its sha256 recorded in
`<root>/.agent-staging/wave-<holder>.hashes` (`wave.hashes` without a holder),
one `<sha256>  <payload path>` line per payload, replaced on each lint. A spawn
can prove it ships the linted bytes by comparing `sha256sum` of the payload with
that line: a payload edited after its lint no longer matches it.

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
import hashlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time

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


def acquire(args: argparse.Namespace, n: int, runner=run, sleep=time.sleep, clock=time.monotonic) -> tuple[int, list[str]]:
    """spawn_admission.py acquire, polled while it refuses only for lack of
    slots and --wait-slot seconds remain. Returns (exit, output lines)."""
    cmd = [sys.executable, os.path.join(args.plugin_root, "scripts", "spawn_admission.py"),
           "acquire", "--provider", args.provider, "--model", args.model,
           "--n", str(n), "--holder", args.holder]
    deadline = clock() + (args.wait_slot or 0)
    announced = False
    while True:
        rc, lines = runner(cmd)
        if rc == 0:
            return rc, lines
        refused = next((l for l in lines if l.startswith("refused:")), "")
        m = re.search(r"(\d+) free of cap (\d+).*asked (\d+)", refused)
        curable = bool(m) and int(m.group(3)) <= int(m.group(2))
        if not (args.wait_slot and curable) or clock() >= deadline:
            return rc, lines
        if not announced:
            print(f"      waiting up to {args.wait_slot:.0f}s for {n} free slot(s): {refused}")
            announced = True
        sleep(max(min(args.slot_poll, deadline - clock()), 0.001))


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
    ap.add_argument("--wait-slot", type=float, default=0, metavar="SECONDS",
                    help="when admission is refused for lack of slots, poll until free or this long")
    ap.add_argument("--slot-poll", type=float, default=15, metavar="SECONDS")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not (args.manifest and args.host and args.root):
        ap.error("MANIFEST, --host and --root are required unless --selftest")
    admit = [args.provider, args.model, args.holder]
    if any(admit) and not all(admit):
        ap.error("--provider, --model and --holder go together")
    try:
        with open(args.manifest, encoding="utf-8") as fh:
            rows = parse_manifest(fh.read())
    except (OSError, ValueError) as err:
        print(f"prepare_wave: {err}", file=sys.stderr)
        return 2

    failed: list[str] = []
    linted: list[str] = []
    defect_count = 0
    for row in rows:
        ok, out = lint_row(row, args)
        name = os.path.basename(row["payload"])
        print(f"{'PASS' if ok else 'FAIL'}  {row['kind']}  {name}")
        for item in out:
            print(f"      {item}")
        if not ok:
            failed.append(name)
            defect_count += max(1, sum(1 for item in out if "DEFECT" in item or "REFUSED" in item))
        elif os.path.isfile(row["payload"]):
            linted.append(row["payload"])
    if linted:
        print(f"      sha256 of {len(linted)} linted payload(s) recorded in {record_hashes(args.root, args.holder, linted)}")

    fixed = defects_fixed(args.root, args.holder, defect_count)
    ids = ", ".join(os.path.splitext(os.path.basename(r["payload"]))[0] for r in rows)
    if failed:
        print(f"prepare_wave: {len(failed)} of {len(rows)} spawn(s) failed — fix them and re-run; nothing acquired")
        return 1

    log_lines = [f"PAYLOAD-LINT: {len(rows)} payloads, {fixed} defects fixed — {ids}"]
    if all(admit) and not args.lint_only:
        rc, lines = acquire(args, len(rows))
        for line in lines:
            print(f"      {line}")
        if rc != 0:
            print("prepare_wave: admission refused or deferred — see Phase 4's ladder; nothing spawned")
            if args.log:
                append(args, log_lines)
            for line in log_lines:
                print(line)
            return 1
        log_lines.append(admission_line(len(rows), args.provider, args.model, lines))
    if args.log:
        append(args, log_lines)
    for line in log_lines:
        print(line)
    return 0


def admission_line(n: int, provider: str, model: str, lines: list[str]) -> str:
    """Phase 4's `ADMISSION: granted <N> slots — <provider>/<model>, cap <C>, ceiling <K>`,
    with C and K read from spawn_admission.py's `granted:` line."""
    granted = next((l for l in lines if l.startswith("granted:")), "")
    cap = re.search(r"of cap (\d+)", granted)
    ceiling = re.search(r"ceiling ([^)]+)\)", granted)
    return (f"ADMISSION: granted {n} slots — {provider}/{model}, "
            f"cap {cap.group(1) if cap else '?'}, ceiling {ceiling.group(1) if ceiling else '?'}")


def append(args: argparse.Namespace, lines: list[str]) -> None:
    rc, out = run([sys.executable, os.path.join(args.plugin_root, "scripts", "dossier_edit.py"), "log", args.log, *lines])
    print(("      " + out[-1]) if out else f"      dossier_edit exit {rc}")


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

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

        # sha256 ledger: recorded, merged across calls, replaced on re-lint.
        other = os.path.join(tmp, "P2.md")
        with open(other, "w", encoding="utf-8") as fh:
            fh.write("a\n")
        path = record_hashes(tmp, "W-1-wave1", [payload, other])
        check("hashes file is wave-<holder>.hashes", path.endswith(os.path.join(".agent-staging", "wave-W-1-wave1.hashes")))
        entries = dict((l.split("  ", 1)[1], l.split("  ", 1)[0]) for l in open(path, encoding="utf-8").read().splitlines())
        check("hash is the payload's sha256", entries[other] == hashlib.sha256(b"a\n").hexdigest())
        with open(other, "w", encoding="utf-8") as fh:
            fh.write("b\n")
        record_hashes(tmp, "W-1-wave1", [other])
        entries = dict((l.split("  ", 1)[1], l.split("  ", 1)[0]) for l in open(path, encoding="utf-8").read().splitlines())
        check("a re-lint replaces its line and keeps the others", entries[other] == hashlib.sha256(b"b\n").hexdigest() and payload in entries)

        # --lint-only never acquires, even with provider/model/holder; a plain run does.
        module = sys.modules[__name__]
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
        check("a provider deferral is never waited for", rc == 1 and 99 not in calls3)

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
