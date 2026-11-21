#!/usr/bin/env python3
"""The dossier front-matter field `pr:`: one canonical format, one parser.

Canonical: `pr: null` before a PR exists, otherwise a YAML flow list of full PR
URLs, the bottom of a stack first:

    pr: [https://github.com/o/r/pull/83]
    pr: [https://github.com/o/r/pull/83, https://github.com/o/r/pull/84]

Readers match a PR by its number, taken from the URL (`pr_numbers`, `pr_field`).
They read every legacy shape too, so an old dossier never breaks: a single URL,
a bare number (`79`), a list (`[79, 80]`), comma-joined URLs, an empty value, `null`.

    dossier_pr.py --normalize ROOT   rewrite legacy `pr:` lines of ROOT/.discovery/dossiers/*.md
                                     to the canonical form, in place (only the `pr:` line)
    dossier_pr.py --selftest

Bare numbers become URLs through ROOT's origin remote (owner/repo on GitHub); a number
that cannot be resolved is left as it is and reported.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
import tempfile

_URL_NUM = re.compile(r"https?://[^\s,\[\]]+?/pull/(\d+)")
_URL = re.compile(r"https?://[^\s,\[\]]+?/pull/\d+")
_BARE = re.compile(r"(?<![\w./])\d+(?![\w.])")
_CANON = re.compile(r"\[\s*(?:https?://[^\s,\[\]]+/pull/\d+)(?:\s*,\s*https?://[^\s,\[\]]+/pull/\d+)*\s*\]")
_PR_LINE = re.compile(r"^pr:[ \t]*(.*)$", re.M)


def _strip_comment(value: str) -> str:
    return re.sub(r"\s+#.*$", "", value).strip()


def pr_numbers(value: str) -> list[int]:
    """The PR numbers a `pr:` value names, in order, from any shape (URL, number, list, comma-joined, empty, null)."""
    v = _strip_comment(value or "").strip("'\"")
    if not v or v in ("null", "~", "None"):
        return []
    if "/pull/" in v:
        return [int(n) for n in _URL_NUM.findall(v)]
    return [int(n) for n in _BARE.findall(v)]


def _pr_raw(front_matter: str) -> str:
    """The `pr:` value text: the line's rest, plus the items of a block list under it."""
    lines = front_matter.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r"^pr:[ \t]*(.*)$", line)
        if not m:
            continue
        parts = [m.group(1)]
        for nxt in lines[i + 1:]:
            if re.match(r"^\s+-\s", nxt):
                parts.append(nxt.split("-", 1)[1].strip())
            else:
                break
        return ", ".join(p for p in parts if p.strip())
    return ""


def pr_field(front_matter: str) -> list[int]:
    """The PR numbers of the `pr:` field in a dossier's front matter text (no fences needed); [] when absent."""
    return pr_numbers(_pr_raw(front_matter))


def is_canonical(value: str) -> bool:
    """True for `null` or a flow list of full PR URLs (a trailing `# comment` is allowed)."""
    v = _strip_comment(value or "")
    return v == "null" or bool(_CANON.fullmatch(v))


def canonical(value: str, base: str | None = None) -> str | None:
    """The canonical text for a `pr:` value, or None when a bare number cannot be turned into a URL (BASE is
    `https://github.com/<owner>/<repo>`). Already-canonical values come back unchanged."""
    v = _strip_comment(value or "").strip("'\"")
    if is_canonical(v):
        return v
    if not v or v in ("null", "~", "None"):
        return "null"
    urls = _URL.findall(v)
    if urls and "/pull/" in v:
        # URLs win, as in pr_numbers; a bare number next to them has no repo of its own to resolve against
        return "[" + ", ".join(urls) + "]"
    nums = pr_numbers(v)
    if not nums:
        return None
    if not base:
        return None
    return "[" + ", ".join(f"{base}/pull/{n}" for n in nums) + "]"


def remote_base(root: str) -> str | None:
    """`https://github.com/<owner>/<repo>` from ROOT's origin remote, or None."""
    try:
        p = subprocess.run(["git", "-C", root, "remote", "get-url", "origin"], capture_output=True, text=True)
    except OSError:
        return None
    url = p.stdout.strip() if p.returncode == 0 else ""
    m = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url)
    return f"https://github.com/{m.group(1)}/{m.group(2)}" if m else None


def normalize_text(text: str, base: str | None) -> tuple[str, str | None, str | None]:
    """Rewrite the `pr:` line (and the items of a block list under it) of one dossier's text.
    Returns (new text, change description or None, problem or None)."""
    if not text.startswith("---\n"):
        return text, None, None
    end = text.find("\n---\n", 4)
    if end == -1:
        return text, None, None
    fm = text[4:end].split("\n")
    for i, line in enumerate(fm):
        m = re.match(r"^pr:[ \t]*(.*)$", line)
        if not m:
            continue
        j = i + 1
        while j < len(fm) and re.match(r"^\s+-\s", fm[j]):
            j += 1
        raw = _pr_raw("\n".join(fm[i:j]))
        comment = re.search(r"\s+#.*$", m.group(1)) if j == i + 1 else None
        if j == i + 1 and is_canonical(m.group(1)):
            return text, None, None
        new = canonical(raw, base)
        if new is None:
            return text, None, f"cannot canonicalise `pr: {raw}` (no origin remote to resolve a bare number)"
        fm[i:j] = ["pr: " + new + (comment.group(0) if comment else "")]
        return "---\n" + "\n".join(fm) + text[end:], f"pr: {raw or '(empty)'} -> {new}", None
    return text, None, None


def normalize_repo(root: str, write: bool = True) -> int:
    """Normalise every dossier of ROOT. Prints one line per change; returns 1 if any value was left unresolved."""
    base = remote_base(root)
    bad = 0
    for f in sorted(glob.glob(os.path.join(root, ".discovery", "dossiers", "*.md"))):
        with open(f, encoding="utf-8") as fh:
            text = fh.read()
        new, change, problem = normalize_text(text, base)
        name = os.path.basename(f)
        if problem:
            print(f"SKIP    {name}: {problem}")
            bad = 1
        elif change:
            print(f"{'FIXED  ' if write else 'WOULD  '} {name}: {change}")
            if write:
                tmp = f + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(new)
                os.replace(tmp, f)
    return bad


def selftest() -> int:
    fails: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            fails.append(f"{name} {detail}")

    u = "https://github.com/o/r/pull/"
    for val, want in [
        (u + "83", [83]), ("79", [79]), ("[79, 80]", [79, 80]), (f"{u}83, {u}84", [83, 84]),
        (f"[{u}83, {u}84]", [83, 84]), ("", []), ("null", []), ("~", []), ("'79'", [79]),
        (f"{u}83  # stack", [83]), ("abc1.2", []),
    ]:
        check(f"pr_numbers({val!r})", pr_numbers(val) == want, str(pr_numbers(val)))
    check("pr_field reads the line", pr_field(f"id: W-1\npr: {u}83\nbranch: x") == [83])
    check("pr_field reads a block list", pr_field(f"id: W-1\npr:\n  - {u}83\n  - {u}84\nbranch: x") == [83, 84])
    check("pr_field absent", pr_field("id: W-1") == [])
    check("pr_field empty", pr_field("pr:\nbranch: x") == [])
    for val, ok in [("null", True), (f"[{u}83]", True), (f"[{u}83, {u}84]", True), (f"[{u}83]  # c", True),
                    (u + "83", False), ("79", False), ("[79]", False), ("", False), (f"{u}83, {u}84", False),
                    ("[]", False)]:
        check(f"is_canonical({val!r})", is_canonical(val) is ok)
    b = "https://github.com/o/r"
    for val, want in [(u + "83", f"[{u}83]"), ("79", f"[{u}79]"), ("[79, 80]", f"[{u}79, {u}80]"),
                      (f"{u}83, {u}84", f"[{u}83, {u}84]"), ("", "null"), ("null", "null"), (f"[{u}83]", f"[{u}83]")]:
        check(f"canonical({val!r})", canonical(val, b) == want, str(canonical(val, b)))
    check("canonical without a base leaves a number", canonical("79", None) is None)
    check("canonical without a base still maps URLs", canonical(u + "83", None) == f"[{u}83]")

    with tempfile.TemporaryDirectory() as td:
        d = os.path.join(td, ".discovery", "dossiers")
        os.makedirs(d)
        subprocess.run(["git", "-C", td, "init", "-q"], check=True, capture_output=True)
        subprocess.run(["git", "-C", td, "remote", "add", "origin", "git@github.com:o/r.git"], check=True, capture_output=True)
        check("remote_base ssh", remote_base(td) == b, str(remote_base(td)))
        body = "\n## Problem\n\npr: 5 stays in the body\n"

        def mk(name: str, pr: str | None) -> str:
            p = os.path.join(d, name)
            line = "" if pr is None else f"pr:{(' ' + pr) if pr else ''}\n"
            with open(p, "w") as fh:
                fh.write(f"---\nid: W-001\nstatus: done\n{line}branch: x\n---\n{body}")
            return p

        paths = {k: mk(f"{k}.md", v) for k, v in [
            ("url", u + "83"), ("num", "79"), ("lst", "[79, 80]"), ("two", f"{u}1, {u}2"), ("empty", ""),
            ("null", "null"), ("canon", f"[{u}9]"), ("none", None), ("cmt", "79  # note")]}
        before = {k: open(p).read() for k, p in paths.items()}
        rc = normalize_repo(td)
        after = {k: open(p).read() for k, p in paths.items()}
        want = {"url": f"[{u}83]", "num": f"[{u}79]", "lst": f"[{u}79, {u}80]", "two": f"[{u}1, {u}2]",
                "empty": "null", "null": "null", "canon": f"[{u}9]", "cmt": f"[{u}79]  # note"}
        check("normalize rc", rc == 0)
        for k, w in want.items():
            check(f"normalize {k}", f"\npr: {w}\n" in after[k], after[k])
        check("normalize leaves a dossier with no pr: alone", after["none"] == before["none"])
        check("normalize edits only the pr line",
              all([x for x in a.split("\n") if not x.startswith("pr:")] == [x for x in before[k].split("\n") if not x.startswith("pr:")]
                  for k, a in after.items()))
        check("normalize keeps the body", all(a.endswith(body) for a in after.values()))
        check("normalize already-canonical file untouched", after["canon"] == before["canon"] and after["null"] == before["null"])
        check("normalize is idempotent", normalize_repo(td) == 0 and all(open(p).read() == after[k] for k, p in paths.items()))
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, ".discovery", "dossiers"))
        subprocess.run(["git", "-C", td, "init", "-q"], check=True, capture_output=True)
        p = os.path.join(td, ".discovery", "dossiers", "a.md")
        with open(p, "w") as fh:
            fh.write("---\nid: W-001\npr: 79\n---\nbody\n")
        check("no origin: a number is reported, not touched", normalize_repo(td) == 1 and "pr: 79\n" in open(p).read())

    for f in fails:
        print("FAIL", f)
    print(f"dossier_pr selftest: {'FAIL' if fails else 'ok'}")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--normalize", metavar="ROOT", help="rewrite legacy `pr:` values under ROOT to the canonical form")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.normalize:
        return normalize_repo(os.path.abspath(args.normalize))
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
