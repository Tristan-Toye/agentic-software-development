#!/usr/bin/env python3
"""The structured records of a build: one home per fact, read by gates and tools.

`## Build log` is the human view. The facts a gate or a tool depends on live
in files under `.discovery/` next to the dossier, written by this script (and
by nothing else), each also summarised as ONE build-log line when `--log
DOSSIER` is given. No reader depends on that line.

    .discovery/deferred/W-xxx.yaml     {"items": [...], "sources": [...]}
    .discovery/decisions/W-xxx.yaml    {"decisions": [...]}
    .discovery/checklists/W-xxx.md     the PROMISE_CHECKLIST, one line per promise

The `.yaml` files are written as JSON (a YAML subset) so this script, the
validator and the dashboard read them with no YAML dependency.

# Usage

    build_records.py deferred add W-014 --evidence 'src/a.py:40 retry ignores 429' --fix '...' \\
        --boundary 'contract: none' --blast-radius 'one package' [--id D-3] [--state open] \\
        [--title T] [--kind bug] [--loc src/a.py:40] [--source 'own reads'] [--log DOSSIER]
    build_records.py deferred none W-014 --source 'own reads' --source 'review replies' [--log DOSSIER]
    build_records.py deferred set W-014 D-3 --state fixed [--note SHA]            [--log DOSSIER]
    build_records.py deferred show W-014
    build_records.py decision add W-014 --decision '...' --alternatives '...' --why '...' \\
        --evidence '...' --phase 6                                                [--log DOSSIER]
    build_records.py decision show W-014
    build_records.py checklist write W-014 --from-file F                          [--log DOSSIER]
    build_records.py checklist show W-014
    build_records.py --selftest

`--root DIR` (default: the current directory) is the tree whose `.discovery/`
holds the records; `--discovery DIR` names the `.discovery` directory itself.
`deferred add` without `--id` takes the next free D-n. A NOTICED item raised
after the ledger is closed is one more `deferred add`.

Deferred states: open | fixing | fixed | wontfix | dossier. Exit codes: 0 done;
1 refused; 2 unusable input. No third-party imports.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile

try:
    import fcntl
except ImportError:  # not POSIX: the atomic rename still holds
    fcntl = None  # type: ignore

DEFERRED_STATES = ("open", "fixing", "fixed", "wontfix", "dossier")
ITEM_REQUIRED = ("id", "evidence", "fix", "boundary", "blast_radius", "state")
ITEM_OPTIONAL = ("title", "kind", "loc", "note")
DECISION_KEYS = ("decision", "alternatives", "why", "evidence", "phase")
WID_RE = re.compile(r"^W-\d+$")
DID_RE = re.compile(r"^D-\d+$")


class Refused(Exception):
    pass


def discovery_dir(root: str | None, discovery: str | None) -> str:
    return discovery or os.path.join(root or os.getcwd(), ".discovery")


def record_path(disc: str, kind: str, wid: str) -> str:
    if not WID_RE.match(wid):
        raise Refused(f"{wid!r} is not a dossier id like W-014")
    return os.path.join(disc, kind, wid + (".md" if kind == "checklists" else ".yaml"))


def load(path: str, default: dict) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return default
    except ValueError as err:
        raise Refused(f"{path} is not readable as the record format: {err}")
    if not isinstance(data, dict):
        raise Refused(f"{path} must hold one mapping")
    for k, v in default.items():
        data.setdefault(k, v)
    return data


def atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".rec-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def update(path: str, default: dict, fn) -> dict:
    """Read-modify-write under one lock; `fn` mutates the mapping and returns a note."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = open(path + ".lock", "w")
    try:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        data = load(path, default)
        note = fn(data)
        atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        return {"data": data, "note": note}
    finally:
        lock.close()


# -- deferred ---------------------------------------------------------------

def next_id(items: list[dict]) -> str:
    nums = [int(i["id"][2:]) for i in items if DID_RE.match(str(i.get("id", "")))]
    return f"D-{max(nums, default=0) + 1}"


def deferred_add(path: str, fields: dict, sources: list[str]) -> dict:
    for k in ("evidence", "fix", "boundary", "blast_radius"):
        if not str(fields.get(k) or "").strip():
            raise Refused(f"a deferred item needs {k.replace('_', '-')}")
    state = fields.get("state") or "open"
    if state not in DEFERRED_STATES:
        raise Refused(f"state {state!r} is not one of {'|'.join(DEFERRED_STATES)}")

    def fn(data: dict) -> str:
        items = data["items"]
        did = fields.get("id") or next_id(items)
        if not DID_RE.match(did):
            raise Refused(f"{did!r} is not an id like D-3")
        if any(i.get("id") == did for i in items):
            raise Refused(f"{did} is already in the record; use `deferred set`")
        item = {k: fields[k] for k in ("evidence", "fix", "boundary", "blast_radius")}
        item = {"id": did, **item, "state": state}
        for k in ITEM_OPTIONAL:
            if fields.get(k):
                item[k] = fields[k]
        items.append(item)
        for s in sources:
            if s not in data["sources"]:
                data["sources"].append(s)
        return did

    return update(path, {"items": [], "sources": []}, fn)


def deferred_none(path: str, sources: list[str]) -> dict:
    if not sources:
        raise Refused("`deferred none` needs at least one --source: what was checked")

    def fn(data: dict) -> str:
        if data["items"]:
            raise Refused("the record already holds items; it is not empty")
        for s in sources:
            if s not in data["sources"]:
                data["sources"].append(s)
        return "none"

    return update(path, {"items": [], "sources": []}, fn)


def deferred_set(path: str, did: str, state: str, note: str | None) -> dict:
    if state not in DEFERRED_STATES:
        raise Refused(f"state {state!r} is not one of {'|'.join(DEFERRED_STATES)}")

    def fn(data: dict) -> str:
        for item in data["items"]:
            if item.get("id") == did:
                old = item.get("state")
                item["state"] = state
                if note is not None:
                    item["note"] = note
                return f"{old} -> {state}"
        raise Refused(f"{did} is not in the record")

    return update(path, {"items": [], "sources": []}, fn)


def validate_deferred(data: dict) -> list[str]:
    """Problems with a deferred record: what the validate gate refuses."""
    out: list[str] = []
    items, sources = data.get("items"), data.get("sources")
    if not isinstance(items, list):
        return ["`items` must be a list"]
    if not items and not (isinstance(sources, list) and sources):
        out.append("`items` is empty and `sources` names nothing: say what was checked")
    for n, item in enumerate(items):
        if not isinstance(item, dict):
            out.append(f"item {n} is not a mapping")
            continue
        miss = [k for k in ITEM_REQUIRED if not str(item.get(k) or "").strip()]
        if miss:
            out.append(f"item {item.get('id', n)} lacks {', '.join(miss)}")
        if item.get("state") not in DEFERRED_STATES:
            out.append(f"item {item.get('id', n)} has state {item.get('state')!r}")
    return out


# -- decisions --------------------------------------------------------------

def decision_add(path: str, fields: dict) -> dict:
    for k in DECISION_KEYS:
        if not str(fields.get(k) or "").strip():
            raise Refused(f"a decision needs {k}")

    def fn(data: dict) -> str:
        data["decisions"].append({k: fields[k] for k in DECISION_KEYS})
        return str(len(data["decisions"]))

    return update(path, {"decisions": []}, fn)


# -- checklist --------------------------------------------------------------

CHECKLIST_LINE_RE = re.compile(r"^\s*[-*]?\s*(?P<member>[^\s—:][^—:]*?)\s+—\s+(?P<cat>[^:]+?):\s*\S")


def checklist_members(text: str) -> list[str]:
    """The member ids a checklist covers: `<member> — <category>: <promise>` lines."""
    seen: list[str] = []
    for line in text.splitlines():
        m = CHECKLIST_LINE_RE.match(line)
        if m and m.group("member") not in seen:
            seen.append(m.group("member"))
    return seen


# -- CLI --------------------------------------------------------------------

def say_log(args, line: str) -> None:
    if getattr(args, "log", None):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import dossier_edit

        print(dossier_edit.log_or_say(args.log, [line]))


def run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="section")

    def common(p):
        p.add_argument("dossier_id")
        p.add_argument("--root")
        p.add_argument("--discovery")
        p.add_argument("--log", metavar="DOSSIER", help="also append one summary line to this dossier's ## Build log")

    d = sub.add_parser("deferred").add_subparsers(dest="verb", required=True)
    p = d.add_parser("add")
    common(p)
    for k in ("evidence", "fix", "boundary", "blast-radius", "id", "state", "title", "kind", "loc", "note"):
        p.add_argument("--" + k)
    p.add_argument("--source", action="append", default=[])
    p = d.add_parser("none")
    common(p)
    p.add_argument("--source", action="append", default=[])
    p = d.add_parser("set")
    common(p)
    p.add_argument("item")
    p.add_argument("--state", required=True)
    p.add_argument("--note")
    common(d.add_parser("show"))

    n = sub.add_parser("decision").add_subparsers(dest="verb", required=True)
    p = n.add_parser("add")
    common(p)
    for k in DECISION_KEYS:
        p.add_argument("--" + k)
    common(n.add_parser("show"))

    c = sub.add_parser("checklist").add_subparsers(dest="verb", required=True)
    p = c.add_parser("write")
    common(p)
    p.add_argument("--from-file", required=True, help="the checklist text; - reads stdin")
    common(c.add_parser("show"))

    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.section:
        ap.error("a subcommand is required unless --selftest")
    disc = discovery_dir(args.root, args.discovery)
    try:
        if args.section == "deferred":
            path = record_path(disc, "deferred", args.dossier_id)
            if args.verb == "show":
                print(json.dumps(load(path, {"items": [], "sources": []}), indent=2, ensure_ascii=False))
            elif args.verb == "add":
                fields = {k: getattr(args, k) for k in ("evidence", "fix", "boundary", "id", "state", *ITEM_OPTIONAL)}
                fields["blast_radius"] = args.blast_radius
                r = deferred_add(path, fields, args.source)
                print(f"build_records: {r['note']} added to {path}")
                say_log(args, f"- {r['note']} deferred (open): {(args.title or args.evidence)[:120]} — record: {path}")
            elif args.verb == "none":
                deferred_none(path, args.source)
                print(f"build_records: no deferred items; sources: {', '.join(args.source)}")
                say_log(args, f"- DEFERRED: none — sources: {', '.join(args.source)}")
            else:
                r = deferred_set(path, args.item, args.state, args.note)
                print(f"build_records: {args.item} {r['note']}")
                say_log(args, f"- {args.item} deferred {r['note']}" + (f" — {args.note}" if args.note else ""))
        elif args.section == "decision":
            path = record_path(disc, "decisions", args.dossier_id)
            if args.verb == "show":
                print(json.dumps(load(path, {"decisions": []}), indent=2, ensure_ascii=False))
            else:
                r = decision_add(path, {k: getattr(args, k) for k in DECISION_KEYS})
                print(f"build_records: decision {r['note']} recorded in {path}")
                say_log(args, f"- DECISION (phase {args.phase}): {args.decision[:140]} — record: {path}")
        else:
            path = record_path(disc, "checklists", args.dossier_id)
            if args.verb == "show":
                with open(path, encoding="utf-8") as fh:
                    sys.stdout.write(fh.read())
            else:
                text = sys.stdin.read() if args.from_file == "-" else open(args.from_file, encoding="utf-8").read()
                members = checklist_members(text)
                if not members:
                    raise Refused("no `<member> — <category>: <promise>` line in the checklist")
                atomic_write(path, text if text.endswith("\n") else text + "\n")
                print(f"build_records: checklist for {len(members)} member(s) written to {path}")
                say_log(args, f"- PROMISE_CHECKLIST: {len(members)} member(s) — record: {path}")
    except Refused as err:
        print(f"build_records: REFUSED — {err}", file=sys.stderr)
        return 1
    except OSError as err:
        print(f"build_records: {err}", file=sys.stderr)
        return 2
    return 0


def selftest() -> int:
    ok = True

    def check(name: str, passed: bool) -> None:
        nonlocal ok
        ok = ok and passed
        print(f"{'PASS' if passed else 'FAIL'}  {name}")

    def refused(fn, *a) -> bool:
        try:
            fn(*a)
        except Refused:
            return True
        return False

    with tempfile.TemporaryDirectory() as tmp:
        dp = record_path(tmp, "deferred", "W-001")
        fields = dict(evidence="a.py:1 bug", fix="patch", boundary="none", blast_radius="one package")
        r1 = deferred_add(dp, fields, ["own reads"])
        r2 = deferred_add(dp, {**fields, "title": "second"}, ["own reads", "review"])
        check("add numbers D-1 then D-2", (r1["note"], r2["note"]) == ("D-1", "D-2"))
        check("sources merge without duplicates", load(dp, {})["sources"] == ["own reads", "review"])
        check("a duplicate id is refused", refused(deferred_add, dp, {**fields, "id": "D-1"}, []))
        check("a missing field is refused", refused(deferred_add, dp, {"evidence": "x"}, []))
        deferred_set(dp, "D-1", "fixed", "abc123")
        item = load(dp, {})["items"][0]
        check("set changes state and note", (item["state"], item["note"]) == ("fixed", "abc123"))
        check("an unknown state is refused", refused(deferred_set, dp, "D-1", "done", None))
        check("an unknown item is refused", refused(deferred_set, dp, "D-9", "open", None))
        check("a valid record has no problems", validate_deferred(load(dp, {})) == [])
        check("an empty record without sources is a problem", validate_deferred({"items": [], "sources": []}) != [])
        check("an empty record with sources is fine", validate_deferred({"items": [], "sources": ["x"]}) == [])
        np = record_path(tmp, "deferred", "W-002")
        check("none needs a source", refused(deferred_none, np, []))
        deferred_none(np, ["own reads"])
        check("none writes items: [] plus sources", load(np, {}) == {"items": [], "sources": ["own reads"]})
        check("none on a record with items is refused", refused(deferred_none, dp, ["x"]))
        xp = record_path(tmp, "decisions", "W-001")
        dec = dict(decision="use A", alternatives="B", why="faster", evidence="a.py:9", phase="6")
        decision_add(xp, dec)
        check("a decision is appended", load(xp, {})["decisions"] == [dec])
        check("a decision missing why is refused", refused(decision_add, xp, {**dec, "why": ""}))
        check("a bad dossier id is refused", refused(record_path, tmp, "deferred", "x"))
        text = "flush — return meaning: count written\n- flush — order: oldest first\nparse — empty case: 0\n"
        check("checklist members are read", checklist_members(text) == ["flush", "parse"])
        # the CLI end to end, with a dossier that takes the log line
        dossier = os.path.join(tmp, "W-003.md")
        with open(dossier, "w") as fh:
            fh.write("---\nid: W-003\nstatus: building\n---\n\n# T\n\n## Build log\n")
        rc = run(["deferred", "add", "W-003", "--root", tmp, "--evidence", "e", "--fix", "f", "--boundary", "b",
                  "--blast-radius", "r", "--log", dossier])
        with open(dossier) as fh:
            log = fh.read()
        check("the CLI writes the record and one log line", rc == 0 and "- D-1 deferred (open)" in log
              and load(record_path(os.path.join(tmp, ".discovery"), "deferred", "W-003"), {})["items"][0]["id"] == "D-1")
        check("the CLI refuses a missing field with exit 1", run(["deferred", "add", "W-003", "--root", tmp, "--evidence", "e"]) == 1)
    print("selftest:", "ok" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
