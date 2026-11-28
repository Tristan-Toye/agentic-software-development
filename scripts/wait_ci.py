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
               [--interval SECONDS=30] [--register-wait SECONDS=120]
               [--lines N=20] [--line-chars N=300] [--log DOSSIER]
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

Right after a push, `gh pr checks` can report no checks at all (`no checks
reported on the '<branch>' branch`, or an empty list) until GitHub registers
the workflow runs. That is not an error: polling continues at the current
interval for up to `--register-wait` seconds (capped by `--timeout`) and only
then fails. Any other `gh` error still fails fast.

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

In `--pr` mode (and `--branch` once it resolves to a PR) ONE `gh pr view <N>
--json headRefOid,mergeStateStatus,mergeable,baseRefName` runs before polling
and serves two guards. A DIRTY merge state or a CONFLICTING PR fails fast at
once, without waiting out the window: GitHub suppresses `synchronize` events
for a conflicting PR, so no check will ever start — merge origin/<base> and
re-push. And before an all-green verdict is reported, the head commit the runs
behind the checks ran on (`gh run view <id> --json headSha`, at most three
distinct runs, read from the check links) must equal the PR's head: a green
on a superseded head is not a verdict (a push that fired no run leaves the
previous head's green as the newest result). One line names both SHAs. If the
`gh pr view` call fails, or a check has no Actions run link, that guard is
skipped and the wait behaves as before. The same `gh pr view` is repeated
every few polls (no output unless it trips: a PR that turns DIRTY mid-wait
exits 5) and once more when the result would be green, so the head compared
and the conflict guard are the ones at the END of the wait, not the start.

Exit codes: 0 every check passed; 1 any check failed or was cancelled;
3 timed out with checks still pending (or the idle wait timed out); 2 unusable
input or a `gh` error (e.g. `gh pr checks` did not return JSON);
4 the green checks ran on a head that is not the PR's current head (stale
green, refused; only when the PR cannot be re-triggered); 5 the PR is
CONFLICTING/DIRTY, so its checks will never start; 7 no run because the PR's
parent (the open PR whose head is this PR's base) is itself DIRTY/CONFLICTING:
GitHub starts no workflow for a stacked child then, so resolve the parent
(checked before any retrigger); 6 CI never started: the PR
head has no workflow run (a stale green on an older head, or no run at all
after the register wait) even after one retrigger; 8 a workflow file of the head
did not parse (GitHub names such a run by the file's path and starts none of its
jobs, so it never shows in `gh pr checks`): checked when no run appears (before
the parent check) and before an all-green verdict, since the workflows that did
parse can be green while the broken one never ran. Fix the YAML and push.

A head with no run is re-triggered ONCE per head sha, through the PR's own
state: `gh pr close N` then `gh pr reopen N` (fires `reopened`, recomputes
mergeability), never a force-push. The wait then continues inside the same
call. The attempt is recorded (a marker file per repo, PR and head sha under
`--state-dir`, default the temp dir), so a second identical failure on the same
head, in this call or a later one, exits 6 at once instead of looping.

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
WORKFLOW_PATH_RE = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")
DEFAULT_REGISTER_WAIT = 120.0
WATCH_EVERY = 4  # polls between merge-state re-checks while waiting
NOT_REGISTERED = "no checks registered yet"

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
            if "no checks reported" in detail.lower():
                return None, f"{NOT_REGISTERED}: {detail}"
            return None, f"gh pr checks did not return JSON (exit {rc}): {detail}"
        if not isinstance(checks, list) or not checks:
            return None, f"{NOT_REGISTERED}: gh pr checks returned no checks"
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


# ----------------------------------------------------------------- pr state


def pr_state(runner: Runner, number: int, repo: str | None) -> dict | None:
    """The PR's head sha, merge state, mergeability and base branch, from ONE
    `gh pr view`; None when the call fails or is not a JSON object (the
    guards built on it are then skipped)."""
    rc, out, _ = runner(with_repo(["gh", "pr", "view", str(number), "--json",
                                   "headRefOid,mergeStateStatus,mergeable,baseRefName"], repo))
    if rc != 0:
        return None
    try:
        state = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return None
    return state if isinstance(state, dict) else None


def conflict_line(number: int, state: dict) -> str | None:
    """The fail-fast line when the PR is DIRTY or CONFLICTING, else None."""
    if state.get("mergeStateStatus") != "DIRTY" and state.get("mergeable") != "CONFLICTING":
        return None
    base = str(state.get("baseRefName") or "base")[:100]
    return (f"wait_ci: PR #{number} is {state.get('mergeStateStatus') or state.get('mergeable')} "
            f"(conflicts with {base}); GitHub suppresses synchronize events, so checks will never "
            f"start: merge origin/{base} and re-push")


def stale_head(runner: Runner, checks: list[dict], pr_head: str, repo: str | None) -> tuple[str, str] | None:
    """(run id, head sha) of the first run behind `checks` that ran on a head
    other than `pr_head`; None when every run checked matches or none could be read."""
    seen: list[str] = []
    for c in checks:
        ref = run_ref(c.get("link", ""))
        if ref and ref[0] not in seen:
            seen.append(ref[0])
    for run_id in seen[:3]:
        rc, out, _ = runner(with_repo(["gh", "run", "view", run_id, "--json", "headSha", "--jq", ".headSha"], repo))
        sha = out.strip() if rc == 0 else ""
        if re.fullmatch(r"[0-9a-fA-F]{7,64}", sha) and not (sha.startswith(pr_head) or pr_head.startswith(sha)):
            return run_id, sha
    return None


def marker_path(args: argparse.Namespace, number: int, head: str) -> str:
    key = re.sub(r"[^A-Za-z0-9]+", "_", f"{args.repo or 'cwd'}-{number}-{head[:12]}")
    return os.path.join(args.state_dir or tempfile.gettempdir(), f"wait_ci-retrigger-{key}")


def retrigger(runner: Runner, args: argparse.Namespace, number: int, head: str) -> None:
    """Close and reopen the PR (once per head: the marker is written first)."""
    with open(marker_path(args, number, head), "w", encoding="utf-8") as fh:
        fh.write(head + "\n")
    print(f"wait_ci: PR #{number} head {head[:12]} has no workflow run; closing and reopening the PR once to retrigger CI")
    runner(with_repo(["gh", "pr", "close", str(number)], args.repo))
    runner(with_repo(["gh", "pr", "reopen", str(number)], args.repo))
    time.sleep(max(args.rerun_settle, 0))


def await_head_run(runner: Runner, target, args: argparse.Namespace, head: str) -> bool:
    """After a retrigger: poll up to the register wait for checks that ran on `head`."""
    deadline = time.monotonic() + max(args.register_wait, 0)
    while True:
        checks, _ = fetch_checks(runner, target, args.repo)
        if checks and stale_head(runner, checks, head, args.repo) is None:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(args.interval, 0.001))


def parent_conflict(runner: Runner, state: dict, repo: str | None) -> tuple[int, str] | None:
    """(number, its base) of an open PR whose head branch is this PR's base (a stacked PR's parent) while that
    parent is DIRTY/CONFLICTING; None otherwise. GitHub starts no workflow for the child while it conflicts."""
    base = str(state.get("baseRefName") or "")
    if not base:
        return None
    rc, out, _ = runner(with_repo(["gh", "pr", "list", "--head", base, "--state", "open", "--json",
                                   "number,mergeStateStatus,mergeable,baseRefName"], repo))
    if rc != 0:
        return None
    try:
        rows = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return None
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and (row.get("mergeStateStatus") == "DIRTY" or row.get("mergeable") == "CONFLICTING"):
            return int(row.get("number") or 0), str(row.get("baseRefName") or "its base")[:100]
    return None


def parent_line(number: int, state: dict, parent: tuple[int, str]) -> str:
    return (f"wait_ci: CI not started: PR #{number} is stacked on #{parent[0]} (branch {str(state.get('baseRefName'))[:100]}), "
            f"which conflicts with {parent[1]}; GitHub starts no workflow for the child while its parent conflicts "
            f"(CI did not fail, it did not run). Resolve #{parent[0]}: merge {parent[1]} into it (the resolve-merge procedure) "
            f"and push; CI then starts for #{number}")


def invalid_workflows(runner: Runner, head: str, repo: str | None) -> list[tuple[str, str]]:
    """(workflow file, run link) for every workflow of `head` that GitHub could not parse. Such a run carries the file's
    path as its name (".github/workflows/ci.yml") instead of the `name:` inside it, fails at startup and starts no job,
    so `gh pr checks` lists nothing for it: it reads as "CI never started" (or, when another workflow ran, as green).
    Any gh error gives [] (the guard is skipped, the wait behaves as before)."""
    if not head:
        return []
    rc, out, _ = runner(with_repo(["gh", "run", "list", "--commit", head, "--limit", "50", "--json",
                                   "workflowName,status,conclusion,url"], repo))
    try:
        runs = json.loads(out) if rc == 0 else []
    except (json.JSONDecodeError, TypeError):
        return []
    bad: dict[str, str] = {}
    for r in runs if isinstance(runs, list) else []:
        name = str(r.get("workflowName") or "")
        if WORKFLOW_PATH_RE.match(name) and r.get("status") == "completed" and r.get("conclusion") == "failure":
            bad.setdefault(name, str(r.get("url") or ""))
    return sorted(bad.items())


def invalid_line(number: int, head: str, bad: list[tuple[str, str]]) -> str:
    files = ", ".join(f"{f} ({u})" if u else f for f, u in bad)
    return (f"wait_ci: workflow file invalid: {files} did not parse on PR #{number} head {head[:12]}, so GitHub started "
            "none of its jobs (CI did not run them; this is a red CI, not a wait). Fix the YAML on this branch (a common "
            "cause: an unquoted value containing ': ', e.g. a step `name: Check: x`), push, and wait on CI again")


def never_started(runner: Runner, args: argparse.Namespace, number: int, head: str, retriggered: bool) -> int:
    fresh = pr_state(runner, number, args.repo) or {}
    print(f"wait_ci: CI never started: PR #{number} head {head[:12]} has no workflow run"
          f"{' even after closing and reopening the PR once' if retriggered else ''}; CI did not fail, it did not run "
          f"(mergeable: {fresh.get('mergeable', 'unknown')}, mergeStateStatus: {fresh.get('mergeStateStatus', 'unknown')}). "
          "Waiting again will not help: report a blocker for the owner (GitHub-side, e.g. refs/pull/N/merge not recomputed)", file=sys.stderr)
    return 6


# --------------------------------------------------------------------- poll


def poll(
    runner: Runner,
    target,
    repo: str | None,
    timeout: float,
    interval: float,
    register_wait: float = DEFAULT_REGISTER_WAIT,
    watch=None,
    watch_every: int = WATCH_EVERY,
) -> tuple[str, list[dict], str]:
    """Poll until no check is pending or the timeout passes. Returns
    (outcome, checks, error) with outcome in {"done", "timeout", "error",
    "conflict"}. `watch`, when given, is called once every `watch_every`
    polls and returns a line (the poll then ends as "conflict" with that
    line as the error) or None; it prints nothing itself.
    A PR with no checks registered yet is polled on for `register_wait`
    seconds (never past the timeout) before it is an error.
    Prints at most one line per state change — nothing else."""
    deadline = time.monotonic() + timeout
    unregistered_since: float | None = None
    last_summary: str | None = None
    cur_interval = max(interval, 0.001)
    polls = 0
    while True:
        polls += 1
        if watch and polls % max(watch_every, 1) == 0:
            line = watch()
            if line:
                return "conflict", [], line
        checks, err = fetch_checks(runner, target, repo)
        if checks is None and err.startswith(NOT_REGISTERED):
            now = time.monotonic()
            unregistered_since = now if unregistered_since is None else unregistered_since
            if now - unregistered_since >= register_wait or now >= deadline:
                return "error", [], f"{err} (after {now - unregistered_since:.0f}s)"
            if last_summary != "waiting for checks to register":
                last_summary = "waiting for checks to register"
                print(last_summary)
            time.sleep(cur_interval)
            continue
        if checks is None:
            return "error", [], err
        unregistered_since = None

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


RUN_URL_RE = re.compile(r"https?://\S+?/actions/runs/\d+")


def ci_line(checks: list[dict], sha: str | None) -> str:
    """The build-log line phase-6.md asks for after a CI verdict:
    `SUITE: ci run <url> on <sha> — <N> failed / <N> passed` (counts are checks)."""
    failed = sum(1 for c in checks if c.get("bucket") in BAD_BUCKETS)
    passed = sum(1 for c in checks if c.get("bucket") == "pass")
    urls = [m.group(0) for c in checks if (m := RUN_URL_RE.search(str(c.get("link") or "")))]
    return f"SUITE: ci run {urls[0] if urls else 'n/a'} on {(sha or 'unknown')[:12]} — {failed} failed / {passed} passed"


def write_ci_line(args: argparse.Namespace, checks: list[dict], sha: str | None) -> None:
    import dossier_edit
    print(dossier_edit.log_or_say(args.log, [ci_line(checks, sha)]))


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
    ap.add_argument("--register-wait", type=float, default=DEFAULT_REGISTER_WAIT,
                    help="seconds to keep polling while no checks are registered yet (after a push)")
    ap.add_argument("--lines", type=int, default=20)
    ap.add_argument("--line-chars", type=int, default=300)
    ap.add_argument("--idle-first", action="store_true", help="wait for a free shared runner before the timeout counts")
    ap.add_argument("--idle-timeout", type=float, default=7200)
    ap.add_argument("--job-log", metavar="JOB", help="print matching lines of one job's log (needs --grep)")
    ap.add_argument("--grep", metavar="REGEX", help="with --job-log: the pattern")
    ap.add_argument("--rerun-failed", action="store_true", help="re-run the failed jobs once and wait again")
    ap.add_argument("--rerun-settle", type=float, default=20, help="seconds to let GitHub register the rerun")
    ap.add_argument("--log", "--build-log", dest="log", metavar="DOSSIER", help="append `SUITE: ci run <url> on <sha> — <N> failed / <N> passed` to this dossier's Build log once the checks have a verdict")
    ap.add_argument("--state-dir", default=None, help="where the once-per-head retrigger markers live (default: the temp dir)")
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

    is_pr = target[0] == "pr"
    pr_no = int(str(target[1])) if is_pr else 0
    if args.job_log:
        checks, err = fetch_checks(active_runner, target, args.repo)
        if checks is None:
            print(f"wait_ci: {err}", file=sys.stderr)
            return 2
        return job_log(active_runner, checks, args)

    state = pr_state(active_runner, pr_no, args.repo) if is_pr else None
    if state:
        line = conflict_line(pr_no, state)
        if line:
            print(line, file=sys.stderr)
            return 5

    def watch() -> str | None:
        fresh = pr_state(active_runner, pr_no, args.repo)
        return conflict_line(pr_no, fresh) if fresh else None

    watcher = watch if state else None
    retriggered = False
    while True:
        outcome, checks, err = poll(active_runner, target, args.repo, args.timeout, args.interval, args.register_wait, watcher)
        no_run = (outcome == "error" and err.startswith(NOT_REGISTERED)) or (outcome == "timeout" and not checks)
        if no_run and is_pr:
            cur = pr_state(active_runner, pr_no, args.repo)
            head = str((cur or {}).get("headRefOid") or "")
            bad = invalid_workflows(active_runner, head, args.repo)
            if bad:
                print(invalid_line(pr_no, head, bad), file=sys.stderr)
                return 8
            parent = parent_conflict(active_runner, cur, args.repo) if cur else None
            if parent:
                print(parent_line(pr_no, cur or {}, parent), file=sys.stderr)
                return 7
            if head:
                if retriggered or os.path.exists(marker_path(args, pr_no, head)):
                    return never_started(active_runner, args, pr_no, head, True)
                retrigger(active_runner, args, pr_no, head)
                retriggered = True
                continue
        if outcome in ("error", "conflict"):
            print(f"wait_ci: {err}" if outcome == "error" else err, file=sys.stderr)
            return 2 if outcome == "error" else 5

        if outcome == "done" and args.rerun_failed and any(c.get("bucket") in BAD_BUCKETS for c in checks):
            failed_names = [str(c.get("name")) for c in checks if c.get("bucket") in BAD_BUCKETS]
            ids = rerun_failed(active_runner, checks, args.repo)
            print(f"rerun: failed {', '.join(failed_names)}; re-ran run(s) {', '.join(ids) or 'none (no run id in the links)'}")
            args.rerun_failed = False
            if ids:
                time.sleep(max(args.rerun_settle, 0))
                outcome, checks, err = poll(active_runner, target, args.repo, args.timeout, args.interval, args.register_wait, watcher)
                if outcome in ("error", "conflict"):
                    print(f"wait_ci: {err}" if outcome == "error" else err, file=sys.stderr)
                    return 2 if outcome == "error" else 5
                print("after the rerun:")

        if outcome == "done" and state and checks and not any(c.get("bucket") in BAD_BUCKETS for c in checks):
            state = pr_state(active_runner, pr_no, args.repo) or state  # the head NOW, not at the start
            line = conflict_line(pr_no, state)
            if line:
                print(line, file=sys.stderr)
                return 5
        if outcome == "done" and state and state.get("headRefOid") and checks \
                and not any(c.get("bucket") in BAD_BUCKETS for c in checks):
            head = str(state["headRefOid"])
            bad = invalid_workflows(active_runner, head, args.repo)
            if bad:
                # the workflows that parsed are green, but one never ran: not a verdict (config-harness #706)
                report(active_runner, checks, args)
                print(invalid_line(pr_no, head, bad), file=sys.stderr)
                return 8
            stale = stale_head(active_runner, checks, head, args.repo)
            if stale:
                print(f"wait_ci: stale green refused: run {stale[0]} ran on {stale[1][:12]} but PR #{pr_no} "
                      f"head is {head[:12]}; the push to the head started no run", file=sys.stderr)
                if not is_pr:
                    return 4
                parent = parent_conflict(active_runner, state, args.repo)
                if parent:
                    print(parent_line(pr_no, state, parent), file=sys.stderr)
                    return 7
                if retriggered or os.path.exists(marker_path(args, pr_no, head)):
                    return never_started(active_runner, args, pr_no, head, True)
                retrigger(active_runner, args, pr_no, head)
                retriggered = True
                if not await_head_run(active_runner, target, args, head):
                    return never_started(active_runner, args, pr_no, head, True)
                continue
        break

    report(active_runner, checks, args)
    if args.log and outcome == "done" and checks:
        write_ci_line(args, checks, str((state or {}).get("headRefOid") or args.sha or ""))

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

    # 1b. the post-push window: no checks registered yet, then real ones.
    calls = {"n": 0}

    def runner_late(cmd: list[str]) -> tuple[int, str, str]:
        if cmd[:3] == ["gh", "pr", "checks"]:
            calls["n"] += 1
            if calls["n"] <= 2:
                return 1, "", "no checks reported on the 'feat/x' branch"
            return 0, json.dumps([{"name": "build", "state": "SUCCESS", "bucket": "pass", "link": "", "workflow": "CI"}]), ""
        return 1, "", "unexpected call"

    with contextlib.redirect_stdout(io.StringIO()):
        outcome, checks, err = poll(runner_late, 5, None, timeout=5, interval=0.01, register_wait=5)
    check("two 'no checks' responses, then checks, reaches done", outcome == "done" and calls["n"] == 3)

    def runner_never(cmd: list[str]) -> tuple[int, str, str]:
        return 1, "", "no checks reported on the 'feat/x' branch"

    with contextlib.redirect_stdout(io.StringIO()):
        outcome, _, err = poll(runner_never, 5, None, timeout=5, interval=0.01, register_wait=0.05)
    check("always 'no checks' errors after the register window", outcome == "error" and NOT_REGISTERED in err)
    with contextlib.redirect_stdout(io.StringIO()):
        outcome, _, err = poll(lambda c: (0, "[]", ""), 5, None, timeout=5, interval=0.01, register_wait=0.05)
    check("an empty list is the same window", outcome == "error" and NOT_REGISTERED in err)
    with contextlib.redirect_stdout(io.StringIO()):
        outcome, _, err = poll(lambda c: (1, "", "authentication required"), 5, None, timeout=5, interval=0.01, register_wait=5)
    check("another gh error still fails fast", outcome == "error" and NOT_REGISTERED not in err)

    # 1c. --log DOSSIER writes the SUITE: ci line through dossier_edit's one lock.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        dos = os.path.join(td, "W-1.md")
        open(dos, "w", encoding="utf-8").write("---\nid: W-1\n---\n\n# T\n\n## Build log\n\n- first\n")
        ok_checks = [{"name": "build", "state": "SUCCESS", "bucket": "pass", "link": "https://github.com/o/r/actions/runs/111/job/222", "workflow": "CI"},
                     {"name": "lint", "state": "FAILURE", "bucket": "fail", "link": "https://github.com/o/r/actions/runs/111/job/223", "workflow": "CI"}]

        def runner_log(cmd: list[str]) -> tuple[int, str, str]:
            if cmd[:3] == ["gh", "pr", "checks"]:
                return 0, json.dumps(ok_checks), ""
            if cmd[:3] == ["gh", "pr", "view"]:
                return 0, json.dumps({"headRefOid": "abcdef0123456789", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}), ""
            return 1, "", "unexpected call"

        with contextlib.redirect_stdout(io.StringIO()):
            main(["--pr", "5", "--log", dos, "--interval", "0.01"], runner=runner_log)
        got = open(dos, encoding="utf-8").read()
        check("--log writes the SUITE: ci line with run url, sha and counts",
              "- SUITE: ci run https://github.com/o/r/actions/runs/111 on abcdef012345 — 1 failed / 1 passed\n" in got)
        check("the --log line matches the format the phase doc names", re.search(r"^- SUITE: ci run \S+ on \S+ — \d+ failed / \d+ passed$", got, re.M) is not None)

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

    # 12. the PR-state guards: stale green refused, DIRTY fails fast, a matching head stays green.
    head_new, head_old = "a" * 40, "b" * 40
    pr_calls: list[list[str]] = []

    def runner_guard(pr_view: dict, run_sha: str):
        def runner_g(cmd: list[str]) -> tuple[int, str, str]:
            pr_calls.append(cmd)
            if cmd[:3] == ["gh", "pr", "view"]:
                return 0, json.dumps(pr_view), ""
            if cmd[:3] == ["gh", "pr", "checks"]:
                return 0, json.dumps([{"name": "build", "state": "SUCCESS", "bucket": "pass",
                                       "link": "https://github.com/o/r/actions/runs/30/job/31", "workflow": "CI"}]), ""
            if cmd[:3] == ["gh", "run", "view"]:
                return 0, run_sha + "\n", ""
            return 1, "", "unexpected call"
        return runner_g

    sdir = tempfile.mkdtemp(prefix="wait_ci_st_")
    clean = {"headRefOid": head_new, "mergeStateStatus": "CLEAN", "mergeable": "MERGEABLE", "baseRefName": "production"}
    for label, view, sha, want, needle in (
        ("a green on a superseded head is refused with both SHAs", clean, head_old, 6, (head_old[:12], head_new[:12])),
        ("a DIRTY PR fails fast naming the base", {**clean, "mergeStateStatus": "DIRTY"}, head_new, 5, ("merge origin/production",)),
        ("a CONFLICTING PR fails fast", {**clean, "mergeable": "CONFLICTING", "mergeStateStatus": "BLOCKED"}, head_new, 5, ("merge origin/production",)),
        ("a matching head stays green", clean, head_new, 0, ()),
    ):
        pr_calls.clear()
        out_g, err_g = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out_g), contextlib.redirect_stderr(err_g):
            rc = main(["--pr", "9", "--interval", "0.01", "--rerun-settle", "0", "--register-wait", "0.05", "--state-dir", sdir],
                      runner=runner_guard(view, sha))
        check(label, rc == want and all(n in err_g.getvalue() for n in needle))
        check(label + " (pr view: once up front, once more before a green; bounded line)",
              (want == 6 or sum(1 for c in pr_calls if c[:3] == ["gh", "pr", "view"]) == (1 if want == 5 else 2))
              and len(err_g.getvalue().splitlines()) <= (2 if want == 6 else 1))
        if want == 5:
            check(label + " (never polled)", not any(c[:3] == ["gh", "pr", "checks"] for c in pr_calls))
        if want == 6:
            check(label + " (never green)", "pass" not in out_g.getvalue().replace("1/1 done, 0 failed", ""))
        if want == 4:
            check(label + " (no report printed)", "pass" not in out_g.getvalue().replace("1/1 done, 0 failed", ""))

    # 13. mid-wait: the head moves during the wait -> 4; the PR turns DIRTY while polling -> 5.
    views = {"n": 0}

    def runner_moves(cmd: list[str]) -> tuple[int, str, str]:
        if cmd[:3] == ["gh", "pr", "view"]:
            views["n"] += 1
            return 0, json.dumps({**clean, "headRefOid": head_new if views["n"] == 1 else head_old}), ""
        return runner_guard(clean, head_new)(cmd)

    out_m, err_m = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out_m), contextlib.redirect_stderr(err_m):
        rc = main(["--pr", "9", "--interval", "0.01", "--rerun-settle", "0", "--register-wait", "0.05", "--state-dir", sdir],
                  runner=runner_moves)
    check("a head that moves during the wait is refused", rc == 6 and head_new[:12] in err_m.getvalue()
          and head_old[:12] in err_m.getvalue() and "CI never started" in err_m.getvalue())

    polls_n = {"n": 0}

    def runner_turns_dirty(cmd: list[str]) -> tuple[int, str, str]:
        if cmd[:3] == ["gh", "pr", "view"]:
            dirty = polls_n["n"] >= 2
            return 0, json.dumps({**clean, "mergeStateStatus": "DIRTY" if dirty else "CLEAN"}), ""
        if cmd[:3] == ["gh", "pr", "checks"]:
            polls_n["n"] += 1
            return 0, json.dumps([{"name": "slow", "state": "IN_PROGRESS", "bucket": "pending", "link": "", "workflow": "CI"}]), ""
        return 1, "", "unexpected call"

    out_d, err_d = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out_d), contextlib.redirect_stderr(err_d):
        rc = main(["--pr", "9", "--interval", "0.001", "--timeout", "5"], runner=runner_turns_dirty)
    check("a PR that turns DIRTY mid-wait fails fast with 5", rc == 5 and "merge origin/production" in err_d.getvalue()
          and len(err_d.getvalue().splitlines()) == 1 and len(out_d.getvalue().splitlines()) <= 1)

    # 14. CI never started: one retrigger per head (close + reopen), then a distinct verdict, never a loop.
    head_x = "c" * 40
    state_h: dict = {}

    def runner_norun(log: list[list[str]], heal: bool = False, stale: bool = False, parent: str = "CLEAN"):
        def r(cmd: list[str]) -> tuple[int, str, str]:
            log.append(cmd)
            if cmd[:3] == ["gh", "pr", "view"]:
                return 0, json.dumps({**clean, "headRefOid": head_x, "mergeable": "UNKNOWN"}), ""
            if cmd[:3] == ["gh", "pr", "list"]:
                return 0, json.dumps([{"number": 620, "mergeStateStatus": parent, "mergeable": "UNKNOWN",
                                       "baseRefName": "stub/2026-11-18"}]), ""
            if cmd[:3] == ["gh", "pr", "reopen"]:
                state_h["reopened"] = True
            if cmd[:3] == ["gh", "pr", "checks"]:
                if stale:
                    return 0, json.dumps([{"name": "build", "state": "SUCCESS", "bucket": "pass",
                                           "link": "https://github.com/o/r/actions/runs/40/job/41", "workflow": "CI"}]), ""
                if heal and state_h.get("reopened"):
                    return 0, json.dumps([{"name": "build", "state": "SUCCESS", "bucket": "pass", "link": "", "workflow": "CI"}]), ""
                return 1, "", "no checks reported on the 'feat/x' branch"
            if cmd[:3] == ["gh", "run", "view"]:
                return 0, (head_x if heal and state_h.get("reopened") else head_old) + "\n", ""
            return 0, "", ""
        return r

    def run14(log, **kw):
        o, e = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            rc14 = main(["--pr", "9", "--interval", "0.01", "--rerun-settle", "0", "--register-wait", "0.05",
                         "--state-dir", sdir], runner=runner_norun(log, **kw))
        return rc14, e.getvalue()

    for mode, kw in (("no run at all", {}), ("stale head", {"stale": True})):
        sdir = tempfile.mkdtemp(prefix="wait_ci_st_")
        log1: list[list[str]] = []
        rc1, err1 = run14(log1, **kw)
        closes = [c for c in log1 if c[:3] == ["gh", "pr", "close"]]
        check(f"{mode}: first call retriggers once (close then reopen, no push) and ends 6",
              rc1 == 6 and len(closes) == 1 and any(c[:3] == ["gh", "pr", "reopen"] for c in log1)
              and not any(c[:2] == ["git", "push"] for c in log1) and "CI did not fail, it did not run" in err1
              and "UNKNOWN" in err1)
        log2: list[list[str]] = []
        rc2, err2 = run14(log2, **kw)
        check(f"{mode}: a repeat on the same head gives 6 with no second retrigger",
              rc2 == 6 and not any(c[:3] in (["gh", "pr", "close"], ["gh", "pr", "reopen"]) for c in log2))
        check(f"{mode}: the verdict names no wait", "report a blocker" in err2)
    for mode, kw in (("no run at all", {}), ("stale head", {"stale": True})):
        sdir = tempfile.mkdtemp(prefix="wait_ci_st_")
        log4: list[list[str]] = []
        rc4, err4 = run14(log4, parent="DIRTY", **kw)
        check(f"{mode}: a conflicting parent PR gives 7 naming it, with no close/reopen and no wait",
              rc4 == 7 and "#620" in err4 and "stub/2026-11-18" in err4 and "resolve-merge" in err4
              and not any(c[:3] in (["gh", "pr", "close"], ["gh", "pr", "reopen"]) for c in log4))
    sdir = tempfile.mkdtemp(prefix="wait_ci_st_")
    state_h.clear()
    log3: list[list[str]] = []
    rc3, _ = run14(log3, heal=True)
    check("a retrigger that works continues the same call to green", rc3 == 0
          and sum(1 for c in log3 if c[:3] == ["gh", "pr", "close"]) == 1)

    def runner_badwf(green: bool):
        def r(cmd: list[str]) -> tuple[int, str, str]:
            if cmd[:3] == ["gh", "pr", "view"]:
                return 0, json.dumps({"headRefOid": "feedface00112233", "mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED",
                                      "baseRefName": "development"}), ""
            if cmd[:3] == ["gh", "pr", "checks"]:
                if green:
                    return 0, json.dumps([{"name": "PR source", "state": "SUCCESS", "bucket": "pass",
                                           "link": "https://github.com/o/r/actions/runs/5/job/6", "workflow": "Enforce"}]), ""
                return 1, "", "no checks reported on the 'sync/x' branch"
            if cmd[:3] == ["gh", "run", "list"] and "--commit" in cmd:
                return 0, json.dumps([{"workflowName": ".github/workflows/ci.yml", "status": "completed", "conclusion": "failure",
                                       "url": "https://github.com/o/r/actions/runs/7"},
                                      {"workflowName": "Enforce", "status": "completed", "conclusion": "success", "url": ""}]), ""
            if cmd[:3] == ["gh", "run", "view"]:
                return 0, "feedface00112233\n", ""
            return 1, "", "unexpected call"
        return r

    for green in (False, True):
        sdir = tempfile.mkdtemp(prefix="wait_ci_st_")
        o8, e8 = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(o8), contextlib.redirect_stderr(e8):
            rc8 = main(["--pr", "9", "--interval", "0.01", "--register-wait", "0.05", "--state-dir", sdir],
                       runner=runner_badwf(green))
        check(f"a workflow file that does not parse gives 8 naming it ({'other workflows green' if green else 'no check at all'}), "
              "never a retrigger or a parent verdict",
              rc8 == 8 and ".github/workflows/ci.yml" in e8.getvalue() and "did not parse" in e8.getvalue()
              and "Enforce" not in e8.getvalue().split("did not parse")[0])

    for item in failures_list:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures_list)} failure(s)")
    return 1 if failures_list else 0


if __name__ == "__main__":
    sys.exit(main())
