#!/usr/bin/env python3
"""Classify a test-file edit: harness-only, or assertion-touching.

/work-on Phase 6 splits test failures into four rows. Row 4 (harness noise —
imports, module setup, fixtures, collection wiring) is the orchestrator's to
fix by hand; rows 1-3 belong to a re-spawn. The boundary was judgement until
this script made it mechanical:

    exit 0  the edit touches no assertion-bearing line (harness-only, allowed)
    exit 1  the edit touches an assertion-bearing line (rows 1-3, re-spawn)
    exit 2  unusable — unknown language, unparseable diff, unreadable file.
            Never guessed around; fix the input or judge the hunk by hand.

An assertion-bearing line is a line inside an assertion span: from a line
that carries an assert-family token (assert, assert_eq!, Assert.Equal,
expect(...).toBe, ...) up to the line where its open delimiters close — so
the arguments of a multi-line assertion count, not just its first line.

Known limit, on purpose: a block-scoped assertion body (the statements under
`with pytest.raises(...):`) is not tracked — only the header and its own
arguments are. The script flags the token and its argument span; when the
output says a hunk is near one, a human reads it.

Usage:
    check_harness_edit.py --diff tests/x.py tests/x.py.new
    check_harness_edit.py --patch fix.diff --root .
    check_harness_edit.py --selftest

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

# One table: extension -> assert-family tokens (regex fragments). A CHANGED
# line matching one of these, or sitting inside a span one of these opens,
# is assertion-bearing. Bias toward flagging: an implicit assertion
# (.unwrap(), .expect()) counts.
ASSERT_TOKENS: dict[str, str] = {
    ".py": r"\bassert\b|\bpytest\.raises\b|\bself\.assert\w*\(|\bpytest\.fail\b|\bself\.fail\w*\(",
    ".rs": r"\bassert!|\bassert_eq!|\bassert_ne!|\bdebug_assert\w*!|#\[should_panic|\.expect\(|\.unwrap\(\)",
    ".cs": r"\bAssert\.|\bAssertions\.|\bShould\w*\(|\bThrows\b",
    ".ts": r"\bexpect\(|\bassert\(|\.toBe\b|\.toEqual\b|\.toStrictEqual\b|\.toThrow\b|\.rejects\.",
    ".tsx": r"\bexpect\(|\bassert\(|\.toBe\b|\.toEqual\b|\.toStrictEqual\b|\.toThrow\b|\.rejects\.",
    ".mts": r"\bexpect\(|\bassert\(|\.toBe\b|\.toEqual\b|\.toThrow\b",
    ".cts": r"\bexpect\(|\bassert\(|\.toBe\b|\.toEqual\b|\.toThrow\b",
    ".js": r"\bexpect\(|\bassert\(|\.toBe\b|\.toEqual\b|\.toThrow\b",
    ".java": r"\bassert\w*\(|\bAssertions\.|\bfail\(|\bassertThat\b",
    ".kt": r"\bassert\w*\(|\bAssertions\.|\bfail\(|\bassertThat\b",
}

OPEN = "([{"
CLOSE = ")]}"

HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def pattern_for(path: str) -> re.Pattern[str] | None:
    """The compiled token pattern for the file's extension, or None."""
    for ext, frag in ASSERT_TOKENS.items():
        if path.endswith(ext):
            return re.compile(frag)
    return None


def assertion_spans(text: str, pattern: re.Pattern[str]) -> list[tuple[int, int]]:
    """1-based inclusive (start, end) line ranges of every assertion span."""
    lines = text.split("\n")
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if not pattern.search(lines[i]):
            i += 1
            continue
        depth = 0
        opened = False
        j = i
        while j < len(lines):
            for ch in lines[j]:
                if ch in OPEN:
                    depth += 1
                    opened = True
                elif ch in CLOSE:
                    depth -= 1
            # Balanced on the first line with no open delimiters at all
            # (`assert x`), or balanced after opening: the span ends here.
            if (j == i and not opened) or (opened and depth <= 0):
                break
            j += 1
        out.append((i + 1, j + 1))
        i = j + 1
    return out


def changed_lines(old: str, new: str) -> list[tuple[int, str]]:
    """(1-based line number, side) for every - and + line between two texts."""
    out: list[tuple[int, str]] = []
    old_ln = new_ln = 0
    for line in difflib.unified_diff(old.split("\n"), new.split("\n"), lineterm=""):
        if line.startswith(("--- ", "+++")) or line in ("---", "+++"):
            continue  # file headers carry no line numbers
        m = HEADER_RE.match(line)
        if m:
            # The next old-side / new-side line the hunk will emit.
            old_ln = int(m.group(1)) - 1
            new_ln = int(m.group(3)) - 1
            continue
        if line.startswith("-"):
            old_ln += 1
            out.append((old_ln, "-"))
        elif line.startswith("+"):
            new_ln += 1
            out.append((new_ln, "+"))
        else:  # context advances both sides
            old_ln += 1
            new_ln += 1
    return out


def strip_prefix(path: str) -> str:
    return re.sub(r"^[ab]/", "", path)


def parse_patch(patch_text: str) -> dict[str, list[tuple[int, list[str]]]]:
    """Unified diff -> { path -> [(old-side start line 1-based, hunk body)] }.

    Hunk body entries keep their diff prefix: '-x', '+x', ' x'.
    """
    out: dict[str, list[tuple[int, list[str]]]] = {}
    path = ""
    start = 0
    body: list[str] | None = None
    for line in patch_text.split("\n"):
        if line.startswith("--- "):
            continue
        if line.startswith("+++ "):
            path = strip_prefix(line[4:].split("\t")[0].strip())
            out.setdefault(path, [])
            continue
        m = HEADER_RE.match(line)
        if m:
            body = []
            start = int(m.group(1))
            out[path].append((start, body))
            continue
        if body is not None:
            if line.startswith(("-", "+", " ")):
                body.append(line)
            else:  # hunk exhausted
                body = None
    return out


def apply_hunks(text: str, hunks: list[tuple[int, list[str]]]) -> str | None:
    """Apply hunks (same file) to text; None when a hunk does not fit."""
    lines = text.split("\n")
    for start, body in sorted(hunks, key=lambda h: -h[0]):  # last first: no shift
        at = start - 1
        if at < 0 or at > len(lines):
            return None
        chunk = lines[:at]
        for entry in body:
            tag, content = entry[0], entry[1:]
            if tag == "-":
                if at >= len(lines) or lines[at] != content:
                    return None
                at += 1
            elif tag == "+":
                chunk.append(content)
            else:
                if at >= len(lines) or lines[at] != content:
                    return None
                chunk.append(content)
                at += 1
        lines = chunk + lines[at:]
    return "\n".join(lines)


def classify(old: str, new: str, path: str) -> tuple[int, list[str]]:
    """(exit code, findings) for the edit old -> new of one file."""
    pattern = pattern_for(path)
    if pattern is None:
        return 2, [f"unknown language: no assertion tokens for '{path}'"]
    changed = changed_lines(old, new)
    if not changed:
        return 0, ["no changes: the texts are identical"]
    hits: list[str] = []
    old_spans = assertion_spans(old, pattern)
    new_spans = assertion_spans(new, pattern)
    for ln, side in changed:
        against = old_spans if side == "-" else new_spans
        for a, b in against:
            if a <= ln <= b:
                hits.append(f"{path}:{ln} ({side}) inside an assertion span ({a}-{b})")
                break
    if hits:
        return 1, hits
    return 0, [
        f"harness-only: {len(changed)} changed line(s), none in an assertion span"
    ]


def refuse(msg: str) -> int:
    print(f"UNUSABLE  {msg}")
    print("exit 2 — the check did not run; fix the input or judge by hand")
    return 2


def selftest() -> int:
    py_a = (
        "import os\n"
        "import pytest\n"
        "from q import FlushQueue\n"
        "\n"
        "# promise: flush/return-meaning\n"
        "def test_flush_count():\n"
        "    q = FlushQueue()\n"
        "    q.enqueue(3)\n"
        "    assert q.flush(10) == 3\n"
    )
    rs_a = (
        "#[test]\n"
        "fn flush_count() {\n"
        "    let q = FlushQueue::new();\n"
        "    q.enqueue(3);\n"
        "    assert_eq!(q.flush(10), 3,\n"
        '        "each written item counts");\n'
        "}\n"
    )
    cases: list[tuple[str, int, int]] = []

    def check(name: str, want: int, code: int) -> None:
        cases.append((name, want, code))

    check(
        "py harness import addition -> 0",
        0,
        classify(
            py_a,
            py_a.replace("import os\n", "import os\nimport logging\n"),
            "tests/test_flush.py",
        )[0],
    )
    check(
        "py fixture-helper addition -> 0",
        0,
        classify(
            py_a,
            py_a.replace(
                "def test_flush_count():",
                "def make_queue():\n    return FlushQueue()\n\n\ndef test_flush_count():",
            ),
            "tests/test_flush.py",
        )[0],
    )
    check(
        "py assertion constant change -> 1",
        1,
        classify(py_a, py_a.replace("== 3", "== 2"), "tests/test_flush.py")[0],
    )
    check(
        "py citation comment change -> 0",
        0,
        classify(
            py_a,
            py_a.replace(
                "# promise: flush/return-meaning",
                "# promise: flush/return-meaning (v2)",
            ),
            "tests/test_flush.py",
        )[0],
    )
    check("identical texts -> 0", 0, classify(py_a, py_a, "tests/test_flush.py")[0])
    check(
        "py assertion removal -> 1",
        1,
        classify(
            py_a,
            py_a.replace("    assert q.flush(10) == 3\n", ""),
            "tests/test_flush.py",
        )[0],
    )
    check(
        "py multiline assert continuation edit -> 1",
        1,
        classify(
            py_a,
            py_a.replace(
                "    assert q.flush(10) == 3\n",
                "    assert (\n        q.flush(10)\n    ) == 3\n",
            ),
            "tests/test_flush.py",
        )[0],
    )
    check(
        "rs harness line addition -> 0",
        0,
        classify(
            rs_a,
            rs_a.replace(
                "fn flush_count() {", "fn flush_count() {\n    let _guard = 1;"
            ),
            "tests/flush.rs",
        )[0],
    )
    check(
        "rs multiline assert argument edit -> 1",
        1,
        classify(
            rs_a,
            rs_a.replace('"each written item counts"', '"each written item"'),
            "tests/flush.rs",
        )[0],
    )
    check("unknown extension -> 2", 2, classify("x", "y", "tests/test_flush.txt")[0])

    # patch mode: apply + classify end to end
    with __import__("tempfile").TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tests").mkdir()
        (root / "tests" / "test_flush.py").write_text(py_a)
        patch = (
            "--- a/tests/test_flush.py\n"
            "+++ b/tests/test_flush.py\n"
            "@@ -1,4 +1,5 @@\n"
            " import os\n"
            "+import logging\n"
            " import pytest\n"
            " from q import FlushQueue\n"
            " \n"
        )
        (root / "harness.patch").write_text(patch)
        got = run_patch((root / "harness.patch").read_text(), root)
        check("patch mode harness-only addition -> 0", 0, got)
        bad_patch = patch.replace("+import logging\n", "")
        (root / "null.patch").write_text(bad_patch)
        check(
            "patch mode all-context hunk is a no-op -> 0", 0, run_patch(bad_patch, root)
        )
        (root / "missing.patch").write_text(
            "--- a/tests/nope.py\n+++ b/tests/nope.py\n@@ -1,1 +1,1 @@\n-x\n+y\n"
        )
        check(
            "patch mode missing file -> 2",
            2,
            run_patch((root / "missing.patch").read_text(), root),
        )

    ok = all(w == g for _, w, g in cases)
    for name, want, got in cases:
        print(f"{'PASS' if want == got else 'FAIL'}  {name}: want {want}, got {got}")
    print(f"\n{'PASS' if ok else 'FAIL'} — {len(cases)} case(s)")
    return 0 if ok else 1


def run_patch(patch_text: str, root: Path) -> int:
    """Classify every file a unified patch touches, worst exit wins."""
    per_file = parse_patch(patch_text)
    if not per_file:
        return refuse("no hunks found in the patch")
    worst = 0
    for rel, hunks in sorted(per_file.items()):
        target = root / rel
        if not target.exists():
            return refuse(f"'{rel}' does not exist under {root}")
        if pattern_for(rel) is None:
            return refuse(f"unknown language: no assertion tokens for '{rel}'")
        old = target.read_text()
        new = apply_hunks(old, hunks)
        if new is None:
            return refuse(f"the patch does not apply cleanly to '{rel}'")
        code, findings = classify(old, new, rel)
        worst = max(worst, code)
        label = {0: "HARNESS-ONLY", 1: "TOUCHES-ASSERTION"}.get(code, "UNUSABLE")
        print(f"{label}  {rel}")
        for f in findings[:10]:
            print(f"  {f}")
    if worst == 1:
        print("\nexit 1 — an assertion changed; this is rows 1-3, re-spawn")
    return worst


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--diff",
        nargs=2,
        metavar=("ORIG", "MODIFIED"),
        help="classify the edit between two files",
    )
    ap.add_argument(
        "--patch", metavar="FILE", help="classify a unified diff file ('-' reads stdin)"
    )
    ap.add_argument(
        "--root",
        default=".",
        help="root the patch's paths resolve against (default: .)",
    )
    ap.add_argument(
        "--selftest", action="store_true", help="run the built-in negative controls"
    )
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.diff:
        try:
            old, new = Path(args.diff[0]).read_text(), Path(args.diff[1]).read_text()
        except OSError as exc:
            return refuse(f"cannot read the input: {exc}")
        code, findings = classify(old, new, Path(args.diff[0]).name)
        for f in findings[:20]:
            print(f)
        return code
    if args.patch:
        text = sys.stdin.read() if args.patch == "-" else Path(args.patch).read_text()
        return run_patch(text, Path(args.root))
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
