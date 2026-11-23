#!/usr/bin/env python3
"""Recover a red CI check that only the repository's own command can clear, without asking the owner.

Some checks fail for a reason no code change fixes: the sealed secrets backup lacks a name a
dossier made required, and only a command run in the repository's VM, with the cloud's
credentials, rewrites it. The repository declares such a recovery in `.agentic/ci-recover.json`;
this script knows no repository, only that file.

# Declaration

    {"recoveries": [{
      "name": "bootstrap-backup",
      "detect": ["bash", "scripts/check_bootstrap_backup.sh"],     exit 0 = nothing to do (runs in the checkout)
      "ci_check": "Dependency direction",                           the CI check that goes red
      "ci_pattern": "check_bootstrap_backup: .* lacks:",           regex over that check's failed log
      "fix": ["bash", "deploy/host/host.sh", "exec", "--", "bash", "deploy/scripts/seal_bootstrap_from_cloud.sh"],
      "paths": ["deploy/secrets/bootstrap.enc.env"],                the only files the fix may change and commit
      "message": "chore(secrets): reseal the bootstrap backup from the cloud"}]}

`detect`, `fix` and `paths` run in the checkout (cwd), no shell. Nothing in the file is a secret.

# Usage

    ci_recover.py prevent --worktree WT [--trailer TEXT]
        PREVENT: run `detect` for each recovery; where it fails, run `fix`, check `detect` passes now,
        commit `paths` through the repository's hooks (never --no-verify). Nothing is pushed: the run
        pushes with its own change. Exit 0 (nothing to do, or resealed and committed), 3 (no
        declaration), 4 (the fix did not satisfy `detect`, or changed nothing), 6 (a commit hook refused).
    ci_recover.py recover --pr N --worktree WT [--branch B] [--repo OWNER/NAME] [--plan] [--trailer TEXT]
        RECOVER: read the PR's checks. Only when EVERY red check is a declared recovery's `ci_check` and its
        failed log matches `ci_pattern` does it run `fix` in WT (a checkout of B, default the PR's head
        branch; for a stacked PR pass the dossier branch it inherits the failure from), commit through
        the hooks, and `git push origin HEAD:B` (no force). Then wait with wait_ci.py. `--plan` stops
        before the fix. Exit 0 (recovered, or --plan says so), 1 (another red check, or a log that does
        not match: not this script's to clear), 2 (WT is not on B, or dirty), 3, 4, 5 (checks still
        pending), 6, 7 (push refused).
    ci_recover.py --selftest

Never prints a value the fix produced: the fix's output is dropped, only its exit status is read.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wait_ci  # noqa: E402  (must follow the sys.path fix-up above)

DECLARATION = os.path.join(".agentic", "ci-recover.json")
KEYS = ("name", "detect", "fix", "paths", "message")


def load(wt: str) -> list[dict]:
    """The declared recoveries of the checkout, [] when there is no file or it is not well formed."""
    try:
        with open(os.path.join(wt, DECLARATION), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    rows = data.get("recoveries") if isinstance(data, dict) else None
    out = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and all(k in r for k in KEYS) and all(isinstance(r[k], list) and r[k] for k in ("detect", "fix", "paths")):
            out.append(r)
    return out


def sh(cmd: list[str], cwd: str) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    except OSError as e:
        return 127, str(e)
    return p.returncode, (p.stderr or "")[-400:]


def git(wt: str, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", wt, *args], capture_output=True, text=True, check=False)
    return p.returncode, (p.stdout + p.stderr).strip()


def apply(rec: dict, wt: str, trailer: str) -> tuple[int, str]:
    """Run the fix, prove `detect` passes, commit the declared paths through the hooks."""
    rc, err = sh(rec["fix"], wt)
    if rc != 0:
        return 4, f"{rec['name']}: the fix command failed (exit {rc}); the backup is unchanged"
    rc, _ = sh(rec["detect"], wt)
    if rc != 0:
        return 4, f"{rec['name']}: the fix ran but the detect command still fails (exit {rc})"
    git(wt, "add", "--", *rec["paths"])
    if git(wt, "diff", "--cached", "--quiet", "--", *rec["paths"])[0] == 0:
        return 4, f"{rec['name']}: the fix changed nothing in {' '.join(rec['paths'])}"
    msg = rec["message"] + (f"\n\n{trailer}" if trailer else "")
    rc, out = git(wt, "commit", "-m", msg, "--", *rec["paths"])
    if rc != 0:
        git(wt, "reset", "-q", "--", *rec["paths"])
        git(wt, "checkout", "-q", "--", *rec["paths"])
        return 6, f"{rec['name']}: a commit hook refused: {out[-300:]}"
    return 0, f"{rec['name']}: resealed and committed {' '.join(rec['paths'])}"


def prevent(args: argparse.Namespace) -> int:
    recs = load(args.worktree)
    if not recs:
        print(f"ci_recover: no recovery declared in {DECLARATION}")
        return 3
    worst = 0
    for rec in recs:
        rc, _ = sh(rec["detect"], args.worktree)
        if rc == 0:
            print(f"ci_recover: {rec['name']}: nothing to do")
            continue
        code, line = apply(rec, args.worktree, args.trailer)
        print(f"ci_recover: {line}")
        worst = worst or code
    return worst


def failed_logs(runner, checks: list[dict]) -> dict[str, str]:
    logs = {}
    for c in checks:
        ref = wait_ci.run_ref(c.get("link", ""))
        if not ref:
            logs[c["name"]] = ""
            continue
        cmd = ["gh", "run", "view", ref[0]] + (["--job", ref[1]] if ref[1] else []) + ["--log-failed"]
        rc, out, err = runner(cmd)
        logs[c["name"]] = wait_ci.strip_ansi(out or err or "")
    return logs


def classify(recs: list[dict], checks: list[dict], logs: dict[str, str]) -> tuple[list[dict], list[str]]:
    """(recoveries to run, red checks no recovery covers). A red check is covered only when a recovery names it AND its log matches."""
    todo, other = [], []
    for c in checks:
        hit = next((r for r in recs if r.get("ci_check") == c["name"] and r.get("ci_pattern")
                    and re.search(r["ci_pattern"], logs.get(c["name"], ""))), None)
        if hit is None:
            other.append(c["name"])
        elif hit not in todo:
            todo.append(hit)
    return todo, other


def recover(args: argparse.Namespace, runner=wait_ci.default_runner) -> int:
    recs = load(args.worktree)
    if not recs:
        print(f"ci_recover: no recovery declared in {DECLARATION}")
        return 3
    cmd = wait_ci.with_repo(["gh", "pr", "checks", str(args.pr), "--json", "name,state,bucket,link,workflow"], args.repo)
    rc, out, err = runner(cmd)
    try:
        checks = json.loads(out)
    except (ValueError, TypeError):
        print(f"ci_recover: gh pr checks did not return JSON (exit {rc}): {(err or out or '').strip()[:200]}")
        return 5
    if any(c.get("bucket") == "pending" for c in checks):
        print("ci_recover: checks still pending; ask again when they finish")
        return 5
    red = [c for c in checks if c.get("bucket") in wait_ci.BAD_BUCKETS]
    if not red:
        print("ci_recover: no red check; nothing to recover")
        return 0
    todo, other = classify(recs, red, failed_logs(runner, red))
    if other:
        print(f"ci_recover: not recoverable here, red and not declared: {', '.join(other)}")
        return 1
    branch = args.branch
    if not branch:
        rc, out, _ = runner(wait_ci.with_repo(["gh", "pr", "view", str(args.pr), "--json", "headRefName"], args.repo))
        try:
            branch = json.loads(out)["headRefName"]
        except (ValueError, KeyError, TypeError):
            print("ci_recover: cannot read the PR's head branch; pass --branch")
            return 2
    names = ", ".join(r["name"] for r in todo)
    if args.plan:
        print(f"ci_recover: recoverable ({names}) on {branch}")
        return 0
    if git(args.worktree, "rev-parse", "--abbrev-ref", "HEAD")[1] != branch or git(args.worktree, "status", "--porcelain")[1]:
        print(f"ci_recover: {args.worktree} is not a clean checkout of {branch}")
        return 2
    for rec in todo:
        code, line = apply(rec, args.worktree, args.trailer)
        print(f"ci_recover: {line}")
        if code:
            return code
    rc, out = git(args.worktree, "push", "origin", f"HEAD:{branch}")
    if rc != 0:
        print(f"ci_recover: push refused: {out[-300:]}")
        return 7
    print(f"ci_recover: pushed {branch}; wait for CI (wait_ci.py --pr {args.pr}); no owner question")
    return 0


# -------------------------------------------------------------------- selftest

FAKE_CHECK = 'grep -q "^NEWNAME=" deploy/secrets/bootstrap.enc.env\n'
FAKE_SEAL = 'echo "NEWNAME=ENC[x]" >> deploy/secrets/bootstrap.enc.env\n'
DECL = {"recoveries": [{
    "name": "bootstrap-backup", "detect": ["bash", "check.sh"], "ci_check": "Dependency direction",
    "ci_pattern": "check_bootstrap_backup: .* lacks:", "fix": ["bash", "seal.sh"],
    "paths": ["deploy/secrets/bootstrap.enc.env"], "message": "chore(secrets): reseal the backup"}]}
LOG_BOOT = "check_bootstrap_backup: deploy/secrets/bootstrap.enc.env lacks: NEWNAME; run seal"
LOG_OTHER = "error[E0308]: mismatched types"


def make_repo(root: str, name: str = "wt", declare: bool = True, hook: str = "") -> str:
    origin = os.path.join(root, f"{name}-origin.git")
    wt = os.path.join(root, name)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "feat", origin], check=True)
    os.makedirs(os.path.join(wt, "deploy", "secrets"))
    os.makedirs(os.path.join(wt, ".agentic"))
    for fn, body in (("check.sh", FAKE_CHECK), ("seal.sh", FAKE_SEAL), (os.path.join("deploy", "secrets", "bootstrap.enc.env"), "OLD=ENC[y]\n")):
        with open(os.path.join(wt, fn), "w") as f:
            f.write(body)
    if declare:
        with open(os.path.join(wt, DECLARATION), "w") as f:
            json.dump(DECL, f)
    subprocess.run(["git", "init", "-q", "-b", "feat", wt], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", wt, "config", k, v], check=True)
    if hook:
        with open(os.path.join(wt, ".git", "hooks", "pre-commit"), "w") as f:
            f.write(hook)
        os.chmod(os.path.join(wt, ".git", "hooks", "pre-commit"), 0o755)
    subprocess.run(["git", "-C", wt, "add", "-A"], check=True)
    subprocess.run(["git", "-C", wt, "commit", "-q", "-m", "init", "--no-verify"], check=True)
    subprocess.run(["git", "-C", wt, "remote", "add", "origin", origin], check=True)
    subprocess.run(["git", "-C", wt, "push", "-q", "origin", "feat"], check=True)
    return wt


def fake_gh(reds: dict[str, str], pending: bool = False):
    """A gh runner: one green check, the given red checks (name -> failed log), maybe one pending."""
    links = {n: f"https://github.com/o/r/actions/runs/{100 + i}/job/{200 + i}" for i, n in enumerate(reds)}
    checks = [{"name": "Build", "bucket": "pass", "state": "SUCCESS", "link": "", "workflow": "ci"}]
    checks += [{"name": n, "bucket": "fail", "state": "FAILURE", "link": links[n], "workflow": "ci"} for n in reds]
    if pending:
        checks.append({"name": "Slow", "bucket": "pending", "state": "PENDING", "link": "", "workflow": "ci"})

    def run(cmd: list[str]) -> tuple[int, str, str]:
        if cmd[:3] == ["gh", "pr", "checks"]:
            return 0, json.dumps(checks), ""
        if cmd[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps({"headRefName": "feat"}), ""
        if cmd[:3] == ["gh", "run", "view"]:
            for n, link in links.items():
                if f"/runs/{cmd[3]}/" in link:
                    return 0, reds[n], ""
        return 1, "", "unexpected " + " ".join(cmd)
    return run


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            failures.append(name)

    def ns(wt: str, **kw) -> argparse.Namespace:
        base = dict(pr=7, worktree=wt, branch=None, repo=None, plan=False, trailer="")
        base.update(kw)
        return argparse.Namespace(**base)

    def head(wt: str) -> str:
        return git(wt, "rev-parse", "HEAD")[1]

    def origin_head(root: str, name: str) -> str:
        return subprocess.run(["git", "--git-dir", os.path.join(root, f"{name}-origin.git"), "rev-parse", "feat"],
                              capture_output=True, text=True).stdout.strip()

    root = tempfile.mkdtemp(prefix="ci-recover-")
    try:
        # RECOVER, inherited bootstrap red only: reseal, commit, push, no owner question.
        wt = make_repo(root, "a")
        before = head(wt)
        rc = recover(ns(wt), fake_gh({"Dependency direction": LOG_BOOT}))
        check("inherited bootstrap red only: recovered (exit 0)", rc == 0)
        check("the reseal is committed and pushed", head(wt) != before and origin_head(root, "a") == head(wt))
        check("the sealed file holds the new name", "NEWNAME" in open(os.path.join(wt, "deploy/secrets/bootstrap.enc.env")).read())

        # Another red check besides the bootstrap one: not auto-recovered.
        wt = make_repo(root, "b")
        before = head(wt)
        rc = recover(ns(wt), fake_gh({"Dependency direction": LOG_BOOT, "Unit tests": LOG_OTHER}))
        check("bootstrap red plus another red check: not recovered (exit 1)", rc == 1)
        check("... nothing committed or pushed", head(wt) == before and origin_head(root, "b") == before)

        # The declared check red for another reason: not recovered.
        wt = make_repo(root, "c")
        rc = recover(ns(wt), fake_gh({"Dependency direction": LOG_OTHER}))
        check("declared check red with an unmatched log: exit 1", rc == 1 and head(wt) == origin_head(root, "c"))

        # Pending, green, no declaration, wrong branch, dirty tree.
        wt = make_repo(root, "d")
        check("a pending check: exit 5", recover(ns(wt), fake_gh({"Dependency direction": LOG_BOOT}, pending=True)) == 5)
        check("--plan names the recovery and changes nothing",
              recover(ns(wt, plan=True), fake_gh({"Dependency direction": LOG_BOOT})) == 0 and head(wt) == origin_head(root, "d"))
        check("no red check: exit 0", recover(ns(wt), fake_gh({})) == 0)
        check("wrong branch: exit 2", recover(ns(wt, branch="other"), fake_gh({"Dependency direction": LOG_BOOT})) == 2)
        with open(os.path.join(wt, "stray.txt"), "w") as f:
            f.write("x")
        check("dirty tree: exit 2", recover(ns(wt), fake_gh({"Dependency direction": LOG_BOOT})) == 2)
        wt = make_repo(root, "e", declare=False)
        check("no declaration: exit 3", recover(ns(wt), fake_gh({"Dependency direction": LOG_BOOT})) == 3)

        # A fix that does not satisfy detect: exit 4, nothing committed.
        wt = make_repo(root, "f")
        with open(os.path.join(wt, "seal.sh"), "w") as f:
            f.write("exit 1\n")
        git(wt, "commit", "-qam", "break the fake seal", "--no-verify")
        git(wt, "push", "-q", "origin", "feat")
        check("a failing fix: exit 4", recover(ns(wt), fake_gh({"Dependency direction": LOG_BOOT})) == 4)

        # A commit hook that refuses goes through: exit 6 and the hook ran (proves hooks are not bypassed).
        wt = make_repo(root, "g", hook="#!/bin/sh\ntouch hook-ran.marker\nexit 1\n")
        before = head(wt)
        rc = recover(ns(wt), fake_gh({"Dependency direction": LOG_BOOT}))
        check("a refusing commit hook: exit 6, hook ran, no push",
              rc == 6 and os.path.exists(os.path.join(wt, "hook-ran.marker")) and origin_head(root, "g") == before)

        # PREVENT.
        wt = make_repo(root, "h")
        before = head(wt)
        rc = prevent(ns(wt, trailer="Co-Authored-By: T <t@t>"))
        check("prevent: missing name resealed and committed, not pushed",
              rc == 0 and head(wt) != before and origin_head(root, "h") == before)
        check("prevent: the commit carries the trailer", "Co-Authored-By: T" in git(wt, "log", "-1", "--format=%B")[1])
        again = head(wt)
        check("prevent: already complete is a no-op", prevent(ns(wt)) == 0 and head(wt) == again)
        wt = make_repo(root, "i", declare=False)
        check("prevent: no declaration is exit 3", prevent(ns(wt)) == 3)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("prevent", "recover"):
        p = sub.add_parser(name)
        p.add_argument("--worktree", required=True)
        p.add_argument("--trailer", default="")
        if name == "recover":
            p.add_argument("--pr", type=int, required=True)
            p.add_argument("--branch")
            p.add_argument("--repo")
            p.add_argument("--plan", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.cmd == "prevent":
        return prevent(args)
    if args.cmd == "recover":
        return recover(args)
    ap.error("a subcommand or --selftest is required")
    return 2


if __name__ == "__main__":
    sys.exit(main())
