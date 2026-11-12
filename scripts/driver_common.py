#!/usr/bin/env python3
"""Shared helpers for the mechanical-phase drivers (finalize_pr, merge_packages,
pre_fanout_check, strength_gate_prep, test_round).

A driver chains scripts that already exist, in the order the phase doc lists
them, and prints EVIDENCE: a bounded digest and the path of the full evidence
file. It never rules. The Opus orchestrator keeps every judgement — an
arbitration row, an owner question, a `TOUCHED_BEYOND` decision, a contract
signature — and a driver that meets anything it cannot classify stops and says
so.

# Mode

`ASD_DRIVER_<NAME>` (for example `ASD_DRIVER_MERGE_PACKAGES`) is `off`, `shadow`
or `on`; unset or anything else means `off`.

    off     the phase doc's manual steps apply. The driver refuses to run: exit 3.
    shadow  the orchestrator does the manual steps AND runs the driver. The driver
            mutates nothing the run depends on: it works in a throwaway worktree
            (or only reads), writes no build-log line, and saves its evidence to
            .discovery/driver-shadow/<dossier>/<driver>-<timestamp>.json so a
            person can compare it with what the orchestrator did by hand.
    on      the driver does the steps; the orchestrator acts on its output.
            Evidence goes to .discovery/driver-evidence/<dossier>/.

# Exit codes (every driver)

    0  clean: every step ran and found nothing for the orchestrator to act on
    1  findings: every step ran; the evidence names things to fix or read
    2  surprise: unexpected git state, a conflict, a missing receipt, an output
       format the driver does not know. It stops at that step, says which, and
       leaves the tree inspectable (a merge it started is aborted first)
    3  off: the mode forbids the call

No third-party imports.
"""

from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from typing import Callable, Iterator

GIT = "/usr/bin/git"
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
EXIT_CLEAN, EXIT_FINDINGS, EXIT_SURPRISE, EXIT_OFF = 0, 1, 2, 3
MODES = ("off", "shadow", "on")
DIGEST_STEP_CAP = 18
DIGEST_FINDING_CAP = 10
LINE_CAP = 200


class Surprise(Exception):
    """Anything the driver cannot classify. Carries where it stopped."""


def env_name(name: str) -> str:
    return "ASD_DRIVER_" + name.upper()


def read_mode(name: str, override: str | None = None) -> str:
    value = (override or os.environ.get(env_name(name), "off")).strip().lower()
    return value if value in MODES else "off"


def sh(cmd: list[str] | str, cwd: str | None = None, timeout: int = 900, shell: bool = False) -> tuple[int, str]:
    """Run one command; (exit code, stdout+stderr). A command that cannot start
    or times out is a surprise, never a silent empty result."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=shell)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise Surprise(f"could not run {cmd if isinstance(cmd, str) else ' '.join(cmd)}: {err}")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def git(cwd: str, *args: str, timeout: int = 300) -> tuple[int, str]:
    return sh([GIT, *args], cwd=cwd, timeout=timeout)


def script(name: str, *args: str, cwd: str | None = None, timeout: int = 900) -> tuple[int, str]:
    """Run a sibling plugin script with this interpreter."""
    return sh([sys.executable, os.path.join(SCRIPTS, name), *args], cwd=cwd, timeout=timeout)


def cap(text: str, lines: int = 12, chars: int = LINE_CAP) -> list[str]:
    out = [(ln if len(ln) <= chars else ln[: chars - 1] + "…") for ln in text.splitlines() if ln.strip()]
    if len(out) > lines:
        out = out[:lines] + [f"(+{len(out) - lines} more lines)"]
    return out


def main_checkout(start: str) -> str:
    """The repository's main checkout for any directory inside it."""
    rc, out = sh([GIT, "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=start)
    if rc != 0:
        return os.path.abspath(start)
    common = out.strip().splitlines()[-1]
    return os.path.dirname(common) if os.path.basename(common) == ".git" else os.path.abspath(start)


def worktree_state(wt: str) -> dict:
    """Facts the drivers check before touching a tree: branch, head, in-progress
    operation, tracked changes, untracked count."""
    rc, out = git(wt, "rev-parse", "--is-inside-work-tree")
    if rc != 0 or out.strip() != "true":
        raise Surprise(f"{wt} is not a git work tree")
    _, head = git(wt, "rev-parse", "HEAD")
    _, branch = git(wt, "rev-parse", "--abbrev-ref", "HEAD")
    _, gitdir = git(wt, "rev-parse", "--path-format=absolute", "--git-dir")
    gd = gitdir.strip()
    ops = [n for n in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply") if os.path.exists(os.path.join(gd, n))]
    _, porcelain = git(wt, "status", "--porcelain")
    tracked = [ln for ln in porcelain.splitlines() if ln and not ln.startswith("??")]
    untracked = [ln for ln in porcelain.splitlines() if ln.startswith("??")]
    return {"head": head.strip(), "branch": branch.strip(), "operation": ops, "tracked_changes": tracked, "untracked": untracked}


def require_clean(wt: str, allow_untracked: bool = True) -> dict:
    st = worktree_state(wt)
    if st["operation"]:
        raise Surprise(f"{wt} is mid-operation ({', '.join(st['operation'])}); finish or abort it first")
    if st["tracked_changes"]:
        raise Surprise(f"{wt} has uncommitted tracked changes ({len(st['tracked_changes'])}): {'; '.join(st['tracked_changes'][:4])}")
    if st["untracked"] and not allow_untracked:
        raise Surprise(f"{wt} has untracked files: {'; '.join(st['untracked'][:4])}")
    return st


@contextlib.contextmanager
def throwaway_worktree(repo: str, ref: str = "HEAD") -> Iterator[str]:
    """A detached scratch worktree of `ref`, removed on exit. Shadow mode's
    place to mutate."""
    tmp = tempfile.mkdtemp(prefix="asd-driver-")
    path = os.path.join(tmp, "wt")
    rc, out = git(repo, "worktree", "add", "--detach", path, ref)
    if rc != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise Surprise(f"could not create the shadow worktree: {out.strip()[:160]}")
    try:
        yield path
    finally:
        git(repo, "worktree", "remove", "--force", path)
        shutil.rmtree(tmp, ignore_errors=True)
        git(repo, "worktree", "prune")


class Driver:
    """One driver run: its steps, facts and findings, and how they are shown."""

    def __init__(self, name: str, mode: str, dossier: str | None, discovery: str | None, log_path: str | None):
        self.name = name
        self.mode = mode
        self.dossier = dossier or "none"
        self.discovery = discovery
        self.log_path = log_path
        self.steps: list[dict] = []
        self.findings: list[str] = []
        self.facts: dict = {}
        self.summary = ""
        self.stopped_at: str | None = None
        self.evidence_path: str | None = None

    @property
    def shadow(self) -> bool:
        return self.mode == "shadow"

    def step(self, name: str, status: str, detail: str = "") -> None:
        """status: ok | finding | skipped (a step this run did not do, said so)."""
        self.steps.append({"step": name, "status": status, "detail": detail[:LINE_CAP * 2]})

    def finding(self, text: str) -> None:
        self.findings.append(text[:LINE_CAP * 2])

    def evidence_file(self) -> str | None:
        if not self.discovery:
            return None
        sub = "driver-shadow" if self.shadow else "driver-evidence"
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        return os.path.join(self.discovery, sub, self.dossier, f"{self.name}-{stamp}.json")

    def verdict(self, rc: int) -> str:
        return {0: "clean", 1: "findings", 2: "needs-opus"}.get(rc, "off")

    def finish(self, rc: int) -> int:
        """Write the full evidence file, print the bounded digest, and (mode on
        only) log the one-line summary. Returns rc."""
        path = self.evidence_file()
        full = {
            "driver": self.name, "mode": self.mode, "dossier": self.dossier, "verdict": self.verdict(rc), "exit": rc,
            "summary": self.summary, "stopped_at": self.stopped_at, "steps": self.steps, "findings": self.findings,
            "facts": self.facts, "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        if path:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(full, fh, indent=1, sort_keys=True)
                self.evidence_path = path
            except OSError as err:
                print(f"note: evidence file not written — {err}")
        out = [f"DRIVER {self.name} mode={self.mode} dossier={self.dossier} verdict={self.verdict(rc)} exit={rc}"]
        if self.summary:
            out.append(f"summary: {self.summary}")
        if self.stopped_at:
            out.append(f"STOPPED AT: {self.stopped_at}")
        shown = self.steps[:DIGEST_STEP_CAP]
        for s in shown:
            out.append(f"  [{s['status']}] {s['step']}" + (f" — {s['detail'][:LINE_CAP]}" if s["detail"] else ""))
        if len(self.steps) > len(shown):
            out.append(f"  (+{len(self.steps) - len(shown)} more steps in the evidence file)")
        for f in self.findings[:DIGEST_FINDING_CAP]:
            out.append(f"  ! {f[:LINE_CAP]}")
        if len(self.findings) > DIGEST_FINDING_CAP:
            out.append(f"  (+{len(self.findings) - DIGEST_FINDING_CAP} more findings in the evidence file)")
        if self.evidence_path:
            out.append(f"evidence: {self.evidence_path}")
        if rc == EXIT_SURPRISE:
            out.append("Opus: do this step by hand; the driver rules nothing.")
        print("\n".join(out))
        if self.log_path and not self.shadow and self.summary:
            try:
                sys.path.insert(0, SCRIPTS)
                import dossier_edit

                print(dossier_edit.log_or_say(self.log_path, [f"DRIVER-{self.name.upper().replace('_', '-')}: {self.summary}"]))
            except ImportError as err:
                print(f"build log: not written — {err}")
        return rc


def drive(name: str, argv: list[str] | None, build_parser: Callable, run: Callable, selftest: Callable[[], int]) -> int:
    """Common entry: parse, --selftest, mode gate, run with surprise handling.

    `build_parser(ap)` adds the driver's own flags; `run(d, args)` does the
    steps on a Driver and returns 0 or 1 (raise Surprise for anything else).
    """
    import argparse

    ap = argparse.ArgumentParser(description=f"{name}: a one-call mechanical-phase driver; prints evidence, rules nothing.")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    ap.add_argument("--mode", choices=MODES, help=f"override {env_name(name)} (tests only)")
    ap.add_argument("--dossier", help="dossier id, e.g. W-014 (names the evidence directory)")
    ap.add_argument("--root", help="the main checkout (default: found from the working directory)")
    ap.add_argument("--log", metavar="DOSSIER_FILE", help="append the one-line summary to this dossier's Build log (mode on only)")
    build_parser(ap)
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    mode = read_mode(name, args.mode)
    if mode == "off":
        print(f"{env_name(name)} is off (the default): do the phase doc's manual steps. Set it to shadow or on to run this driver.")
        return EXIT_OFF
    root = args.root or main_checkout(getattr(args, "worktree", None) or os.getcwd())
    d = Driver(name, mode, args.dossier, os.path.join(root, ".discovery"), args.log)
    args.root = root
    try:
        rc = run(d, args)
    except Surprise as err:
        d.stopped_at = str(err)
        d.summary = d.summary or f"stopped: {str(err)[:120]}"
        return d.finish(EXIT_SURPRISE)
    except Exception as err:  # an unknown failure is a surprise, never a crash with no evidence
        d.stopped_at = f"unexpected {type(err).__name__}: {err}"
        d.facts["traceback"] = traceback.format_exc()[-1200:]
        d.summary = d.summary or "stopped: unexpected error"
        return d.finish(EXIT_SURPRISE)
    return d.finish(rc)


# ---------------------------------------------------------------- selftest support


class Checker:
    def __init__(self, label: str):
        self.label = label
        self.failed: list[str] = []
        self.count = 0

    def __call__(self, name: str, ok: bool) -> None:
        self.count += 1
        if not ok:
            self.failed.append(name)

    def report(self) -> int:
        if self.failed:
            print(f"{self.label} selftest FAILED: " + "; ".join(self.failed))
            return 1
        print(f"{self.label} selftest OK ({self.count} checks)")
        return 0


def fixture_git(cwd: str, *args: str) -> str:
    proc = subprocess.run([GIT, *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def fixture_repo(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    fixture_git(path, "init", "-q", "-b", "main")
    fixture_git(path, "config", "user.email", "t@example.com")
    fixture_git(path, "config", "user.name", "t")
    fixture_git(path, "config", "commit.gpgsign", "false")


def fixture_write(root: str, rel: str, text: str) -> None:
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)


def fixture_commit(root: str, rel: str, text: str, msg: str) -> str:
    fixture_write(root, rel, text)
    fixture_git(root, "add", rel)
    fixture_git(root, "commit", "-q", "-m", msg)
    return fixture_git(root, "rev-parse", "HEAD").strip()


def run_self(name: str, *args: str, env: dict | None = None, cwd: str | None = None) -> tuple[int, str]:
    """Run a driver script as a subprocess for selftests."""
    e = dict(os.environ)
    e.update(env or {})
    proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, name), *args], capture_output=True, text=True, env=e, cwd=cwd)
    return proc.returncode, proc.stdout + proc.stderr


def selftest() -> int:
    c = Checker("driver_common")
    c("mode defaults to off", read_mode("x_y") == "off")
    os.environ["ASD_DRIVER_X_Y"] = "SHADOW"
    c("mode reads the env var, case-insensitive", read_mode("x_y") == "shadow")
    os.environ["ASD_DRIVER_X_Y"] = "bogus"
    c("an unknown mode is off", read_mode("x_y") == "off")
    del os.environ["ASD_DRIVER_X_Y"]
    c("cap bounds lines", len(cap("\n".join(str(i) for i in range(50)), 5)) == 6)
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "r")
        fixture_repo(repo)
        fixture_commit(repo, "a.txt", "1\n", "init")
        c("clean tree passes require_clean", require_clean(repo)["branch"] == "main")
        fixture_write(repo, "a.txt", "2\n")
        try:
            require_clean(repo)
            c("a dirty tree is a surprise", False)
        except Surprise:
            c("a dirty tree is a surprise", True)
        fixture_git(repo, "checkout", "-q", "--", "a.txt")
        with throwaway_worktree(repo) as wt:
            c("throwaway worktree exists inside", os.path.isfile(os.path.join(wt, "a.txt")))
            gone = wt
        c("throwaway worktree is removed", not os.path.exists(gone))
        d = Driver("demo", "on", "W-1", os.path.join(repo, ".discovery"), None)
        d.step("one", "ok", "fine")
        d.finding("a thing")
        d.summary = "demo done"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = d.finish(1)
        c("finish writes the evidence file", rc == 1 and d.evidence_path and os.path.isfile(d.evidence_path)
          and "driver-evidence" in d.evidence_path)
        d2 = Driver("demo", "shadow", "W-1", os.path.join(repo, ".discovery"), None)
        with contextlib.redirect_stdout(io.StringIO()):
            d2.finish(0)
        c("shadow evidence goes to driver-shadow/<dossier>/", d2.evidence_path and "driver-shadow/W-1/demo-" in d2.evidence_path)
    return c.report()


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else 0)
