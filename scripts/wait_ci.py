#!/usr/bin/env python3
"""Wait for a PR's CI checks and print a short digest instead of polling by hand.

The orchestrator was polling CI with `sleep` plus `gh pr checks` and `gh run
view`, by hand, on an expensive model that pays for its context again on
every step. This script makes the whole wait ONE call: it polls
`gh pr checks` until nothing is pending (or the timeout passes), printing at
most one line per state change so the caller's context stays small, then
prints one line per check and, for each failed GitHub Actions check, a
bounded digest of its failed-step log — reusing run_tests.py's own digest
logic, so the "cut every line, cap the failures" rule lives in one place.

# Usage

    wait_ci.py --pr N [--repo OWNER/NAME] [--timeout SECONDS=3600]
               [--interval SECONDS=30] [--lines N=20] [--line-chars N=300]
    wait_ci.py --selftest

Polling calls `gh pr checks <N> --json name,state,bucket,link,workflow`
(fields verified against `gh pr checks --help`); `bucket` is gh's own
pass/fail/pending/skipping/cancel classification of `state`, so this script
never has to guess at state strings itself. It backs off geometrically
between polls, capped at 4x `--interval`.

When polling ends, one line per check is printed (`bucket  name`), then for
every check whose bucket is `fail` or `cancel`: the run id (and job id, if
present) is read out of its GitHub Actions link
(`.../actions/runs/<id>/job/<job>`), `gh run view <id> [--job <job>]
--log-failed` is fetched, and the digest is printed along with the link. A
failed check with no such link (a non-Actions check) is listed with a note
instead.

Exit codes: 0 every check passed; 1 any check failed or was cancelled;
3 timed out with checks still pending; 2 unusable input or a `gh` error
(e.g. `gh pr checks` did not return JSON).

No third-party imports: this runs wherever python3 and gh do.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_tests  # noqa: E402  (must follow the sys.path fix-up above)

LINK_RE = re.compile(r"/actions/runs/(\d+)(?:/job/(\d+))?")
BAD_BUCKETS = ("fail", "cancel")

Runner = "callable[[list[str]], tuple[int, str, str]]"


def default_runner(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout, proc.stderr


def run_ref(link: str) -> tuple[str, str | None] | None:
    m = LINK_RE.search(link or "")
    if not m:
        return None
    return m.group(1), m.group(2)


def poll(
    runner: Runner,
    pr: int,
    repo: str | None,
    timeout: float,
    interval: float,
) -> tuple[str, list[dict], str]:
    """Poll until no check is pending or the timeout passes. Returns
    (outcome, checks, error) with outcome in {"done", "timeout", "error"}.
    Prints at most one line per state change — nothing else."""
    cmd = ["gh", "pr", "checks", str(pr), "--json", "name,state,bucket,link,workflow"]
    if repo:
        cmd += ["--repo", repo]
    deadline = time.monotonic() + timeout
    last_summary: str | None = None
    cur_interval = max(interval, 0.001)
    while True:
        rc, out, err = runner(cmd)
        try:
            checks = json.loads(out)
        except (json.JSONDecodeError, TypeError):
            detail = (err or out or "").strip()[:300]
            return "error", [], f"gh pr checks did not return JSON (exit {rc}): {detail}"
        if not isinstance(checks, list) or not checks:
            return "error", [], "gh pr checks returned no checks"

        done = sum(1 for c in checks if c.get("bucket") != "pending")
        failed = sum(1 for c in checks if c.get("bucket") in BAD_BUCKETS)
        summary = f"{done}/{len(checks)} done, {failed} failed"
        if summary != last_summary:
            print(summary)
            last_summary = summary

        if done == len(checks):
            return "done", checks, ""
        if time.monotonic() >= deadline:
            return "timeout", checks, ""
        time.sleep(cur_interval)
        cur_interval = min(cur_interval * 1.5, interval * 4)


def failure_digest(runner: Runner, check: dict, lines_n: int, line_chars: int) -> list[str]:
    ref = run_ref(check.get("link", ""))
    if not ref:
        return [f"  (no GitHub Actions run id in link) {check.get('link', '')}"]
    run_id, job_id = ref
    cmd = ["gh", "run", "view", run_id]
    if job_id:
        cmd += ["--job", job_id]
    cmd += ["--log-failed"]
    rc, out, err = runner(cmd)
    text = out or err or ""
    parser, totals, failures = run_tests.parse_log(text)
    body = run_tests.format_body(parser, totals, failures, 10, lines_n, line_chars, text)
    return [f"--- {check.get('name')} ---", *body, f"  link: {check.get('link', '')}"]


def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pr", type=int)
    ap.add_argument("--repo")
    ap.add_argument("--timeout", type=float, default=3600)
    ap.add_argument("--interval", type=float, default=30)
    ap.add_argument("--lines", type=int, default=20)
    ap.add_argument("--line-chars", type=int, default=300)
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.selftest:
        return selftest()
    if not args.pr:
        ap.error("--pr is required unless --selftest")

    active_runner = runner or default_runner
    outcome, checks, err = poll(active_runner, args.pr, args.repo, args.timeout, args.interval)
    if outcome == "error":
        print(f"wait_ci: {err}", file=sys.stderr)
        return 2

    lines = [f"{c.get('bucket', '?'):8} {c.get('name', '?')}" for c in checks]
    for c in checks:
        if c.get("bucket") in BAD_BUCKETS:
            lines += failure_digest(active_runner, c, args.lines, args.line_chars)
    print("\n".join(lines))

    if outcome == "timeout":
        print(f"wait_ci: timed out after {args.timeout:.0f}s with checks still pending", file=sys.stderr)
        return 3
    return 1 if any(c.get("bucket") in BAD_BUCKETS for c in checks) else 0


# -------------------------------------------------------------------- selftest


def selftest() -> int:
    import contextlib
    import io

    failures_list: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures_list.append(name)

    # 1. pending, then pass.
    state = {"n": 0}

    def runner_pass(cmd: list[str]) -> tuple[int, str, str]:
        if cmd[:3] == ["gh", "pr", "checks"]:
            state["n"] += 1
            if state["n"] == 1:
                return 0, json.dumps([{"name": "build", "state": "IN_PROGRESS", "bucket": "pending", "link": "", "workflow": "CI"}]), ""
            return 0, json.dumps([{"name": "build", "state": "SUCCESS", "bucket": "pass", "link": "https://github.com/o/r/actions/runs/111/job/222", "workflow": "CI"}]), ""
        return 1, "", "unexpected call"

    outcome, checks, err = poll(runner_pass, 5, None, timeout=5, interval=0.01)
    check("pending then pass reaches done", outcome == "done" and checks[0]["bucket"] == "pass")

    # 2. a failure, with a fake --log-failed body that includes a huge line.
    huge = "boom" * 20000

    def runner_fail(cmd: list[str]) -> tuple[int, str, str]:
        if cmd[:3] == ["gh", "pr", "checks"]:
            return 0, json.dumps([{
                "name": "test", "state": "FAILURE", "bucket": "fail",
                "link": "https://github.com/o/r/actions/runs/333/job/444", "workflow": "CI",
            }]), ""
        if cmd[:3] == ["gh", "run", "view"]:
            check("job id passed through", "--job" in cmd and "444" in cmd)
            return 0, f"FAILED tests/test_x.py::test_y - assert 1 == 2\n{huge}\n=== 1 failed, 3 passed in 0.5s ===\n", ""
        return 1, "", "unexpected call"

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--pr", "9"], runner=runner_fail)
    out = buf.getvalue()
    check("failure path exits 1", rc == 1)
    check("digest names the failing test", "test_y" in out)
    check("digest keeps the link", "actions/runs/333/job/444" in out)
    check("every line stays bounded", all(len(l) <= 302 for l in out.splitlines()))  # 300 chars + "  " prefix

    # 3. a check with no run-id link is reported, not crashed on.
    def runner_no_link(cmd: list[str]) -> tuple[int, str, str]:
        return 0, json.dumps([{"name": "external", "state": "FAILURE", "bucket": "fail", "link": "https://example.com/x", "workflow": ""}]), ""

    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        rc = main(["--pr", "9"], runner=runner_no_link)
    check("no run id is noted, not fatal", rc == 1 and "no GitHub Actions run id" in buf2.getvalue())

    # 4. timeout with a check still pending.
    def runner_pending(cmd: list[str]) -> tuple[int, str, str]:
        return 0, json.dumps([{"name": "slow", "state": "IN_PROGRESS", "bucket": "pending", "link": "", "workflow": "CI"}]), ""

    buf3 = io.StringIO()
    with contextlib.redirect_stdout(buf3):
        rc = main(["--pr", "9", "--timeout", "0.05", "--interval", "0.01"], runner=runner_pending)
    check("timeout exits 3", rc == 3)

    # 5. unusable input: gh does not return JSON.
    def runner_broken(cmd: list[str]) -> tuple[int, str, str]:
        return 1, "", "authentication required"

    buf4 = io.StringIO()
    with contextlib.redirect_stdout(buf4):
        rc = main(["--pr", "9"], runner=runner_broken)
    check("a gh error exits 2", rc == 2)

    check("run_ref parses run and job", run_ref("https://github.com/o/r/actions/runs/9/job/8") == ("9", "8"))
    check("run_ref parses run only", run_ref("https://github.com/o/r/actions/runs/9") == ("9", None))
    check("run_ref rejects a non-Actions link", run_ref("https://example.com/x") is None)

    for item in failures_list:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures_list)} failure(s)")
    return 1 if failures_list else 0


if __name__ == "__main__":
    sys.exit(main())
