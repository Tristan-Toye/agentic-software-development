#!/usr/bin/env python3
"""Wait for a PR's (or a pushed branch's) CI and print a short digest instead of polling by hand.

The orchestrator was polling CI with `sleep` plus `gh pr checks` and `gh run
view`, by hand, on an expensive model that pays for its context again on
every step. This script makes the whole wait ONE call: it polls
`gh pr checks` until nothing is pending (or the timeout passes), printing at
most one line per state change so the caller's context stays small, then
prints one line per check and, for each failed GitHub Actions check, a
bounded digest of its failed-step log — reusing run_tests.py's own digest
logic, so the "cut every line, cap the failures" rule lives in one place.

The recorded runs also did four things around that wait by hand: polled `gh
run list --branch B` for a branch with no PR yet, polled for an idle shared
self-hosted runner before opening the PR, fetched one job's log through `gh api
.../jobs/N/logs | sed ANSI | grep` (37 times), and re-ran failed jobs. Each is
a flag here.

# Usage

    wait_ci.py --pr N [--repo OWNER/NAME] [--timeout SECONDS=3600]
               [--interval SECONDS=30] [--lines N=20] [--line-chars N=300]
    wait_ci.py --branch NAME [--sha SHA] ...        find the PR (or the runs) by branch
    wait_ci.py --idle-first [--idle-timeout S=7200] ...   wait for a free runner first
    wait_ci.py (--pr N | --branch NAME) --job-log JOB --grep REGEX [--lines N=20]
    wait_ci.py (--pr N | --branch NAME) --rerun-failed [--rerun-settle S=20] ...
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
--log-failed` is fetched, ANSI colour codes are stripped, and the digest is
printed along with the link. A failed check with no such link (a non-Actions
check) is listed with a note instead.

`--branch NAME` finds the open PR whose head is NAME (`gh pr list --head`) and
follows its checks. With no PR yet it follows the branch's workflow runs
instead — those of the newest pushed sha, or of `--sha` — one line per run
(`gh run list --branch`, bucket derived from status/conclusion), and keeps
polling while the pushed branch has no run registered yet.

`--idle-first` waits until no workflow run of the repository is in progress or
queued (the shared self-hosted runner is single-file: a second suite queued
behind another one only times out), ignoring the branch's own runs, BEFORE the
`--timeout` clock starts; `--idle-timeout` bounds that wait (exit 3 when it
passes). With neither `--pr` nor `--branch` it only waits for idle and exits 0.

`--job-log JOB --grep REGEX` fetches ONE job's whole log once (`gh run view
--job --log`), strips ANSI, saves it under the temp dir (the path is printed)
and prints at most `--lines` of the lines matching REGEX, each cut at
`--line-chars`. JOB is a check name (exact first, then a case-insensitive
substring). It does not wait. Exit 0 when something matched, 1 when nothing
did, 2 when the job or its run cannot be found.

`--rerun-failed` re-runs the failed jobs once (`gh run rerun <id> --failed`, one
call per failed run), sleeps `--rerun-settle` seconds so GitHub registers the
new attempt, waits again with the same `--timeout`, and prints the second
round's verdict. It never reruns twice.

Exit codes: 0 every check passed; 1 any check failed or was cancelled;
3 timed out with checks still pending (or the idle wait timed out); 2 unusable
input or a `gh` error (e.g. `gh pr checks` did not return JSON).

No third-party imports: this runs wherever python3 and gh do.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_tests  # noqa: E402  (must follow the sys.path fix-up above)

LINK_RE = re.compile(r"/actions/runs/(\d+)(?:/job/(\d+))?")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
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


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def with_repo(cmd: list[str], repo: str | None) -> list[str]:
    return cmd + (["--repo", repo] if repo else [])


# ------------------------------------------------------------------ targets


def bucket_of_run(run: dict) -> str:
    """gh run list's status/conclusion -> the bucket vocabulary `gh pr checks` uses."""
    if run.get("status") != "completed":
        return "pending"
    conclusion = run.get("conclusion") or ""
    if conclusion == "success":
        return "pass"
    if conclusion in ("skipped", "neutral"):
        return "skipping"
    if conclusion == "cancelled":
        return "cancel"
    return "fail"


def runs_as_checks(runs: list[dict], sha: str | None) -> list[dict]:
    """The workflow runs of one sha (the given one, else the newest run's), as check dicts."""
    if not runs:
        return []
    want = sha or runs[0].get("headSha")
    return [
        {"name": r.get("workflowName") or r.get("name") or "run", "state": r.get("status", ""),
         "bucket": bucket_of_run(r), "link": r.get("url", ""), "workflow": r.get("workflowName", "")}
        for r in runs if r.get("headSha") == want or (sha and str(r.get("headSha", "")).startswith(sha))
    ]


def fetch_checks(runner: Runner, target, repo: str | None) -> tuple[list[dict] | None, str]:
    """(checks, error). A target is a PR number, ("pr", N) or ("branch", NAME, SHA)."""
    if isinstance(target, int):
        target = ("pr", target)
    if target[0] == "pr":
        cmd = with_repo(["gh", "pr", "checks", str(target[1]), "--json", "name,state,bucket,link,workflow"], repo)
        rc, out, err = runner(cmd)
        try:
            checks = json.loads(out)
        except (json.JSONDecodeError, TypeError):
            detail = (err or out or "").strip()[:300]
            return None, f"gh pr checks did not return JSON (exit {rc}): {detail}"
        if not isinstance(checks, list) or not checks:
            return None, "gh pr checks returned no checks"
        return checks, ""
    cmd = with_repo(["gh", "run", "list", "--branch", target[1], "--limit", "30", "--json",
                     "databaseId,status,conclusion,workflowName,headSha,url"], repo)
    rc, out, err = runner(cmd)
    try:
        runs = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        detail = (err or out or "").strip()[:300]
        return None, f"gh run list did not return JSON (exit {rc}): {detail}"
    return runs_as_checks(runs if isinstance(runs, list) else [], target[2]), ""


def resolve_target(runner: Runner, args: argparse.Namespace) -> tuple[object, str]:
    """(target, error): --pr as given, or --branch -> the open PR, else the branch's runs."""
    if args.pr:
        return ("pr", args.pr), ""
    cmd = with_repo(["gh", "pr", "list", "--head", args.branch, "--state", "open", "--json", "number", "--limit", "1"], args.repo)
    rc, out, err = runner(cmd)
    try:
        prs = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return None, f"gh pr list did not return JSON (exit {rc}): {(err or out or '').strip()[:300]}"
    if prs:
        print(f"PR #{prs[0]['number']} for branch {args.branch}")
        return ("pr", int(prs[0]["number"])), ""
    print(f"no open PR for branch {args.branch}; following its workflow runs")
    return ("branch", args.branch, args.sha), ""


# --------------------------------------------------------------------- idle


def own_branch_of(runner: Runner, args: argparse.Namespace) -> str | None:
    if args.branch:
        return args.branch
    if args.pr:
        rc, out, _ = runner(with_repo(["gh", "pr", "view", str(args.pr), "--json", "headRefName", "--jq", ".headRefName"], args.repo))
        return out.strip() or None if rc == 0 else None
    return None


def busy_runs(runner: Runner, repo: str | None, own: str | None) -> list[str]:
    """Ids of in-progress or queued runs that are not the branch's own."""
    ids: list[str] = []
    for status in ("in_progress", "queued"):
        rc, out, _ = runner(with_repo(["gh", "run", "list", "--status", status, "--limit", "50", "--json",
                                       "databaseId,headBranch,status"], repo))
        try:
            rows = json.loads(out)
        except (json.JSONDecodeError, TypeError):
            continue
        ids += [str(r.get("databaseId")) for r in rows if own is None or r.get("headBranch") != own]
    return ids


def wait_idle(runner: Runner, args: argparse.Namespace) -> int:
    own = own_branch_of(runner, args)
    deadline = time.monotonic() + args.idle_timeout
    last: str | None = None
    pause = max(args.interval, 0.001)
    while True:
        ids = busy_runs(runner, args.repo, own)
        line = f"runner busy: {len(ids)} run(s) ahead ({', '.join(ids[:5])}{'...' if len(ids) > 5 else ''})" if ids else "runner idle"
        if line != last:
            print(line)
            last = line
        if not ids:
            return 0
        if time.monotonic() >= deadline:
            print(f"wait_ci: runner still busy after {args.idle_timeout:.0f}s", file=sys.stderr)
            return 3
        time.sleep(pause)


# --------------------------------------------------------------------- poll


def poll(
    runner: Runner,
    target,
    repo: str | None,
    timeout: float,
    interval: float,
) -> tuple[str, list[dict], str]:
    """Poll until no check is pending or the timeout passes. Returns
    (outcome, checks, error) with outcome in {"done", "timeout", "error"}.
    Prints at most one line per state change — nothing else."""
    deadline = time.monotonic() + timeout
    last_summary: str | None = None
    cur_interval = max(interval, 0.001)
    is_branch = not isinstance(target, int) and target[0] == "branch"
    while True:
        checks, err = fetch_checks(runner, target, repo)
        if checks is None:
            return "error", [], err
        if not checks and not is_branch:
            return "error", [], "gh pr checks returned no checks"

        done = sum(1 for c in checks if c.get("bucket") != "pending")
        failed = sum(1 for c in checks if c.get("bucket") in BAD_BUCKETS)
        summary = f"{done}/{len(checks)} done, {failed} failed" if checks else "no runs yet"
        if summary != last_summary:
            print(summary)
            last_summary = summary

        if checks and done == len(checks):
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
    text = strip_ansi(out or err or "")
    parser, totals, failures = run_tests.parse_log(text)
    body = run_tests.format_body(parser, totals, failures, 10, lines_n, line_chars, text)
    return [f"--- {check.get('name')} ---", *body, f"  link: {check.get('link', '')}"]


def report(runner: Runner, checks: list[dict], args: argparse.Namespace) -> None:
    lines = [f"{c.get('bucket', '?'):8} {c.get('name', '?')}" for c in checks]
    for c in checks:
        if c.get("bucket") in BAD_BUCKETS:
            lines += failure_digest(runner, c, args.lines, args.line_chars)
    print("\n".join(lines))


# ------------------------------------------------------------------ job log


def find_job(runner: Runner, checks: list[dict], name: str) -> tuple[str, str] | None:
    """(run id, job id) of the check called `name` (exact, then substring)."""
    wanted = name.lower()
    matches = [c for c in checks if str(c.get("name", "")).lower() == wanted]
    matches = matches or [c for c in checks if wanted in str(c.get("name", "")).lower()]
    for c in matches:
        ref = run_ref(c.get("link", ""))
        if ref and ref[1]:
            return ref[0], ref[1]
    # A run-level check (a branch with no PR): look the job up inside each run.
    for c in checks:
        ref = run_ref(c.get("link", ""))
        if not ref:
            continue
        rc, out, _ = runner(["gh", "run", "view", ref[0], "--json", "jobs"])
        try:
            jobs = json.loads(out).get("jobs", [])
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue
        for j in jobs:
            if wanted in str(j.get("name", "")).lower():
                return ref[0], str(j.get("databaseId"))
    return None


def job_log(runner: Runner, checks: list[dict], args: argparse.Namespace) -> int:
    try:
        pattern = re.compile(args.grep)
    except re.error as err:
        print(f"wait_ci: bad --grep: {err}", file=sys.stderr)
        return 2
    found = find_job(runner, checks, args.job_log)
    if not found:
        print(f"wait_ci: no job matching {args.job_log!r} among: {', '.join(str(c.get('name')) for c in checks)[:300]}", file=sys.stderr)
        return 2
    run_id, job_id = found
    rc, out, err = runner(with_repo(["gh", "run", "view", run_id, "--job", job_id, "--log"], args.repo))
    if rc != 0 and not out:
        print(f"wait_ci: could not fetch the log of job {job_id}: {(err or '').strip()[:200]}", file=sys.stderr)
        return 2
    text = strip_ansi(out)
    path = os.path.join(tempfile.gettempdir(), f"wait_ci-job-{job_id}.log")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    hits = [l for l in text.splitlines() if pattern.search(l)]
    shown = hits[: args.lines]
    print(f"job {args.job_log} (run {run_id}, job {job_id}): {len(hits)} line(s) match /{args.grep}/, showing {len(shown)}; log {path}")
    for l in shown:
        print("  " + run_tests.cut(l, args.line_chars))
    if len(hits) > len(shown):
        print(f"  (+{len(hits) - len(shown)} more, see log)")
    return 0 if hits else 1


# -------------------------------------------------------------------- rerun


def rerun_failed(runner: Runner, checks: list[dict], repo: str | None) -> list[str]:
    """Re-run the failed jobs of every failed run, once each. Returns the run ids."""
    ids: list[str] = []
    for c in checks:
        if c.get("bucket") not in BAD_BUCKETS:
            continue
        ref = run_ref(c.get("link", ""))
        if ref and ref[0] not in ids:
            ids.append(ref[0])
    for run_id in ids:
        runner(with_repo(["gh", "run", "rerun", run_id, "--failed"], repo))
    return ids


def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pr", type=int)
    ap.add_argument("--branch", help="find the PR (or the workflow runs) by branch name")
    ap.add_argument("--sha", help="with --branch and no PR: the pushed sha whose runs to follow")
    ap.add_argument("--repo")
    ap.add_argument("--timeout", type=float, default=3600)
    ap.add_argument("--interval", type=float, default=30)
    ap.add_argument("--lines", type=int, default=20)
    ap.add_argument("--line-chars", type=int, default=300)
    ap.add_argument("--idle-first", action="store_true", help="wait for a free shared runner before the timeout counts")
    ap.add_argument("--idle-timeout", type=float, default=7200)
    ap.add_argument("--job-log", metavar="JOB", help="print matching lines of one job's log (needs --grep)")
    ap.add_argument("--grep", metavar="REGEX", help="with --job-log: the pattern")
    ap.add_argument("--rerun-failed", action="store_true", help="re-run the failed jobs once and wait again")
    ap.add_argument("--rerun-settle", type=float, default=20, help="seconds to let GitHub register the rerun")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.selftest:
        return selftest()
    if args.pr and args.branch:
        ap.error("--pr and --branch are alternatives")
    if args.job_log and not args.grep:
        ap.error("--job-log needs --grep")
    if not (args.pr or args.branch or args.idle_first):
        ap.error("--pr or --branch is required unless --selftest (or --idle-first alone)")

    active_runner = runner or default_runner
    if args.idle_first:
        rc = wait_idle(active_runner, args)
        if rc:
            return rc
        if not (args.pr or args.branch):
            return 0

    target, err = resolve_target(active_runner, args)
    if target is None:
        print(f"wait_ci: {err}", file=sys.stderr)
        return 2

    if args.job_log:
        checks, err = fetch_checks(active_runner, target, args.repo)
        if checks is None:
            print(f"wait_ci: {err}", file=sys.stderr)
            return 2
        return job_log(active_runner, checks, args)

    outcome, checks, err = poll(active_runner, target, args.repo, args.timeout, args.interval)
    if outcome == "error":
        print(f"wait_ci: {err}", file=sys.stderr)
        return 2

    if outcome == "done" and args.rerun_failed and any(c.get("bucket") in BAD_BUCKETS for c in checks):
        failed_names = [str(c.get("name")) for c in checks if c.get("bucket") in BAD_BUCKETS]
        ids = rerun_failed(active_runner, checks, args.repo)
        print(f"rerun: failed {', '.join(failed_names)}; re-ran run(s) {', '.join(ids) or 'none (no run id in the links)'}")
        if ids:
            time.sleep(max(args.rerun_settle, 0))
            outcome, checks, err = poll(active_runner, target, args.repo, args.timeout, args.interval)
            if outcome == "error":
                print(f"wait_ci: {err}", file=sys.stderr)
                return 2
            print("after the rerun:")

    report(active_runner, checks, args)

    if outcome == "timeout":
        print(f"wait_ci: timed out after {args.timeout:.0f}s with checks still pending", file=sys.stderr)
        return 3
    if not checks:
        print("wait_ci: no workflow run appeared for the branch", file=sys.stderr)
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
            return 0, f"\x1b[31mFAILED tests/test_x.py::test_y - assert 1 == 2\x1b[0m\n{huge}\n=== 1 failed, 3 passed in 0.5s ===\n", ""
        return 1, "", "unexpected call"

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--pr", "9"], runner=runner_fail)
    out = buf.getvalue()
    check("failure path exits 1", rc == 1)
    check("digest names the failing test", "test_y" in out)
    check("digest keeps the link", "actions/runs/333/job/444" in out)
    check("every line stays bounded", all(len(l) <= 302 for l in out.splitlines()))  # 300 chars + "  " prefix
    check("ANSI is stripped from the digest", "\x1b" not in out)

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

    # 6. --branch with an open PR follows the PR's checks.
    calls: list[list[str]] = []

    def runner_branch_pr(cmd: list[str]) -> tuple[int, str, str]:
        calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "list"]:
            return 0, json.dumps([{"number": 77}]), ""
        if cmd[:3] == ["gh", "pr", "checks"]:
            return 0, json.dumps([{"name": "build", "state": "SUCCESS", "bucket": "pass", "link": "", "workflow": "CI"}]), ""
        return 1, "", "unexpected call"

    buf5 = io.StringIO()
    with contextlib.redirect_stdout(buf5):
        rc = main(["--branch", "feat/x"], runner=runner_branch_pr)
    check("--branch resolves the PR and passes", rc == 0 and "PR #77 for branch feat/x" in buf5.getvalue())
    check("--branch asks for the open PR by head", any(c[:5] == ["gh", "pr", "list", "--head", "feat/x"] for c in calls))

    # 7. --branch with no PR follows the runs; a run registers late; then one fails.
    n_list = {"n": 0}

    def runner_branch_runs(cmd: list[str]) -> tuple[int, str, str]:
        if cmd[:3] == ["gh", "pr", "list"]:
            return 0, "[]", ""
        if cmd[:3] == ["gh", "run", "list"]:
            n_list["n"] += 1
            if n_list["n"] == 1:
                return 0, "[]", ""  # push not registered yet
            return 0, json.dumps([
                {"databaseId": 5, "status": "completed", "conclusion": "failure", "workflowName": "CI", "headSha": "abc", "url": "https://github.com/o/r/actions/runs/5"},
                {"databaseId": 4, "status": "completed", "conclusion": "success", "workflowName": "CI", "headSha": "old", "url": "https://github.com/o/r/actions/runs/4"},
            ]), ""
        if cmd[:3] == ["gh", "run", "view"]:
            return 0, "FAILED tests/test_q.py::test_r - nope\n=== 1 failed in 0.1s ===\n", ""
        return 1, "", "unexpected call"

    buf6 = io.StringIO()
    with contextlib.redirect_stdout(buf6):
        rc = main(["--branch", "feat/y", "--interval", "0.01", "--timeout", "5"], runner=runner_branch_runs)
    out6 = buf6.getvalue()
    check("--branch with no PR waits for a run and reports its failure", rc == 1 and "no runs yet" in out6 and "fail     CI" in out6)
    check("--branch keeps only the newest sha's runs", "old" not in out6 and out6.count("CI") >= 1 and "test_r" in out6)
    check("bucket_of_run maps statuses", [bucket_of_run(r) for r in (
        {"status": "in_progress"}, {"status": "completed", "conclusion": "success"},
        {"status": "completed", "conclusion": "cancelled"}, {"status": "completed", "conclusion": "timed_out"},
        {"status": "completed", "conclusion": "skipped"})] == ["pending", "pass", "cancel", "fail", "skipping"])

    # 8. --idle-first waits for another branch's run to end, ignoring the branch's own.
    n_idle = {"n": 0}

    def runner_idle(cmd: list[str]) -> tuple[int, str, str]:
        if cmd[:3] == ["gh", "run", "list"] and "--status" in cmd:
            status = cmd[cmd.index("--status") + 1]
            if status == "in_progress":
                n_idle["n"] += 1
                rows = [{"databaseId": 1, "headBranch": "feat/z", "status": "in_progress"}]  # our own: ignored
                if n_idle["n"] < 3:
                    rows.append({"databaseId": 2, "headBranch": "other", "status": "in_progress"})
                return 0, json.dumps(rows), ""
            return 0, "[]", ""
        if cmd[:3] == ["gh", "pr", "list"]:
            return 0, json.dumps([{"number": 3}]), ""
        if cmd[:3] == ["gh", "pr", "checks"]:
            return 0, json.dumps([{"name": "b", "state": "SUCCESS", "bucket": "pass", "link": "", "workflow": "CI"}]), ""
        return 1, "", "unexpected call"

    buf7 = io.StringIO()
    with contextlib.redirect_stdout(buf7):
        rc = main(["--branch", "feat/z", "--idle-first", "--interval", "0.01"], runner=runner_idle)
    out7 = buf7.getvalue()
    check("--idle-first waits out the other run, then proceeds", rc == 0 and "runner busy: 1 run(s) ahead (2)" in out7 and "runner idle" in out7)
    check("--idle-first alone only waits", main(["--idle-first", "--interval", "0.01"], runner=lambda c: (0, "[]", "")) == 0)
    always_busy = lambda c: (0, json.dumps([{"databaseId": 9, "headBranch": "o", "status": "in_progress"}]), "")  # noqa: E731
    with contextlib.redirect_stdout(io.StringIO()):
        rc = main(["--idle-first", "--interval", "0.01", "--idle-timeout", "0.05"], runner=always_busy)
    check("--idle-first times out with 3", rc == 3)

    # 9. --job-log: one job's log, ANSI stripped, matches bounded.
    log_body = "\n".join([f"\x1b[1m2026 step{i}\x1b[0m ok" for i in range(50)] + [f"2026 thread 'x{i}' panicked at src/a.rs:{i}" for i in range(30)] + ["z" * 5000 + " panicked"])

    def runner_joblog(cmd: list[str]) -> tuple[int, str, str]:
        if cmd[:3] == ["gh", "pr", "checks"]:
            return 0, json.dumps([
                {"name": "Rust", "state": "SUCCESS", "bucket": "pass", "link": "https://github.com/o/r/actions/runs/10/job/11", "workflow": "CI"},
                {"name": "Stack suite", "state": "FAILURE", "bucket": "fail", "link": "https://github.com/o/r/actions/runs/10/job/12", "workflow": "CI"},
            ]), ""
        if cmd[:3] == ["gh", "run", "view"] and "--json" in cmd:
            return 0, json.dumps({"jobs": [{"name": "Rust", "databaseId": 11}]}), ""  # the run-level fallback lookup
        if cmd[:3] == ["gh", "run", "view"]:
            check("job-log fetches the whole log of the matched job", "--job" in cmd and "12" in cmd and "--log" in cmd and "--log-failed" not in cmd)
            return 0, log_body, ""
        return 1, "", "unexpected call"

    buf8 = io.StringIO()
    with contextlib.redirect_stdout(buf8):
        rc = main(["--pr", "9", "--job-log", "stack", "--grep", "panicked", "--lines", "5"], runner=runner_joblog)
    out8 = buf8.getvalue()
    check("--job-log prints the match count and a bounded sample", rc == 0 and "31 line(s) match" in out8 and "showing 5" in out8 and "(+26 more" in out8)
    check("--job-log output is bounded and ANSI-free", len(out8.splitlines()) <= 8 and "\x1b" not in out8 and all(len(l) <= 302 for l in out8.splitlines()[1:]))
    with contextlib.redirect_stdout(io.StringIO()):
        rc = main(["--pr", "9", "--job-log", "stack", "--grep", "NEVER-THERE"], runner=runner_joblog)
    check("--job-log with no match exits 1", rc == 1)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc = main(["--pr", "9", "--job-log", "nonexistent", "--grep", "x"], runner=runner_joblog)
    check("--job-log for an unknown job exits 2", rc == 2)

    # 10. --rerun-failed: fail, rerun once, second round passes.
    state_rr = {"checks": 0, "reruns": []}

    def runner_rerun(cmd: list[str]) -> tuple[int, str, str]:
        if cmd[:3] == ["gh", "pr", "checks"]:
            state_rr["checks"] += 1
            if state_rr["reruns"]:
                return 0, json.dumps([{"name": "Stack", "state": "SUCCESS", "bucket": "pass", "link": "https://github.com/o/r/actions/runs/20/job/21", "workflow": "CI"}]), ""
            return 0, json.dumps([{"name": "Stack", "state": "FAILURE", "bucket": "fail", "link": "https://github.com/o/r/actions/runs/20/job/21", "workflow": "CI"}]), ""
        if cmd[:3] == ["gh", "run", "rerun"]:
            state_rr["reruns"].append(cmd)
            return 0, "", ""
        if cmd[:3] == ["gh", "run", "view"]:
            return 0, "", ""
        return 1, "", "unexpected call"

    buf9 = io.StringIO()
    with contextlib.redirect_stdout(buf9):
        rc = main(["--pr", "9", "--rerun-failed", "--rerun-settle", "0", "--interval", "0.01"], runner=runner_rerun)
    out9 = buf9.getvalue()
    check("--rerun-failed reruns the failed run once with --failed", len(state_rr["reruns"]) == 1 and state_rr["reruns"][0][3:] == ["20", "--failed"])
    check("--rerun-failed reports the second round's pass", rc == 0 and "rerun: failed Stack; re-ran run(s) 20" in out9 and "after the rerun:" in out9)

    # 11. --rerun-failed does not rerun a green PR; --pr and --job-log without --grep are refused.
    state_rr["reruns"].clear()
    state_rr["reruns"].append("already")  # makes the fake return green
    with contextlib.redirect_stdout(io.StringIO()):
        rc = main(["--pr", "9", "--rerun-failed", "--interval", "0.01"], runner=runner_rerun)
    check("a green run is not rerun", rc == 0 and len(state_rr["reruns"]) == 1)

    for item in failures_list:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures_list)} failure(s)")
    return 1 if failures_list else 0


if __name__ == "__main__":
    sys.exit(main())
