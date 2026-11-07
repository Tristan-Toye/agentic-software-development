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
import glob
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

TIME_LOGGING_OFF = ("time logging is off: do not log time to Tempo, do not ask the owner about it, and do not read "
                    "references/time-logging.md (ASD_RUNNER_TIME_LOGGING=on turns the gate back on).")
TIME_LOGGING_ON = "time logging is on: follow references/time-logging.md once, in the first stage only."
RUNNER_RULES = """\
You run under the dossier runner and nobody reads the terminal.
- Ask the owner only through scripts/ask_owner.py, and never wait in the background: keep working on whatever is not blocked; when nothing can proceed, end the turn with the line `RUNNER: WAITING <id>[,<id>...]` and stop. The ids are question ids from ask_owner.py only; a wait on a background command or workflow is not a WAITING line: just end the turn, and Claude Code re-invokes you when it finishes. The runner waits for you and sends the owner's message into this session as `OWNER ANSWER <id>: ...` or `OWNER FOLLOWUP <id>: ...`. A follow-up is the owner's typed message and the question stays open, as a chat, until the owner presses a button: if it is a question, answer it with `ask_owner.py reply <id> --text "..."`; if it reads as an answer or an instruction, reply with your reading and add it as a button, `ask_owner.py reply <id> --text "<what you take it to mean, and what you will do>" --option "D|Confirm: <your reading in a few words>|<what happens>"` (a key the question does not have yet); if it is unclear, reply with your question. When what you recommend changes (a measurement, the owner's point), say so and move the highlighted button with `--recommend <key>` on that reply. Then end the turn with `RUNNER: WAITING <id>` again. Never close a question yourself: only the owner's button does, and every question ends with one.
- Never end a turn to wait for a command: run commands in the foreground (raise the Bash timeout up to 10 minutes) or wait for them inside the same turn. A workflow or background task that notifies you later is fine, but say `waiting for <what>` in plain text in that turn (never as a `RUNNER:` line); the runner keeps the session open for its notification.
- When the auto-mode safety check blocks a call you need, never work around it: ask the owner with `ask_owner.py ask`, quoting the exact command (or path) and what it is for, with options to allow it once, to allow it as a standing rule, or to go without it; carry on with what is not blocked. The dashboard's Blocked card answers that question; an approval names the exact command, so retry it as written.
- Run every test through scripts/run_tests.py and every gate list through scripts/run_gates.py (`--vm` for the VM, `--detach` then `--wait` for a long run), never through a script or command of your own: their result lines are how the owner's timeline shows a suite going green or red. Run only the tests the command's step names: under /work-on and /fix-deferred with SUITE-MODE `stub`, that is the new and affected tests and the pull request's CI, never the full workspace or `--stack` suite, which only /stub runs.
- Never end a stage (or the run) while a background command, workflow or agent you started is still running: it dies with this process and no notification reaches the next stage. Wait for its notification and take its result first; the runner holds a stage end that comes too early. If a checkpoint must still name work in flight, say how the next stage tells it finished or died (its report file, its branch or worktree, its output file).
- Gates already settled for this run: the worktree gate is yes, and {time_logging}
- The plugin's references (`references/work-on/phase-*.md`, `formats/*`, `runner.md`) are long and the same in every stage: read only the section your step needs (`ref_section` with the document's short name, `phase-4` or `formats/dossier`, and a heading or `--list`), not whole files; the phase file you start a phase with is the one you read whole, and never the same section twice in one stage.
- Phase 2 takes its stub from `python3 scripts/stub_target.py --root R` (the plugin's scripts). Exit 3 (`wait <stub>`) means that stub is being integrated and no new one may be cut until it is in development: end the turn with the line `RUNNER: WAITING stub`; the runner waits without tokens and sends `STUB READY: <branch>` once the new stub is cut. Never cut a stub yourself.
- End each stage with its phase_checkpoint call and then, as the last line of your reply, `RUNNER: STAGE-DONE <stage name>`, and stop. The next stage starts in a new process with your checkpoints as its hand-off.
- End with `RUNNER: DONE` when /work-on is finished, and with `RUNNER: FAILED <why>` when you cannot go on.
- Keep the owner informed: post an update with `ask_owner.py note --root R --dossier ID --text "..."` when a stage starts and ends and whenever something worth knowing happens (a wave returned, the suite went green or red, an arbitration was ruled, a plan changed). Write it for a person reading on a phone: what happened and what it means, in plain sentences; markdown bullets and small tables are welcome. Never paste commands, file paths, SHAs, flags or log output into an update; the build log keeps those.
"""

def runner_rules() -> str:
    """The appended rules, with the run's settled gates: time logging is off unless ASD_RUNNER_TIME_LOGGING=on.
    Every stage is a new session running the command again, so a gate the command opens with ("time logging first")
    would otherwise be read, and asked, at every stage."""
    on = (os.environ.get("ASD_RUNNER_TIME_LOGGING") or "off").strip().lower() in ("on", "1", "true", "yes")
    return RUNNER_RULES.replace("{time_logging}", TIME_LOGGING_ON if on else TIME_LOGGING_OFF)


LIMIT_RE = re.compile(r"usage limit|rate.?limit|weekly limit|limit reached|out of (?:extra )?usage|hit your .*limit|\b429\b|overloaded", re.I)


NETWORK_RE = re.compile(r"ENOTFOUND|EAI_AGAIN|ECONNRESET|ECONNREFUSED|ETIMEDOUT|EHOSTUNREACH|ENETUNREACH|EPIPE"
                        r"|can'?t reach the API|unable to connect|fetch failed|socket hang ?up|network (?:error|is unreachable)"
                        r"|connection (?:error|reset|refused|closed)|timed? ?out|API Error: 5\d\d|\b50[0234]\b|internal server error"
                        r"|bad gateway|service unavailable|gateway time-?out", re.I)
# Waits between network retries, seconds; the last one repeats for as long as the outage lasts (ASD_RUNNER_NET_RETRIES
# sets a limit; the default, 0, is none).
NETWORK_BACKOFF = (30, 60, 120, 300, 600, 900)


def network_error(text: str) -> bool:
    """Whether a failed turn is a transient network or server error (wait and retry the stage), not a real failure."""
    return bool(NETWORK_RE.search(text or "")) and not usage_limit(text)


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
    return [{"name": a["name"], "dir": os.path.expanduser(a["dir"]), **({"email": str(a["email"])} if a.get("email") else {})} for a in acc] \
        or [{"name": "default", "dir": os.path.expanduser("~/.claude")}]


def account_login(d: str) -> str:
    """The email a config dir is logged in as: `<dir>/.claude.json`, or `~/.claude.json` for the default `~/.claude`."""
    home = os.path.expanduser("~")
    f = os.path.join(home, ".claude.json") if os.path.abspath(d) == os.path.join(home, ".claude") else os.path.join(d, ".claude.json")
    return str(((read_json(f, {}) or {}).get("oauthAccount") or {}).get("emailAddress") or "")


def account_problem(a: dict, accs: list[dict]) -> str:
    """Why an account must not be used, or "": its dir is logged in as another email than the one it is bound to
    (`email` in accounts.json), or as the same email as an account listed before it (switching would gain nothing)."""
    got = account_login(a["dir"])
    if a.get("email") and got and got.lower() != a["email"].lower():
        return f"{a['dir']} is logged in as {got}, not {a['email']}"
    for b in accs[: accs.index(a)]:
        if got and account_login(b["dir"]).lower() == got.lower():
            return f"same login ({got}) as account {b['name']}"
    return ""


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
    all_accs, st = load_accounts(), account_state()
    # An account logged in as the wrong email, or as a twin of another, is skipped while a good one exists.
    accs = [a for a in all_accs if not account_problem(a, all_accs)] or all_accs
    free_at = lambda a: 0.0 if limit_superseded(st.get(a["name"]), read_json(os.path.join(USAGE_DIR(), a["name"] + ".json"), {})) else float((st.get(a["name"]) or {}).get("limited_until") or 0)
    order = sorted(accs, key=lambda a: (a["name"] != prefer, accs.index(a)))
    for a in order:
        if free_at(a) <= now_s:
            return a, now_s
    a = min(accs, key=free_at)
    return a, free_at(a)


USAGE_DIR = lambda: os.environ.get("ASD_USAGE_DIR") or os.path.expanduser("~/.agentic/usage")


def limit_superseded(mark: dict | None, usage: dict | None) -> bool:
    """A recorded limit no longer holds once a later reading says the account's windows are open (its login changed,
    or the window reset early). The same rule as `limitSuperseded` in shared/questions/accounts.ts."""
    try:
        if not mark or not usage or not mark.get("marked") or not usage.get("ts") or usage["ts"] <= mark["marked"]:
            return False
    except TypeError:
        return False
    ws = [w for w in ((usage.get("windows") or {}).get(k) for k in ("five_hour", "seven_day")) if isinstance(w, dict)]
    return bool(ws) and all(w.get("status") != "rejected" and not (isinstance(w.get("used_percentage"), (int, float)) and w["used_percentage"] >= 100) for w in ws)


def record_usage(account: str, info: dict) -> None:
    """The account's subscription windows from a `rate_limit_event` (`rate_limit_info`): `<USAGE_DIR>/<account>.json`
    {ts, status, windows: {five_hour|seven_day|…: {used_percentage, resets_at, status}}}. `unifiedWindows` carries every
    window with `utilization` 0-1; without it the event names one window. Windows it does not name are kept."""
    if not account or not isinstance(info, dict):
        return
    path = os.path.join(USAGE_DIR(), account + ".json")
    cur = read_json(path, {}) or {}
    wins = dict(cur.get("windows") or {})
    uw = info.get("unifiedWindows")
    pairs = uw.items() if isinstance(uw, dict) and uw else ([(info.get("rateLimitType"), info)] if info.get("rateLimitType") else [])
    for name, w in pairs:
        if not isinstance(w, dict) or not name:
            continue
        u = w.get("utilization")
        pct = None if u is None else round(float(u) * 100 if float(u) <= 1.0 else float(u), 1)
        prev = wins.get(name) or {}
        wins[name] = {"used_percentage": pct if pct is not None else prev.get("used_percentage"),
                      "resets_at": w.get("resetsAt") or prev.get("resets_at"), "status": w.get("status") or info.get("status") or prev.get("status")}
    try:
        os.makedirs(USAGE_DIR(), exist_ok=True)
        write_json(path, {"ts": now(), "status": info.get("status"), "windows": wins})
    except OSError:
        pass


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


def run_key(root: str, dossier: str, command: str = "work-on", items: str = "") -> str:
    """The run's key: `<repo>.<ID>`, and for a `plan` run of a routed deferred item `<repo>.<ID>.plan-<D-n>`, so the
    planners of several items run at once, beside the source dossier's own run."""
    return key_of(root, dossier) + (f".plan-{items}" if command == "plan" and items else "")


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


# ---- background work in flight, from the session transcript (#239) ----
BG_LAUNCHED = re.compile(r"running in background|launched in background|moved to the background|launched successfully|running in the background", re.I)
BG_NOTIFIED = re.compile(r"<task-notification>[\s\S]*?<tool-use-id>([^<\s]+)</tool-use-id>")


def transcript_path(session: str, config_dir: str | None) -> str | None:
    """The session's transcript: `<config dir>/projects/*/<session>.jsonl` (`ASD_TRANSCRIPTS_DIR` overrides the
    projects dir)."""
    if not session or not re.fullmatch(r"[\w-]+", session):
        return None
    base = os.environ.get("ASD_TRANSCRIPTS_DIR") or os.path.join(config_dir or os.path.expanduser("~/.claude"), "projects")
    try:
        for d in os.listdir(base):
            f = os.path.join(base, d, session + ".jsonl")
            if os.path.isfile(f):
                return f
    except OSError:
        pass
    return None


def background_in_flight(lines: list[str]) -> list[str]:
    """Labels of the background commands, workflows and agents a session launched that have not notified it yet:
    a tool call whose result says it went to the background, minus every `<task-notification>` naming its id."""
    calls: dict[str, str] = {}
    bg: dict[str, str] = {}
    for line in lines:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        c = (o.get("message") or {}).get("content")
        if o.get("type") == "assistant" and isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id"):
                    i = b.get("input") or {}
                    calls[b["id"]] = str(i.get("description") or i.get("name") or i.get("skill") or b.get("name") or "tool")[:100]
        elif o.get("type") == "user":
            texts = [c] if isinstance(c, str) else []
            for b in c if isinstance(c, list) else []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result" and b.get("tool_use_id") in calls:
                    t = b.get("content") if isinstance(b.get("content"), str) else json.dumps(b.get("content") or "")
                    if BG_LAUNCHED.search(t[:400]):
                        bg[b["tool_use_id"]] = calls[b["tool_use_id"]]
                elif b.get("type") == "text" and isinstance(b.get("text"), str):
                    texts.append(b["text"])
            for t in texts:
                for m in BG_NOTIFIED.finditer(t):
                    bg.pop(m.group(1), None)
        elif o.get("type") == "queue-operation" and isinstance(o.get("content"), str):
            for m in BG_NOTIFIED.finditer(o["content"]):
                bg.pop(m.group(1), None)
    return list(bg.values())


HOLD_STAGE = ("RUNNER: the stage cannot end yet. Background work you started is still running and would die with this "
              "process, with no notice to the next stage: {what}. Wait for its notification (end this turn with no RUNNER: "
              "line; Claude Code re-invokes you when it finishes), take its result, then end the stage again.")
HOLD_MAX = 3   # after this many held ends the runner lets the stage end, and logs what it cut off


# ---- the orchestrator's model: pinned, never an alias, never Fable ----
# "opus" is an alias each login resolves for itself: on one account it resolved to claude-fable-5-1, at 2.5x the
# price, and ran every orchestrator on it for a day. The runner passes an exact id and checks the one the session
# reports; ASD_RUNNER_MODEL overrides it (an Opus or Sonnet id).
DEFAULT_MODEL = "claude-opus-5-5"
FORBIDDEN_MODEL = re.compile(r"fable", re.I)


def runner_model(requested: str | None) -> str:
    m = (requested or os.environ.get("ASD_RUNNER_MODEL") or DEFAULT_MODEL).strip()
    if FORBIDDEN_MODEL.search(m):
        raise SystemExit(f"run_dossier.py: model {m} is not allowed; the orchestrator runs on {DEFAULT_MODEL}")
    return m


def model_ok(expected: str, actual: str) -> bool:
    """The model the session reports matches the pinned one: the same id, or (for an alias) the same family; never Fable."""
    if not actual or actual == "<synthetic>":
        return True
    if FORBIDDEN_MODEL.search(actual):
        return False
    if expected in ("opus", "sonnet", "haiku"):
        return expected in actual
    return actual.split("[")[0] == expected.split("[")[0]


# ---- run slots: at most ASD_RUNNER_MAX_LIVE runs work at once (default 4) ----
# Ten runs resuming at one limit reset spent a whole 5-hour window in 12 minutes. A run takes a slot before each
# stage and after an owner's answer; only `running` runs hold one (waiting, paused and queued runs spend nothing).
# Without a free slot it records `queued` and waits its turn: the longest-queued run goes first.


def max_live() -> int:
    try:
        return max(0, int(os.environ.get("ASD_RUNNER_MAX_LIVE") or 4))
    except ValueError:
        return 4


def slot_census(me: str) -> tuple[int, list[tuple[str, str]]]:
    """(runs holding a slot, [(queued_since, key)] of runs waiting for one), other than `me`, live pids only."""
    held, queued = 0, []
    try:
        names = [n for n in os.listdir(runs_dir()) if n.endswith(".runner.json")]
    except OSError:
        return 0, []
    for n in names:
        key = n[: -len(".runner.json")]
        if key == me:
            continue
        st = read_json(os.path.join(runs_dir(), n), {}) or {}
        if not alive(st.get("pid")):
            continue
        if st.get("state") == "running":
            held += 1
        elif st.get("state") == "queued":
            queued.append((str(st.get("queued_since") or ""), key))
    return held, sorted(queued)


# owner_wait's code for "the owner wrote to the run" (not an answer).
MESSAGE_RC = 20
# Commands that take an argument list after the dossier id (`--items`).
ITEM_COMMANDS = ("fix-deferred", "after-merge", "plan", "resolve-merge", "stub")


class Runner:
    def __init__(self, root: str, dossier: str, command: str, model: str | None,
                 permission_mode: str, max_stages: int, items: str = ""):
        self.root = os.path.abspath(root)
        self.dossier = dossier
        self.command = command
        self.items = items  # fix-deferred: the D-ids the owner selected, "D-1,D-3" (empty = all); after-merge: the merged PR numbers, "429,430"
        self.model = runner_model(model) if command != "product-summary" else model
        self.permission_mode = permission_mode
        self.max_stages = max_stages
        prev = read_json(os.path.join(runs_dir(), run_key(root, dossier, command, items) + ".runner.json"), {}) or {}
        prev_acct = prev.get("account", "")
        self.account, _ = pick_account(prefer=prev_acct)
        self.key = run_key(root, dossier, command, items)
        d = runs_dir()
        self.updates = os.path.join(d, self.key + ".jsonl")
        self.state_path = os.path.join(d, self.key + ".runner.json")
        self.cp_path = os.path.join(d, self.key + ".checkpoints.json")
        self.err_path = os.path.join(d, self.key + ".stderr.log")
        self.state: dict = {"pid": os.getpid(), "account": self.account["name"], "root": root, "command": command, "items": items, "permission_mode": permission_mode, "started": now(), "state": "running", "stage": 0,
                            "session": "", "questions": [], "claude_pid": None,
                            # the owner's messages already delivered: a resumed run never sends one twice
                            "inbox_seen": int(prev.get("inbox_seen") or 0)}
        self.state["model"] = self.model
        self.stop_marker = os.path.join(d, self.key + ".stop-requested")
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

    def restart_path(self) -> str:
        return os.path.join(runs_dir(), self.key + ".restart")

    def take_restart(self) -> str | None:
        """The dashboard's request to restart the stage once the owner's ruling arrives (`<key>.restart`, holding the
        rule saved), consumed; None when there is none."""
        try:
            with open(self.restart_path()) as fh:
                why = fh.read().strip()
            os.remove(self.restart_path())
            return why
        except OSError:
            return None

    def clear_restart(self) -> None:
        try:
            os.remove(self.restart_path())
        except OSError:
            pass

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
                "--permission-mode", self.permission_mode, "--append-system-prompt", runner_rules()]
        cmd += ["--model", self.model or runner_model(None)]
        return cmd

    def command_line(self) -> str:
        """The command the stage starts with. A /stub run's key is the stub's slug (stub-2026-11-03); the command takes
        the branch itself (stub/2026-11-03), carried in --items."""
        if self.command == "stub" and self.items:
            return f"/{PLUGIN}:stub {self.items}"
        return f"/{PLUGIN}:{self.command} {self.dossier}" + (f" {self.items}" if self.items and self.command in ITEM_COMMANDS else "")

    def handoff(self) -> str | None:
        if not self.checkpoints:
            return None
        rows = [f"RUNNER HAND-OFF. Task: run {self.command_line()} in {self.root}.",
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
        self.wrong_model = ""
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
        self.send(self.command_line())
        extra = False
        hand = self.handoff()
        carry = getattr(self, "pending_owner", "")
        if carry:
            # The stage restarted so a new permission rule applies (the dashboard's "Always allow"): the owner's answer
            # to the question the previous process asked travels with the hand-off.
            hand = (hand + "\n" if hand else f"RUNNER HAND-OFF. Task: run {self.command_line()} in {self.root}.\n") + (
                "This stage restarted so that a permission rule the owner just saved applies. Your previous process asked "
                "the owner a question; carry on from the dossier and this answer, and retry the command the rule now allows:\n"
                + carry)
            self.pending_owner = ""
        if hand:
            self.send(hand)
            extra = True
        return q, extra

    def background(self) -> list[str]:
        """The session's background work still in flight (#239), from its transcript; [] when it cannot be read."""
        path = transcript_path(str(self.state.get("session") or ""), (self.account or {}).get("dir"))
        if not path:
            return []
        try:
            with open(path, errors="replace") as fh:
                return background_in_flight(fh.read().splitlines())
        except OSError:
            return []

    def take_slot(self) -> bool:
        """Wait for a run slot (max_live); False when the run was stopped while it waited."""
        cap = max_live()
        if cap == 0:
            return True
        since = ""
        while not self.stopping:
            held, queued = slot_census(self.key)
            ahead = [k for s, k in queued if since and s < since]
            if held < cap and (not since or not ahead):
                if since:
                    self.event(f"slot free; {held + 1} of {cap} runs working", "running")
                self.save(state="running", queued_since=None)
                return True
            if not since:
                since = now()
                self.save(state="queued", queued_since=since)
                self.event(f"queued: {held} of {cap} runs are working (ASD_RUNNER_MAX_LIVE); this one starts when a slot frees", "queued")
            time.sleep(float(os.environ.get("ASD_RUNNER_SLOT_POLL") or 10))
        return False

    def wait_for_stub(self) -> str | None:
        """Poll scripts/stub_target.py until a stub can be built on (the integrated one merged, the next one cut)."""
        self.save(state="waiting", questions=[])
        self.event("waiting for the stub being integrated to merge into development; a new stub is cut then", "waiting")
        poll = float(os.environ.get("ASD_STUB_POLL") or 300)
        script = os.environ.get("ASD_STUB_TARGET_BIN") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "stub_target.py")
        while not self.stopping:
            r = subprocess.run([sys.executable, script, "--root", self.root], capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                branch = r.stdout.strip().splitlines()[-1]
                self.event(f"stub ready: {branch}", "running")
                return branch
            if r.returncode not in (0, 3):
                self.log(f"stub_target failed: {(r.stderr or r.stdout).strip()[:200]}; trying again")
            end = time.time() + poll
            while time.time() < end and not self.stopping:
                time.sleep(min(5.0, poll))
        return None

    def forward_owner_replies(self) -> bool:
        """Send on what the owner wrote on this run's open questions while the run was not waiting on them. The agent
        may ask and keep working; the runner used to pass a reply on only inside a wait, so a follow-up typed meanwhile
        sat unread. Checked every 20 s; each owner line goes once, in the wait's own form (OWNER FOLLOWUP / ANSWER).
        A question the run is waiting on is left to the wait."""
        nowm = time.monotonic()
        if nowm - getattr(self, "_fwdq_checked", 0.0) < float(os.environ.get("ASD_RUNNER_QFWD_EVERY") or 20):
            return False
        self._fwdq_checked = nowm
        if self.proc is None or self.proc.poll() is not None:
            return False
        qdir = os.environ.get("ASD_QUESTIONS_DIR") or os.path.expanduser("~/.agentic/questions")
        prefix = self.key_q_prefix()
        waiting = set(self.state.get("questions") or [])
        done = dict(self.state.get("fwd_q") or {})
        started = str(self.state.get("started") or "")
        sent = 0
        try:
            names = [n for n in os.listdir(qdir) if n.startswith(prefix) and n.endswith(".jsonl")]
        except OSError:
            return False
        for n in names:
            qid = n[:-6]
            if qid in waiting:
                continue
            try:
                with open(os.path.join(qdir, n)) as fh:
                    lines = [json.loads(l) for l in fh if l.strip()]
            except (OSError, ValueError):
                continue
            q = lines[0] if lines else {}
            # This run's question: asked by its run key, or (older questions) asked since this runner started.
            if q.get("run") and q["run"] != self.key:
                continue
            if not q.get("run") and str(q.get("ts") or "") < started:
                continue
            last_agent = max((i for i, l in enumerate(lines) if l.get("from") == "agent"), default=0)
            for l in lines[last_agent + 1:]:
                if l.get("from") != "owner" or str(l.get("ts") or "") <= str(done.get(qid) or ""):
                    continue
                if l.get("kind") == "answer":
                    opt = l.get("option") or ""
                    self.send(f"OWNER ANSWER {qid}{f' [option {opt}]' if opt else ''}: {l.get('text', '')}")
                else:
                    self.send(f"OWNER FOLLOWUP {qid}: {l.get('text', '')} (the owner wrote this on your open question while you worked; "
                              "answer it in the question's chat with ask_owner.py reply)")
                done[qid] = str(l.get("ts") or "")
                sent += 1
        if sent:
            self.save(fwd_q=done)
            self.log(f"sent on {sent} owner repl{'y' if sent == 1 else 'ies'} on open questions")
        return bool(sent)

    def key_q_prefix(self) -> str:
        """Question ids of this run's dossier: `<repo>.<ID>.Q`."""
        return key_of(self.root, self.dossier) + ".Q"

    def forward_dropped(self) -> bool:
        """Send on the completion notices the CLI dropped. A background command or workflow that finishes while no turn
        is running is announced by a `<task-notification>`; headless sessions have been seen to `remove` it from the
        queue instead of delivering it, so the session waits forever for news it was already sent. Checked at most every
        30 s while the session is idle; each dropped notice is sent once, as a user message."""
        nowm = time.monotonic()
        if nowm - getattr(self, "_fwd_checked", 0.0) < float(os.environ.get("ASD_RUNNER_FWD_EVERY") or 30):
            return False
        self._fwd_checked = nowm
        if self.proc is None or self.proc.poll() is not None:
            return False
        path = transcript_path(str(self.state.get("session") or ""), (self.account or {}).get("dir"))
        if not path:
            return False
        done = set(self.state.get("forwarded") or [])
        try:
            with open(path, errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return False
        delivered, dropped = set(), {}
        for line in lines:
            if '"queue-operation"' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            c = o.get("content") if isinstance(o.get("content"), str) else ""
            m = re.search(r"<task-id>([^<\s]+)</task-id>", c)
            if not m:
                continue
            if o.get("operation") == "dequeue":
                delivered.add(m.group(1))
            if o.get("operation") == "remove":
                dropped[m.group(1)] = c
        # A notice delivered later (its own dequeue, or a task-notification inside a user message) is not re-sent.
        for line in lines:
            if '"type":"user"' in line.replace(" ", "") and "<task-notification>" in line:
                for m in re.finditer(r"<task-id>([^<\s\\]+)</task-id>", line):
                    delivered.add(m.group(1))
        todo = [(t, c) for t, c in dropped.items() if t not in done and t not in delivered]
        if not todo:
            return False
        for t, c in todo:
            self.send("BACKGROUND TASK FINISHED (the CLI dropped this notice instead of delivering it; read the task's output and carry on):\n" + c)
        self.save(forwarded=sorted(done | {t for t, _ in todo}))
        self.log(f"sent on {len(todo)} dropped background notice(s): " + ", ".join(t for t, _ in todo))
        return True

    def deliver_inbox(self) -> bool:
        """Send the owner's undelivered messages (`run_dossier.py chat` on a live run) into the live session."""
        if self.proc is None or self.proc.poll() is not None:
            return False
        seen = int(self.state.get("inbox_seen") or 0)
        new = read_inbox(self.key, seen)
        if not new:
            return False
        for m in new:
            if (m.get("text") or "").strip():
                self.send(owner_message(m["text"].strip()))
        self.save(inbox_seen=seen + len(new))
        self.log(f"delivered {len(new)} message(s) from the owner to the running agent")
        return True

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
        if ev.get("type") == "rate_limit_event":
            record_usage((getattr(self, "account", None) or {}).get("name", ""), ev.get("rate_limit_info") or {})
            return
        if ev.get("type") == "system" and ev.get("subtype") == "init" and ev.get("session_id"):
            self.save(session=ev["session_id"])
        # The model the session really runs on: anything but the pinned one (Fable above all) ends the stage at once.
        # A sub-agent's message (parent_tool_use_id set) has its own model, Sonnet by design: only Fable is refused there.
        got = ev.get("model") if ev.get("type") == "system" and ev.get("subtype") == "init" else (ev.get("message") or {}).get("model") if ev.get("type") == "assistant" else None
        wrong = isinstance(got, str) and (FORBIDDEN_MODEL.search(got) if ev.get("parent_tool_use_id") else not model_ok(self.model or runner_model(None), got))
        if wrong and not getattr(self, "wrong_model", ""):
            self.wrong_model = got
            self.log(f"the session runs on {got}, not {self.model or runner_model(None)}: stopping the stage")
            kill_group(self.proc)
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
            if result is None and self.deliver_inbox():
                quiet_since = time.monotonic()  # a message between turns starts a turn of its own
            if result is None and self.forward_owner_replies():
                quiet_since = time.monotonic()  # the owner wrote on an open question while the agent worked
            if result is None and idle is not None and self.forward_dropped():
                quiet_since = time.monotonic()  # a dropped notification, sent on: the session works again
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
            # Only the session's own work is activity: a queue or system event (a notification the CLI then drops)
            # must not keep the idle nudge from ever firing.
            if not isinstance(ev, dict) or ev.get("type") in ("assistant", "user", "result"):
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
                if read_inbox(self.key, int(self.state.get("inbox_seen") or 0)):
                    return "", MESSAGE_RC, ""  # the owner wrote to the run: wake the session with it
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
        holds = 0       # stage ends held while background work ran
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
                if getattr(self, "wrong_model", ""):
                    return "failed", f"the session ran on {self.wrong_model}, not {self.model or runner_model(None)}; the runner refuses any other model (set the account's model, or ASD_RUNNER_MODEL)"
                if usage_limit(tail):
                    return "limit", tail.strip()
                if network_error(tail):
                    return "network", tail.strip().splitlines()[-1][:300] if tail.strip() else "network error"
                return "failed", f"the claude process exited with code {val} before a result"
            text, err = val  # type: ignore[misc]
            if getattr(self, "wrong_model", ""):
                kill_group(self.proc)
                self.end_process()
                return "failed", f"the session ran on {self.wrong_model}, not {self.model or runner_model(None)}; the runner refuses any other model (set the account's model, or ASD_RUNNER_MODEL)"
            if err and usage_limit(text):
                kill_group(self.proc)
                self.end_process()
                return "limit", text.strip()
            if err and network_error(text):
                kill_group(self.proc)
                self.end_process()
                return "network", text.strip().splitlines()[-1][:300] if text.strip() else "network error"
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
            if verb in ("STAGE-DONE", "DONE"):
                # #239: ending the process kills the background work it started, silently. Hold the end until it is done.
                bg = self.background()
                if bg and holds < HOLD_MAX:
                    holds += 1
                    self.log(f"stage end held ({holds}/{HOLD_MAX}): {len(bg)} background task(s) still running: " + "; ".join(bg[:4]))
                    self.send(HOLD_STAGE.format(what="; ".join(bg[:6])))
                    quiet = True
                    continue
                if bg:
                    self.log(f"stage ended with {len(bg)} background task(s) still running, after {HOLD_MAX} holds; they stop with it: " + "; ".join(bg[:4]))
            if verb == "STAGE-DONE":
                self.clear_restart()  # the next stage is a new process: it loads the new rules anyway
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
            if ids[:1] == ["stub"]:
                # The breakpoint for a new stub: the current one is being integrated, and a new stub is cut only once it
                # is in development. Wait here, spending nothing, then hand the session the branch to build on.
                branch = self.wait_for_stub()
                if branch is None:
                    return "stopped", ""
                if not self.take_slot():
                    return "stopped", ""
                self.send(f"STUB READY: {branch}. The integrated stub is in development and {branch} is cut from it: "
                          "fork from it and carry on with the step you stopped at.")
                continue
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
            if rc == MESSAGE_RC:
                # Not an answer: the question stays open. Deliver the message; the agent ends that turn waiting again.
                self.save(state="running")
                self.deliver_inbox()
                continue
            if rc not in (0, 10):
                kill_group(self.proc)
                self.end_process()
                return "failed", f"waiting on {qid or ids[0]} failed: {out[:200]}"
            self.save(questions=[i for i in ids if i != qid] if rc == 0 else ids)
            if not self.take_slot():
                return "stopped", ""
            self.event(("answered " if rc == 0 else "follow-up on ") + qid, "running")
            why = self.take_restart() if rc == 0 else None
            if why is not None:
                # A rule saved while the agent waited only applies to a new process: restart the stage at this safe
                # point (the agent is waiting, nothing of its own runs) and hand the answer over.
                self.pending_owner = "OWNER " + out
                kill_group(self.proc)
                self.end_process()
                return "restart", why
            self.send("OWNER " + out)

    def on_term(self, *_a) -> None:
        self.stopping = True
        kill_group(self.proc)
        for p in self.children:
            if p.poll() is None:
                p.kill()

    def run(self) -> int:
        prev = read_json(self.state_path, {})
        if prev.get("state") in ("running", "waiting", "queued") and alive(prev.get("pid")) and prev.get("pid") != os.getpid():
            say(f"{self.key} is already run by pid {prev['pid']}")
            return 2
        try:
            os.remove(self.stop_marker)
        except OSError:
            pass
        if prev.get("state") == "done":
            self.checkpoints = []
            write_json(self.cp_path, [])
        signal.signal(signal.SIGTERM, self.on_term)
        signal.signal(signal.SIGINT, self.on_term)
        self.save()
        self.event(f"start /{self.command} {self.dossier} in {self.root}", "running")
        started = 0
        net_tries = 0   # network errors in a row; a stage that ends well resets it
        while True:
            if self.stopping:
                return self.finish("stopped", "stopped", 3)
            if started >= self.max_stages:
                return self.finish("stopped", f"--max-stages {self.max_stages} reached", 4)
            started += 1
            self.save(stage=started, questions=[])
            if not self.take_slot():
                return self.finish("stopped", "stopped", 3)
            self.event(f"stage {started} start", "running")
            outcome, detail = self.run_stage()
            if outcome != "network":
                net_tries = 0
            if outcome == "next":
                self.event(f"stage {started} end" + (f": {detail}" if detail else ""), "running")
                continue
            if outcome == "restart":
                self.event(f"restarting stage {started} so the new permission rule applies" + (f" ({detail[:160]})" if detail else ""), "running")
                started -= 1
                continue
            if outcome == "done":
                self.event(f"stage {started} end", "running")
                self.deferred_report()
                self.product_summary()
                self.ready_fix_pr()
                return self.finish("done", "done", 0)
            if outcome == "stopped":
                self.end_process()
                return self.finish("stopped", "stopped", 3)
            if outcome == "network":
                # A transient network or API-server error: pause, then run the same stage again from its checkpoints,
                # backing off, however long it lasts: an outage says nothing about the work. Only with
                # ASD_RUNNER_NET_RETRIES set does it become a failure the owner rules on.
                net_tries += 1
                limit_n = int(os.environ.get("ASD_RUNNER_NET_RETRIES") or 0)
                if not limit_n or net_tries <= limit_n:
                    wait = float(os.environ.get("ASD_RUNNER_NET_WAIT") or NETWORK_BACKOFF[min(net_tries, len(NETWORK_BACKOFF)) - 1])
                    until = time.time() + wait
                    at = datetime.datetime.fromtimestamp(until, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    self.save(state="paused", resumes_at=at)
                    self.event(f"paused: network error ({detail[:160]}); retry {net_tries}{'/' + str(limit_n) if limit_n else ''} of stage {started} at {at}", "paused")
                    while time.time() < until and not self.stopping:
                        time.sleep(min(5.0, max(0.2, until - time.time())))
                    if self.stopping:
                        return self.finish("stopped", "stopped", 3)
                    self.save(resumes_at=None)
                    self.event(f"resumed after the network error; stage {started} again", "running")
                    started -= 1
                    continue
                detail = f"network errors on {limit_n} retries in a row: {detail}"
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

    def ready_fix_pr(self) -> None:
        """A finished /fix-deferred leaves no draft behind, whatever the agent did: `gh stack link` opens one unless told
        otherwise, and the merge queue refuses a stack with a draft in it. Best effort: no PR yet is not an error."""
        if self.command != "fix-deferred":
            return
        try:
            p = subprocess.run(shlex.split(os.environ.get("ASD_GH_BIN") or "gh") + ["pr", "ready", f"fix/{self.dossier}-deferred"], cwd=self.root,
                               capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
            if p.returncode == 0 and "already" not in (p.stdout + p.stderr).lower():
                self.log(f"marked fix/{self.dossier}-deferred ready for review")
        except (OSError, subprocess.TimeoutExpired) as e:
            self.log(f"could not mark fix/{self.dossier}-deferred ready: {e}")

    def finish(self, state: str, text: str, rc: int) -> int:
        self.end_process()
        if state == "stopped" and self.stopping and not os.path.exists(self.stop_marker):
            # A SIGTERM nobody asked for: a logout or a shutdown, not the owner. The state stays as it is, so `revive`
            # picks the run up again once this process is gone.
            self.event("interrupted from outside (a logout or a shutdown); it restarts when it can")
            return rc
        try:
            os.remove(self.stop_marker)
        except OSError:
            pass
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
        if state in ("running", "waiting", "queued") and not alive(st.get("pid")):
            state = "stopped"
        q = ",".join(st.get("questions") or [])
        rows.append(f"{key}\t{state}\tstage {st.get('stage', 0)}\tsession {st.get('session') or '-'}"
                    f"\tpid {st.get('pid')}" + (f"\tquestions {q}" if q else ""))
    print("\n".join(rows) if rows else "no runs")
    return 0


def cmd_revive() -> int:
    """Restart every run that was killed rather than stopped: its record still says it is working, its process is gone
    (a logout, a reboot, a crash). It runs the same stage again from its checkpoints, as Resume does. A run dead for over
    ASD_RUNNER_REVIVE_HOURS (default 48) stays down, and one that keeps dying is left stopped after 3 restarts in an hour."""
    hours = float(os.environ.get("ASD_RUNNER_REVIVE_HOURS") or 48)
    for path in glob.glob(os.path.join(runs_dir(), "*.runner.json")):
        st = read_json(path, {}) or {}
        root = st.get("root") or ""
        if st.get("state") not in ("running", "waiting", "paused", "queued") or alive(st.get("pid")) or not os.path.isdir(root):
            continue
        if time.time() - os.path.getmtime(path) > hours * 3600:
            continue
        key = os.path.basename(path)[:-len(".runner.json")]
        prefix = main_repo_name(root) + "."
        if not key.startswith(prefix):
            continue
        dossier = re.sub(r"\.plan-D-\d+$", "", key[len(prefix):])
        guard = os.path.join(runs_dir(), key + ".revive.json")
        recent = [t for t in (read_json(guard, []) or []) if time.time() - t < 3600]
        if len(recent) >= 3:
            st["state"] = "stopped"
            write_json(path, st)
            append_line(os.path.join(runs_dir(), key + ".jsonl"), {"ts": now(), "kind": "runner", "text": "not restarted: its process died 3 times within an hour; Resume it by hand", "state": "stopped"})
            continue
        write_json(guard, recent + [time.time()])
        append_line(os.path.join(runs_dir(), key + ".jsonl"), {"ts": now(), "kind": "runner", "text": "its process was gone; restarting from the checkpoints"})
        args = [sys.executable, os.path.abspath(__file__), dossier, "--root", root, "--command", st.get("command") or "work-on"]
        if st.get("items"):
            args += ["--items", st["items"]]
        if st.get("model"):
            args += ["--model", st["model"]]
        subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
        print(f"run_dossier: restarted {key}")
    return 0


def already_built(root: str, dossier: str) -> str:
    """Why /work-on must not start: the dossier is in the merge queue, or has an open pull request and no build of its
    own to resume. Its status on development can still read `ready`: the record that says done is on its branch, not
    merged yet. Starting it anyway deadlocks an integration: the run waits for the stub, the stub waits for the merge,
    and the merge waits for the run. "" when it may start, also when GitHub cannot be asked."""
    repo = main_repo_name(root)
    queue = read_json(os.path.join(runs_dir(), f"{repo}.merge-queue.json"), {}) or {}
    if any(i.get("id") == dossier for i in queue.get("items") or []):
        return f"{dossier} is in the merge queue; it is built"
    prev = read_json(os.path.join(runs_dir(), f"{repo}.{dossier}.runner.json"), {}) or {}
    if prev.get("command") == "work-on" and prev.get("state") in ("running", "waiting", "paused", "queued", "stopped", "failed"):
        return ""
    try:
        p = subprocess.run(shlex.split(os.environ.get("ASD_GH_BIN") or "gh") + ["pr", "list", "--state", "open", "--limit", "200", "--json", "number,headRefName"],
                           cwd=root, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        prs = json.loads(p.stdout or "[]") if p.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return ""
    pat = re.compile(r"(^|[^A-Za-z0-9])" + re.escape(dossier) + r"(?![0-9])", re.I)
    mine = [x["number"] for x in prs if pat.search(x.get("headRefName", "")) and not x["headRefName"].startswith(("plan/", "docs/"))]
    return f"{dossier} is already built: pull request " + ", ".join(f"#{n}" for n in mine) + " is open" if mine else ""


def cmd_stop(a: argparse.Namespace) -> int:
    key = key_of(a.root, a.dossier)
    path = os.path.join(runs_dir(), key + ".runner.json")
    st = read_json(path, None)
    if not st:
        print(f"no run for {key}")
        return 1
    pid, cpid = st.get("pid"), st.get("claude_pid")
    if st.get("state") in ("running", "waiting", "queued") or alive(cpid):
        if alive(pid):
            open(os.path.join(runs_dir(), key + ".stop-requested"), "w").close()   # the owner's stop, not a logout
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
        if st.get("state") in ("running", "waiting", "queued"):
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


# ---- the owner's messages to a live run: `<key>.inbox.jsonl`, delivered by the runner into the live session ----
def inbox_path(key: str) -> str:
    return os.path.join(runs_dir(), key + ".inbox.jsonl")


def read_inbox(key: str, seen: int) -> list[dict]:
    """The owner's messages after the first `seen` lines of the inbox."""
    try:
        with open(inbox_path(key)) as fh:
            lines = [l for l in fh.read().split("\n") if l.strip()]
    except OSError:
        return []
    out = []
    for l in lines[seen:]:
        try:
            out.append(json.loads(l))
        except ValueError:
            out.append({"text": ""})
    return out


def owner_message(text: str) -> str:
    return ("OWNER MESSAGE (not an answer to a question; the owner typed it on the dashboard): " + text + "\n"
            "Read it against what you are doing now. If it corrects an earlier answer or ruling, the message wins: act on it, "
            "undo what the old answer made you do where needed, and log the change in the build log. Confirm in one short "
            "note (ask_owner.py note) what you will do with it, or ask with ask_owner.py if it is unclear. Then carry on.")


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
    if st.get("state") in ("running", "waiting", "paused", "queued") and alive(st.get("pid")):
        # A live run: the runner delivers it into the running session (mid-turn, or by waking a question wait).
        append_line(inbox_path(key), {"ts": now(), "text": text[:20000]})
        append_line(os.path.join(d, key + ".jsonl"), {"ts": now(), "kind": "update", "event": "owner-message", "from": "owner", "text": text[:20000]})
        say(f"sent to {key}; the running agent gets it " + ("when the usage pause ends" if st.get("state") == "paused" else "now"))
        return 0
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
    cmd += ["-p", "--resume", sid, "--output-format", "json", "--model", runner_model(None), "--permission-mode", st.get("permission_mode") or "auto",
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


def cmd_usage(a: argparse.Namespace) -> int:
    """`usage [--account NAME]`: refresh each account's 5-hour and weekly windows with one tiny haiku call, read from
    its `rate_limit_event`. A run's own stages record them as they go; this is for an account nothing runs on."""
    accs = [x for x in load_accounts() if not a.account or x["name"] == a.account]
    if not accs:
        print(f"no account named {a.account}", file=sys.stderr)
        return 2
    bad = 0
    for acc in accs:
        env = dict(os.environ, ASD_RUN_UPDATES="0")
        env.pop("ASD_RUN_KEY", None)
        if os.path.abspath(acc["dir"]) != os.path.abspath(os.path.expanduser("~/.claude")):
            env["CLAUDE_CONFIG_DIR"] = acc["dir"]
        else:
            env.pop("CLAUDE_CONFIG_DIR", None)
        cmd = shlex.split(os.environ.get("ASD_CLAUDE_BIN") or "claude") + [
            "-p", "--output-format", "stream-json", "--verbose", "--model", "haiku", "Reply with the single word ok."]
        got = False
        try:
            out = subprocess.run(cmd, cwd=os.path.expanduser("~"), env=env, capture_output=True, text=True, timeout=120)
            for line in out.stdout.splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "rate_limit_event":
                    record_usage(acc["name"], ev.get("rate_limit_info") or {})
                    got = True
            tail = (out.stdout + out.stderr)[-2000:]
            if not got and usage_limit(tail):
                record_usage(acc["name"], {"status": "rejected"})
                got = True
        except (OSError, subprocess.TimeoutExpired):
            pass
        say(f"{acc['name']}: " + ("usage recorded" if got else "no usage reading"))
        bad += 0 if got else 1
    return 0 if not bad else 1


def cmd_summary(a: argparse.Namespace) -> int:
    """`summary ID`: write the product summary of a finished dossier now (for builds that ended before the runner
    wrote one, or to redo it). The run's own record stays as it is."""
    key = key_of(a.root, a.dossier)
    path = os.path.join(runs_dir(), key + ".runner.json")
    prev = read_json(path, {}) or {}
    if prev.get("state") in ("running", "waiting", "paused", "queued") and alive(prev.get("pid")):
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
argm = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else ""
out({"type": "system", "subtype": "init", "session_id": f"sess-{n}", "model": script.get("init_model", argm)})
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
        elif "append" in a:
            with open(a["append"], "a") as fh:
                fh.write(a["line"] + "\n")
            final = a.get("final", final)
        elif "subagent" in a:
            out({"type": "assistant", "parent_tool_use_id": "toolu_1", "message": {"model": a["subagent"], "content": [{"type": "text", "text": "x"}]}})
            final = a.get("final", final)
        elif "final" in a:
            final = a["final"]
    err = next((a["error"] for a in acts if "error" in a), None)
    if err:
        out({"type": "result", "subtype": "error_during_execution", "is_error": True, "session_id": f"sess-{n}", "result": err})
        continue
    out({"type": "result", "subtype": "success", "is_error": False, "session_id": f"sess-{n}", "result": "SECRET-MODEL-TEXT\n" + final})
    if script.get("exit_after_turn"):
        break
'''


def already_built_for_test(root: str, dossier: str, env: dict) -> str:
    saved = dict(os.environ)
    os.environ.update(env)
    try:
        return already_built(root, dossier)
    finally:
        os.environ.clear(); os.environ.update(saved)


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

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # a detached child may still write as it goes
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
              and "--model sonnet" in first_sum and rn.model == DEFAULT_MODEL and rn.command == "work-on")
        os.environ["ASD_USAGE_DIR"] = os.path.join(tmp, "usage")
        record_usage("acct", {"status": "allowed", "rateLimitType": "five_hour", "unifiedWindows": {
            "five_hour": {"utilization": 0.08, "resetsAt": 100, "status": "allowed"}, "seven_day": {"utilization": 0.4, "resetsAt": 200, "status": "allowed"}}})
        record_usage("acct", {"status": "rejected", "rateLimitType": "seven_day", "utilization": 1.0, "resetsAt": 300})
        uw = read_json(os.path.join(tmp, "usage", "acct.json"), {}).get("windows", {})
        check("usage: a rate_limit_event's windows are recorded as percentages; a one-window event keeps the others",
              uw.get("five_hour", {}).get("used_percentage") == 8.0 and uw.get("seven_day", {}).get("used_percentage") == 100.0
              and uw.get("seven_day", {}).get("resets_at") == 300 and uw.get("five_hour", {}).get("resets_at") == 100)
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
        # ---- 1a. a permission rule saved while the agent waits restarts the stage ----------
        q5 = "repo.W-5.Q1"
        scenario([
            {"turns": [[{"ask": 1, "dossier": "W-5", "final": "RUNNER: WAITING {id}"}]]},
            {"turns": [[{"final": "retried, works\nRUNNER: DONE"}]]},
        ])
        p5 = start("W-5")
        check("restart: the agent waits on its question", bool(until(lambda: os.path.exists(qfile(q5)) and state_of("W-5").get("state") == "waiting")))
        with open(os.path.join(rdir, "repo.W-5.restart"), "w") as fh:
            fh.write("Bash(ssh vm-test *)")
        owner_line(q5, "answer", "Approved: you may run ssh vm-test rebuild", "")
        p5.wait(timeout=60)
        r5 = texts("W-5")
        check("restart: the answer restarts the stage in a new process, which gets the answer with the hand-off",
              p5.returncode == 0 and any(t.startswith("restarting stage 1 so the new permission rule applies") for t in r5)
              and r5[-1] == "done" and not os.path.exists(os.path.join(rdir, "repo.W-5.restart"))
              and any("OWNER ANSWER " + q5 in m and "permission rule the owner just saved" in m for m in msgs(2)))
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
        with open(os.path.join(fdir, "gh_ready.py"), "w") as fh:
            fh.write("import os,sys\nopen(os.path.join(os.path.dirname(os.path.abspath(__file__)),'ready.txt'),'a').write(' '.join(sys.argv[1:])+'\\n')\n")
        env["ASD_GH_BIN"] = f"{sys.executable} {os.path.join(fdir, 'gh_ready.py')}"
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-75", "--command", "fix-deferred")
        p.wait(timeout=60)
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-76", "--command", "after-merge")
        p.wait(timeout=60)
        env.pop("ASD_GH_BIN")
        rd = open(os.path.join(fdir, "ready.txt")).read() if os.path.exists(os.path.join(fdir, "ready.txt")) else ""
        check("fix-deferred: a finished run marks its PR ready; no other command does", "pr ready fix/W-75-deferred" in rd and "W-76" not in rd)
        with open(os.path.join(fdir, "gh_prs.py"), "w") as fh:
            fh.write("import json\nprint(json.dumps([{'number': 427, 'headRefName': 'feat/W-78-x'}, {'number': 9, 'headRefName': 'plan/W-79-y'}]))\n")
        env["ASD_GH_BIN"] = f"{sys.executable} {os.path.join(fdir, 'gh_prs.py')}"
        built = subprocess.run([sys.executable, me, "W-78", "--root", root, "--foreground"], env=env, capture_output=True, text=True, timeout=30)
        check("work-on: a dossier with an open pull request is refused, naming it", built.returncode == 2 and "#427" in built.stderr)
        write_json(os.path.join(rdir, "repo.merge-queue.json"), {"items": [{"id": "W-80"}]})
        queued = subprocess.run([sys.executable, me, "W-80", "--root", root, "--foreground"], env=env, capture_output=True, text=True, timeout=30)
        os.remove(os.path.join(rdir, "repo.merge-queue.json"))
        check("work-on: a dossier in the merge queue is refused", queued.returncode == 2 and "merge queue" in queued.stderr)
        write_json(os.path.join(rdir, "repo.W-78.runner.json"), {"state": "stopped", "command": "work-on", "pid": 0})
        check("work-on: its own stopped build can still be resumed", already_built_for_test(root, "W-78", env) == "")
        os.remove(os.path.join(rdir, "repo.W-78.runner.json"))
        env.pop("ASD_GH_BIN")
        scenario([{"hang": True, "turns": []}])
        p = start("W-71")
        until(lambda: state_of("W-71").get("claude_pid"))
        p.terminate(); p.wait(timeout=30)
        st71 = state_of("W-71")
        check("revive: a SIGTERM nobody asked for keeps the run's state, so it can restart", st71.get("state") == "running" and any("interrupted" in t for t in texts("W-71")))
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        r = subprocess.run([sys.executable, me, "revive"], env=env, capture_output=True, text=True, timeout=60)
        done71 = until(lambda: state_of("W-71").get("state") == "done", 30)
        check("revive: the killed run restarts and finishes", "restarted repo.W-71" in r.stdout and bool(done71))
        write_json(os.path.join(rdir, "repo.W-72.runner.json"), {"pid": 2 ** 22 + 12346, "state": "stopped", "root": root, "command": "work-on"})
        write_json(os.path.join(rdir, "repo.W-73.runner.json"), {"pid": 2 ** 22 + 12347, "state": "running", "root": root, "command": "work-on"})
        write_json(os.path.join(rdir, "repo.W-73.revive.json"), [time.time()] * 3)
        write_json(os.path.join(rdir, "repo.W-74.runner.json"), {"pid": 2 ** 22 + 12348, "state": "running", "root": root, "command": "work-on"})
        os.utime(os.path.join(rdir, "repo.W-74.runner.json"), (time.time() - 72 * 3600, time.time() - 72 * 3600))
        r = subprocess.run([sys.executable, me, "revive"], env=env, capture_output=True, text=True, timeout=60)
        check("revive: a stopped run, one dying over and over, and one long dead all stay down", "W-72" not in r.stdout and "W-73" not in r.stdout and "W-74" not in r.stdout and state_of("W-73").get("state") == "stopped")
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

        check("accounts: a limit marked before an open reading no longer holds", limit_superseded({"marked": "2026-11-06T08:53:41Z"}, {"ts": "2026-11-06T11:01:17Z", "windows": {"five_hour": {"used_percentage": 2.0, "status": "allowed"}, "seven_day": {"used_percentage": 0, "status": "allowed"}}})
              and not limit_superseded({"marked": "2026-11-06T08:53:41Z"}, {"ts": "2026-11-06T08:50:00Z", "windows": {"five_hour": {"used_percentage": 2.0, "status": "allowed"}}})
              and not limit_superseded({"marked": "2026-11-06T08:53:41Z"}, {"ts": "2026-11-06T09:00:00Z", "windows": {"five_hour": {"used_percentage": 100, "status": "rejected"}}}))
        # ---- run slots: a full house queues the run; it starts when a slot frees ----
        for n in os.listdir(rdir):   # earlier cases left fake live runs behind: they would hold the slot
            if n.endswith(".runner.json"):
                stx = read_json(os.path.join(rdir, n), {}) or {}
                if stx.get("state") in ("running", "queued"):
                    write_json(os.path.join(rdir, n), {**stx, "state": "done"})
        busy = {"pid": os.getpid(), "state": "running"}
        write_json(os.path.join(rdir, "repo.W-90.runner.json"), busy)
        env["ASD_RUNNER_MAX_LIVE"] = "1"; env["ASD_RUNNER_SLOT_POLL"] = "0.2"
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-37")
        queued = until(lambda: state_of("W-37").get("state") == "queued", 20)
        check("slots: with every slot taken the run is queued, its claude not started", bool(queued) and not os.path.exists(os.path.join(fdir, "pid.1")))
        write_json(os.path.join(rdir, "repo.W-90.runner.json"), {**busy, "state": "waiting"})
        check("slots: a run waiting on the owner frees its slot; the queued one starts and finishes", p.wait(timeout=60) == 0 and any(t.startswith("slot free") for t in texts("W-37")))
        os.remove(os.path.join(rdir, "repo.W-90.runner.json"))
        env.pop("ASD_RUNNER_MAX_LIVE"); env.pop("ASD_RUNNER_SLOT_POLL")

        # ---- accounts bound to an email: a dir logged in as someone else, or as a twin, is skipped ----
        ah = os.path.join(tmp, "acchome"); os.makedirs(os.path.join(ah, "d1")); os.makedirs(os.path.join(ah, "d2")); os.makedirs(os.path.join(ah, "d3"))
        for d, em in (("d1", "work@x"), ("d2", "work@x"), ("d3", "me@y")):
            write_json(os.path.join(ah, d, ".claude.json"), {"oauthAccount": {"emailAddress": em}})
        accs = [{"name": "a", "dir": os.path.join(ah, "d1"), "email": "me@y"}, {"name": "b", "dir": os.path.join(ah, "d2")}, {"name": "c", "dir": os.path.join(ah, "d3")}]
        check("accounts: a dir logged in as another email than its own is not used", "not me@y" in account_problem(accs[0], accs))
        check("accounts: a twin of an earlier login is not used; a distinct one is", "same login" in account_problem(accs[2] if False else accs[1], [dict(accs[0], email=""), accs[1]]) and account_problem(accs[2], accs) == "")

        # ---- the orchestrator's model: pinned to an exact id, never Fable ----
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-34")
        check("model: the orchestrator gets --model claude-opus-5-5 by default", p.wait(timeout=60) == 0 and "--model claude-opus-5-5" in open(os.path.join(fdir, "env.1")).read())
        check("runner rules: time logging is settled off; references are read by section", "time logging is off" in open(os.path.join(fdir, "env.1")).read() and "ref_section" in open(os.path.join(fdir, "env.1")).read())
        bad = subprocess.run([sys.executable, me, "--root", root, "--dossier", "W-35", "--model", "claude-fable-5-1", "--foreground"], env=env, capture_output=True, text=True, timeout=30)
        check("model: Fable is refused before anything starts", bad.returncode != 0 and "not allowed" in (bad.stdout + bad.stderr))
        scenario([{"init_model": "claude-fable-5-1", "turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-36")
        asked = until(lambda: any("asked" in t for t in texts("W-36")), 30)
        check("model: a session that reports another model is stopped and the owner asked", bool(asked) and any("ran on claude-fable-5-1" in t for t in texts("W-36")))
        q36 = (state_of("W-36").get("questions") or [""])[0]
        owner_line(q36, "answer", "Stop", "B")
        p.wait(timeout=30)
        scenario([{"turns": [[{"subagent": "claude-sonnet-5-5", "final": "RUNNER: DONE"}]]}])
        p = start("W-37")
        p.wait(timeout=60)
        check("model: a Sonnet sub-agent's message does not stop the session", not any("ran on" in t for t in texts("W-37")) and state_of("W-37").get("state") == "done")
        scenario([{"turns": [[{"subagent": "claude-fable-5-1", "final": "RUNNER: DONE"}]]}])
        p = start("W-38")
        asked = until(lambda: any("asked" in t for t in texts("W-38")), 30)
        check("model: a Fable sub-agent still stops the session", bool(asked) and any("ran on claude-fable-5-1" in t for t in texts("W-38")))
        owner_line((state_of("W-38").get("questions") or [""])[0], "answer", "Stop", "B")
        p.wait(timeout=30)
        check("model: model_ok", model_ok("claude-opus-5-5", "claude-opus-5-5") and model_ok("sonnet", "claude-sonnet-5-5") and not model_ok("claude-opus-5-5", "claude-fable-5-1") and not model_ok("opus", "claude-fable-5-1"))

        # ---- a network error pauses and retries the stage by itself; only after the retries does it ask ----
        net = "API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)"
        check("network: ENOTFOUND and 5xx are network errors; a usage limit is not", network_error(net) and network_error("API Error: 503 Service Unavailable") and not network_error("usage limit reached") and not network_error("the contract is wrong"))
        scenario([{"turns": [[{"error": net}]]}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        env["ASD_RUNNER_NET_WAIT"] = "0.2"
        p = start("W-32")
        rc = p.wait(timeout=60)
        t32 = texts("W-32")
        check("network: paused, resumed and finished, with no question", rc == 0 and any(t.startswith("paused: network error") and "retry 1 of stage" in t for t in t32)
              and any(t.startswith("resumed after the network error") for t in t32) and not any("asked" in t for t in t32))
        scenario([{"turns": [[{"error": net}]]}, {"turns": [[{"error": net}]]}])
        env["ASD_RUNNER_NET_RETRIES"] = "1"
        p = start("W-33")
        asked = until(lambda: any("asked" in t for t in texts("W-33")), 30)
        check("network: after the retries it asks the owner, naming the network errors", bool(asked) and any("network errors on 1 retries" in t for t in texts("W-33")))
        q33 = (state_of("W-33").get("questions") or [""])[0]
        owner_line(q33, "answer", "Stop", "B")   # the owner rules Stop on the failure question
        check("network: the owner's Stop ends the run", p.wait(timeout=30) == 1)
        fakes = [int(open(os.path.join(fdir, f)).read()) for f in os.listdir(fdir) if f.startswith("pid.")]
        until(lambda: not any(alive(x) for x in fakes), 15)
        fakes = [int(open(os.path.join(fdir, f)).read()) for f in os.listdir(fdir) if f.startswith("pid.")]
        until(lambda: not any(alive(x) for x in fakes), 15)
        env.pop("ASD_RUNNER_NET_WAIT"); env.pop("ASD_RUNNER_NET_RETRIES")

        # ---- a reply on an open question the run is not waiting on is sent on mid-run ----
        scenario([{"turns": [[{"ask": 1, "dossier": "W-41", "final": "asked, carrying on with other work"}], [{"final": "RUNNER: DONE"}]]}])
        env.update(ASD_RUNNER_QFWD_EVERY="0.2", ASD_RUNNER_IDLE="60")
        p = start("W-41")
        q41 = "repo.W-41.Q1"
        until(lambda: os.path.exists(qfile(q41)) and any("carrying on" in x for x in texts("W-41") + [""]) or os.path.exists(os.path.join(fdir, "in.1.jsonl")), 20)
        time.sleep(0.5)
        owner_line(q41, "followup", "what exactly is the problem?")
        rc = p.wait(timeout=60)
        env.pop("ASD_RUNNER_QFWD_EVERY"); env["ASD_RUNNER_IDLE"] = "1"
        fwd = [m for m in msgs(1) if m.startswith(f"OWNER FOLLOWUP {q41}")]
        check("question reply: sent on mid-run, once, and the run goes on", rc == 0 and len(fwd) == 1 and "what exactly is the problem?" in fwd[0]
              and json.loads(open(qfile(q41)).readline()).get("run") == "repo.W-41")

        # ---- the stub breakpoint: wait (no tokens) until a stub can be built on, then hand it over ----
        st_fake = os.path.join(tmp, "stub_target_fake.py")
        open(st_fake, "w").write("import os,sys\nc=os.path.join(os.path.dirname(__file__),'st.count')\nn=int(open(c).read())+1 if os.path.exists(c) else 1\nopen(c,'w').write(str(n))\n"
                                 "print('wait stub/2026-11-03' if n < 2 else 'stub/2026-11-09'); sys.exit(3 if n < 2 else 0)\n")
        scenario([{"turns": [[{"final": "the stub is being integrated\nRUNNER: WAITING stub"}], [{"final": "RUNNER: DONE"}]]}])
        env.update(ASD_STUB_TARGET_BIN=st_fake, ASD_STUB_POLL="0.2")
        p = start("W-39")
        rc = p.wait(timeout=60)
        env.pop("ASD_STUB_TARGET_BIN"); env.pop("ASD_STUB_POLL")
        check("stub breakpoint: waits without a question, then sends STUB READY with the new stub",
              rc == 0 and any(t.startswith("waiting for the stub being integrated") for t in texts("W-39"))
              and any(m.startswith("STUB READY: stub/2026-11-09") for m in msgs(1)) and not any("asked" in t for t in texts("W-39")))

        # ---- a completion notice the CLI dropped is sent on, so a waiting session wakes ----
        tdir2 = os.path.join(tmp, "projects2", "-repo"); os.makedirs(tdir2, exist_ok=True)
        note = "<task-notification>\n<task-id>bxdrop1</task-id>\n<status>completed</status>\n</task-notification>"
        with open(os.path.join(tdir2, "sess-1.jsonl"), "w") as fh:
            fh.write(json.dumps({"type": "queue-operation", "operation": "enqueue", "content": note}) + "\n")
            fh.write(json.dumps({"type": "queue-operation", "operation": "remove", "content": note}) + "\n")
        scenario([{"turns": [[{"final": "waiting for the wave"}], [{"final": "RUNNER: DONE"}]]}])
        env.update(ASD_TRANSCRIPTS_DIR=os.path.join(tmp, "projects2"), ASD_RUNNER_FWD_EVERY="0.2", ASD_RUNNER_IDLE="60")
        p = start("W-38")
        rc = p.wait(timeout=60)
        for k in ("ASD_TRANSCRIPTS_DIR", "ASD_RUNNER_FWD_EVERY"): env.pop(k)
        env["ASD_RUNNER_IDLE"] = "1"
        check("dropped notice: sent on as a message, the session wakes and finishes, once",
              rc == 0 and sum(1 for m in msgs(1) if m.startswith("BACKGROUND TASK FINISHED") and "bxdrop1" in m) == 1
              and any(t.startswith("sent on 1 dropped background notice") for t in texts("W-38")))

        # ---- plan: one routed deferred item, under its own key beside the source dossier's run ----
        scenario([{"turns": [[{"final": "planned W-50\nRUNNER: DONE"}]]}])
        write_json(os.path.join(rdir, "repo.W-40.runner.json"), {"pid": os.getpid(), "state": "running"})  # the source's own run is live
        p = start("W-40", "--command", "plan", "--items", "D-1")
        rc = p.wait(timeout=60)
        check("plan: runs under <repo>.<ID>.plan-<D> while the source dossier's run is live", rc == 0 and state_of("W-40.plan-D-1").get("state") == "done")
        check("plan: the session gets /plan <ID> <D>", msgs(1)[0] == f"/{PLUGIN}:plan W-40 D-1")
        os.remove(os.path.join(rdir, "repo.W-40.runner.json"))
        bad = subprocess.run([sys.executable, me, "--root", root, "--dossier", "W-40", "--command", "plan", "--items", "D-1,D-2", "--foreground"], env=env, capture_output=True, text=True, timeout=30)
        check("plan: refused with more than one item", bad.returncode == 2)
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-41", "--command", "plan")
        check("plan: with no item it plans the dossier itself under its own key", p.wait(timeout=60) == 0 and msgs(1)[0] == f"/{PLUGIN}:plan W-41" and state_of("W-41").get("command") == "plan")

        # ---- #239: a stage end waits for background work the session launched ----
        J = lambda o: json.dumps(o)
        lines = [J({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "b1", "name": "Bash", "input": {"description": "Run the VM suite"}}]}}),
                 J({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "b1", "content": "Command running in background with ID: x1"}]}}),
                 J({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "w1", "name": "Workflow", "input": {"name": "p:phase4-wave"}}]}}),
                 J({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "w1", "content": "Workflow launched in background. Task ID: t"}]}}),
                 J({"type": "user", "message": {"content": "<task-notification>\n<task-id>t</task-id>\n<tool-use-id>w1</tool-use-id>\n</task-notification>"}}),
                 J({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "f1", "name": "Bash", "input": {"description": "fg"}}]}}),
                 J({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "f1", "content": "ok"}]}})]
        check("background: launched and not notified is in flight; notified or foreground is not", background_in_flight(lines) == ["Run the VM suite"])
        tdir = os.path.join(tmp, "projects", "-repo")
        os.makedirs(tdir, exist_ok=True)
        tr = os.path.join(tdir, "sess-1.jsonl")
        open(tr, "w").write("\n".join(lines[:2]) + "\n")
        note = J({"type": "user", "message": {"content": "<task-notification>\n<tool-use-id>b1</tool-use-id>\n</task-notification>"}})
        scenario([{"turns": [[{"checkpoint": ["Phase 3", "review spawned in the background"]}, {"final": "RUNNER: STAGE-DONE Phase 3"}],
                             [{"append": tr, "line": note, "final": "RUNNER: STAGE-DONE Phase 3"}]]},
                  {"turns": [[{"final": "RUNNER: DONE"}]]}])
        env["ASD_TRANSCRIPTS_DIR"] = os.path.join(tmp, "projects")
        p = start("W-31")
        rc = p.wait(timeout=60)
        env.pop("ASD_TRANSCRIPTS_DIR")
        held = [t for t in texts("W-31") if t.startswith("stage end held")]
        check("background: the stage end is held while it runs, then the run goes on", rc == 0 and len(held) == 1 and "Run the VM suite" in held[0]
              and any(m.startswith("RUNNER: the stage cannot end yet") for m in msgs(1)))

        # ---- the owner's message to a live run: wakes a question wait, the question stays open ----
        q30 = "repo.W-30.Q1"
        scenario([{"turns": [[{"ask": 1, "dossier": "W-30", "final": "RUNNER: WAITING {id}"}],
                             [{"final": "noted\nRUNNER: WAITING " + q30}],
                             [{"final": "RUNNER: DONE"}]]}])
        p = start("W-30")
        until(lambda: state_of("W-30").get("state") == "waiting")
        r = subprocess.run([sys.executable, me, "chat", "W-30", "--root", root, "--text", "I answered Q0 wrong: use B"], env=env, capture_output=True, text=True, timeout=30)
        check("message: chat on a live run is queued for it", r.returncode == 0 and "running agent gets it" in r.stdout)
        got = until(lambda: [m for m in msgs(1) if m.startswith("OWNER MESSAGE")] if os.path.exists(os.path.join(fdir, "in.1.jsonl")) else None)
        check("message: the waiting session gets it as OWNER MESSAGE", bool(got) and "use B" in got[0])
        check("message: the runner waits on the question again, which stays open",
              bool(until(lambda: state_of("W-30").get("state") == "waiting" and len(msgs(1)) >= 2)) and not any(x.get("from") == "owner" for x in lines_of(qfile(q30))))
        owner_line(q30, "answer", "A", "A")
        check("message: the answer still goes through and the run finishes", p.wait(timeout=60) == 0 and state_of("W-30").get("inbox_seen") == 1)
        check("message: the timeline has the owner's message", any(x.get("event") == "owner-message" for x in lines_of(os.path.join(rdir, "repo.W-30.jsonl"))))

        # Before the temp dir goes: every fake claude process and runner child this test started must be gone.
        for _ in range(100):
            pids = [int(open(os.path.join(fdir, f)).read() or 0) for f in os.listdir(fdir) if f.startswith("pid.")]
            if not any(alive(x) for x in pids):
                break
            time.sleep(0.1)
        time.sleep(0.3)

    print("FAILED: " + ", ".join(failures) if failures else "all ok")
    return 1 if failures else 0


def repo_of(cwd: str) -> str | None:
    """The repository root of `cwd` (its git toplevel), or None outside a repository."""
    try:
        out = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def detach(argv: list[str], root: str, dossier: str, key: str = "") -> int:
    """Start this runner again as a background process, its output in `<key>.runner.log`; print the pid."""
    key = key or key_of(root, dossier)
    log = os.path.join(runs_dir(), key + ".runner.log")
    os.makedirs(runs_dir(), exist_ok=True)
    args = [sys.executable, os.path.abspath(__file__)] + [a for a in argv if a != "--detach"] + ["--foreground"]
    with open(log, "ab") as fh:
        p = subprocess.Popen(args, cwd=root, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                             start_new_session=True)
    say(f"started {key} in the background, pid {p.pid}; log {log}")
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
    if argv and argv[0] == "revive":
        return cmd_revive()
    if argv and argv[0] == "usage":
        ap = argparse.ArgumentParser(prog="run_dossier.py usage")
        ap.add_argument("--account")
        return cmd_usage(ap.parse_args(argv[1:]))
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
    ap.add_argument("--command", choices=["work-on", "stub", "fix-deferred", "after-merge", "plan", "resolve-merge"], default="work-on")
    ap.add_argument("--model")
    ap.add_argument("--permission-mode", default="auto")
    ap.add_argument("--max-stages", type=int, default=20)
    ap.add_argument("--items", default="", help="fix-deferred: the D-ids to fix, e.g. D-1,D-3 (default: every open item); after-merge: the merged PR numbers, e.g. 429,430")
    ap.add_argument("--detach", action="store_true", help="the default: run in the background; output to <key>.runner.log")
    ap.add_argument("--foreground", action="store_true", help="run in this terminal instead of the background")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not resolve(a, ap) or not a.root or not a.dossier:
        ap.print_usage(sys.stderr)
        return 2
    items = ",".join(i for i in re.split(r"[,\s]+", a.items or "") if i)
    if items and not ((a.command == "fix-deferred" and re.fullmatch(r"D-\d+(?:,D-\d+)*", items))
                      or (a.command in ("after-merge", "resolve-merge") and re.fullmatch(r"\d+(?:,\d+)*", items))
                      or (a.command == "plan" and re.fullmatch(r"D-\d+", items))
                      or (a.command == "stub" and re.fullmatch(r"(?:stub|release)/[\w.-]+", items))):
        print("run_dossier.py: --items takes D-ids (D-1,D-3) with --command fix-deferred, PR numbers (429,430) with --command after-merge, "
              "or one D-id with --command plan", file=sys.stderr)
        return 2
    runner_model(a.model)  # refuses a forbidden model before anything starts
    if a.command == "work-on":
        why = already_built(a.root, a.dossier)
        if why:
            print(f"run_dossier.py: not starting /work-on: {why}", file=sys.stderr)
            return 2
    if not a.foreground:
        return detach(argv + ([] if "--root" in argv else ["--root", a.root]), a.root, a.dossier, run_key(a.root, a.dossier, a.command, items))
    return Runner(a.root, a.dossier, a.command, a.model, a.permission_mode, a.max_stages, items).run()


if __name__ == "__main__":
    sys.exit(main())
