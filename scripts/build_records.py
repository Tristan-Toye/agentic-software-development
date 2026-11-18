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
    build_records.py checklist usage W-014 --dossier D [--surface NAME=MEMBER,MEMBER ...] [--log DOSSIER]
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
import subprocess
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


EXCLUDE_LINE = ".discovery/deferred/"


def exclude_store(path: str) -> bool:
    """Keep the deferred store (plugin working state: `<ID>.yaml` and its `.lock`)
    out of `git status` when PATH lies in a repository's MAIN checkout: add
    `.discovery/deferred/` to the repository's `.git/info/exclude` (local, never
    a tracked change), once. A worktree or a non-repo is left alone. Returns True
    when a line was added."""
    d = os.path.dirname(os.path.realpath(path))
    def git(*a: str) -> str:
        r = subprocess.run(["git", "-C", d, *a], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    common, top = git("rev-parse", "--path-format=absolute", "--git-common-dir"), git("rev-parse", "--show-toplevel")
    if not common or not top or os.path.dirname(os.path.realpath(common)) != os.path.realpath(top):
        return False
    ex = os.path.join(common, "info", "exclude")
    try:
        have = open(ex, encoding="utf-8").read() if os.path.exists(ex) else ""
        if EXCLUDE_LINE in (x.strip() for x in have.splitlines()):
            return False
        os.makedirs(os.path.dirname(ex), exist_ok=True)
        with open(ex, "a", encoding="utf-8") as fh:
            fh.write(("" if not have or have.endswith("\n") else "\n") + EXCLUDE_LINE + "\n")
    except OSError:
        return False
    return True


def update(path: str, default: dict, fn) -> dict:
    """Read-modify-write under one lock; `fn` mutates the mapping and returns a note."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.basename(os.path.dirname(path)) == "deferred":
        exclude_store(path)
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


def criteria_items(dossier_text: str) -> list[tuple[int, str]]:
    """The numbered `## Acceptance criteria`, continuation lines folded in and
    the `(owner: ...; env: ...)` annotation dropped, so a test file's name never
    counts as a mention of the member it is named after."""
    m = re.search(r"^## Acceptance criteria\s*$(.*?)(?=^## |\Z)", dossier_text, re.M | re.S)
    if not m:
        return []
    items: list[tuple[int, str]] = []
    for line in m.group(1).splitlines():
        n = re.match(r"^\s*(\d+)[.)]\s+(.*)", line)
        if n:
            items.append((int(n.group(1)), n.group(2)))
        elif items and line.strip():
            items[-1] = (items[-1][0], items[-1][1] + " " + line.strip())
    return [(k, re.sub(r"\(owner:[^)]*\)", "", t)) for k, t in items]


def member_pattern(member: str) -> re.Pattern:
    """A member id names itself by its last segment (`Queue.flush`,
    `flush`), matched as a whole identifier."""
    last = re.split(r"\.|::|#", member.strip("`"))[-1]
    return re.compile(r"(?<![\w$])%s(?![\w$])" % re.escape(last))


def checklist_usage(checklist_text: str, dossier_text: str,
                    surfaces: dict[str, list[str]] | None = None) -> dict:
    """How often the acceptance criteria exercise each checklist member, and,
    given the Phase 3 surface split, each surface: the usage half of the
    mutation check's surface choice (references/work-on/phase-6-conditional.md).
    A surface's weight is the number of DISTINCT criteria naming any of its
    members, so a surface with many members is not counted twice for one
    criterion. A proxy for an operational profile (Musa), never a measured one."""
    items = criteria_items(dossier_text)
    members = checklist_members(checklist_text)
    per_member = {m: [k for k, t in items if member_pattern(m).search(t)] for m in members}
    out: dict = {"criteria": len(items), "members": per_member, "surfaces": {}, "usage_surface": []}
    if surfaces:
        unknown = sorted({m for ms in surfaces.values() for m in ms} - set(members))
        if unknown:
            raise Refused("--surface names member(s) the checklist does not: %s" % ", ".join(unknown))
        unassigned = [m for m in members if not any(m in ms for ms in surfaces.values())]
        if unassigned:
            raise Refused("checklist member(s) in no --surface: %s — every member belongs "
                          "to the surface whose unit author covers it" % ", ".join(unassigned))
        for name, ms in surfaces.items():
            out["surfaces"][name] = sorted({k for m in ms for k in per_member[m]})
        top = max((len(v) for v in out["surfaces"].values()), default=0)
        if top:
            out["usage_surface"] = [n for n, v in out["surfaces"].items() if len(v) == top]
    return out


def usage_lines(u: dict) -> list[str]:
    lines = []
    for m, ks in sorted(u["members"].items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lines.append("USAGE: %s %d/%d (%s)" % (m, len(ks), u["criteria"],
                                              "criteria " + ", ".join(map(str, ks)) if ks else "no criterion"))
    for name, ks in sorted(u["surfaces"].items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lines.append("SURFACE-USAGE: %s %d/%d (%s)" % (name, len(ks), u["criteria"],
                                                      "criteria " + ", ".join(map(str, ks)) if ks else "no criterion"))
    if u["surfaces"]:
        lines.append("USAGE-SURFACE: " + (", ".join(u["usage_surface"]) if u["usage_surface"]
                                         else "none — no criterion names a checklist member"))
    return lines


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
    p = c.add_parser("usage")
    common(p)
    p.add_argument("--dossier", required=True, help="the dossier whose ## Acceptance criteria are counted")
    p.add_argument("--surface", action="append", default=[], metavar="NAME=MEMBER[,MEMBER...]",
                   help="one per Phase 3 surface: the checklist members its unit author covers")

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
            elif args.verb == "usage":
                surfaces: dict[str, list[str]] = {}
                for spec in args.surface:
                    name, sep, ms = spec.partition("=")
                    if not sep or not name.strip() or not ms.strip():
                        raise Refused("--surface %r is not NAME=MEMBER[,MEMBER...]" % spec)
                    surfaces[name.strip()] = [m.strip() for m in ms.split(",") if m.strip()]
                with open(path, encoding="utf-8") as fh:
                    checklist = fh.read()
                with open(args.dossier, encoding="utf-8") as fh:
                    dossier_text = fh.read()
                u = checklist_usage(checklist, dossier_text, surfaces or None)
                if not u["criteria"]:
                    raise Refused("%s has no numbered ## Acceptance criteria" % args.dossier)
                lines = usage_lines(u)
                print("\n".join(lines))
                if surfaces:
                    say_log(args, "- " + lines[-1] + " — "
                            + "; ".join(l.split(": ", 1)[1] for l in lines if l.startswith("SURFACE-USAGE")))
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
        # usage: which criteria exercise which member and surface
        cl = ("Queue.flush — order: oldest first\nQueue.drain — empty case: 0\n"
              "parse — invalid case: raises\nStore.put — return meaning: id\n")
        dos = ("# T\n\n## Acceptance criteria\n\n"
               "1. With 3 parallel `flush()` calls the store receives each item once.\n"
               "   (owner: tests/integration/test_parse_flow.py; env: local)\n"
               "2. After `flush()` then `drain()`, put is called once per item.\n"
               "3. A malformed line makes `parse` raise. (owner: tests/unit/test_flush.py; env: local)\n"
               "4. flushing is logged. (owner: t.py; env: local)\n\n## Work packages\n\n5. parse flush\n")
        u = checklist_usage(cl, dos, {"queue": ["Queue.flush", "Queue.drain"], "codec": ["parse"],
                                      "store": ["Store.put"]})
        check("usage counts criteria per member, last segment, whole identifier",
              u["members"] == {"Queue.flush": [1, 2], "Queue.drain": [2], "parse": [3], "Store.put": [2]},
              )
        check("the owner annotation never counts (test_parse_flow.py, test_flush.py)",
              1 not in u["members"]["parse"] and 3 not in u["members"]["Queue.flush"])
        check("a surface counts distinct criteria, not member mentions",
              u["surfaces"] == {"queue": [1, 2], "codec": [3], "store": [2]})
        check("the usage surface is the most-exercised one", u["usage_surface"] == ["queue"])
        lines = usage_lines(u)
        check("usage lines end with USAGE-SURFACE", lines[-1] == "USAGE-SURFACE: queue"
              and "USAGE: Queue.flush 2/4 (criteria 1, 2)" in lines)
        check("a tie names every tied surface",
              checklist_usage(cl, dos, {"a": ["Queue.drain", "Queue.flush"], "b": ["parse", "Store.put"]})
              ["usage_surface"] == ["a", "b"])
        check("a member in no surface is refused",
              refused(checklist_usage, cl, dos, {"queue": ["Queue.flush"]}))
        check("a surface naming an unknown member is refused",
              refused(checklist_usage, cl, dos, {"queue": ["nope"], "x": ["Queue.flush", "Queue.drain",
                                                                          "parse", "Store.put"]}))
        # the CLI end to end
        cdir = os.path.join(tmp, ".discovery", "checklists")
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "W-004.md"), "w") as fh:
            fh.write(cl)
        dpath = os.path.join(tmp, "W-004.md")
        with open(dpath, "w") as fh:
            fh.write("---\nid: W-004\n---\n\n" + dos.replace("## Work packages", "## Build log"))
        rc = run(["checklist", "usage", "W-004", "--root", tmp, "--dossier", dpath,
                  "--surface", "queue=Queue.flush,Queue.drain", "--surface", "rest=parse,Store.put",
                  "--log", dpath])
        with open(dpath) as fh:
            check("the usage CLI logs the USAGE-SURFACE line", rc == 0 and "- USAGE-SURFACE: queue, rest — queue 2/4" in fh.read())
        check("a malformed --surface is refused with exit 1",
              run(["checklist", "usage", "W-004", "--root", tmp, "--dossier", dpath, "--surface", "queue"]) == 1)
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
    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        repo = os.path.join(tmp, "app")
        os.makedirs(repo)
        for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", repo, *a], capture_output=True, check=True)
        open(os.path.join(repo, "f"), "w").write("x")
        subprocess.run(["git", "-C", repo, "add", "."], capture_output=True, check=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "i"], capture_output=True, check=True)
        dp = record_path(os.path.join(repo, ".discovery"), "deferred", "W-001")
        deferred_add(dp, dict(evidence="e", fix="f", boundary="b", blast_radius="r"), [])
        deferred_add(dp, dict(evidence="e", fix="f", boundary="b", blast_radius="r"), [])
        exf = os.path.join(repo, ".git", "info", "exclude")
        check("store under a main checkout is excluded exactly once",
              open(exf).read().splitlines().count(EXCLUDE_LINE) == 1)
        st = subprocess.run(["git", "-C", repo, "status", "--porcelain"], capture_output=True, text=True).stdout
        check("the store (yaml and lock) no longer shows in git status", st.strip() == "")
        wt = os.path.join(tmp, "app-W-1")
        subprocess.run(["git", "-C", repo, "worktree", "add", "-q", "-b", "w1", wt], capture_output=True, check=True)
        before = open(exf).read()
        deferred_add(record_path(os.path.join(wt, ".discovery"), "deferred", "W-001"), dict(evidence="e", fix="f", boundary="b", blast_radius="r"), [])
        check("a worktree's store changes no exclude file", open(exf).read() == before)
    print("selftest:", "ok" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
