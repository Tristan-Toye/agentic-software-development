#!/usr/bin/env python3
"""List the lines in test files that make a test likely to flake under load.

A blind test author never runs its tests on a loaded machine, and the recorded
flaky tests share a few shapes: a sleep used to wait for something, a
wall-clock bound under one second, a read of the real clock, threads or tasks
whose interleaving the assertion depends on, state shared between tests, a
fixed port or path, and unseeded randomness. This script finds those shapes by
pattern, so Phase 6 can log them and Phase 7 can decide whether the `tests`
lens runs and which lines it reads. A marker is a reason to look, never a
verdict: the lens judges whether the line is a race.

# Usage

    flake_risk.py [--max N=40] [--exempt TABLE | --no-exempt] FILE ...
    flake_risk.py --selftest

Prints one line per marker, `path:line  <kind>  <the line, cut at 120
characters>`, at most `--max` of them, then one summary line:

    FLAKE-RISK: <n> markers in <k> of <m> files (sleep 2, wall-clock 1)
    FLAKE-RISK: none in <m> files
    ... (<e> exempt)        appended when the exempt table cleared any

# Kinds, and what is deliberately not one

Calibrated against a full audit of a 435-file Rust suite (every marker read
and judged), so each exclusion below names a shape that was never the race:

- `sleep` — a sleep call. Not one: a sleep inside a loop that checks a
  deadline (`deadline`, `.elapsed()`, `Instant::now()` in the loop): that is a
  poll interval, the stable form.
- `wall-clock` — a sub-second duration used as an UPPER bound: after `<` or
  `<=`, as a `timeout`/`recv_timeout`/`within`/`deadline` argument, or a
  sub-second `const` later used that way. Not one: a lower bound (`>=`), an
  `assert_eq!` of a value, a sleep argument, a poll interval.
- `clock` — a read of the wall clock as data. Not one: a monotonic read (the
  deadline of a poll), or a read turned into `.as_nanos()` (a uniqueness
  token or an opaque payload).
- `concurrency` — a thread, task or process started by the test. Not one: a
  spawn whose handle is later awaited, joined, aborted, passed on, or
  collected and iterated; a fake server's accept loop.
- `shared-state` — a mutable static (`static mut`, a static `Mutex`/`RwLock`/
  `Cell`/atomic, `lazy_static!`), a `global`, an environment variable written.
  Not one: an immutable once-init (`OnceLock<T>` of no interior mutability).
- `fixed-resource` — a fixed `host:port` on a line that binds or listens, or a
  `/tmp/...` literal on a line that does file I/O. Not one: an address or path
  that is only parsed, formatted or compared.
- `random` — an unseeded generator.

Comment lines and `use`/`import` lines are skipped, and string literals are
blanked before every kind but `fixed-resource` is matched, so a word in an
assertion message is not code.

# The exempt table

A marker the lens judged harmless, whose shape no general rule above can tell
from the race, goes in the repo's exempt table: `.flake-risk-exempt.md`, the
first one found walking up from each scanned file (or `--exempt TABLE`). It is
a Markdown table; every row is `| path | kind | line contains | why harmless |`
with the path relative to the table, the kind or `*`, a substring of the
stripped line (backticks around it are dropped, `\\|` is a literal bar), and a
reason. A row with no reason makes the table unusable (exit 2): an exemption
is a ruling, and a ruling names its mechanism. A row that matched no line of
a scanned file is reported on stderr as stale.

Exit codes: 0 no marker; 1 markers found; 2 unusable input (no file, a file
that cannot be read, or a malformed exempt table).

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import sys
import tempfile

ONE_SECOND_MS = 1000
LINE_CHARS = 120
EXEMPT_NAME = ".flake-risk-exempt.md"
LOOKBACK = 60
COMMENT = re.compile(r"^\s*(//|#(?!\[)|--|\*|/\*)")
USE_LINE = re.compile(r"^\s*(pub(\([^)]*\))?\s+)?use\s|^\s*(from\s+\S+\s+)?import\s")

# Kinds matched on the line with its string literals blanked.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("sleep", re.compile(
        r"\bthread::sleep\b|\btime::sleep\b|\btime\.sleep\(|\basyncio\.sleep\(|"
        r"\bThread\.Sleep\(|\bTask\.Delay\(|\bsetTimeout\(|^\s*sleep\s+[\d.]")),
    ("wall-clock", re.compile(r"\btimeout\s*=\s*0?\.\d")),
    ("clock", re.compile(
        r"\bSystemTime::now\(\)|\b(Utc|Local)::now\(\)|\btime\.time\(\)|"
        r"\bdatetime\.(utc)?now\(|\bDateTime(Offset)?\.(Utc)?Now\b|\bDate\.now\(\)|"
        r"\bnew Date\(\)")),
    ("concurrency", re.compile(
        r"\bthread::spawn\b|\btokio::spawn\b|\btask::spawn\b|\bspawn_blocking\b|\bJoinSet\b|"
        r"\bjoin_all\b|\bthreading\.Thread\(|\b(Thread|Process)PoolExecutor\b|"
        r"\basyncio\.(gather|create_task)\(|\bTask\.Run\(|\bParallel\.|"
        r"\bPromise\.all\(|\bnew Thread\(|\bfork\(\)")),
    ("shared-state", re.compile(
        r"\bstatic mut\b|\bstatic\s+\w+\s*:[^=]*\b(Mutex|RwLock|RefCell|Cell|Atomic\w+)\b|"
        r"\blazy_static!|\bthread_local!|"
        r"\benv::(set|remove)_var\(|\bos\.environ\[[^\]]+\]\s*=(?!=)|"
        r"\bos\.environ\.(setdefault|update)\(|\bsetenv\(|"
        r"\bSetEnvironmentVariable\(|\bprocess\.env\.\w+\s*=(?!=)|^\s*global\s+\w")),
    ("random", re.compile(
        r"\bthread_rng\(\)|\brand::random\b|\brandom\.(random|randint|choice|shuffle|sample)\(|"
        r"\bMath\.random\(\)|\bnew Random\(\)")),
]
# Kinds matched on the raw line: the resource IS a string literal.
HOST_PORT = re.compile(r"\b(127\.0\.0\.1|localhost|0\.0\.0\.0):\d{2,5}\b")
BINDS = re.compile(r"\bbind\b|\blisten\b|TcpListener|UdpSocket|\bserve\b|--port")
TMP_PATH = re.compile(r"[\"']/tmp/[\w./-]+[\"']")
FILE_IO = re.compile(
    r"\bcreate|\bwrite|\bremove|\bopen\(|\bmkdir|\bFile::|\bfs::|set_permissions|\.exists\(|"
    r"\bunlink|\brename|\bcopy\(")

LOOP_HEADER = re.compile(r"^\s*(loop\b|while\b|for\b|async\s+for\b)")
FN_HEADER = re.compile(r"^\s*(pub(\([^)]*\))?\s+)?(async\s+)?(fn|def)\s")
IMPL_FOR = re.compile(r"^\s*impl\b.*\bfor\b")
ROUTE = re.compile(r"\.route\(")
BLOCKING_SLEEP = re.compile(r"\bthread::sleep\b|\btime\.sleep\(|\bThread\.Sleep\(|^\s*sleep\s+[\d.]")
ASYNC_SLEEP = re.compile(r"\btime::sleep\(|\basyncio\.sleep\(|\bTask\.Delay\(")
AWAIT_BEFORE = re.compile(r"\bawait\s+$")
DEADLINE = re.compile(r"deadline|\.elapsed\(\)|Instant::now\(\)|time\.monotonic\(\)|_DEADLINE\b")

MILLIS = re.compile(r"\b(?:from_millis|FromMilliseconds)\(\s*([\d_]+)\s*\)")
SUB_MILLI = re.compile(r"\bfrom_(?:micros|nanos)\(|\bfrom_secs_f(?:32|64)\(\s*0?\.\d")
CONST_DURATION = re.compile(
    r"\bconst\s+(\w+)\s*:\s*(?:\w+::)*Duration\s*=\s*(?:\w+::)*Duration::"
    r"(?:from_millis\(\s*([\d_]+)\s*\)|from_(?:micros|nanos)\(|from_secs_f(?:32|64)\(\s*0?\.\d)")
UPPER_CALL = re.compile(r"\b(timeout|recv_timeout|within|deadline)\w*\b[^;]*$")
LOWER_TAIL = re.compile(r"(>=?|==|!=)\s*$")

SPAWN_LET = re.compile(r"^\s*let\s+(?:mut\s+)?(\w+)\b")
SPAWN_PUSH = re.compile(r"\b(\w+)\.push\(")
SIGNATURE_END = re.compile(r"^\s*\)\s*(->[^{]*)?\{\s*$")
TEST_ATTR = re.compile(r"^\s*#\[(\w+::)*test\b")
SERVER_BODY = re.compile(r"\baccept\(|\bserve\(|axum::serve|incoming\(")


def blank_strings(text: str, python_like: bool) -> str:
    """`text` with every string literal's contents and every trailing `//`
    comment replaced by spaces; newlines and the quotes themselves are kept,
    so line numbers and columns survive."""
    out = list(text)
    i, n = 0, len(text)
    quotes = ('"', "'") if python_like else ('"',)
    while i < n:
        ch = text[i]
        if not python_like and text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if python_like and ch == "#":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        raw = re.match(r'r(#*)"', text[i:]) if not python_like else None
        if raw and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
            close = '"' + raw.group(1)
            start = i + len(raw.group(0))
            end = text.find(close, start)
            end = n if end < 0 else end
            for k in range(start, end):
                if out[k] != "\n":
                    out[k] = " "
            i = end + len(close)
            continue
        if ch in quotes:
            triple = python_like and text.startswith(ch * 3, i)
            delim = ch * 3 if triple else ch
            k = i + len(delim)
            while k < n and not text.startswith(delim, k):
                if text[k] == "\\":
                    if out[k] != "\n":
                        out[k] = " "
                    k += 1
                if k < n and out[k] != "\n":
                    out[k] = " "
                k += 1
            i = k + len(delim)
            continue
        i += 1
    return "".join(out)


def indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def enclosing(lines: list[str], index: int):
    """The lines that open the blocks around line `index`, innermost first,
    read by indentation (no parser: formatted code is enough)."""
    level = indent(lines[index])
    for back in range(index - 1, max(-1, index - LOOKBACK), -1):
        line = lines[back]
        if line.strip() and indent(line) < level:
            level = indent(line)
            yield back, line
            if level == 0:
                return


def in_double(lines: list[str], index: int) -> bool:
    """Whether line `index` sits in a test double: a trait impl, or the
    handler of a fake server's route. A sleep there is the double's latency,
    the subject of the test, never the test waiting for something."""
    for _, line in enclosing(lines, index):
        if IMPL_FOR.match(line) or ROUTE.search(line):
            return True
    return False


def sleep_is_awaited_here(line: str) -> bool:
    """Whether every async sleep on `line` is awaited on the spot. One that is
    not is a timer future handed to the code under test (a shutdown signal,
    a `select!` arm), not a wait."""
    if BLOCKING_SLEEP.search(line):
        return True
    for match in ASYNC_SLEEP.finditer(line):
        depth, k = 1, match.end()
        while k < len(line) and depth:
            depth += {"(": 1, ")": -1}.get(line[k], 0)
            k += 1
        if line[k:].lstrip().startswith(".await") or AWAIT_BEFORE.search(line[:match.start()]):
            return True
    return False


def static_is_benign(text: str, lines: list[str], index: int) -> bool:
    """A unit mutex is a serialisation lock (the cure for shared state, not the
    disease), an atomic touched only by `fetch_add` is a counter that makes
    names unique, and a static declared inside one test's own body is reached
    by that test alone."""
    line = lines[index]
    if re.search(r"Mutex<\(\)>", line):
        return True
    for back, header in enclosing(lines, index):
        if FN_HEADER.match(header):
            above = [l for l in lines[max(0, back - 4):back] if l.strip()]
            if any(TEST_ATTR.match(l) for l in above):
                return True
            break
    match = re.search(r"\bstatic\s+(\w+)\s*:[^=]*\bAtomic\w+", line)
    if not match:
        return False
    uses = [m.end() for m in re.finditer(rf"\b{match.group(1)}\b", text)]
    return all(text[end:].lstrip().startswith((".fetch_add(", ":")) for end in uses)


def in_deadline_loop(lines: list[str], index: int) -> bool:
    """Whether line `index` sits in a loop whose header or body checks a
    deadline: the sleep in it is a poll interval."""
    own = indent(lines[index])
    level = own
    for back in range(index - 1, max(-1, index - LOOKBACK), -1):
        line = lines[back]
        if not line.strip() or indent(line) >= level:
            continue
        level = indent(line)
        if FN_HEADER.match(line):
            return False
        if LOOP_HEADER.match(line) or line.rstrip().endswith("loop {"):
            end = index + 1
            while end < len(lines) and (not lines[end].strip() or indent(lines[end]) > level):
                end += 1
            return bool(DEADLINE.search("\n".join(lines[back:end + 1])))
    return False


def spawn_is_settled(lines: list[str], index: int) -> bool:
    """Whether the task or thread started on line `index` is awaited, joined,
    aborted, handed on or collected later in the file, or is a fake server's
    accept loop: either way no assertion reads it mid-flight."""
    if SERVER_BODY.search("\n".join(lines[index:index + 5])):
        return True
    name = None
    for back in range(index, max(-1, index - 8), -1):
        if back < index and lines[back].rstrip().endswith(";"):
            break
        let = SPAWN_LET.match(lines[back])
        push = SPAWN_PUSH.search(lines[back])
        if let or push:
            name = (let or push).group(1)
            break
    if name is None:
        for _, line in enclosing(lines, index):
            if FN_HEADER.match(line) or SIGNATURE_END.match(line):
                return not lines[index].rstrip().endswith(";")
            holder = SPAWN_LET.match(line) or SPAWN_PUSH.search(line)
            if holder:
                name = holder.group(1)
                break
    if name is None:
        return False
    rest = "\n".join(lines[index + 1:])
    word = re.escape(name)
    return bool(re.search(
        rf"\b{word}\b\s*(\(|\.await|\.join\(|\.abort\(|\.is_finished\(|\.into_iter\(|\.drain\()|"
        rf"[(,]\s*{word}\s*[),]|\bfor\s+.+\bin\s+(&\s*)?(mut\s+)?{word}\b|"
        rf"join_all\(\s*{word}\b|gather\(\*\s*{word}\b", rest))


def sub_second_consts(text: str) -> set[str]:
    names = set()
    for match in CONST_DURATION.finditer(text):
        millis = match.group(2)
        if millis is None or int(millis.replace("_", "")) < ONE_SECOND_MS:
            names.add(match.group(1))
    return names


def is_upper_bound(prefix: str) -> bool:
    """Whether a duration that starts right after `prefix` (its line up to
    the duration) is an upper bound."""
    head = re.sub(r"(\w+::)*\s*$", "", prefix).rstrip()
    if LOWER_TAIL.search(head) or "assert_eq" in head:
        return False
    if re.search(r"<=?$", head):
        return True
    return bool(UPPER_CALL.search(head))


def wall_clock(line: str, consts: set[str]) -> bool:
    for match in MILLIS.finditer(line):
        if int(match.group(1).replace("_", "")) < ONE_SECOND_MS and is_upper_bound(line[:match.start()]):
            return True
    for match in SUB_MILLI.finditer(line):
        if is_upper_bound(line[:match.start()]):
            return True
    for name in consts:
        for match in re.finditer(rf"\b{re.escape(name)}\b(?!\s*[*/+-])", line):
            prefix = line[:match.start()]
            if CONST_DURATION.search(line) or prefix.rstrip().endswith(("+", "-", "*", "/")):
                continue
            if not is_upper_bound(prefix):
                continue
            return True
    return False


def markers(text: str, python_like: bool = False) -> list[tuple[int, str, str]]:
    """Every (line number, kind, line) on a non-comment line, in line order."""
    raw_lines = text.splitlines()
    lines = blank_strings(text, python_like).splitlines()
    lines += [""] * (len(raw_lines) - len(lines))
    consts = sub_second_consts(text)
    found: list[tuple[int, str, str]] = []
    for index, (raw, line) in enumerate(zip(raw_lines, lines)):
        if COMMENT.match(raw) or USE_LINE.match(raw):
            continue
        kinds = [kind for kind, pattern in PATTERNS if pattern.search(line)]
        if "sleep" in kinds and (in_deadline_loop(lines, index) or in_double(lines, index)
                                 or not sleep_is_awaited_here(line)):
            kinds.remove("sleep")
        if "shared-state" in kinds and static_is_benign("\n".join(lines), lines, index):
            kinds.remove("shared-state")
        if "concurrency" in kinds and spawn_is_settled(lines, index):
            kinds.remove("concurrency")
        if "clock" in kinds and ".as_nanos()" in "\n".join(lines[index:index + 5]):
            kinds.remove("clock")
        if "wall-clock" not in kinds and wall_clock(line, consts):
            kinds.append("wall-clock")
        if (HOST_PORT.search(raw) and BINDS.search(line)) or (TMP_PATH.search(raw) and FILE_IO.search(line)):
            kinds.append("fixed-resource")
        for kind in kinds:
            found.append((index + 1, kind, raw.strip()))
    return found


class ExemptError(ValueError):
    pass


def load_exempt(table: str) -> list[dict]:
    """The rows of one exempt table, each {path, kind, contains, reason, used}."""
    base = os.path.dirname(os.path.abspath(table))
    rows: list[dict] = []
    with open(table, encoding="utf-8") as fh:
        for number, line in enumerate(fh, start=1):
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [cell.strip().replace("\\|", "|")
                     for cell in re.split(r"(?<!\\)\|", line.strip("|"))]
            if len(cells) != 4:
                raise ExemptError(f"{table}:{number}: a row has 4 cells, this has {len(cells)}")
            path, kind, contains, reason = cells
            if set(path) <= set("-: ") or path.lower() == "path":
                continue
            contains = contains.strip("`")
            if not contains or not reason:
                raise ExemptError(f"{table}:{number}: every row names a line and a reason")
            rows.append({"path": os.path.normpath(os.path.join(base, path.strip("`"))),
                         "kind": kind.strip("`"), "contains": contains,
                         "reason": reason, "used": False, "where": f"{table}:{number}"})
    return rows


def find_table(path: str, cache: dict[str, str | None]) -> str | None:
    directory = os.path.dirname(os.path.abspath(path))
    walked = []
    while directory not in cache:
        walked.append(directory)
        candidate = os.path.join(directory, EXEMPT_NAME)
        if os.path.isfile(candidate):
            cache[directory] = candidate
            break
        parent = os.path.dirname(directory)
        if parent == directory:
            cache[directory] = None
            break
        directory = parent
    for seen in walked:
        cache[seen] = cache[directory]
    return cache[directory]


def run(paths: list[str], cap: int, exempt: str | None = None, use_tables: bool = True) -> int:
    if not paths:
        print("flake_risk: no file given", file=sys.stderr)
        return 2
    tables: dict[str, list[dict]] = {}
    cache: dict[str, str | None] = {}
    rows: list[str] = []
    counts: dict[str, int] = {}
    hit_files = 0
    exempted = 0
    scanned: set[str] = set()
    try:
        for path in paths:
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError as err:
                print(f"flake_risk: cannot read {path}: {err.strerror}", file=sys.stderr)
                return 2
            table = exempt if exempt else (find_table(path, cache) if use_tables else None)
            if table and table not in tables:
                tables[table] = load_exempt(table)
            own = [row for row in tables.get(table, [])
                   if row["path"] == os.path.normpath(os.path.abspath(path))]
            scanned.add(os.path.normpath(os.path.abspath(path)))
            found = []
            for number, kind, line in markers(text, path.endswith(".py")):
                match = next((row for row in own if row["kind"] in (kind, "*")
                              and row["contains"] in line), None)
                if match:
                    match["used"] = True
                    exempted += 1
                else:
                    found.append((number, kind, line))
            if found:
                hit_files += 1
            for number, kind, line in found:
                counts[kind] = counts.get(kind, 0) + 1
                cut = line if len(line) <= LINE_CHARS else line[: LINE_CHARS - 1] + "…"
                rows.append(f"{path}:{number}  {kind}  {cut}")
    except (OSError, ExemptError) as err:
        print(f"flake_risk: unusable exempt table: {err}", file=sys.stderr)
        return 2
    for table_rows in tables.values():
        for row in table_rows:
            if not row["used"] and row["path"] in scanned:
                print(f"flake_risk: stale exempt row {row['where']} matched nothing", file=sys.stderr)
    for row in rows[:cap]:
        print(row)
    if len(rows) > cap:
        print(f"… {len(rows) - cap} more")
    tail = f" ({exempted} exempt)" if exempted else ""
    if not rows:
        print(f"FLAKE-RISK: none in {len(paths)} files{tail}")
        return 0
    kinds = ", ".join(f"{kind} {n}" for kind, n in sorted(counts.items()))
    print(f"FLAKE-RISK: {len(rows)} markers in {hit_files} of {len(paths)} files ({kinds}){tail}")
    return 1


def selftest() -> int:
    failures: list[str] = []

    def kinds_of(text: str, python_like: bool = False) -> list[str]:
        return [kind for _, kind, _ in markers(text, python_like)]

    cases = [
        ("rust sleep", "    std::thread::sleep(Duration::from_secs(2));", ["sleep"]),
        ("sub-second ceiling", "    assert!(t < Duration::from_millis(50));", ["wall-clock"]),
        ("millis with underscore", "let d = Duration::from_millis(1_500);", []),
        ("one second is the floor", "assert!(e < Duration::from_millis(1000));", []),
        ("poll interval is not a bound", "const POLL: Duration = Duration::from_millis(50);", []),
        ("poll sleep is a sleep, not a bound", "tokio::time::sleep(Duration::from_millis(20)).await;", ["sleep"]),
        ("sub-second timeout argument", "let r = recv_timeout(Duration::from_millis(200));", ["wall-clock"]),
        ("micros in a comparison", "assert!(d < Duration::from_micros(5));", ["wall-clock"]),
        ("elapsed against a sub-second bound", "assert!(t.elapsed() < Duration::from_millis(300));", ["wall-clock"]),
        ("lower bound is not a ceiling", "assert!(elapsed >= Duration::from_millis(300));", []),
        ("lower half of a range", "assert!(e >= Duration::from_millis(200) && e < Duration::from_secs(2));", []),
        ("assert_eq of a value", "assert_eq!(COMMIT_RETRY_DELAY, Duration::from_millis(50));", []),
        ("sub-second deadline", "let deadline = Instant::now() + Duration::from_millis(500);", ["wall-clock"]),
        ("elapsed against the floor", "assert!(t.elapsed() < Duration::from_secs(1));", []),
        ("monotonic deadline", "let deadline = Instant::now() + Duration::from_secs(45);", []),
        ("wall clock as data", "let stamp = SystemTime::now();", ["clock"]),
        ("wall clock as a token", "let n = SystemTime::now()\n    .duration_since(UNIX_EPOCH)\n    .unwrap()\n    .as_nanos();", []),
        ("unjoined tokio spawn", "tokio::spawn(async move { work().await });", ["concurrency"]),
        ("env var written", "std::env::set_var(\"KEY\", \"v\");", ["shared-state"]),
        ("env var compared is not a write", "if os.environ[\"K\"] == \"v\":", []),
        ("parsed port is not bound", "let addr = \"127.0.0.1:8080\";", []),
        ("bound fixed port", "let l = TcpListener::bind(\"127.0.0.1:8080\").unwrap();", ["fixed-resource"]),
        ("formatted tmp path", "let s = StubGitHost::with_source_root(PathBuf::from(\"/tmp/src\"));", []),
        ("written tmp path", "std::fs::write(\"/tmp/shared.json\", b\"x\").unwrap();", ["fixed-resource"]),
        ("unseeded random", "let mut rng = rand::thread_rng();", ["random"]),
        ("seeded random", "let mut rng = StdRng::seed_from_u64(7);", []),
        ("python sleep", "    time.sleep(0.1)", ["sleep"]),
        ("python timeout", "    q.get(timeout=0.2)", ["wall-clock"]),
        ("rust comment", "    // thread::sleep is never used here", []),
        ("python comment", "    # time.sleep(1)", []),
        ("use line", "use tokio::time::sleep;", []),
        ("import of OnceLock", "use std::sync::{Arc, OnceLock};", []),
        ("immutable once-init", "static KEEP: OnceLock<tracing::Dispatch> = OnceLock::new();", []),
        ("static mutex", "static RECORD: Mutex<Vec<String>> = Mutex::new(Vec::new());", ["shared-state"]),
        ("word in a message", "    .expect(\"the spawn_blocking section must not panic\");", []),
        ("sleep in a script string", "const HOLDER: &str = \"import time; \\\n    time.sleep(60)\";", []),
        ("rust attribute is code", "#[tokio::test] async fn t() { tokio::spawn(x()); }", ["concurrency"]),
        ("csharp now", "var t = DateTime.UtcNow;", ["clock"]),
        ("clean line", "assert_eq!(queue.flush(), 3);", []),
    ]
    for name, text, want in cases:
        got = kinds_of(text)
        if got != want:
            failures.append(f"{name}: want {want}, got {got}")

    poll = ("async fn wait() {\n    let deadline = Instant::now() + LIMIT;\n    loop {\n"
            "        if ready().await {\n            return;\n        }\n"
            "        assert!(Instant::now() < deadline, \"late\");\n"
            "        tokio::time::sleep(POLL).await;\n    }\n}\n")
    if kinds_of(poll) != []:
        failures.append(f"a sleep in a deadline loop is a poll interval: got {kinds_of(poll)}")
    blind = "async fn t() {\n    send().await;\n    tokio::time::sleep(Duration::from_secs(2)).await;\n    assert!(seen());\n}\n"
    if kinds_of(blind) != ["sleep"]:
        failures.append(f"a blind sleep before an assert is flagged: got {kinds_of(blind)}")
    counted = ("fn t() {\n    for _ in 0..200 {\n        if ready() {\n            break;\n        }\n"
               "        std::thread::sleep(Duration::from_millis(10));\n    }\n}\n")
    if kinds_of(counted) != ["sleep"]:
        failures.append(f"a counted poll with no deadline is flagged: got {kinds_of(counted)}")
    joined = ("let handle = tokio::spawn(async move {\n    run().await\n});\n"
              "handle.await.unwrap();\n")
    if kinds_of(joined) != []:
        failures.append(f"an awaited spawn is settled: got {kinds_of(joined)}")
    collected = ("for _ in 0..8 {\n    handles.push(tokio::spawn(async move { go().await }));\n}\n"
                 "for handle in handles {\n    handle.await.unwrap();\n}\n")
    if kinds_of(collected) != []:
        failures.append(f"a collected and iterated spawn is settled: got {kinds_of(collected)}")
    handed = "fn spawn_run() -> (Tx, JoinHandle<()>) {\n    let task = tokio::spawn(run());\n    (shutdown, task)\n}\n"
    if kinds_of(handed) != []:
        failures.append(f"a spawn handed to the caller is settled: got {kinds_of(handed)}")
    server = "std::thread::spawn(move || {\n    for _ in 0..2 {\n        let Ok((s, _)) = listener.accept() else {\n"
    if kinds_of(server) != []:
        failures.append(f"a fake server's accept loop is settled: got {kinds_of(server)}")
    named = ("const BUDGET: Duration = Duration::from_millis(200);\n"
             "fn t() {\n    assert!(elapsed <= BUDGET, \"slow\");\n}\n")
    if kinds_of(named) != ["wall-clock"]:
        failures.append(f"a sub-second const used as a ceiling is flagged: got {kinds_of(named)}")
    named_floor = ("const BUDGET: Duration = Duration::from_millis(1500);\n"
                   "fn t() {\n    assert!(elapsed <= BUDGET);\n    sleep(SHORT);\n}\n")
    if kinds_of(named_floor) != []:
        failures.append(f"a const of at least a second is no ceiling finding: got {kinds_of(named_floor)}")
    double = ("struct Slow;\n\n#[async_trait]\nimpl Runner for Slow {\n"
              "    async fn run(&self) -> u8 {\n        tokio::time::sleep(self.delay).await;\n        0\n    }\n}\n")
    if kinds_of(double) != []:
        failures.append(f"a sleep inside a trait impl is the double's latency: got {kinds_of(double)}")
    route = ("let router = Router::new().route(\n    \"/v1\",\n    post(move || async move {\n"
             "        tokio::time::sleep(delay).await;\n        ok()\n    }),\n);\n")
    if kinds_of(route) != []:
        failures.append(f"a sleep in a fake route handler is the subject: got {kinds_of(route)}")
    timer = "let h = runner.run(tokio::time::sleep(SHUTDOWN_DELAY));\nh.await;\n"
    if kinds_of(timer) != []:
        failures.append(f"a sleep future handed on is no wait: got {kinds_of(timer)}")
    awaited = "    tokio::time::sleep(Duration::from_secs(2)).await;\n"
    if kinds_of(awaited) != ["sleep"]:
        failures.append(f"an awaited sleep is still a sleep: got {kinds_of(awaited)}")
    serial = "static SERIAL: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());\n"
    if kinds_of(serial) != []:
        failures.append(f"a unit mutex is a serialisation lock: got {kinds_of(serial)}")
    counter = ("static COUNTER: AtomicU32 = AtomicU32::new(0);\n"
               "fn name() -> String {\n    format!(\"db_{}\", COUNTER.fetch_add(1, Ordering::SeqCst))\n}\n")
    if kinds_of(counter) != []:
        failures.append(f"a fetch_add-only atomic is a uniqueness counter: got {kinds_of(counter)}")
    flag = ("static RAN: AtomicBool = AtomicBool::new(false);\n"
            "fn t() {\n    RAN.store(true, Ordering::SeqCst);\n}\n")
    if kinds_of(flag) != ["shared-state"]:
        failures.append(f"an atomic flag written by a test is shared state: got {kinds_of(flag)}")
    mapped = ("let tasks: Vec<_> = (0..16)\n    .map(|_| {\n        let store = store.clone();\n"
              "        tokio::spawn(async move { store.ensure().await })\n    })\n    .collect();\n"
              "for task in tasks {\n    task.await.unwrap();\n}\n")
    if kinds_of(mapped) != []:
        failures.append(f"a spawn collected through a map is settled: got {kinds_of(mapped)}")
    summed = ("const SLACK: Duration = Duration::from_millis(500);\n"
              "fn t() {\n    let j = tokio::time::timeout(DELAY + WAIT + SLACK, handle);\n}\n")
    if kinds_of(summed) != []:
        failures.append(f"a sub-second const inside a sum is no ceiling of its own: got {kinds_of(summed)}")
    local = ("#[test]\nfn t() {\n    static RAN: AtomicBool = AtomicBool::new(false);\n"
             "    RAN.store(true, Ordering::SeqCst);\n}\n")
    if kinds_of(local) != []:
        failures.append(f"a static inside one test's body is that test's own: got {kinds_of(local)}")
    helper_static = ("fn helper() {\n    static RAN: AtomicBool = AtomicBool::new(false);\n"
                     "    RAN.store(true, Ordering::SeqCst);\n}\n")
    if kinds_of(helper_static) != ["shared-state"]:
        failures.append(f"a static inside a shared helper is shared: got {kinds_of(helper_static)}")
    returned = ("fn spawn_run(\n    config: Config,\n) -> JoinHandle<()> {\n"
                "    tokio::spawn(async move { run(&config).await })\n}\n")
    if kinds_of(returned) != []:
        failures.append(f"a spawn returned from a multi-line signature is handed on: got {kinds_of(returned)}")
    factory = ("let spawn_one = || {\n    let s = store.clone();\n    tokio::spawn(async move { s.go().await })\n};\n"
               "let first = spawn_one();\nfirst.await.unwrap();\n")
    if kinds_of(factory) != []:
        failures.append(f"a spawn made by a closure the test calls is handed on: got {kinds_of(factory)}")
    py_string = "def t():\n    s = 'time.sleep(1)'\n    assert s\n"
    if kinds_of(py_string, python_like=True) != []:
        failures.append(f"a python string is blanked: got {kinds_of(py_string, True)}")

    with tempfile.TemporaryDirectory() as tmp:
        clean = os.path.join(tmp, "clean_test.rs")
        risky = os.path.join(tmp, "risky_test.py")
        with open(clean, "w", encoding="utf-8") as fh:
            fh.write("fn t() {\n    assert_eq!(1 + 1, 2);\n}\n")
        with open(risky, "w", encoding="utf-8") as fh:
            fh.write("import time\n\ndef test_x():\n    time.sleep(0.5)\n    assert done()\n")

        def quiet(paths: list[str], **kw) -> tuple[int, str, str]:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = run(paths, 40, **kw)
            return rc, out.getvalue(), err.getvalue()

        rc, _, _ = quiet([clean])
        if rc != 0:
            failures.append("a clean file exits 0")
        rc, out, _ = quiet([clean, risky])
        if rc != 1 or f"{risky}:4  sleep" not in out or "1 markers in 1 of 2 files (sleep 1)" not in out:
            failures.append(f"a marker exits 1 with its location and summary: {out!r}")
        if quiet([os.path.join(tmp, "missing.rs")])[0] != 2:
            failures.append("an unreadable file exits 2")
        if quiet([])[0] != 2:
            failures.append("no file exits 2")

        table = os.path.join(tmp, EXEMPT_NAME)
        with open(table, "w", encoding="utf-8") as fh:
            fh.write("| path | kind | line contains | why harmless |\n|---|---|---|---|\n"
                     "| risky_test.py | sleep | `time.sleep(0.5)` | the half second is the subject |\n"
                     "| clean_test.rs | * | `never there` | stale on purpose |\n")
        rc, out, err = quiet([clean, risky])
        if rc != 0 or "FLAKE-RISK: none in 2 files (1 exempt)" not in out:
            failures.append(f"a table row found by walking up exempts its marker: {out!r}")
        if "stale exempt row" not in err:
            failures.append(f"a row that matched nothing is reported stale: {err!r}")
        if quiet([risky], use_tables=False)[0] != 1:
            failures.append("--no-exempt ignores the table")
        with open(table, "a", encoding="utf-8") as fh:
            fh.write("| risky_test.py | sleep | `time.sleep` |  |\n")
        if quiet([risky])[0] != 2:
            failures.append("a row with no reason makes the table unusable")
        bars = os.path.join(tmp, "bars.md")
        with open(bars, "w", encoding="utf-8") as fh:
            fh.write("| path | kind | line contains | why |\n|---|---|---|---|\n"
                     "| risky_test.py | sleep | `x.map(\\|v\\| v)` | an escaped bar is a bar |\n")
        if load_exempt(bars)[0]["contains"] != "x.map(|v| v)":
            failures.append(f"an escaped bar reads as a bar: {load_exempt(bars)[0]['contains']!r}")

    for failure in failures:
        print("FAIL", failure)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", metavar="FILE")
    ap.add_argument("--max", type=int, default=40, help="marker lines printed at most")
    ap.add_argument("--exempt", metavar="TABLE", help=f"the exempt table (default: nearest {EXEMPT_NAME})")
    ap.add_argument("--no-exempt", action="store_true", help="ignore every exempt table")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    return run(args.paths, args.max, exempt=args.exempt, use_tables=not args.no_exempt)


if __name__ == "__main__":
    sys.exit(main())
