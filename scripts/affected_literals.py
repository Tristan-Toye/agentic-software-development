#!/usr/bin/env python3
"""Phase 2's affected-literal sweep: which existing tests hold a literal the
contract changes.

A contract that renames a column, drops a key or extends a list leaves every
pre-existing test that spells the old literal (a column list, a key map, a
hoisted expected value) failing for a reason the contract already knew. Those
tests surfaced as row-3 arbitrations after the fan-out, each one a re-spawn.
This sweep moves them into the first fan-out: every such test is listed as
`AFFECTED_TESTS` with the work package that owns it, and one no package owns is
a defect to fix before the contract commit (add the test to a package's owned
paths, or add a package).

What it reads (read-only, stdlib, no model):

    git diff -U2 BASE [HEAD]    the contract's change to files that already
                                existed at BASE (new stub files have no old
                                literal to break)
    git ls-tree -r BASE         the test files that already existed at BASE

A quoted string literal in a changed hunk is a *token*. A token on a removed
line and not on an added line is *dropped or renamed* (strong). Every token of
a hunk that adds or removes a token forms a *group* (a column list, a key map).
A test file is affected when it spells a dropped token, or two or more tokens
of one group, or any `--literal` the orchestrator names (a count, a constant
the diff cannot show: `--literal 26`). Documentation comments are ignored.

Usage:
    affected_literals.py --root X --dossier DOSSIER.md [--base SHA] [--head REF]
                         [--literal TEXT ...]
    affected_literals.py --selftest

`--base` defaults to the dossier's `baseline_commit`; without `--head` the
working tree is compared (the sweep runs before the contract commit). Output is
one `AFFECTED_TESTS:` block (paste it into `## Build log`). Exit 0 when every
affected test has an owner or none is affected, 1 when a test has no owning
package, 2 on a usage or git surprise.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STRING_RE = re.compile(r'"((?:[^"\\\n]|\\.){2,60})"|\'((?:[^\'\\\n]|\\.){2,60})\'')
COMMENT_RE = re.compile(r"^\s*(?://|#|\*|/\*|--|;|\"\"\"|''')")
TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs)(/|$)|(^|/)test_[^/]*$|_test\.[A-Za-z]+$"
    r"|\.(test|spec)\.[A-Za-z]+$|Tests?\.[A-Za-z]+$")
SKIP_SUFFIX = (".md", ".txt", ".lock", ".json", ".yml", ".yaml", ".toml", ".svg", ".png")
MAX_TOKENS_PER_GROUP = 40


class Surprise(Exception):
    pass


def git(root: str, *args: str) -> str:
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    p = subprocess.run(["/usr/bin/git", "-C", root, *args], capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise Surprise("git %s failed: %s" % (" ".join(args[:2]), p.stderr.strip()[:200]))
    return p.stdout


def is_test(path: str) -> bool:
    return bool(TEST_PATH_RE.search(path))


def tokens_of(line: str) -> set[str]:
    if COMMENT_RE.match(line):
        return set()
    return {a or b for a, b in STRING_RE.findall(line)}


def parse_diff(diff: str) -> list[dict]:
    """Hunks of the diff as {file, removed, added, all} token sets, for files
    that are not tests and not prose/data."""
    hunks: list[dict] = []
    cur_file = ""
    skip = True
    hunk: dict | None = None
    for raw in diff.splitlines():
        if raw.startswith("diff --git"):
            hunk = None
            skip = True
        elif raw.startswith("--- "):
            old = raw[4:].strip()
            skip = old == "/dev/null"  # a new file has no old literal
        elif raw.startswith("+++ "):
            cur_file = raw[4:].strip()
            cur_file = cur_file[2:] if cur_file.startswith("b/") else cur_file
            if is_test(cur_file) or cur_file.endswith(SKIP_SUFFIX) or cur_file == "/dev/null":
                skip = True
        elif raw.startswith("@@"):
            hunk = {"file": cur_file, "removed": set(), "added": set(), "all": set()}
            if not skip:
                hunks.append(hunk)
        elif hunk is not None and not skip and raw[:1] in ("+", "-", " "):
            toks = tokens_of(raw[1:])
            hunk["all"] |= toks
            if raw[0] == "-":
                hunk["removed"] |= toks
            elif raw[0] == "+":
                hunk["added"] |= toks
    return hunks


def build_groups(hunks: list[dict]) -> tuple[set[str], list[set[str]]]:
    strong: set[str] = set()
    groups: list[set[str]] = []
    for h in hunks:
        changed = h["removed"] | h["added"]
        if not changed:
            continue
        strong |= h["removed"] - h["added"]
        if 2 <= len(h["all"]) <= MAX_TOKENS_PER_GROUP:
            groups.append(set(h["all"]))
    return strong, groups


def lit_re(token: str, quoted: bool) -> re.Pattern:
    body = re.escape(token)
    if quoted:
        return re.compile(r"[\"']" + body + r"[\"']")
    return re.compile(r"(?<![\w.])" + body + r"(?![\w])")


def sweep_file(text: str, strong: set[str], groups: list[set[str]], literals: list[str]) -> list[tuple[int, str]]:
    """(line, token) hits when the file is affected, else []."""
    lines = text.splitlines()

    def hits_of(tokens, quoted):
        out = []
        for tok in sorted(tokens):
            rx = lit_re(tok, quoted)
            for i, line in enumerate(lines, 1):
                if rx.search(line):
                    out.append((i, tok))
        return out

    found = hits_of(strong, True) + hits_of(literals, False)
    for group in groups:
        g = hits_of(group, True)
        if len({t for _, t in g}) >= 2:
            found += g
    return sorted(set(found))


def baseline_of(dossier: str) -> str:
    with open(dossier, encoding="utf-8") as fh:
        m = re.search(r"^baseline_commit:\s*(\S+)", fh.read(), re.M)
    if not m:
        raise Surprise("the dossier has no `baseline_commit:`; pass --base")
    return m.group(1).strip("'\"")


def owner_of(path: str, rows: list[dict]) -> str | None:
    import merge_packages as mp
    for row in rows:
        if mp.inside(path, row["owned"]):
            return row["id"]
    return None


def run(root: str, dossier: str, base: str | None, head: str | None, literals: list[str]) -> tuple[int, str]:
    import merge_packages as mp
    with open(dossier, encoding="utf-8") as fh:
        rows = mp.parse_package_table(fh.read())
    base = base or baseline_of(dossier)
    git(root, "rev-parse", "--verify", base + "^{commit}")
    diff = git(root, "diff", "-U2", "--no-color", base, *([head] if head else []))
    strong, groups = build_groups(parse_diff(diff))
    tests = [p for p in git(root, "ls-tree", "-r", "--name-only", base).splitlines()
             if is_test(p) and not p.endswith(SKIP_SUFFIX)]
    out: list[str] = []
    unowned = 0
    for path in sorted(tests):
        full = os.path.join(root, path)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                hits = sweep_file(fh.read(), strong, groups, literals)
        except OSError:
            continue
        if not hits:
            continue
        owner = owner_of(path, rows)
        unowned += owner is None
        shown = ", ".join("%d:%s" % (n, t) for n, t in hits[:6])
        out.append("- %s (owner: %s; hits: %s%s)" % (
            path, owner or "UNOWNED", shown, " ..." if len(hits) > 6 else ""))
    head_line = "AFFECTED_TESTS: %d test file(s), %d without an owning package" % (len(out), unowned)
    if not out:
        head_line = "AFFECTED_TESTS: none — no existing test spells a literal the contract changes"
    return (1 if unowned else 0), "\n".join([head_line] + out)


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)
            print("SELFTEST FAIL  " + name)

    check("quoted tokens, comments ignored",
          tokens_of('  cols = ["id", "name"]  # x') == {"id", "name"} and tokens_of("// \"abc\"") == set())
    check("test paths", is_test("tests/a.py") and is_test("src/foo_test.go") and not is_test("src/a.py"))
    with tempfile.TemporaryDirectory() as tmp:
        def sh(*a: str) -> None:
            env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
            subprocess.run(["/usr/bin/git", "-C", tmp, "-c", "user.email=t@t", "-c", "user.name=t",
                            "-c", "commit.gpgsign=false", *a], check=True, capture_output=True, env=env)

        def put(rel: str, text: str) -> None:
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
            with open(os.path.join(tmp, rel), "w") as fh:
                fh.write(text)

        sh("init", "-q")
        put("src/settings.py", 'COLUMNS = ["id", "name", "kind"]\nLIMIT = 26\n')
        put("tests/test_settings.py", 'EXPECTED_COLUMNS = ["id", "name", "kind"]\n')
        put("tests/test_other.py", 'KEYS = ["kind"]\nCOUNT = 26\n')
        put("tests/test_mine.py", 'assert cols == ["id", "name", "kind"]\n')
        put("tests/test_quiet.py", 'assert True\n')
        dossier = os.path.join(tmp, "W-1.md")
        put("W-1.md", "---\nbaseline_commit: BASE\n---\n## Work packages\n\n"
                      "| Package | Owned paths | Depends on |\n|---|---|---|\n"
                      "| P1 settings | `src/settings.py`, `tests/test_mine.py` | — |\n")
        sh("add", "-A")
        sh("commit", "-q", "-m", "base")
        base = subprocess.run(["/usr/bin/git", "-C", tmp, "rev-parse", "HEAD"], capture_output=True,
                              text=True).stdout.strip()
        # the contract renames a column and adds one; also a new stub file (ignored)
        put("src/settings.py", 'COLUMNS = ["id", "title", "kind", "tags"]\nLIMIT = 27\n')
        put("src/new_stub.py", 'X = ["id", "title"]\n')
        with open(dossier) as fh:
            body = fh.read().replace("baseline_commit: BASE", "baseline_commit: " + base)
        with open(dossier, "w") as fh:
            fh.write(body)
        code, text = run(tmp, dossier, None, None, ["26"])
        check("unowned affected test is exit 1", code == 1)
        check("the column list test is found", "tests/test_settings.py (owner: UNOWNED" in text)
        check("an owned test names its package", "tests/test_mine.py (owner: P1" in text)
        check("the --literal count hits tests/test_other.py", "tests/test_other.py" in text and "26" in text)
        check("a quiet test is not listed", "test_quiet" not in text)
        code, text = run(tmp, dossier, None, None, [])
        check("no --literal: the count test is not listed", "test_other" not in text)
        # nothing changed against HEAD
        sh("add", "-A")
        sh("commit", "-q", "-m", "contract")
        code, text = run(tmp, dossier, base, "HEAD", [])
        check("commit mode (--head) finds the same list test", "tests/test_settings.py" in text)
        code, text = run(tmp, dossier, "HEAD", "HEAD", [])
        check("empty diff is clean", code == 0 and text.startswith("AFFECTED_TESTS: none"))
    print("selftest: %d failure(s)" % len(failures))
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--root")
    ap.add_argument("--dossier")
    ap.add_argument("--base")
    ap.add_argument("--head")
    ap.add_argument("--literal", action="append", default=[])
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.root or not args.dossier:
        ap.error("--root and --dossier are required")
    try:
        code, text = run(args.root, args.dossier, args.base, args.head, args.literal)
    except Surprise as exc:
        print("affected_literals: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:  # driver_common.Surprise from the package table
        print("affected_literals: %s" % exc, file=sys.stderr)
        return 2
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
