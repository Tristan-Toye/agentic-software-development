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
    RUNNER: ENV <vm|stack|disk> <detail>
                                a broken environment (the Lima VM down, a compose service unhealthy, the disk low),
                                reported instead of asked about; with ASD_STACK_REPAIR=1 the runner repairs vm and
                                stack (scripts/stack_repair.py) and answers `RUNNER ENV <kind>: repaired ..` or
                                `.. not repaired ..`; the turn goes on, no stage ends
    RUNNER: DONE                state done, exit 0
    RUNNER: REPLAN <why>        an owner ruling (or the contract) sends the
                                dossier back to /plan: the run ends done, the
                                dossier is set to planned, and a /plan run of
                                the dossier starts (queued for a slot) with no
                                question to the owner
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
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

CUT = 3500
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stack_repair  # noqa: E402
ASK_OWNER = os.path.join(HERE, "ask_owner.py")
PLUGIN = "agentic-software-development"

TIME_LOGGING_OFF = ("time logging is off: do not log time to Tempo, do not ask the owner about it, and do not read "
                    "references/time-logging.md (ASD_RUNNER_TIME_LOGGING=on turns the gate back on).")
TIME_LOGGING_ON = "time logging is on: follow references/time-logging.md once, in the first stage only."
# Added to the rules while the target repo's own CLAUDE.md is left out of the session (ASD_RUNNER_LEAN_CLAUDEMD): that
# file is the repo's router to its rules and its environment notes, so the rules point at it instead of copying it.
CLAUDE_MD_POINTER = ("- The target repo's own CLAUDE.md is not loaded in this session (it costs ~5k tokens in every session and sub-agent). "
                     "It routes to the repo's binding rules (a ledger such as docs/learned-rules.md: read its entries whose scope matches, "
                     "in Phase 2 and again in Phase 8b) and holds its build and test environment notes (PATH, database, VM). Read the "
                     "section you need with `grep -n '^## ' CLAUDE.md`, then Read that range, when you start a phase that needs the repo's "
                     "rules, or when a command fails on the environment; never the whole file.\n")
RUNNER_RULES = """\
You run under the dossier runner and nobody reads the terminal.
- Ask the owner only through scripts/ask_owner.py, and never wait in the background: keep working on whatever is not blocked; when nothing can proceed, end the turn with the line `RUNNER: WAITING <id>[,<id>...]` and stop. The ids are question ids from ask_owner.py only; a wait on a background command or workflow is not a WAITING line: just end the turn, and Claude Code re-invokes you when it finishes. The runner waits for you and sends the owner's message into this session as `OWNER ANSWER <id>: ...` or `OWNER FOLLOWUP <id>: ...`. A follow-up is the owner's typed message and the question stays open, as a chat, until the owner presses a button: if it is a question, answer it with `ask_owner.py reply <id> --text "..."`; if it reads as an answer or an instruction, reply with your reading and add it as a button, `ask_owner.py reply <id> --text "<what you take it to mean, and what you will do>" --option "D|Confirm: <your reading in a few words>|<what happens>"` (a key the question does not have yet); if it is unclear, reply with your question. When what you recommend changes (a measurement, the owner's point), say so and move the highlighted button with `--recommend <key>` on that reply. Then end the turn with `RUNNER: WAITING <id>` again. Never close a question yourself: only the owner's button does, and every question ends with one.
- Never end a turn to wait for a command: run commands in the foreground (raise the Bash timeout up to 10 minutes) or wait for them inside the same turn. A workflow or background task that notifies you later is fine, but say `waiting for <what>` in plain text in that turn (never as a `RUNNER:` line); the runner keeps the session open for its notification.
- When the auto-mode safety check blocks a call you need, never work around it: ask the owner with `ask_owner.py ask`, quoting the exact command (or path) and what it is for, with options to allow it once, to allow it as a standing rule, or to go without it; carry on with what is not blocked. The dashboard's Blocked card answers that question; an approval names the exact command, so retry it as written.
- Run every test through scripts/run_tests.py and every gate list through scripts/run_gates.py (`--path-prepend DIR` / `--env-script FILE` for a toolchain off the shell PATH, `--detach` then `--wait ID` for a long run), never through a script or command of your own: their result lines are how the owner's timeline shows a suite going green or red. Run only the tests the command's step names: under /work-on and /fix-deferred with SUITE-MODE `stub`, that is the new and affected tests and the pull request's CI, never the full workspace or `--stack` suite, which only /stub runs.
- Never end a stage (or the run) while a background command, workflow or agent you started is still running: it dies with this process and no notification reaches the next stage. Wait for its notification and take its result first; the runner holds a stage end that comes too early, once. A task you stopped or killed yourself needs no wait: end the stage, and the runner cuts off whatever is left and logs it. Never ask the owner whether to end the stage, restart or stop a background task. If a checkpoint must still name work in flight, say how the next stage tells it finished or died (its report file, its branch or worktree, its output file).
- Gates already settled for this run: the worktree gate is yes, and {time_logging}
- The owner's Start is standing authority (references/owner-questions.md, "What is worth asking"): pushing the dossier's own branch and opening or updating its draft PR into the base or the accepting stub is yes; so is a graduation issue in the plugin repository, and committing a repository rule from this build on the dossier's branch (the PR ratifies it). Carry out the readings, defaults and rulings the dossier already records; never ask to confirm them. Ask only technical, architectural or product design questions the dossier leaves open, issues you cannot resolve in scope, and actions outside that authority (a merge, a push to a protected branch, a force-push, Jira writes, deleting what is not the run's own). Do the authorized ones, log them, and name them in your next update.
- The plugin's references (`references/work-on/phase-*.md`, `formats/*`, `runner.md`) are long and the same in every stage: read only the section your step needs (`ref_section` with the document's short name, `phase-4` or `formats/dossier`, and a heading or `--list`), not whole files; the phase file you start a phase with is the one you read whole, and never the same section twice in one stage.
- Phase 2 takes its stub from `python3 scripts/stub_target.py --root R` (the plugin's scripts). Exit 3 (`wait <stub>`) means that stub is being integrated and no new one may be cut until it is in development: end the turn with the line `RUNNER: WAITING stub`; the runner waits without tokens and sends `STUB READY: <branch>` once the new stub is cut. Never cut a stub yourself.
- A broken environment (the Lima VM stopped or unreachable, a shared docker compose service unhealthy, the disk low) is not an owner question: end the turn with the single line `RUNNER: ENV <vm|stack|disk> <short detail>` and nothing after it. The runner repairs vm and stack when the owner enabled that and answers `RUNNER ENV <kind>: repaired ...` (carry on) or `RUNNER ENV <kind>: not repaired ...` (then ask the owner as usual). A disk is never repaired automatically.
- End each stage with its phase_checkpoint call and then, as the last line of your reply, `RUNNER: STAGE-DONE <stage name>`, and stop. The next stage starts in a new process with your checkpoints as its hand-off.
- `RUNNER KEEPALIVE: ...` is the runner keeping the prompt cache warm while you wait: reply with exactly `OK` and do nothing else (no tool call, no RUNNER: line, no change to your plan).
{claude_md}- End with `RUNNER: DONE` when /work-on is finished, and with `RUNNER: FAILED <why>` when you cannot go on. When the owner rules the dossier back to /plan (or the build shows it must be re-planned and the owner agreed), record the ruling and the inputs for the re-plan in the dossier, leave nothing half-built behind, and end with `RUNNER: REPLAN <why>`: the runner sets the dossier to planned and starts /plan itself. Never end such a run with FAILED.
- Keep the owner informed: post an update with `ask_owner.py note --root R --dossier ID --text "..."` when a stage starts and ends and whenever something worth knowing happens (a wave returned, the suite went green or red, an arbitration was ruled, a plan changed). Write it for a person reading on a phone: what happened and what it means, in plain sentences; markdown bullets and small tables are welcome. Never paste commands, file paths, SHAs, flags or log output into an update; the build log keeps those.
"""

def runner_rules() -> str:
    """The appended rules, with the run's settled gates: time logging is off unless ASD_RUNNER_TIME_LOGGING=on.
    Every stage is a new session running the command again, so a gate the command opens with ("time logging first")
    would otherwise be read, and asked, at every stage."""
    on = (os.environ.get("ASD_RUNNER_TIME_LOGGING") or "off").strip().lower() in ("on", "1", "true", "yes")
    return (RUNNER_RULES.replace("{time_logging}", TIME_LOGGING_ON if on else TIME_LOGGING_OFF)
            .replace("{claude_md}", CLAUDE_MD_POINTER if lean_on("CLAUDEMD") else ""))


LIMIT_RE = re.compile(r"usage limit|rate.?limit|weekly limit|limit reached|out of (?:extra )?usage|hit your .*limit|\b429\b|overloaded", re.I)


NETWORK_RE = re.compile(r"ENOTFOUND|EAI_AGAIN|ECONNRESET|ECONNREFUSED|ETIMEDOUT|EHOSTUNREACH|ENETUNREACH|EPIPE"
                        r"|can'?t reach the API|couldn'?t reach Anthropic|unable to connect|could not connect|fetch failed|socket hang ?up|network (?:error|is unreachable)"
                        r"|connection (?:error|reset|refused|closed)|timed? ?out|API Error: 5\d\d|\b50[0234]\b|internal server error"
                        r"|bad gateway|service unavailable|gateway time-?out", re.I)
# Waits between network retries, seconds; the last one repeats for as long as the outage lasts (ASD_RUNNER_NET_RETRIES
# sets a limit; the default, 0, is none).
GIT_SSH_HARDENED = "ssh -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
NETWORK_BACKOFF = (30, 60, 120, 300, 600, 900)
# The exit codes of a claude process that something outside the runner signalled: SIGTERM/SIGKILL as a negative
# return code, or as 128+N when claude's own handler exits. The work it held is checkpointed; the stage reruns.
KILLED_CODES = (-signal.SIGTERM, -signal.SIGKILL, -signal.SIGHUP, 128 + signal.SIGTERM, 128 + signal.SIGKILL, 128 + signal.SIGHUP)
KILL_BACKOFF = (60, 300)
# What a resumed session is told: it is the same session, so no hand-off, only the fact of the interruption.
RESUME_MSG = ("RUNNER RESUME: your process was interrupted from outside (a kill, a network error or a usage limit), not by "
              "anything you did. This is the same session: carry on from the exact point you stopped at, run again the tool "
              "call that was cut off if it did not finish, and do not redo finished work. Task: {task}. The same RUNNER: "
              "rules apply.")


def script_stamp(path: str) -> tuple[str, float]:
    """(the runner script a fresh start would use, its mtime). Under a plugin cache (.../<version>/scripts/) a plugin
    update installs a new version directory beside the old one: the highest version with this script wins."""
    path = os.path.abspath(path)
    scripts = os.path.dirname(path)
    ver_dir = os.path.dirname(scripts)
    parent = os.path.dirname(ver_dir)
    def vkey(n: str) -> tuple:
        return tuple(int(x) if x.isdigit() else -1 for x in re.split(r"[.-]", n))
    if re.fullmatch(r"\d+(?:\.\d+)+", os.path.basename(ver_dir) or ""):
        try:
            vers = [n for n in os.listdir(parent) if re.fullmatch(r"\d+(?:\.\d+)+", n)
                    and os.path.isfile(os.path.join(parent, n, os.path.basename(scripts), os.path.basename(path)))]
        except OSError:
            vers = []
        if vers:
            path = os.path.join(parent, max(vers, key=vkey), os.path.basename(scripts), os.path.basename(path))
    try:
        return path, os.stat(path).st_mtime
    except OSError:
        return path, 0.0


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
        return
    learn_burn(account, wins, now_s())


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


# The line a keep-alive ping sends (documented in RUNNER_RULES and references/runner.md): the agent answers OK and stops.
KEEPALIVE_MSG = "RUNNER KEEPALIVE: reply with exactly OK and do nothing else."
NUDGE = ("RUNNER: your last turn ended without a RUNNER: line and nothing has happened since. "
         "If you were waiting on a background command or workflow, check its result now and continue. "
         "Every turn must end with a RUNNER: line (STAGE-DONE, WAITING, DONE, REPLAN or FAILED).")

# A question id from ask_owner.py: `<repo>.<ID>.Qn` (or a bare `Qn`).
QUESTION_ID = re.compile(r"(?:^|\.)Q\d+$")
MARKER = re.compile(r"^RUNNER:\s*(STAGE-DONE|WAITING|DONE|REPLAN|FAILED|ENV)\b[ \t]*(.*)$")
# A FAILED whose reason says the owner sent the dossier back to /plan is a REPLAN under an older wording.
REPLAN_WORDS = re.compile(r"\b(sent|send|sending|go(?:es|ing)?)\s+back\s+to\s+/?plan\b|\bre-?plan(?:ned|ning)?\b.*\b(ruling|ruled|owner)\b", re.I)


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


def proc_command(pid) -> str:
    """The command line of a process ("" when it is gone or ps cannot say)."""
    try:
        return subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


def runner_alive(pid) -> bool:
    """A runner process is alive: the pid answers AND it is a run_dossier process. After a logout or a reboot the dead
    runner's pid can belong to an unrelated process; `alive` alone would then keep the run from ever being revived."""
    if not alive(pid):
        return False
    cmd = proc_command(pid)
    return not cmd or "run_dossier" in cmd   # ps unable to say: believe the pid


def say(msg: str) -> None:
    print(f"[run_dossier] {msg}", flush=True)


def user_message(text: str) -> bytes:
    msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]},
           "parent_tool_use_id": None}
    return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")


def set_dossier_status(root: str, dossier: str, status: str, log=lambda m: None) -> bool:
    """Set `status:` in the dossier's front matter (.discovery/dossiers/<ID>-*.md), when it differs. Best effort."""
    d = os.path.join(root, ".discovery", "dossiers")
    try:
        names = sorted(n for n in os.listdir(d) if re.match(re.escape(dossier) + r"(-.*)?\.md$", n))
    except OSError:
        return False
    if not names:
        return False
    path = os.path.join(d, names[0])
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return False
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return False
    fm = m.group(1)
    new = re.sub(r"(?m)^status:[ \t]*\S.*$", f"status: {status}", fm, count=1)
    if new == fm:
        return True
    open(path, "w", encoding="utf-8").write(f"---\n{new}\n---\n" + text[m.end():])
    log(f"dossier {dossier}: status set to {status}")
    return True


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
BG_TASK_ID = re.compile(r"\bID:\s*([\w-]+)")
# A task stopped on purpose (TaskStop, KillShell) never sends a notification: its stop result, or a stop that finds
# the task already gone, ends it.
BG_STOPPED = re.compile(r"(?:Successfully (?:stopped|killed|terminated|cancell?ed) (?:task|shell|background)|(?:task|shell) [\w-]+ (?:was |is )?(?:already )?(?:stopped|killed|terminated|not running|completed|finished)|No (?:task|shell|background \w+) (?:found )?with ID)[^\w-]*([\w-]+)?", re.I)
# The tools that stop a task by id: the id is in the call's input, whatever shape the result takes.
BG_STOP_TOOLS = {"TaskStop": "task_id", "KillShell": "shell_id", "KillBash": "shell_id", "TaskKill": "task_id"}
BG_OUTPUT = re.compile(r"written to:?\s*(\S+?)\.?(?:\s|$)")


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


def background_tasks(lines: list[str]) -> list[dict]:
    """The background commands, workflows and agents a session launched that have not notified it yet, each as
    {"label", "id", "output"}: a tool call whose result says it went to the background, minus every
    `<task-notification>` naming its id and every task stopped on purpose (a stop sends no notification). A stop is
    read from the stop call's own input (TaskStop task_id), so any result text that is not an error ends the task, and
    the "stopped" / "No task found" texts end it too."""
    calls: dict[str, str] = {}
    stops: dict[str, str] = {}      # a stop call's tool_use id -> the task id it names
    bg: dict[str, dict] = {}
    task_of: dict[str, str] = {}   # background task id -> the tool_use id that launched it
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
                    key = BG_STOP_TOOLS.get(str(b.get("name") or ""))
                    if key and i.get(key):
                        stops[b["id"]] = str(i[key])
        elif o.get("type") == "user":
            texts = [c] if isinstance(c, str) else []
            for b in c if isinstance(c, list) else []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result" and b.get("tool_use_id") in calls:
                    t = b.get("content") if isinstance(b.get("content"), str) else json.dumps(b.get("content") or "")
                    tid = b["tool_use_id"]
                    if BG_LAUNCHED.search(t[:400]):
                        m = BG_TASK_ID.search(t[:400])
                        out = BG_OUTPUT.search(t[:600])
                        bg[tid] = {"label": calls[tid], "id": m.group(1) if m else "", "output": out.group(1) if out else ""}
                        if m:
                            task_of[m.group(1)] = tid
                    else:
                        m = BG_STOPPED.search(t[:400])
                        named = stops.get(tid) or (m.group(1) if m else None)
                        # a stop call that did not fail ends the task it names; a failed one only when the task was already gone
                        if named in task_of and (m or (tid in stops and not b.get("is_error"))):
                            bg.pop(task_of[named], None)
                elif b.get("type") == "text" and isinstance(b.get("text"), str):
                    texts.append(b["text"])
            for t in texts:
                for m in BG_NOTIFIED.finditer(t):
                    bg.pop(m.group(1), None)
        elif o.get("type") == "queue-operation" and isinstance(o.get("content"), str):
            for m in BG_NOTIFIED.finditer(o["content"]):
                bg.pop(m.group(1), None)
    return list(bg.values())


def background_in_flight(lines: list[str]) -> list[str]:
    """Labels of `background_tasks`."""
    return [t["label"] for t in background_tasks(lines)]


def output_unheld(path: str) -> bool:
    """True when a background command's output file is held open by no process: a running command writes into it, so
    nobody holding it means the command is gone (killed with `kill`, or its stop lost). False when it cannot tell
    (no lsof, the file is missing, the path is not under a tasks directory): then the task counts as running."""
    if not path or "/tasks/" not in path or not os.path.isfile(path):
        return False
    try:
        r = subprocess.run(["lsof", "-t", "--", path], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 1 and not r.stdout.strip()


HOLD_STAGE = ("RUNNER: the stage cannot end yet. Background work you started is still running and would die with this "
              "process, with no notice to the next stage: {what}. Wait for its notification (end this turn with no RUNNER: "
              "line; Claude Code re-invokes you when it finishes), take its result, then end the stage again. If it will "
              "not finish, or you already stopped it, just end the stage again: the runner ends it and logs what it cut "
              "off. This is not a question for the owner: never ask whether to end the stage, restart or stop the task.")
HOLD_MAX = 1   # after this many held ends (one: a task that never notifies is stale background work, not a reason to ask) the runner lets the stage end, and logs what it cut off


# ---- the orchestrator's model: pinned, never an alias, never Fable ----
# "opus" is an alias each login resolves for itself: on one account it resolved to claude-fable-5-1, at 2.5x the
# price, and ran every orchestrator on it for a day. The runner passes an exact id and checks the one the session
# reports; ASD_RUNNER_MODEL overrides it (an Opus or Sonnet id).
DEFAULT_MODEL = "claude-opus-5-5"

# ---- a smaller fixed prefix for runner sessions (ASD_RUNNER_LEAN, default on; "0" = the argv of 1.52.35 and before) ----
# Measured on config-harness with one haiku call each: 30.8k tokens before, 25.3k after (see references/runner.md).
# Tools the pipeline never calls (grep of commands, skills, agents, references, hooks): their schemas and names ride
# in every call. The nav and runs MCP servers, Workflow, Monitor, SendMessage, LSP, ToolSearch, Web* all stay.
LEAN_DISALLOWED = ["CronCreate", "CronDelete", "CronList", "DesignSync", "ListAgents", "PublishPlugin", "PushNotification",
                   "RemoteTrigger", "ReportFindings", "ScheduleWakeup", "ShareOnboardingGuide", "NotebookEdit",
                   "EnterWorktree", "ExitWorktree"]
# Claude Code's bundled skills: listed by name only (skillOverrides "name-only"; "off" would widen the budget of the
# plugin's own skills and cost more). The plugin's skills are not touched.
LEAN_NAME_ONLY = ["deep-research", "design", "design-sync", "dataviz", "update-config", "verify", "debug", "code-review",
                  "simplify", "batch", "fewer-permission-prompts", "doctor", "loop", "schedule", "claude-api",
                  "workflow-authoring", "run", "run-skill-generator", "plugin-authoring"]
LEAN_PLUGINS_OFF = ["context-mode@context-mode"]
LEAN_PLUGIN_CONTEXT7 = "context7@claude-plugins-official"
COMPACT_WINDOW_DEFAULT = 250000
DESC_CAP = 460   # the longest agent, command or skill description the selftest allows (measured prefix cost, runner.md)
SPLIT_AT_DEFAULT = 135000
# Models whose context window is 200k: a larger auto-compact window is meaningless there (the CLI caps it at the
# window), so none is passed. claude-opus-5-5 and claude-sonnet-5-5 report a 1M window (modelUsage.contextWindow).
SMALL_WINDOW = re.compile(r"haiku-[34]|claude-3|opus-4|sonnet-4|\[200k\]", re.I)


# ---- the owner's standing "Always allow" rulings, kept per repo --------------------------------------------------
# The dashboard writes `<repo>.json` in $ASD_RULINGS_DIR (default ~/.agentic/rulings): {"repo", "rules": [{"rule",
# "kind": "allow", "by", "ts", "question", "run", "tool", "target"}]}. Every claude session of the runner for that repo
# gets them as `permissions.allow` in --settings (claude_cmd), together with `permissions.deny` for the categories the
# owner's agents never do (merging a PR, a force or protected-branch push, a secret path); a deny rule outranks an allow
# rule in Claude Code, and a stored rule that could cover such a call is dropped here too, whoever wrote it.
RULING_DENY = ["Bash(gh pr merge:*)", "Bash(gh api:* /merge*)", "Bash(git push --force:*)", "Bash(git push -f:*)",
               "Bash(git push --force-with-lease:*)", "Bash(git push --mirror:*)", "Bash(git push * production:*)",
               "Bash(git push * main:*)", "Bash(git push * master:*)", "Bash(git push * development:*)",
               "Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.gnupg/**)"]
RULING_PROBES = ["gh pr merge 1", "gh pr merge 1 --squash --auto", "gh api -X PUT repos/o/r/pulls/1/merge",
                 "gh api graphql -f query=mutation{mergePullRequest}", "git push --force", "git push -f origin x",
                 "git push --force-with-lease origin x", "git push --mirror", "git push origin production",
                 "git push origin main", "git push origin master", "git push origin development",
                 "git push origin --delete production", "cat ~/.ssh/id_rsa", "cat ~/.aws/credentials", "sops -d secrets.yaml"]
SECRET_RULE_RE = re.compile(r"\.ssh|\.aws|\.gnupg|id_rsa|id_ed25519|\.env\b|secrets?\.|credentials|keychain", re.I)


def rulings_dir() -> str:
    return os.environ.get("ASD_RULINGS_DIR") or os.path.expanduser("~/.agentic/rulings")


def ruling_refused(rule: str) -> str:
    """Why a stored rule must never apply ("" when it may): it would allow, or could cover, a call in the refused categories."""
    m = re.match(r"^([A-Z][A-Za-z]+)\((.+)\)$", (rule or "").strip(), re.S)
    if not m:
        return "not a permission rule"
    tool, inner = m.group(1), m.group(2).strip()
    if not inner or re.fullmatch(r"\*+", inner) or inner.startswith("* "):
        return "allows everything"
    if tool != "Bash":
        return "names a secret path" if SECRET_RULE_RE.search(inner) else ""
    pat = re.escape(inner.replace(":*", " *")).replace(r"\*", ".*")
    rx = re.compile(pat, re.S)
    for probe in RULING_PROBES:
        if rx.fullmatch(probe) or rx.fullmatch(probe + " origin x") or rx.match(probe):
            return "covers a call the runner refuses: " + probe
    return ""


def repo_rulings(repo: str) -> list[str]:
    """The allow rules the owner saved with "Always allow" for `repo`, minus any that fail ruling_refused."""
    doc = read_json(os.path.join(rulings_dir(), f"{repo}.json"), {}) or {}
    out: list[str] = []
    for r in doc.get("rules") or []:
        rule = str(r.get("rule", "") if isinstance(r, dict) else r).strip()
        if rule and rule not in out and not ruling_refused(rule):
            out.append(rule)
    return out



def lean_on(name: str) -> bool:
    """A lean runner session's opt-out `name` (CLAUDEMD, GIT, MEMORY, CONTEXT7) is on unless ASD_RUNNER_LEAN is 0 or
    ASD_RUNNER_LEAN_<NAME> is 0."""
    return (os.environ.get("ASD_RUNNER_LEAN") or "1").strip() != "0" and (os.environ.get(f"ASD_RUNNER_LEAN_{name}") or "1").strip() != "0"


def lean_settings(root: str | None = None) -> str:
    """The --settings JSON of a lean runner session: it merges over the project's and the user's settings.
    `claudeMdExcludes` skips the target repo's own CLAUDE.md (its worktrees too) in the session and in every sub-agent it
    spawns (measured); the user's and the org's CLAUDE.md stay. The context7 plugin is left out; org-managed plugins are
    not touched."""
    plugins = list(LEAN_PLUGINS_OFF) + ([LEAN_PLUGIN_CONTEXT7] if lean_on("CONTEXT7") else [])
    cfg: dict = {"enabledPlugins": {p: False for p in plugins},
                 "skillOverrides": {k: "name-only" for k in LEAN_NAME_ONLY}}
    if root and lean_on("CLAUDEMD"):
        r = os.path.abspath(root).rstrip("/")
        cfg["claudeMdExcludes"] = [f"{r}/CLAUDE.md", f"{r}-*/CLAUDE.md"]
    return json.dumps(cfg, separators=(",", ":"))


def lean_env() -> dict:
    """The environment variables of a lean runner session. Telemetry is left on (no DISABLE_NONESSENTIAL_TRAFFIC)."""
    env = {}
    if lean_on("GIT"):
        env["CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS"] = "1"   # the built-in commit and PR instructions and the git status snapshot: the pipeline commits through gate_commit.py
    if lean_on("MEMORY"):
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    return env


def compact_window(model: str) -> int:
    """The --autocompact window for a runner session on `model`; 0 = pass none."""
    n = int(env_num("ASD_RUNNER_COMPACT_WINDOW", COMPACT_WINDOW_DEFAULT, int))
    if n <= 0 or SMALL_WINDOW.search(model or ""):
        return 0
    return min(max(n, 100000), 1000000)
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


# ---- run slots: at most ASD_RUNNER_MAX_LIVE runs work at once (default 4; see "slots that follow the budget" below) ----
# Ten runs resuming at one limit reset spent a whole 5-hour window in 12 minutes. A run takes a slot before each
# stage and after an owner's answer; only `running` runs hold one (waiting, paused and queued runs spend nothing).
# Without a free slot it records `queued` and waits its turn: the longest-queued run goes first.
# A `running` run whose session is blocked on a long test or gate wait records `testing` and releases its slot: the
# session spends nothing while it waits (podcast W-007 sat 35 min in the foreground on run_tests.py holding a slot
# while queued runs idled). Trade-off, accepted by the owner: when the wait ends the run goes straight back to
# `running` WITHOUT queueing, so the live count may briefly exceed the cap until another run finishes or waits.

TESTING_QUIET_SECS = 300    # an in-flight Bash call with no output for longer than this counts as a long wait
TESTING_CMD = re.compile(r"\brun_(?:tests|gates)\.py\b")   # the long waits we know by name (any flags, `--wait` included)
LIVE_STATES = ("running", "testing", "waiting", "queued")   # a runner in one of these is alive (paused ones are checked separately)


def max_live() -> int:
    try:
        return max(0, int(os.environ.get("ASD_RUNNER_MAX_LIVE") or 4))
    except ValueError:
        return 4


# ---- slots that follow the budget (pacing) ----
# Unless ASD_RUNNER_ADAPTIVE=0 or ASD_RUNNER_MAX_LIVE is set, the number of runs that may work at once follows what the
# accounts have left: for each usable account, how many runs fit in the rest of its 5-hour and weekly windows at the
# observed burn rate, summed, clamped to ASD_RUNNER_MIN_LIVE..ASD_RUNNER_CAP_MAX (3..6). A lower cap only stops
# admissions; a run that works is never preempted. The cap is cached for 60 s in `slots-cap.json` in the runs dir (all
# runners agree; the dashboard shows it).
#
# BURN_PRIOR, percentage points of a window used per hour by ONE running run, calibrated on 2026-11-10 from this
# machine's history: the 41 runs' `state` events in ~/.agentic/runs/*.jsonl give how many runs were `running` over time
# (350 running intervals, 435 run-hours); ~/.claude/usage-log.jsonl gives the usage percentages over time.
#   five_hour: 613 consecutive log pairs in one window, <= 2 h apart, with >= 1 run running: +1377 points over 208
#     run-hours = 6.6 points per run-hour (it includes the owner's interactive use, so slightly high); per-window
#     highs give a median of 4.7 points per run-hour, but 9 of 11 windows hit 100 (a lower bound). Prior: 6.0.
#   seven_day: the same pairs give +121 points over 208 run-hours = 0.58; the week of 11-06 (high 100, 168 run-hours)
#     gives 0.60. Prior: 0.5.
# Both are replaced by an EWMA of what the usage readings show (burn.json), so the prior only matters at the start.
BURN_PRIOR = {"five_hour": 6.0, "seven_day": 0.5}
BURN_ALPHA = 0.2
PACE_MARGIN = {"five_hour": 10.0, "seven_day": 5.0}
WINDOW_HOURS = {"five_hour": 5.0, "seven_day": 168.0}
BURN_FILE = lambda: os.path.join(USAGE_DIR(), "burn.json")


def now_s() -> float:
    """The clock of pacing and keep-alive; ASD_RUNNER_NOW (epoch seconds) is the tests' knob."""
    try:
        return float(os.environ["ASD_RUNNER_NOW"])
    except (KeyError, ValueError):
        return time.time()


def env_num(name: str, default: float, conv=float):
    try:
        return conv(os.environ.get(name) or default)
    except ValueError:
        return default


def fixed_slots() -> bool:
    """The old behaviour: ASD_RUNNER_ADAPTIVE=0, or a cap the owner set with ASD_RUNNER_MAX_LIVE."""
    return (os.environ.get("ASD_RUNNER_ADAPTIVE") or "").strip() == "0" or bool((os.environ.get("ASD_RUNNER_MAX_LIVE") or "").strip())


def epoch_of(s) -> float | None:
    try:
        return datetime.datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return None


def running_by_account() -> dict[str, int]:
    """Runs in state `running` with a live runner, by the account they use."""
    n: dict[str, int] = {}
    try:
        names = [x for x in os.listdir(runs_dir()) if x.endswith(".runner.json")]
    except OSError:
        return n
    for x in names:
        st = read_json(os.path.join(runs_dir(), x), {}) or {}
        if st.get("state") in ("running", "testing") and alive(st.get("pid")):
            n[str(st.get("account") or "")] = n.get(str(st.get("account") or ""), 0) + 1
    return n


def burn_rates() -> dict[str, float]:
    """{window: points per hour of one running run}: the EWMA in burn.json, else the prior."""
    cur = (read_json(BURN_FILE(), {}) or {}).get("rates") or {}
    return {w: float(cur[w]) if isinstance(cur.get(w), (int, float)) and cur[w] > 0 else p for w, p in BURN_PRIOR.items()}


def learn_burn(account: str, wins: dict, t: float, running: int | None = None) -> None:
    """From a new reading: delta used% / (running runs on the account x delta hours) feeds the EWMA of each window
    (burn.json, keyed by window, not by account). An interval that crosses a reset, runs over 2 h, has no run working,
    or shows the percentage falling is skipped. The baseline stays put until 5 minutes or 1 point have passed."""
    if not account:
        return
    n = running_by_account().get(account, 0) if running is None else running
    path = BURN_FILE()
    cur = read_json(path, {}) or {}
    last, rates = cur.get("last") or {}, dict(cur.get("rates") or {})
    mine = dict(last.get(account) or {})
    for w in BURN_PRIOR:
        win = wins.get(w) or {}
        used, reset = win.get("used_percentage"), win.get("resets_at")
        if not isinstance(used, (int, float)) or not isinstance(reset, (int, float)):
            continue
        base = mine.get(w)
        new = {"t": t, "used": used, "reset": reset, "n": n}
        if base and base.get("reset") == reset and 0 < t - base["t"]:
            dh, du = (t - base["t"]) / 3600, used - base["used"]
            if dh > 2 or du < 0:
                pass
            elif dh * 60 < 5 and du < 1:
                new = base   # too little to measure: keep the older baseline
            else:
                k = (n + base.get("n", 0)) / 2   # runs working at both ends of the interval, else it was not loaded steadily
                if n > 0 and base.get("n", 0) > 0:
                    r = du / (k * dh)
                    old = rates.get(w) if isinstance(rates.get(w), (int, float)) and rates[w] > 0 else BURN_PRIOR[w]
                    rates[w] = round(old * (1 - BURN_ALPHA) + r * BURN_ALPHA, 4)
        mine[w] = new
    last[account] = mine
    try:
        write_json(path, {"rates": rates, "last": last})
    except OSError:
        pass


def refresh_usage(acc: dict) -> bool:
    """One tiny haiku call on the account, to read its windows from the `rate_limit_event`: True when a reading came."""
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
    return got


def refresh_gate(acc: dict, t: float) -> bool:
    """Refresh an account's reading at most once per 15 minutes (ASD_PACE_REFRESH_S), whichever runner asks first: a
    lock file keeps the others from refreshing at once. True when this call made the refresh."""
    base = os.path.join(USAGE_DIR(), acc["name"] + ".refresh")
    try:
        os.makedirs(USAGE_DIR(), exist_ok=True)
        if t - os.path.getmtime(base) < env_num("ASD_PACE_REFRESH_S", 900):
            return False
    except OSError:
        pass
    try:
        fd = os.open(base + ".lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(base + ".lock") > 300:
                os.remove(base + ".lock")   # a runner died holding it
        except OSError:
            pass
        return False
    except OSError:
        return False
    try:
        with open(base, "w") as fh:
            fh.write(str(t))
        os.utime(base, (t, t))
        refresh_usage(acc)
    finally:
        try:
            os.remove(base + ".lock")
        except OSError:
            pass
    return True


def compute_cap(t: float | None = None) -> dict:
    """The paced cap and why: {cap, reason, ts, mode, accounts: [...]}; see the comment above for the formula."""
    t = now_s() if t is None else t
    lo = max(1, int(env_num("ASD_RUNNER_MIN_LIVE", 3, int)))
    hi = max(lo, int(env_num("ASD_RUNNER_CAP_MAX", 6, int)))
    rates = burn_rates()
    all_accs, st = load_accounts(), account_state()
    accs = [a for a in all_accs if not account_problem(a, all_accs)] or all_accs
    running = running_by_account()
    rows, parts, total, known, limited = [], [], 0.0, 0, 0
    for a in accs:
        name = a["name"]
        path = os.path.join(USAGE_DIR(), name + ".json")
        mark = st.get(name)
        use = read_json(path, {}) or {}
        if mark and float(mark.get("limited_until") or 0) > t and not limit_superseded(mark, use):
            limited += 1
            rows.append({"account": name, "limited": True, "runs": 0})
            parts.append(f"{name} limit-marked → 0")
            continue
        ts = epoch_of(use.get("ts"))
        if (ts is None or t - ts > 1800) and not running.get(name):
            if refresh_gate(a, t):
                use = read_json(path, {}) or {}
                ts = epoch_of(use.get("ts"))
        w = use.get("windows") or {}
        w5, w7 = w.get("five_hour") or {}, w.get("seven_day") or {}
        if ts is None or not isinstance(w5.get("used_percentage"), (int, float)):
            share = 4.0 / max(1, len(accs))
            total += share
            rows.append({"account": name, "unknown": True, "runs": round(share, 2)})
            parts.append(f"{name} no reading → {share:.1f}")
            continue
        known += 1

        def left(win: dict, key: str) -> tuple[float, float]:
            used, reset = win.get("used_percentage"), win.get("resets_at")
            if not isinstance(used, (int, float)):
                return 0.0, WINDOW_HOURS[key]
            if isinstance(reset, (int, float)) and reset <= t:
                return 0.0, WINDOW_HOURS[key]    # the window reset after the reading: it is empty again
            return float(used), (reset - t) / 3600 if isinstance(reset, (int, float)) else WINDOW_HOURS[key]

        u5, h5 = left(w5, "five_hour")
        u7, h7 = left(w7, "seven_day") if isinstance(w7.get("used_percentage"), (int, float)) else (0.0, WINDOW_HOURS["seven_day"])
        if u7 >= 98:
            runs = 0.0
        else:
            runs5 = (100 - PACE_MARGIN["five_hour"] - u5) / (rates["five_hour"] * max(h5, 0.25))
            runs7 = (100 - PACE_MARGIN["seven_day"] - u7) / (rates["seven_day"] * max(h7, 1.0))
            runs = max(0.0, min(runs5, runs7))
        total += runs
        rows.append({"account": name, "five_hour": u5, "h5": round(h5, 2), "seven_day": u7, "h7": round(h7, 1), "runs": round(runs, 2)})
        parts.append(f"{name} 5h {u5:.0f}% resets in {h5:.1f}h, weekly {u7:.0f}% → {runs:.1f}")
    if known == 0 and limited < len(accs):
        cap, parts = 4, ["no usage reading yet"]
    else:
        cap = max(lo, min(hi, int(total)))
    return {"cap": cap, "reason": f"{cap} slots: " + "; ".join(parts), "ts": t, "mode": "adaptive", "accounts": rows,
            "min": lo, "max": hi, "rates": rates}


def slot_cap() -> tuple[int, str]:
    """(cap, reason) for admissions. Fixed (ASD_RUNNER_ADAPTIVE=0 or ASD_RUNNER_MAX_LIVE) or paced and cached ~60 s."""
    if fixed_slots():
        n = max_live()
        return n, f"fixed at {n}: " + ("ASD_RUNNER_ADAPTIVE=0" if (os.environ.get("ASD_RUNNER_ADAPTIVE") or "").strip() == "0" else "ASD_RUNNER_MAX_LIVE")
    path, t = os.path.join(runs_dir(), "slots-cap.json"), now_s()
    c = read_json(path, {}) or {}
    if c.get("mode") == "adaptive" and isinstance(c.get("ts"), (int, float)) and abs(t - c["ts"]) < env_num("ASD_PACE_TTL", 60):
        return int(c["cap"]), str(c.get("reason") or "")
    c = compute_cap(t)
    try:
        write_json(path, c)
    except OSError:
        pass
    return c["cap"], c["reason"]


def queue_order(st: dict) -> tuple[int, str]:
    """A queued run's place: runs the owner just answered (`queued_first`) go before the rest, then longest-queued first."""
    return (0 if st.get("queued_first") else 1, str(st.get("queued_since") or ""))


def slot_census(me: str) -> tuple[int, list[tuple[tuple[int, str], str]]]:
    """(runs holding a slot, [(queue_order, key)] of runs waiting for one), other than `me`, live pids only."""
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
        if st.get("state") == "running":   # `testing` is alive but holds no slot
            held += 1
        elif st.get("state") == "queued":
            queued.append((queue_order(st), key))
    return held, sorted(queued)


# ---- the disk floor: no stage starts below ASD_DISK_FLOOR_GIB free (default 30) ----
# A dozen runs each building a workspace filled a 926 GB disk: builds, commits and the VM all failed at once. A run
# below the floor queues like a run without a slot, starts scripts/disk_guard.py reclaim (build output nobody uses),
# and starts its stage once the disk has room again.


def disk_floor() -> float:
    try:
        return float(os.environ.get("ASD_DISK_FLOOR_GIB") or 30)
    except ValueError:
        return 30.0


def disk_free_gib(path: str) -> float | None:
    try:
        return shutil.disk_usage(path).free / 2**30
    except OSError:
        return None


def start_reclaim() -> None:
    """Start disk_guard.py reclaim in the background (it holds a lock, so a second one exits at once)."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "disk_guard.py")
    if os.environ.get("ASD_DISK_NO_RECLAIM") or not os.path.exists(script):
        return
    try:
        subprocess.Popen([sys.executable, script, "reclaim"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


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
        self.code = script_stamp(__file__)   # a stage boundary re-execs the runner when this changes (a deployed fix)
        self.grace = float(os.environ.get("ASD_RUNNER_GRACE") or 1.5)
        self.q: queue.Queue | None = None   # the open claude process's event queue (a keep-alive ping reads its result here)
        self.last_api = 0.0                 # when the session's last API call came back (any assistant or result event)
        self.ka_since: float | None = None  # when this wait (owner question or queue) began
        self.turn_open = False              # a user message went in and no result event came yet: the session is mid-turn
        self.last_out = time.monotonic()    # monotonic time of the last stream event (or message sent) of the session
        self.last_ev = "none"               # the last stream event: its type and tool name, for the stall diagnostic
        self.tool_names: dict = {}          # tool_use id -> tool name
        self.inflight_bash: dict = {}       # Bash tool_use id -> (command, monotonic start): calls with no result yet
        self.stall_bg_at = 0.0              # when the transcript was last checked for background work during a quiet spell
        self.ka_checked = 0.0               # monotonic time of the last keep-alive check
        self.ka_cold: set[str] = set()      # sessions whose cache a ping found cold: not pinged again
        self.ka_said: set[str] = set()
        self.stray = 0                      # ping results still to come that read_turn must not take for a turn's
        self.ctx = 0                        # the session's context now: the last assistant usage, input + cache read + cache write
        self.split: dict | None = None      # a checkpoint taken above ASD_RUNNER_SPLIT_AT: {tid, name, ctx, ready}
        self.resume_sid = ""                # the session the next stage resumes (--resume) after a failure from outside the model
        self.resume_acct = ""               # the account that session belongs to: another account cannot resume it
        self.resumed = False                # the open process is a resume
        self.got_init = False               # the open process has started its session

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
        model = self.model or runner_model(None)
        cmd += ["--model", model]
        # The keep-alive resume ping is claude_cmd() + --resume, so everything that shapes the prefix is built here only.
        lean = (os.environ.get("ASD_RUNNER_LEAN") or "1").strip() != "0"
        rules = repo_rulings(main_repo_name(self.root))
        if lean or rules:
            st = json.loads(lean_settings(self.root)) if lean else {}
            if rules:   # the owner's standing rulings for this repo; the deny list is passed with them and outranks them
                st["permissions"] = {"allow": rules, "deny": RULING_DENY}
            cmd += ["--settings", lean_settings(self.root) if lean and not rules else json.dumps(st, separators=(",", ":"))]
        if lean:
            cmd += ["--disallowedTools", ",".join(LEAN_DISALLOWED)]
        win = compact_window(model)
        if win:
            cmd += ["--autocompact", str(win)]
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

    def child_env(self) -> dict:
        """The environment of a claude child of this run (the stage's and a keep-alive resume's): run key and account."""
        env = dict(os.environ, ASD_RUN_KEY=self.key)
        acct = getattr(self, "account", None)
        if acct and os.path.abspath(acct["dir"]) != os.path.abspath(os.path.expanduser("~/.claude")):
            env["CLAUDE_CONFIG_DIR"] = acct["dir"]
        elif acct:
            env.pop("CLAUDE_CONFIG_DIR", None)
        if compact_window(self.model or runner_model(None)):
            env.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)   # the variable would outrank the --autocompact claude_cmd passes
        if (os.environ.get("ASD_RUNNER_GIT_HARDEN") or "").strip() != "0":
            self.git_harden(env)
        if (os.environ.get("ASD_RUNNER_LEAN") or "1").strip() != "0":
            env.update(lean_env())
        return env

    def git_harden(self, env: dict) -> None:
        """Git never waits on a prompt or a dead connection (a `git pull` hung a session for 40 hours): no terminal
        prompt, an ssh that fails fast (BatchMode, a connect timeout, keepalives) unless GIT_SSH_COMMAND or GIT_SSH
        is set or the repo has its own core.sshCommand (GIT_SSH_COMMAND would outrank it), and https transfers that
        stay below 1 kB/s for 60 s abort. Off with ASD_RUNNER_GIT_HARDEN=0."""
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env.setdefault("GIT_HTTP_LOW_SPEED_LIMIT", "1000")
        env.setdefault("GIT_HTTP_LOW_SPEED_TIME", "60")
        if env.get("GIT_SSH_COMMAND") or env.get("GIT_SSH"):
            return
        try:
            own = subprocess.run(["git", "config", "core.sshCommand"], cwd=self.root, capture_output=True, text=True,
                                 timeout=10, stdin=subprocess.DEVNULL).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            own = ""
        if not own:
            env["GIT_SSH_COMMAND"] = GIT_SSH_HARDENED

    def spawn(self) -> tuple[queue.Queue, bool]:
        self.wrong_model = ""
        self.stray = 0
        self.ctx = 0
        self.split = None
        self.last_api = now_s()
        sid, self.resume_sid = self.resume_sid, ""
        self.resumed = bool(sid) and self.resume_acct == self.account["name"]
        self.got_init = False
        self.turn_open = False
        self.last_out = time.monotonic()
        self.last_ev = "none"
        try:
            self.err_start = os.path.getsize(self.err_path)
        except OSError:
            self.err_start = 0
        self.proc = subprocess.Popen(
            self.claude_cmd() + (["--resume", sid] if self.resumed else []), cwd=self.root, env=self.child_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=open(self.err_path, "ab"), start_new_session=True)
        self.save(claude_pid=self.proc.pid)
        q: queue.Queue = queue.Queue()
        self.q = q

        def pump(p=self.proc):
            for raw in p.stdout:
                try:
                    q.put(json.loads(raw))
                except ValueError:
                    continue
            q.put(None)

        threading.Thread(target=pump, daemon=True).start()
        if self.resumed:
            # Same session, same cache and work: the failure was outside the model (a kill, a network error, a limit).
            self.send(RESUME_MSG.format(task=self.command_line()))
            return q, True
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

    def mark_resume(self) -> None:
        """The next stage resumes this session (claude --resume) instead of starting a fresh one with a hand-off: for a
        failure outside the model, where the transcript still holds all the work and the prompt cache may still be warm."""
        sid = str(self.state.get("session") or "")
        if sid and os.environ.get("ASD_RUNNER_RESUME", "1") != "0":
            self.resume_sid, self.resume_acct = sid, self.account["name"]

    def background(self) -> list[str]:
        """The session's background work still in flight (#239), from its transcript; [] when it cannot be read."""
        path = transcript_path(str(self.state.get("session") or ""), (self.account or {}).get("dir"))
        if not path:
            return []
        try:
            with open(path, errors="replace") as fh:
                tasks = background_tasks(fh.read().splitlines())
        except OSError:
            return []
        live = []
        for t in tasks:
            if output_unheld(t["output"]):
                self.log(f"background task {t['id']} ({t['label']}) has no process left (nothing holds its output open); not waiting for it")
                continue
            live.append(t["label"])
        return live

    def take_slot(self, first: bool = False) -> bool:
        """Wait for a run slot (max_live); False when the run was stopped while it waited. `first`: the owner just
        answered this run, so it queues ahead of every run not answered (it already waited on the owner)."""
        mine: tuple[int, str] | None = None
        low_said, reclaimed = False, 0.0
        while not self.stopping:
            free, floor = disk_free_gib(self.root), disk_floor()
            low = free is not None and free < floor
            if low:
                if time.time() - reclaimed > 300:
                    start_reclaim(); reclaimed = time.time()
                if not low_said:
                    if not mine:
                        since = now()
                        mine = queue_order({"queued_since": since, "queued_first": first})
                        self.save(state="queued", queued_since=since, queued_first=first or None)
                    self.event(f"queued: the disk has {free:.0f} GiB free, below the {floor:.0f} GiB floor (ASD_DISK_FLOOR_GIB); "
                               "reclaiming unused build output, and this run starts once the disk has room", "queued")
                    low_said = True
                self.keepalive_tick()
                time.sleep(float(os.environ.get("ASD_RUNNER_SLOT_POLL") or 10))
                continue
            cap, why = slot_cap()   # paced and cached for ~60 s, or fixed; a lower cap only stops admissions
            if cap == 0:
                self.save(state="running", queued_since=None, queued_first=None)
                self.ka_since = None
                return True
            held, queued = slot_census(self.key)
            ahead = [k for o, k in queued if mine and o < mine]
            if held < cap and (not mine or not ahead):
                if mine:
                    self.event(f"{'disk has room' if low_said else 'slot free'}; {held + 1} of {cap} runs working", "running")
                self.save(state="running", queued_since=None, queued_first=None)
                self.ka_since = None
                return True
            if not mine:
                since = now()
                mine = queue_order({"queued_since": since, "queued_first": first})
                self.save(state="queued", queued_since=since, queued_first=first or None)
                self.event(f"queued{' first (answered)' if first else ''}: {held} of {cap} runs are working ({why}); "
                           "this one starts when a slot frees", "queued")
            self.keepalive_tick()
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
        A question the run is waiting on is left to the wait. A reply from before this run started answers an earlier
        run: a new agent would take that old "Stop" as an order about its own work, so it is never sent."""
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
                if l.get("from") != "owner" or str(l.get("ts") or "") <= str(done.get(qid) or "") or str(l.get("ts") or "") < started:
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
        self.turn_open = True
        self.last_out = time.monotonic()
        try:
            self.proc.stdin.write(user_message(text))
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass  # the process died; read_turn reports it

    # -- keeping the prompt cache warm ------------------------------------

    def session_last_api(self) -> float:
        """When the session last called the API: the last event seen, else the transcript's mtime."""
        if self.last_api:
            return self.last_api
        path = transcript_path(str(self.state.get("session") or ""), (self.account or {}).get("dir"))
        try:
            return os.path.getmtime(path) if path else 0.0
        except OSError:
            return 0.0

    def keepalive_blocked(self, t: float) -> str:
        """Why no ping now ("" = none): the account is limit-marked (the run would be paused) or its 5-hour window is >= 80%."""
        name = (self.account or {}).get("name", "")
        use = read_json(os.path.join(USAGE_DIR(), name + ".json"), {}) or {}
        mark = account_state().get(name)
        if mark and float(mark.get("limited_until") or 0) > t and not limit_superseded(mark, use):
            return "paused for usage"
        w5 = (use.get("windows") or {}).get("five_hour") or {}
        if isinstance(w5.get("used_percentage"), (int, float)) and w5["used_percentage"] >= 80 \
                and not (isinstance(w5.get("resets_at"), (int, float)) and w5["resets_at"] <= t):
            return f"five-hour window at {w5['used_percentage']:.0f}%"
        return ""

    def keepalive_tick(self) -> None:
        """Prompt-cache keep-alive for a run that waits on the owner: the cache lives 1 h, and an owner's answer can
        take longer. A run queued for a slot is never pinged: the slot wait is not worth what a ping costs. When the session's last API call is ASD_KEEPALIVE_AFTER seconds old
        (3000) and the wait is under ASD_KEEPALIVE_MAX_H hours (12), send one tiny ping. No ping while the run is
        paused for usage, when its account's 5-hour window is at 80% or more, or with ASD_KEEPALIVE=0. A ping takes
        no run slot and never changes the run's state."""
        if (os.environ.get("ASD_KEEPALIVE") or "").strip() == "0" or self.stopping:
            return
        if self.state.get("state") != "waiting":   # a slot wait ("queued") is not worth a ping: the run starts when a slot frees
            self.ka_since = None
            return
        if time.monotonic() - self.ka_checked < env_num("ASD_KEEPALIVE_POLL", 5):
            return
        self.ka_checked = time.monotonic()
        t = now_s()
        if self.ka_since is None:
            self.ka_since = t
        sid = str(self.state.get("session") or "")
        if not sid or sid in self.ka_cold:
            return
        if t - self.ka_since >= env_num("ASD_KEEPALIVE_MAX_H", 12) * 3600:
            if sid + ":max" not in self.ka_said:
                self.ka_said.add(sid + ":max")
                self.log("keepalive stopped: waited longer than ASD_KEEPALIVE_MAX_H")
            return
        last = self.session_last_api()
        if last and t - last < env_num("ASD_KEEPALIVE_AFTER", 3000):
            return
        if self.keepalive_blocked(t):
            return
        self.keepalive_ping(sid)

    def keepalive_ping(self, sid: str) -> None:
        """One ping, in the calling thread (the one that would deliver an owner's answer, so the two never interleave):
        into the open process's stdin when it lives, else one `claude -p --resume <sid>` turn built by claude_cmd, with
        the same flags, model, account, cwd and appended prompt as the stage, so the cached prefix matches."""
        live = self.proc is not None and self.proc.poll() is None and self.q is not None
        ev = self.ping_live() if live else self.ping_resume(sid)
        self.last_api = now_s()   # asked, answered or not: the next ping comes ASD_KEEPALIVE_AFTER later
        if not ev:
            self.log("keepalive ping: no answer")
            return
        if ev.get("is_error"):
            self.log("keepalive ping: the turn ended in an error: " + str(ev.get("result") or "")[:160].replace("\n", " "))
            return
        u = ev.get("usage") or {}
        read, wrote = int(u.get("cache_read_input_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0)
        self.log(f"keepalive ping: cache read {read}, written {wrote} tokens")
        if wrote > read:
            self.ka_cold.add(sid)
            self.log("keepalive stopped: the cache was already cold (the ping wrote more than it read); no more pings for this session")

    def ping_live(self) -> dict | None:
        """The open process answers the ping on its own stdin; its result is consumed here, not taken for stage output."""
        self.send(KEEPALIVE_MSG)
        end = time.monotonic() + env_num("ASD_KEEPALIVE_TIMEOUT", 180)
        while time.monotonic() < end and not self.stopping:
            try:
                ev = self.q.get(timeout=1.0)
            except queue.Empty:
                continue
            if ev is None:
                self.q.put(None)   # the process ended: read_turn reports it
                return None
            if not isinstance(ev, dict):
                continue
            self.note_event(ev)
            if ev.get("type") == "result":
                return ev
        self.stray += 1   # its result may still come; read_turn skips it
        return None

    def ping_resume(self, sid: str) -> dict | None:
        cmd = self.claude_cmd() + ["--resume", sid]
        try:
            out = subprocess.run(cmd, cwd=self.root, env=self.child_env(), input=user_message(KEEPALIVE_MSG),
                                 capture_output=True, timeout=env_num("ASD_KEEPALIVE_TIMEOUT", 180))
        except (OSError, subprocess.TimeoutExpired):
            return None
        res = None
        for line in out.stdout.decode("utf-8", "replace").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "rate_limit_event":
                record_usage((self.account or {}).get("name", ""), ev.get("rate_limit_info") or {})
            elif ev.get("type") == "result":
                res = ev
        return res

    def stderr_tail(self) -> str:
        """The last 4 KiB this session's claude wrote to stderr (the log is appended across sessions)."""
        try:
            with open(self.err_path, "rb") as fh:
                fh.seek(0, 2); fh.seek(max(getattr(self, "err_start", 0), fh.tell() - 4096))
                return fh.read().decode("utf-8", "replace")
        except OSError:
            return ""

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
        self.last_out = time.monotonic()   # any stream-json line is output, a sub-agent's included
        self.track_event(ev)
        if ev.get("type") in ("assistant", "result"):
            self.last_api = now_s()
        if ev.get("type") == "rate_limit_event":
            record_usage((getattr(self, "account", None) or {}).get("name", ""), ev.get("rate_limit_info") or {})
            return
        if ev.get("type") == "system" and ev.get("subtype") == "init" and ev.get("session_id"):
            self.save(session=ev["session_id"])
            self.got_init = True
        # The model the session really runs on: anything but the pinned one (Fable above all) ends the stage at once.
        # A sub-agent's message (parent_tool_use_id set) has its own model, Sonnet by design: only Fable is refused there.
        got = ev.get("model") if ev.get("type") == "system" and ev.get("subtype") == "init" else (ev.get("message") or {}).get("model") if ev.get("type") == "assistant" else None
        wrong = isinstance(got, str) and (FORBIDDEN_MODEL.search(got) if ev.get("parent_tool_use_id") else not model_ok(self.model or runner_model(None), got))
        if wrong and not getattr(self, "wrong_model", ""):
            self.wrong_model = got
            self.log(f"the session runs on {got}, not {self.model or runner_model(None)}: stopping the stage")
            kill_group(self.proc)
        if ev.get("type") == "user" and self.split and not ev.get("parent_tool_use_id"):
            c = (ev.get("message") or {}).get("content")
            if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                           and b.get("tool_use_id") == self.split["tid"] for b in c):
                self.split["ready"] = True   # the checkpoint's result is in: nothing is half done
        if ev.get("type") != "assistant":
            return
        msg = ev.get("message") or {}
        u = msg.get("usage")
        if isinstance(u, dict) and not ev.get("parent_tool_use_id"):
            self.ctx = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        content = msg.get("content")
        if not isinstance(content, list):
            return
        if self.split and not ev.get("parent_tool_use_id") and msg.get("id") and msg.get("id") != self.split["mid"]:
            self.split["ready"] = True   # a later API response: the session went on after the checkpoint
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
            at = int(env_num("ASD_RUNNER_SPLIT_AT", SPLIT_AT_DEFAULT, int))
            if at > 0 and self.ctx >= at and not self.split:
                self.split = {"tid": tid, "mid": msg.get("id") or "", "name": self.checkpoints[-1]["name"], "ctx": self.ctx, "ready": False}

    def track_event(self, ev: dict) -> None:
        """Keep what the stall watchdog needs: whether the session is mid-turn, and the last event's type and tool."""
        t = str(ev.get("type"))
        if t == "result":
            self.turn_open = False
            self.inflight_bash.clear()
        elif t in ("assistant", "user"):
            self.turn_open = True
        tool = ""
        c = (ev.get("message") or {}).get("content") if t in ("assistant", "user") else None
        if isinstance(c, list):
            for b in c:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    self.tool_names[str(b.get("id", ""))] = tool = str(b.get("name", ""))
                    inp = b.get("input") or {}
                    if tool == "Bash" and isinstance(inp, dict) and not inp.get("run_in_background"):
                        self.inflight_bash[str(b.get("id", ""))] = (str(inp.get("command", "")), time.monotonic())
                elif b.get("type") == "tool_result":
                    tool = self.tool_names.get(str(b.get("tool_use_id", "")), "")
                    t = "tool_result"
                    self.inflight_bash.pop(str(b.get("tool_use_id", "")), None)
        self.refresh_testing()
        if t not in ("rate_limit_event",):
            self.last_ev = t + (f" {tool}" if tool else "") + (" (sub-agent)" if ev.get("parent_tool_use_id") else "")

    def blocked_on(self) -> str:
        """The in-flight foreground Bash call the session is blocked on for a long wait, or "". A call counts when it
        runs run_tests.py or run_gates.py (--wait included), or when it has produced no output (no stream event of any
        kind) for more than TESTING_QUIET_SECS (ASD_TESTING_QUIET_SECS overrides)."""
        if not self.turn_open or not self.inflight_bash:
            return ""
        quiet = time.monotonic() - self.last_out > env_num("ASD_TESTING_QUIET_SECS", TESTING_QUIET_SECS)
        for cmd, _ in self.inflight_bash.values():
            if TESTING_CMD.search(cmd) or quiet:
                return cmd.strip().splitlines()[0][:80] if cmd.strip() else "a Bash call"
        return ""

    def refresh_testing(self) -> None:
        """running -> testing while blocked_on() says the session waits on a test or gate; back to running the moment
        that call's result arrives. Only these two states are touched; going back never queues (see the slot notes)."""
        cur = self.state.get("state")
        if cur not in ("running", "testing") or self.stopping:
            return
        why = self.blocked_on()
        if why and cur == "running":
            self.save(state="testing")
            self.event(f"session waits on `{why}`; slot released while it tests", "testing")
        elif not why and cur == "testing":
            self.save(state="running")
            self.event("the wait ended; the run works again (no queue; the cap may be briefly exceeded)", "running")

    def stalled(self) -> bool:
        """True when the session is wedged: mid-turn, running, and no stream-json output for ASD_STALL_MIN minutes
        (45; 0 disables). Not mid-turn (the runner's own waits on an owner's answer or a slot, a turn ended while
        background work runs) is never a stall, nor is a transcript that still shows background work in flight.
        A Bash call has its own timeout (10 min at most) and a sub-agent's events are output, so neither is quiet this long."""
        lim = env_num("ASD_STALL_MIN", 45) * 60
        if lim <= 0 or not self.turn_open or self.stopping or self.state.get("state") not in ("running", "testing"):
            return False
        if self.proc is None or self.proc.poll() is not None:
            return False
        if time.monotonic() - self.last_out < lim:
            return False
        if time.monotonic() - self.stall_bg_at >= 60:
            self.stall_bg_at = time.monotonic()
            if self.background():
                return False
            self.stall_bg_at = 0.0
            return True
        return False

    def process_tree(self) -> str:
        """ps rows (pid, etime, stat, cpu, command[:120]) of the claude process group and its descendants."""
        try:
            out = subprocess.run(["ps", "-axo", "pid=,ppid=,pgid=,etime=,stat=,pcpu=,command="], capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            return "(ps failed)"
        rows, kids = {}, {}
        for ln in out.splitlines():
            f = ln.split(None, 6)
            if len(f) < 7 or not f[0].isdigit() or not f[1].isdigit():
                continue
            rows[int(f[0])] = f
            kids.setdefault(int(f[1]), []).append(int(f[0]))
        root = self.proc.pid if self.proc else -1
        want, todo = set(), [root]
        while todo:
            x = todo.pop()
            if x in want:
                continue
            want.add(x)
            todo += kids.get(x, [])
        want |= {pid for pid, f in rows.items() if f[2] == str(root)}
        return "\n".join(f"  pid {pid} etime {f[3]} stat {f[4]} cpu {f[5]}% {f[6][:120]}" for pid, f in sorted(rows.items()) if pid in want) or "  (no process found)"

    def stall_report(self) -> str:
        """Log the diagnostic, then kill the process group (SIGKILL when SIGTERM does not end it); returns the detail."""
        mins = int((time.monotonic() - self.last_out) / 60)
        last = self.last_ev
        tool = last.split(" ", 1)[1] if last.count(" ") else last
        self.log(f"stall diagnostic: last stream event: {last}; process tree of claude pid {self.proc.pid}:\n" + self.process_tree())
        detail = f"stalled: no output for {mins} min mid-turn (last: {tool})"
        self.log(detail)
        kill_group(self.proc)
        for _ in range(50):
            if self.proc.poll() is not None:
                break
            time.sleep(0.1)
        kill_group(self.proc, signal.SIGKILL)
        return detail

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
                self.refresh_testing()   # a Bash call quiet for TESTING_QUIET_SECS has no event to trigger it
                if self.stalled():
                    return "stalled", None
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
            if self.split and self.split["ready"] and result is None:
                return "split", None
            if ev.get("type") == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                        last_text = b["text"]
            elif ev.get("type") == "result":
                self.split = None   # the turn is over: a checkpoint taken in it is not a place to cut the next one
                if self.stray > 0:
                    self.stray -= 1   # the late result of a keep-alive ping that timed out: not a turn's end
                    continue
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
                self.keepalive_tick()   # in this thread, so a ping and an answer never overlap on the session
                time.sleep(0.1)
        finally:
            self.ka_since = None
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

    def repair_env(self, why: str) -> dict:
        """The standing repair (scripts/stack_repair.py): detect the VM and stack, repair what is down, within its
        bounds. Off unless ASD_STACK_REPAIR=1. Never raises and never fails the run; returns {resource: outcome}."""
        if not stack_repair.enabled():
            return {}
        out: dict = {}
        try:
            cfg = stack_repair.load_config(self.root)
            if not cfg:
                return {}
            for _ in range(2):   # a started VM may show a broken stack that was not probed before
                found = stack_repair.detect(cfg, self.root)
                if not found:
                    break
                self.event(f"environment ({why}): " + "; ".join(f"{k}: {v}" for k, v in found.items()))
                res = stack_repair.repair(cfg, self.root, found, lambda t: self.event(t))
                out.update(res)
                if not any(v.startswith("repaired") for v in res.values()):
                    break
        except Exception as e:  # noqa: BLE001 - a repair must never end a run
            self.event(f"environment repair crashed: {e!r}")
        return out

    def handle_env(self, rest: str, n: int) -> str:
        """The message sent back for a `RUNNER: ENV <kind> <detail>` line."""
        parts = rest.split(None, 1)
        kind = parts[0].lower() if parts else ""
        detail = parts[1] if len(parts) > 1 else ""
        self.event(f"session reports environment failure: {kind} {detail[:160]}".strip())
        ask = "Ask the owner about it as usual (ask_owner.py, then RUNNER: WAITING)."
        if kind not in ("vm", "stack"):
            return f"RUNNER ENV {kind or '?'}: the runner does not repair this; {ask}"
        if not stack_repair.enabled():
            return f"RUNNER ENV {kind}: automatic repair is off (ASD_STACK_REPAIR is not 1); {ask}"
        if n > 4:
            return f"RUNNER ENV {kind}: reported {n} times this stage; {ask}"
        if not stack_repair.load_config(self.root):
            return f"RUNNER ENV {kind}: no repair is configured for this repo (.agentic/stack-repair.json); {ask}"
        res = self.repair_env("reported by the session").get(kind)
        if res is None:   # the checks find it healthy (the stage-start repair or another runner fixed it)
            return f"RUNNER ENV {kind}: repaired by the runner (healthy now); carry on with the step you stopped at."
        if res.startswith("repaired"):
            return f"RUNNER ENV {kind}: repaired by the runner; carry on with the step you stopped at."
        return f"RUNNER ENV {kind}: not repaired ({res}); {ask}"

    def run_stage(self) -> tuple[str, str]:
        """('next'|'done'|'failed'|'stopped', detail)."""
        n0 = len(self.checkpoints)
        q, extra = self.spawn()
        idle = float(os.environ.get("ASD_RUNNER_IDLE") or 1200)
        quiet = False   # the last turn ended without a RUNNER: line
        nudges = 0
        holds = 0       # stage ends held while background work ran
        bg_max = float(os.environ.get("ASD_RUNNER_BG_MAX") or 14400)
        bg_waited = 0.0  # quiet seconds spent waiting on background work the transcript shows in flight
        env_msgs = 0     # RUNNER: ENV lines this stage
        while True:
            kind, val = self.read_turn(q, extra, idle if quiet else None)
            extra = False
            if kind != "idle":
                bg_waited = 0.0
            if kind == "idle":
                # A turn ended "waiting" on a background command or workflow, and nothing re-invoked the session
                # within the idle window. While the transcript still shows that work in flight, the quiet is the
                # work (a fix round of 20 minutes is normal): keep waiting, up to ASD_RUNNER_BG_MAX. Otherwise nudge
                # it, twice at most.
                bg = self.background()
                if bg and bg_waited < bg_max:
                    if bg_waited == 0:
                        self.log(f"quiet, but {len(bg)} background task(s) still running: " + "; ".join(bg[:4]) + "; waiting for them")
                    bg_waited += idle
                    continue
                if nudges >= 2:
                    kill_group(self.proc)
                    self.end_process()
                    return "failed", "the turn ended with no RUNNER: line, and two nudges brought none"
                nudges += 1
                self.log(f"no RUNNER: line after {int(idle)} s of quiet; nudge {nudges}")
                self.send(NUDGE)
                continue
            if kind == "split":
                cp, self.split = self.split, None
                bg = self.background()
                if bg:
                    # a command or workflow in flight dies with the process: the split waits for a later checkpoint
                    self.log(f"split at checkpoint {cp['name']} held: {len(bg)} background task(s) still running: " + "; ".join(bg[:4]))
                    continue
                self.event(f"split at checkpoint {cp['name']}: context {cp['ctx'] // 1000}k")
                self.clear_restart()
                kill_group(self.proc)   # not a closed stdin: that would let the session finish its turn first
                self.end_process()
                return "next", cp["name"]
            if kind == "stopped":
                return "stopped", ""
            if kind == "stalled":
                detail = self.stall_report()
                self.end_process()
                return "stalled", detail
            if kind == "exit":
                self.end_process()
                tail = self.stderr_tail()
                if getattr(self, "wrong_model", ""):
                    return "failed", f"the session ran on {self.wrong_model}, not {self.model or runner_model(None)}; the runner refuses any other model (set the account's model, or ASD_RUNNER_MODEL)"
                if self.resumed and not self.got_init and not (self.stopping or os.path.exists(self.stop_marker)):
                    # claude could not resume the session (its transcript is gone): start the stage fresh instead
                    return "fresh", (tail.strip().splitlines()[-1][:200] if tail.strip() else f"exit {val}")
                if usage_limit(tail):
                    return "limit", tail.strip()
                if network_error(tail):
                    return "network", tail.strip().splitlines()[-1][:300] if tail.strip() else "network error"
                if val in KILLED_CODES and (self.stopping or os.path.exists(self.stop_marker)):
                    # The owner's stop reached the claude child before the runner's own stop path did: that exit is the stop.
                    self.log(f"the claude process exited with code {val}: that is the stop, not a failure")
                    return "stopped", ""
                if val in KILLED_CODES:
                    return "killed", f"the claude process was killed from outside the runner (exit {val}{self.kill_context()})"
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
            if err and not text.strip():
                # an empty error result: the reason is on stderr (claude could not load the organisation's settings
                # at start, for one, which happens right after the Mac wakes with no network yet)
                text = self.stderr_tail()
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
            nudges = 0   # it answered with a RUNNER: line; a later quiet spell gets its own two nudges
            verb, rest = mk
            if verb == "FAILED" and REPLAN_WORDS.search(rest):
                verb = "REPLAN"
            if verb in ("STAGE-DONE", "DONE", "REPLAN"):
                # #239: ending the process kills the background work it started, silently. Hold the end until it is done.
                bg = self.background()
                if bg and holds < HOLD_MAX:
                    holds += 1
                    self.log(f"stage end held ({holds}/{HOLD_MAX}): {len(bg)} background task(s) still running: " + "; ".join(bg[:4]))
                    self.send(HOLD_STAGE.format(what="; ".join(bg[:6])))
                    quiet = True
                    continue
                if bg:
                    self.log(f"stage ended with {len(bg)} background task(s) still running, after {HOLD_MAX} hold; the runner ends the stage and cuts them off: " + "; ".join(bg[:4]))
            if verb == "ENV":
                # `RUNNER: ENV <vm|stack|disk> <detail>`: a broken environment, reported instead of asked about.
                env_msgs += 1
                self.send(self.handle_env(rest, env_msgs))
                quiet = True
                continue
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
            if verb == "REPLAN":
                self.end_process()
                return "replan", rest or "sent back to /plan"
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
            if not self.take_slot(first=True):
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

    def kill_context(self) -> str:
        """What the runner can still see after an outside kill, to name the sender next time: free disk, the claude
        version now installed (an auto-update is a suspect), and the host's last wake."""
        bits = []
        free = disk_free_gib(self.root)
        if free is not None:
            bits.append(f"{free:.0f} GiB free")
        if not os.environ.get("ASD_CLAUDE_BIN"):   # a stand-in binary has no version to ask for
            try:
                v = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=10)
                if v.returncode == 0 and v.stdout.strip():
                    bits.append("claude " + v.stdout.strip().split()[0])
            except (OSError, subprocess.SubprocessError):
                pass
        if sys.platform == "darwin":
            try:
                w = subprocess.run(["sysctl", "-n", "kern.waketime"], capture_output=True, text=True, timeout=5)
                m = re.search(r"sec = (\d+)", w.stdout)
                if m:
                    bits.append("host woke " + datetime.datetime.fromtimestamp(int(m.group(1)), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            except (OSError, subprocess.SubprocessError):
                pass
        return ("; " + ", ".join(bits)) if bits else ""

    def reexec_if_changed(self) -> None:
        """A long-lived runner keeps the code it started with in memory, so a deployed fix never reaches it. At a stage
        boundary, when the script a fresh start would use differs from the one this process loaded, exec it with the
        same arguments: the pid stays (so the already-running check passes), the checkpoints carry the work on.
        ASD_RUNNER_REEXEC=0 turns it off."""
        if os.environ.get("ASD_RUNNER_REEXEC") == "0" or self.stopping:
            return
        path, mtime = script_stamp(__file__)
        if (path, mtime) == self.code or not mtime:
            return
        self.end_process()
        self.event(f"runner code changed ({path}); restarting the runner on it, same run", "running")
        self.save(state="running")
        args = [a for a in sys.argv[1:] if a != "--detach"]
        if "--foreground" not in args and sys.argv[1:2] != ["chat"]:
            args.append("--foreground")
        try:
            sys.stdout.flush(); sys.stderr.flush()
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable, path, *args])

    def on_term(self, *_a) -> None:
        self.stopping = True
        kill_group(self.proc)
        for p in self.children:
            if p.poll() is None:
                p.kill()

    def run(self) -> int:
        prev = read_json(self.state_path, {})
        if prev.get("state") in LIVE_STATES and runner_alive(prev.get("pid")) and prev.get("pid") != os.getpid():
            say(f"{self.key} is already run by pid {prev['pid']}")
            return 2
        try:
            os.remove(self.stop_marker)
        except OSError:
            pass
        if prev.get("state") == "done" or (prev.get("command") and prev.get("command") != self.command):
            # A finished run, or one of another command (a /plan after a /work-on sent back): its checkpoints are not
            # this run's hand-off.
            self.checkpoints = []
            write_json(self.cp_path, [])
        signal.signal(signal.SIGTERM, self.on_term)
        signal.signal(signal.SIGINT, self.on_term)
        self.save()
        self.event(f"start /{self.command} {self.dossier} in {self.root}", "running")
        started = 0
        if prev.get("state") in ("running", "testing", "waiting", "queued", "paused") and prev.get("session") and prev.get("account") == self.account["name"]:
            # The last runner process died (a logout, a reboot) with its session recorded: pick that session up again.
            self.state["session"] = str(prev["session"])
            self.mark_resume()
            self.state["session"] = ""
        net_tries = 0   # network errors in a row; a stage that ends well resets it
        kill_tries = 0  # outside kills in a row; likewise
        stall_tries = 0  # stalls of one stage in a row; the third goes to the owner
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
            self.repair_env(f"stage {started} start")
            free = disk_free_gib(self.root)
            if free is not None:
                say(f"{self.key} disk: {free:.0f} GiB free at stage {started} start")
            outcome, detail = self.run_stage()
            if outcome != "network":
                net_tries = 0
            if outcome != "killed":
                kill_tries = 0
            if outcome != "stalled":
                stall_tries = 0
            if outcome == "stalled":
                # The session wedged (no stream output mid-turn): not the owner's call for the first two. Kill (done),
                # then run the same stage again from its checkpoints; the third in a row takes the failure path.
                stall_tries += 1
                if stall_tries < 3:
                    self.mark_resume()
                    self.event(f"{detail}; stage {started} again from its checkpoints (stall {stall_tries} of 2)", "running")
                    self.reexec_if_changed()
                    started -= 1
                    continue
            if outcome == "next":
                self.event(f"stage {started} end" + (f": {detail}" if detail else ""), "running")
                self.reexec_if_changed()
                continue
            if outcome == "killed":
                # Something outside the runner (a logout, a memory or disk squeeze, a crash of the host app) killed
                # the claude process mid-stage. Not the owner's call: back off and run the stage again from its
                # checkpoints, through take_slot (so the disk floor applies), ASD_RUNNER_KILL_RETRIES times in a row.
                kill_tries += 1
                self.mark_resume()
                limit_k = int(os.environ.get("ASD_RUNNER_KILL_RETRIES") or 2)
                if kill_tries <= limit_k:
                    wait = float(os.environ.get("ASD_RUNNER_KILL_WAIT") or KILL_BACKOFF[min(kill_tries, len(KILL_BACKOFF)) - 1])
                    self.event(f"{detail}; stage {started} again in {int(wait)} s (retry {kill_tries}/{limit_k})", "running")
                    until = time.time() + wait
                    while time.time() < until and not self.stopping:
                        time.sleep(min(5.0, max(0.2, until - time.time())))
                    if self.stopping:
                        return self.finish("stopped", "stopped", 3)
                    self.reexec_if_changed()
                    started -= 1
                    continue
                detail = f"{detail}, {limit_k} retries in a row"
            if outcome == "fresh":
                self.event(f"the session could not be resumed ({detail}); stage {started} again from the checkpoints", "running")
                started -= 1
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
            if outcome == "replan":
                return self.replan(detail)
            if outcome == "stopped":
                self.end_process()
                return self.finish("stopped", "stopped", 3)
            if outcome == "network":
                # A transient network or API-server error: pause, then run the same stage again from its checkpoints,
                # backing off, however long it lasts: an outage says nothing about the work. Only with
                # ASD_RUNNER_NET_RETRIES set does it become a failure the owner rules on.
                net_tries += 1
                self.mark_resume()
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
                self.mark_resume()   # only used while the account stays the same
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
                if detail.startswith("the claude process exited with code"):
                    self.mark_resume()   # a crash of the process, not a verdict of the model: keep its session
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

    def replan(self, why: str) -> int:
        """`RUNNER: REPLAN`: the dossier goes back to /plan. Not a failure and not a question: the run ends done, the
        dossier is set to planned, and a /plan run of the same dossier is started detached; it queues for a slot like any
        run. Its checkpoints start empty: the /work-on ones belong to another command (run() clears them)."""
        set_dossier_status(self.root, self.dossier, "planned", self.log)
        rc = self.finish("done", f"sent back to /plan: {why}", 0)
        if self.command != "work-on" or os.environ.get("ASD_RUNNER_REPLAN", "1") == "0":
            return rc
        try:
            detach([self.dossier, "--root", self.root, "--command", "plan"], self.root, self.dossier, self.key)
            self.event("re-plan started: /plan " + self.dossier + " (it starts when a slot frees)", "done")
        except OSError as e:
            self.event(f"could not start the re-plan: {e}; start /plan from the dashboard", "done")
        return rc

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
        if state in LIVE_STATES and not alive(st.get("pid")):
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
        if st.get("state") not in ("running", "testing", "waiting", "paused", "queued") or runner_alive(st.get("pid")) or not os.path.isdir(root):
            continue
        if time.time() - os.path.getmtime(path) > hours * 3600:
            continue
        cpid = st.get("claude_pid")
        if alive(cpid) and "claude" in proc_command(cpid):
            # the runner died but its claude child lives on, orphaned: the restart resumes that session, so end the orphan first
            try:
                os.killpg(int(cpid), signal.SIGTERM)
            except (OSError, ValueError):
                pass
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
    if prev.get("command") == "work-on" and prev.get("state") in ("running", "testing", "waiting", "paused", "queued", "stopped", "failed"):
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
    if st.get("state") in LIVE_STATES or alive(cpid):
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
        if st.get("state") in LIVE_STATES:
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
    if st.get("state") in ("running", "testing", "waiting", "paused", "queued") and alive(st.get("pid")):
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
        got = refresh_usage(acc)
        say(f"{acc['name']}: " + ("usage recorded" if got else "no usage reading"))
        bad += 0 if got else 1
    return 0 if not bad else 1


def cmd_summary(a: argparse.Namespace) -> int:
    """`summary ID`: write the product summary of a finished dossier now (for builds that ended before the runner
    wrote one, or to redo it). The run's own record stays as it is."""
    key = key_of(a.root, a.dossier)
    path = os.path.join(runs_dir(), key + ".runner.json")
    prev = read_json(path, {}) or {}
    if prev.get("state") in ("running", "testing", "waiting", "paused", "queued") and runner_alive(prev.get("pid")):
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
if "--resume" in sys.argv and script.get("no_resume"):   # claude finds no such session: it exits before any event
    sys.stderr.write("No conversation found with session ID: " + sys.argv[sys.argv.index("--resume") + 1] + "\n"); sys.exit(1)
out({"type": "system", "subtype": "init", "session_id": f"sess-{n}", "model": script.get("init_model", argm)})
if script.get("crash") is not None:
    sys.exit(script["crash"])
turn = 0
pingn = 0
for raw in sys.stdin:
    msg = json.loads(raw)
    text = msg["message"]["content"][0]["text"]
    inlog.write(raw); inlog.flush()
    if text.startswith("RUNNER HAND-OFF"):
        continue
    if text.startswith("RUNNER KEEPALIVE"):
        pings = script.get("pings") or [[9000, 100]]
        pr, pw = pings[min(pingn, len(pings) - 1)]; pingn += 1
        out({"type": "result", "subtype": "success", "is_error": False, "session_id": f"sess-{n}", "result": "OK",
             "usage": {"input_tokens": 3, "output_tokens": 2, "cache_read_input_tokens": pr, "cache_creation_input_tokens": pw}})
        continue
    if script.get("stall"):   # a Bash tool_use, then silence: the 40 hour `git pull`
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "ts", "name": "Bash", "input": {"command": "git pull"}}]}})
        import time; time.sleep(600)
    if script.get("hang"):
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t0", "name": "mcp__x__phase_checkpoint", "input": {"phase": "P-hang", "summary": "s"}}]}})
        import time; time.sleep(600)
    acts = script["turns"][turn]; turn += 1
    final = ""
    for a in acts:
        if "checkpoint" in a:
            msg = {"id": f"m{n}-{turn}", "content": [{"type": "text", "text": "SECRET-MODEL-TEXT"}, {"type": "tool_use", "id": f"t{n}{turn}", "name": "mcp__plugin_agentic-software-development_nav__phase_checkpoint", "input": {"phase": a["checkpoint"][0], "summary": a["checkpoint"][1]}}]}
            if "ctx" in a:
                msg["usage"] = {"input_tokens": 3, "cache_read_input_tokens": a["ctx"] - 1003, "cache_creation_input_tokens": 1000, "output_tokens": 5}
            out({"type": "assistant", "message": msg})
            if "ctx" in a:
                out({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{n}{turn}", "content": "ok"}]}})
                if a.get("go_on"):   # the session works on after the checkpoint: one more API response, then the turn's end
                    out({"type": "assistant", "message": {"id": f"m{n}-{turn}b", "content": [{"type": "text", "text": "more work"}], "usage": {"input_tokens": 3, "cache_read_input_tokens": a["ctx"], "cache_creation_input_tokens": 0}}})
                    import time; time.sleep(0.5)
        elif "usage" in a:   # a plain API response with no checkpoint, carrying a context size
            out({"type": "assistant", "message": {"id": f"mu{n}-{turn}", "content": [{"type": "text", "text": "work"}], "usage": {"input_tokens": 3, "cache_read_input_tokens": a["usage"], "cache_creation_input_tokens": 0}}})
        elif "bash" in a:   # a foreground Bash call that blocks `hold` seconds, then its result, then `after` s of work
            import time
            out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"bx{n}{turn}", "name": "Bash", "input": {"command": a["bash"]}}]}})
            time.sleep(a["hold"])
            out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"bx{n}{turn}", "content": "ok"}]}})
            time.sleep(a.get("after", 0))
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


FAKE_LIMACTL = """\
#!/usr/bin/env python3
import json, os, sys
d = os.environ["ASD_FAKE_LIMA"]
def rd(n, default=""):
    try:
        return open(os.path.join(d, n)).read().strip()
    except OSError:
        return default
def log(t):
    open(os.path.join(d, "calls.log"), "a").write(t + "\\n")
a = sys.argv[1:]
if a[:2] == ["list", "--json"]:
    print(json.dumps({"name": "nightwatch", "status": rd("vm", "Running")}))
elif a[:1] == ["start"]:
    log("start " + a[1])
    if rd("start_fails") != "1":
        open(os.path.join(d, "vm"), "w").write("Running")
elif a[:1] == ["shell"]:
    if rd("vm", "Running") != "Running":
        sys.exit(1)
    cmd = a[-1]
    if "docker compose ps" in cmd:
        for r in json.loads(rd("stack", "[]")):
            print(json.dumps(r))
    elif "up.sh" in cmd:
        log("up " + cmd)
        if rd("up_fixes", "1") == "1":
            open(os.path.join(d, "stack"), "w").write(json.dumps([{"Service": "nats", "State": "running", "Health": "healthy"}]))
    else:
        log("UNEXPECTED " + cmd)
        sys.exit(9)
else:
    log("UNEXPECTED " + " ".join(a))
    sys.exit(9)
"""


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
    # Leak guard: nothing a fixture asks or runs may reach the owner's real stores; no ruling of the owner's may shape an argv here.
    real_dirs = [os.environ.get("ASD_QUESTIONS_DIR") or os.path.expanduser("~/.agentic/questions"), runs_dir()]
    listing = lambda: {d: {f for f in os.listdir(d) if f.startswith(("repo.", "clone.", "W-"))} if os.path.isdir(d) else set() for d in real_dirs}
    before_real = listing()
    rulings_tmp = tempfile.mkdtemp(prefix="asd-rulings-")
    os.environ["ASD_RULINGS_DIR"] = rulings_tmp

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
                   ASD_ASK_OWNER=ASK_OWNER, ASD_OWNER_POLL="0.1", ASD_RUNNER_GRACE="0.2",
                   # the runners of this test see no accounts file (the one default account), and the paced cap is off: the
                   # tests of pacing and of the pings set what they need
                   ASD_RUNNER_ADAPTIVE="0", ASD_ACCOUNTS=os.path.join(tmp, "no-accounts.json"), ASD_ACCOUNT_STATE=os.path.join(tmp, "e2e-state.json"),
                   ASD_USAGE_DIR=os.path.join(tmp, "usage-e2e"))

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

        # ---- 2a. REPLAN (and FAILED worded as a send-back): no question; the dossier is planned and /plan starts ----
        os.makedirs(os.path.join(root, ".discovery", "dossiers"), exist_ok=True)
        for did, final in (("W-90", "RUNNER: REPLAN owner ruling Q2=A: contract gaps 1-7"),
                           ("W-91", "worktree removed\nRUNNER: FAILED sent back to /plan by owner ruling Q2=A")):
            dpath = os.path.join(root, ".discovery", "dossiers", f"{did}-x.md")
            open(dpath, "w").write(f"---\nid: {did}\nstatus: ready\nblocked_by: []\n---\n# {did}\n")
            write_json(os.path.join(rdir, f"repo.{did}.checkpoints.json"), [])
            scenario([{"turns": [[{"checkpoint": ["Phase 2", "stopped: sent back"]}, {"final": final}]]},
                      {"turns": [[{"final": "RUNNER: DONE"}]]}])
            p = start(did)
            check(f"replan {did}: the /work-on run ends done (exit 0)", p.wait(timeout=60) == 0)
            check(f"replan {did}: no owner question", not os.path.exists(qfile(f"repo.{did}.Q1")))
            check(f"replan {did}: the dossier is set to planned", "status: planned" in open(dpath).read())
            check(f"replan {did}: a /plan run starts and finishes", bool(until(lambda: state_of(did).get("command") == "plan" and state_of(did).get("state") == "done", 60)))
            check(f"replan {did}: /plan gets the plan command, not the /work-on checkpoints",
                  msgs(2)[0] == f"/{PLUGIN}:plan {did}" and len(msgs(2)) == 1)
            check(f"replan {did}: the send-back is in the run's updates", any("sent back to /plan" in t for t in texts(did)))

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

        # ---- 3b. a claude process killed from outside reruns the stage, no owner question --
        env["ASD_RUNNER_KILL_WAIT"] = "0.1"
        scenario([{"crash": 143}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-90")
        check("outside kill: the stage reruns and ends DONE", p.wait(timeout=60) == 0 and state_of("W-90").get("state") == "done")
        check("outside kill: no owner question, the kill is logged",
              not os.path.exists(qfile("repo.W-90.Q1")) and any("killed from outside the runner (exit 143; " in t and "GiB free" in t for t in texts("W-90")))
        argv2 = open(os.path.join(fdir, "env.2")).read()
        check("outside kill: the rerun resumes the same session (--resume) with a short resume message, no hand-off",
              "--resume sess-1" in argv2 and msgs(2)[0].startswith("RUNNER RESUME") and not any(m.startswith("RUNNER HAND-OFF") for m in msgs(2))
              and "--resume" not in open(os.path.join(fdir, "env.1")).read())
        scenario([{"crash": 143}, {"no_resume": True}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-92")
        check("outside kill: a session claude cannot resume falls back to a fresh stage, no owner question, no retry used up",
              p.wait(timeout=60) == 0 and not os.path.exists(qfile("repo.W-92.Q1"))
              and any("could not be resumed (No conversation found" in t for t in texts("W-92"))
              and "--resume" not in open(os.path.join(fdir, "env.3")).read() and msgs(3)[0] == f"/{PLUGIN}:work-on W-92")
        scenario([{"crash": 143}, {"crash": 137}, {"crash": 143}])
        p = start("W-91")
        check("outside kill: past ASD_RUNNER_KILL_RETRIES the owner rules", bool(until(lambda: os.path.exists(qfile("repo.W-91.Q1")))))
        owner_line("repo.W-91.Q1", "answer", "B", "B")
        check("outside kill: three in a row end failed", p.wait(timeout=60) == 1 and any("2 retries in a row" in t for t in texts("W-91")))

        # ---- 3c. a changed runner script is exec'd at the next stage boundary, same run --
        sd = os.path.join(tmp, "reexec", "scripts")
        shutil.copytree(HERE, sd, ignore=shutil.ignore_patterns("__pycache__"))
        copy = os.path.join(sd, os.path.basename(me))
        scenario([{"turns": [[{"append": copy, "line": "# a deployed fix"}, {"final": "RUNNER: STAGE-DONE one"}]]}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        rp = subprocess.Popen([sys.executable, copy, "--root", root, "--dossier", "W-92", "--foreground"], env=env,
                              stdout=open(os.path.join(tmp, "out.W-92.txt"), "w"), stderr=subprocess.STDOUT)
        check("re-exec: the run ends DONE in the same process", rp.wait(timeout=60) == 0 and state_of("W-92").get("state") == "done"
              and state_of("W-92").get("pid") == rp.pid)
        check("re-exec: the restart is logged", any("runner code changed" in t for t in texts("W-92")))
        vroot = os.path.join(tmp, "cache", "asd")
        for v in ("1.9.0", "1.10.0", "1.2.30"):
            os.makedirs(os.path.join(vroot, v, "scripts"))
            open(os.path.join(vroot, v, "scripts", "run_dossier.py"), "w").write("#")
        os.makedirs(os.path.join(vroot, "1.11.0", "scripts"))   # a version without the script does not count
        check("re-exec: under a plugin cache the highest version wins",
              script_stamp(os.path.join(vroot, "1.9.0", "scripts", "run_dossier.py"))[0] == os.path.join(vroot, "1.10.0", "scripts", "run_dossier.py"))

        # ---- 4. --max-stages ----------------------------------------------------
        scenario([{"turns": [[{"final": "RUNNER: STAGE-DONE one"}]]}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-4", "--max-stages", "1")
        check("max-stages: exit 4, state stopped, one process",
              p.wait(timeout=60) == 4 and state_of("W-4").get("state") == "stopped"
              and open(os.path.join(fdir, "count")).read() == "1" and any("--max-stages 1" in t for t in texts("W-4")))

        # ---- 4b. a stop that reaches the claude child first is "stopped"; the same signal with no stop is an outside kill --
        scenario([{"hang": True}])
        p = start("W-93")
        check("stop race: the process is up", bool(until(lambda: os.path.exists(os.path.join(fdir, "pid.1")) and state_of("W-93").get("claude_pid"))))
        open(os.path.join(rdir, "repo.W-93.stop-requested"), "w").close()
        os.kill(int(open(os.path.join(fdir, "pid.1")).read()), signal.SIGTERM)
        check("stop race: claude exit 143 with the stop marker ends stopped, not failed", p.wait(timeout=30) == 3 and state_of("W-93").get("state") == "stopped")
        check("stop race: logged as the stop", any("that is the stop, not a failure" in t for t in texts("W-93")) and not any("before a result" in t for t in texts("W-93")))
        env["ASD_RUNNER_KILL_WAIT"] = "0.1"
        scenario([{"hang": True}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-94")
        check("outside SIGTERM: the process is up", bool(until(lambda: os.path.exists(os.path.join(fdir, "pid.1")) and state_of("W-94").get("claude_pid"))))
        os.kill(int(open(os.path.join(fdir, "pid.1")).read()), signal.SIGTERM)
        check("outside SIGTERM without a stop is not 'stopped': it is logged as killed and the stage reruns",
              p.wait(timeout=60) == 0 and state_of("W-94").get("state") == "done"
              and any("killed from outside the runner" in t for t in texts("W-94")) and not any("that is the stop" in t for t in texts("W-94")))

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
        until(lambda: state_of("W-71").get("claude_pid") and state_of("W-71").get("session"))
        p.terminate(); p.wait(timeout=30)
        st71 = state_of("W-71")
        check("revive: a SIGTERM nobody asked for keeps the run's state, so it can restart", st71.get("state") == "running" and any("interrupted" in t for t in texts("W-71")))
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        r = subprocess.run([sys.executable, me, "revive"], env=env, capture_output=True, text=True, timeout=60)
        done71 = until(lambda: state_of("W-71").get("state") == "done", 30)
        check("revive: the killed run restarts and finishes", "restarted repo.W-71" in r.stdout and bool(done71))
        check("revive: the restart resumes the session its dead runner recorded",
              "--resume sess-1" in open(os.path.join(fdir, "env.1")).read() and msgs(1)[0].startswith("RUNNER RESUME"))
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
        order = sorted([(queue_order({"queued_since": "2026-11-09T10:00:00Z"}), "old"),
                        (queue_order({"queued_since": "2026-11-09T11:00:00Z", "queued_first": True}), "answered"),
                        (queue_order({"queued_since": "2026-11-09T10:30:00Z", "queued_first": True}), "answered-earlier")])
        check("slots: an answered run queues ahead of runs queued before it, answered runs among themselves by time",
              [k for _, k in order] == ["answered-earlier", "answered", "old"])
        # ---- a long test wait releases the slot: `testing` is live but holds none ----
        bsy = {"pid": os.getpid(), "state": "testing"}
        before = slot_census("x")[0]
        write_json(os.path.join(rdir, "repo.W-91.runner.json"), bsy)
        check("slots: slot_census ignores a testing run (alive, no slot)", slot_census("x")[0] == before)
        write_json(os.path.join(rdir, "repo.W-91.runner.json"), {**bsy, "state": "running"})
        check("slots: ...while a running run counts", slot_census("x")[0] == before + 1)
        write_json(os.path.join(rdir, "repo.W-91.runner.json"), {**bsy, "state": "queued", "queued_since": "2026-11-09T10:00:00Z"})
        check("slots: a queued run is still counted as queued", len(slot_census("x")[1]) >= 1)
        os.remove(os.path.join(rdir, "repo.W-91.runner.json"))
        env["ASD_RUNNER_MAX_LIVE"] = "1"
        scenario([{"turns": [[{"bash": "python3 scripts/run_tests.py --wait", "hold": 4, "after": 4}, {"final": "RUNNER: DONE"}]]}])
        p = start("W-92")
        went = until(lambda: state_of("W-92").get("state") == "testing", 20)
        check("testing: the state flips to testing while run_tests.py is in flight", bool(went))
        held_now = slot_census("other")[0] if went else -1
        check("testing: slot_census does not count the testing run", held_now == 0)
        back = until(lambda: state_of("W-92").get("state") == "running", 20)
        check("testing: the state goes back to running when the result arrives, with no queue", bool(back))
        p.wait(timeout=60)
        env["ASD_TESTING_QUIET_SECS"] = "1"
        scenario([{"turns": [[{"bash": "sleep 99", "hold": 5, "after": 3}, {"final": "RUNNER: DONE"}]]}])
        p = start("W-93")
        check("testing: any Bash call quiet past the threshold flips to testing", bool(until(lambda: state_of("W-93").get("state") == "testing", 20)))
        p.wait(timeout=60)
        env.pop("ASD_TESTING_QUIET_SECS")
        env.pop("ASD_RUNNER_MAX_LIVE")
        # ---- the disk floor: below it the run queues (no claude), and starts once the disk has room ----
        env["ASD_DISK_FLOOR_GIB"] = "1e12"; env["ASD_DISK_NO_RECLAIM"] = "1"
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-38")
        low = until(lambda: any("below the" in t and "floor" in t for t in texts("W-38")), 20)
        check("disk: below the floor the run is queued, its claude not started", bool(low) and state_of("W-38").get("state") == "queued" and not os.path.exists(os.path.join(fdir, "pid.1")))
        p.kill(); p.wait()
        env.pop("ASD_DISK_FLOOR_GIB"); env.pop("ASD_DISK_NO_RECLAIM")
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
        check("network: claude unable to load the organisation's settings at start is a network error",
              network_error("Claude Code couldn't reach Anthropic to load your organization's settings (could not connect). Check your network connection and retry"))
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

        # ---- an earlier run's answer on the same dossier is history: a new run never receives it ----
        os.makedirs(os.path.dirname(qfile("repo.W-42.Q9")), exist_ok=True)
        with open(qfile("repo.W-42.Q9"), "w") as fh:
            fh.write(json.dumps({"ts": "2026-01-01T00:00:00Z", "from": "agent", "kind": "question", "text": "failed; retry?", "run": "repo.W-42"}) + "\n")
            fh.write(json.dumps({"ts": "2026-01-01T00:05:00Z", "from": "owner", "kind": "answer", "text": "Stop", "option": "B"}) + "\n")
        scenario([{"turns": [[{"ask": 1, "dossier": "W-42", "final": "asked, carrying on with other work"}], [{"final": "RUNNER: DONE"}]]}])
        env.update(ASD_RUNNER_QFWD_EVERY="0.2", ASD_RUNNER_IDLE="60")
        p = start("W-42")
        q42 = "repo.W-42.Q10"
        until(lambda: os.path.exists(qfile(q42)) and os.path.exists(os.path.join(fdir, "in.1.jsonl")), 20)
        time.sleep(0.5)
        owner_line(q42, "followup", "and this one?")
        rc = p.wait(timeout=60)
        env.pop("ASD_RUNNER_QFWD_EVERY"); env["ASD_RUNNER_IDLE"] = "1"
        check("question reply: an earlier run's answer is not sent to a new run, today's still is",
              rc == 0 and not any("repo.W-42.Q9" in m for m in msgs(1)) and any(m.startswith(f"OWNER FOLLOWUP {q42}") for m in msgs(1)))

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

        # ---- quiet while a workflow runs: no nudge, no failure, until it notifies ----
        launch = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu_wf", "name": "Workflow", "input": {"name": "phase4-wave"}}]}},
                  {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu_wf", "content": "Workflow launched in background with ID: wfx1"}]}}]
        with open(os.path.join(tdir2, "sess-1.jsonl"), "w") as fh:
            fh.write("".join(json.dumps(o) + "\n" for o in launch))
        scenario([{"turns": [[{"final": "waiting for the fix round"}], [{"final": "RUNNER: DONE"}]]}])
        env.update(ASD_TRANSCRIPTS_DIR=os.path.join(tmp, "projects2"), ASD_RUNNER_IDLE="0.5")

        def finish_wf() -> None:
            time.sleep(4)
            with open(os.path.join(tdir2, "sess-1.jsonl"), "a") as fh:
                fh.write(json.dumps({"type": "queue-operation", "operation": "enqueue", "content":
                                     "<task-notification>\n<task-id>wfx1</task-id>\n<tool-use-id>tu_wf</tool-use-id>\n<status>completed</status>\n</task-notification>"}) + "\n")
        threading.Thread(target=finish_wf, daemon=True).start()
        p = start("W-39")
        rc = p.wait(timeout=60)
        env.pop("ASD_TRANSCRIPTS_DIR"); env["ASD_RUNNER_IDLE"] = "1"
        tw = texts("W-39")
        check("quiet while a workflow runs: no nudge until it is done, then the run finishes",
              rc == 0 and any("background task(s) still running" in t for t in tw)
              and sum(1 for t in tw if "nudge" in t) == 1 and len(msgs(1)) == 2)

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
        stop = [J({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "b2", "name": "Bash", "input": {"description": "Run the gates"}}]}}),
                J({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "b2", "content": "Command running in background with ID: bg2. Output is being written to: /x"}]}}),
                J({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "s1", "name": "TaskStop", "input": {"task_id": "bg2"}}]}}),
                J({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "s1", "content": '{"message":"Successfully stopped task: bg2 (cd x; gates)"}'}]}})]
        gone = stop[:2] + [J({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "s2", "name": "TaskStop", "input": {"task_id": "bg2"}}]}}),
                           J({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "s2", "content": "<tool_use_error>No task found with ID: bg2</tool_use_error>"}]}})]
        check("background: a task stopped on purpose, or already gone when stopped, is no longer in flight",
              background_in_flight(lines + stop) == ["Run the VM suite"] and background_in_flight(gone) == [] and background_in_flight(stop[:2]) == ["Run the gates"])
        # the real shapes (config-harness transcripts): a stop whose result is JSON, a command moved to the background after
        # its timeout, a stop by shell_id, a stop that fails with some other text
        mv = [J({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "m1", "name": "Bash", "input": {"description": "Locate state"}}]}}),
              J({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "m1", "content": "Command did not complete within its 120s timeout and was moved to the background (ID: mv1). Output is being written to: /tmp/x/tasks/mv1.output. You will be notified"}]}})]
        st = lambda tool, key, res, err=False: [J({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "k1", "name": tool, "input": {key: "mv1"}}]}}),
                                                J({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "k1", "content": res, "is_error": err}]}})]
        check("background: a command moved to the background after its timeout is in flight, with its output path", [t["output"] for t in background_tasks(mv)] == ["/tmp/x/tasks/mv1.output"])
        check("background: a stop is read from the call's task id, whatever the result text says",
              background_in_flight(mv + st("TaskStop", "task_id", "stopped")) == [] and background_in_flight(mv + st("KillShell", "shell_id", "done")) == []
              and background_in_flight(mv + st("TaskStop", "task_id", '{"message":"Killed task: mv1"}')) == [])
        check("background: a stop that failed for another reason, or one naming another task, leaves it in flight",
              background_in_flight(mv + st("TaskStop", "task_id", "permission denied", True)) == ["Locate state"]
              and background_in_flight(mv + [st("TaskStop", "task_id", "ok")[0].replace("mv1", "other"), st("TaskStop", "task_id", "ok")[1]]) == ["Locate state"])
        check("background: the hold is one round and tells the agent not to ask the owner",
              HOLD_MAX == 1 and "not a question for the owner" in HOLD_STAGE and "never ask" in HOLD_STAGE)
        tdir0 = os.path.join(tmp, "tasks")
        os.makedirs(tdir0, exist_ok=True)
        outf = os.path.join(tdir0, "o1.output")
        open(outf, "w").write("x")
        hold = open(outf, "a")
        held_now = output_unheld(outf)
        hold.close()
        check("background: process check: an output file a process holds open is running, one nobody holds is gone, an unknown path is running",
              held_now is False and output_unheld(outf) is True and output_unheld("/nope/tasks/z.output") is False and output_unheld("") is False)
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


        # ---- the standing VM and stack repair (scripts/stack_repair.py): fake limactl on PATH, opt-in ----
        fl, bindir, rep = (os.path.join(tmp, x) for x in ("lima", "bin", "repdir"))
        for d in (fl, bindir, rep, os.path.join(root, ".agentic")):
            os.makedirs(d, exist_ok=True)
        open(os.path.join(bindir, "limactl"), "w").write(FAKE_LIMACTL)
        os.chmod(os.path.join(bindir, "limactl"), 0o755)
        write_json(os.path.join(root, ".agentic", "stack-repair.json"), {
            "vm": "nightwatch", "compose_dir": "deploy/compose", "vm_start": ["limactl", "start", "{vm}"],
            "stack_up": ["limactl", "shell", "{vm}", "--", "bash", "-lc", "cd {root} && bash deploy/scripts/up.sh core"]})
        env.update(PATH=bindir + os.pathsep + os.environ.get("PATH", ""), ASD_FAKE_LIMA=fl, ASD_STACK_REPAIR_DIR=rep)
        saved_env = dict(os.environ)
        os.environ.update(PATH=env["PATH"], ASD_FAKE_LIMA=fl, ASD_STACK_REPAIR_DIR=rep)
        os.environ.pop("ASD_STACK_REPAIR", None)
        healthy = [{"Service": "nats", "State": "running", "Health": "healthy"}, {"Service": "postgres", "State": "running", "Health": ""}]
        sick = [{"Service": "nats", "State": "running", "Health": "unhealthy"}]

        def fake_reset(vm="Running", stack=healthy, **kw):
            for f in os.listdir(fl):
                os.remove(os.path.join(fl, f))
            for f in ("stack-repair-state.json", "stack-repair.lock"):
                try:
                    os.remove(os.path.join(rep, f))
                except OSError:
                    pass
            open(os.path.join(fl, "vm"), "w").write(vm)
            open(os.path.join(fl, "stack"), "w").write(json.dumps(stack))
            for k, v in kw.items():
                open(os.path.join(fl, k), "w").write(v)

        def calls() -> list[str]:
            try:
                return open(os.path.join(fl, "calls.log")).read().splitlines()
            except OSError:
                return []

        rlog: list[str] = []
        cfg = stack_repair.load_config(root)
        check("repair: the repo's .agentic/stack-repair.json is read", bool(cfg) and cfg["vm"] == "nightwatch")
        check("repair: the plugin's config names config-harness", bool(stack_repair.load_config(os.path.join(tmp, "config-harness"))))
        fake_reset("Stopped")
        check("repair detect: a stopped VM", list(stack_repair.detect(cfg, root)) == ["vm"])
        fake_reset(stack=sick)
        check("repair detect: an unhealthy service", list(stack_repair.detect(cfg, root)) == ["stack"] and "nats unhealthy" in stack_repair.detect(cfg, root)["stack"])
        fake_reset(stack=healthy)
        check("repair detect: a healthy VM and stack", stack_repair.detect(cfg, root) == {})
        fake_reset("Stopped")
        check("repair gate: off unless ASD_STACK_REPAIR=1, nothing is run",
              not stack_repair.enabled() and stack_repair.repair(cfg, root, {"vm": "x"}, rlog.append) == {"vm": "off"} and calls() == [])
        os.environ["ASD_STACK_REPAIR"] = "1"
        res = stack_repair.repair(cfg, root, {"vm": "x"}, rlog.append)
        check("repair vm: a stopped VM is started with the configured command", res["vm"] == "repaired" and calls() == ["start nightwatch"]
              and any("repair vm" in x for x in rlog))
        fake_reset(stack=sick)
        res = stack_repair.repair(cfg, root, {"stack": "x"}, rlog.append)
        check("repair stack: an unhealthy stack runs the repo's up.sh", res["stack"] == "repaired" and len(calls()) == 1
              and "bash deploy/scripts/up.sh core" in calls()[0] and root in calls()[0])
        fake_reset(stack=sick, up_fixes="0")
        r1 = [stack_repair.repair(cfg, root, {"stack": "x"}, rlog.append)["stack"] for _ in range(3)]
        check("repair bound: two attempts an hour per resource, then it stays for the owner",
              r1[0].startswith("failed") and r1[1].startswith("failed") and r1[2].startswith("bound") and len(calls()) == 2)
        fake_reset("Stopped", start_fails="1")
        stack_repair.repair(cfg, root, {"vm": "x"}, rlog.append)
        check("repair bound: the VM bound counts its own attempts",
              stack_repair.repair(cfg, root, {"vm": "x"}, rlog.append)["vm"].startswith("failed")
              and stack_repair.repair(cfg, root, {"vm": "x"}, rlog.append)["vm"].startswith("bound"))
        # the lock: a second repairer waits for the first, then finds it already fixed and starts nothing
        fake_reset("Stopped")
        lk = stack_repair._Lock(5)
        lk.__enter__()
        box: dict = {}
        th = threading.Thread(target=lambda: box.update(stack_repair.repair(cfg, root, {"vm": "x"}, rlog.append, wait_s=20)))
        th.start()
        time.sleep(0.6)
        waited = th.is_alive() and calls() == []
        open(os.path.join(fl, "vm"), "w").write("Running")   # the holder fixed it
        lk.__exit__()
        th.join(20)
        check("repair lock: another runner waits for the lock, then sees it fixed and starts nothing",
              waited and box.get("vm", "").startswith("repaired") and calls() == [])
        fake_reset("Stopped")
        lk = stack_repair._Lock(5)
        lk.__enter__()
        res = stack_repair.repair(cfg, root, {"vm": "x"}, rlog.append, wait_s=0.3)
        lk.__exit__()
        check("repair lock: a lock that stays busy ends in 'failed', so the owner is asked", res["vm"].startswith("failed") and calls() == [])
        os.environ.pop("ASD_STACK_REPAIR")

        # in a run: the gate off, a session's ENV line is answered "off"; on, the VM is started before the stage and on request
        fake_reset("Stopped")
        scenario([{"turns": [[{"final": "RUNNER: ENV vm limactl: instance nightwatch is stopped"}], [{"final": "RUNNER: DONE"}]]}])
        p = start("W-12")
        rc = p.wait(timeout=60)
        check("repair run: gate off, the ENV line is answered 'off' and no command runs",
              rc == 0 and any(m.startswith("RUNNER ENV vm: automatic repair is off") for m in msgs(1)) and calls() == [])
        env["ASD_STACK_REPAIR"] = "1"
        fake_reset("Stopped")
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        rc = start("W-13").wait(timeout=60)
        tl = [x.get("text", "") for x in lines_of(os.path.join(rdir, "repo.W-13.jsonl"))]
        check("repair run: gate on, a stopped VM is started before the first stage, logged as run events",
              rc == 0 and calls() == ["start nightwatch"] and any("repair vm" in t for t in tl) and any("environment (stage 1 start)" in t for t in tl))
        fake_reset(stack=sick)
        scenario([{"turns": [[{"final": "RUNNER: ENV stack nats unhealthy"}], [{"final": "RUNNER: ENV disk 1 GiB free"}], [{"final": "RUNNER: DONE"}]]}])
        # the stack is already repaired by the stage-start check, so the session's report finds it healthy
        rc = start("W-14").wait(timeout=60)
        check("repair run: an ENV stack line is answered 'repaired'; an ENV disk line is left to the owner",
              rc == 0 and len([c for c in calls() if c.startswith("up")]) == 1
              and any(m.startswith("RUNNER ENV stack: repaired") for m in msgs(1)) and any(m.startswith("RUNNER ENV disk: the runner does not repair") for m in msgs(1)))
        env.pop("ASD_STACK_REPAIR")
        os.environ.clear(); os.environ.update(saved_env)

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

        # ---- slots that follow the budget (pacing): the math, in process, with an injected clock and usage files ----
        pdir = os.path.join(tmp, "pace"); pusage = os.path.join(pdir, "usage"); pruns = os.path.join(pdir, "runs")
        for d in (pusage, pruns):
            os.makedirs(d, exist_ok=True)
        saved = {k: os.environ.get(k) for k in ("ASD_RUNS_DIR", "ASD_USAGE_DIR", "ASD_ACCOUNTS", "ASD_ACCOUNT_STATE", "ASD_RUNNER_NOW", "ASD_CLAUDE_BIN",
                                                "ASD_RUNNER_ADAPTIVE", "ASD_RUNNER_MAX_LIVE", "ASD_PACE_TTL", "ASD_PACE_REFRESH_S")}
        T = time.time()
        iso = lambda e: datetime.datetime.fromtimestamp(e, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        os.environ.update(ASD_RUNS_DIR=pruns, ASD_USAGE_DIR=pusage, ASD_ACCOUNTS=os.path.join(pdir, "accounts.json"),
                          ASD_ACCOUNT_STATE=os.path.join(pdir, "state.json"), ASD_RUNNER_NOW=str(T))
        for k in ("ASD_RUNNER_ADAPTIVE", "ASD_RUNNER_MAX_LIVE", "ASD_PACE_TTL", "ASD_PACE_REFRESH_S"):
            os.environ.pop(k, None)
        write_json(os.environ["ASD_ACCOUNTS"], {"accounts": [{"name": "main", "dir": os.path.join(pdir, "cm")}, {"name": "gmail", "dir": os.path.join(pdir, "cg")}]})
        counter = os.path.join(pdir, "refreshes")
        refresher = os.path.join(pdir, "refresher.py")
        open(refresher, "w").write(
            "import json,sys,time\nopen(%r,'a').write('x')\n"
            "print(json.dumps({'type':'rate_limit_event','rate_limit_info':{'status':'allowed','unifiedWindows':{"
            "'five_hour':{'utilization':0.5,'resetsAt':int(time.time())+7200,'status':'allowed'},"
            "'seven_day':{'utilization':0.1,'resetsAt':int(time.time())+86400*3,'status':'allowed'}}}}))\n" % counter)
        os.environ["ASD_CLAUDE_BIN"] = f"{shlex.quote(sys.executable)} {shlex.quote(refresher)}"

        def put(name: str, u5, h5, u7, h7, age=60) -> None:
            write_json(os.path.join(pusage, name + ".json"), {"ts": iso(T - age), "status": "allowed", "windows": {
                "five_hour": {"used_percentage": u5, "resets_at": int(T + h5 * 3600), "status": "allowed"},
                "seven_day": {"used_percentage": u7, "resets_at": int(T + h7 * 3600), "status": "allowed"}}})

        def fresh() -> None:
            for f in os.listdir(pusage):
                if f.endswith((".refresh", ".lock")) or f in ("burn.json",):
                    os.remove(os.path.join(pusage, f))
            for f in (counter, os.path.join(pruns, "slots-cap.json"), os.environ["ASD_ACCOUNT_STATE"]):
                if os.path.exists(f):
                    os.remove(f)

        def refreshes() -> int:
            return len(open(counter).read()) if os.path.exists(counter) else 0

        fresh(); put("main", 5, 3, 0, 30); put("gmail", 5, 3, 0, 30)
        c = compute_cap()
        check("pacing: low use gives the maximum, 6 (each account fits 4.7 runs in its 5-hour window)", c["cap"] == 6 and c["reason"].startswith("6 slots: main 5h 5% resets in 3.0h"))
        put("main", 85, 2, 10, 30); put("gmail", 85, 2, 10, 30)
        check("pacing: high use gives the minimum, 3", compute_cap()["cap"] == 3)
        put("main", 40, 2, 10, 30); put("gmail", 90, 2, 10, 30)
        check("pacing: one account with room (4.2 runs), one without: 4", compute_cap()["cap"] == 4)
        put("main", 5, 3, 0, 30); put("gmail", 5, 3, 98, 30)
        c = compute_cap()
        check("pacing: an account whose weekly window is at 98% contributes nothing", c["cap"] == 4 and c["accounts"][1]["runs"] == 0)
        put("gmail", 5, 3, 0, 30)
        write_json(os.environ["ASD_ACCOUNT_STATE"], {"main": {"limited_until": T + 3600, "marked": iso(T)}})
        c = compute_cap()
        check("pacing: a limit-marked account is excluded", c["cap"] == 4 and c["accounts"][0].get("limited") and "limit-marked" in c["reason"])
        write_json(os.environ["ASD_ACCOUNT_STATE"], {"main": {"limited_until": T + 3600, "marked": iso(T)}, "gmail": {"limited_until": T + 3600, "marked": iso(T)}})
        check("pacing: every account limit-marked leaves the minimum (the pause logic does the rest)", compute_cap()["cap"] == 3)
        # a reading whose window has reset since counts as an empty window
        write_json(os.environ["ASD_ACCOUNT_STATE"], {})
        put("main", 99, -1, 0, 30); put("gmail", 99, -1, 0, 30)
        check("pacing: a window that reset since the reading is empty again", compute_cap()["cap"] == 6)
        # stale reading: refreshed through the usage call, once per 15 minutes
        fresh(); put("main", 5, 3, 0, 30); put("gmail", 99, 3, 0, 30, age=7200)
        c = compute_cap()
        check("pacing: a stale reading with no run on the account is refreshed (one call), and the new reading counts",
              refreshes() == 1 and c["accounts"][1].get("five_hour") == 50.0)
        put("gmail", 99, 3, 0, 30, age=7200); compute_cap()
        check("pacing: the refresh happens at most once per 15 minutes per account", refreshes() == 1)
        os.utime(os.path.join(pusage, "gmail.refresh"), (T - 1000, T - 1000)); put("gmail", 99, 3, 0, 30, age=7200); compute_cap()
        check("pacing: ... and again after the 15 minutes", refreshes() == 2)
        write_json(os.path.join(pruns, "repo.W-299.runner.json"), {"pid": os.getpid(), "state": "running", "account": "gmail"})
        os.utime(os.path.join(pusage, "gmail.refresh"), (T - 1000, T - 1000)); put("gmail", 99, 3, 0, 30, age=7200)
        c = compute_cap()
        check("pacing: a run working on the account keeps its reading fresh: no refresh, the old reading is used", refreshes() == 2 and c["accounts"][1].get("five_hour") == 99.0)
        os.remove(os.path.join(pruns, "repo.W-299.runner.json"))
        # no reading at all: 4 / number of accounts each, 4 overall
        fresh(); os.environ["ASD_CLAUDE_BIN"] = f"{shlex.quote(sys.executable)} -c pass"
        for f in os.listdir(pusage):
            os.remove(os.path.join(pusage, f))
        c = compute_cap()
        check("pacing: with no reading anywhere the cap falls back to 4", c["cap"] == 4 and "no usage reading" in c["reason"])
        fresh(); put("main", 5, 3, 0, 30)
        c = compute_cap()
        check("pacing: an account with no reading counts 4/accounts (2) next to the known one: 4.7 + 2 = 6", c["cap"] == 6 and c["accounts"][1]["runs"] == 2.0)
        # fixed caps: ADAPTIVE=0, or MAX_LIVE set, keep the old behaviour; the paced cap is cached for a minute
        put("gmail", 90, 2, 10, 30); put("main", 90, 2, 10, 30)
        os.environ["ASD_RUNNER_ADAPTIVE"] = "0"
        check("pacing: ASD_RUNNER_ADAPTIVE=0 keeps the fixed cap, 4", slot_cap()[0] == 4)
        os.environ.pop("ASD_RUNNER_ADAPTIVE"); os.environ["ASD_RUNNER_MAX_LIVE"] = "2"
        check("pacing: an explicit ASD_RUNNER_MAX_LIVE keeps the fixed cap", slot_cap()[0] == 2)
        os.environ["ASD_RUNNER_MAX_LIVE"] = "0"
        check("pacing: ASD_RUNNER_MAX_LIVE=0 stays 'no limit'", slot_cap()[0] == 0)
        os.environ.pop("ASD_RUNNER_MAX_LIVE")
        fresh(); put("gmail", 5, 3, 0, 30); put("main", 5, 3, 0, 30)
        check("pacing: slot_cap is paced without either knob, and writes the dashboard's record", slot_cap()[0] == 6
              and read_json(os.path.join(pruns, "slots-cap.json"), {}).get("reason", "").startswith("6 slots: "))
        put("gmail", 90, 2, 10, 30); put("main", 90, 2, 10, 30)
        check("pacing: the cap is cached for ~60 s, so runners agree", slot_cap()[0] == 6)
        os.environ["ASD_RUNNER_NOW"] = str(T + 61)
        check("pacing: ... and recomputed after it", slot_cap()[0] == 3)
        os.environ["ASD_RUNNER_NOW"] = str(T)
        # burn rates: learned from successive readings, keyed by window; reset-crossing and long intervals are skipped
        fresh()
        check("burn: the prior is used until something is learned", burn_rates() == BURN_PRIOR)
        win = lambda u, r: {"five_hour": {"used_percentage": u, "resets_at": r}, "seven_day": {"used_percentage": u / 10, "resets_at": 9_000_000_000}}
        learn_burn("main", win(10, 5000), 1000.0, running=2)
        learn_burn("main", win(14, 5000), 1000.0 + 1800, running=2)    # +4 points in 0.5 h with 2 runs: 4 points/run-hour
        check("burn: delta used% / (runs x hours) feeds an EWMA (0.8 x 6 + 0.2 x 4 = 5.6)", abs(burn_rates()["five_hour"] - 5.6) < 1e-6)
        learn_burn("main", win(3, 9000), 1000.0 + 3600, running=2)     # a new window: the interval crosses a reset
        learn_burn("main", win(80, 9000), 1000.0 + 3600 * 5, running=2)  # 4 h gap: longer than 2 h
        check("burn: an interval that crosses a reset, or is longer than 2 h, is skipped", abs(burn_rates()["five_hour"] - 5.6) < 1e-6)
        learn_burn("main", win(80, 9000), 1000.0 + 3600 * 5 + 900, running=0)
        learn_burn("main", win(83, 9000), 1000.0 + 3600 * 5 + 1800, running=0)   # nobody running: nothing to divide by
        check("burn: an interval with no run working is skipped", abs(burn_rates()["five_hour"] - 5.6) < 1e-6)
        check("burn: the weekly rate is learned apart from the five-hour one",
              burn_rates()["seven_day"] != BURN_PRIOR["seven_day"] and abs(burn_rates()["seven_day"] - (0.5 * 0.8 + 0.2 * (0.4 / (2 * 0.5)))) < 1e-6)
        fresh()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

        # ---- the queued event names the cap and why (paced), through a real runner ----
        pe_acc, pe_use = os.path.join(tmp, "pe-accounts.json"), os.path.join(tmp, "pe-usage")
        os.makedirs(pe_use, exist_ok=True)
        write_json(pe_acc, {"accounts": [{"name": "k1", "dir": os.path.join(tmp, "k1")}, {"name": "k2", "dir": os.path.join(tmp, "k2")}]})
        for nm in ("k1", "k2"):
            write_json(os.path.join(pe_use, nm + ".json"), {"ts": now(), "status": "allowed", "windows": {
                "five_hour": {"used_percentage": 85, "resets_at": int(time.time() + 7200), "status": "allowed"},
                "seven_day": {"used_percentage": 10, "resets_at": int(time.time() + 86400 * 3), "status": "allowed"}}})
        for i in (291, 292, 293):
            write_json(os.path.join(rdir, f"repo.W-{i}.runner.json"), {"pid": os.getpid(), "state": "running"})
        env.pop("ASD_RUNNER_ADAPTIVE"); env.update(ASD_ACCOUNTS=pe_acc, ASD_USAGE_DIR=pe_use, ASD_RUNNER_SLOT_POLL="0.2")
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-239")
        qd = until(lambda: any(t.startswith("queued:") and "3 of 3 runs are working (3 slots: k1 5h 85%" in t for t in texts("W-239")), 20)
        check("pacing: high use paces the cap to 3; the queued event carries the reason", bool(qd) and state_of("W-239").get("state") == "queued" and not os.path.exists(os.path.join(fdir, "pid.1")))
        for i in (291, 292, 293):
            os.remove(os.path.join(rdir, f"repo.W-{i}.runner.json"))
        check("pacing: the queued run starts when a slot frees (never preempting the others)", p.wait(timeout=60) == 0)
        env["ASD_RUNNER_ADAPTIVE"] = "0"; env["ASD_ACCOUNTS"] = os.path.join(tmp, "no-accounts.json"); env["ASD_USAGE_DIR"] = os.path.join(tmp, "usage-e2e")
        env.pop("ASD_RUNNER_SLOT_POLL")

        # ---- keep-alive pings for the prompt cache, through a real runner and a fake claude that answers them ----
        def ka_run(dossier: str, pings, **knobs) -> tuple:
            """A run that asks the owner a question and waits; returns (process, question id)."""
            qk = f"repo.{dossier}.Q1"
            scenario([{"turns": [[{"ask": 1, "dossier": dossier, "final": "RUNNER: WAITING {id}"}], [{"final": "RUNNER: DONE"}]], "pings": pings}])
            env.update({k: str(v) for k, v in knobs.items()})
            p = start(dossier)
            until(lambda: os.path.exists(qfile(qk)) and state_of(dossier).get("state") == "waiting")
            return p, qk

        def pinglines(dossier: str) -> list[str]:
            return [t for t in texts(dossier) if t.startswith("keepalive ping")]

        def ka_end(p, dossier: str, qk: str) -> bool:
            for k in ("ASD_KEEPALIVE_AFTER", "ASD_KEEPALIVE_MAX_H", "ASD_KEEPALIVE", "ASD_KEEPALIVE_POLL"):
                env.pop(k, None)
            owner_line(qk, "answer", "A", "A")
            try:
                return p.wait(timeout=30) == 0
            except subprocess.TimeoutExpired:
                print(f"  {dossier} did not finish: state {state_of(dossier)}; output tail: " + open(os.path.join(tmp, f"out.{dossier}.txt")).read()[-600:])
                p.kill(); p.wait()
                return False

        p, qk = ka_run("W-240", [[9000, 100]], ASD_KEEPALIVE_AFTER=1, ASD_KEEPALIVE_POLL=0.2)
        got = until(lambda: any("cache read 9000, written 100 tokens" in t for t in pinglines("W-240")), 20)
        check("keepalive: a waiting run is pinged once the session has been quiet for ASD_KEEPALIVE_AFTER, and the event reports the cache", bool(got))
        pm = [m for m in msgs(1) if m.startswith("RUNNER KEEPALIVE")]
        check("keepalive: the ping is the documented line, sent to the open process", bool(pm) and pm[0] == KEEPALIVE_MSG)
        check("keepalive: the run's state, questions and stage are unchanged", state_of("W-240").get("state") == "waiting" and state_of("W-240").get("questions") == [qk])
        time.sleep(2.5)
        check("keepalive: pings repeat every ASD_KEEPALIVE_AFTER while the wait lasts", len(pinglines("W-240")) >= 2)
        owner_line(qk, "answer", "A", "A")
        env.pop("ASD_KEEPALIVE_AFTER"); env.pop("ASD_KEEPALIVE_POLL")
        check("keepalive: the owner's answer after pings is delivered and the run finishes (pings are not turns)",
              p.wait(timeout=60) == 0 and any(m.startswith("OWNER ") for m in msgs(1)))
        p, qk = ka_run("W-241", [[9000, 100]], ASD_KEEPALIVE_AFTER=1000, ASD_KEEPALIVE_POLL=0.2)
        time.sleep(3)
        check("keepalive: not before the threshold", not pinglines("W-241") and not any(m.startswith("RUNNER KEEPALIVE") for m in msgs(1)))
        check("keepalive: the run still finishes", ka_end(p, "W-241", qk))
        p, qk = ka_run("W-242", [[9000, 100]], ASD_KEEPALIVE_AFTER=0.3, ASD_KEEPALIVE_MAX_H=0.0006, ASD_KEEPALIVE_POLL=0.1)
        stopped = until(lambda: any(t.startswith("keepalive stopped: waited longer") for t in texts("W-242")), 20)
        n_then = len(pinglines("W-242")); time.sleep(1.5)
        check("keepalive: pings stop at ASD_KEEPALIVE_MAX_H, with an event", bool(stopped) and n_then >= 1 and len(pinglines("W-242")) == n_then)
        check("keepalive: the run still finishes", ka_end(p, "W-242", qk))
        p, qk = ka_run("W-243", [[100, 9000]], ASD_KEEPALIVE_AFTER=0.5, ASD_KEEPALIVE_POLL=0.1)
        cold = until(lambda: any("already cold" in t for t in texts("W-243")), 20)
        time.sleep(2)
        check("keepalive: a ping that writes more than it reads stops the pinging for that session (one ping only)",
              bool(cold) and len([t for t in pinglines("W-243") if t.startswith("keepalive ping:")]) == 1)
        check("keepalive: the run still finishes", ka_end(p, "W-243", qk))
        p, qk = ka_run("W-244", [[9000, 100]], ASD_KEEPALIVE_AFTER=0.3, ASD_KEEPALIVE_POLL=0.1, ASD_KEEPALIVE="0")
        time.sleep(2.5)
        check("keepalive: ASD_KEEPALIVE=0 turns it off", not pinglines("W-244"))
        check("keepalive: the run still finishes", ka_end(p, "W-244", qk))
        env["ASD_ACCOUNT_STATE"] = os.path.join(tmp, "ka-state.json")
        write_json(env["ASD_ACCOUNT_STATE"], {"default": {"limited_until": time.time() + 3600, "marked": now()}})
        p, qk = ka_run("W-245", [[9000, 100]], ASD_KEEPALIVE_AFTER=0.3, ASD_KEEPALIVE_POLL=0.1)
        time.sleep(2.5)
        check("keepalive: no ping while the account is limit-marked (the run would be paused)", not pinglines("W-245"))
        check("keepalive: the run still finishes", ka_end(p, "W-245", qk))
        write_json(env["ASD_ACCOUNT_STATE"], {})
        os.makedirs(env["ASD_USAGE_DIR"], exist_ok=True)
        write_json(os.path.join(env["ASD_USAGE_DIR"], "default.json"), {"ts": now(), "status": "allowed", "windows": {
            "five_hour": {"used_percentage": 85, "resets_at": int(time.time() + 7200), "status": "allowed"}}})
        p, qk = ka_run("W-246", [[9000, 100]], ASD_KEEPALIVE_AFTER=0.3, ASD_KEEPALIVE_POLL=0.1)
        time.sleep(2.5)
        check("keepalive: no ping while the account's five-hour window is at 80% or more", not pinglines("W-246"))
        check("keepalive: the run still finishes", ka_end(p, "W-246", qk))
        env.pop("ASD_ACCOUNT_STATE")
        env["ASD_ACCOUNT_STATE"] = os.path.join(tmp, "e2e-state.json")
        # the other path: no process, so one `claude -p --resume <sid>` turn built by the stage's own argv builder
        sv = {k: os.environ.get(k) for k in ("ASD_RUNS_DIR", "ASD_CLAUDE_BIN", "ASD_FAKE_DIR", "ASD_ASK_OWNER", "ASD_ACCOUNTS", "ASD_USAGE_DIR", "ASD_ACCOUNT_STATE")}
        os.environ.update(ASD_RUNS_DIR=rdir, ASD_CLAUDE_BIN=env["ASD_CLAUDE_BIN"], ASD_FAKE_DIR=fdir, ASD_ASK_OWNER=ASK_OWNER,
                          ASD_ACCOUNTS=os.path.join(tmp, "no-accounts.json"), ASD_USAGE_DIR=os.path.join(tmp, "usage-e2e2"), ASD_ACCOUNT_STATE=os.path.join(tmp, "e2e-state2.json"))
        scenario([{"turns": [], "pings": [[7000, 50]]}])
        rk = Runner(root, "W-247", "work-on", None, "auto", 1)
        rk.state["session"] = "sess-resume"; rk.state["state"] = "queued"
        rk.keepalive_ping("sess-resume")
        envf = open(os.path.join(fdir, "env.1")).read().split("\n", 1) if os.path.exists(os.path.join(fdir, "env.1")) else ["", ""]
        want = " ".join(rk.claude_cmd()[2:] + ["--resume", "sess-resume"])
        check("keepalive: with no live process the ping resumes the session with the stage's own argv (model, rules, permission mode) and run key",
              envf[1] == want and envf[0] == rk.key and any(m.startswith("RUNNER KEEPALIVE") for m in msgs(1)))
        check("keepalive: the resumed ping is logged with its cache usage and leaves the run's state alone",
              "keepalive ping: cache read 7000, written 50 tokens" in texts("W-247") and rk.state["state"] == "queued")
        # ---- no keep-alive while queued; the git hardening of child_env ----
        scenario([{"turns": [], "pings": [[7000, 50]]}])
        rk.state["state"] = "queued"; rk.last_api = 1.0; rk.ka_checked = 0.0
        rk.keepalive_tick()
        check("keepalive: a queued run (a slot wait) is never pinged", not os.path.exists(os.path.join(fdir, "env.1")) and rk.ka_since is None)
        rk.state["state"] = "waiting"
        rk.keepalive_tick()
        check("keepalive: a run waiting on the owner still is (control)", os.path.exists(os.path.join(fdir, "env.1")))
        rk.state["state"] = "queued"
        gsave = {k: os.environ.get(k) for k in ("GIT_SSH_COMMAND", "GIT_SSH", "GIT_TERMINAL_PROMPT", "GIT_HTTP_LOW_SPEED_LIMIT", "GIT_HTTP_LOW_SPEED_TIME", "ASD_RUNNER_GIT_HARDEN")}
        for k in gsave:
            os.environ.pop(k, None)
        subprocess.run(["git", "init", "-q", root], capture_output=True)
        e1 = rk.child_env()
        check("git harden: no terminal prompt, low-speed abort, and a fail-fast ssh",
              e1.get("GIT_TERMINAL_PROMPT") == "0" and e1.get("GIT_HTTP_LOW_SPEED_LIMIT") == "1000" and e1.get("GIT_HTTP_LOW_SPEED_TIME") == "60"
              and e1.get("GIT_SSH_COMMAND") == "ssh -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4")
        os.environ["GIT_SSH_COMMAND"] = "ssh -i mine"
        check("git harden: an existing GIT_SSH_COMMAND is kept", rk.child_env().get("GIT_SSH_COMMAND") == "ssh -i mine")
        os.environ.pop("GIT_SSH_COMMAND"); os.environ["GIT_SSH"] = "/bin/mine"
        check("git harden: an existing GIT_SSH is kept (no GIT_SSH_COMMAND set over it)", "GIT_SSH_COMMAND" not in rk.child_env())
        os.environ.pop("GIT_SSH")
        subprocess.run(["git", "config", "core.sshCommand", "ssh -F repo"], cwd=root, capture_output=True)
        e3 = rk.child_env()
        check("git harden: a repo's own core.sshCommand is not overridden, the other variables still set", "GIT_SSH_COMMAND" not in e3 and e3.get("GIT_TERMINAL_PROMPT") == "0")
        subprocess.run(["git", "config", "--unset", "core.sshCommand"], cwd=root, capture_output=True)
        os.environ["ASD_RUNNER_GIT_HARDEN"] = "0"
        e4 = rk.child_env()
        check("git harden: ASD_RUNNER_GIT_HARDEN=0 turns it all off", not any(k in e4 for k in ("GIT_TERMINAL_PROMPT", "GIT_SSH_COMMAND", "GIT_HTTP_LOW_SPEED_LIMIT")))
        for k, v in gsave.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        # ---- a smaller prefix, the compact window, and a fresh stage instead of a compaction ----
        def argv_of(**e) -> list[str]:
            saved = {k: os.environ.get(k) for k in e}
            for k, v in e.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
            try:
                return rk.claude_cmd()
            finally:
                for k, v in saved.items():
                    if v is None: os.environ.pop(k, None)
                    else: os.environ[k] = v

        plain = argv_of(ASD_RUNNER_LEAN="0", ASD_RUNNER_COMPACT_WINDOW="0")
        check("lean off + no compact window: the argv of before (no --settings, --disallowedTools, --autocompact)",
              not {"--settings", "--disallowedTools", "--autocompact"} & set(plain) and plain[-2:] == ["--model", DEFAULT_MODEL])
        lean = argv_of(ASD_RUNNER_LEAN=None, ASD_RUNNER_COMPACT_WINDOW="0")
        sj = json.loads(lean[lean.index("--settings") + 1])
        check("lean on (the default): --settings turns context-mode off and lists the bundled skills by name only",
              sj["enabledPlugins"] == {"context-mode@context-mode": False, LEAN_PLUGIN_CONTEXT7: False} and sj["skillOverrides"]["verify"] == "name-only"
              and not any(v == "off" for v in sj["skillOverrides"].values()))
        dis = lean[lean.index("--disallowedTools") + 1].split(",")
        check("lean on: tools the pipeline never calls are disallowed; the ones it uses are not",
              {"CronCreate", "NotebookEdit", "EnterWorktree"} <= set(dis) and not {"Workflow", "Monitor", "SendMessage", "LSP", "ToolSearch", "Skill", "Task", "WebFetch"} & set(dis)
              and not any("nav" in d or "runs" in d for d in dis))
        rules_of = lambda a: a[a.index("--append-system-prompt") + 1]
        check("lean on: argv is the lean-off argv plus the three flags, nothing else changed (the rules differ by the CLAUDE.md pointer only)",
              [x for x in lean if x not in (lean[lean.index("--settings") + 1], lean[lean.index("--disallowedTools") + 1], "--settings", "--disallowedTools", rules_of(lean))]
              == [x for x in plain if x != rules_of(plain)]
              and rules_of(lean).replace(CLAUDE_MD_POINTER, "") == rules_of(plain))
        # ---- the owner's standing rulings: stored per repo, applied to every session of that repo, never past the deny list ----
        rrepo = main_repo_name(root)
        write_json(os.path.join(rulings_tmp, f"{rrepo}.json"), {"repo": rrepo, "rules": [
            {"rule": "Bash(limactl shell vm -- docker compose build *)", "kind": "allow", "by": "owner", "ts": now(), "question": "Q1"},
            {"rule": "Bash(gh pr *)", "kind": "allow", "by": "x", "ts": now(), "question": "Q2"},
            {"rule": "Bash(git push --force *)", "kind": "allow", "by": "x", "ts": now(), "question": "Q3"},
            {"rule": "Read(~/.ssh/id_rsa)", "kind": "allow", "by": "x", "ts": now(), "question": "Q4"}]})
        rl = argv_of(ASD_RUNNER_LEAN=None, ASD_RUNNER_COMPACT_WINDOW="0", ASD_RULINGS_DIR=rulings_tmp)
        rs = json.loads(rl[rl.index("--settings") + 1])
        check("rulings: a stored allow rule reaches the settings of every session of that repo, beside the lean settings",
              rs["permissions"]["allow"] == ["Bash(limactl shell vm -- docker compose build *)"] and rs["enabledPlugins"].get("context-mode@context-mode") is False)
        check("rulings: a stored rule that covers a refused category (merge, force push, secret path) is never applied, and the deny list rides along",
              "Bash(gh pr *)" not in rs["permissions"]["allow"] and "Bash(gh pr merge:*)" in rs["permissions"]["deny"] and "Bash(git push --force:*)" in rs["permissions"]["deny"])
        rp = argv_of(ASD_RUNNER_LEAN="0", ASD_RUNNER_COMPACT_WINDOW="0", ASD_RULINGS_DIR=rulings_tmp)
        check("rulings: they apply with lean off too, and without a lean flag the rest of the argv is unchanged",
              json.loads(rp[rp.index("--settings") + 1])["permissions"]["allow"] == rs["permissions"]["allow"] and "--disallowedTools" not in rp)
        os.remove(os.path.join(rulings_tmp, f"{rrepo}.json"))
        check("rulings: none stored, none passed", "permissions" not in json.loads(argv_of(ASD_RUNNER_LEAN=None, ASD_RUNNER_COMPACT_WINDOW="0", ASD_RULINGS_DIR=rulings_tmp)[argv_of(ASD_RUNNER_LEAN=None, ASD_RUNNER_COMPACT_WINDOW="0", ASD_RULINGS_DIR=rulings_tmp).index("--settings") + 1]))
        # ---- the session-start opt-outs: each on by default under lean, each with its own switch ----
        env_of = lambda **e: (lambda saved: (os.environ.update({k: v for k, v in e.items() if v is not None}), [os.environ.pop(k, None) for k, v in e.items() if v is None],
                                             Runner(root, "W-248", "work-on", None, "auto", 1).child_env(), [os.environ.pop(k, None) for k in e],
                                             [os.environ.__setitem__(k, v) for k, v in saved.items() if v is not None])[2])({k: os.environ.get(k) for k in e})
        sj_root = json.loads(lean[lean.index("--settings") + 1])
        check("lean opt-out (a): the target repo's CLAUDE.md and its worktrees' are excluded via claudeMdExcludes; the user's is not",
              sj_root.get("claudeMdExcludes") == [f"{os.path.abspath(root)}/CLAUDE.md", f"{os.path.abspath(root)}-*/CLAUDE.md"] and "CLAUDE.md" in rules_of(lean) and "grep -n" in rules_of(lean))
        off = argv_of(ASD_RUNNER_LEAN_CLAUDEMD="0", ASD_RUNNER_COMPACT_WINDOW="0")
        check("lean opt-out (a): ASD_RUNNER_LEAN_CLAUDEMD=0 leaves it loaded and drops the pointer",
              "claudeMdExcludes" not in off[off.index("--settings") + 1] and CLAUDE_MD_POINTER not in rules_of(off))
        off = argv_of(ASD_RUNNER_LEAN_CONTEXT7="0", ASD_RUNNER_COMPACT_WINDOW="0")
        check("lean opt-out (d): the context7 plugin is off in --settings, no other plugin is named; ASD_RUNNER_LEAN_CONTEXT7=0 keeps it",
              LEAN_PLUGIN_CONTEXT7 not in json.loads(off[off.index("--settings") + 1])["enabledPlugins"]
              and set(sj_root["enabledPlugins"]) == {"context-mode@context-mode", LEAN_PLUGIN_CONTEXT7})
        e_on = env_of(ASD_RUNNER_LEAN=None)
        check("lean opt-outs (b)(c): git instructions and auto-memory are off in the child's environment; telemetry is not touched",
              e_on.get("CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS") == "1" and e_on.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") == "1"
              and "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" not in e_on)
        e_b = env_of(ASD_RUNNER_LEAN_GIT="0"); e_c = env_of(ASD_RUNNER_LEAN_MEMORY="0"); e_0 = env_of(ASD_RUNNER_LEAN="0")
        check("lean opt-outs: each has its own switch, and ASD_RUNNER_LEAN=0 sets none",
              "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS" not in e_b and e_b.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") == "1"
              and "CLAUDE_CODE_DISABLE_AUTO_MEMORY" not in e_c and e_c.get("CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS") == "1"
              and "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS" not in e_0 and "CLAUDE_CODE_DISABLE_AUTO_MEMORY" not in e_0)
        check("lean ASD_RUNNER_LEAN=0 is exactly today's argv", argv_of(ASD_RUNNER_LEAN="0", ASD_RUNNER_COMPACT_WINDOW="0") == plain)
        cw = argv_of(ASD_RUNNER_LEAN="0", ASD_RUNNER_COMPACT_WINDOW=None)
        check("compact window: default 250000 as --autocompact", cw[cw.index("--autocompact") + 1] == "250000" and cw[:len(plain)] == plain)
        cw = argv_of(ASD_RUNNER_LEAN="0", ASD_RUNNER_COMPACT_WINDOW="300000")
        check("compact window: ASD_RUNNER_COMPACT_WINDOW sets it", cw[cw.index("--autocompact") + 1] == "300000")
        check("compact window: 0 passes none", "--autocompact" not in argv_of(ASD_RUNNER_LEAN="0", ASD_RUNNER_COMPACT_WINDOW="0"))
        # The plugin's own agent, command and skill descriptions ride in every call's prefix (about 3.1k tokens in all,
        # measured); a long one is a standing cost, so each stays at or under DESC_CAP characters.
        plug_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        long_desc = []
        for pat in ("commands/*.md", "skills/*/SKILL.md", "sub-agents/*.md", "primary-agents/*.md"):
            for df in sorted(glob.glob(os.path.join(plug_root, pat))):
                dfm = re.match(r"---\n(.*?)\n---", open(df, encoding="utf-8").read(), re.S)
                ddm = re.search(r"^description:\s*(.*?)(?=^\S|\Z)", dfm.group(1), re.S | re.M) if dfm else None
                if ddm and len(" ".join(ddm.group(1).split())) > DESC_CAP:
                    long_desc.append(os.path.relpath(df, plug_root))
        check(f"plugin descriptions stay at or under {DESC_CAP} characters (they ride in every call's prefix): {long_desc}", not long_desc)
        check("compact window: none for a 200k-window model, one for the 1M models",
              compact_window("claude-haiku-4-5") == 0 and compact_window("claude-opus-4-1") == 0
              and compact_window("claude-opus-5-5") == 250000 and compact_window("claude-sonnet-5-5") == 250000)
        saved_win = os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW")
        os.environ["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "100000"
        check("compact window: the env variable that would outrank the flag is dropped from the child's env",
              "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in rk.child_env())
        if saved_win is None: os.environ.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW")
        else: os.environ["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = saved_win
        full = rk.claude_cmd()
        check("keepalive resume argv with lean and compact window on is still claude_cmd() + --resume",
              "--settings" in full and "--autocompact" in full)

        # a split: a checkpoint taken with the context above ASD_RUNNER_SPLIT_AT ends the session there
        for k, v in sv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        env["ASD_RUNNER_SPLIT_AT"] = "135000"
        scenario([
            {"turns": [[{"checkpoint": ["Phase 3", "built\nNEXT: Phase 4"], "ctx": 150000}, {"final": "RUNNER: STAGE-DONE Phase 3"}]]},
            {"turns": [[{"checkpoint": ["Phase 4", "wave\nNEXT: Phase 6"], "ctx": 120000}, {"final": "RUNNER: STAGE-DONE Phase 4"}]]},
            {"turns": [[{"final": "RUNNER: DONE"}]]},
        ])
        p = start("W-260")
        check("split: the run finishes with a process per stage", p.wait(timeout=60) == 0 and open(os.path.join(fdir, "count")).read() == "3")
        t260 = texts("W-260")
        check("split: a checkpoint at 150k (limit 135k) logs 'split at checkpoint P: context Nk'", "split at checkpoint Phase 3: context 150k" in t260)
        check("split: a checkpoint at 120k does not split", not any(t.startswith("split at checkpoint Phase 4") for t in t260))
        m2 = msgs(2)
        check("split: the next stage starts with the hand-off from that checkpoint",
              m2[0] == f"/{PLUGIN}:work-on W-260" and m2[1].startswith("RUNNER HAND-OFF") and "1. Phase 3: built" in m2[1] and m2[1].rstrip().endswith("Continue from there; do not redo finished stages.") and "NEXT: Phase 4" in m2[1])
        check("split: the cut session is gone and the stages end in order",
              not alive(int(open(os.path.join(fdir, "pid.1")).read())) and [x["text"] for x in lines_of(os.path.join(rdir, "repo.W-260.jsonl")) if x.get("kind") == "runner" and x["text"].startswith("stage 1 end")] == ["stage 1 end: Phase 3"]
              and [c["name"] for c in read_json(os.path.join(rdir, "repo.W-260.checkpoints.json"), [])] == ["Phase 3", "Phase 4"])
        scenario([{"turns": [[{"usage": 170000}, {"final": "RUNNER: DONE"}]]}])
        p = start("W-261")
        check("split: none without a checkpoint, however large the context (auto-compaction is left to the CLI)",
              p.wait(timeout=60) == 0 and open(os.path.join(fdir, "count")).read() == "1" and not any(t.startswith("split at") for t in texts("W-261")))
        scenario([{"turns": [[{"checkpoint": ["Phase 3", "built\nNEXT: Phase 4"], "ctx": 150000, "go_on": True}, {"final": "RUNNER: STAGE-DONE Phase 3"}]]},
                  {"turns": [[{"final": "RUNNER: DONE"}]]}])
        env["ASD_RUNNER_SPLIT_AT"] = "0"
        p = start("W-262")
        check("split: ASD_RUNNER_SPLIT_AT=0 turns it off (the stage ends by its own STAGE-DONE)",
              p.wait(timeout=60) == 0 and not any(t.startswith("split at") for t in texts("W-262")) and "stage 1 end: Phase 3" in texts("W-262"))
        env["ASD_RUNNER_SPLIT_AT"] = "135000"
        scenario([{"turns": [[{"checkpoint": ["Phase 3", "built\nNEXT: Phase 4"], "ctx": 150000, "go_on": True}, {"final": "RUNNER: STAGE-DONE Phase 3"}]]}])
        p = start("W-263", "--max-stages", "1")
        check("split: it counts as a stage for --max-stages (the run stops there, the hand-off kept)",
              p.wait(timeout=60) == 4 and "split at checkpoint Phase 3: context 150k" in texts("W-263")
              and [c["name"] for c in read_json(os.path.join(rdir, "repo.W-263.checkpoints.json"), [])] == ["Phase 3"])
        env.pop("ASD_RUNNER_SPLIT_AT")

        # ---- the stall watchdog ----
        env["ASD_STALL_MIN"] = "0.03"
        scenario([{"stall": True}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-320")
        rc = p.wait(timeout=90)
        t320 = texts("W-320")
        check("stall: a session silent mid-turn is killed and the stage restarts, with no owner question",
              rc == 0 and any(t.startswith("stalled: no output for") and "(last: Bash)" in t for t in t320) and not any("asked" in t for t in t320)
              and any("stage 1 again from its checkpoints (stall 1 of 2)" in t for t in t320))
        check("stall: the diagnostic names the last event and lists the process tree", any(t.startswith("stall diagnostic: last stream event: assistant Bash") and "pid " in t and "etime" in t for t in t320))
        check("stall: the restart resumes the same session", msgs(2)[0].startswith("RUNNER RESUME"))
        scenario([{"stall": True}, {"stall": True}, {"stall": True}])
        p = start("W-321")
        asked = until(lambda: any("asked" in t for t in texts("W-321")), 90)
        t321 = texts("W-321")
        check("stall: the third stall in a stage asks the owner, the first two did not",
              bool(asked) and sum(1 for t in t321 if "again from its checkpoints (stall" in t) == 2 and any("stage 1 failed: stalled: no output for" in t for t in t321)
              and sum(1 for t in t321 if "asked" in t) == 1)
        q321 = (state_of("W-321").get("questions") or [""])[0]
        owner_line(q321, "answer", "Stop", "B")
        p.wait(timeout=30)
        env["ASD_STALL_MIN"] = "0"
        scenario([{"turns": [], "hang": True}])
        p = start("W-322")
        until(lambda: state_of("W-322").get("claude_pid"))
        time.sleep(3)
        check("stall: ASD_STALL_MIN=0 disables the watchdog", p.poll() is None and not any("stalled" in t for t in texts("W-322")))
        p.terminate(); p.wait(timeout=30)
        os.remove(os.path.join(rdir, "repo.W-322.runner.json"))
        env.pop("ASD_STALL_MIN")

        # ---- revive: every working state, a reused runner pid ----
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}] * 4)
        sleeper = subprocess.Popen(["sleep", "120"])
        for dos, stt, pid in (("W-310", "queued", 2 ** 22 + 12351), ("W-311", "waiting", 2 ** 22 + 12352), ("W-312", "paused", 2 ** 22 + 12353), ("W-313", "running", sleeper.pid)):
            write_json(os.path.join(rdir, f"repo.{dos}.runner.json"), {"pid": pid, "state": stt, "root": root, "command": "work-on", "stage": 1})
        r = subprocess.run([sys.executable, me, "revive"], env=env, capture_output=True, text=True, timeout=90)
        ok = until(lambda: all(state_of(d).get("state") == "done" for d in ("W-310", "W-311", "W-312", "W-313")), 60)
        sleeper.kill(); sleeper.wait()
        check("revive: queued, waiting, paused runs whose runner died are restarted", all(f"restarted repo.{d}" in r.stdout for d in ("W-310", "W-311", "W-312")) and bool(ok))
        check("revive: a dead runner whose pid now belongs to an unrelated process is restarted too", "restarted repo.W-313" in r.stdout)

        # Before the temp dir goes: every fake claude process and runner child this test started must be gone.
        for _ in range(100):
            pids = [int(open(os.path.join(fdir, f)).read() or 0) for f in os.listdir(fdir) if f.startswith("pid.")]
            if not any(alive(x) for x in pids):
                break
            time.sleep(0.1)
        time.sleep(0.3)

    leaked = {d: sorted(n - before_real[d]) for d, n in listing().items() if n - before_real[d]}
    check("no fixture file was created in the real questions or runs dir during the selftest", not leaked)
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
