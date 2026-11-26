#!/usr/bin/env python3
"""Deterministic helper of the /pattern-sweep skill (skills/pattern-sweep/SKILL.md).

  scan   --root R [--repo-name N]   fast, read-only, regex-based counts of the built-in bad-habit patterns for the
                                    languages found under R (Rust, C#, Terraform, Python, TypeScript); prints JSON.
                                    Per pattern: occurrences, files, files touched in the last 90 days, up to three
                                    path:line anchors and a score (files x harm). Also files over the length limit and
                                    code blocks copied into three or more files. Evidence only: the skill judges it.
  record --root R --items N [--summary S] [--repo-name N]
                                    writes <agentic dir>/pattern-sweep/<repo>.json as
                                    {"last_run": iso, "items": N, "summary": "..."}; the dashboard reads that file.
  --selftest                        temp dirs only; never touches the real ~/.agentic.

The agentic dir is $ASD_AGENTIC_DIR, else ~/.agentic (the directory the other scripts keep their state in).
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

RECENT_DAYS = 90
LONG_FILE_LINES = 800
DUP_WINDOW = 6          # consecutive normalised lines that make a copied block
DUP_MIN_CHARS = 200
DUP_MIN_FILES = 3
MAX_FILES = 6000
MAX_BYTES = 1_000_000

LANG_EXT = {".rs": "rust", ".cs": "csharp", ".tf": "terraform", ".py": "python", ".ts": "typescript", ".tsx": "typescript"}
SKIP_DIRS = {".git", "node_modules", "target", "bin", "obj", ".terraform", "vendor", "dist", "build", "__pycache__", ".venv", ".discovery", ".agent-staging", "worktrees"}
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|e2e)(/|$)|_tests?\.|\.tests?\.|Tests?\.cs$|(^|/)tests?\.rs$|(^|/)test_")

# (id, title, langs, regex, harm 1-5, where: prod|test|any)
PATTERNS = [
    ("unwrap-prod", "unwrap/expect in production paths", {"rust"}, r"\.unwrap\(\)|\.expect\(", 4, "prod"),
    ("swallowed-error-rs", "errors dropped with let _ = / .ok();", {"rust"}, r"\blet\s+_\s*=|\.ok\(\);", 4, "prod"),
    ("swallowed-catch-cs", "catch blocks that swallow (catch { } / catch (Exception))", {"csharp"}, r"\bcatch\s*(\(\s*Exception\b[^)]*\))?\s*\{", 4, "prod"),
    ("swallowed-except-py", "bare or blanket except", {"python"}, r"^\s*except\s*(Exception)?\s*:|^\s*except\s+Exception\s+as\s+\w+\s*:\s*(pass)?\s*$", 3, "prod"),
    ("swallowed-catch-ts", "empty or ignored catch", {"typescript"}, r"catch\s*(\([^)]*\))?\s*\{\s*(/\*.*\*/)?\s*\}", 3, "prod"),
    ("sleep-in-test", "sleeps in tests", {"rust", "csharp", "python", "typescript"}, r"thread::sleep|tokio::time::sleep|Thread\.Sleep|Task\.Delay|time\.sleep\(|setTimeout\(|Bun\.sleep|await sleep\(", 3, "test"),
    ("magic-number", "magic numbers (3+ digit literals outside const/comments)", {"rust", "csharp", "python", "typescript"}, r"[(\s,=<>+*/-]\d{3,}(?![\d.\w])", 2, "prod"),
    ("todo-cluster", "TODO/FIXME/HACK markers", {"rust", "csharp", "terraform", "python", "typescript"}, r"\b(TODO|FIXME|HACK|XXX)\b", 2, "any"),
    ("env-parsing", "environment variables read ad hoc (duplicated config parsing)", {"rust", "csharp", "python", "typescript"}, r"env::var\(|Environment\.GetEnvironmentVariable|os\.environ|os\.getenv|process\.env\.", 3, "prod"),
    ("ad-hoc-deser", "config/JSON parsed in many places", {"rust", "csharp", "python", "typescript"}, r"serde_json::from_(str|slice|reader)|toml::from_str|JsonSerializer\.Deserialize|JsonConvert\.DeserializeObject|json\.loads\(|JSON\.parse\(", 2, "prod"),
    ("err-type-boxed", "error type family: Box<dyn Error>", {"rust"}, r"Box<dyn\s+(std::error::)?Error", 3, "prod"),
    ("err-type-anyhow", "error type family: anyhow", {"rust"}, r"\banyhow::|anyhow!\(|\bAnyhow\b", 3, "prod"),
    ("err-type-thiserror", "error type family: thiserror", {"rust"}, r"thiserror::Error|#\[derive\([^)]*Error", 3, "prod"),
    ("err-type-string", "error type family: Result<_, String>", {"rust"}, r"Result<[^;{]*,\s*String>", 3, "prod"),
    ("dead-flag", "feature flags / toggles (check which are never flipped)", {"rust", "csharp", "python", "typescript"}, r"cfg\(feature|FeatureFlag|feature_flag|FEATURE_[A-Z_]+|ENABLE_[A-Z_]+|IsFeatureEnabled|\bflags?\.[a-z_]+enabled", 2, "prod"),
    ("tf-open-cidr", "terraform: 0.0.0.0/0 open ranges", {"terraform"}, r"0\.0\.0\.0/0", 5, "any"),
    ("tf-hardcoded-id", "terraform: hard-coded account/AMI ids", {"terraform"}, r"\b\d{12}\b|\bami-[0-9a-f]{8,}", 4, "any"),
    ("tf-count-literal", "terraform: literal count/for_each toggles", {"terraform"}, r"^\s*count\s*=\s*\d+", 2, "any"),
    ("tf-no-version", "terraform: unpinned module/provider versions", {"terraform"}, r"version\s*=\s*\"(>=?\s*[\d.]+|\*)\"", 3, "any"),
    ("tf-lifecycle-ignore", "terraform: lifecycle ignore_changes", {"terraform"}, r"ignore_changes", 3, "any"),
    ("cs-async-void", "C#: async void / .Result / .Wait() blocking", {"csharp"}, r"\basync\s+void\b|\.Result\b(?!\w)|\.GetAwaiter\(\)\.GetResult\(\)|\.Wait\(\)", 4, "prod"),
    ("rust-clone-heavy", "Rust: .clone() on every borrow", {"rust"}, r"\.clone\(\)", 1, "prod"),
]
CONST_LINE = re.compile(r"^\s*(pub\s+)?(const|static|readonly|#define)\b|^\s*(//|#|/\*|\*)|\bconst\s|\bstatic\s|\[\w+\(\d+\)\]")


def agentic_dir():
    return os.environ.get("ASD_AGENTIC_DIR") or os.path.expanduser("~/.agentic")


def git(root, *args):
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=60)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def repo_name(root, override=None):
    if override:
        return override
    top = git(root, "rev-parse", "--show-toplevel")
    return os.path.basename((top or os.path.abspath(root)).strip())


def list_files(root):
    out = git(root, "ls-files")
    rels = out.splitlines() if out else []
    if not rels:
        for d, ds, fs in os.walk(root):
            ds[:] = [x for x in ds if x not in SKIP_DIRS]
            rels += [os.path.relpath(os.path.join(d, f), root) for f in fs]
    keep = []
    for r in rels:
        if set(r.split("/")[:-1]) & SKIP_DIRS:
            continue
        if os.path.splitext(r)[1] in LANG_EXT:
            keep.append(r)
    return sorted(keep)[:MAX_FILES]


def recent_files(root):
    out = git(root, "log", f"--since={RECENT_DAYS}.days", "--name-only", "--pretty=format:")
    return set(out.split("\n")) if out else set()


def scan(root):
    root = os.path.abspath(root)
    files = list_files(root)
    recent = recent_files(root)
    langs = {}
    compiled = [(p, re.compile(p[3])) for p in PATTERNS]
    hits = {p[0]: {"count": 0, "files": {}, "anchors": []} for p, _ in compiled}
    long_files, windows = [], {}
    for rel in files:
        lang = LANG_EXT[os.path.splitext(rel)[1]]
        langs[lang] = langs.get(lang, 0) + 1
        try:
            if os.path.getsize(os.path.join(root, rel)) > MAX_BYTES:
                continue
            lines = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read().split("\n")
        except OSError:
            continue
        is_test_file = bool(TEST_PATH.search(rel))
        if len(lines) > LONG_FILE_LINES and not is_test_file:
            long_files.append({"path": rel, "lines": len(lines), "recent": rel in recent})
        in_cfg_test = False   # Rust: everything after #[cfg(test)] in a file is test code
        for i, line in enumerate(lines, 1):
            if lang == "rust" and not in_cfg_test and "#[cfg(test)]" in line:
                in_cfg_test = True
            is_test = is_test_file or in_cfg_test
            for (pid, _t, plangs, _rx, _h, where), rx in compiled:
                if lang not in plangs or (where == "prod" and is_test) or (where == "test" and not is_test):
                    continue
                if pid == "magic-number" and CONST_LINE.search(line):
                    continue
                if rx.search(line):
                    h = hits[pid]
                    h["count"] += 1
                    h["files"][rel] = h["files"].get(rel, 0) + 1
                    if len(h["anchors"]) < 3 and rel not in {a.split(":")[0] for a in h["anchors"]}:
                        h["anchors"].append(f"{rel}:{i}")
        if not is_test_file and lang != "terraform":
            norm = [(n, s) for n, s in ((i, re.sub(r"\s+", " ", l).strip()) for i, l in enumerate(lines, 1))
                    if s and not re.match(r"^(use |import |using |from |#|//|\}|\{|\)|\]|pub use|mod )", s)]
            for k in range(0, len(norm) - DUP_WINDOW + 1):
                chunk = norm[k:k + DUP_WINDOW]
                text = "\n".join(s for _, s in chunk)
                if len(text) < DUP_MIN_CHARS:
                    continue
                windows.setdefault(hashlib.sha1(text.encode()).hexdigest(), {})[rel] = chunk[0][0]
    out = []
    for (pid, title, plangs, _rx, harm, where), _ in compiled:
        h = hits[pid]
        if not h["count"]:
            continue
        nfiles = len(h["files"])
        if pid == "todo-cluster":
            nfiles = sum(1 for c in h["files"].values() if c >= 3)   # a cluster is 3+ markers in one file
        out.append({"id": pid, "title": title, "harm": harm, "count": h["count"], "files": len(h["files"]),
                    "recent_files": sum(1 for f in h["files"] if f in recent),
                    "top_files": sorted(h["files"], key=lambda f: -h["files"][f])[:3],
                    "anchors": h["anchors"], "score": nfiles * harm})
    out.sort(key=lambda x: -x["score"])
    dups = sorted(({"files": len(v), "anchors": [f"{p}:{n}" for p, n in sorted(v.items())[:3]]}
                   for v in windows.values() if len(v) >= DUP_MIN_FILES), key=lambda d: -d["files"])[:8]
    fam = {p["id"]: p["files"] for p in out if p["id"].startswith("err-type-")}
    return {"root": root, "repo": repo_name(root), "languages": langs, "files_scanned": len(files), "recent_days": RECENT_DAYS,
            "patterns": out, "long_files": sorted(long_files, key=lambda x: -x["lines"])[:10], "copied_blocks": dups,
            "error_type_drift": len(fam) >= 2, "error_type_families": fam}


def record_path(root, name=None):
    return os.path.join(agentic_dir(), "pattern-sweep", repo_name(root, name) + ".json")


def record(root, items, summary="", name=None):
    path = record_path(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = {"last_run": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "items": int(items), "summary": summary}
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(body, f, indent=1)
    os.replace(tmp, path)
    return path, body


def selftest():
    fails = []

    def check(name, ok):
        print(("PASS " if ok else "FAIL ") + "pattern_sweep: " + name)
        if not ok:
            fails.append(name)

    with tempfile.TemporaryDirectory() as t:
        repo = os.path.join(t, "demo")
        os.makedirs(os.path.join(repo, "src"))
        os.makedirs(os.path.join(repo, "tests"))
        dup = "\n".join(f"    let value_number_{i} = compute_something_long_name({i}, other_argument_here);" for i in range(8))
        for n in "abc":
            open(os.path.join(repo, "src", f"{n}.rs"), "w").write(
                "fn f() {\n    let x = foo().unwrap();\n    let _ = bar();\n    // TODO a\n    // TODO b\n    // FIXME c\n    let t = 86400 * 2;\n" + dup + "\n}\n")
        open(os.path.join(repo, "tests", "t.rs"), "w").write("fn t() { std::thread::sleep(d); x.unwrap(); }\n")
        open(os.path.join(repo, "main.tf"), "w").write('resource "x" "y" {\n  cidr = "0.0.0.0/0"\n}\n')
        res = scan(repo)
        by = {p["id"]: p for p in res["patterns"]}
        check("languages detected", res["languages"].get("rust") == 4 and res["languages"].get("terraform") == 1)
        check("unwrap counted in prod only, spread = 3 files", by["unwrap-prod"]["count"] == 3 and by["unwrap-prod"]["files"] == 3)
        check("sleep counted in tests only", by["sleep-in-test"]["count"] == 1)
        check("magic number found", by.get("magic-number", {}).get("count") == 3)
        check("todo cluster needs 3 markers per file", by["todo-cluster"]["score"] == 3 * 2)
        check("terraform open cidr, harm 5", by["tf-open-cidr"]["score"] == 5)
        check("anchors are path:line", by["unwrap-prod"]["anchors"][0].endswith(":2"))
        check("copied block across 3 files", bool(res["copied_blocks"]) and res["copied_blocks"][0]["files"] == 3)
        check("sorted by score", [p["score"] for p in res["patterns"]] == sorted([p["score"] for p in res["patterns"]], reverse=True))
        ad = os.path.join(t, "agentic")
        old = os.environ.get("ASD_AGENTIC_DIR")
        os.environ["ASD_AGENTIC_DIR"] = ad
        try:
            path, body = record(repo, 3, "three items")
            data = json.load(open(path))
            check("record honours ASD_AGENTIC_DIR", path == os.path.join(ad, "pattern-sweep", "demo.json"))
            check("record body", data["items"] == 3 and data["summary"] == "three items" and data["last_run"].endswith("Z"))
            record(repo, 0)
            check("record overwrites", json.load(open(path))["items"] == 0)
        finally:
            if old is None:
                os.environ.pop("ASD_AGENTIC_DIR", None)
            else:
                os.environ["ASD_AGENTIC_DIR"] = old
    print(f"{len(fails)} failed" if fails else "all passed")
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("scan")
    s.add_argument("--root", required=True)
    r = sub.add_parser("record")
    r.add_argument("--root", required=True)
    r.add_argument("--items", type=int, required=True)
    r.add_argument("--summary", default="")
    r.add_argument("--repo-name", default=None)
    s.add_argument("--repo-name", default=None)
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.cmd == "scan":
        res = scan(a.root)
        if a.repo_name:
            res["repo"] = a.repo_name
        print(json.dumps(res, indent=1))
        return 0
    if a.cmd == "record":
        path, body = record(a.root, a.items, a.summary, a.repo_name)
        print(json.dumps({"path": path, **body}))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
