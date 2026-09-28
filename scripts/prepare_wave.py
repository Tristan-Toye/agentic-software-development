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
        [--log LIVE_DOSSIER]
    prepare_wave.py --selftest

Without `--provider/--model/--holder` it only lints (a re-check after a fix).
With them, and only when no spawn has a defect, it acquires one slot per spawn
and prints the `ADMISSION:` line. `--log` appends `PAYLOAD-LINT:` (and
`ADMISSION:` when taken) to the dossier's `## Build log` through
`dossier_edit.py`, so the dossier never has to be opened for it.

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
import os
import re
import shlex
import subprocess
import sys
import tempfile

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

    fixed = defects_fixed(args.root, args.holder, defect_count)
    ids = ", ".join(os.path.splitext(os.path.basename(r["payload"]))[0] for r in rows)
    if failed:
        print(f"prepare_wave: {len(failed)} of {len(rows)} spawn(s) failed — fix them and re-run; nothing acquired")
        return 1

    log_lines = [f"PAYLOAD-LINT: {len(rows)} payloads, {fixed} defects fixed — {ids}"]
    if all(admit):
        rc, lines = run([sys.executable, os.path.join(args.plugin_root, "scripts", "spawn_admission.py"),
                         "acquire", "--provider", args.provider, "--model", args.model,
                         "--n", str(len(rows)), "--holder", args.holder])
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

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
