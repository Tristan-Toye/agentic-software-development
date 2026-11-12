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
                     [--build-log DOSSIER]                # also logs `QUESTION: <id> — <question>`
    ask_owner.py reply ID --text TEXT
    ask_owner.py wait ID [--timeout SECONDS] [--build-log DOSSIER]   # default: no timeout; the
                                                           # build log gets `OWNER-RULING (<date>): <id> — <answer>`
    ask_owner.py accept ID --text TEXT [--build-log DOSSIER]
    ask_owner.py list [--open] [--dossier ID]
    ask_owner.py show ID
    ask_owner.py note --root R --dossier ID [--stage S] --text T
    ask_owner.py --selftest

`note` appends an `update` line to `$ASD_RUNS_DIR/<repo>.<ID>.jsonl` (default
~/.agentic/runs), the run's updates store (references/runner.md); same single
O_APPEND write and 3,500-character cut.

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
POLL = float(os.environ.get("ASD_OWNER_POLL") or 2.0)  # tests shorten it



def main_repo_name(root: str) -> str:
    """The main checkout's directory name for `root`, also when `root` is a
    worktree: the dashboard names a repo by its main checkout, so a question
    or an update posted from `../<repo>-<ID>` must carry `<repo>`."""
    root = os.path.abspath(root)
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, timeout=10)
        common = out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        common = ""
    main = common[: -len("/.git")] if common.endswith("/.git") else root
    return os.path.basename(main.rstrip("/")) or "root"

def store_dir() -> str:
    d = os.environ.get("ASD_QUESTIONS_DIR") or os.path.expanduser("~/.agentic/questions")
    os.makedirs(d, exist_ok=True)
    return d


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def clip(text: str) -> str:
    return text if len(text) <= CUT else text[:CUT] + " [cut]"


def build_log(path: str | None, line: str) -> None:
    """`--build-log DOSSIER`: write one build-log line through dossier_edit's lock."""
    if not path:
        return
    import dossier_edit
    print(dossier_edit.log_or_say(path, [" ".join(line.split())]), file=sys.stderr)


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
    """The owner's answer: an option button, or a typed message the agent accepted (`accept`)."""
    for ln in lines:
        if ln.get("kind") == "answer" and (ln.get("from") == "owner" or ln.get("accepted") is True):
            return ln
    return None


def parse_option(spec: str) -> dict:
    parts = spec.split("|", 2)
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise SystemExit(f"option must be KEY|label|consequence: {spec!r}")
    return {"key": parts[0].strip(), "label": parts[1].strip(), "consequence": parts[2].strip()}


# ---------------------------------------------------------------- the approval lint
# A run started by the runner has standing authority for the steps below (references/owner-questions.md, "What is
# worth asking"): a question that only asks leave for one of them stops the run for nothing. The lint is narrow on
# purpose, and applies only to a run the runner started: phrase patterns checked against the question texts of past runs, so a design question that mentions a push
# in passing goes through. ASD_ASK_LINT=0 turns it off.
KINDS = ("design", "product", "uncertainty")
_PR = r"(?:the |a |its |this |my )?(?:draft |plan |pull )?(?:PR|pull request)"
APPROVAL_PATTERNS = [
    ("push the branch", re.compile(r"\b(?:may|can|shall|should) I (?:now )?push\b[^?]{0,40}\bbranch\b"
                                  r"|\bpush (?:the |its |this |my )?(?:\w+ ){0,2}branch\b[^?]{0,60}\bopen (?:a |the )?(?:draft )?(?:PR|pull request)", re.I)),
    ("open the pull request", re.compile(r"\b(?:may|can|shall|should) I (?:now )?open " + _PR, re.I)),
    ("mark ready for review", re.compile(r"\b(?:may|can|shall|should) I (?:now )?mark\b[^?]{0,30}\bready\b"
                                       r"|\bmark (?:the |this |it |that |its )?(?:draft )?(?:PR |pull request )?(?:#?\d+ )?(?:as )?ready(?: for review)?\b[^?]{0,20}\?", re.I)),
    ("approve the plan or the revisions", re.compile(r"\bapprove (?:the |this |my )?(?:plan|description)\b"
                                                    r"|\bapprove (?:all )?(?:\d+|these|the) (?:\w+ )?(?:revisions|changes|readings)\b", re.I)),
    ("retry or stop after a failure", re.compile(r"\bretry\b[^?.]{0,25}\bor (?:stop|give up|abort)\b|\b(?:stop|abort|give up) or retry\b", re.I)),
    ("end the stage anyway", re.compile(r"\bend (?:the|this) stage anyway\b"
                                       r"|\brunner\b[^.?]{0,80}\b(?:won't|will not|wouldn't|refus\w+|has refused)\b[^.?]{0,60}\b(?:stage|phase)\b", re.I)),
]


def approval_kind(question: str, options: list[dict]) -> str | None:
    """The name of the approval pattern the question matches, or None. An option label is checked for the
    stage-end case only ("End the stage anyway" as a button)."""
    for name, pat in APPROVAL_PATTERNS:
        if pat.search(question):
            return name
    for o in options:
        if re.search(r"\bend (?:the|this) stage anyway\b", o.get("label", ""), re.I):
            return "end the stage anyway"
    return None


def cmd_ask(a: argparse.Namespace) -> int:
    options = [parse_option(o) for o in (a.option or [])]
    if not 2 <= len(options) <= 4:
        raise SystemExit("need 2 to 4 --option values")
    # Only a run the runner started (ASD_RUN_KEY) has the standing authority; a terminal run's start gate still asks.
    if os.environ.get("ASD_RUN_KEY") and (os.environ.get("ASD_ASK_LINT") or "1").strip() != "0":
        hit = approval_kind(a.question, options)
        if hit:
            print(f"REFUSED: this asks leave for a step the run already has authority for ({hit}). Do the step, record it in "
                  "## Build log with the usual line and name it in the run's summary: references/owner-questions.md, "
                  "\"A run started by the runner carries standing authority for\". Only a decision the owner has to make "
                  "(a design, a product choice, an uncertainty you cannot settle) is a question. If this really is one, "
                  "reword it as that decision, or set ASD_ASK_LINT=0.", file=sys.stderr)
            return 2
    keys = [o["key"] for o in options]
    if len(set(keys)) != len(keys):
        raise SystemExit("option keys must be distinct")
    rec = a.recommend or ""
    if rec and rec not in keys:
        raise SystemExit(f"--recommend {rec!r} is not one of {keys}")
    root = os.path.abspath(a.root)
    repo = main_repo_name(root)
    prefix = f"{repo}.{a.dossier}.Q"
    d = store_dir()
    pat = re.compile(re.escape(prefix) + r"(\d+)\.jsonl$")
    n = 1 + max([int(m.group(1)) for f in os.listdir(d) if (m := pat.match(f))] or [0])
    line = {
        "ts": now(), "from": "agent", "kind": "question", "text": clip(a.question),
        "dossier": a.dossier, "repo": repo, "root": root, "options": options,
        **({"run": os.environ["ASD_RUN_KEY"]} if os.environ.get("ASD_RUN_KEY") else {}),
        "recommendation": rec, "blocks": clip(a.blocks),
        **({"qkind": a.kind} if a.kind else {}),
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
    build_log(a.build_log, f"QUESTION: {qid} — {' '.join(a.question.split())[:240]}")
    return 0


def cmd_reply(a: argparse.Namespace) -> int:
    """`reply <id> --text T [--option KEY|label|consequence ...]`: the agent's message in the question's chat. An
    option adds a button to the question (a key it does not have yet), so the owner closes it with a press: offer
    your reading of the owner's typed words as one ("D|Do it as you wrote: …|…"), never close it yourself."""
    lines = read_lines(a.id)
    if final_answer(lines) is not None:
        print(f"{a.id} is already answered", file=sys.stderr)
        return 1
    have = {o.get("key") for ln in lines for o in (ln.get("options") or []) if isinstance(o, dict)}
    opts = [parse_option(o) for o in (a.option or [])]
    for o in opts:
        if o["key"] in have:
            raise SystemExit(f"option {o['key']} exists already; pick a new key")
        have.add(o["key"])
    line = {"ts": now(), "from": "agent", "kind": "reply", "text": clip(a.text)}
    if opts:
        line["options"] = opts
    # The recommendation can move: when the reply changes what you recommend, name the new key (an existing option,
    # or one this reply adds); the dashboard highlights it instead of the question's first recommendation.
    if getattr(a, "recommend", None):
        if a.recommend not in have:
            raise SystemExit(f"--recommend {a.recommend}: no such option (the question's, or one this reply adds)")
        line["recommend"] = a.recommend
    write_line(path_of(a.id), line)
    return 0


def cmd_accept(a: argparse.Namespace) -> int:
    """`accept <id> --text T`: the owner's typed message is an answer you can act on. T restates what you will do; it
    closes the question as answered (`from: agent, kind: answer, accepted: true`, with the owner's own words)."""
    lines = read_lines(a.id)
    if final_answer(lines) is not None:
        print(f"{a.id} is already answered", file=sys.stderr)
        return 1
    owner = [ln for ln in lines if ln.get("from") == "owner" and ln.get("kind") == "followup"]
    if not owner:
        print(f"{a.id}: the owner has written nothing to accept", file=sys.stderr)
        return 1
    write_line(path_of(a.id), {"ts": now(), "from": "agent", "kind": "answer", "accepted": True,
                               "text": clip(a.text), "owner_text": clip(str(owner[-1].get("text", "")))})
    build_log(a.build_log, f"OWNER-RULING ({today()}): {a.id} — {' '.join(a.text.split())[:300]}")
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
            build_log(a.build_log, f"OWNER-RULING ({today()}): {a.id} —{tag} {' '.join(str(ln.get('text', '')).split())[:300]}")
            return 0
        if state == "followup":
            emit(f"FOLLOWUP {a.id}: {ln.get('text', '')}")
            return 10
        if deadline is not None and time.monotonic() >= deadline:
            print(f"TIMEOUT {a.id}")
            return 3
        step = poll if deadline is None else max(0.0, min(poll, deadline - time.monotonic()))
        time.sleep(step)


def runs_dir() -> str:
    d = os.environ.get("ASD_RUNS_DIR") or os.path.expanduser("~/.agentic/runs")
    os.makedirs(d, exist_ok=True)
    return d


def cmd_note(a: argparse.Namespace) -> int:
    root = os.path.abspath(a.root)
    repo = main_repo_name(root)
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", a.dossier):
        raise SystemExit(f"bad dossier id: {a.dossier}")
    line = {"ts": now(), "kind": "update", "text": clip(a.text)}
    if a.stage:
        line["stage"] = a.stage
    write_line(os.path.join(runs_dir(), f"{repo}.{a.dossier}.jsonl"), line)
    return 0


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

        # ---- --kind and the approval lint ----
        kenv = dict(env, ASD_RUN_KEY="myrepo.W-1")
        def ask_q(text, extra=(), e=kenv, dossier="W-91", opts=("A|yes|do it", "B|no|do not")):
            args = ["ask", "--root", root, "--dossier", dossier, "--question", text, "--blocks", "x"]
            for o in opts:
                args += ["--option", o]
            return run(*args, *extra, env=e)
        k = ask_q("Two designs for the cache: keep one map or two?", ["--kind", "design"], dossier="W-91")
        kq = json.loads(open(os.path.join(env["ASD_QUESTIONS_DIR"], "myrepo.W-91.Q1.jsonl")).readline())
        check("--kind is stored on the question", k.returncode == 0 and kq.get("qkind") == "design" and kq.get("kind") == "question")
        check("--kind outside design|product|uncertainty is refused", ask_q("Which?", ["--kind", "other"]).returncode != 0)
        check("no --kind still works (older prompts)", "qkind" not in json.loads(open(os.path.join(env["ASD_QUESTIONS_DIR"], ask_q("Which cache?", dossier="W-10").stdout.strip() + ".jsonl")).readline()))
        refused = ["May I push the plan branch and open its pull request into development?",
                   "The build is done. May I mark the PR ready for review?",
                   "Do you accept the description, and may I mark the PR ready for review?",
                   "May I open the draft PR now?",
                   "Do you approve the plan so I can go on?",
                   "Please approve all 4 revisions before I continue.",
                   "The check failed on a network error. Retry or stop?",
                   "Phase 3 is finished, but the runner won't let the stage end. How should I proceed?",
                   "The task is gone. Should I end the stage anyway?"]
        rs = [ask_q(t) for t in refused]
        check("lint: each approval-type question is refused with exit 2", all(r.returncode == 2 for r in rs))
        check("lint: the message names the standing authority and says to do the step and log it",
              "references/owner-questions.md" in rs[0].stderr and "standing authority" in rs[0].stderr and "Build log" in rs[0].stderr
              and not os.path.exists(os.path.join(env["ASD_QUESTIONS_DIR"], "myrepo.W-91.Q2.jsonl")))
        check("lint: an option labelled 'End the stage anyway' is refused", ask_q("What now?", opts=("A|End the stage anyway|cuts it off", "B|Wait|waits")).returncode == 2)
        passing = ["Two defensible designs for the retry policy: a fixed delay or backoff? The dossier does not say.",
                   "The push hook rejects the commit because of a lint failure in a file outside the scope. Fix it here or defer it?",
                   "The plan lists two readings of AMBIGUITY 3. Which one do you want?",
                   "The reviewers disagree on the naming of the ready flag in the contract. Which name stays?",
                   "How do you want to proceed with the missing test, and may I push?"]
        check("lint: design and issue questions that mention push, retry or ready in passing go through", all(ask_q(t, dossier="W-11").returncode == 0 for t in passing))
        check("lint: ASD_ASK_LINT=0 turns it off", ask_q(refused[0], e=dict(kenv, ASD_ASK_LINT="0"), dossier="W-12").returncode == 0)
        check("lint: a run without ASD_RUN_KEY (a terminal run's start gate) is not linted", ask_q(refused[0], e=env, dossier="W-13").returncode == 0)

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
        r = run("reply", qid, "--text", "So you mean: keep the overlap.", "--option", "D|Confirm: keep the overlap|the turn does not wait", env=env)
        last = json.loads(open(path).read().splitlines()[-1])
        check("a reply can add a button", r.returncode == 0 and last.get("options") == [{"key": "D", "label": "Confirm: keep the overlap", "consequence": "the turn does not wait"}])
        check("a reply cannot reuse an option key", run("reply", qid, "--text", "x", "--option", "A|again|c", env=env).returncode != 0)
        r = run("reply", qid, "--text", "Having measured it, D is better.", "--recommend", "D", env=env)
        check("a reply can move the recommendation to an option that exists", r.returncode == 0 and json.loads(open(path).read().splitlines()[-1]).get("recommend") == "D")
        check("a reply cannot recommend an option that does not exist", run("reply", qid, "--text", "x", "--recommend", "Z", env=env).returncode != 0)

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

        write_line(path2, {"ts": now(), "from": "owner", "kind": "followup", "text": "just use the old port", "via": "page"})
        acc = run("accept", qid2, "--text", "I will keep port 7317.", env=env)
        w = run("wait", qid2, "--timeout", "1", env=env)
        fa = final_answer(read_lines(qid2)) or {}
        check("accept: the agent closes a typed answer it can act on, with the owner's words kept",
              acc.returncode == 0 and w.returncode == 0 and w.stdout.strip() == f"ANSWER {qid2}: I will keep port 7317."
              and fa.get("accepted") is True and fa.get("owner_text") == "just use the old port"
              and run("accept", qid2, "--text", "again", env=env).returncode == 1)

        # a worktree carries its main checkout's name
        main = os.path.join(tmp, "mainrepo")
        subprocess.run(["git", "init", "-q", main], check=True)
        subprocess.run(["git", "-C", main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        wt = os.path.join(tmp, "mainrepo-W-7")
        subprocess.run(["git", "-C", main, "worktree", "add", "-q", wt], check=True)
        check("a worktree is keyed by its main checkout", main_repo_name(wt) == "mainrepo" and main_repo_name(main) == "mainrepo")
        wq = run("ask", "--root", wt, "--dossier", "W-7", "--question", "q?", "--option", "A|a|x", "--option", "B|b|y", "--blocks", "z", env=env).stdout.strip()
        check("a question asked from a worktree carries the repo name", wq == "mainrepo.W-7.Q1")

        # --build-log: the QUESTION and OWNER-RULING lines the dashboard parses
        dos = os.path.join(tmp, "W-5.md")
        open(dos, "w", encoding="utf-8").write("---\nid: W-5\n---\n\n# T\n\n## Build log\n\n- first\n")
        bq = run(*base[:-2], "--blocks", "the build", "--build-log", dos, env=env).stdout.strip()
        check("ask --build-log writes `QUESTION: <id> — <question>`", f"- QUESTION: {bq} — Which?\n" in open(dos, encoding="utf-8").read())
        with open(os.path.join(env["ASD_QUESTIONS_DIR"], bq + ".jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now(), "from": "owner", "kind": "answer", "option": "A", "text": "go with one"}) + "\n")
        wt_ = run("wait", bq, "--timeout", "5", "--build-log", dos, env=env)
        got_log = open(dos, encoding="utf-8").read()
        check("wait --build-log writes the OWNER-RULING line", wt_.returncode == 0 and f"- OWNER-RULING ({today()}): {bq} — [option A] go with one\n" in got_log)
        check("those lines match the dashboard's parsers", re.search(r"^- QUESTION:\s*(\S+\.Q\d+)\s*—", got_log, re.M) is not None
              and re.search(r"^- OWNER-RULING\b[^:]*:\s*(\S+\.Q\d+)\s*—\s*(.*)$", got_log, re.M) is not None)
        run("wait", bq, "--timeout", "5", "--build-log", dos, env=env)
        check("wait run twice logs the ruling once", open(dos, encoding="utf-8").read().count("OWNER-RULING") == 1)

        # note
        env["ASD_RUNS_DIR"] = os.path.join(tmp, "runs")
        n = run("note", "--root", root, "--dossier", "W-1", "--stage", "Phase 4", "--text", "wave 1 back", env=env)
        nf = os.path.join(tmp, "runs", "myrepo.W-1.jsonl")
        got = json.loads(open(nf).read().splitlines()[-1]) if os.path.exists(nf) else {}
        check("note appends an update line", n.returncode == 0 and got.get("kind") == "update"
              and got.get("stage") == "Phase 4" and got.get("text") == "wave 1 back" and "ts" in got)
        run("note", "--root", root, "--dossier", "W-1", "--text", "y" * 5000, env=env)
        raw = open(nf).read().splitlines()[-1]
        check("note 3,500-char cut, no stage key", json.loads(raw)["text"] == "y" * 3500 + " [cut]"
              and "stage" not in json.loads(raw) and len(raw.encode()) < 4096)

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
    p.add_argument("--kind", choices=KINDS, help="design, product or uncertainty: what the owner is asked to decide (optional)")
    p.add_argument("--build-log", metavar="DOSSIER", help="also append `QUESTION: <id> — <question>` to this dossier's Build log")
    p = sub.add_parser("reply")
    p.add_argument("id")
    p.add_argument("--text", required=True)
    p.add_argument("--option", action="append", help="KEY|label|consequence: a button this reply adds")
    p.add_argument("--recommend", help="KEY: the option you now recommend, when it changed")
    p = sub.add_parser("accept")
    p.add_argument("id")
    p.add_argument("--text", required=True)
    p.add_argument("--build-log", metavar="DOSSIER", help="also append `OWNER-RULING (<date>): <id> — <text>` to this dossier's Build log")
    p = sub.add_parser("wait")
    p.add_argument("id")
    p.add_argument("--timeout", type=float)
    p.add_argument("--build-log", metavar="DOSSIER", help="when the owner has answered, append `OWNER-RULING (<date>): <id> — <answer>` to this dossier's Build log")
    p = sub.add_parser("list")
    p.add_argument("--open", action="store_true")
    p.add_argument("--dossier")
    p = sub.add_parser("show")
    p.add_argument("id")
    p = sub.add_parser("note")
    p.add_argument("--root", required=True)
    p.add_argument("--dossier", required=True)
    p.add_argument("--stage")
    p.add_argument("--text", required=True)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.cmd:
        ap.print_usage(sys.stderr)
        return 2
    return {"ask": cmd_ask, "reply": cmd_reply, "accept": cmd_accept, "wait": cmd_wait, "list": cmd_list, "show": cmd_show, "note": cmd_note}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
