#!/usr/bin/env python3
"""The one switch for the five mechanical-phase drivers, without touching the LaunchAgent.

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

Usage:
    driver_modes.py show                      effective modes from the file
    driver_modes.py set NAME=MODE [...]       change some; the others stay
    driver_modes.py all MODE                  every driver to MODE
    driver_modes.py off                       every driver off (rollback)
    driver_modes.py --file PATH ...           another file than the default
    driver_modes.py --selftest

NAME is one of finalize_pr, merge_packages, pre_fanout_check, strength_gate_prep,
test_round; MODE is off, shadow or on. The file is written atomically.
Exit 0 done, 2 bad arguments or an unreadable file.

No third-party imports.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile

NAMES = ("finalize_pr", "merge_packages", "pre_fanout_check", "strength_gate_prep", "test_round")
MODES = ("off", "shadow", "on")


def env_name(name: str) -> str:
    return "ASD_DRIVER_" + name.upper()


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
        name = str(key).strip().lower()
        mode = str(value).strip().lower() if isinstance(value, str) else ""
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


def write(path: str, modes: dict[str, str]) -> None:
    """Atomic: a temp file in the same directory, then rename."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".drivers.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({k: modes[k] for k in NAMES if k in modes}, fh, indent=1)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def show(path: str) -> str:
    modes, warnings = load(path)
    rows = [f"drivers file: {path}" + ("" if os.path.exists(path) else " (absent: every driver off)")]
    for name in NAMES:
        rows.append(f"  {name:<20} {modes.get(name, 'off')}")
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
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    path = a.file or default_file()
    if a.cmd in (None, "show"):
        print(show(path))
        return 0
    modes, _ = load(path)
    if a.cmd == "set":
        for pair in a.pairs:
            name, _, mode = pair.partition("=")
            name, mode = name.strip().lower(), mode.strip().lower()
            if name not in NAMES or mode not in MODES:
                print(f"driver_modes.py: bad pair {pair!r}: NAME is one of {', '.join(NAMES)}; MODE is one of {', '.join(MODES)}", file=sys.stderr)
                return 2
            modes[name] = mode
    elif a.cmd == "all":
        modes = {n: a.mode for n in NAMES}
    elif a.cmd == "off":
        modes = {}
    try:
        write(path, modes)
    except OSError as err:
        print(f"driver_modes.py: cannot write {path}: {err}", file=sys.stderr)
        return 2
    print(show(path))
    return 0


def selftest() -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return _selftest()


def _selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("ASD_DRIVER")}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "sub", "drivers.json")
            check("a missing file is no modes, no warning", load(f) == ({}, []))
            check("set creates the file", main(["--file", f, "set", "test_round=shadow", "finalize_pr=ON"]) == 0
                  and load(f) == ({"test_round": "shadow", "finalize_pr": "on"}, []))
            check("set keeps the others", main(["--file", f, "set", "merge_packages=shadow"]) == 0 and load(f)[0]["test_round"] == "shadow"
                  and load(f)[0]["merge_packages"] == "shadow")
            check("a bad pair is refused and writes nothing", main(["--file", f, "set", "nope=on"]) == 2 and main(["--file", f, "set", "test_round=maybe"]) == 2
                  and load(f)[0]["test_round"] == "shadow" and "nope" not in load(f)[0])
            check("all writes every driver", main(["--file", f, "all", "shadow"]) == 0 and load(f)[0] == {n: "shadow" for n in NAMES})
            check("no temp file is left behind", [x for x in os.listdir(os.path.dirname(f)) if x.endswith(".tmp")] == [])
            check("off clears", main(["--file", f, "off"]) == 0 and load(f) == ({}, []) and json.load(open(f)) == {})
            with open(f, "w") as fh:
                fh.write('{"test_round": "shadow", "bogus": "on", "finalize_pr": "maybe", "merge_packages": 3}')
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
            check("the file's modes are exported", exp == {"ASD_DRIVER_PRE_FANOUT_CHECK": "on"} and eff["pre_fanout_check"] == "on" and eff["finalize_pr"] == "off")
            os.environ["ASD_DRIVERS_FILE"] = f
            check("ASD_DRIVERS_FILE moves the default", default_file() == f and load()[0]["pre_fanout_check"] == "on")
            del os.environ["ASD_DRIVERS_FILE"]
            check("show lists all five", all(n in show(f) for n in NAMES))
    finally:
        os.environ.update(saved)
    if failures:
        print("driver_modes selftest FAILED: " + "; ".join(failures))
        return 1
    print("driver_modes selftest OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
