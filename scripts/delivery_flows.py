#!/usr/bin/env python3
"""Which delivery flows a repository has switched on: `stub`, `kanban`, or both.

    delivery_flows.py --repo NAME [--root R]       print {"stub": bool, "kanban": bool}
    delivery_flows.py --set stub|kanban on|off --repo NAME
    delivery_flows.py --selftest

The settings file is `~/.agentic/delivery-flows.json` (`ASD_DELIVERY_FLOWS` replaces the path):
`{"<repo dir name>": {"stub": true, "kanban": false}}`. A missing repo or key is stub on, kanban off; both off
is invalid and reads as stub on. `--root R` derives the name from the repository's main checkout (a worktree
resolves to its main repo's name). `--set` refuses to turn the last flow off, writes atomically (tmp + rename) and
only creates an entry for the repo being toggled. Importable: `flows_of(repo) -> dict`.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

FLOWS = ("stub", "kanban")
DEFAULT = {"stub": True, "kanban": False}


def settings_path() -> str:
    return os.environ.get("ASD_DELIVERY_FLOWS") or os.path.expanduser("~/.agentic/delivery-flows.json")


def load() -> dict:
    try:
        with open(settings_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def flows_of(repo: str) -> dict:
    """The {stub, kanban} switches of one repo name; defaults fill gaps, both off reads as stub on."""
    entry = load().get(repo)
    entry = entry if isinstance(entry, dict) else {}
    out = {k: entry[k] if isinstance(entry.get(k), bool) else DEFAULT[k] for k in FLOWS}
    return out if any(out.values()) else dict(DEFAULT)


def repo_name(root: str) -> str:
    """Basename of the main checkout of `root` (a worktree resolves to its main repository)."""
    root = os.path.realpath(root)
    try:
        p = subprocess.run(["git", "-C", root, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, timeout=20)
        common = p.stdout.strip()
        if p.returncode == 0 and os.path.basename(common) == ".git":
            return os.path.basename(os.path.dirname(common))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return os.path.basename(root)


def set_flow(repo: str, flow: str, on: bool) -> dict:
    """Switch one flow; refuses (ValueError) to leave both off. Atomic write."""
    cur = flows_of(repo)
    new = {**cur, flow: on}
    if not any(new.values()):
        raise ValueError(f"{repo}: refusing to turn {flow} off, it is the last flow on")
    data = load()
    data[repo] = new
    path = settings_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".delivery-flows.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return new


def selftest() -> int:
    bad: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        os.environ["ASD_DELIVERY_FLOWS"] = os.path.join(td, "sub", "flows.json")

        def check(name: str, ok: bool) -> None:
            if not ok:
                bad.append(name)

        check("missing file: stub on, kanban off", flows_of("r") == {"stub": True, "kanban": False})
        check("reading creates no file", not os.path.exists(settings_path()))
        check("kanban on keeps stub on", set_flow("r", "kanban", True) == {"stub": True, "kanban": True})
        check("stub off leaves kanban only", set_flow("r", "stub", False) == {"stub": False, "kanban": True})
        try:
            set_flow("r", "kanban", False)
            check("last flow off is refused", False)
        except ValueError:
            pass
        check("refusal left the file alone", flows_of("r") == {"stub": False, "kanban": True})
        check("other repos untouched, no entry written", flows_of("o") == DEFAULT and list(load()) == ["r"])
        with open(settings_path(), "w") as f:
            json.dump({"x": {"stub": False, "kanban": False}, "y": {"kanban": True}, "z": "junk"}, f)
        check("both off reads as stub on", flows_of("x") == DEFAULT)
        check("missing key defaults", flows_of("y") == {"stub": True, "kanban": True})
        check("junk entry defaults", flows_of("z") == DEFAULT)
        with open(settings_path(), "w") as f:
            f.write("{not json")
        check("corrupt file defaults", flows_of("r") == DEFAULT)
        check("no tmp files left", not [n for n in os.listdir(os.path.dirname(settings_path())) if n.startswith(".")])
        check("repo_name of a plain dir is its basename", repo_name(td) == os.path.basename(os.path.realpath(td)))
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--repo", "r"], capture_output=True, text=True,
                           env=os.environ)
        check("CLI prints json", r.returncode == 0 and json.loads(r.stdout) == DEFAULT)
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--set", "stub", "off", "--repo", "q"],
                           capture_output=True, text=True, env=os.environ)
        check("CLI refuses last flow off, exit 1", r.returncode == 1)
    for b in bad:
        print(f"FAIL  {b}")
    print("selftest: " + ("FAIL" if bad else "ok"))
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", help="repository directory name")
    ap.add_argument("--root", help="repository root; the name is derived from it when --repo is absent")
    ap.add_argument("--set", nargs=2, metavar=("FLOW", "on|off"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    repo = a.repo or (repo_name(a.root) if a.root else None)
    if not repo:
        ap.error("--repo or --root is required")
    if a.set:
        flow, state = a.set
        if flow not in FLOWS or state not in ("on", "off"):
            ap.error("--set takes stub|kanban on|off")
        try:
            print(json.dumps(set_flow(repo, flow, state == "on")))
        except ValueError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 1
        return 0
    print(json.dumps(flows_of(repo)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
