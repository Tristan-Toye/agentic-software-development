#!/usr/bin/env python3
"""Shared helpers for the mechanical-phase drivers (merge_packages,
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
            <git common dir>/asd-drivers/driver-shadow/<dossier>/<driver>-<timestamp>.json so a
            person can compare it with what the orchestrator did by hand.
    on      the driver does the steps; the orchestrator acts on its output.
            Evidence goes to <git common dir>/asd-drivers/driver-evidence/<dossier>/
            (inside .git: never tracked, never dirties a tree, survives worktree removal).

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
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from typing import Callable, Iterator

import driver_modes

GIT = "/usr/bin/git"
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
EXIT_CLEAN, EXIT_FINDINGS, EXIT_SURPRISE, EXIT_OFF = 0, 1, 2, 3
MODES = ("off", "shadow", "on")
DIGEST_STEP_CAP = 18
DIGEST_FINDING_CAP = 10
LINE_CAP = 200


class Surprise(Exception):
    """Anything the driver cannot classify. Carries where it stopped."""


def resolve_dossier(root: str, dossier: str | None, explicit: str | None = None, required: bool = True,
                    also: tuple = ()) -> str | None:
    """The dossier file of one ID. Dossiers are named `<ID>-<slug>.md`, so
    `<ID>.md` alone is not enough. Order: an explicit path (`--log`), then in
    `root` (the main checkout) and then in each `also` tree (the build
    worktree) `.discovery/dossiers/<ID>.md`, then exactly one `<ID>-*.md`. The
    first tree with a match wins. Several matches in one tree are a surprise
    naming every candidate. No match is a surprise when `required`, else None."""
    if explicit:
        return explicit
    if not dossier:
        if required:
            raise Surprise("no dossier ID or path given (give --dossier or --log)")
        return None
    plain = os.path.join(root, ".discovery", "dossiers", f"{dossier}.md")
    seen: list[str] = []
    for tree in (root, *[a for a in also if a]):
        folder = os.path.join(tree, ".discovery", "dossiers")
        if folder in seen:
            continue
        seen.append(folder)
        one = os.path.join(folder, f"{dossier}.md")
        if os.path.isfile(one):
            return one
        found = sorted(os.path.join(folder, n) for n in (os.listdir(folder) if os.path.isdir(folder) else [])
                       if n.startswith(dossier + "-") and n.endswith(".md"))
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise Surprise(f"dossier {dossier} is ambiguous, candidates: {', '.join(found)}")
    if required:
        raise Surprise(f"dossier file not found: {plain} (nor {dossier}-*.md, in {len(seen)} tree(s) searched)")
    return None


# ---------------------------------------------------------------- context derivation
# The orchestrator records its fan-out in the build worktree: every spawned payload is a file
# `.agent-staging/payloads/<id>.md` of `NAME: value` fields (implementer: BRANCH, PACKAGE; test
# authors: TEST_PATHS; both: DOSSIER or WORKTREE_DIR). A driver run without arguments reads them
# back, and only ever uses what is unambiguous.

FIELD_LINE = re.compile(r"^([A-Z][A-Z0-9_]*):[ \t]*(.*)$")


def payload_fields(path: str) -> dict[str, str]:
    """The top-level `NAME: value` fields of one payload file (a `|` block's lines joined by spaces)."""
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return {}
    out: dict[str, str] = {}
    name = None
    for line in lines:
        m = FIELD_LINE.match(line)
        if m:
            name = m.group(1)
            out.setdefault(name, "" if m.group(2) == "|" else m.group(2).strip())
            if m.group(2) == "|":
                out[name] = ""
        elif name and line[:1] in (" ", "\t") and line.strip() and name not in ("CONTRACT",):
            if len(out.get(name, "")) < 4000:
                out[name] = (out[name] + " " + line.strip()).strip()
        elif line.strip():
            name = None
    return out


def staged_payloads(wt: str) -> list[dict[str, str]]:
    folder = os.path.join(wt, ".agent-staging", "payloads")
    if not os.path.isdir(folder):
        return []
    return [payload_fields(os.path.join(folder, n)) for n in sorted(os.listdir(folder)) if n.endswith(".md")]


def derive_branches(wt: str) -> tuple[dict[str, str], list[str]]:
    """{package id: implementer branch} from the staged implementer payloads (BRANCH + PACKAGE), keeping a
    package only when every payload naming it agrees on one branch and that branch exists in the repository.
    Second item: why anything was left out."""
    found: dict[str, set[str]] = {}
    for f in staged_payloads(wt):
        if f.get("BRANCH") and f.get("PACKAGE"):
            found.setdefault(f["PACKAGE"].split()[0], set()).add(f["BRANCH"].strip())
    _, cur = git(wt, "rev-parse", "--abbrev-ref", "HEAD")
    out: dict[str, str] = {}
    notes: list[str] = []
    for pkg, branches in sorted(found.items()):
        if len(branches) > 1:
            notes.append(f"{pkg}: payloads name {len(branches)} branches ({', '.join(sorted(branches))})")
            continue
        br = next(iter(branches))
        rc, _ = git(wt, "rev-parse", "--verify", "--quiet", f"refs/heads/{br}")
        if rc != 0:
            notes.append(f"{pkg}: branch {br} does not exist")
        elif br == cur.strip():
            notes.append(f"{pkg}: branch {br} is the worktree's own branch")
        else:
            out[pkg] = br
    return out, notes


def derive_test_paths(wt: str) -> list[str]:
    """The TEST_PATHS of every staged test-author payload that exist inside `wt`, relative to it, in order."""
    root = os.path.realpath(wt)
    out: list[str] = []
    for f in staged_payloads(wt):
        for raw in re.split(r"[,\s]+", f.get("TEST_PATHS", "")):
            if not raw:
                continue
            full = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(wt, raw))
            if full != root and not full.startswith(root + os.sep):
                continue
            rel = os.path.relpath(full, root)
            if os.path.exists(full) and rel not in out:
                out.append(rel)
    return out


ID_IN_TEXT = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,6}-\d{1,6})(?![0-9])")


def derive_dossier(root: str, wt: str | None) -> str | None:
    """The dossier ID of a run with none given: the one ID the staged payloads' DOSSIER paths agree on, else
    the one ID in the worktree's branch name that has a dossier file. None when there is no unique answer."""
    ids: set[str] = set()
    for f in staged_payloads(wt) if wt else []:
        m = ID_IN_TEXT.match(os.path.basename(f.get("DOSSIER", "").split()[0]) if f.get("DOSSIER") else "")
        if m:
            ids.add(m.group(1))
    if len(ids) == 1:
        return ids.pop()
    if ids:
        return None
    if wt:
        _, br = git(wt, "rev-parse", "--abbrev-ref", "HEAD")
        cands = {m.group(1) for m in ID_IN_TEXT.finditer(br.strip())}
        real = set()
        for c in cands:
            try:
                if resolve_dossier(root, c, required=False, also=(wt,)):
                    real.add(c)
            except Surprise:
                pass
        if len(real) == 1:
            return real.pop()
    return None


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


def toplevel(start: str) -> str | None:
    """The working tree root containing `start` (a linked worktree's own root), or None outside git."""
    rc, out = sh([GIT, "rev-parse", "--show-toplevel"], cwd=start)
    return out.strip().splitlines()[-1] if rc == 0 and out.strip() else None


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


def evidence_root(root: str) -> str:
    """Where driver evidence lives: `asd-drivers/` in the git common dir, so it is never
    tracked and never makes a checkout dirty."""
    rc, out = sh([GIT, "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=root)
    common = out.strip().splitlines()[-1] if rc == 0 and out.strip() else os.path.join(root, ".git")
    return os.path.join(common, "asd-drivers")


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


def drive(name: str, argv: list[str] | None, build_parser: Callable, run: Callable, selftest: Callable[[], int],
          derive: tuple = ()) -> int:
    """Common entry: parse, --selftest, mode gate, run with surprise handling.

    `build_parser(ap)` adds the driver's own flags; `run(d, args)` does the
    steps on a Driver and returns 0 or 1 (raise Surprise for anything else).
    `derive` names the run context the driver fills in when its flag is missing:
    "worktree" (the working directory's git root) and "dossier" (see derive_dossier).
    """
    import argparse

    ap = argparse.ArgumentParser(description=f"{name}: a one-call mechanical-phase driver; prints evidence, rules nothing.")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    ap.add_argument("--mode", choices=MODES, help=f"override {env_name(name)} (tests only)")
    ap.add_argument("--dossier", help="dossier id, e.g. W-014 (names the evidence directory)")
    ap.add_argument("--root", help="the main checkout (default: found from the working directory)")
    ap.add_argument("--log", "--build-log", dest="log", metavar="DOSSIER_FILE", help="append the one-line summary to this dossier's Build log (mode on only)")
    build_parser(ap)
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    mode = "on" if name in driver_modes.GRADUATED else read_mode(name, args.mode)   # a graduated driver is the procedure
    if mode == "off":
        print(f"{env_name(name)} is off (the default): do the phase doc's manual steps. Set it to shadow or on to run this driver.")
        return EXIT_OFF
    derived: list[str] = []
    if "worktree" in derive and not getattr(args, "worktree", None):
        args.worktree = toplevel(os.getcwd())
        if args.worktree:
            derived.append(f"--worktree {args.worktree} (the working directory's git root)")
    root = args.root or main_checkout(getattr(args, "worktree", None) or os.getcwd())
    if "dossier" in derive and not args.dossier:
        args.dossier = derive_dossier(root, getattr(args, "worktree", None) or toplevel(os.getcwd()))
        if args.dossier:
            derived.append(f"--dossier {args.dossier} (from the staged payloads or the branch name)")
    d = Driver(name, mode, args.dossier, evidence_root(root), args.log)
    if derived:
        d.facts["derived_args"] = derived
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
    e["ASD_BUILD_SLOTS"] = "0"   # a selftest never waits on, or takes, a real host build slot (0 turns the limit off)
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
    with tempfile.TemporaryDirectory() as tmp:
        folder = os.path.join(tmp, ".discovery", "dossiers")
        for n in ("W-1.md", "W-2-slug.md", "W-3-a.md", "W-3-b.md", "W-22-other.md"):
            fixture_write(tmp, f".discovery/dossiers/{n}", "x\n")
        c("resolver: plain name", resolve_dossier(tmp, "W-1") == os.path.join(folder, "W-1.md"))
        c("resolver: slugged name", resolve_dossier(tmp, "W-2") == os.path.join(folder, "W-2-slug.md"))
        c("resolver: explicit path wins", resolve_dossier(tmp, "W-3", "/x/y.md") == "/x/y.md")
        c("resolver: W-2 does not match W-22", resolve_dossier(tmp, "W-22") == os.path.join(folder, "W-22-other.md"))
        try:
            resolve_dossier(tmp, "W-3")
            c("resolver: ambiguous raises", False)
        except Surprise as err:
            c("resolver: ambiguous names candidates", "W-3-a.md" in str(err) and "W-3-b.md" in str(err))
        try:
            resolve_dossier(tmp, "W-9")
            c("resolver: missing raises", False)
        except Surprise:
            c("resolver: missing raises", True)
        c("resolver: missing optional is None", resolve_dossier(tmp, "W-9", required=False) is None)
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
