#!/usr/bin/env python3
"""Support-code digest for the integration test author: signatures and doc comments only.

The integration author traces the wiring and reads the test support around it (harness,
fixtures, conftest, builders, fakes). The recorded runs show 41% of its read bytes were files
a sibling spawn had already read. This script writes ONE digest of that support code, built
mechanically (no LLM), that every author of the wave carries as `SUPPORT_DIGEST:` and reads
first; it opens a file only for a detail the digest lacks.

  support_digest.py build --root X [--paths a,b] [--exclude p1,p2] [--out FILE]
      writes X/.agent-staging/support-digest.md (or FILE) and prints its path; prints
      nothing and writes nothing when no file qualifies. Refuses when
      X/.agent-staging/sealed-paths.txt is missing: that file is the read policy.
  support_digest.py check --root X [FILE]
      exit 1 when FILE (default: the staged digest) is stale against the tree, or names a
      path the read policy seals
  support_digest.py --selftest

Blindness, by structure rather than by promise:
  * The read policy is shared/blind-read.ts `sealedRead`, whose list is
    `.agent-staging/sealed-paths.txt` (compose_payloads.py `write_sealed_paths`: every work
    package's implementation paths and the contract files). A file in it is never a candidate,
    never an explicit `--paths` entry (it is refused), and `check` fails a digest that names
    one. There is no second list: no sealed-paths.txt, no digest (it fails closed, where the
    hook fails open).
  * A file is rendered from the nav parsers (code_nav.py: Python through `ast`, a Rust and a
    C# tokenizer), item by item, from parts that cannot hold a body: the doc comments, the
    attributes or decorators, and the signature up to the body's opening brace or colon. A
    body is replaced by `…`; an initializer (`= expr`) of a field or constant by `= …`. No
    path copies a source range that contains a body. The selftest asserts it on fixtures
    whose bodies, initializers and expression bodies carry distinctive text.
  * A file the parsers cannot read (JSON, YAML, SQL, a syntax error, another language) is
    listed by name with no content.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
import re
import sys
import tempfile
import textwrap

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
STAGING = ".agent-staging"
SEALED_NAME = "sealed-paths.txt"
DIGEST_NAME = "support-digest.md"
MARKER = "# support_digest.py: signatures and doc comments only; bodies replaced by …"
EXTS = (".py", ".rs", ".cs")
SKIP_DIRS = {".git", ".agent-staging", ".discovery", "node_modules", "target", "bin", "obj",
             "__pycache__", ".venv", "venv", "dist", "build", ".tox", ".mypy_cache"}
SUPPORT_DIRS = {"tests", "test", "__tests__", "spec", "test-support", "testing", "testsupport",
                "test_support", "fixtures", "harness", "support", "helpers", "testutils",
                "test_utils", "testkit"}
SUPPORT_DIR_SUFFIX = re.compile(r"\.(Tests?|IntegrationTests?|TestSupport|Testing|TestKit)$")
SUPPORT_NAME = re.compile(r"(conftest|fixture|harness|helper|support|testkit|testhost|fake|mock|"
                          r"stub|builder|factory|common|utils?)", re.I)
TEST_FILE = re.compile(r"(^test_.*\.py$|_test\.[A-Za-z]+$|\.(test|spec)\.[A-Za-z]+$|Tests?\.cs$)")
MAX_FILES = 60
MAX_TOTAL = 48000
MAX_ITEM = 1500
FUNCTIONS = {"fn", "macro_rules", "def", "async def", "method", "ctor", "dtor", "operator",
             "indexer", "property", "event"}
CONTAINERS = {"class", "impl", "trait", "mod", "namespace", "struct", "interface", "record"}
WHOLE = {"enum", "type", "union", "delegate"}  # declarations that hold no executable body


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cn = _load("code_nav")


class Refused(Exception):
    """The read policy is missing, or a request names a sealed path. Exit 1."""


# ---------------------------------------------------------------- read policy

def sealed_set(root: str) -> set[str]:
    """The absolute paths the integration author's hook refuses (shared/blind-read.ts
    `sealedSet`): each non-comment line of X/.agent-staging/sealed-paths.txt, resolved against X.
    Real paths are added too, so a symlink cannot smuggle a sealed file in."""
    path = os.path.join(root, STAGING, SEALED_NAME)
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        raise Refused("%s is missing (%s): it is the read policy, run compose_payloads.py for the "
                      "integration-test-author first" % (path, exc.strerror))
    out: set[str] = set()
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            p = os.path.normpath(os.path.join(root, line))
            out.add(p)
            out.add(os.path.realpath(p))
    return out


def is_sealed(abs_path: str, sealed: set[str]) -> bool:
    return os.path.normpath(abs_path) in sealed or os.path.realpath(abs_path) in sealed


# ---------------------------------------------------------------- candidates

def is_support(rel: str) -> bool:
    """A repo-relative path that looks like test support: harness, fixtures, conftest, builders,
    fakes. A plain test file is not support: its flow is the author's to write, not to reuse."""
    if not rel.endswith(EXTS):
        return False
    parts = rel.replace("\\", "/").split("/")
    name = parts[-1]
    in_dir = any(p.lower() in SUPPORT_DIRS or SUPPORT_DIR_SUFFIX.search(p) for p in parts[:-1])
    named = bool(SUPPORT_NAME.search("/".join(parts[-2:])))
    if TEST_FILE.search(name) and not SUPPORT_NAME.search(name):
        return False
    return (in_dir and named) or bool(re.search(r"(conftest|harness|testkit|testhost|testsupport|"
                                                r"test_support)", name, re.I))


def covered(rel: str, own: list[str]) -> bool:
    return any(rel == o.rstrip("/") or (o.endswith("/") and rel.startswith(o)) for o in own)


def discover(root: str, sealed: set[str], exclude: list[str]) -> tuple[list[str], list[str]]:
    """(candidate repo-relative paths, sealed ones skipped) in sorted order."""
    found: list[str] = []
    skipped: list[str] = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            rel = os.path.relpath(os.path.join(base, f), root)
            if not is_support(rel) or covered(rel, exclude):
                continue
            if is_sealed(os.path.join(root, rel), sealed):
                skipped.append(rel)
            else:
                found.append(rel)
    return sorted(found), sorted(skipped)


# ---------------------------------------------------------------- rendering

def _cut_initializer(sig: str) -> str:
    """A leaf declaration up to its first top-level `=` (not `==`, `=>`, `<=`, `>=`, `!=`),
    then ` = …`. A field or constant initializer may hold a lambda body."""
    depth = 0
    i = 0
    quote = ""
    while i < len(sig):
        c = sig[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "=" and depth <= 0:
            prev = sig[i - 1] if i else ""
            nxt = sig[i + 1] if i + 1 < len(sig) else ""
            if nxt not in "=>" and prev not in "=!<>":
                return sig[:i].rstrip() + " = …"
        i += 1
    return sig


def _py_cut(text: str, tree: ast.AST) -> dict[int, int]:
    """For a def whose body starts on the header's own line (`def f(): return x`): the character
    offset in `text` where that body begins, keyed by the def's first line."""
    starts = [0]
    for m in re.finditer("\n", text):
        starts.append(m.end())
    out: dict[int, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            b0 = node.body[0]
            if b0.lineno == node.lineno:
                line = text[starts[b0.lineno - 1]:].split("\n", 1)[0]
                col = len(line.encode("utf-8")[:b0.col_offset].decode("utf-8", "ignore"))
                out[node.lineno] = starts[b0.lineno - 1] + col
    return out


def _clip(s: str) -> str:
    return s if len(s) <= MAX_ITEM else s[:MAX_ITEM].rstrip() + " …"


def render_item(it, S, cut: dict[int, int], depth: int) -> list[str]:
    """One item, from doc comments, attributes and signature only. Never `text[head:end]`."""
    text = S.text
    pad = "  " * depth
    out: list[str] = []

    def put(block: str, extra: str = "") -> None:
        block = textwrap.dedent(block.strip("\n")).rstrip()
        if block:
            out.append("\n".join(pad + extra + ln if ln.strip() else ln for ln in _clip(block).split("\n")))

    py = it.lang == "python"
    sig_to = it.sig_end
    same_line_body = py and it.start_line and S.line(it.sig_start) in cut
    if same_line_body:
        sig_to = min(sig_to, cut[S.line(it.sig_start)])
    if it.open is not None:
        sig_to = min(sig_to, it.open)
    sig_to = max(sig_to, it.sig_start)
    sig = text[it.sig_start:sig_to].rstrip()
    whole = it.kind in WHOLE or (it.lang == "rust" and it.kind == "struct")
    leaf = it.kind not in FUNCTIONS and it.kind not in CONTAINERS and not whole
    if leaf or (it.kind in FUNCTIONS and it.kind in ("property", "event", "indexer") and it.open is None):
        sig = _cut_initializer(sig)
    if "=>" in sig and it.kind in FUNCTIONS and not py:
        sig = sig.split("=>", 1)[0].rstrip()

    if not py:
        for s, e in it.docs:
            put(text[S.line_start(s):e])
        if it.attr < it.sig_start:
            put(text[S.line_start(it.attr):it.sig_start])
    else:
        if it.attr < it.sig_start:
            put(text[S.line_start(it.attr):it.sig_start])
    if whole and it.open is None:
        sig = text[it.sig_start:it.sig_end].rstrip()
    if whole and it.open is not None:
        # a data declaration (struct, enum, delegate): names and types, never code. Collapsed
        # when it nests deeper than a variant or carries an arrow.
        sig = text[it.sig_start:it.end].rstrip()
        depth = peak = 0
        for ch in sig:
            depth += (ch == "{") - (ch == "}")
            peak = max(peak, depth)
        if "=>" in sig or peak > 2:
            sig = text[it.sig_start:it.open].rstrip() + " { … }"
    put(sig)
    if py and it.docs and not same_line_body:
        put(text[S.line_start(it.docs[0][0]):it.docs[0][1]], "    ")
    if whole:
        pass
    elif it.kind in CONTAINERS and it.children:
        for ch in it.children:
            out.extend(render_item(ch, S, cut, depth + 1))
    elif it.kind in FUNCTIONS or it.kind in CONTAINERS:
        has_body = it.open is not None or (py and it.sig_end < it.end) or same_line_body \
            or text[it.sig_end:it.end].strip() not in ("", ";")
        if has_body:
            out.append(pad + "  …" if py else pad + "  { … }")
    return out


def render_file(root: str, rel: str) -> str:
    """The digest section of one file: `## rel (lang, N lines)` and its items, or a name-only
    line for a file the parsers cannot read."""
    path = os.path.join(root, rel)
    try:
        lang, S, items = cn.load(path)
    except (cn.Unusable, OSError) as exc:
        return "## %s\n(not digestible: %s; open it only for a detail you need)\n" % (rel, str(exc)[:80])
    cut: dict[int, int] = {}
    if lang == "python":
        try:
            cut = _py_cut(S.text, ast.parse(S.text))
        except SyntaxError:
            cut = {}
    lines = ["## %s (%s, %d lines)" % (rel, lang, S.text.count("\n") + 1)]
    if not items:
        lines.append("(no items)")
    for it in items:
        lines.extend(render_item(it, S, cut, 0))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- build / check

def build_text(root: str, paths: list[str] | None = None, exclude: list[str] | None = None) -> tuple[str, list[str]]:
    """(digest text, repo-relative files covered). Empty text when nothing qualifies."""
    sealed = sealed_set(root)
    exclude = [e for e in (exclude or []) if e]
    cands, skipped = discover(root, sealed, exclude)
    for p in paths or []:
        p = os.path.relpath(os.path.join(root, p), root) if not os.path.isabs(p) else os.path.relpath(p, root)
        if is_sealed(os.path.join(root, p), sealed):
            raise Refused("%s is sealed (it is in %s): the integration author may not read it, so "
                          "its digest may not either" % (p, SEALED_NAME))
        if not os.path.isfile(os.path.join(root, p)):
            raise Refused("%s is not a file under %s" % (p, root))
        if p not in cands:
            cands.append(p)
    cands = sorted(set(cands))
    if not cands:
        return "", []
    sections: list[str] = []
    named_only: list[str] = []
    total = 0
    used: list[str] = []
    for rel in cands:
        if len(used) >= MAX_FILES:
            named_only.append(rel)
            continue
        sec = render_file(root, rel)
        if total + len(sec) > MAX_TOTAL:
            named_only.append(rel)
            continue
        total += len(sec)
        used.append(rel)
        sections.append(sec)
    head = [MARKER,
            "# policy: %s (%d sealed path(s) excluded); a file below is one the author may read" % (
                SEALED_NAME, len([s for s in sealed if s.startswith(root)]) // 2 or len(sealed) // 2)]
    body = "\n".join(sections)
    if named_only:
        body += "\n## not digested (over the cap; open one only for a detail you need)\n" + \
                "".join("- %s\n" % r for r in named_only)
    text = "\n".join(head) + "\n\n" + body
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return text.replace("# policy:", "# digest: %s\n# policy:" % digest, 1), used


def default_out(root: str) -> str:
    return os.path.join(root, STAGING, DIGEST_NAME)


def write_digest(root: str, out: str | None = None, paths: list[str] | None = None,
                 exclude: list[str] | None = None) -> str | None:
    """Write the digest (atomically, and only when its bytes changed: concurrent composes of one
    wave write the same text). Returns its path, or None when no file qualifies (a stale digest of
    an earlier run is then removed)."""
    out = out or default_out(root)
    text, _ = build_text(root, paths, exclude)
    if not text:
        if os.path.exists(out):
            os.remove(out)
        return None
    try:
        with open(out, encoding="utf-8") as fh:
            if fh.read() == text:
                return out
    except OSError:
        pass
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = "%s.%d.tmp" % (out, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, out)
    return out


SECTION = re.compile(r"^## (?P<path>[^\n(]+?)(?: \(\w+, \d+ lines\))?$", re.M)


def digest_paths(text: str) -> list[str]:
    return [m.group("path").strip() for m in SECTION.finditer(text)
            if not m.group("path").startswith("not digested")]


def sealed_in_digest(text: str, root: str) -> list[str]:
    """Paths a digest names that the read policy next to `root` seals (lint and `check`)."""
    sealed = sealed_set(root)
    return [p for p in digest_paths(text) if is_sealed(os.path.join(root, p), sealed)]


def check(root: str, file: str | None = None) -> list[str]:
    file = file or default_out(root)
    try:
        with open(file, encoding="utf-8") as fh:
            have = fh.read()
    except OSError as exc:
        return ["%s: cannot read (%s)" % (file, exc.strerror)]
    problems = ["%s names a sealed path: %s" % (file, p) for p in sealed_in_digest(have, root)]
    if MARKER not in have:
        problems.append("%s lacks the generated marker" % file)
    return problems


# ---------------------------------------------------------------- selftest

PY_FIXTURE = '''"""Module doc."""
import pytest

FIXTURE_CONST = "PYCONSTLEAK-1"


@pytest.fixture
def app_client(db, clock=None):
    """Stand up the real app."""
    token = "PYBODYLEAK-2"
    return make(token)


def one_liner(x): return "PYONELINERLEAK-3"


async def seed_publication(client, items=3):
    """Seed N items."""
    await client.post("PYASYNCLEAK-4")


class Harness:
    """The harness class."""
    marker = "PYCLASSVARLEAK-5"

    def run(self, flow: str) -> int:
        """Run a flow."""
        return len("PYMETHODLEAK-6")

    def short(self): return "PYSHORTLEAK-7"
'''
RS_FIXTURE = '''//! crate doc
/// Repo root for tests.
pub const ROOT: &str = "RSCONSTLEAK-1";
pub static HOOK: fn() -> u8 = || { 42 /* RSSTATICLEAK-2 */ };

/// A grant.
#[derive(Debug)]
pub struct Grant { pub id: u32 }

/// Stand up the stack.
pub fn repo_root(prefix: &str) -> String where {
    let secret = "RSBODYLEAK-3";
    format!("{}{}", prefix, secret)
}

pub trait Seed {
    /// Seed it.
    fn seed(&self, n: u32) -> u32;
    fn default_seed(&self) -> u32 { 7 /* RSDEFAULTLEAK-4 */ }
}

impl Seed for Grant {
    fn seed(&self, n: u32) -> u32 { n + 1 /* RSIMPLLEAK-5 */ }
}
'''
CS_FIXTURE = '''namespace Acme.Tests.Support
{
    /// <summary>Builds a host.</summary>
    public class TestHost
    {
        public static readonly Func<int> Make = () => { return 1; /* CSFIELDLEAK-1 */ };
        public int Port { get; set; } = Compute("CSPROPINITLEAK-2");
        /// <summary>The clock.</summary>
        public string Name => "CSEXPRLEAK-3";
        public string Other { get { return "CSACCESSORLEAK-4"; } }
        /// <summary>Start it.</summary>
        public void Start(int port = 80)
        {
            var s = "CSBODYLEAK-5";
        }
    }
}
'''
LEAKS = re.compile(r"[A-Z]+LEAK-\d")


def selftest() -> int:
    failures: list[str] = []

    def check_(name: str, ok: bool, detail: str = "") -> None:
        print("%s  %s" % ("ok  " if ok else "FAIL", name))
        if not ok:
            failures.append(name + (": " + detail if detail else ""))

    with tempfile.TemporaryDirectory() as root:
        def w(rel: str, text: str) -> None:
            p = os.path.join(root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)

        w("tests/support/harness.py", PY_FIXTURE)
        w("tests/common/mod.rs", RS_FIXTURE)
        w("Acme.Tests/Support/TestHost.cs", CS_FIXTURE)
        w("tests/integration/test_flow.py", "def test_a():\n    assert 'TESTFILELEAK-9'\n")
        w("tests/fixtures/seed.json", '{"a": "JSONLEAK-1"}')
        w("tests/fixtures/broken_fixture.py", "def (:\n    'BROKENLEAK-2'\n")
        w("tests/support/sealed_helper.py", "def sealed(): return 'SEALEDLEAK-1'\n")
        w("tests/support/mine_support.py", "def mine(): return 'OWNLEAK-1'\n")
        w("src/impl.py", "def impl(): return 'IMPLLEAK-1'\n")

        try:
            build_text(root)
            check_("no sealed-paths.txt: the build fails closed", False)
        except Refused:
            check_("no sealed-paths.txt: the build fails closed", True)

        w(".agent-staging/sealed-paths.txt",
          "# sealed\nsrc/impl.py\ntests/support/sealed_helper.py\n")
        text, used = build_text(root, exclude=["tests/support/mine_support.py"])
        leaks = sorted(set(LEAKS.findall(text)))
        check_("no body, initializer or expression-body text leaks (py, rs, cs)",
               not leaks and "LEAK-" not in text, repr(re.findall(r"\w+LEAK-\d", text)))
        check_("digest covers the support files", used == [
            "Acme.Tests/Support/TestHost.cs", "tests/common/mod.rs",
            "tests/fixtures/broken_fixture.py", "tests/support/harness.py"], repr(used))
        check_("a sealed support file is not covered", "sealed_helper" not in text and "SEALEDLEAK" not in text)
        check_("an own TEST_PATHS file is excluded", "mine_support" not in text)
        check_("a plain test file and a json fixture carry no content", "TESTFILELEAK" not in text and "JSONLEAK" not in text)
        check_("an unparsable file is named, not dumped", "broken_fixture.py" in text and "BROKENLEAK" not in text
               and "not digestible" in text)
        check_("python signatures and docstrings survive",
               "def app_client(db, clock=None):" in text and '"""Stand up the real app."""' in text
               and "async def seed_publication(client, items=3):" in text
               and "def run(self, flow: str) -> int:" in text and "@pytest.fixture" in text
               and "def one_liner(x):" in text and "def short(self):" in text)
        check_("rust signatures and docs survive",
               "pub fn repo_root(prefix: &str) -> String" in text and "/// Stand up the stack." in text
               and "fn seed(&self, n: u32) -> u32;" in text and "pub struct Grant { pub id: u32 }" in text
               and "pub const ROOT: &str = …" in text)
        check_("c# signatures and docs survive",
               "public void Start(int port = 80)" in text and "/// <summary>Start it.</summary>" in text
               and "public string Name" in text and "public int Port" in text)
        check_("bodies are replaced by a marker", "{ … }" in text and "  …" in text)
        check_("the digest header names the policy", MARKER in text and SEALED_NAME in text)

        try:
            build_text(root, paths=["src/impl.py"])
            check_("an explicit sealed path is refused", False)
        except Refused:
            check_("an explicit sealed path is refused", True)
        out = write_digest(root, exclude=["tests/support/mine_support.py"])
        check_("write_digest writes under .agent-staging", out == default_out(root) and os.path.isfile(out))
        mtime = os.stat(out).st_mtime_ns
        write_digest(root, exclude=["tests/support/mine_support.py"])
        check_("an unchanged digest is not rewritten", os.stat(out).st_mtime_ns == mtime)
        check_("check accepts a fresh digest", check(root, out) == [], repr(check(root, out)))
        with open(out, "a", encoding="utf-8") as fh:
            fh.write("\n## src/impl.py (python, 1 lines)\n")
        check_("check refuses a digest that names a sealed path",
               any("sealed path: src/impl.py" in p for p in check(root, out)), repr(check(root, out)))
        w(".agent-staging/sealed-paths.txt", "# sealed\n")
        for rel in ("tests/support", "tests/common", "Acme.Tests", "tests/fixtures"):
            import shutil
            shutil.rmtree(os.path.join(root, rel))
        check_("no qualifying file: no digest, the stale one is removed",
               write_digest(root) is None and not os.path.exists(out))
    # _cut_initializer corner cases
    check_("an initializer is cut at the first top-level =",
           _cut_initializer("static int X = f(a == b)") == "static int X = …"
           and _cut_initializer("Func<int,int> F = x => x") == "Func<int,int> F = …"
           and _cut_initializer("[A(B = 1)] int x") == "[A(B = 1)] int x")
    if failures:
        print("\n%d failure(s):" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("\nsupport_digest selftest passed")
    return 0


def main(argv: list[str]) -> int:
    import argparse
    if argv and argv[0] == "--selftest":
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--root", required=True)
    b.add_argument("--paths", default="")
    b.add_argument("--exclude", default="")
    b.add_argument("--out")
    c = sub.add_parser("check")
    c.add_argument("--root", required=True)
    c.add_argument("file", nargs="?")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    try:
        if a.cmd == "build":
            split = lambda s: [p.strip() for p in s.split(",") if p.strip()]
            path = write_digest(root, a.out, split(a.paths), split(a.exclude))
            if path:
                print(path)
            return 0
        problems = check(root, a.file)
        for p in problems:
            print("support_digest: " + p, file=sys.stderr)
        return 1 if problems else 0
    except Refused as exc:
        print("support_digest: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
