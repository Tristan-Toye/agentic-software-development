#!/usr/bin/env python3
"""The one switch for the mechanical-phase drivers, without touching the LaunchAgent.

The dashboard server runs as a LaunchAgent whose environment holds only PATH and
ASD_SLACK_USER, so `ASD_DRIVER_<NAME>` cannot be set there per run. Instead
run_dossier.py reads a small file and exports `ASD_DRIVER_<NAME>` into the claude
child of every run it starts:

    ~/.agentic/drivers.json          (override: env ASD_DRIVERS_FILE, or --file)
    {"pre_fanout_check": "shadow", "test_round": "shadow"}

A name missing from the file is `off`. An explicit `ASD_DRIVER_<NAME>` already in
the runner's environment wins over the file. An unknown name or mode is warned
about in the runner log and ignored. Runs already started keep the modes they
started with (their runner metadata records them); the file is read when a run
starts.

A run quota keeps the token cost small: `limit 3` means only the next 3 fresh
/work-on runs carry the modes in the file. Each one claims a place (its run key is
written under "_quota" in the same file, under a file lock). A run that finds the
quota full, and every run that is not /work-on, gets every driver off. The modes
stay in the file, so `show` still tells the story. A claimed run keeps its modes
for its whole life, even after the quota is full; only an explicit `off` takes
them away. No quota means every run carries the modes, as before.

One driver is script-only and costs no tokens (FREE: vr_promote_preview). It is exempt from the quota: a run
that finds the quota full, and a run that is not /work-on, still gets it exported with the mode in the file,
and it never claims a place. `show` marks it "(no quota)". `on` makes vr_promote_preview also write the Sequence preview tabs of the
Phase 3 review (vr_promote.py --shadow-all); that is the owner's call, no PR switches it, and `all on` leaves it at shadow.

Switching a driver on is graduation: the PR that does it rewrites the phase doc as the plain procedure, removes
the mode branch from the code and moves the name from NAMES to GRADUATED. A graduated name is always on and part
of the procedure: `set` and `show` say so, the runner exports nothing for it, and a stale key for it in an
existing drivers file is ignored without a warning. The selftest fails when a graduated name, or its
ASD_DRIVER_<NAME>, still appears in the docs the agents read (see GRADUATED_SCAN).

Usage:
    driver_modes.py show                      effective modes from the file
    driver_modes.py set NAME=MODE [...]       change some; the others stay
    driver_modes.py all MODE                  every driver to MODE
    driver_modes.py off                       every driver off (rollback)
    driver_modes.py limit N                   only the next N /work-on runs carry the modes
                                              (starts a fresh count; the claimed runs are listed by show)
    driver_modes.py limit N --per-repo        the next N /work-on runs of EACH repo carry the modes
                                              (the repo is the run key's part before its first dot)
    driver_modes.py limit none                no quota: every run carries the modes
    driver_modes.py --file PATH ...           another file than the default
    driver_modes.py --selftest

NAME is one of pre_fanout_check, strength_gate_prep,
test_round, vr_promote_preview; MODE is off, shadow or on. The file is written atomically.
Exit 0 done, 2 bad arguments or an unreadable file.

No third-party imports.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time

NAMES = ("pre_fanout_check", "strength_gate_prep", "test_round", "vr_promote_preview")
FREE = ("vr_promote_preview",)   # script-only, no tokens: exempt from the run quota
# Drivers that were switched on and are now just the procedure. name -> graduation date. Never a mode, never exported.
GRADUATED = {"flow_coverage_gate": "2026-11-20", "finalize_pr": "2026-11-23",
             "merge_packages": "2026-11-23"}
GRADUATED_SCAN = ("references", "primary-agents", "sub-agents", "skills", "claude", "opencode")   # dirs the agents read
NO_ON: dict = {}   # drivers with no `on` mode yet: name -> why (`set` refuses on, `all on` leaves them at shadow). None now.
ALL_ON_SKIP = ("vr_promote_preview",)   # `all on` leaves these at shadow: only an explicit `set name=on` (the owner's call) turns them on
MODES = ("off", "shadow", "on")
QUOTA_KEY = "_quota"   # in the drivers file next to the driver names: {"limit": N, "claimed": [{"run", "at"}]}
QUOTA_COMMAND = "work-on"


def env_name(name: str) -> str:
    return "ASD_DRIVER_" + name.upper()


def name_of(var: str) -> str:
    """The driver name of an ASD_DRIVER_<NAME> variable (the inverse of env_name)."""
    return var[len("ASD_DRIVER_"):].lower()


def default_file() -> str:
    return os.environ.get("ASD_DRIVERS_FILE") or os.path.expanduser("~/.agentic/drivers.json")


def load(path: str | None = None) -> tuple[dict[str, str], list[str]]:
    """(valid modes by driver name, warnings). A missing file is no modes and no warning; an
    unreadable one, an unknown name or an unknown mode is a warning and is ignored."""
    path = path or default_file()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return {}, []
    except (OSError, ValueError) as err:
        return {}, [f"drivers file {path} unreadable, ignored: {err}"]
    if not isinstance(raw, dict):
        return {}, [f"drivers file {path} is not a JSON object, ignored"]
    modes: dict[str, str] = {}
    warnings: list[str] = []
    for key, value in raw.items():
        if key == QUOTA_KEY:
            continue
        name = str(key).strip().lower()
        mode = str(value).strip().lower() if isinstance(value, str) else ""
        if name in GRADUATED:
            continue
        if name not in NAMES:
            warnings.append(f"drivers file: unknown driver {key!r} ignored (known: {', '.join(NAMES)})")
        elif mode not in MODES:
            warnings.append(f"drivers file: {key}: unknown mode {value!r} ignored (known: {', '.join(MODES)})")
        else:
            modes[name] = mode
    return modes, warnings


def resolve(environ: dict, path: str | None = None) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """(effective mode per driver, env vars to export into the child, warnings).
    An ASD_DRIVER_<NAME> already in `environ` wins and is not exported again."""
    file_modes, warnings = load(path)
    effective: dict[str, str] = {}
    exports: dict[str, str] = {}
    for name in NAMES:
        var = env_name(name)
        if var in environ:
            value = str(environ[var]).strip().lower()
            effective[name] = value if value in MODES else "off"
        elif name in file_modes:
            effective[name] = file_modes[name]
            exports[var] = file_modes[name]
        else:
            effective[name] = "off"
    return effective, exports, warnings


def _quota_of(raw: object) -> dict | None:
    """The valid quota of a parsed file, or None: {"limit": int >= 0, "claimed": [{"run": str, "at": str}, ...]},
    plus "per_repo": True when the limit counts each repo's runs on their own."""
    q = raw.get(QUOTA_KEY) if isinstance(raw, dict) else None
    if not isinstance(q, dict) or isinstance(q.get("limit"), bool) or not isinstance(q.get("limit"), int) or q["limit"] < 0:
        return None
    claimed = [c for c in (q.get("claimed") or []) if isinstance(c, dict) and isinstance(c.get("run"), str)]
    out = {"limit": q["limit"], "claimed": claimed}
    if q.get("per_repo") is True:
        out["per_repo"] = True
    return out


def repo_of(run_key: str) -> str:
    """The repo of a run key ("podcast-generation-engine.W-037" -> "podcast-generation-engine")."""
    return run_key.split(".", 1)[0]


def used_by(quota: dict, run_key: str) -> int:
    """The places that count against run_key's limit: all of them, or only its repo's under per_repo."""
    if not quota.get("per_repo"):
        return len(quota["claimed"])
    repo = repo_of(run_key)
    return sum(1 for c in quota["claimed"] if repo_of(c["run"]) == repo)


def load_quota(path: str | None = None) -> dict | None:
    """The quota in the file, or None when there is none (or the file is missing or unreadable)."""
    try:
        with open(path or default_file(), encoding="utf-8") as fh:
            return _quota_of(json.load(fh))
    except (OSError, ValueError):
        return None


def write(path: str, modes: dict[str, str], quota: dict | None = None) -> None:
    """Atomic: a temp file in the same directory, then rename. The quota (if any) goes in beside the modes."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    out: dict = {k: modes[k] for k in NAMES if k in modes}
    if quota is not None:
        out[QUOTA_KEY] = quota
    fd, tmp = tempfile.mkstemp(prefix=".drivers.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextlib.contextmanager
def locked(path: str):
    """An exclusive lock on <file>.lock, so two processes never read-modify-write the file at the same time."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def claim(run_key: str, path: str | None = None) -> bool:
    """Ask for a place in the quota for a fresh run. No quota: True (nothing is recorded). A run that already holds a
    place: True again, no second place. Quota full: False. Read, check and write happen under the file lock."""
    path = path or default_file()
    with locked(path):
        modes, _ = load(path)
        quota = load_quota(path)
        if quota is None:
            return True
        if any(c["run"] == run_key for c in quota["claimed"]):
            return True
        if used_by(quota, run_key) >= quota["limit"]:
            return False
        quota["claimed"].append({"run": run_key, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        write(path, modes, quota)
        return True


def graduated_text(name: str) -> str:
    return f"graduated (always on, part of the procedure) since {GRADUATED[name]}"


def graduated_leaks(root: str) -> list[str]:
    """`path:line` of every place under GRADUATED_SCAN (in repo `root`) that still names a graduated driver or its
    ASD_DRIVER_<NAME> variable: once graduated, the agents must not know the driver, its modes or shadow."""
    # the driver's script file (`finalize_pr.py`) is the procedure itself and stays; the bare name and the variable do not
    needles = re.compile("|".join([re.escape(n) + r"(?!\.py)" for n in GRADUATED] + [re.escape(env_name(n)) for n in GRADUATED]), re.I)
    found: list[str] = []
    for top in GRADUATED_SCAN:
        for dirpath, _dirs, files in os.walk(os.path.join(root, top)):
            for fn in sorted(files):
                full = os.path.join(dirpath, fn)
                try:
                    with open(full, encoding="utf-8") as fh:
                        for i, line in enumerate(fh, 1):
                            if needles.search(line):
                                found.append(f"{os.path.relpath(full, root)}:{i}")
                except (OSError, UnicodeDecodeError):
                    continue
    return found


def show(path: str) -> str:
    modes, warnings = load(path)
    rows = [f"drivers file: {path}" + ("" if os.path.exists(path) else " (absent: every driver off)")]
    for name in NAMES:
        rows.append(f"  {name:<20} {modes.get(name, 'off')}" + ("  (no quota)" if name in FREE else ""))
    for name in GRADUATED:
        rows.append(f"  {name:<20} {graduated_text(name)}")
    quota = load_quota(path)
    if quota is None:
        rows.append("  quota: none (every run carries the modes)")
    else:
        if quota.get("per_repo"):
            repos: dict[str, int] = {}
            for c in quota["claimed"]:
                repos[repo_of(c["run"])] = repos.get(repo_of(c["run"]), 0) + 1
            rows.append(f"  quota: {quota['limit']} per repo" + ("".join(f"; {r} {n} of {quota['limit']} used" + (" (used up)" if n >= quota["limit"] else "") for r, n in sorted(repos.items())) or "; none used"))
        else:
            used = len(quota["claimed"])
            rows.append(f"  quota: {used} of {quota['limit']} used" + (" (used up: new runs get every driver off)" if used >= quota["limit"] else ""))
        for c in quota["claimed"]:
            rows.append(f"    {c['run']}  {c.get('at', '')}")
    for w in warnings:
        rows.append(f"  warning: {w}")
    held = [f"{env_name(n)}={os.environ[env_name(n)]}" for n in NAMES if env_name(n) in os.environ]
    if held:
        rows.append("  this shell also has " + ", ".join(held) + " (a runner with the same variable would prefer it)")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Set the mechanical-phase driver modes run_dossier.py exports into its runs.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--file", help="the drivers file (default $ASD_DRIVERS_FILE or ~/.agentic/drivers.json)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("show", help="print the modes in the file")
    p_set = sub.add_parser("set", help="NAME=MODE ...")
    p_set.add_argument("pairs", nargs="+", metavar="NAME=MODE")
    p_all = sub.add_parser("all", help="every driver to MODE")
    p_all.add_argument("mode", choices=MODES)
    sub.add_parser("off", help="every driver off")
    p_limit = sub.add_parser("limit", help="only the next N /work-on runs carry the modes; 'none' clears the quota")
    p_limit.add_argument("count", metavar="N|none")
    p_limit.add_argument("--per-repo", action="store_true", help="N places for each repo instead of N in all")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    path = a.file or default_file()
    if a.cmd in (None, "show"):
        print(show(path))
        return 0
    if a.cmd == "limit":
        c = a.count.strip().lower()
        if c != "none" and not (c.isdigit() and int(c) >= 0):
            print("driver_modes.py: limit takes a number (0 or more) or 'none'", file=sys.stderr)
            return 2
        try:
            with locked(path):
                modes, _ = load(path)
                quota = None if c == "none" else {"limit": int(c), "claimed": []}
                if quota is not None and a.per_repo:
                    quota["per_repo"] = True
                write(path, modes, quota)
        except OSError as err:
            print(f"driver_modes.py: cannot write {path}: {err}", file=sys.stderr)
            return 2
        print(show(path))
        return 0
    try:
        with locked(path):
            return _change(a, path)
    except OSError as err:
        print(f"driver_modes.py: cannot write {path}: {err}", file=sys.stderr)
        return 2


def _change(a: argparse.Namespace, path: str) -> int:
    """set / all / off, with the file lock held; the quota (if any) is kept."""
    modes, _ = load(path)
    quota = load_quota(path)
    if a.cmd == "set":
        for pair in a.pairs:
            name, _, mode = pair.partition("=")
            name, mode = name.strip().lower(), mode.strip().lower()
            if name in GRADUATED:
                print(f"driver_modes.py: {name}: {graduated_text(name)}; nothing to set")
                continue
            if name not in NAMES or mode not in MODES:
                print(f"driver_modes.py: bad pair {pair!r}: NAME is one of {', '.join(NAMES)}; MODE is one of {', '.join(MODES)}", file=sys.stderr)
                return 2
            if mode == "on" and name in NO_ON:
                print(f"driver_modes.py: {name}=on refused: {NO_ON[name]}", file=sys.stderr)
                return 2
            modes[name] = mode
    elif a.cmd == "all":
        modes = {n: ("shadow" if (a.mode == "on" and (n in NO_ON or n in ALL_ON_SKIP)) else a.mode) for n in NAMES}
    elif a.cmd == "off":
        modes = {}
    try:
        write(path, modes, quota)
    except OSError as err:
        print(f"driver_modes.py: cannot write {path}: {err}", file=sys.stderr)
        return 2
    print(show(path))
    return 0


def selftest() -> int:
    failures = _selftest()
    if failures:
        print("driver_modes selftest FAILED: " + "; ".join(failures))
        return 1
    print("driver_modes selftest OK")
    return 0


def _selftest() -> list[str]:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return _run_selftest()


def _run_selftest() -> list[str]:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("ASD_DRIVER")}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "sub", "drivers.json")
            check("a missing file is no modes, no warning", load(f) == ({}, []))
            check("set creates the file", main(["--file", f, "set", "test_round=shadow", "pre_fanout_check=ON"]) == 0
                  and load(f) == ({"test_round": "shadow", "pre_fanout_check": "on"}, []))
            check("set keeps the others", main(["--file", f, "set", "strength_gate_prep=shadow"]) == 0 and load(f)[0]["test_round"] == "shadow"
                  and load(f)[0]["strength_gate_prep"] == "shadow")
            check("a bad pair is refused and writes nothing", main(["--file", f, "set", "nope=on"]) == 2 and main(["--file", f, "set", "test_round=maybe"]) == 2
                  and load(f)[0]["test_round"] == "shadow" and "nope" not in load(f)[0])
            check("all writes every driver", main(["--file", f, "all", "shadow"]) == 0 and load(f)[0] == {n: "shadow" for n in NAMES})
            check("no temp file is left behind", [x for x in os.listdir(os.path.dirname(f)) if x.endswith(".tmp")] == [])
            check("off clears", main(["--file", f, "off"]) == 0 and load(f) == ({}, []) and json.load(open(f)) == {})
            with open(f, "w") as fh:
                fh.write('{"test_round": "shadow", "bogus": "on", "strength_gate_prep": "maybe", "pre_fanout_check": 3}')
            modes, warns = load(f)
            check("unknown names and modes are warned about and ignored", modes == {"test_round": "shadow"} and len(warns) == 3)
            with open(f, "w") as fh:
                fh.write("{not json")
            check("a corrupt file warns and yields no modes", load(f)[0] == {} and len(load(f)[1]) == 1)
            with open(f, "w") as fh:
                fh.write('["x"]')
            check("a non-object file warns", load(f)[0] == {} and len(load(f)[1]) == 1)
            write(f, {"test_round": "shadow", "pre_fanout_check": "on"})
            eff, exp, _ = resolve({"ASD_DRIVER_TEST_ROUND": "off"}, f)
            check("an explicit environment variable wins and is not exported", eff["test_round"] == "off" and "ASD_DRIVER_TEST_ROUND" not in exp)
            check("the file's modes are exported", exp == {"ASD_DRIVER_PRE_FANOUT_CHECK": "on"} and eff["pre_fanout_check"] == "on" and eff["strength_gate_prep"] == "off")
            os.environ["ASD_DRIVERS_FILE"] = f
            check("ASD_DRIVERS_FILE moves the default", default_file() == f and load()[0]["pre_fanout_check"] == "on")
            del os.environ["ASD_DRIVERS_FILE"]
            check("show lists every driver and marks the script-only ones", all(n in show(f) for n in NAMES)
                  and show(f).count("(no quota)") == len(FREE))
            check("a graduated driver: set says so, writes nothing, exits 0", main(["--file", f, "set", "flow_coverage_gate=shadow"]) == 0
                  and "flow_coverage_gate" not in load(f)[0] and "flow_coverage_gate" not in open(f).read())
            check("show says graduated and not a mode", "flow_coverage_gate" in show(f) and "graduated (always on, part of the procedure)" in show(f))
            with open(f, "w") as fh:
                fh.write('{"test_round": "shadow", "flow_coverage_gate": "shadow", "_quota": {"limit": 1, "claimed": []}}')
            eff, exp, warns = resolve({}, f)
            check("a stale graduated key is ignored without a warning or an export", load(f) == ({"test_round": "shadow"}, []) and warns == []
                  and exp == {"ASD_DRIVER_TEST_ROUND": "shadow"} and "flow_coverage_gate" not in eff)
            check("writing the file drops the stale key", (write(f, load(f)[0], load_quota(f)), "flow_coverage_gate" not in open(f).read())[1])
            write(f, {"test_round": "shadow", "pre_fanout_check": "on"})
            check("no graduated driver is still a driver name", not (set(GRADUATED) & set(NAMES)))
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            check("a graduated driver is named nowhere the agents read: " + ", ".join(graduated_leaks(root)[:5]), graduated_leaks(root) == [])
            leak = os.path.join(tmp, "leakrepo")
            os.makedirs(os.path.join(leak, "references"))
            with open(os.path.join(leak, "references", "x.md"), "w") as fh:
                fh.write("ok\nWhen ASD_DRIVER_FLOW_COVERAGE_GATE is shadow\n")
            check("the leak scan finds a graduated name or variable", graduated_leaks(leak) == ["references/x.md:2"])
            check("vr_promote_preview=on is accepted (it writes the Sequence preview tabs)", main(["--file", f, "set", "vr_promote_preview=on"]) == 0
                  and load(f)[0]["vr_promote_preview"] == "on" and main(["--file", f, "set", "vr_promote_preview=shadow"]) == 0)
            check("all on keeps vr_promote_preview at shadow (only set turns it on)", main(["--file", f, "all", "on"]) == 0 and load(f)[0]["vr_promote_preview"] == "shadow"
                  and load(f)[0]["test_round"] == "on")
            # the mechanism for a driver with no on mode (none today): refused by set, held at shadow by all
            NO_ON["vr_promote_preview"] = "test"
            try:
                check("a NO_ON driver: set on is refused and all on keeps it at shadow", main(["--file", f, "set", "vr_promote_preview=on"]) == 2
                      and main(["--file", f, "all", "on"]) == 0 and load(f)[0]["vr_promote_preview"] == "shadow" and load(f)[0]["test_round"] == "on")
            finally:
                NO_ON.clear()
            check("the free drivers are known names", set(FREE) <= set(NAMES) and name_of(env_name("vr_promote_preview")) == "vr_promote_preview")
            # ---- the run quota
            q = os.path.join(tmp, "quota", "drivers.json")
            check("no quota: claim is always true and records nothing", claim("r0", q) is True and not os.path.exists(q))
            main(["--file", q, "set", "test_round=shadow"])
            check("no quota in a file with modes: claim true, file unchanged", claim("r0", q) and load_quota(q) is None and "claimed" not in open(q).read())
            check("limit 2 stores the quota beside the modes", main(["--file", q, "limit", "2"]) == 0 and load_quota(q) == {"limit": 2, "claimed": []}
                  and load(q) == ({"test_round": "shadow"}, []))
            check("the quota key is not read as a driver name", load(q)[1] == [])
            check("the first two claims succeed, the third is refused", claim("r1", q) and claim("r2", q) and not claim("r3", q))
            check("a claimed run claims again without a new place", claim("r1", q) and [c["run"] for c in (load_quota(q) or {})["claimed"]] == ["r1", "r2"])
            check("a full quota leaves the modes alone", load(q)[0] == {"test_round": "shadow"})
            shown = show(q)
            check("show prints the quota and the claimed runs", "2 of 2 used" in shown and "r1" in shown and "r2" in shown and "used up" in shown)
            check("set keeps the quota and its claims", main(["--file", q, "set", "strength_gate_prep=on"]) == 0 and len((load_quota(q) or {})["claimed"]) == 2)
            check("off keeps the quota", main(["--file", q, "off"]) == 0 and (load_quota(q) or {})["limit"] == 2 and load(q)[0] == {})
            check("limit N starts a fresh count", main(["--file", q, "limit", "3"]) == 0 and load_quota(q) == {"limit": 3, "claimed": []})
            check("a bad limit is refused", main(["--file", q, "limit", "many"]) == 2 and main(["--file", q, "limit", "-1"]) == 2 and (load_quota(q) or {})["limit"] == 3)
            check("limit none clears the quota", main(["--file", q, "limit", "none"]) == 0 and load_quota(q) is None and QUOTA_KEY not in json.load(open(q)))
            check("limit 2 --per-repo stores per_repo", main(["--file", q, "limit", "2", "--per-repo"]) == 0 and load_quota(q) == {"limit": 2, "claimed": [], "per_repo": True})
            check("per repo: each repo gets its own 2 places", claim("a.W-1", q) and claim("a.W-2", q) and not claim("a.W-3", q) and claim("b.W-1", q) and claim("b.stub-x.resolve-1", q) and not claim("b.W-9", q))
            check("per repo: show lists each repo", "a 2 of 2 used (used up)" in show(q) and "b 2 of 2 used (used up)" in show(q))
            check("plain limit after per-repo drops per_repo", main(["--file", q, "limit", "1"]) == 0 and load_quota(q) == {"limit": 1, "claimed": []})
            with open(q, "w") as fh:
                fh.write('{"test_round": "shadow"}')
            check("an old file with only modes still reads", load(q) == ({"test_round": "shadow"}, []) and load_quota(q) is None)
            # concurrent claims from separate processes never pass the limit
            c = os.path.join(tmp, "conc", "drivers.json")
            main(["--file", c, "set", "test_round=shadow"])
            main(["--file", c, "limit", "3"])
            code = "import sys; sys.path.insert(0, %r); import driver_modes as d; print(d.claim(sys.argv[2], sys.argv[1]))" % os.path.dirname(os.path.abspath(__file__))
            procs = [subprocess.Popen([sys.executable, "-c", code, c, f"run{i}"], stdout=subprocess.PIPE, text=True) for i in range(12)]
            got = [pr.communicate()[0].strip() for pr in procs]
            check("12 concurrent claims on a quota of 3: exactly 3 win", got.count("True") == 3 and got.count("False") == 9
                  and len((load_quota(c) or {})["claimed"]) == 3 and len({x["run"] for x in (load_quota(c) or {})["claimed"]}) == 3)
    finally:
        os.environ.update(saved)
    return failures


if __name__ == "__main__":
    sys.exit(main())
