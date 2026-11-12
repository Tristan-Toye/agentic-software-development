#!/usr/bin/env python3
"""The stub a new build targets: the accepting stub, a fresh cut, or a wait.

    stub_target.py --root R [--wait] [--dry-run]
    stub_target.py --selftest

Prints the branch and exits 0 when there is one to build on:
- the **accepting stub**: the newest `stub/*` on origin with no pull request into development;
- else, when every stub's pull request is merged (or there is no stub), a new `stub/<today>` cut from
  `origin/development` (pushed, idempotent: an existing branch of that name is used as it is).

Exits 3 and prints `wait <stub>` when the newest stub is **being integrated** (its pull request into development is
open): a new stub is cut only after that one has merged, so it starts with the whole integrated batch. With `--wait`
it polls (`ASD_STUB_POLL` seconds, default 300) until the stub merges, then cuts and prints the new one. Under the
runner the build ends its turn with `RUNNER: WAITING stub` instead, and the runner waits without spending tokens.
`--dry-run` cuts nothing (prints `cut stub/<today>`). Exit 2 on a git or gh failure. `ASD_GH_BIN` replaces gh.
"""
from __future__ import annotations

import argparse
import datetime
import os
import shlex
import subprocess
import sys
import time


def sh(cmd: list[str], cwd: str | None = None, timeout: int = 120) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return p.returncode, (p.stdout if p.returncode == 0 else p.stdout + p.stderr).strip()


def gh(root: str, *args: str) -> tuple[int, str]:
    return sh(shlex.split(os.environ.get("ASD_GH_BIN") or "gh") + list(args), cwd=root)


def stubs(root: str) -> list[str]:
    """The stub branches on origin. `git ls-remote` over ssh needs a user lookup (getpwuid), which a long-lived process
    loses after the nightly logout ("No user exists for uid"); so it is tried ASD_STUB_TRIES times (default 3, backing off
    ASD_STUB_RETRY_WAIT seconds, default 5, doubling), then the same list is read through gh's HTTPS API."""
    tries = max(1, int(os.environ.get("ASD_STUB_TRIES") or 3))
    wait = float(os.environ.get("ASD_STUB_RETRY_WAIT") or 5)
    out = ""
    for i in range(tries):
        rc, out = sh(["git", "-C", root, "ls-remote", "--heads", "origin", "stub/*"])
        if rc == 0:
            return sorted((l.split("\t")[1].removeprefix("refs/heads/") for l in out.splitlines() if "\t" in l), reverse=True)
        if i + 1 < tries:
            time.sleep(wait * 2 ** i)
    grc, gout = gh(root, "api", "repos/{owner}/{repo}/git/matching-refs/heads/stub/", "--jq", ".[].ref")
    if grc == 0:
        return sorted((l.strip().removeprefix("refs/heads/") for l in gout.splitlines() if l.strip()), reverse=True)
    raise RuntimeError("git ls-remote failed: " + out[:200] + " (gh api: " + gout[:100] + ")")


def into_dev(root: str, branch: str) -> str:
    rc, out = gh(root, "pr", "list", "--head", branch, "--base", "development", "--state", "all", "--json", "state", "-q", '[.[].state] | join(",")')
    if rc != 0:
        raise RuntimeError("gh pr list failed: " + out[:200])
    states = out.split(",") if out else []
    return "open" if "OPEN" in states else "merged" if "MERGED" in states else ""


def target(root: str, dry: bool) -> tuple[int, str]:
    """(0, branch) | (3, 'wait <stub>')."""
    integrating = ""
    for b in stubs(root):
        st = into_dev(root, b)
        if not st:
            return 0, b
        if st == "open" and not integrating:
            integrating = b
    if integrating:
        return 3, f"wait {integrating}"
    new = "stub/" + datetime.date.today().isoformat()
    if dry:
        return 0, f"cut {new}"
    sh(["git", "-C", root, "fetch", "-q", "origin", "development"])
    rc, out = sh(["git", "-C", root, "push", "origin", f"origin/development:refs/heads/{new}"])
    if rc != 0 and "already exists" not in out and "rejected" not in out:
        raise RuntimeError(f"could not cut {new}: {out[:200]}")
    return 0, new


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--root")
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.root:
        ap.print_usage(sys.stderr); return 2
    try:
        while True:
            rc, out = target(a.root, a.dry_run)
            if rc == 0 or not a.wait:
                print(out); return rc
            time.sleep(float(os.environ.get("ASD_STUB_POLL") or 300))
    except RuntimeError as e:
        print(f"stub_target: {e}", file=sys.stderr); return 2


FAKE = r'''
import json, os, sys
a = sys.argv[1:]; d = os.environ["FAKE_DIR"]
st = json.load(open(os.path.join(d, "states.json")))
if a[:2] == ["pr", "list"]: print(st.get(a[a.index("--head") + 1], "")); sys.exit(0)
if a[:1] == ["api"]:
    if st.get("_api_down"): sys.stderr.write("gh down\n"); sys.exit(1)
    print("\n".join("refs/heads/" + k for k in st if k.startswith("stub/"))); sys.exit(0)
sys.exit(3)
'''


def selftest() -> int:
    import json, tempfile
    fails = []
    def check(name: str, ok: bool, why: str = "") -> None:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok or not why else "  — " + why))
        if not ok: fails.append(name)
    with tempfile.TemporaryDirectory() as tmp:
        g = lambda *x, cwd=None: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *x], cwd=cwd, capture_output=True, text=True)
        g("init", "-q", "--bare", "-b", "development", os.path.join(tmp, "o.git"))
        g("clone", "-q", os.path.join(tmp, "o.git"), os.path.join(tmp, "w")); w = os.path.join(tmp, "w")
        g("commit", "-q", "--allow-empty", "-m", "b", cwd=w); g("push", "-q", "origin", "HEAD:development", "HEAD:stub/2026-11-03", cwd=w)
        open(os.path.join(tmp, "gh.py"), "w").write(FAKE)
        os.environ.update(ASD_GH_BIN=f"{sys.executable} {os.path.join(tmp, 'gh.py')}", FAKE_DIR=tmp)
        put = lambda d: json.dump(d, open(os.path.join(tmp, "states.json"), "w"))
        put({}); check("an accepting stub is the target", target(w, True) == (0, "stub/2026-11-03"))
        put({"stub/2026-11-03": "OPEN"}); check("a stub being integrated means wait; nothing is cut", target(w, False) == (3, "wait stub/2026-11-03"))
        put({"stub/2026-11-03": "MERGED"})
        rc, b = target(w, False)
        heads = subprocess.run(["git", "-C", w, "ls-remote", "--heads", "origin", "stub/*"], capture_output=True, text=True).stdout
        check("once merged, a new stub is cut from development", rc == 0 and b == "stub/" + datetime.date.today().isoformat() and b in heads, b + heads)
        check("the new stub is then the accepting one", target(w, False) == (0, b))
        # ls-remote fails (a lost user lookup): retried, then read through gh's HTTPS API; both failing is an error
        os.environ.update(ASD_STUB_RETRY_WAIT="0")
        put({})
        check("ls-remote healthy: no fallback", stubs(w) == [b, "stub/2026-11-03"] or stubs(w) == ["stub/2026-11-03", b] or len(stubs(w)) == 2)
        put({"stub/2026-11-09": ""})
        broken = os.path.join(tmp, "nope")
        g("remote", "set-url", "origin", broken, cwd=w)
        check("ls-remote failing: the stub list comes from gh api, and the target still resolves", stubs(w) == ["stub/2026-11-09"] and target(w, True) == (0, "stub/2026-11-09"))
        put({"_api_down": True})
        try:
            stubs(w); ok = False
        except RuntimeError as e:
            ok = "git ls-remote failed" in str(e) and "gh api" in str(e)
        check("both failing: an error (the runner's wait retries it), never a guess", ok)
    print("FAILED: " + ", ".join(fails) if fails else "all ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
