#!/usr/bin/env python3
"""Owner questions from a run: ask, reply, wait, list, show.

The orchestrator asks the owner a question it cannot settle itself, keeps
working on what is not blocked, and waits only when it is blocked. The contract
is references/owner-questions.md. This script reads and writes the store
directly, so it works with the server (shared/questions/server.ts) down.

The store is one directory, $ASD_QUESTIONS_DIR (default ~/.agentic/questions),
one append-only JSONL file per question, `<repo>.<dossier>.Q<n>.jsonl`. Every
line is written with one O_APPEND write of under 4 KB; longer text is cut at
3,500 characters with " [cut]". The first `answer` line is final.

# Usage

    ask_owner.py ask --root R --dossier ID --question TEXT
                     --option "A|label|consequence" [--option ...]
                     [--recommend A] --blocks TEXT        # prints the id
    ask_owner.py reply ID --text TEXT
    ask_owner.py wait ID [--timeout SECONDS]               # default: no timeout
    ask_owner.py list [--open] [--dossier ID]
    ask_owner.py show ID
    ask_owner.py --selftest

`wait` prints ANSWER (exit 0), FOLLOWUP (exit 10) or TIMEOUT (exit 3), at most
40 lines. Python 3 standard library only.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

CUT = 3500
MAX_LINES = 40
POLL = 2.0


def store_dir() -> str:
    d = os.environ.get("ASD_QUESTIONS_DIR") or os.path.expanduser("~/.agentic/questions")
    os.makedirs(d, exist_ok=True)
    return d


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clip(text: str) -> str:
    return text if len(text) <= CUT else text[:CUT] + " [cut]"


def path_of(qid: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", qid) or "/" in qid:
        raise SystemExit(f"bad question id: {qid}")
    return os.path.join(store_dir(), qid + ".jsonl")


def write_line(path: str, obj: dict) -> None:
    data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def read_lines(qid: str) -> list[dict]:
    p = path_of(qid)
    if not os.path.exists(p):
        raise SystemExit(f"no such question: {qid}")
    out = []
    with open(p, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
    return out


def final_answer(lines: list[dict]) -> dict | None:
    for ln in lines:
        if ln.get("from") == "owner" and ln.get("kind") == "answer":
            return ln
    return None


def parse_option(spec: str) -> dict:
    parts = spec.split("|", 2)
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise SystemExit(f"option must be KEY|label|consequence: {spec!r}")
    return {"key": parts[0].strip(), "label": parts[1].strip(), "consequence": parts[2].strip()}


def cmd_ask(a: argparse.Namespace) -> int:
    options = [parse_option(o) for o in (a.option or [])]
    if not 2 <= len(options) <= 4:
        raise SystemExit("need 2 to 4 --option values")
    keys = [o["key"] for o in options]
    if len(set(keys)) != len(keys):
        raise SystemExit("option keys must be distinct")
    rec = a.recommend or ""
    if rec and rec not in keys:
        raise SystemExit(f"--recommend {rec!r} is not one of {keys}")
    root = os.path.abspath(a.root)
    repo = os.path.basename(root.rstrip("/")) or "root"
    prefix = f"{repo}.{a.dossier}.Q"
    d = store_dir()
    pat = re.compile(re.escape(prefix) + r"(\d+)\.jsonl$")
    n = 1 + max([int(m.group(1)) for f in os.listdir(d) if (m := pat.match(f))] or [0])
    line = {
        "ts": now(), "from": "agent", "kind": "question", "text": clip(a.question),
        "dossier": a.dossier, "repo": repo, "root": root, "options": options,
        "recommendation": rec, "blocks": clip(a.blocks),
    }
    while True:
        qid = f"{prefix}{n}"
        try:
            fd = os.open(os.path.join(d, qid + ".jsonl"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            n += 1
            continue
        try:
            os.write(fd, (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        break
    print(qid)
    return 0


def cmd_reply(a: argparse.Namespace) -> int:
    read_lines(a.id)
    write_line(path_of(a.id), {"ts": now(), "from": "agent", "kind": "reply", "text": clip(a.text)})
    return 0


def wait_once(qid: str) -> tuple[str, dict | None]:
    """('answer'|'followup'|'none', line) for the file as it is now."""
    lines = read_lines(qid)
    ans = final_answer(lines)
    if ans is not None:
        return "answer", ans
    last_agent = max((i for i, ln in enumerate(lines) if ln.get("from") == "agent"), default=-1)
    after = [ln for ln in lines[last_agent + 1:] if ln.get("from") == "owner"]
    if after:
        first = after[0]
        if first.get("kind") == "answer":
            return "answer", first
        return "followup", first
    return "none", None


def emit(text: str) -> None:
    out = text.splitlines()
    if len(out) > MAX_LINES:
        out = out[:MAX_LINES - 1] + ["[cut]"]
    print("\n".join(out))


def cmd_wait(a: argparse.Namespace) -> int:
    read_lines(a.id)
    deadline = None if a.timeout is None else time.monotonic() + a.timeout
    poll = getattr(a, "poll", POLL)
    while True:
        state, ln = wait_once(a.id)
        if state == "answer":
            opt = ln.get("option") or ""
            tag = f" [option {opt}]" if opt else ""
            emit(f"ANSWER {a.id}{tag}: {ln.get('text', '')}")
            return 0
        if state == "followup":
            emit(f"FOLLOWUP {a.id}: {ln.get('text', '')}")
            return 10
        if deadline is not None and time.monotonic() >= deadline:
            print(f"TIMEOUT {a.id}")
            return 3
        step = poll if deadline is None else max(0.0, min(poll, deadline - time.monotonic()))
        time.sleep(step)


def summarise(qid: str) -> tuple[dict, str]:
    lines = read_lines(qid)
    q = lines[0] if lines else {}
    return q, ("answered" if final_answer(lines) else "open")


def cmd_list(a: argparse.Namespace) -> int:
    d = store_dir()
    rows = []
    for f in sorted(os.listdir(d)):
        if not f.endswith(".jsonl"):
            continue
        qid = f[:-6]
        q, state = summarise(qid)
        if a.open and state != "open":
            continue
        if a.dossier and q.get("dossier") != a.dossier:
            continue
        rows.append(f"{qid}\t{state}\t{(q.get('text') or '')[:80]}")
    emit("\n".join(rows))
    return 0


def cmd_show(a: argparse.Namespace) -> int:
    rows = []
    for ln in read_lines(a.id):
        who = f"{ln.get('from')}/{ln.get('kind')}"
        extra = f" [option {ln['option']}]" if ln.get("option") else ""
        rows.append(f"{ln.get('ts')} {who}{extra}: {ln.get('text', '')}")
        for o in ln.get("options") or []:
            rows.append(f"  {o['key']}) {o['label']} -> {o['consequence']}")
        if ln.get("recommendation"):
            rows.append(f"  recommend: {ln['recommendation']}")
        if ln.get("blocks"):
            rows.append(f"  blocks: {ln['blocks']}")
    emit("\n".join(rows))
    return 0


# ---------------------------------------------------------------- selftest


def selftest() -> int:
    me = os.path.abspath(__file__)
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            failures.append(name)

    def run(*args, env=None, timeout=60):
        return subprocess.run([sys.executable, me, *args], capture_output=True, text=True,
                              env=env or os.environ, timeout=timeout)

    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, ASD_QUESTIONS_DIR=os.path.join(tmp, "q"))
        os.environ["ASD_QUESTIONS_DIR"] = env["ASD_QUESTIONS_DIR"]
        root = os.path.join(tmp, "myrepo")
        os.makedirs(root)
        base = ["ask", "--root", root, "--dossier", "W-1", "--question", "Which?",
                "--option", "A|one|does one", "--option", "B|two|does two",
                "--recommend", "A", "--blocks", "the build"]

        r1 = run(*base, env=env)
        r2 = run(*base, env=env)
        check("ask prints id Q1", r1.stdout.strip() == "myrepo.W-1.Q1")
        check("id allocation gives Q2", r2.stdout.strip() == "myrepo.W-1.Q2")
        first = json.loads(open(os.path.join(env["ASD_QUESTIONS_DIR"], "myrepo.W-1.Q1.jsonl")).readline())
        check("question line fields", first["kind"] == "question" and first["repo"] == "myrepo"
              and len(first["options"]) == 2 and first["recommendation"] == "A")

        bad = run("ask", "--root", root, "--dossier", "W-1", "--question", "x", "--option",
                  "A|a|b", "--blocks", "y", env=env)
        check("one option refused", bad.returncode != 0)
        bad = run(*base[:-4], "--recommend", "Z", "--blocks", "y", env=env)
        check("recommendation outside keys refused", bad.returncode != 0)
        bad = run("ask", "--root", root, "--dossier", "W-1", "--question", "x", "--option",
                  "A|a|b", "--option", "Bnolabel", "--blocks", "y", env=env)
        check("malformed option refused", bad.returncode != 0)

        procs = [subprocess.Popen([sys.executable, me, *base], env=env, stdout=subprocess.PIPE, text=True)
                 for _ in range(2)]
        ids = sorted(p.communicate()[0].strip() for p in procs)
        check("concurrent ask gives distinct ids", ids == ["myrepo.W-1.Q3", "myrepo.W-1.Q4"])

        qid = "myrepo.W-1.Q1"
        path = os.path.join(env["ASD_QUESTIONS_DIR"], qid + ".jsonl")
        check("reply appends", run("reply", qid, "--text", "fact", env=env).returncode == 0
              and json.loads(open(path).read().splitlines()[-1])["kind"] == "reply")

        def owner(kind, text, option="", delay=0.5):
            time.sleep(delay)
            write_line(path, {"ts": now(), "from": "owner", "kind": kind, "text": text,
                              "option": option, "via": "cli"})

        t = threading.Thread(target=owner, args=("answer", "go with A", "A"))
        t.start()
        w = run("wait", qid, "--timeout", "20", env=env)
        t.join()
        check("wait on answer", w.returncode == 0 and w.stdout.strip() == f"ANSWER {qid} [option A]: go with A")

        qid2 = "myrepo.W-1.Q2"
        path2 = os.path.join(env["ASD_QUESTIONS_DIR"], qid2 + ".jsonl")
        write_line(path2, {"ts": now(), "from": "owner", "kind": "followup", "text": "why?", "via": "cli"})
        w = run("wait", qid2, "--timeout", "20", env=env)
        check("wait on followup exits 10", w.returncode == 10 and w.stdout.startswith(f"FOLLOWUP {qid2}: why?"))
        run("reply", qid2, "--text", "because", env=env)
        w = run("wait", qid2, "--timeout", "1", env=env)
        check("wait after reply times out exit 3", w.returncode == 3 and w.stdout.strip() == f"TIMEOUT {qid2}")

        write_line(path, {"ts": now(), "from": "owner", "kind": "answer", "text": "later B", "option": "B", "via": "cli"})
        lines = read_lines(qid)
        check("first answer final", final_answer(lines)["text"] == "go with A")

        run("reply", qid2, "--text", "x" * 5000, env=env)
        last = json.loads(open(path2).read().splitlines()[-1])
        check("3,500-char cut", last["text"] == "x" * 3500 + " [cut]"
              and len(open(path2).read().splitlines()[-1].encode()) < 4096)

        ls = run("list", "--open", env=env).stdout
        check("list --open hides answered", qid + "\t" not in ls and qid2 + "\t" in ls)
        ls = run("list", "--dossier", "W-9", env=env).stdout
        check("list --dossier filters", ls.strip() == "")
        sh = run("show", qid, env=env).stdout
        check("show prints thread", "A) one -> does one" in sh and "owner/answer" in sh)

    print("FAILED: " + ", ".join(failures) if failures else "all ok")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Owner questions from a run.")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("ask")
    p.add_argument("--root", required=True)
    p.add_argument("--dossier", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--option", action="append")
    p.add_argument("--recommend")
    p.add_argument("--blocks", required=True)
    p = sub.add_parser("reply")
    p.add_argument("id")
    p.add_argument("--text", required=True)
    p = sub.add_parser("wait")
    p.add_argument("id")
    p.add_argument("--timeout", type=float)
    p = sub.add_parser("list")
    p.add_argument("--open", action="store_true")
    p.add_argument("--dossier")
    p = sub.add_parser("show")
    p.add_argument("id")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.cmd:
        ap.print_usage(sys.stderr)
        return 2
    return {"ask": cmd_ask, "reply": cmd_reply, "wait": cmd_wait, "list": cmd_list, "show": cmd_show}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
