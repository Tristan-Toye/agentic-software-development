#!/usr/bin/env python3
"""Run one dossier through /work-on without a terminal: the dossier runner.

One headless Claude Code process per stage, so every stage starts from a clean
context; owner questions are asked and answered through ask_owner.py. One
runner owns one dossier and never picks up another while it waits. The
contract is references/runner.md.

Each stage is `claude -p --input-format stream-json --output-format
stream-json --verbose`, run in the repository root with the environment
variable ASD_RUN_KEY=<repo>.<ID> and the runner-mode rules appended to the
system prompt. The first user message is the command; for a stage after the
first, a second message carries the hand-off (the task, every earlier
checkpoint summary, the last NEXT: line). The runner reads the output events;
the `result` event ends a turn and the turn's RUNNER: line decides:

    RUNNER: STAGE-DONE <name>   stdin closes, the next stage is a new process
    RUNNER: WAITING <id>[,..]   `ask_owner.py wait` runs here (no tokens
                                spent); the owner's line goes into the same
                                live process as `OWNER ANSWER|FOLLOWUP <id>: ..`
    RUNNER: DONE                state done, exit 0
    RUNNER: FAILED <why>, or no RUNNER: line, or a crashed process
                                state failed; a question to the owner: retry
                                the stage, or stop

# Usage

    run_dossier.py --root R --dossier ID [--command work-on|stub]
                   [--model M] [--permission-mode auto] [--max-stages N]
    run_dossier.py status [--root R --dossier ID]
    run_dossier.py stop --root R --dossier ID
    run_dossier.py --selftest

Exit codes of a run: 0 done, 1 failed (the owner chose to stop), 2 another
runner owns the dossier, 3 stopped, 4 --max-stages reached.

# Stores

$ASD_RUNS_DIR (default ~/.agentic/runs): `<key>.jsonl` (append-only updates,
`runner` lines written here), `<key>.runner.json` {pid, started, state, stage,
session, questions, claude_pid}, `<key>.checkpoints.json` [{name, summary}],
`<key>.stderr.log`. $ASD_CLAUDE_BIN overrides the `claude` executable (it may
carry arguments); $ASD_RUNNER_GRACE (seconds, default 1.5) is how long a stage
that was handed a second message waits after its first result for another.

The runner's stdout is one line per event worth seeing, never model text.
Python 3 standard library only.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time

CUT = 3500
HERE = os.path.dirname(os.path.abspath(__file__))
ASK_OWNER = os.path.join(HERE, "ask_owner.py")
PLUGIN = "agentic-software-development"

RUNNER_RULES = """\
You run under the dossier runner and nobody reads the terminal.
- Ask the owner only through scripts/ask_owner.py, and never wait in the background: keep working on whatever is not blocked; when nothing can proceed, end the turn with the line `RUNNER: WAITING <id>[,<id>...]` and stop. The ids are question ids from ask_owner.py only; a wait on a background command or workflow is not a WAITING line: just end the turn, and Claude Code re-invokes you when it finishes. The runner waits for you and sends the owner's message into this session as `OWNER ANSWER <id>: ...` or `OWNER FOLLOWUP <id>: ...`. On a follow-up answer it with `ask_owner.py reply`, then end the turn with `RUNNER: WAITING <id>` again.
- Never end a turn to wait for a command: run commands in the foreground (raise the Bash timeout up to 10 minutes) or wait for them inside the same turn. A workflow or background task that notifies you later is fine, but say `waiting for <what>` in plain text in that turn (never as a `RUNNER:` line); the runner keeps the session open for its notification.
- End each stage with its phase_checkpoint call and then, as the last line of your reply, `RUNNER: STAGE-DONE <stage name>`, and stop. The next stage starts in a new process with your checkpoints as its hand-off.
- End with `RUNNER: DONE` when /work-on is finished, and with `RUNNER: FAILED <why>` when you cannot go on.
- Keep the owner informed: post an update with `ask_owner.py note --root R --dossier ID --text "..."` when a stage starts and ends and whenever something worth knowing happens (a wave returned, the suite went green or red, an arbitration was ruled, a plan changed). Write it for a person reading on a phone: what happened and what it means, in plain sentences; markdown bullets and small tables are welcome. Never paste commands, file paths, SHAs, flags or log output into an update; the build log keeps those.
"""

LIMIT_RE = re.compile(r"usage limit|rate.?limit|weekly limit|limit reached|out of (?:extra )?usage|hit your .*limit|\b429\b|overloaded", re.I)


def usage_limit(text: str) -> bool:
    """Whether a failed turn is a usage or rate limit (wait and retry), not a real failure."""
    return bool(LIMIT_RE.search(text or ""))


def limit_reset(text: str, now_s: float | None = None) -> float:
    """When to retry after a limit: the reset epoch the CLI prints after `|`, or an ISO
    reset time in the text, else in 30 minutes. Always at least 60 s from now."""
    now_s = time.time() if now_s is None else now_s
    m = re.search(r"\|(\d{10})\b", text or "") or re.search(r"resets?[_ ]?at\D{0,4}(\d{10})\b", text or "")
    if m:
        return max(now_s + 60, float(m.group(1)) + 60)
    m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))", text or "")
    if m:
        try:
            ts = datetime.datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).timestamp()
            return max(now_s + 60, ts + 60)
        except ValueError:
            pass
    hr = human_reset(text, now_s)
    if hr is not None:
        return max(now_s + 60, hr + 60)
    return now_s + float(os.environ.get("ASD_RUNNER_LIMIT_WAIT") or 1800)


# ---- accounts: several Claude logins, one config dir each (CLAUDE_CONFIG_DIR) ----
ACCOUNTS_FILE = lambda: os.environ.get("ASD_ACCOUNTS") or os.path.expanduser("~/.agentic/accounts.json")
ACCOUNT_STATE = lambda: os.environ.get("ASD_ACCOUNT_STATE") or os.path.expanduser("~/.agentic/accounts-state.json")


def load_accounts() -> list[dict]:
    """`~/.agentic/accounts.json`: {"accounts": [{"name": "a", "dir": "~/.claude"}, ...]}, in
    order of preference. Without it there is one account: the default config dir."""
    try:
        with open(ACCOUNTS_FILE()) as fh:
            acc = [a for a in json.load(fh).get("accounts", []) if a.get("name") and a.get("dir")]
    except (OSError, ValueError):
        acc = []
    return [{"name": a["name"], "dir": os.path.expanduser(a["dir"])} for a in acc] or [{"name": "default", "dir": os.path.expanduser("~/.claude")}]


def account_state() -> dict:
    try:
        with open(ACCOUNT_STATE()) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def mark_limited(name: str, until: float) -> None:
    st = account_state()
    st[name] = {"limited_until": until, "marked": now()}
    write_json(ACCOUNT_STATE(), st)


def pick_account(now_s: float | None = None, prefer: str = "") -> tuple[dict, float]:
    """The account to run on and the time it is usable from: one that is not limited,
    `prefer` first, else the one whose limit resets first."""
    now_s = time.time() if now_s is None else now_s
    accs, st = load_accounts(), account_state()
    free_at = lambda a: float((st.get(a["name"]) or {}).get("limited_until") or 0)
    order = sorted(accs, key=lambda a: (a["name"] != prefer, accs.index(a)))
    for a in order:
        if free_at(a) <= now_s:
            return a, now_s
    a = min(accs, key=free_at)
    return a, free_at(a)


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def human_reset(text: str, now_s: float) -> float | None:
    """"resets Nov 6 at 12pm (Europe/Brussels)" or "resets 3:30pm (Europe/Brussels)" -> epoch."""
    m = re.search(r"resets?\s+(?:(?P<mon>[A-Za-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2}),?\s+(?:at\s+)?)?(?P<h>\d{1,2})(?::(?P<mi>\d{2}))?\s*(?P<ap>am|pm)\s*(?:\((?P<tz>[A-Za-z_]+/[A-Za-z_]+|UTC)\))?", text or "", re.I)
    if not m:
        return None
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(m.group("tz")) if m.group("tz") else datetime.datetime.now().astimezone().tzinfo
    except Exception:
        tz = datetime.datetime.now().astimezone().tzinfo
    h = int(m.group("h")) % 12 + (12 if m.group("ap").lower() == "pm" else 0)
    mi = int(m.group("mi") or 0)
    base = datetime.datetime.fromtimestamp(now_s, tz)
    if m.group("mon") and m.group("mon").lower()[:3] in MONTHS:
        cand = base.replace(month=MONTHS[m.group("mon").lower()[:3]], day=int(m.group("day")), hour=h, minute=mi, second=0, microsecond=0)
        if cand.timestamp() < now_s - 86400:
            cand = cand.replace(year=cand.year + 1)
    else:
        cand = base.replace(hour=h, minute=mi, second=0, microsecond=0)
        if cand.timestamp() <= now_s:
            cand += datetime.timedelta(days=1)
    return cand.timestamp()


NUDGE = ("RUNNER: your last turn ended without a RUNNER: line and nothing has happened since. "
         "If you were waiting on a background command or workflow, check its result now and continue. "
         "Every turn must end with a RUNNER: line (STAGE-DONE, WAITING, DONE or FAILED).")

# A question id from ask_owner.py: `<repo>.<ID>.Qn` (or a bare `Qn`).
QUESTION_ID = re.compile(r"(?:^|\.)Q\d+$")
MARKER = re.compile(r"^RUNNER:\s*(STAGE-DONE|WAITING|DONE|FAILED)\b[ \t]*(.*)$")


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clip(text: str, n: int = CUT) -> str:
    return text if len(text) <= n else text[:n] + " [cut]"


def runs_dir() -> str:
    d = os.environ.get("ASD_RUNS_DIR") or os.path.expanduser("~/.agentic/runs")
    os.makedirs(d, exist_ok=True)
    return d



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

def key_of(root: str, dossier: str) -> str:
    return f"{main_repo_name(root)}.{dossier}"


def check_ident(value: str, what: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", value):
        raise SystemExit(f"bad {what}: {value}")


def append_line(path: str, obj: dict) -> None:
    data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def write_json(path: str, obj) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    os.replace(tmp, path)


def read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def say(msg: str) -> None:
    print(f"[run_dossier] {msg}", flush=True)


def user_message(text: str) -> bytes:
    msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]},
           "parent_tool_use_id": None}
    return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")


def parse_marker(text: str) -> tuple[str, str] | None:
    """The turn's last RUNNER: line as (verb, rest), or None."""
    found = None
    for ln in text.splitlines():
        m = MARKER.match(ln.strip())
        if m:
            found = (m.group(1), m.group(2).strip())
    return found


def kill_group(proc: subprocess.Popen | None, sig=signal.SIGTERM) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


class Runner:
    def __init__(self, root: str, dossier: str, command: str, model: str | None,
                 permission_mode: str, max_stages: int, items: str = ""):
        self.root = os.path.abspath(root)
        self.dossier = dossier
        self.command = command
        self.items = items  # fix-deferred: the D-ids the owner selected, "D-1,D-3"; empty = all
        self.model = model
        self.permission_mode = permission_mode
        self.max_stages = max_stages
        prev_acct = (read_json(os.path.join(runs_dir(), key_of(root, dossier) + ".runner.json"), {}) or {}).get("account", "")
        self.account, _ = pick_account(prefer=prev_acct)
        self.key = key_of(root, dossier)
        d = runs_dir()
        self.updates = os.path.join(d, self.key + ".jsonl")
        self.state_path = os.path.join(d, self.key + ".runner.json")
        self.cp_path = os.path.join(d, self.key + ".checkpoints.json")
        self.err_path = os.path.join(d, self.key + ".stderr.log")
        self.state: dict = {"pid": os.getpid(), "account": self.account["name"], "root": root, "command": command, "permission_mode": permission_mode, "started": now(), "state": "running", "stage": 0,
                            "session": "", "questions": [], "claude_pid": None}
        self.checkpoints: list[dict] = read_json(self.cp_path, [])
        self.seen_tool_ids: set[str] = set()
        self.stopping = False
        self.proc: subprocess.Popen | None = None
        self.children: list[subprocess.Popen] = []
        self.grace = float(os.environ.get("ASD_RUNNER_GRACE") or 1.5)

    # -- records ----------------------------------------------------------

    def save(self, **kw) -> None:
        self.state.update(kw)
        write_json(self.state_path, self.state)

    def log(self, text: str, state: str | None = None) -> None:
        line = {"ts": now(), "kind": "runner", "text": clip(text), "stage": str(self.state["stage"])}
        if state:
            line["state"] = state
        if self.state.get("session"):
            line["session"] = self.state["session"]
        try:
            append_line(self.updates, line)
        except OSError:
            pass

    def deferred_report(self) -> None:
        """After a /work-on run: one short extra process runs /deferred <ID> and
        stores its report at <key>.deferred.md, which the dashboard shows and
        /fix-deferred reads. ASD_RUNNER_DEFERRED=0 skips it."""
        if self.command != "work-on" or os.environ.get("ASD_RUNNER_DEFERRED") == "0" or self.stopping:
            return
        self.event("deferred report", "running")
        saved = (self.command, self.checkpoints)
        self.command, self.checkpoints = "deferred", []
        text = ""
        try:
            q, extra = self.spawn()
            kind, val = self.read_turn(q, extra)
            if kind == "result":
                text = str(val[0])  # type: ignore[index]
        finally:
            kill_group(self.proc)
            self.end_process()
            self.command, self.checkpoints = saved
        text = re.sub(r"^RUNNER:.*$", "", text, flags=re.M).strip() if text else ""
        if not text:
            self.event("deferred report: none produced")
            return
        path = os.path.join(runs_dir(), self.key + ".deferred.md")
        with open(path, "w") as fh:
            fh.write(text + "\n")
        n = len(re.findall(r"^###\s+D-\d+", text, re.M))
        self.event(f"deferred report: {n} issue(s)")

    def product_summary(self, live: bool = True) -> bool:
        """After a /work-on run (and its deferred report): one short extra process
        runs /product-summary <ID> and stores the page at <key>.summary.md, which
        the dashboard shows above the dossier. On `ASD_RUNNER_SUMMARY_MODEL`
        (default sonnet; empty = the run's model). ASD_RUNNER_SUMMARY=0 skips it."""
        if (live and self.command != "work-on") or os.environ.get("ASD_RUNNER_SUMMARY") == "0" or self.stopping:
            return False
        self.event("product summary", "running" if live else None)
        saved = (self.command, self.checkpoints, self.model)
        model = os.environ.get("ASD_RUNNER_SUMMARY_MODEL", "sonnet")
        self.command, self.checkpoints, self.model = "product-summary", [], (model or self.model)
        text = ""
        try:
            q, extra = self.spawn()
            kind, val = self.read_turn(q, extra)
            if kind == "result" and not val[1]:  # type: ignore[index]
                text = str(val[0])  # type: ignore[index]
        finally:
            kill_group(self.proc)
            self.end_process()
            self.command, self.checkpoints, self.model = saved
        text = re.sub(r"^RUNNER:.*$", "", text, flags=re.M).strip() if text else ""
        if not text:
            self.event("product summary: none produced")
            return False
        with open(os.path.join(runs_dir(), self.key + ".summary.md"), "w") as fh:
            fh.write(text + "\n")
        self.event("product summary written")
        return True

    def record_result(self, text: str) -> None:
        """The finished run's last reply, as the owner would see it in a terminal:
        a `result` line in the updates store (the dashboard shows it on top, and the
        owner can chat with this session; `run_dossier.py chat`)."""
        body = re.sub(r"^RUNNER:.*$", "", text or "", flags=re.M).strip()
        sid = self.state.get("session") or ""
        self.save(result_session=sid)
        try:  # a chat belongs to one run's reply: the next run's reply starts a new one
            os.remove(os.path.join(runs_dir(), self.key + ".chat.json"))
        except OSError:
            pass
        if not body:
            return
        try:
            append_line(self.updates, {"ts": now(), "kind": "result", "text": body[:20000], "session": sid,
                                       "stage": str(self.state["stage"]), "command": self.command})
        except OSError:
            pass

    def no_checkpoint(self, name: str) -> None:
        """A stage ended without a phase_checkpoint (the tool was missing or
        skipped): write the stage line the hook would have written, so the
        dashboard still draws the phase divider, and hand the next stage the
        dossier itself — /work-on resumes from its status and ## Build log."""
        name = name.strip() or f"stage {self.state['stage']}"
        self.checkpoints.append({"name": name, "summary": "No checkpoint was recorded for this stage. "
                                 "Resume from the dossier: its status and ## Build log say where the run is."})
        write_json(self.cp_path, self.checkpoints)
        try:
            append_line(self.updates, {"ts": now(), "kind": "stage", "stage": clip(name),
                                       "text": "stage ended without a checkpoint; the build log is the hand-off"})
        except OSError:
            pass

    def event(self, text: str, state: str | None = None) -> None:
        say(f"{self.key} {text}")
        self.log(text, state)

    # -- the process ------------------------------------------------------

    def claude_cmd(self) -> list[str]:
        cmd = shlex.split(os.environ.get("ASD_CLAUDE_BIN") or "claude")
        cmd += ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--permission-mode", self.permission_mode, "--append-system-prompt", RUNNER_RULES]
        if self.model:
            cmd += ["--model", self.model]
        return cmd

    def handoff(self) -> str | None:
        if not self.checkpoints:
            return None
        args = f" {self.items}" if self.items and self.command == "fix-deferred" else ""
        rows = [f"RUNNER HAND-OFF. Task: run /{PLUGIN}:{self.command} {self.dossier}{args} in {self.root}.",
                "Earlier stages, in order:"]
        for i, cp in enumerate(self.checkpoints, 1):
            rows.append(f"{i}. {cp['name']}: {clip(cp['summary'], 4000)}")
        nxt = ""
        for cp in reversed(self.checkpoints):
            for ln in reversed(cp["summary"].splitlines()):
                if ln.strip().startswith("NEXT:"):
                    nxt = ln.strip()
                    break
            if nxt:
                break
        if nxt:
            rows.append(nxt)
        rows.append("Continue from there; do not redo finished stages.")
        return "\n".join(rows)

    def spawn(self) -> tuple[queue.Queue, bool]:
        env = dict(os.environ, ASD_RUN_KEY=self.key)
        acct = getattr(self, "account", None)
        if acct and os.path.abspath(acct["dir"]) != os.path.abspath(os.path.expanduser("~/.claude")):
            env["CLAUDE_CONFIG_DIR"] = acct["dir"]
        elif acct:
            env.pop("CLAUDE_CONFIG_DIR", None)
        self.proc = subprocess.Popen(
            self.claude_cmd(), cwd=self.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=open(self.err_path, "ab"), start_new_session=True)
        self.save(claude_pid=self.proc.pid)
        q: queue.Queue = queue.Queue()

        def pump(p=self.proc):
            for raw in p.stdout:
                try:
                    q.put(json.loads(raw))
                except ValueError:
                    continue
            q.put(None)

        threading.Thread(target=pump, daemon=True).start()
        self.send(f"/{PLUGIN}:{self.command} {self.dossier}" + (f" {self.items}" if self.items and self.command == "fix-deferred" else ""))
        extra = False
        hand = self.handoff()
        if hand:
            self.send(hand)
            extra = True
        return q, extra

    def send(self, text: str) -> None:
        try:
            self.proc.stdin.write(user_message(text))
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass  # the process died; read_turn reports it

    def end_process(self) -> None:
        p = self.proc
        if p is None:
            return
        try:
            p.stdin.close()
        except OSError:
            pass
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            kill_group(p)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                kill_group(p, signal.SIGKILL)
        kill_group(p)  # stragglers of the group
        self.save(claude_pid=None)
        self.proc = None

    # -- reading a turn ---------------------------------------------------

    def note_event(self, ev: dict) -> None:
        if ev.get("type") == "system" and ev.get("subtype") == "init" and ev.get("session_id"):
            self.save(session=ev["session_id"])
        if ev.get("type") != "assistant":
            return
        content = (ev.get("message") or {}).get("content")
        if not isinstance(content, list):
            return
        for b in content:
            if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                continue
            name = str(b.get("name", ""))
            if not (name == "phase_checkpoint" or name.endswith("__phase_checkpoint")):
                continue
            tid = str(b.get("id", ""))
            if tid and tid in self.seen_tool_ids:
                continue
            self.seen_tool_ids.add(tid)
            inp = b.get("input") or {}
            self.checkpoints.append({"name": str(inp.get("phase", "")), "summary": str(inp.get("summary", ""))})
            write_json(self.cp_path, self.checkpoints)

    def read_turn(self, q: queue.Queue, extra: bool, idle: float | None = None) -> tuple[str, object]:
        """('result', (text, is_error)) | ('exit', code) | ('stopped', None) | ('idle', None)
        when `idle` seconds pass with no event at all."""
        last_text = ""
        result: tuple[str, bool] | None = None
        quiet_since = time.monotonic()
        while True:
            timeout = self.grace if (result is not None and extra) else 1.0
            try:
                ev = q.get(timeout=timeout)
            except queue.Empty:
                if self.stopping:
                    return "stopped", None
                if result is not None:
                    return "result", result
                if idle is not None and time.monotonic() - quiet_since > idle:
                    return "idle", None
                continue
            quiet_since = time.monotonic()
            if ev is None:
                if result is not None:
                    return "result", result
                if self.stopping:
                    return "stopped", None
                rc = self.proc.wait() if self.proc else -1
                return "exit", rc
            self.note_event(ev)
            if ev.get("type") == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                        last_text = b["text"]
            elif ev.get("type") == "result":
                text = ev.get("result") if isinstance(ev.get("result"), str) and ev.get("result") else last_text
                err = bool(ev.get("is_error")) or str(ev.get("subtype", "success")) != "success"
                result = (text or "", err)
                if not extra:
                    return "result", result
                # a second message was sent with the command: it may start a turn of its own, so
                # wait out the grace for more events before taking this result as the turn's end

    # -- waiting on the owner ---------------------------------------------

    def ask_cmd(self, *args: str) -> subprocess.Popen:
        p = subprocess.Popen([sys.executable, ASK_OWNER, *args], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        self.children.append(p)
        return p

    def owner_wait(self, ids: list[str]) -> tuple[str, int, str]:
        """Blocks until the first of the questions has an owner line: (id, rc, stdout)."""
        procs = {i: self.ask_cmd("wait", i) for i in ids}
        try:
            while True:
                for i, p in procs.items():
                    if p.poll() is not None:
                        out, err = p.communicate()
                        return i, p.returncode, (out.strip() or err.strip())
                if self.stopping:
                    return "", -1, ""
                time.sleep(0.1)
        finally:
            for p in procs.values():
                if p.poll() is None:
                    p.kill()
                    p.wait()

    def ask_failure(self, reason: str) -> str:
        """Question the owner about a failed stage; 'retry' or 'stop'."""
        p = self.ask_cmd(
            "ask", "--root", self.root, "--dossier", self.dossier,
            "--question", clip(f"The run of {self.dossier} failed at stage {self.state['stage']}: {reason}", 1500),
            "--option", "A|Retry the stage|starts the stage again in a new process, with the same hand-off",
            "--option", "B|Stop|ends the run; the dossier keeps what the earlier stages committed",
            "--recommend", "A", "--blocks", f"the run of {self.dossier}")
        out, err = p.communicate()
        qid = out.strip()
        if p.returncode != 0 or not qid:
            self.event(f"could not ask the owner: {err.strip()[:200]}")
            return "stop"
        self.save(questions=[qid])
        self.event(f"failed, asked {qid}: {reason}", "failed")
        while True:
            _, rc, text = self.owner_wait([qid])
            if self.stopping:
                return "stop"
            if rc == 10:  # a follow-up the runner cannot answer
                subprocess.run([sys.executable, ASK_OWNER, "reply", qid, "--text",
                                "The runner cannot answer questions; choose A (retry) or B (stop)."],
                               capture_output=True)
                continue
            if rc != 0:
                self.event(f"waiting on {qid} failed: {text[:200]}")
                return "stop"
            self.save(questions=[])
            m = re.search(r"\[option ([A-Za-z])\]", text)
            choice = (m.group(1) if m else text.split(":", 1)[-1].strip()[:1]).upper()
            return "retry" if choice == "A" else "stop"

    # -- the run ----------------------------------------------------------

    def run_stage(self) -> tuple[str, str]:
        """('next'|'done'|'failed'|'stopped', detail)."""
        n0 = len(self.checkpoints)
        q, extra = self.spawn()
        idle = float(os.environ.get("ASD_RUNNER_IDLE") or 1200)
        quiet = False   # the last turn ended without a RUNNER: line
        nudges = 0
        while True:
            kind, val = self.read_turn(q, extra, idle if quiet else None)
            extra = False
            if kind == "idle":
                # A turn ended "waiting" on a background command or workflow, and nothing
                # re-invoked the session within the idle window: nudge it, twice at most.
                if nudges >= 2:
                    kill_group(self.proc)
                    self.end_process()
                    return "failed", "the turn ended with no RUNNER: line, and two nudges brought none"
                nudges += 1
                self.log(f"no RUNNER: line after {int(idle)} s of quiet; nudge {nudges}")
                self.send(NUDGE)
                continue
            if kind == "stopped":
                return "stopped", ""
            if kind == "exit":
                self.end_process()
                try:
                    with open(self.err_path, "rb") as fh:
                        fh.seek(0, 2); fh.seek(max(0, fh.tell() - 4096))
                        tail = fh.read().decode("utf-8", "replace")
                except OSError:
                    tail = ""
                if usage_limit(tail):
                    return "limit", tail.strip()
                return "failed", f"the claude process exited with code {val} before a result"
            text, err = val  # type: ignore[misc]
            if err and usage_limit(text):
                kill_group(self.proc)
                self.end_process()
                return "limit", text.strip()
            if err:
                kill_group(self.proc)
                self.end_process()
                return "failed", "the turn ended in an error" + (f": {text.strip().splitlines()[-1][:300]}" if text.strip() else "")
            mk = parse_marker(text)
            if mk is None:
                # Not a failure yet: a turn that ends while a background command or
                # workflow runs is re-invoked by Claude Code when that work finishes.
                if not quiet:
                    self.log("turn ended without a RUNNER: line; waiting for background work to re-invoke it")
                quiet = True
                continue
            quiet = False
            verb, rest = mk
            if verb == "STAGE-DONE":
                self.end_process()
                if len(self.checkpoints) == n0:
                    self.no_checkpoint(rest)
                return "next", rest
            if verb == "DONE":
                self.record_result(text)
                self.end_process()
                return "done", ""
            if verb == "FAILED":
                kill_group(self.proc)
                self.end_process()
                return "failed", rest or "no reason given"
            ids = [i for i in re.split(r"[,\s]+", rest) if i]
            qids = [i for i in ids if QUESTION_ID.search(i)]
            if not qids:
                # `RUNNER: WAITING gates,background,task` is a wait on background work,
                # not on the owner: keep the session open, as for a turn with no RUNNER: line.
                self.log(f"RUNNER: WAITING names no question ({rest[:80] or 'empty'}); waiting for background work to re-invoke it")
                quiet = True
                continue
            ids = qids
            self.save(state="waiting", questions=ids)
            self.event(f"waiting on {','.join(ids)}", "waiting")
            qid, rc, out = self.owner_wait(ids)
            if self.stopping:
                return "stopped", ""
            if rc not in (0, 10):
                kill_group(self.proc)
                self.end_process()
                return "failed", f"waiting on {qid or ids[0]} failed: {out[:200]}"
            self.save(state="running", questions=[i for i in ids if i != qid] if rc == 0 else ids)
            self.event(("answered " if rc == 0 else "follow-up on ") + qid, "running")
            self.send("OWNER " + out)

    def on_term(self, *_a) -> None:
        self.stopping = True
        kill_group(self.proc)
        for p in self.children:
            if p.poll() is None:
                p.kill()

    def run(self) -> int:
        prev = read_json(self.state_path, {})
        if prev.get("state") in ("running", "waiting") and alive(prev.get("pid")) and prev.get("pid") != os.getpid():
            say(f"{self.key} is already run by pid {prev['pid']}")
            return 2
        if prev.get("state") == "done":
            self.checkpoints = []
            write_json(self.cp_path, [])
        signal.signal(signal.SIGTERM, self.on_term)
        signal.signal(signal.SIGINT, self.on_term)
        self.save()
        self.event(f"start /{self.command} {self.dossier} in {self.root}", "running")
        started = 0
        while True:
            if self.stopping:
                return self.finish("stopped", "stopped", 3)
            if started >= self.max_stages:
                return self.finish("stopped", f"--max-stages {self.max_stages} reached", 4)
            started += 1
            self.save(stage=started, state="running", questions=[])
            self.event(f"stage {started} start", "running")
            outcome, detail = self.run_stage()
            if outcome == "next":
                self.event(f"stage {started} end" + (f": {detail}" if detail else ""), "running")
                continue
            if outcome == "done":
                self.event(f"stage {started} end", "running")
                self.deferred_report()
                self.product_summary()
                return self.finish("done", "done", 0)
            if outcome == "stopped":
                self.end_process()
                return self.finish("stopped", "stopped", 3)
            if outcome == "limit":
                # Out of tokens: pause until the limit resets, then run the same stage
                # again (its checkpoints are the hand-off; /work-on resumes from the dossier).
                until = limit_reset(detail)
                cur = self.account["name"]
                mark_limited(cur, until)
                nxt, usable = pick_account(prefer=cur)
                fmt = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                if nxt["name"] != cur and usable <= time.time():
                    self.account = nxt
                    self.save(account=nxt["name"])
                    self.event(f"switched to account {nxt['name']}: account {cur} hit its usage limit until {fmt(until)}; stage {started} again", "running")
                    started -= 1
                    continue
                self.account, until = nxt, max(usable, time.time() + 60)
                at = fmt(until)
                self.save(state="paused", resumes_at=at, account=nxt["name"])
                self.event(f"paused: usage limit reached on every account; resumes at {at} on account {nxt['name']}", "paused")
                while time.time() < until and not self.stopping:
                    time.sleep(min(30.0, max(0.5, until - time.time())))
                if self.stopping:
                    return self.finish("stopped", "stopped", 3)
                self.save(resumes_at=None)
                self.event(f"resumed after the usage limit; stage {started} again", "running")
                started -= 1
                continue
            self.save(state="failed")
            self.event(f"stage {started} failed: {detail}", "failed")
            if self.ask_failure(detail) == "retry":
                self.event(f"retrying stage {started}", "running")
                continue
            if self.stopping:
                return self.finish("stopped", "stopped", 3)
            return self.finish("failed", f"failed: {detail}", 1)

    def finish(self, state: str, text: str, rc: int) -> int:
        self.end_process()
        self.save(state=state, questions=[])
        self.event(text, state)
        return rc


# ---------------------------------------------------------------- commands


def cmd_status(a: argparse.Namespace) -> int:
    d = runs_dir()
    want = key_of(a.root, a.dossier) if a.root and a.dossier else None
    rows = []
    for f in sorted(os.listdir(d)):
        if not f.endswith(".runner.json"):
            continue
        key = f[: -len(".runner.json")]
        if want and key != want:
            continue
        st = read_json(os.path.join(d, f), {})
        state = st.get("state", "?")
        if state in ("running", "waiting") and not alive(st.get("pid")):
            state = "stopped"
        q = ",".join(st.get("questions") or [])
        rows.append(f"{key}\t{state}\tstage {st.get('stage', 0)}\tsession {st.get('session') or '-'}"
                    f"\tpid {st.get('pid')}" + (f"\tquestions {q}" if q else ""))
    print("\n".join(rows) if rows else "no runs")
    return 0


def cmd_stop(a: argparse.Namespace) -> int:
    key = key_of(a.root, a.dossier)
    path = os.path.join(runs_dir(), key + ".runner.json")
    st = read_json(path, None)
    if not st:
        print(f"no run for {key}")
        return 1
    pid, cpid = st.get("pid"), st.get("claude_pid")
    if st.get("state") in ("running", "waiting") or alive(cpid):
        if alive(pid):
            os.kill(pid, signal.SIGTERM)  # the runner's handler kills the claude group and records stopped
            end = time.monotonic() + 20
            while alive(pid) and time.monotonic() < end:
                time.sleep(0.1)
        if alive(cpid):  # no runner left to do it
            try:
                os.killpg(cpid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        st = read_json(path, st)
        if st.get("state") in ("running", "waiting"):
            st.update(state="stopped", claude_pid=None, questions=[])
            write_json(path, st)
            try:
                append_line(os.path.join(runs_dir(), key + ".jsonl"),
                            {"ts": now(), "kind": "runner", "text": "stopped", "state": "stopped",
                             "stage": str(st.get("stage", 0))})
            except OSError:
                pass
    print(f"stopped {key}")
    return 0


CHAT_RULES = """\
The run is finished and the owner is talking to you from the dashboard; nobody reads the terminal. Answer their message
in plain sentences, for a person reading on a phone; markdown bullets and small tables are welcome. Answer questions
from what you know and what you can read. Change code, branches, pull requests or the dossier only when the message
asks you to, and say exactly what you changed. Do not end with a RUNNER: line.
"""


def chat_session(key: str) -> tuple[str, str]:
    """(session id, account) the owner's chat resumes: the chat's own session once it has one, else the session that
    wrote the run's last reply (`result_session`, the last `result` line, or the last `stage N end` line)."""
    d = runs_dir()
    ch = read_json(os.path.join(d, key + ".chat.json"), {}) or {}
    st = read_json(os.path.join(d, key + ".runner.json"), {}) or {}
    acct = ch.get("account") or st.get("account") or ""
    if ch.get("session"):
        return ch["session"], acct
    if st.get("result_session"):
        return st["result_session"], acct
    sid = ""
    try:
        with open(os.path.join(d, key + ".jsonl")) as fh:
            for raw in fh:
                try:
                    o = json.loads(raw)
                except ValueError:
                    continue
                if o.get("kind") == "result" and o.get("session"):
                    sid = o["session"]
                elif o.get("kind") == "runner" and re.match(r"stage \S+ end\b", str(o.get("text", ""))) and o.get("session"):
                    sid = o["session"]
    except OSError:
        pass
    return sid, acct


def chat_line(key: str, who: str, text: str, **kw) -> None:
    append_line(os.path.join(runs_dir(), key + ".jsonl"), {"ts": now(), "kind": "chat", "from": who, "text": text[:20000], **kw})


def cmd_chat(a: argparse.Namespace) -> int:
    """`chat ID --text T`: the owner's message to the finished run's session. The owner line is written at once; the
    reply (or why there is none) follows as a `chat` line from a background process, `<key>.chat.log` its output."""
    key = key_of(a.root, a.dossier)
    d = runs_dir()
    text = (a.text or "").strip()
    if not text:
        print("run_dossier.py chat: --text is empty", file=sys.stderr)
        return 2
    st = read_json(os.path.join(d, key + ".runner.json"), {}) or {}
    if st.get("state") in ("running", "waiting", "paused") and alive(st.get("pid")):
        print(f"{key} is still running; answer its questions instead", file=sys.stderr)
        return 2
    cp = os.path.join(d, key + ".chat.json")
    ch = read_json(cp, {}) or {}
    if ch.get("state") == "thinking" and alive(ch.get("pid")) and ch.get("pid") != os.getpid():
        print(f"{key}: the agent is still answering the last message", file=sys.stderr)
        return 2
    sid, acct = chat_session(key)
    if not sid:
        print(f"{key}: no session to talk to (the run recorded none)", file=sys.stderr)
        return 1
    if not a.foreground:
        chat_line(key, "owner", text)
        write_json(cp, {**ch, "state": "thinking", "pid": None, "session": ch.get("session") or "", "account": acct, "since": now()})
        log = os.path.join(d, key + ".chat.log")
        args = [sys.executable, os.path.abspath(__file__), "chat", a.dossier, "--root", a.root, "--text", text, "--foreground"]
        with open(log, "ab") as fh:
            p = subprocess.Popen(args, cwd=a.root, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
        write_json(cp, {**read_json(cp, {}), "pid": p.pid})
        say(f"sent to {key}; the reply follows on the dashboard")
        return 0
    write_json(cp, {**read_json(cp, {}), "state": "thinking", "pid": os.getpid(), "account": acct})
    root = st.get("root") or a.root
    cmd = shlex.split(os.environ.get("ASD_CLAUDE_BIN") or "claude")
    cmd += ["-p", "--resume", sid, "--output-format", "json", "--permission-mode", st.get("permission_mode") or "auto",
            "--append-system-prompt", CHAT_RULES]
    env = dict(os.environ, ASD_RUN_UPDATES="0")
    env.pop("ASD_RUN_KEY", None)
    acc = next((x for x in load_accounts() if x.get("name") == acct), None)
    if acc and os.path.abspath(acc["dir"]) != os.path.abspath(os.path.expanduser("~/.claude")):
        env["CLAUDE_CONFIG_DIR"] = acc["dir"]
    elif acc:
        env.pop("CLAUDE_CONFIG_DIR", None)
    state, new_sid = "idle", sid
    try:
        out = subprocess.run(cmd, cwd=root, env=env, input=text, capture_output=True, text=True,
                             timeout=float(os.environ.get("ASD_CHAT_TIMEOUT") or 1800))
        try:
            j = json.loads(out.stdout.strip().splitlines()[-1]) if out.stdout.strip() else {}
        except ValueError:
            j = {}
        reply = str(j.get("result") or "").strip()
        new_sid = str(j.get("session_id") or sid)
        if out.returncode != 0 or j.get("is_error") or not reply:
            why = reply or (out.stderr or "").strip().splitlines()[-1:] or [f"exit {out.returncode}"]
            why = why if isinstance(why, str) else why[0]
            state = "failed"
            chat_line(key, "runner", ("The agent hit its usage limit; try again later. " if usage_limit(why) else "The agent could not answer: ") + why[:500])
        else:
            chat_line(key, "agent", reply, session=new_sid)
    except subprocess.TimeoutExpired:
        state = "failed"
        chat_line(key, "runner", "The agent did not answer within the time limit.")
    except OSError as e:
        state = "failed"
        chat_line(key, "runner", f"The agent could not be started: {e}")
    write_json(cp, {**read_json(cp, {}), "state": state, "pid": None, "session": new_sid, "account": acct})
    return 0 if state == "idle" else 1


def cmd_summary(a: argparse.Namespace) -> int:
    """`summary ID`: write the product summary of a finished dossier now (for builds that ended before the runner
    wrote one, or to redo it). The run's own record stays as it is."""
    key = key_of(a.root, a.dossier)
    path = os.path.join(runs_dir(), key + ".runner.json")
    prev = read_json(path, {}) or {}
    if prev.get("state") in ("running", "waiting", "paused") and alive(prev.get("pid")):
        print(f"{key} is still running; the summary follows when it is done", file=sys.stderr)
        return 2
    r = Runner(prev.get("root") or a.root, a.dossier, "work-on", None, prev.get("permission_mode") or "auto", 1)
    r.state = dict(prev) if prev else r.state
    r.state_path = path if prev else os.devnull
    ok = r.product_summary(live=False)
    if prev:
        write_json(path, {**read_json(path, prev), "claude_pid": None})
    say(f"{key}: product summary " + ("written" if ok else "not produced"))
    return 0 if ok else 1


# ---------------------------------------------------------------- selftest

FAKE_CLAUDE = r'''
import json, os, subprocess, sys
d = os.environ["ASD_FAKE_DIR"]
cnt = os.path.join(d, "count")
n = int(open(cnt).read()) + 1 if os.path.exists(cnt) else 1
open(cnt, "w").write(str(n))
script = json.load(open(os.path.join(d, "script.json")))[n - 1]
inlog = open(os.path.join(d, f"in.{n}.jsonl"), "a")
open(os.path.join(d, f"pid.{n}"), "w").write(str(os.getpid()))
open(os.path.join(d, f"env.{n}"), "w").write(os.environ.get("ASD_RUN_KEY", "") + "\n" + " ".join(sys.argv[1:]))
ask = os.path.join(os.environ["ASD_ASK_OWNER"])
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
out({"type": "system", "subtype": "init", "session_id": f"sess-{n}"})
if script.get("crash") is not None:
    sys.exit(script["crash"])
turn = 0
for raw in sys.stdin:
    msg = json.loads(raw)
    text = msg["message"]["content"][0]["text"]
    inlog.write(raw); inlog.flush()
    if text.startswith("RUNNER HAND-OFF"):
        continue
    if script.get("hang"):
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t0", "name": "mcp__x__phase_checkpoint", "input": {"phase": "P-hang", "summary": "s"}}]}})
        import time; time.sleep(600)
    acts = script["turns"][turn]; turn += 1
    final = ""
    for a in acts:
        if "checkpoint" in a:
            out({"type": "assistant", "message": {"content": [{"type": "text", "text": "SECRET-MODEL-TEXT"}, {"type": "tool_use", "id": f"t{n}{turn}", "name": "mcp__plugin_agentic-software-development_nav__phase_checkpoint", "input": {"phase": a["checkpoint"][0], "summary": a["checkpoint"][1]}}]}})
        elif "ask" in a:
            r = subprocess.run([sys.executable, ask, "ask", "--root", os.getcwd(), "--dossier", a["dossier"], "--question", "Which?", "--option", "A|one|c1", "--option", "B|two|c2", "--blocks", "build"], capture_output=True, text=True)
            final = a["final"].replace("{id}", r.stdout.strip())
        elif "reply" in a:
            subprocess.run([sys.executable, ask, "reply", a["reply"], "--text", "because"], capture_output=True)
            final = a["final"]
        elif "final" in a:
            final = a["final"]
    out({"type": "result", "subtype": "success", "is_error": False, "session_id": f"sess-{n}", "result": "SECRET-MODEL-TEXT\n" + final})
    if script.get("exit_after_turn"):
        break
'''


def selftest() -> int:
    me = os.path.abspath(__file__)
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        print(("ok   " if ok else "FAIL ") + name, flush=True)
        if not ok:
            failures.append(name)

    def lines_of(path: str) -> list[dict]:
        try:
            return [json.loads(x) for x in open(path).read().splitlines() if x.strip()]
        except OSError:
            return []

    def until(fn, secs=30.0):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            v = fn()
            if v:
                return v
            time.sleep(0.05)
        return None

    with tempfile.TemporaryDirectory() as tmp:
        qdir, rdir, fdir = (os.path.join(tmp, x) for x in ("q", "runs", "fake"))
        root = os.path.join(tmp, "repo")
        for d in (qdir, rdir, fdir, root):
            os.makedirs(d)
        fake = os.path.join(fdir, "fake_claude.py")
        open(fake, "w").write(FAKE_CLAUDE)
        env = dict(os.environ, ASD_QUESTIONS_DIR=qdir, ASD_RUNS_DIR=rdir, ASD_FAKE_DIR=fdir, ASD_RUNNER_DEFERRED="0", ASD_RUNNER_SUMMARY="0", ASD_RUNNER_IDLE="1",
                   ASD_CLAUDE_BIN=f"{shlex.quote(sys.executable)} {shlex.quote(fake)}",
                   ASD_ASK_OWNER=ASK_OWNER, ASD_OWNER_POLL="0.1", ASD_RUNNER_GRACE="0.2")

        def scenario(procs: list[dict]) -> None:
            for f in os.listdir(fdir):
                if f == "count" or f.startswith(("in.", "pid.", "env.")):
                    os.remove(os.path.join(fdir, f))
            json.dump(procs, open(os.path.join(fdir, "script.json"), "w"))

        def start(dossier: str, *extra: str) -> subprocess.Popen:
            out = open(os.path.join(tmp, f"out.{dossier}.txt"), "w")
            return subprocess.Popen([sys.executable, me, "--root", root, "--dossier", dossier, "--foreground", *extra],
                                    env=env, stdout=out, stderr=subprocess.STDOUT, text=True)

        def owner_line(qid: str, kind: str, text: str, option: str = "") -> None:
            append_line(os.path.join(qdir, qid + ".jsonl"),
                        {"ts": now(), "from": "owner", "kind": kind, "text": text, "option": option, "via": "cli"})

        def qfile(qid: str) -> str:
            return os.path.join(qdir, qid + ".jsonl")

        def state_of(dossier: str) -> dict:
            return read_json(os.path.join(rdir, f"repo.{dossier}.runner.json"), {})

        def texts(dossier: str, kind: str = "runner") -> list[str]:
            return [x["text"] for x in lines_of(os.path.join(rdir, f"repo.{dossier}.jsonl")) if x.get("kind") == kind]

        def msgs(n: int) -> list[str]:
            return [json.loads(x)["message"]["content"][0]["text"] for x in open(os.path.join(fdir, f"in.{n}.jsonl")).read().splitlines()]

        # ---- 1. stages, hand-off, WAITING, FOLLOWUP, ANSWER, DONE ---------------
        q1 = "repo.W-1.Q1"
        scenario([
            {"turns": [[{"checkpoint": ["Phase 2", "contract committed\nNEXT: Phase 4 wave 1"]},
                        {"final": "RUNNER: STAGE-DONE Phase 2"}]]},
            {"turns": [[{"ask": 1, "dossier": "W-1", "final": "RUNNER: WAITING {id}"}],
                       [{"reply": q1, "final": "RUNNER: WAITING " + q1}],
                       [{"checkpoint": ["Phase 4", "wave 1 merged\nNEXT: Phase 6"]},
                        {"final": "RUNNER: STAGE-DONE Phase 4"}]]},
            {"turns": [[{"final": "all green\nRUNNER: DONE"}]]},
        ])
        p = start("W-1")
        check("waiting: the question exists and the runner is waiting",
              bool(until(lambda: os.path.exists(qfile(q1)) and state_of("W-1").get("state") == "waiting")))
        check("waiting: runner.json lists the question", state_of("W-1").get("questions") == [q1])
        owner_line(q1, "followup", "why?")
        check("followup: the agent replied in the same process (reply line appears)",
              bool(until(lambda: any(x.get("kind") == "reply" for x in lines_of(qfile(q1))))))
        until(lambda: any(l.startswith("OWNER FOLLOWUP") for l in (msgs(2) if os.path.exists(os.path.join(fdir, "in.2.jsonl")) else [])))
        owner_line(q1, "answer", "go with A", "A")
        rc = p.wait(timeout=60)
        check("run exits 0 on DONE", rc == 0)
        check("three processes, one per stage", open(os.path.join(fdir, "count")).read() == "3")
        m2 = msgs(2)
        check("stage 2 first message is the command", m2[0] == f"/{PLUGIN}:work-on W-1")
        check("stage 2 hand-off carries the earlier checkpoint and the NEXT line",
              m2[1].startswith("RUNNER HAND-OFF") and "1. Phase 2: contract committed" in m2[1] and "NEXT: Phase 4 wave 1" in m2[1])
        check("followup then answer went into the same process",
              m2[2] == f"OWNER FOLLOWUP {q1}: why?" and m2[3] == f"OWNER ANSWER {q1} [option A]: go with A")
        m3 = msgs(3)
        check("stage 3 hand-off lists both checkpoints in order, ends with the last NEXT",
              "1. Phase 2:" in m3[1] and "2. Phase 4: wave 1 merged" in m3[1] and m3[1].rstrip().splitlines()[-2] == "NEXT: Phase 6")
        check("stage 1 has no hand-off", len(msgs(1)) == 1)
        envs = open(os.path.join(fdir, "env.1")).read().split("\n", 1)
        check("process env ASD_RUN_KEY and flags",
              envs[0] == "repo.W-1" and "--input-format stream-json" in envs[1] and "--output-format stream-json" in envs[1]
              and "--verbose" in envs[1] and "--permission-mode auto" in envs[1] and "RUNNER: DONE" in envs[1])
        st = state_of("W-1")
        check("runner.json done", st.get("state") == "done" and st.get("stage") == 3 and st.get("session") == "sess-3"
              and st.get("pid") and st.get("started") and st.get("questions") == [])
        check("checkpoints.json", [c["name"] for c in read_json(os.path.join(rdir, "repo.W-1.checkpoints.json"), [])] == ["Phase 2", "Phase 4"])
        os.environ["ASD_RUNS_DIR"] = rdir
        rn = Runner(root, "W-77", "work-on", None, "auto", 5)
        rn.no_checkpoint("Phase 3 — contract")
        fakedef = os.path.join(tmp, "fake-deferred.py")
        with open(fakedef, "w") as fh:
            fh.write("import json,sys\nfirst=sys.stdin.readline()\nopen(sys.argv[0]+'.first','w').write(first)\n"
                     "print(json.dumps({'type':'result','subtype':'success','is_error':False,'session_id':'d',"
                     "'result':'## Deferred issues — W-77\\n### D-1 — fix a\\n### D-2 — fix b\\nRUNNER: DONE'}),flush=True)\n")
        old_bin = os.environ.get("ASD_CLAUDE_BIN")
        os.environ["ASD_CLAUDE_BIN"] = f"{sys.executable} {fakedef}"
        try:
            rn.deferred_report()
        finally:
            if old_bin is None: os.environ.pop("ASD_CLAUDE_BIN", None)
            else: os.environ["ASD_CLAUDE_BIN"] = old_bin
        rep_path = os.path.join(rdir, "repo.W-77.deferred.md")
        rep_txt = open(rep_path).read() if os.path.exists(rep_path) else ""
        check("after DONE the /deferred report is stored, without the RUNNER line",
              "### D-2" in rep_txt and "RUNNER:" not in rep_txt and ":deferred W-77" in open(fakedef + ".first").read())
        fakesum = os.path.join(tmp, "fake-summary.py")
        with open(fakesum, "w") as fh:
            fh.write("import json,sys\nfirst=sys.stdin.readline()\nopen(sys.argv[0]+'.first','w').write(first+' '.join(sys.argv[1:]))\n"
                     "print(json.dumps({'type':'result','subtype':'success','is_error':False,'session_id':'s',"
                     "'result':'**In one line:** grants now explain themselves.\\n### Under the hood\\n- typed detail\\nRUNNER: DONE'}),flush=True)\n")
        os.environ["ASD_CLAUDE_BIN"] = f"{sys.executable} {fakesum}"
        os.environ.pop("ASD_RUNNER_SUMMARY_MODEL", None)
        try:
            ok_sum = rn.product_summary()
        finally:
            if old_bin is None: os.environ.pop("ASD_CLAUDE_BIN", None)
            else: os.environ["ASD_CLAUDE_BIN"] = old_bin
        sum_path = os.path.join(rdir, "repo.W-77.summary.md")
        sum_txt = open(sum_path).read() if os.path.exists(sum_path) else ""
        first_sum = open(fakesum + ".first").read() if os.path.exists(fakesum + ".first") else ""
        check("after DONE the product summary is stored without the RUNNER line, on sonnet, and the run's model is restored",
              ok_sum and sum_txt.startswith("**In one line:**") and "RUNNER:" not in sum_txt and ":product-summary W-77" in first_sum
              and "--model sonnet" in first_sum and rn.model is None and rn.command == "work-on")
        cps = read_json(os.path.join(rdir, "repo.W-77.checkpoints.json"), [])
        stl = [x for x in lines_of(os.path.join(rdir, "repo.W-77.jsonl")) if x.get("kind") == "stage"]
        check("usage limits are recognised, real errors are not",
              usage_limit("Claude AI usage limit reached|1793962800") and usage_limit("You've hit your weekly limit · resets Nov 6")
              and usage_limit("API Error: 429 rate_limit_error") and not usage_limit("TypeError: x is undefined"))
        check("the reset time comes from the CLI's epoch or an ISO time, else 30 minutes",
              limit_reset("limit reached|2000000000", 1_000_000_000) == 2000000060
              and limit_reset("resets 2026-11-06T11:00:00+00:00", 1_000_000_000) == datetime.datetime(2026, 11, 6, 11, tzinfo=datetime.timezone.utc).timestamp() + 60
              and limit_reset("weekly limit", 1_000_000_000) == 1_000_000_000 + 1800)
        os.environ["ASD_ACCOUNTS"] = os.path.join(tmp, "accounts.json")
        os.environ["ASD_ACCOUNT_STATE"] = os.path.join(tmp, "accounts-state.json")
        write_json(os.environ["ASD_ACCOUNTS"], {"accounts": [{"name": "a", "dir": os.path.join(tmp, "ca")}, {"name": "b", "dir": os.path.join(tmp, "cb")}]})
        t0 = 1_000_000_000
        check("accounts: the preferred free account is used", pick_account(t0, "b")[0]["name"] == "b" and pick_account(t0)[0]["name"] == "a")
        mark_limited("a", t0 + 3600)
        check("accounts: a limited account hands over to the free one", pick_account(t0, "a") == (load_accounts()[1], t0))
        mark_limited("b", t0 + 600)
        check("accounts: all limited -> the one that resets first, with its time", pick_account(t0, "a")[0]["name"] == "b" and pick_account(t0, "a")[1] == t0 + 600)
        for k in ("ASD_ACCOUNTS", "ASD_ACCOUNT_STATE"):
            os.environ.pop(k, None)
        br = datetime.datetime(2026, 11, 3, 10, 0, tzinfo=datetime.timezone.utc).timestamp()
        check("the weekly reset text is read: 'resets Nov 6 at 12pm (Europe/Brussels)'",
              human_reset("You've hit your weekly limit · resets Nov 6 at 12pm (Europe/Brussels)", br) == datetime.datetime(2026, 11, 6, 11, 0, tzinfo=datetime.timezone.utc).timestamp())
        check("the session reset text is read: 'resets 3:30pm (UTC)', next occurrence",
              human_reset("limit reached · resets 3:30pm (UTC)", br) == datetime.datetime(2026, 11, 3, 15, 30, tzinfo=datetime.timezone.utc).timestamp())
        check("a stage without a checkpoint still gets a stage line and a dossier hand-off",
              cps and cps[-1]["name"] == "Phase 3 — contract" and "Build log" in cps[-1]["summary"]
              and stl and stl[-1]["stage"] == "Phase 3 — contract")
        rt = texts("W-1")
        check("runner lines: start, stages, waiting, answered, done",
              rt[0].startswith("start ") and "stage 1 start" in rt and "stage 2 end: Phase 4" in rt
              and f"waiting on {q1}" in rt and f"follow-up on {q1}" in rt and f"answered {q1}" in rt and rt[-1] == "done")
        check("runner lines carry state and session",
              all("state" in x for x in lines_of(os.path.join(rdir, "repo.W-1.jsonl")) if x.get("kind") == "runner") and
              any(x.get("session") == "sess-2" for x in lines_of(os.path.join(rdir, "repo.W-1.jsonl"))))
        check("done: the last reply is a result line without its RUNNER: line, and its session is the chat's",
              len(texts("W-1", "result")) == 1 and texts("W-1", "result")[0].endswith("all green") and "RUNNER:" not in texts("W-1", "result")[0] and state_of("W-1").get("result_session") == "sess-3")
        # ---- 1b. chat with the finished run's session --------------------------------
        cfake = os.path.join(fdir, "chat_claude.py")
        open(cfake, "w").write("import json,sys\na=sys.argv[1:]\nmsg=sys.stdin.read()\n"
                               "print(json.dumps({'result':'re: '+msg+' @'+a[a.index('--resume')+1],'session_id':'chat-1','is_error':False}))\n")
        cenv = dict(env, ASD_CLAUDE_BIN=f"{shlex.quote(sys.executable)} {shlex.quote(cfake)}")
        cr = subprocess.run([sys.executable, me, "chat", "W-1", "--root", root, "--text", "why A?"], env=cenv, capture_output=True, text=True)
        check("chat: the owner line is written at once and the reply resumes the result session",
              cr.returncode == 0 and bool(until(lambda: any(x.get("from") == "agent" for x in lines_of(os.path.join(rdir, "repo.W-1.jsonl")) if x.get("kind") == "chat")))
              and [x["text"] for x in lines_of(os.path.join(rdir, "repo.W-1.jsonl")) if x.get("kind") == "chat"] == ["why A?", "re: why A? @sess-3"])
        until(lambda: read_json(os.path.join(rdir, "repo.W-1.chat.json"), {}).get("state") == "idle")
        subprocess.run([sys.executable, me, "chat", "W-1", "--root", root, "--text", "and B?", "--foreground"], env=cenv, capture_output=True, text=True)
        check("chat: the next message resumes the chat's own session",
              [x["text"] for x in lines_of(os.path.join(rdir, "repo.W-1.jsonl")) if x.get("kind") == "chat"][-1] == "re: and B? @chat-1")
        bad = subprocess.run([sys.executable, me, "--root", root, "--dossier", "W-1", "--items", "D-1"], env=env, capture_output=True, text=True)
        check("--items only with fix-deferred", bad.returncode == 2)
        out1 = open(os.path.join(tmp, "out.W-1.txt")).read()
        check("stdout never carries model text", "SECRET-MODEL-TEXT" not in out1 and "[run_dossier] repo.W-1 stage 1 start" in out1)

        # ---- 2. FAILED, then no RUNNER line: question each time ----------------
        scenario([
            {"turns": [[{"final": "RUNNER: FAILED tests red"}]]},
            {"turns": [[{"final": "I am done thinking."}]]},
        ])
        p = start("W-2")
        qa, qb = "repo.W-2.Q1", "repo.W-2.Q2"
        check("FAILED asks the owner", bool(until(lambda: os.path.exists(qfile(qa)))))
        check("failed state with the question while it waits", bool(until(lambda: state_of("W-2").get("state") == "failed" and state_of("W-2").get("questions") == [qa])))
        first = lines_of(qfile(qa))[0]
        check("owner question has retry/stop options", [o["key"] for o in first["options"]] == ["A", "B"] and "tests red" in first["text"])
        owner_line(qa, "followup", "what?")
        check("a follow-up on it is answered by the runner",
              bool(until(lambda: any(x.get("kind") == "reply" and "cannot answer" in x["text"] for x in lines_of(qfile(qa))))))
        owner_line(qa, "answer", "retry", "A")
        check("retry starts a new process", bool(until(lambda: os.path.exists(os.path.join(fdir, "pid.2")))))
        check("no RUNNER line asks again", bool(until(lambda: os.path.exists(qfile(qb)))))
        owner_line(qb, "answer", "stop", "B")
        check("stop answer ends failed, exit 1", p.wait(timeout=60) == 1 and state_of("W-2").get("state") == "failed")
        check("failure reasons recorded",
              any("tests red" in t for t in texts("W-2")) and any("no RUNNER: line" in t for t in texts("W-2")))
        check("failed process is gone", not alive(int(open(os.path.join(fdir, "pid.2")).read())))

        # ---- 2b. WAITING on background work (no question id) is not a failure --
        scenario([{"turns": [[{"final": "gates running\nRUNNER: WAITING gates,background,task,b1yrknlzz"}],
                             [{"final": "gates green\nRUNNER: DONE"}]]}])
        p = start("W-20")
        check("WAITING without a question id keeps the session and ends DONE", p.wait(timeout=60) == 0 and state_of("W-20").get("state") == "done")
        check("no owner question for a background wait", not os.path.exists(qfile("repo.W-20.Q1")))

        # ---- 3. a crashed process counts as FAILED -----------------------------
        scenario([{"crash": 3}])
        p = start("W-3")
        qc = "repo.W-3.Q1"
        check("crash asks the owner", bool(until(lambda: os.path.exists(qfile(qc)))))
        owner_line(qc, "answer", "B", "B")
        check("crash ends failed, exit 1", p.wait(timeout=60) == 1 and any("exited with code 3" in t for t in texts("W-3")))

        # ---- 4. --max-stages ----------------------------------------------------
        scenario([{"turns": [[{"final": "RUNNER: STAGE-DONE one"}]]}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-4", "--max-stages", "1")
        check("max-stages: exit 4, state stopped, one process",
              p.wait(timeout=60) == 4 and state_of("W-4").get("state") == "stopped"
              and open(os.path.join(fdir, "count")).read() == "1" and any("--max-stages 1" in t for t in texts("W-4")))

        # ---- 5. status and stop -------------------------------------------------
        scenario([{"hang": True}])
        p = start("W-5")
        check("stop target: the process is up", bool(until(lambda: os.path.exists(os.path.join(fdir, "pid.1")) and state_of("W-5").get("claude_pid"))))
        check("hook-free checkpoint tracked from the stream", bool(until(lambda: [c["name"] for c in read_json(os.path.join(rdir, "repo.W-5.checkpoints.json"), [])] == ["P-hang"])))
        s = subprocess.run([sys.executable, me, "status", "--root", root, "--dossier", "W-5"], env=env, capture_output=True, text=True)
        check("status shows running", "repo.W-5\trunning" in s.stdout)
        cpid = int(open(os.path.join(fdir, "pid.1")).read())
        r = subprocess.run([sys.executable, me, "stop", "--root", root, "--dossier", "W-5"], env=env, capture_output=True, text=True, timeout=60)
        check("stop: runner exits 3, state stopped", r.returncode == 0 and p.wait(timeout=30) == 3 and state_of("W-5").get("state") == "stopped")
        check("stop: the claude process is gone", until(lambda: not alive(cpid), 10) is True)
        check("stop recorded as a runner line", texts("W-5")[-1] == "stopped")
        write_json(os.path.join(rdir, "repo.W-6.runner.json"), {"pid": 2 ** 22 + 12345, "state": "running", "stage": 2, "session": "", "questions": []})
        d = subprocess.run([sys.executable, me, "W-9", "--root", root], env=env, capture_output=True, text=True, timeout=30)
        check("the default detaches and prints the pid", d.returncode == 0 and "in the background, pid" in d.stdout)
        check("the detached child logs to <key>.runner.log", bool(until(lambda: os.path.exists(os.path.join(rdir, "repo.W-9.runner.log")))))
        subprocess.run([sys.executable, me, "stop", "W-9", "--root", root], env=env, capture_output=True, text=True, timeout=60)
        s = subprocess.run([sys.executable, me, "status"], env=env, capture_output=True, text=True)
        check("status: a dead pid reads stopped", "repo.W-6\tstopped" in s.stdout and "repo.W-1\tdone" in s.stdout)
        write_json(os.path.join(rdir, "repo.W-7.runner.json"), {"pid": os.getpid(), "state": "running"})
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-7")
        check("a live owner of the dossier refuses a second runner", p.wait(timeout=30) == 2)

    print("FAILED: " + ", ".join(failures) if failures else "all ok")
    return 1 if failures else 0


def repo_of(cwd: str) -> str | None:
    """The repository root of `cwd` (its git toplevel), or None outside a repository."""
    try:
        out = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def detach(argv: list[str], root: str, dossier: str) -> int:
    """Start this runner again as a background process, its output in `<key>.runner.log`; print the pid."""
    log = os.path.join(runs_dir(), key_of(root, dossier) + ".runner.log")
    os.makedirs(runs_dir(), exist_ok=True)
    args = [sys.executable, os.path.abspath(__file__)] + [a for a in argv if a != "--detach"] + ["--foreground"]
    with open(log, "ab") as fh:
        p = subprocess.Popen(args, cwd=root, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                             start_new_session=True)
    say(f"started {key_of(root, dossier)} in the background, pid {p.pid}; log {log}")
    return 0


def resolve(a: argparse.Namespace, ap: argparse.ArgumentParser) -> bool:
    """Fill `a.dossier` from the positional id and `a.root` from the current repository."""
    a.dossier = a.dossier or a.id
    if a.dossier:
        check_ident(a.dossier, "dossier id")
    if not a.root and a.dossier:
        a.root = repo_of(os.getcwd())
        if not a.root:
            print("run_dossier.py: not inside a git repository; pass --root", file=sys.stderr)
            return False
    if a.root:
        a.root = os.path.abspath(a.root)
    return True


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "chat":
        ap = argparse.ArgumentParser(prog="run_dossier.py chat")
        ap.add_argument("id", nargs="?", help="dossier id, e.g. W-123")
        ap.add_argument("--root", help="repository root (default: the repository of the current directory)")
        ap.add_argument("--dossier")
        ap.add_argument("--text", required=True)
        ap.add_argument("--foreground", action="store_true")
        a = ap.parse_args(argv[1:])
        if not resolve(a, ap) or not (a.root and a.dossier):
            ap.print_usage(sys.stderr)
            return 2
        return cmd_chat(a)
    if argv and argv[0] == "summary":
        ap = argparse.ArgumentParser(prog="run_dossier.py summary")
        ap.add_argument("id", nargs="?", help="dossier id, e.g. W-123")
        ap.add_argument("--root", help="repository root (default: the repository of the current directory)")
        ap.add_argument("--dossier")
        a = ap.parse_args(argv[1:])
        if not resolve(a, ap) or not (a.root and a.dossier):
            ap.print_usage(sys.stderr)
            return 2
        return cmd_summary(a)
    if argv and argv[0] in ("status", "stop"):
        ap = argparse.ArgumentParser(prog=f"run_dossier.py {argv[0]}")
        need = argv[0] == "stop"
        ap.add_argument("id", nargs="?", help="dossier id, e.g. W-123")
        ap.add_argument("--root", help="repository root (default: the repository of the current directory)")
        ap.add_argument("--dossier")
        a = ap.parse_args(argv[1:])
        if not resolve(a, ap):
            return 2
        if need and not (a.root and a.dossier):
            ap.print_usage(sys.stderr)
            return 2
        return {"status": cmd_status, "stop": cmd_stop}[argv[0]](a)
    ap = argparse.ArgumentParser(description="Run one dossier through /work-on, one headless process per stage.")
    ap.add_argument("id", nargs="?", help="dossier id, e.g. W-123")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root", help="repository root (default: the repository of the current directory)")
    ap.add_argument("--dossier", help="same as the positional id")
    ap.add_argument("--command", choices=["work-on", "stub", "fix-deferred"], default="work-on")
    ap.add_argument("--model")
    ap.add_argument("--permission-mode", default="auto")
    ap.add_argument("--max-stages", type=int, default=20)
    ap.add_argument("--items", default="", help="fix-deferred only: the D-ids to fix, e.g. D-1,D-3 (default: every open item)")
    ap.add_argument("--detach", action="store_true", help="the default: run in the background; output to <key>.runner.log")
    ap.add_argument("--foreground", action="store_true", help="run in this terminal instead of the background")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not resolve(a, ap) or not a.root or not a.dossier:
        ap.print_usage(sys.stderr)
        return 2
    items = ",".join(i for i in re.split(r"[,\s]+", a.items or "") if i)
    if items and (a.command != "fix-deferred" or not re.fullmatch(r"D-\d+(?:,D-\d+)*", items)):
        print("run_dossier.py: --items takes D-ids (D-1,D-3) and only with --command fix-deferred", file=sys.stderr)
        return 2
    if not a.foreground:
        return detach(argv + ([] if "--root" in argv else ["--root", a.root]), a.root, a.dossier)
    return Runner(a.root, a.dossier, a.command, a.model, a.permission_mode, a.max_stages, items).run()


if __name__ == "__main__":
    sys.exit(main())
