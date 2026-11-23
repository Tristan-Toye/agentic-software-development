#!/usr/bin/env python3
"""Stage a contract surface for a blind test author from the source file (#519).

A surface under `.agent-staging/contract-support/` is the bytes of the source file at the
contract commit, never the output of a navigation tool (code_item / code_outline cut long
items; an author then returns GAP: for a shape past the cut). This script writes it from
`git show <ref>:<path>` with an optional line range, and re-checks it against the source.

  stage_surface.py stage --root X --ref SHA --name grant PATH[:START-END] ...
      writes X/.agent-staging/contract-support/surface-<name>.md and prints its path
  stage_surface.py check --root X FILE...
      exit 1 when a staged surface differs from its source slice, or ends mid-item
      (unbalanced braces/brackets in the slice)
  stage_surface.py --selftest

Each staged part is `<!-- stage_surface: PATH @ REF lines A-B of N -->` then the lines verbatim.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

HEADER = re.compile(r"^<!-- stage_surface: (?P<path>.+) @ (?P<ref>\S+) lines (?P<a>\d+)-(?P<b>\d+) of (?P<n>\d+) -->$")


def show(root: str, ref: str, path: str) -> list[str]:
    done = subprocess.run(["git", "-C", root, "show", "%s:%s" % (ref, path)], capture_output=True)
    if done.returncode != 0:
        raise SystemExit("stage_surface: git show %s:%s failed: %s" % (ref, path, done.stderr.decode().strip()))
    return done.stdout.decode("utf-8", errors="replace").splitlines(keepends=True)


def parse_spec(spec: str) -> tuple[str, int | None, int | None]:
    m = re.fullmatch(r"(.+?):(\d+)-(\d+)", spec)
    return (m.group(1), int(m.group(2)), int(m.group(3))) if m else (spec, None, None)


def balanced(lines: list[str]) -> bool:
    """False when the slice opens more braces/brackets/parens than it closes (cut mid-item)."""
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', "".join(l for l in lines if not l.lstrip().startswith(("//", "#"))))
    return all(text.count(o) <= text.count(c) for o, c in ("{}", "[]", "()"))


def parts_of(text: str) -> list[tuple[dict, list[str]]]:
    parts: list[tuple[dict, list[str]]] = []
    for line in text.splitlines(keepends=True):
        m = HEADER.match(line.rstrip("\n"))
        if m:
            parts.append((m.groupdict(), []))
        elif parts:
            parts[-1][1].append(line)
    return parts


def stage(root: str, ref: str, name: str, specs: list[str]) -> str:
    out = []
    for spec in specs:
        path, a, b = parse_spec(spec)
        src = show(root, ref, path)
        a, b = a or 1, min(b or len(src), len(src))
        out.append("<!-- stage_surface: %s @ %s lines %d-%d of %d -->\n" % (path, ref, a, b, len(src)))
        out.extend(src[a - 1:b])
        if out and not out[-1].endswith("\n"):
            out.append("\n")
    target = os.path.join(root, ".agent-staging", "contract-support", "surface-%s.md" % name)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("".join(out))
    return target


def check(root: str, file: str) -> list[str]:
    bad: list[str] = []
    parts = parts_of(open(file, encoding="utf-8", errors="replace").read())
    if not parts:
        return ["%s: no stage_surface header; stage it with stage_surface.py stage, not from tool output" % file]
    for meta, body in parts:
        a, b, n = int(meta["a"]), int(meta["b"]), int(meta["n"])
        src = show(root, meta["ref"], meta["path"])
        if len(src) != n:
            bad.append("%s: %s has %d lines at %s, header says %d" % (file, meta["path"], len(src), meta["ref"], n))
        elif [l.rstrip("\n") for l in src[a - 1:b]] != [l.rstrip("\n") for l in body]:
            bad.append("%s: %s lines %d-%d differ from the source (%d staged, %d expected)"
                       % (file, meta["path"], a, b, len(body), b - a + 1))
        elif not balanced(body):
            bad.append("%s: %s lines %d-%d end mid-item (unbalanced brackets); widen the range"
                       % (file, meta["path"], a, b))
    return bad


def selftest() -> int:
    fails: list[str] = []

    def ok(name: str, cond: bool, detail: object = "") -> None:
        if not cond:
            fails.append("%s %s" % (name, detail))

    with tempfile.TemporaryDirectory() as tmp:
        def git(*a: str) -> None:
            subprocess.run(["git", "-C", tmp, "-c", "user.email=t@t", "-c", "user.name=t", *a],
                           check=True, capture_output=True)
        git("init", "-q")
        src = "public record A(int X);\npublic class B\n{\n    int F();\n}\n"
        with open(os.path.join(tmp, "a.cs"), "w") as fh:
            fh.write(src)
        git("add", "a.cs")
        git("commit", "-qm", "x")
        target = stage(tmp, "HEAD", "t", ["a.cs"])
        ok("whole file staged verbatim", open(target).read().endswith(src), open(target).read())
        ok("whole file checks clean", check(tmp, target) == [], check(tmp, target))
        cut = stage(tmp, "HEAD", "cut", ["a.cs:1-3"])
        ok("a slice ending mid-item is reported", any("mid-item" in b for b in check(tmp, cut)), check(tmp, cut))
        full = stage(tmp, "HEAD", "rng", ["a.cs:2-5"])
        ok("a whole-item range is clean", check(tmp, full) == [], check(tmp, full))
        with open(target, "a") as fh:
            fh.write("extra\n")
        txt = open(target).read().replace("int F();", "int G();")
        with open(target, "w") as fh:
            fh.write(txt)
        ok("a doctored surface is reported", any("differ" in b for b in check(tmp, target)), check(tmp, target))
        raw = os.path.join(tmp, "raw.md")
        with open(raw, "w") as fh:
            fh.write(src)
        ok("a surface with no header is reported", any("no stage_surface header" in b for b in check(tmp, raw)))
    for f in fails:
        print("SELFTEST FAIL", f)
    print("selftest: %d failure(s)" % len(fails))
    return 1 if fails else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--selftest"]:
        return selftest()
    import argparse
    ap = argparse.ArgumentParser(prog="stage_surface.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("stage")
    st.add_argument("--root", required=True)
    st.add_argument("--ref", required=True)
    st.add_argument("--name", required=True)
    st.add_argument("specs", nargs="+")
    ck = sub.add_parser("check")
    ck.add_argument("--root", required=True)
    ck.add_argument("files", nargs="+")
    ns = ap.parse_args(argv)
    if ns.cmd == "stage":
        print(stage(ns.root, ns.ref, ns.name, ns.specs))
        return 0
    bad = [b for f in ns.files for b in check(ns.root, f)]
    print("\n".join(bad) if bad else "stage_surface: %d surface(s) match their source" % len(ns.files))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
