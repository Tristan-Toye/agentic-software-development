#!/usr/bin/env python3
"""Keep and verify a whole wave of returned sub-agents in ONE call.

A wave used to spawn one small model per return, each only to copy a report into a
file and run verify_return.py (or to copy a reviewer document into a file). This
script does both mechanically for every return of the wave at once, so the
workflow spends one wrapper spawn per wave, not one per agent.

# Usage

    verify_wave.py < SPEC          # SPEC on stdin, a heredoc with a quoted delimiter
    verify_wave.py --selftest

SPEC is a sequence of sections:

    @@ASD-RETURN {"label": "P1", "path": "/abs/report.txt", "argv": ["--root", ...]}
    <the returned text, verbatim, any number of lines>
    @@ASD-END

`path` (optional) is where the body is written (parent directories created).
`argv` (optional) is a verify_return.py argument list, run with the Python that runs
this script; a section without `argv` is only written (a reviewer document). A
section with `"nobody": true` has no body (the agent returned nothing): nothing is
written, but its `argv` is still checked.

Prints one line per section:

    RESULT {"label": ..., "verdict": "clean"|"flagged", "flags": [...], "returned_line": "..."}
    WRITTEN {"label": ..., "path": ...}          (a write-only section)

and exits 0 when every section was handled, 1 when any section could not be (a
malformed section, a write failure, a verifier that did not run), 2 on a usage
error. A flagged return is not a failure of this script: its verdict is the result.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HEAD = "@@ASD-RETURN "
END = "@@ASD-END"


def parse(text: str) -> list[tuple[dict, str]]:
    sections: list[tuple[dict, str]] = []
    meta: dict | None = None
    body: list[str] = []
    for line in text.split("\n"):
        if meta is None:
            if line.startswith(HEAD):
                try:
                    meta = json.loads(line[len(HEAD):])
                except ValueError:
                    sections.append(({"label": "?", "bad": "header is not JSON"}, ""))
                    meta = None
                    continue
                if not isinstance(meta, dict):
                    sections.append(({"label": "?", "bad": "header is not an object"}, ""))
                    meta = None
                    continue
                body = []
            continue
        if line == END:
            sections.append((meta, "\n".join(body)))
            meta = None
        else:
            body.append(line)
    if meta is not None:
        sections.append(({"label": str(meta.get("label", "?")), "bad": "section has no " + END}, ""))
    return sections


def run_verify(argv: list[str]) -> tuple[str, list[str], str, int]:
    proc = subprocess.run([sys.executable, os.path.join(HERE, "verify_return.py"), *map(str, argv)],
                          capture_output=True, text=True)
    out = proc.stdout.splitlines()
    verdict_line = next((l for l in out if l.startswith("verdict:")), "")
    line = next((l[len("RETURNED-LINE: "):] for l in out if l.startswith("RETURNED-LINE: ")), "")
    if proc.returncode not in (0, 1) or not verdict_line:
        tail = (proc.stderr.strip() or proc.stdout.strip()).splitlines()[-1:] or ["no output"]
        return "flagged", ["verify-error"], tail[0][:200], proc.returncode
    if verdict_line.strip() == "verdict: clean":
        return "clean", [], line, proc.returncode
    flags = [f.strip() for f in verdict_line.split("FLAGGED", 1)[-1].split(",") if f.strip()]
    return "flagged", flags, line, proc.returncode


def handle(meta: dict, body: str) -> tuple[str, bool]:
    label = str(meta.get("label", "?"))
    if meta.get("bad"):
        return "RESULT " + json.dumps({"label": label, "verdict": "flagged", "flags": ["bad-spec"], "returned_line": meta["bad"]}), False
    ok = True
    path = meta.get("path")
    wrote = False
    if path and not meta.get("nobody"):
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body if body.endswith("\n") else body + "\n")
            wrote = True
        except OSError as err:
            ok = False
            if not meta.get("argv"):
                return "RESULT " + json.dumps({"label": label, "verdict": "flagged", "flags": ["write-failed"], "returned_line": str(err)[:200]}), False
    argv = meta.get("argv")
    if not argv:
        return "WRITTEN " + json.dumps({"label": label, "path": path if wrote else ""}), ok
    verdict, flags, line, _ = run_verify(argv)
    if path and not wrote and not meta.get("nobody"):
        flags = ["write-failed"] + flags
        verdict = "flagged"
    return "RESULT " + json.dumps({"label": label, "verdict": verdict, "flags": flags, "returned_line": line}), ok and "verify-error" not in flags


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--selftest"]:
        return selftest()
    if args:
        print(__doc__.split("\n")[0], file=sys.stderr)
        return 2
    sections = parse(sys.stdin.read())
    if not sections:
        print("verify_wave: no @@ASD-RETURN section on stdin", file=sys.stderr)
        return 2
    rc = 0
    for meta, body in sections:
        line, ok = handle(meta, body)
        print(line)
        if not ok:
            rc = 1
    return rc


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)
            print("FAIL " + name)

    def git(root: str, *a: str) -> str:
        return subprocess.run(["git", "-C", root, *a], capture_output=True, text=True, check=True).stdout.strip()

    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "tree")
        os.makedirs(root)
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "t@example.invalid")
        git(root, "config", "user.name", "t")
        with open(os.path.join(root, "a.txt"), "w") as fh:
            fh.write("a\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "base")
        base = git(root, "rev-parse", "HEAD")
        rep = os.path.join(tmp, "reports", "P1.txt")
        doc = os.path.join(tmp, "docs", "lens.md")
        spec = (
            HEAD + json.dumps({"label": "P1", "path": rep, "argv": ["--root", root, "--branch", "main", "--base", base,
                                                                    "--print-line", "--agent", "P1"]}) + "\n"
            "line one\nGAP: none\n" + END + "\n"
            + HEAD + json.dumps({"label": "doc", "path": doc}) + "\n## CR-1: x\n" + END + "\n"
            + HEAD + json.dumps({"label": "bad", "path": os.path.join(tmp, "x"), "argv": ["--root", os.path.join(tmp, "nope"),
                                                                                        "--branch", "m", "--base", base]}) + "\n" + END + "\n"
        )
        proc = subprocess.run([sys.executable, os.path.abspath(__file__)], input=spec, capture_output=True, text=True)
        lines = proc.stdout.splitlines()
        results = {}
        for l in lines:
            kind, _, rest = l.partition(" ")
            results[json.loads(rest)["label"]] = (kind, json.loads(rest))
        check("report written verbatim", os.path.exists(rep) and open(rep).read() == "line one\nGAP: none\n")
        check("document written", os.path.exists(doc) and open(doc).read() == "## CR-1: x\n")
        check("write-only section is WRITTEN", results.get("doc", ("",))[0] == "WRITTEN")
        # P1 made no commit: verify_return flags it, which is a result, not a script failure.
        check("verified section has a RESULT", results.get("P1", ("",))[0] == "RESULT")
        check("no-commit return is flagged", results.get("P1", ("", {}))[1].get("verdict") == "flagged")
        check("verifier error is flagged", results.get("bad", ("", {}))[1].get("flags") == ["verify-error"])
        check("exit 1 when a verifier did not run", proc.returncode == 1)
        check("parse: unterminated section is bad", parse(HEAD + '{"label": "z"}\nabc')[0][0].get("bad") is not None)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
