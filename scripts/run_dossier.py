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
    RUNNER: WAITING ci:<pr>[,ci:<pr>..]
                                `wait_ci.py` runs here (no tokens spent, the slot is free meanwhile); the verdict goes into
                                the same live process as `CI RESULT <pr>: green|red|timeout <failing checks + log tail>`
    RUNNER: WAITING merged:<pr>[,merged:<pr>..]
                                planning is blocked on code only that pull request adds: the runner polls the PR (no tokens,
                                the slot is free, the dashboard says "waiting for PR n to merge") and sends
                                `PR RESULT <pr>: merged|closed ..` into the same live process; closed unmerged is the owner's call
    RUNNER: ENV <vm|stack|disk> <detail>
                                a broken environment (the Lima VM down, a compose service unhealthy, the disk low),
                                reported instead of asked about; with ASD_STACK_REPAIR=1 (or a /stub run) the runner repairs vm and
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
import fcntl
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
import stack_env  # noqa: E402
import driver_modes  # noqa: E402
import planning_runs  # noqa: E402
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
- To wait for CI never run `wait_ci.py` yourself in a loop (every call is a full turn): push, then end the turn with `RUNNER: WAITING ci:<pr number>` (several: `RUNNER: WAITING ci:12,ci:13`, one line per PR comes back together; never mix with question ids). The runner runs `wait_ci.py` for you (up to hours, no tokens) and sends `CI RESULT <pr>: green|red|timeout|never-started <failing check names and the log tail>` into this session. On `red`, fix and push, then wait again the same way; on `timeout`, wait once more, then ask the owner; on `never-started` CI did not run (it did not fail: GitHub created no run for the head, even after one close/reopen), so never wait on that PR again and end with `RUNNER: FAILED CI never started on PR <pr>: ...`. Use `wait_ci.py --job-log` / `--rerun-failed` directly only for a one-off read of a log.
- Never end a turn to wait for a command: run commands in the foreground (raise the Bash timeout up to 10 minutes) or wait for them inside the same turn. A workflow or background task that notifies you later is fine, but say `waiting for <what>` in plain text in that turn (never as a `RUNNER:` line); the runner keeps the session open for its notification.
- When the auto-mode safety check blocks a call you need, never work around it: ask the owner with `ask_owner.py ask`, quoting the exact command (or path) and what it is for, with options to allow it once, to allow it as a standing rule, or to go without it; carry on with what is not blocked. The dashboard's Blocked card answers that question; an approval names the exact command, so retry it as written.
- Run every test through scripts/run_tests.py and every gate list through scripts/run_gates.py (`--path-prepend DIR` / `--env-script FILE` for a toolchain off the shell PATH, `--detach` then `--wait ID` for a long run), never through a script or command of your own: their result lines are how the owner's timeline shows a suite going green or red. Run only the tests the command's step names: under /work-on and /fix-deferred with SUITE-MODE `stub`, that is the new and affected tests and the pull request's CI, never the full workspace or `--stack` suite, which only /stub runs.
- Never end a stage (or the run) while a background command, workflow or agent you started is still running: it dies with this process and no notification reaches the next stage. Wait for its notification and take its result first; the runner holds a stage end that comes too early, once. A task you stopped or killed yourself needs no wait: end the stage, and the runner cuts off whatever is left and logs it. Never ask the owner whether to end the stage, restart or stop a background task. If a checkpoint must still name work in flight, say how the next stage tells it finished or died (its report file, its branch or worktree, its output file).
- Gates already settled for this run: the worktree gate is yes, and {time_logging}
- The owner's Start is standing authority (references/owner-questions.md, "What is worth asking"): pushing the dossier's own branch and opening or updating its draft PR into the base or the accepting stub is yes; so is a graduation issue in the plugin repository, and committing a repository rule from this build on the dossier's branch (the PR ratifies it). Carry out the readings, defaults and rulings the dossier already records; never ask to confirm them. Ask only technical, architectural or product design questions the dossier leaves open, issues you cannot resolve in scope, and actions outside that authority (a merge, a push to a protected branch, a force-push, Jira writes, deleting what is not the run's own). Do the authorized ones, log them, and name them in your next update.
- The plugin's references (`references/work-on/phase-*.md`, `formats/*`, `runner.md`) are long and the same in every stage: read only the section your step needs (`ref_section` with the document's short name, `phase-4` or `formats/dossier`, and a heading or `--list`), not whole files; the phase file you start a phase with is the one you read whole, and never the same section twice in one stage.
- Phase 2 takes its stub from `python3 scripts/stub_target.py --root R` (the plugin's scripts). Exit 3 (`wait <stub>`) means that stub is being integrated and no new one may be cut until it is in development: end the turn with the line `RUNNER: WAITING stub`; the runner waits without tokens and sends `STUB READY: <branch>` once the new stub is cut. Never cut a stub yourself.
- A broken environment (the Lima VM stopped or unreachable, a shared docker compose service unhealthy, the disk low) is not an owner question: end the turn with the single line `RUNNER: ENV <vm|stack|disk> <short detail>` and nothing after it. The runner repairs vm and stack when the owner enabled that and answers `RUNNER ENV <kind>: repaired ...` (carry on) or `RUNNER ENV vm: not repaired ...` (then ask the owner as usual). For `stack` the runner never answers with an owner question: a bring-up that a foreign or leftover compose project refuses (`foreign_project_state owner=...`, `suite_window_active holder=...`) is reported as `RUNNER: ENV stack <the refusal line>`; the runner tears a leftover project down itself when it can prove it dead and answers `repaired`, otherwise it answers to skip your live stack tests, defer those criteria to the /stub full suite and head the PR note with `STACK-ENV: skipped — ...`. You never tear down, bring up or restart a shared stack project yourself (opmode-stack.sh, up.sh, compose) and never ask the owner about it. A disk is never repaired automatically. A stale shared fake-llm fixture is the runner's: run tests through `run_tests.py --vm` (it rebuilds from the persistent checkout or skips the DB tests under `STACK-ENV: skipped`); never rebuild or restart a shared stack service from your worktree, and never ask the owner about it.
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
# A sign-in the API refused for a reason outside the account: Cloudflare's bot page in front of the API ("403 Attention
# Required! | Cloudflare", shown by the CLI as "Failed to authenticate") blocks the current network address for a while,
# then lets it through again. Retried like a network error, but only ASD_RUNNER_AUTH_RETRIES times in a row (default 16,
# about 3.5 hours at the network backoff): a sign-in that stays refused that long needs the owner.
AUTH_OUTAGE_RE = re.compile(r"Attention Required|cloudflare|Failed to authenticate|API Error: 40[13]\b|authentication_error"
                            r"|OAuth token has expired|invalid x-api-key", re.I)
AUTH_RETRIES = 16
# An account whose login is gone ("Not logged in · Please run /login", "Invalid API key · Please run /login"): not a
# Cloudflare block and not a failure of the run. A run that can start a fresh session moves to a signed-in account; one that
# must resume its session waits for the owner to sign the account in again (see Runner.logged_out_wait).
LOGGED_OUT_RE = re.compile(r"Not logged in|run /login|Invalid API key", re.I)
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
    """(the runner script a fresh start would use, its mtime). Under a plugin cache (.../<version>/scripts/, where the
    version directory is a semver such as 1.52.57 or a 12-hex-char commit SHA such as 224e8127ba5d) a plugin update
    installs a new directory beside the old one: among the sibling directories of either shape that hold this script,
    the most recently installed wins, by the mtime of the script file. Version ordering plays no part (a SHA has none).
    A path not under a plugin cache (the main checkout) is used as it is."""
    path = os.path.abspath(path)
    scripts = os.path.dirname(path)
    ver_dir = os.path.dirname(scripts)
    parent = os.path.dirname(ver_dir)
    def is_ver(n: str) -> bool:
        return bool(re.fullmatch(r"\d+(?:\.\d+)+|[0-9a-f]{12}", n or ""))
    def mtime(p: str) -> float:
        try:
            return os.stat(p).st_mtime
        except OSError:
            return -1.0
    if is_ver(os.path.basename(ver_dir)):
        try:
            cands = [os.path.join(parent, n, os.path.basename(scripts), os.path.basename(path)) for n in sorted(os.listdir(parent))
                     if is_ver(n)]
            cands = [c for c in cands if os.path.isfile(c)]
        except OSError:
            cands = []
        if cands:
            path = max(cands, key=lambda c: (mtime(c), c == path))
    try:
        return path, os.stat(path).st_mtime
    except OSError:
        return path, 0.0


def network_error(text: str) -> bool:
    """Whether a failed turn is a transient network or server error (wait and retry the stage), not a real failure."""
    return bool(NETWORK_RE.search(text or "") or AUTH_OUTAGE_RE.search(text or "")) and not usage_limit(text) and not logged_out(text)


def auth_outage(text: str) -> bool:
    """Whether a failed turn is a refused sign-in (a Cloudflare block of this network, an expired token): retried a
    bounded number of times, then asked."""
    return bool(AUTH_OUTAGE_RE.search(text or "")) and not usage_limit(text) and not logged_out(text)


def logged_out(text: str) -> bool:
    """Whether a failed turn says the account is signed out (the owner must run /login), not an outage or a limit."""
    return bool(LOGGED_OUT_RE.search(text or "")) and not usage_limit(text)


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


# ---- a signed-out account: `claude auth status` (its `loggedIn` only), cached ~60 s per account in runs_dir() ----
# $ASD_AUTH_STATUS_CMD replaces the command (tests); it runs with the account's CLAUDE_CONFIG_DIR. Anything but a clear
# `"loggedIn": false` counts as signed in, so a broken check never blocks an account. Nothing it prints is kept.


def auth_path(name: str) -> str:
    return os.path.join(runs_dir(), f"auth-{name}.json")


def auth_question_path(name: str) -> str:
    return os.path.join(runs_dir(), f"loggedout-q-{name}.json")


def auth_status(acc: dict, force: bool = False) -> bool:
    """Whether the account is signed in; the answer is cached ASD_AUTH_TTL (60) s, `force` asks again."""
    path, t = auth_path(acc["name"]), now_s()
    c = read_json(path, {}) or {}
    if not force and isinstance(c.get("loggedIn"), bool) and isinstance(c.get("ts"), (int, float)) and abs(t - c["ts"]) < env_num("ASD_AUTH_TTL", 60):
        return c["loggedIn"]
    cmd = shlex.split(os.environ.get("ASD_AUTH_STATUS_CMD") or "") or shlex.split(os.environ.get("ASD_CLAUDE_BIN") or "claude") + ["auth", "status"]
    ok = True
    try:
        r = subprocess.run(cmd, env=dict(os.environ, CLAUDE_CONFIG_DIR=acc["dir"]), capture_output=True, text=True, timeout=30)
        m = re.search(r'"loggedIn"\s*:\s*(true|false)', r.stdout or "")
        ok = not (m and m.group(1) == "false")
    except (OSError, subprocess.SubprocessError):
        pass
    if ok != c.get("loggedIn", True):
        try:   # the paced cap's rows follow the change at once
            os.remove(os.path.join(runs_dir(), "slots-cap.json"))
        except OSError:
            pass
    try:
        write_json(path, {"loggedIn": ok, "ts": t})
        if ok:
            os.remove(auth_question_path(acc["name"]))   # signed in again: the next sign-out asks once more
    except OSError:
        pass
    return ok


def account_signed_out(acc: dict) -> bool:
    """Whether an account is known to be signed out: only an account a run saw refuse is checked (no spawn for the rest)."""
    c = read_json(auth_path(acc["name"]), {}) or {}
    return c.get("loggedIn") is False and not auth_status(acc)


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
# A CI wait: `ci:<pr number>`.
CI_ID = re.compile(r"^ci:\d+$")
# A merge wait: `merged:<pr number>`, planning blocked on code only that PR adds (see merge_wait).
MERGE_ID = re.compile(r"^merged:\d+$")
CI_PARENT_NEXT = ("Next step: the cause is the parent PR of #{n} (see above). The runner already had a resolve-merge run work on it and "
                  "it still conflicts: do NOT resolve it yourself (that PR is not yours to edit) and do NOT ask the owner how. End the turn with "
                  "`RUNNER: FAILED CI not started on PR #{n}: its parent PR still conflicts after the runner's resolve-merge` so it is reported as a blocker.")
# `wait_ci.py` exit 7: "PR #76 is stacked on #620 (branch ...), which conflicts with ..."
PARENT_RE = re.compile(r"stacked on #(\d+)")
CI_NEVER_STARTED_NEXT = ("Next step: do NOT end the turn with RUNNER: WAITING ci:{n} again (the runner will not wait on it). "
                         "State in your summary that CI did not run on PR #{n} (not that it failed; GitHub's mergeability for it is "
                         "unknown and a close/reopen did not start a run), then end the turn with `RUNNER: FAILED CI never started on "
                         "PR #{n}: GitHub created no workflow run for the head, the owner must unstick the PR` so it is reported as a blocker.")
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


# A free-form /plan (`--seed FILE`): no dossier exists yet, so the run is keyed by a slug, `<repo>.seed-<slug>`, and the
# description is kept beside the run's records (`<key>.seed.md`), where every restart (revive, Resume) finds it again.
SEED_RE = re.compile(r"seed-[a-z0-9][a-z0-9-]{0,60}")
SEED_MAX = 64 * 1024


def seed_path(root: str, dossier: str) -> str:
    return os.path.join(runs_dir(), key_of(root, dossier) + ".seed.md")


def store_seed(root: str, dossier: str, src: str) -> str:
    """Copy the seed file to `<key>.seed.md` (atomically); "" when stored, else why not."""
    try:
        text = open(src, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError) as e:
        return f"cannot read the seed {src}: {e}"
    if not text.strip():
        return f"the seed {src} is empty"
    if len(text.encode("utf-8")) > SEED_MAX:
        return f"the seed {src} is over {SEED_MAX // 1024} KiB; a seed is a brief, not a document"
    dst = seed_path(root, dossier)
    st = read_json(dst[:-len(".seed.md")] + ".runner.json", {}) or {}
    if os.path.exists(dst) and open(dst, encoding="utf-8").read() != text and st.get("state") in ("running", "waiting", "queued", "paused", "testing"):
        return f"a run of {dossier} with another seed is live; pick another slug"
    os.makedirs(runs_dir(), exist_ok=True)
    tmp = dst + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, dst)
    return ""


def run_key(root: str, dossier: str, command: str = "work-on", items: str = "") -> str:
    """The run's key: `<repo>.<ID>`, and for a `plan` run of a routed deferred item `<repo>.<ID>.plan-<D-n>`, so the
    planners of several items run at once, beside the source dossier's own run. A `resolve-merge` run of given PRs is
    `<repo>.<ID>.resolve-<pr>`: the runner starts one for a conflicting parent PR while the dossier's own session waits on
    its child's CI, and a second run under the dossier's own key would be refused as live."""
    return key_of(root, dossier) + (f".plan-{items}" if command == "plan" and items else "") + (
        f".resolve-{items.replace(',', '-')}" if command == "resolve-merge" and items else "")


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


def lock_path(key: str) -> str:
    return os.path.join(runs_dir(), key + ".runner.lock")


def try_lock(key: str):
    """The per-key runner lock: an exclusive flock on `<key>.runner.lock`, held by the runner process for its whole life
    (the kernel drops it when the process dies, however it dies). Returns the open file (keep it) or None when another
    runner holds it. The pid in the run record cannot guarantee one runner per key: two starts in the same moment both
    read a dead or absent pid. The lock can."""
    fh = open(lock_path(key), "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def lock_held(key: str) -> bool:
    """Whether a runner holds the key's lock right now (a probe: the lock is released again at once)."""
    fh = try_lock(key)
    if fh is None:
        return True
    fh.close()
    return False


def say(msg: str) -> None:
    print(f"[run_dossier] {msg}", flush=True)


def user_message(text: str) -> bytes:
    msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]},
           "parent_tool_use_id": None}
    return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")


def set_dossier_status(root: str, dossier: str, status: str, log=lambda m: None) -> bool:
    """Set `status:` in the dossier's front matter (.discovery/dossiers/<ID>-*.md), when it differs. Best effort.
    The write goes through `dossier_edit.py set`, so a committed-mode main checkout is never edited: the dossier's
    worktree gets it, or its notes file; a dossier whose branch is open but whose worktree is gone is not written
    (dossier_edit exits 3, WORKTREE GONE) and the message is logged."""
    d = os.path.join(root, ".discovery", "dossiers")
    try:
        names = sorted(n for n in os.listdir(d) if re.match(re.escape(dossier) + r"(-.*)?\.md$", n))
    except OSError:
        return False
    if not names:
        return False
    path = os.path.join(d, names[0])
    text = dossier_text(path)
    if text is None or not text.startswith("---\n"):
        return False
    if front_status(text) == status:
        return True
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "dossier_edit.py"), "set", path, f"status={status}"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"dossier {dossier}: could not set status {status}: {e}")
        return False
    if r.returncode != 0:
        log(f"dossier {dossier}: status {status} not written (dossier_edit exit {r.returncode}): "
            + (r.stderr.strip() or r.stdout.strip())[:600])
        return False
    log(f"dossier {dossier}: status set to {status}")
    return True


def dossier_text(path: str) -> str | None:
    """A dossier's text as a reader should see it: with its notes file (.discovery/notes/<file>) laid over it,
    because the main checkout's committed copy is never written (dossier_edit.redirect_path). None when unreadable."""
    try:
        import dossier_edit  # noqa: PLC0415 (scripts/ is on sys.path)
        return dossier_edit.read_overlaid(path)
    except OSError:
        return None


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
    {"label", "id", "output", "tool"}: a tool call whose result says it went to the background, minus every
    `<task-notification>` naming its id and every task stopped on purpose (a stop sends no notification). A stop is
    read from the stop call's own input (TaskStop task_id), so any result text that is not an error ends the task, and
    the "stopped" / "No task found" texts end it too."""
    calls: dict[str, str] = {}
    tools: dict[str, str] = {}      # tool_use id -> the tool's name (Bash, Agent, Workflow, ...)
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
                    tools[b["id"]] = str(b.get("name") or "")
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
                        bg[tid] = {"label": calls[tid], "id": m.group(1) if m else "", "output": out.group(1) if out else "", "tool": tools.get(tid, "")}
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

# a stage the agent ended with "duplicate run: <id> is already owned by the live run <key> ...; this run changed nothing"
DUPLICATE_RUN_RE = re.compile(r"duplicate run\b[^\n]*\bchanged nothing", re.I)
TESTING_HUNG_MIN = 240      # a known test/CI wait is hung only after this many minutes (ASD_TESTING_HUNG_MIN), not the plain stall limit
TESTING_QUIET_SECS = 300    # an in-flight Bash call with no output for longer than this counts as a long wait
# the long waits we know by name (any flags, `--wait` included): tests, gates, a hand-written poll on a detached run's
# state directory, and CI (wait_ci.py, `gh pr checks --watch`, `gh run watch`; a fix-deferred session held its slot for
# the whole of a Rust CI job in wait_ci.py)
TESTING_CMD = re.compile(r"\brun_(?:tests|gates)(?:\.py\b|-detach/)|\bwait_ci\.py\b|\bfix_deferred_pr\.py\b|\bgh\s+(?:pr\s+checks\b[^\n]*--watch|run\s+watch)\b")


def wait_label(cmd: str) -> str:
    """A readable label for a long Bash call: the `;`/`&&` segment that runs the test or CI wait (else the first segment
    that is not a bare `VAR=value` assignment or a `cd`), at most 80 characters. Runner sessions open most commands with
    `P=<plugin cache path>; ...`, so the first line alone names only that path."""
    text = cmd.strip()
    if not text:
        return "a Bash call"
    segs = [x.strip() for x in re.split(r"&&|;|\n", text) if x.strip()]
    hit = next((x for x in segs if TESTING_CMD.search(x)), None)
    if hit is None:
        hit = next((x for x in segs if not re.fullmatch(r"(?:\w+=\S*\s*)+", x) and not re.match(r"cd\s", x)), segs[0])
    return re.sub(r"\$P/scripts/|\S*/scripts/", "", hit)[:80]
# Stream lines the CLI emits while a foreground tool call is still running (a Bash call turns into a task after a few
# seconds and reports progress). They say the call is alive, not that the session works: they keep a call from looking
# stalled, but never from looking like a long wait.
PROGRESS_EVENTS = ("tool_progress",)
PROGRESS_SUBTYPES = ("task_started", "task_progress")
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
BURN_MIN_SAMPLES = 3   # an account's own rate replaces the pooled one once it has this many samples (ASD_BURN_MIN_SAMPLES)


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
    """{window: points per hour of one running run}: the pooled EWMA in burn.json, else the prior."""
    cur = (read_json(BURN_FILE(), {}) or {}).get("rates") or {}
    return {w: float(cur[w]) if isinstance(cur.get(w), (int, float)) and cur[w] > 0 else p for w, p in BURN_PRIOR.items()}


def burn_for(account: str) -> tuple[dict[str, float], dict[str, str]]:
    """({window: points per hour}, {window: "account"|"pooled"|"prior"}) for one account. Accounts are different plans,
    so an account's own EWMA is used once it has ASD_BURN_MIN_SAMPLES samples in that window, else the pooled one, else
    the prior. burn.json written before the per-account rates (no `by_account`) still loads: everything is pooled."""
    data = read_json(BURN_FILE(), {}) or {}
    pooled, mine = data.get("rates") or {}, (data.get("by_account") or {}).get(account) or {}
    need = max(1, int(env_num("ASD_BURN_MIN_SAMPLES", BURN_MIN_SAMPLES, int)))
    rates, src = {}, {}
    for w, prior in BURN_PRIOR.items():
        own = mine.get(w) if isinstance(mine, dict) else None
        ok = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
        if isinstance(own, dict) and ok(own.get("rate")) and isinstance(own.get("n"), (int, float)) and own["n"] >= need:
            rates[w], src[w] = float(own["rate"]), "account"
        elif ok(pooled.get(w)):
            rates[w], src[w] = float(pooled[w]), "pooled"
        else:
            rates[w], src[w] = prior, "prior"
    return rates, src


def learn_burn(account: str, wins: dict, t: float, running: int | None = None) -> None:
    """From a new reading: delta used% / (running runs on the account x delta hours) feeds the EWMA of each window
    (burn.json: the pooled rate per window, and each account's own rate and sample count in `by_account`). An interval that crosses a reset, runs over 2 h, has no run working,
    or shows the percentage falling is skipped. The baseline stays put until 5 minutes or 1 point have passed."""
    if not account:
        return
    n = running_by_account().get(account, 0) if running is None else running
    path = BURN_FILE()
    cur = read_json(path, {}) or {}
    last, rates = cur.get("last") or {}, dict(cur.get("rates") or {})
    by_acct = {k: dict(v) for k, v in (cur.get("by_account") or {}).items() if isinstance(v, dict)}
    own = {w: dict(v) for w, v in (by_acct.get(account) or {}).items() if isinstance(v, dict)}
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
                    # the account's own EWMA starts from the first sample (the pooled rate is another plan's), and counts them
                    o = own.get(w) or {}
                    if isinstance(o.get("rate"), (int, float)) and o["rate"] > 0:
                        o = {"rate": round(o["rate"] * (1 - BURN_ALPHA) + r * BURN_ALPHA, 4), "n": int(o.get("n") or 0) + 1}
                    else:
                        o = {"rate": round(r, 4), "n": 1}
                    own[w] = o
        mine[w] = new
    last[account] = mine
    by_acct[account] = own
    try:
        write_json(path, {"rates": rates, "by_account": by_acct, "last": last})
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
    rates = burn_rates()   # pooled; each account row uses burn_for(name)
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
        if account_signed_out(a):
            limited += 1
            rows.append({"account": name, "limited": True, "signedout": True, "runs": 0})
            parts.append(f"{name} signed out → 0")
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
        ar, src = burn_for(name)
        if u7 >= 98:
            runs = 0.0
        else:
            runs5 = (100 - PACE_MARGIN["five_hour"] - u5) / (ar["five_hour"] * max(h5, 0.25))
            runs7 = (100 - PACE_MARGIN["seven_day"] - u7) / (ar["seven_day"] * max(h7, 1.0))
            runs = max(0.0, min(runs5, runs7))
        total += runs
        rows.append({"account": name, "five_hour": u5, "h5": round(h5, 2), "seven_day": u7, "h7": round(h7, 1), "runs": round(runs, 2),
                     "burn": ar, "burn_from": src})
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
    c = paced_cap()
    return c["cap"], c["reason"]


def paced_cap() -> dict:
    """compute_cap(), cached ~60 s in slots-cap.json (ASD_PACE_TTL)."""
    path, t = os.path.join(runs_dir(), "slots-cap.json"), now_s()
    c = read_json(path, {}) or {}
    if c.get("mode") == "adaptive" and isinstance(c.get("ts"), (int, float)) and abs(t - c["ts"]) < env_num("ASD_PACE_TTL", 60):
        return c
    c = compute_cap(t)
    try:
        write_json(path, c)
    except OSError:
        pass
    return c


def headroom_pick(rows: list, running: dict, current: str) -> tuple[str, str]:
    """(account, why) for a run with no session to resume: the account with the most paced headroom, i.e. the largest
    `runs` of compute_cap's rows minus the runs already working on it. Limited accounts and accounts with no reading are
    never chosen; a tie, or no reading on the current account, keeps `current`; a limited current account moves to the best."""
    score = {r["account"]: float(r.get("runs") or 0) - running.get(r["account"], 0) for r in rows or []
             if isinstance(r, dict) and r.get("account") and not r.get("limited") and not r.get("signedout") and not r.get("unknown")}
    if not score:
        return current, ""
    cur_row = next((r for r in rows if isinstance(r, dict) and r.get("account") == current), None)
    if cur_row is not None and cur_row.get("unknown"):
        return current, ""
    best = max(score, key=lambda k: (score[k], k == current))
    if best == current or (current in score and score[best] <= score[current]):
        return current, ""
    return best, ", ".join(f"{k} {v:.1f}" for k, v in sorted(score.items()))


QUEUE_RUN_COMMANDS = ("resolve-merge", "after-merge")   # the runs the merge queue starts (merge_dossier.QUEUE_RUNS): each unblocks merges


def queue_order(st: dict) -> tuple[int, str]:
    """A queued run's place: runs the owner just answered, and the merge queue's own runs (`QUEUE_RUN_COMMANDS`), carry
    `queued_first` and go before the rest, then longest-queued first."""
    return (0 if st.get("queued_first") else 1, str(st.get("queued_since") or ""))


def owner_replied_since(key: str, since: str) -> bool:
    """True when the owner wrote on one of run `key`'s questions (an answer or a follow-up), or sent the run a message,
    after `since`: a queued run with fresh owner input queues first, as an answered waiting run does."""
    try:
        t0 = datetime.datetime.fromisoformat(str(since).replace("Z", "+00:00"))
    except ValueError:
        return False
    qdir = os.environ.get("ASD_QUESTIONS_DIR") or os.path.expanduser("~/.agentic/questions")
    pat = re.compile(re.escape(key) + r"\.Q\d+\.jsonl")
    try:
        names = [n for n in os.listdir(qdir) if pat.fullmatch(n)]
    except OSError:
        names = []
    lines: list[dict] = []
    for n in names:
        try:
            with open(os.path.join(qdir, n)) as fh:
                lines += [json.loads(l) for l in fh if l.strip()]
        except (OSError, ValueError):
            continue
    try:
        with open(inbox_path(key)) as fh:
            lines += [{**d, "from": "owner"} for d in (json.loads(l) for l in fh if l.strip()) if isinstance(d, dict)]
    except (OSError, ValueError):
        pass
    for l in lines:
        if l.get("from") != "owner":
            continue
        try:
            if datetime.datetime.fromisoformat(str(l.get("ts") or "").replace("Z", "+00:00")) > t0:
                return True
        except ValueError:
            continue
    return False


def repo_of_key(key: str) -> str:
    """The repo name of a run key `<repo>.<ID>` (or `<repo>.<ID>.plan-<D-n>`): what key_of/run_key put in front of the ID."""
    return re.sub(r"\.(?:plan|resolve)-[^.]+$", "", key).rpartition(".")[0] or key


def slot_census_by_repo(me: str) -> tuple[dict[str, int], list[tuple[tuple[int, str], str]]]:
    """({repo: runs holding a slot}, [(queue_order, key)] of runs waiting for one), other than `me`, live pids only."""
    held: dict[str, int] = {}
    queued = []
    try:
        names = [n for n in os.listdir(runs_dir()) if n.endswith(".runner.json")]
    except OSError:
        return {}, []
    for n in names:
        key = n[: -len(".runner.json")]
        if key == me:
            continue
        st = read_json(os.path.join(runs_dir(), n), {}) or {}
        if not alive(st.get("pid")):
            continue
        if st.get("state") == "running":   # `testing` is alive but holds no slot
            r = repo_of_key(key)
            held[r] = held.get(r, 0) + 1
        elif st.get("state") == "queued":
            queued.append((queue_order(st), key))
    return held, sorted(queued)


def slot_overflow(me: str) -> int:
    """Live runs other than `me` that went back to work over the cap (`over_cap`, set by refresh_testing): they took a
    slot a reserved repo was owed, so that repo's first run does not wait on them (slot_admission's `over`)."""
    try:
        names = [n for n in os.listdir(runs_dir()) if n.endswith(".runner.json")]
    except OSError:
        return 0
    n_over = 0
    for n in names:
        if n[: -len(".runner.json")] == me:
            continue
        st = read_json(os.path.join(runs_dir(), n), {}) or {}
        if st.get("state") == "running" and st.get("over_cap") and alive(st.get("pid")):
            n_over += 1
    return n_over


def slot_census(me: str) -> tuple[int, list[tuple[tuple[int, str], str]]]:
    """(runs holding a slot, [(queue_order, key)] of runs waiting for one), other than `me`, live pids only."""
    held, queued = slot_census_by_repo(me)
    return sum(held.values()), queued


# ---- one reserved slot per active repo, the rest shared ----
# `~/.agentic/active-repos.json` (ASD_ACTIVE_REPOS_FILE) is `{"repos": {"<repo>": "<main checkout root>"}}`, keyed by the
# name the run keys use. Without the file nothing is reserved and every repo is shown. An active repo holds a reservation
# while it has a dossier whose front-matter status is not `done`; the reservation holds with no run queued. A repo
# gives it up when nothing is buildable. The dashboard shows only the active repos (shared/questions/activerepos.ts).


def active_repos_file() -> str:
    return os.environ.get("ASD_ACTIVE_REPOS_FILE") or os.path.expanduser("~/.agentic/active-repos.json")


def read_active_repos() -> dict[str, str] | None:
    """{repo: root} from the active-repos file; None when the file is missing or unreadable (today's behaviour)."""
    d = read_json(active_repos_file(), None)
    if not isinstance(d, dict) or not isinstance(d.get("repos"), dict):
        return None
    return {str(k): str(v) for k, v in d["repos"].items() if k}


def has_buildable_dossier(root: str) -> bool:
    """True when `<root>/.discovery/dossiers/*.md` holds a dossier (front matter) whose status is not `done`."""
    d = os.path.join(root, ".discovery", "dossiers")
    try:
        names = [n for n in os.listdir(d) if n.endswith(".md")]
    except OSError:
        return False
    for n in names:
        text = dossier_text(os.path.join(d, n))
        if text is None:
            continue
        if text.startswith("---\n") and front_status(text) != "done":
            return True
    return False


_RESERVED_CACHE: dict = {"at": 0.0, "key": None, "val": set()}


def reserved_repos() -> set[str]:
    """The active repos that hold a reservation now (a not-done dossier), cached ~60 s (ASD_RESERVE_TTL)."""
    active = read_active_repos()
    if not active:
        return set()
    key, t = json.dumps(sorted(active.items())), time.time()
    if _RESERVED_CACHE["key"] == key and abs(t - _RESERVED_CACHE["at"]) < env_num("ASD_RESERVE_TTL", 60):
        return set(_RESERVED_CACHE["val"])
    val = {r for r, root in active.items() if has_buildable_dossier(root)}
    _RESERVED_CACHE.update(at=t, key=key, val=val)
    return set(val)


def slot_admission(repo: str, cap: int, held_by_repo: dict[str, int], queued: list[tuple[tuple[int, str], str]],
                   mine: tuple[int, str] | None, reserved: set[str], over: int = 0) -> tuple[bool, str]:
    """(admit, how) for a run of `repo`; `how` is "reserved", "shared" or the reason it waits. `queued` excludes the run.
    - repo reserved and holding no slot: admitted when held - over < cap (at least `len(reserved)` slots count, so with a
      cap not above the reservations each reserved repo still runs one); only the repo's first queued run takes it.
      `over` counts the runs that came back from testing over the cap (slot_overflow): a returning run cannot queue (its
      session is mid-turn), so it borrows the slot, and the reserved repo it was owed to still starts;
    - otherwise admitted when held + (other reserved repos holding no slot) < cap and nobody is ahead in the shared queue."""
    held = sum(held_by_repo.values())
    idle = sorted(r for r in reserved if r != repo and not held_by_repo.get(r))
    if repo in reserved and not held_by_repo.get(repo):
        ahead_own = [k for o, k in queued if mine and o < mine and repo_of_key(k) == repo]
        if held - over < max(cap, len(reserved)) and not ahead_own:
            return True, "reserved"
        return False, f"waiting for the reserved slot of {repo}" + (" (an earlier run of this repo goes first)" if ahead_own else "")
    ahead = [k for o, k in queued if mine and o < mine]
    if held + len(idle) < cap and not ahead:
        return True, "shared"
    return False, "waiting for a shared slot" + (f" ({len(idle)} reserved for {', '.join(idle)})" if idle else "")


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
                 permission_mode: str, max_stages: int, items: str = "", plan_branch: str = ""):
        self.root = os.path.abspath(root)
        self.dossier = dossier
        self.command = command
        self.seed = seed_path(root, dossier) if command == "plan" and not items and SEED_RE.fullmatch(dossier) else ""
        self.items = items  # fix-deferred: the D-ids the owner selected, "D-1,D-3" (empty = all); after-merge: the merged PR numbers, "429,430"
        self.model = runner_model(model) if command != "product-summary" else model
        self.permission_mode = permission_mode
        self.max_stages = max_stages
        self.ci_never_started: set[int] = set()
        self.parent_tries: dict[int, int] = {}   # parent PR -> resolve-merge runs the runner started for it
        prev = read_json(os.path.join(runs_dir(), run_key(root, dossier, command, items) + ".runner.json"), {}) or {}
        prev_acct = prev.get("account", "")
        # /work-on stacked on a re-plan's unmerged plan branch (committed mode): a restart of the same unfinished build
        # keeps the branch it was started with.
        if command == "work-on" and not plan_branch and prev.get("command") == "work-on" and prev.get("state") != "done":
            plan_branch = str(prev.get("plan_branch") or "")
        self.plan_branch = plan_branch
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
        if self.plan_branch:
            self.state["plan_branch"] = self.plan_branch
        self.driver_exports: dict = {}      # ASD_DRIVER_<NAME> the drivers file adds to the claude child (see start_drivers)
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
        self.bg_cmds_only = False           # background() saw only Bash tasks in flight
        self.bg_wait = ""                   # turn ended and only background shell commands run (a CI wait): what it waits on
        self.last_out = time.monotonic()    # monotonic time of the last stream event (or message sent) of the session
        self.last_work = self.last_out      # the same, without the progress lines of a tool call still in flight
        self.last_ev = "none"               # the last stream event: its type and tool name, for the stall diagnostic
        self.tool_names: dict = {}          # tool_use id -> tool name
        self.inflight_bash: dict = {}       # Bash tool_use id -> (command, monotonic start): calls with no result yet
        self.stall_bg_at = 0.0              # when the transcript was last checked for background work during a quiet spell
        self.ka_checked = 0.0               # monotonic time of the last keep-alive check
        self.ka_cold: set[str] = set()      # sessions whose cache a ping found cold: not pinged again
        self.ka_said: set[str] = set()
        self.ka_skipped: dict[str, str] = {}   # session -> the reason of its last logged skipped ping
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
        if self.command == "work-on" and self.plan_branch:
            return f"/{PLUGIN}:work-on {self.dossier} plan-branch={self.plan_branch}"
        if self.seed:
            return f"/{PLUGIN}:plan the description in {self.seed} (read that file first: it is the whole brief, a free-form description, not a dossier)"
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

    def start_drivers(self, prev: dict | None = None) -> None:
        """Read the drivers file (~/.agentic/drivers.json, $ASD_DRIVERS_FILE) when the run first starts: the modes this
        run's claude children get as ASD_DRIVER_<NAME>, a warning per bad entry, and the effective modes in the runner
        metadata (`drivers`), so a later report knows which arm the dossier ran in. With a run quota in the file (`driver_modes.py
        limit N`) only a fresh /work-on run that claims a place carries the modes; the others get every driver off. A restarted runner of the same,
        unfinished run keeps the modes it recorded, so editing the file mid-run never moves a dossier to the other arm;
        only a switch to `off` reaches it (rollback), never a quota that ran out."""
        prev = prev or {}
        pinned = prev.get("drivers")
        if isinstance(pinned, dict) and prev.get("state") != "done" and prev.get("command") == self.command:
            # Pinned, except that `off` always wins: a driver the file no longer runs (driver_modes.py off, or set
            # NAME=off) is off for this run too, so a rollback reaches runs already going.
            now_modes, warnings = driver_modes.load()
            for w in warnings:
                self.event("warning: " + w)
            effective = {n: (str(pinned.get(n) or "off") if now_modes.get(n, "off") != "off" else "off")
                         for n in driver_modes.NAMES}
            self.driver_exports = {
                driver_modes.env_name(n): m for n, m in effective.items()
                if m != "off" and driver_modes.env_name(n) not in os.environ
            }
        else:
            effective, self.driver_exports, warnings = driver_modes.resolve(os.environ)
            for w in warnings:
                self.event("warning: " + w)
            quota = driver_modes.load_quota()
            counted = {v for v in self.driver_exports if driver_modes.name_of(v) not in driver_modes.FREE}
            if quota is not None and counted:
                # A run quota: only a fresh /work-on run that gets a place carries the file's modes. Any other run
                # (a full quota, /plan, ...) gets every file-driven driver off; an explicit ASD_DRIVER_* still wins.
                # The script-only drivers (driver_modes.FREE) cost no tokens: they keep their file mode either way,
                # and a run that carries only those never claims a place.
                placed = False
                if self.command == driver_modes.QUOTA_COMMAND:
                    try:
                        placed = driver_modes.claim(self.key)
                    except OSError as err:
                        self.event(f"warning: drivers quota not claimed, drivers off: {err}")
                if not placed:
                    effective = {n: ("off" if (driver_modes.env_name(n) in self.driver_exports and n not in driver_modes.FREE) else m)
                                 for n, m in effective.items()}
                    self.driver_exports = {v: m for v, m in self.driver_exports.items() if driver_modes.name_of(v) in driver_modes.FREE}
                    if self.command == driver_modes.QUOTA_COMMAND:
                        self.event(f"drivers: off (quota of {quota['limit']} used up)")
        self.save(drivers=effective)
        if any(m != "off" for m in effective.values()):
            self.event("drivers: " + ", ".join(f"{n}={m}" for n, m in effective.items() if m != "off"))

    def child_env(self) -> dict:
        """The environment of a claude child of this run (the stage's and a keep-alive resume's): run key and account."""
        env = dict(os.environ, ASD_RUN_KEY=self.key)
        env.update(self.driver_exports)   # an ASD_DRIVER_* already in the runner's environment is never in here: it wins
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

    def reset_watchdog(self) -> None:
        """A new claude process starts with a fresh idle clock and none of the old process's Bash calls in flight. The
        previous stage's `last_work` and `inflight_bash` (a call running when the stage split or was killed, then a long
        queue wait for a slot) otherwise make the stall watchdog read the new process as hung for the old one's minutes:
        W-182's stage 2 was declared stalled "73 min" three seconds after it started, twice, and asked the owner."""
        self.turn_open = False
        self.last_out = time.monotonic()
        self.last_work = self.last_out
        self.spawned_at = self.last_out
        self.inflight_bash.clear()
        self.bg_wait = ""
        self.stall_bg_at = 0.0

    def spawn(self) -> tuple[queue.Queue, bool]:
        self.wrong_model = ""
        self.stray = 0
        self.ctx = 0
        self.split = None
        self.last_api = now_s()
        sid, self.resume_sid = self.resume_sid, ""
        self.resumed = bool(sid) and self.resume_acct == self.account["name"]
        self.got_init = False
        self.reset_watchdog()
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
            if not transcript_path(sid, (self.account or {}).get("dir")):
                # The session was created under another account's config dir (the run switched accounts after a
                # usage limit): `claude --resume` here would find nothing. Start fresh from the checkpoints.
                if getattr(self, "_foreign_logged", "") != sid:
                    self._foreign_logged = sid
                    self.event(f"session {sid[:8]} belongs to another account or was not found under account "
                               f"{self.account['name']}; starting fresh from the checkpoints", "running")
                return
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
        live: list[str] = []
        tools: list[str] = []
        for t in tasks:
            if output_unheld(t["output"]):
                self.log(f"background task {t['id']} ({t['label']}) has no process left (nothing holds its output open); not waiting for it")
                continue
            live.append(t["label"])
            tools.append(t.get("tool") or "")
        self.bg_cmds_only = bool(live) and all(x == "Bash" for x in tools)
        return live

    def take_slot(self, first: bool = False, rehome: bool = False) -> bool:
        """Wait for a run slot (max_live); False when the run was stopped while it waited. `first`: the owner just
        answered this run, so it queues ahead of every run not answered (it already waited on the owner). `rehome`: the
        run is about to start a stage in a fresh session (nothing to resume), so the slot goes with the account that has the
        most paced headroom, not the one it used last; a run with a session to resume keeps its account."""
        free0, floor0 = disk_free_gib(self.root), disk_floor()
        if os.environ.pop("ASD_REEXEC_HELD", None) == self.key and not (free0 is not None and free0 < floor0):
            # this process is the exec of a runner that held a slot at a stage boundary (reexec_if_changed): same pid, same
            # run, the slot was never given up, so it is not asked for again (it would queue behind the runs that took
            # their slots meanwhile); the account pick still applies, and a disk below the floor falls through to the normal loop
            # (it queues for disk as before). A dead runner never gets here: its pid is gone and the census skips it.
            self.event("restarted on new code; keeps the slot it held", "running")
            if rehome:
                self.rehome_account()
            self.save(state="running", queued_since=None, queued_first=None, over_cap=None)
            self.ka_since = None
            return True
        mine: tuple[int, str] | None = None
        queue_run = getattr(self, "command", "") in QUEUE_RUN_COMMANDS   # a run the merge queue waits on goes before ordinary queued runs
        first = first or queue_run
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
            if mine and not first and owner_replied_since(self.key, str(self.state.get("queued_since") or "")):
                first = True
                mine = queue_order({"queued_since": self.state.get("queued_since"), "queued_first": True})
                self.save(queued_first=True)
                self.event("queued first: the owner replied while this run waited for a slot", "queued")
            held_by, queued = slot_census_by_repo(self.key)
            held = sum(held_by.values())
            reserved = reserved_repos()
            repo = repo_of_key(self.key)
            ok, how = slot_admission(repo, cap, held_by, queued, mine, reserved, slot_overflow(self.key))
            if ok:
                if mine:
                    self.event(f"{'disk has room' if low_said else 'slot free'}; {held + 1} of {cap} runs working"
                               + (f"; using the reserved slot of {repo}" if how == "reserved" else ""), "running")
                if rehome:
                    self.rehome_account()
                self.save(state="running", queued_since=None, queued_first=None, over_cap=None)
                self.ka_since = None
                return True
            if not mine:
                since = now()
                mine = queue_order({"queued_since": since, "queued_first": first})
                self.save(state="queued", queued_since=since, queued_first=first or None)
                self.event(f"queued{' first (a merge-queue run)' if queue_run else ' first (answered)' if first else ''}: {held} of {cap} runs are working ({why}); "
                           f"{how}; this one starts when a slot frees", "queued")
            self.keepalive_tick()
            time.sleep(float(os.environ.get("ASD_RUNNER_SLOT_POLL") or 10))
        return False

    def rehome_account(self) -> None:
        """A slot was just granted to a run that starts a fresh session: move it to the account with the most paced
        headroom (see headroom_pick). Not for a run that resumes a session (its transcript lives in its account's dir)."""
        if fixed_slots() or (self.resume_sid and self.resume_acct == self.account["name"]):
            return
        try:
            running = running_by_account()
            if self.state.get("state") in ("running", "testing"):
                cur = self.account["name"]
                running[cur] = max(0, running.get(cur, 0) - 1)   # this run is counted on its old account; it is the one moving
            nxt, why = headroom_pick(paced_cap().get("accounts") or [], running, self.account["name"])
            if nxt == self.account["name"]:
                return
            acc = next((a for a in load_accounts() if a["name"] == nxt), None)
            if acc is None:
                return
            old, self.account = self.account["name"], acc
            self.save(account=nxt)
            self.event(f"slot free on account {nxt} (most headroom: {why}); was on {old}", "running")
        except Exception as e:   # the account choice is an optimisation: never fail a slot over it
            self.log(f"headroom pick failed: {e}")

    def wait_for_stub(self) -> str | None:
        """Poll scripts/stub_target.py until a stub can be built on (the integrated one merged, the next one cut)."""
        self.save(state="waiting", questions=[], waiting_for="stub")
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
        """Question ids of this run: `<repo>.<ID>.Q`, and for a plan run of a deferred item `<repo>.<ID>.plan-<D-n>.Q`
        (ask_owner.py names a question after the run key, so the dossier key alone missed every plan run's questions)."""
        return self.key + ".Q"

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
        self.bg_wait = ""
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

    def keepalive_skip(self, sid: str, reason: str) -> None:
        """One log line per skipped ping and reason (repeats of the same reason while the wait lasts are not logged again)."""
        if self.ka_skipped.get(sid) != reason:
            self.ka_skipped[sid] = reason
            self.log(f"keepalive skipped a ping for {self.key}: {reason}")

    def keepalive_tick(self) -> None:
        """Prompt-cache keep-alive for a run that waits on the owner or on background work: the cache lives 1 h, and an
        owner's answer can take longer. A `waiting` run and a `running`/`testing` one whose turn has ended (the session
        waits on a background task) are pinged; a run queued for a slot never is: the slot wait is not worth what a ping
        costs. When the session's last API call is ASD_KEEPALIVE_AFTER seconds old (3000) and the wait is under
        ASD_KEEPALIVE_MAX_H hours (24), send one tiny ping. No ping while the run is paused for usage, when its
        account's 5-hour window is at 80% or more, while a turn is open mid-stage (the session is working or blocked
        in a tool), or with ASD_KEEPALIVE=0; each skip of a due ping is logged with its reason. A ping takes no run
        slot and never changes the run's state."""
        if (os.environ.get("ASD_KEEPALIVE") or "").strip() == "0" or self.stopping:
            return
        st = self.state.get("state")
        if st not in ("waiting", "running", "testing"):   # a slot wait ("queued") is not worth a ping: the run starts when a slot frees
            self.ka_since = None
            return
        if time.monotonic() - self.ka_checked < env_num("ASD_KEEPALIVE_POLL", 5):
            return
        self.ka_checked = time.monotonic()
        t = now_s()
        sid = str(self.state.get("session") or "")
        if st != "waiting" and not self.turn_open and not self.session_last_api():
            return   # no API call seen yet: nothing to keep warm
        if st != "waiting" and self.turn_open:   # mid-stage the session is working or blocked in a tool call: no ping can go in
            self.ka_since = None
            last = self.session_last_api()
            if sid and sid not in self.ka_cold and last and t - last >= env_num("ASD_KEEPALIVE_AFTER", 3000):
                self.keepalive_skip(sid, "a turn is open (the session is mid-stage)")
            return
        if self.ka_since is None:
            self.ka_since = t
        if not sid or sid in self.ka_cold:
            return
        if t - self.ka_since >= env_num("ASD_KEEPALIVE_MAX_H", 24) * 3600:
            if sid + ":max" not in self.ka_said:
                self.ka_said.add(sid + ":max")
                self.log("keepalive stopped: waited longer than ASD_KEEPALIVE_MAX_H")
            return
        last = self.session_last_api()
        if last and t - last < env_num("ASD_KEEPALIVE_AFTER", 3000):
            return
        why = self.keepalive_blocked(t)
        if why:
            self.keepalive_skip(sid, why)
            return
        self.ka_skipped.pop(sid, None)
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
        if not (ev.get("type") in PROGRESS_EVENTS or (ev.get("type") == "system" and ev.get("subtype") in PROGRESS_SUBTYPES)):
            self.last_work = self.last_out
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
            self.bg_wait = ""
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
        if not self.turn_open:
            return self.bg_wait
        if not self.inflight_bash:
            return ""
        quiet = time.monotonic() - self.last_work > env_num("ASD_TESTING_QUIET_SECS", TESTING_QUIET_SECS)
        for cmd, _ in self.inflight_bash.values():
            if TESTING_CMD.search(cmd) or quiet:
                return wait_label(cmd)
        return ""

    def refresh_testing(self) -> None:
        """running -> testing while blocked_on() says the session waits on a test or gate; back to running the moment
        that call's result arrives. Only these two states are touched; going back never queues (see the slot notes)."""
        cur = self.state.get("state")
        if cur not in ("running", "testing") or self.stopping:
            return
        why = self.blocked_on()
        if why and cur == "running":
            self.save(state="testing", over_cap=None)
            self.event(f"session waits on `{why}`; slot released while it tests", "testing")
        elif not why and cur == "testing":
            owed = self.return_over_cap()
            self.save(state="running", over_cap=bool(owed) or None)
            self.event("the wait ended; the run works again" + (
                f" over the cap, on a slot reserved for {', '.join(owed)}; a run of that repo still starts"
                if owed else " (no queue; the cap may be briefly exceeded)"), "running")

    def return_over_cap(self) -> list[str]:
        """The reserved repos (other than this run's) holding no slot, when this run coming back from testing takes a
        slot they are owed: the others' held slots plus those reservations already reach the cap. [] otherwise."""
        try:
            cap, _ = slot_cap()
            if cap == 0:
                return []
            held_by, _ = slot_census_by_repo(self.key)
            repo = repo_of_key(self.key)
            idle = sorted(r for r in reserved_repos() if r != repo and not held_by.get(r))
            return idle if idle and sum(held_by.values()) + len(idle) >= cap else []
        except Exception:
            return []

    def stalled(self) -> bool:
        """True when the session is wedged: mid-turn, running, and no stream-json output for ASD_STALL_MIN minutes
        (45; 0 disables). Not mid-turn (the runner's own waits on an owner's answer or a slot, a turn ended while
        background work runs) is never a stall, nor is a transcript that still shows background work in flight.
        A Bash call has its own timeout (10 min at most) and a sub-agent's events are output, so neither is quiet this long.
        The CLI's progress lines for a foreground Bash call are output too, so a call hung for good is caught by
        `hung_bash` instead: older than the limit while the session did no work for as long."""
        lim = env_num("ASD_STALL_MIN", 45) * 60
        if lim <= 0 or not self.turn_open or self.stopping or self.state.get("state") not in ("running", "testing"):
            return False
        if self.proc is None or self.proc.poll() is not None:
            return False
        if time.monotonic() - max(self.last_out, getattr(self, "spawned_at", 0.0)) < lim and not self.hung_bash(lim):
            return False
        if time.monotonic() - self.stall_bg_at >= 60:
            self.stall_bg_at = time.monotonic()
            if self.background():
                return False
            self.stall_bg_at = 0.0
            return True
        return False

    def hung_bash(self, lim: float) -> str:
        """The first line of a foreground Bash call running for `lim` seconds or more while the session did no work
        (only the CLI's progress lines) for as long, else "". A Bash call cannot outlive its own 10 minute timeout, so
        one far past it is hung: W-176 sat six hours on a `git pull` whose login shell never started the command,
        with progress lines keeping `last_out` fresh the whole time."""
        now = time.monotonic()
        born = getattr(self, "spawned_at", 0.0)   # nothing older than this process counts: queue waits, a resumed session's old events
        if lim <= 0 or not self.inflight_bash or now - max(self.last_work, born) < lim:
            return ""
        for cmd, t0 in self.inflight_bash.values():
            own = max(lim, env_num("ASD_TESTING_HUNG_MIN", TESTING_HUNG_MIN) * 60) if TESTING_CMD.search(cmd) else lim
            if now - max(t0, born) >= own and now - max(self.last_work, born) >= own:
                return cmd.strip().splitlines()[0][:80] if cmd.strip() else "a Bash call"
        return ""

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
        lim = env_num("ASD_STALL_MIN", 45) * 60
        hung = self.hung_bash(lim) if time.monotonic() - self.last_out < lim else ""   # silent: the plain stall below
        self.log(f"stall diagnostic: last stream event: {last}; process tree of claude pid {self.proc.pid}:\n" + self.process_tree())
        if hung:
            work = int((time.monotonic() - max(self.last_work, getattr(self, "spawned_at", 0.0))) / 60)
            detail = f"stalled: Bash call `{hung}` hung, no work for {work} min mid-turn (last: Bash)"
        else:
            detail = f"stalled: no output for {mins} min mid-turn (last: {tool})"
        self.log(detail)
        kill_group(self.proc)
        for _ in range(50):
            if self.proc.poll() is not None:
                break
            time.sleep(0.1)
        kill_group(self.proc, signal.SIGKILL)
        return detail

    def read_turn(self, q: queue.Queue, extra: bool, idle: float | None = None, keepalive: bool = False) -> tuple[str, object]:
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
                if keepalive and result is None:
                    self.keepalive_tick()   # a stage session waiting on background work keeps its cache warm too
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
            # progress lines may come faster than the 1 s poll above, so a hung Bash call is checked on them too
            if result is None and (ev.get("type") in PROGRESS_EVENTS or ev.get("type") == "system") and self.stalled():
                return "stalled", None
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

    def dossier_file(self) -> str | None:
        """The dossier's file (for wait_ci --log), when this run has one."""
        d = os.path.join(self.root, ".discovery", "dossiers")
        try:
            names = sorted(n for n in os.listdir(d) if re.match(re.escape(str(self.dossier)) + r"(-.*)?\.md$", n))
        except OSError:
            return None
        return os.path.join(d, names[0]) if names else None

    def run_live_key(self, key: str) -> bool:
        st = read_json(os.path.join(runs_dir(), key + ".runner.json"), {}) or {}
        return st.get("state") in LIVE_STATES and runner_alive(st.get("pid"))

    def pr_conflicts(self, n: int) -> bool | None:
        """Whether PR n is DIRTY/CONFLICTING now; None when it cannot be read."""
        try:
            p = subprocess.run(shlex.split(os.environ.get("ASD_GH_BIN") or "gh") + ["pr", "view", str(n), "--json", "mergeStateStatus,mergeable,state"],
                               cwd=self.root, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
            row = json.loads(p.stdout) if p.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        if not isinstance(row, dict):
            return None
        if row.get("state") not in (None, "OPEN"):
            return False   # merged or closed: it no longer blocks the child
        return row.get("mergeStateStatus") == "DIRTY" or row.get("mergeable") == "CONFLICTING"

    def start_parent_resolve(self, parent: int) -> str:
        """The parent PR of a CI wait conflicts: have the queue's resolve-merge procedure resolve it, in a run of its own
        (`<repo>.<ID>.resolve-<pr>`; this dossier's own key is the waiting session's). It is not put in the merge queue:
        a queued dossier is merged into its stub once clean, and conflict resolution must not merge anything. Not started
        twice for one parent (a live run of that key is waited for).
        Returns the key of the run that resolves it."""
        rkey = run_key(self.root, self.dossier, "resolve-merge", str(parent))
        if self.run_live_key(rkey):
            self.event(f"parent #{parent} conflicts; its resolve-merge run is already live; waiting for it", "waiting")
            return rkey
        self.parent_tries[parent] = self.parent_tries.get(parent, 0) + 1
        cmd = shlex.split(os.environ.get("ASD_PARENT_RESOLVE_BIN") or f"{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))}") + [
            self.dossier, "--root", self.root, "--command", "resolve-merge", "--items", str(parent), "--model", "sonnet"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
            first = (r.stdout.strip().splitlines() or r.stderr.strip().splitlines() or [""])[0]
        except (OSError, subprocess.SubprocessError) as e:
            first = str(e)
        self.event(f"parent #{parent} conflicts, so CI cannot start on the stacked PR: started a resolve-merge run for it (attempt "
                   f"{self.parent_tries[parent]} of 2); waiting for it, no owner question: {first[:160]}", "waiting")
        return rkey

    def ci_wait(self, prs: list[int]) -> list[tuple[int, str, str]] | str | None:
        """`RUNNER: WAITING ci:<pr>`: run scripts/wait_ci.py for each PR here, in parallel, without a token spent, and
        return [(pr, green|red|timeout, compact summary)] once every PR has a verdict (None: the run was stopped; "message": the owner wrote to the run, nothing is lost: the wait restarts).
        The run's state is `waiting` meanwhile, so the slot is free and the keepalive ping runs, as in an owner wait.
        wait_ci has no 10-minute cap of its own (that is the agent's Bash limit), so one call waits ASD_CI_TIMEOUT s."""
        script = os.environ.get("ASD_WAIT_CI_BIN") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "wait_ci.py")
        timeout = env_num("ASD_CI_TIMEOUT", 5400)
        log = self.dossier_file()
        self.save(state="waiting", questions=[], ci=prs, waiting_for="ci")
        self.event("waiting on CI " + ", ".join(f"#{n}" for n in prs), "waiting")
        procs: dict[int, subprocess.Popen] = {}
        for n in prs:
            cmd = [sys.executable, script, "--pr", str(n), "--timeout", str(timeout)] + (["--log", log] if log else [])
            procs[n] = subprocess.Popen(cmd, cwd=self.root, env=self.child_env(), stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True)
            self.children.append(procs[n])
        out: dict[int, tuple[str, str]] = {}
        poll_at: dict[int, float] = {}
        resolving: dict[int, tuple[int, str, float, float]] = {}   # child PR -> (conflicting parent, resolve run key, deadline, started)
        try:
            while len(out) < len(procs):
                for n, p in list(procs.items()):
                    if n in resolving:
                        if time.monotonic() < poll_at.get(n, 0.0):
                            continue
                        poll_at[n] = time.monotonic() + env_num("ASD_PARENT_POLL", 20)
                        parent, rkey, deadline, t0 = resolving[n]
                        live = self.run_live_key(rkey) or time.monotonic() - t0 < env_num("ASD_PARENT_GRACE", 60)   # a just-started run has no record yet
                        dirty = self.pr_conflicts(parent)
                        if dirty is False or (not live and dirty is None):
                            del resolving[n]   # the parent is clean again (or the run ended and it cannot be read): CI on the child starts
                            self.event(f"parent #{parent} no longer conflicts; waiting on CI #{n} again", "waiting")
                            cmd = [sys.executable, script, "--pr", str(n), "--timeout", str(timeout)] + (["--log", log] if log else [])
                            procs[n] = subprocess.Popen(cmd, cwd=self.root, env=self.child_env(), stdout=subprocess.PIPE,
                                                        stderr=subprocess.STDOUT, text=True)
                            self.children.append(procs[n])
                        elif not live or time.monotonic() > deadline:
                            del resolving[n]
                            out[n] = ("never-started", f"PR #{n} is stacked on #{parent}, which still conflicts: the runner's resolve-merge run "
                                      + ("ended without resolving it" if not live else "did not finish in time"))
                        continue
                    if n not in out and p.poll() is not None:
                        text = (p.communicate()[0] or "").strip()
                        verdict = {0: "green", 3: "timeout", 6: "never-started", 7: "never-started"}.get(p.returncode, "red")
                        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
                        tail = " | ".join(lines[-int(env_num("ASD_CI_TAIL_LINES", 30)):])[-3000:]
                        if p.returncode == 6:
                            self.ci_never_started.add(n)
                        if p.returncode == 7 and (m := PARENT_RE.search(text)) and self.parent_tries.get(int(m.group(1)), 0) < 2:
                            # The parent PR conflicts: the RUNNER gets it resolved (the session must not touch it, and the
                            # owner has nothing to decide), then CI on the child is waited for again. Not a never-started
                            # PR for the session: nothing is sent until CI has a verdict.
                            parent = int(m.group(1))
                            rkey = self.start_parent_resolve(parent)
                            resolving[n] = (parent, rkey, time.monotonic() + env_num("ASD_PARENT_RESOLVE_TIMEOUT", 4 * 3600), time.monotonic())
                            continue
                        if p.returncode not in (0, 1, 3, 6, 7):
                            tail = f"(wait_ci exit {p.returncode}) " + tail
                        out[n] = (verdict, tail)
                if self.stopping:
                    return None
                if read_inbox(self.key, int(self.state.get("inbox_seen") or 0)):
                    return "message"   # the owner wrote to the run: the caller delivers it, the agent answers and waits again
                self.keepalive_tick()
                time.sleep(0.1)
        finally:
            self.ka_since = None
            self.save(ci=[])
            for p in procs.values():
                if p.poll() is None:
                    p.kill()
                    p.wait()
        return [(n, *out[n]) for n in prs]

    def pr_state(self, n: int) -> tuple[str, str] | None:
        """(state, base branch) of PR n: OPEN, MERGED or CLOSED; None when it cannot be read."""
        try:
            p = subprocess.run(shlex.split(os.environ.get("ASD_GH_BIN") or "gh") + ["pr", "view", str(n), "--json", "state,mergedAt,baseRefName"],
                               cwd=self.root, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
            row = json.loads(p.stdout) if p.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        if not isinstance(row, dict) or not row.get("state"):
            return None
        state = "MERGED" if row.get("mergedAt") else str(row["state"]).upper()
        return state, str(row.get("baseRefName") or "")

    def merge_wait(self, prs: list[int]) -> list[tuple[int, str, str]] | str | None:
        """`RUNNER: WAITING merged:<pr>`: planning is blocked on code only these pull requests add. Poll them here with no
        token spent, in state `waiting` (the slot is free, the keepalive runs, the dashboard says "waiting for PR n to
        merge"), until each is merged or closed. Returns [(pr, merged|closed, detail)]; None when stopped; "message" when
        the owner wrote to the run. A PR that cannot be read is retried; it never ends the wait."""
        poll = env_num("ASD_PR_POLL", 120)
        self.save(state="waiting", questions=[], waiting_for="pr", merge_prs=prs)
        self.event("waiting for " + ", ".join(f"PR {n}" for n in prs) + " to merge", "waiting")
        out: dict[int, tuple[str, str]] = {}
        next_at = 0.0
        try:
            while len(out) < len(prs):
                if time.monotonic() >= next_at:
                    next_at = time.monotonic() + poll
                    for n in prs:
                        if n in out:
                            continue
                        got = self.pr_state(n)
                        if got is None or got[0] == "OPEN":
                            continue
                        out[n] = ("merged", f"into {got[1] or 'its base'}") if got[0] == "MERGED" else ("closed", "without merging")
                if self.stopping:
                    return None
                if read_inbox(self.key, int(self.state.get("inbox_seen") or 0)):
                    return "message"
                self.keepalive_tick()
                time.sleep(0.1)
        finally:
            self.ka_since = None
            self.save(merge_prs=[])
        return [(n, *out[n]) for n in prs]

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

    def logged_out_stage(self, started: int, detail: str, n: int) -> bool:
        """A stage ended because its account is signed out. True: run the stage again (on a signed-in account, or once
        this one is signed in again); False: the sign-out is not confirmed after several tries, so the normal failure path."""
        cur = self.account
        if auth_status(cur, force=n > 1):
            if n > 3:
                return False
            self.mark_resume()
            self.event(f"stage {started} said the account was signed out ({detail[:100]}), but account {cur['name']} is signed in; stage {started} again", "running")
            return True
        self.mark_resume()
        if not (self.resume_sid and self.resume_acct == cur["name"]):   # nothing to resume: any signed-in account will do
            st = account_state()
            for a in load_accounts():
                mark = st.get(a["name"]) or {}
                if a["name"] != cur["name"] and not account_problem(a, load_accounts()) and auth_status(a) \
                        and not (float(mark.get("limited_until") or 0) > now_s()):
                    self.account = a
                    self.save(account=a["name"])
                    self.event(f"switched to account {a['name']}: account {cur['name']} is signed out; stage {started} again", "running")
                    return True
        name = cur["name"]
        msg = (f"account {name} is signed out; run `CLAUDE_CONFIG_DIR={shlex.quote(cur['dir'])} claude` then /login; "
               "this run resumes by itself once it is signed in")
        self.ask_logged_out(cur)
        self.save(state="waiting", questions=[], waiting_for="login")
        self.event(msg, "waiting")
        poll = float(os.environ.get("ASD_AUTH_POLL") or 60)
        while not self.stopping:
            until = time.time() + poll
            while time.time() < until and not self.stopping:
                time.sleep(min(1.0, max(0.05, until - time.time())))
            if not self.stopping and auth_status(cur, force=True):
                break
        if self.stopping:
            return True
        self.event(f"account {name} is signed in again; stage {started} again", "running")
        return True

    def ask_logged_out(self, acc: dict) -> None:
        """Ask the owner to sign an account in: once per account until it is signed in again, however many runs wait."""
        try:
            fd = os.open(auth_question_path(acc["name"]), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except OSError:
            return
        p = self.ask_cmd(
            "ask", "--root", self.root, "--dossier", self.dossier,
            "--question", f"Claude account {acc['name']} is signed out: runs on it wait (new runs go to a signed-in account). "
                          f"Run `CLAUDE_CONFIG_DIR={shlex.quote(acc['dir'])} claude` and /login; waiting runs resume by themselves.",
            "--option", "A|Signed in|nothing to do, the runs notice within a minute",
            "--option", "B|Leave it signed out|runs that can start fresh keep using the other accounts",
            "--recommend", "A", "--blocks", f"the runs on account {acc['name']}", "--topic", "run-failure")
        out, err = p.communicate()
        if p.returncode != 0 or not out.strip():
            self.event(f"could not ask the owner about account {acc['name']}: {err.strip()[:200]}")
            try:
                os.remove(auth_question_path(acc["name"]))
            except OSError:
                pass
            return
        write_json(auth_question_path(acc["name"]), {"question": out.strip()})

    def ask_failure(self, reason: str) -> str:
        """Question the owner about a failed stage; 'retry' or 'stop'."""
        p = self.ask_cmd(
            "ask", "--root", self.root, "--dossier", self.dossier,
            "--question", clip(f"The run of {self.dossier} failed at stage {self.state['stage']}: {reason}", 1500),
            "--option", "A|Retry the stage|starts the stage again in a new process, with the same hand-off",
            "--option", "B|Stop|ends the run; the dossier keeps what the earlier stages committed",
            "--recommend", "A", "--blocks", f"the run of {self.dossier}", "--topic", "run-failure")
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
        bounds. Off unless ASD_STACK_REPAIR=1, except for a /stub run (on unless ASD_STACK_REPAIR=0). Never raises and
        never fails the run; returns {resource: outcome}."""
        if not stack_repair.enabled(self.command):
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
                res = stack_repair.repair(cfg, self.root, found, lambda t: self.event(t), command=self.command)
                out.update(res)
                if not any(v.startswith("repaired") for v in res.values()):
                    break
        except Exception as e:  # noqa: BLE001 - a repair must never end a run
            self.event(f"environment repair crashed: {e!r}")
        return out

    def env_skip(self, why: str) -> str:
        """The answer to a dossier run's shared-stack problem the runner cannot fix: never an owner question. The run skips
        its live stack tests, the /stub full suite runs them, and the PR note says so under a `STACK-ENV: skipped` head."""
        return (f"RUNNER ENV stack: {why}. This is not an owner question and not yours to repair. Skip this run's live stack "
                "tests: do not run opmode-stack.sh, up.sh or any compose command yourself, run the rest of your tests "
                "(`run_tests.py --vm` already skips the DB-backed ones), defer those criteria to the /stub full suite, and put "
                f"`STACK-ENV: skipped \u2014 {why}` at the head of the PR note and in the build log. Carry on with the step you stopped at.")

    def handle_leftover(self, detail: str, n: int) -> str:
        """A bring-up refused because a compose project a suite left up is in the way (`foreign_project_state`): the runner
        tears it down itself when it can prove it dead (scripts/stack_env.py `leftover`), else the run skips to the stub."""
        if n > 4:
            return self.env_skip(f"reported {n} times this stage; the leftover shared stack project was not cleared")
        cfg = stack_env.leftover_config(self.root)
        if not cfg:
            return self.env_skip("the repo's stack-repair config lists no leftover project to clear")
        try:
            res = stack_env.leftover(self.root, cfg, detail, log=lambda t: self.event(t))
        except Exception as e:  # noqa: BLE001 - never ends a run
            res = {"state": "skipped", "name": "?", "why": f"the check crashed: {e!r}"}
        if res["state"] == "repaired":
            return (f"RUNNER ENV stack: repaired by the runner ({res['name']}: {res['why']}); carry on with the step you "
                    "stopped at and bring your own stack up again.")
        if res["state"] == "none":
            return (f"RUNNER ENV stack: the leftover project is not up any more ({res['why']}); retry the step you stopped at "
                    "once. If it refuses again, report it again.")
        return self.env_skip(f"leftover project {res['name']} was not torn down ({res['why']})")

    def handle_env(self, rest: str, n: int) -> str:
        """The message sent back for a `RUNNER: ENV <kind> <detail>` line."""
        parts = rest.split(None, 1)
        kind = parts[0].lower() if parts else ""
        detail = parts[1] if len(parts) > 1 else ""
        self.event(f"session reports environment failure: {kind} {detail[:160]}".strip())
        ask = "Ask the owner about it as usual (ask_owner.py, then RUNNER: WAITING)."
        if kind not in ("vm", "stack"):
            return f"RUNNER ENV {kind or '?'}: the runner does not repair this; {ask}"
        if re.search(r"fake-llm|fixture", detail, re.I):
            # A stale shared fixture is the runner's to handle (scripts/stack_env.py, inside run_tests.py --vm): it is
            # rebuilt from the persistent checkout when safe, else the DB tests are skipped. Never an owner question,
            # and independent of ASD_STACK_REPAIR (that gate is for a stopped VM or a down stack).
            if n > 4:
                return f"RUNNER ENV {kind}: reported {n} times this stage; {ask}"
            return (f"RUNNER ENV {kind}: the fake-llm fixture is the runner's to handle, not yours and not the owner's. "
                    "Run the tests through `run_tests.py --vm ...` (not by sourcing the env script directly): it rebuilds the "
                    "fixture from the persistent checkout when that is safe, otherwise runs the DB-backed tests skipped under a "
                    "`STACK-ENV: skipped` digest head, which you record in the build log and PR note. Never rebuild or restart "
                    "a shared stack service yourself.")
        # A dossier run never asks the owner about the shared stack (owner ruling 2026-11-19): what the runner cannot fix
        # becomes a skip to the stub's full suite. A /stub run owns the stack and keeps asking, as before.
        skip = kind == "stack" and self.command != "stub"
        def fail(why: str) -> str:
            return self.env_skip(why) if skip else f"RUNNER ENV {kind}: {why}; {ask}"
        if skip and re.search(r"foreign_project_state|leftover", detail, re.I):
            return self.handle_leftover(detail, n)
        if skip and re.search(r"suite_window_active|stack_lease_refused", detail, re.I):
            return self.env_skip("another run's suite holds the shared stack right now: " + detail[:120])
        if not stack_repair.enabled(self.command):
            return fail("automatic repair is off (ASD_STACK_REPAIR is not 1)")
        if n > 4:
            return fail(f"reported {n} times this stage")
        if not stack_repair.load_config(self.root):
            return fail("no repair is configured for this repo (.agentic/stack-repair.json)")
        res = self.repair_env("reported by the session").get(kind)
        if res is None:   # the checks find it healthy (the stage-start repair or another runner fixed it)
            return f"RUNNER ENV {kind}: repaired by the runner (healthy now); carry on with the step you stopped at."
        if res.startswith("repaired"):
            return f"RUNNER ENV {kind}: repaired by the runner; carry on with the step you stopped at."
        return fail(f"not repaired ({res})")
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
            kind, val = self.read_turn(q, extra, idle if quiet else None, keepalive=True)
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
                    if self.bg_cmds_only:
                        # Only shell commands run (a CI wait, a detached test run): no model works, so the slot goes
                        # back until the session is re-invoked. A background agent or workflow keeps it.
                        self.bg_wait = "background: " + bg[0][:68]
                        self.refresh_testing()
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
                if logged_out(tail):
                    return "loggedout", tail.strip().splitlines()[-1][:300] if tail.strip() else "not logged in"
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
            if err and logged_out(text):
                kill_group(self.proc)
                self.end_process()
                return "loggedout", text.strip().splitlines()[-1][:300]
            if err and network_error(text):
                kill_group(self.proc)
                self.end_process()
                return "network", text.strip().splitlines()[-1][:300] if text.strip() else "network error"
            if err and self.resumed and "No conversation found with session ID" in (text or ""):
                # claude (2.1.x) reports an unknown session as a turn that ended in an error, not as an early exit
                kill_group(self.proc)
                self.end_process()
                return "fresh", text.strip().splitlines()[-1][:200]
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
            mps = [i for i in ids if MERGE_ID.match(i)]
            if mps:
                if len(mps) != len(ids):
                    self.send("RUNNER: WAITING mixes merged:<pr> with other ids; wait on one kind per line. "
                              "End the turn again with only the ids you need first.")
                    quiet = True
                    continue
                res = self.merge_wait([int(i[7:]) for i in mps])
                if res is None:
                    return "stopped", ""
                if res == "message":
                    self.save(state="running")
                    self.deliver_inbox()
                    continue
                if not self.take_slot(first=True):
                    return "stopped", ""
                self.event("PR result: " + ", ".join(f"#{n} {v}" for n, v, _ in res), "running")
                self.send("\n".join(f"PR RESULT {n}: {v} {t}. " + (
                    "Carry on with the step you stopped at." if v == "merged" else
                    "It was closed unmerged: this is a real decision, ask the owner how planning goes on.") for n, v, t in res))
                continue
            cis = [i for i in ids if CI_ID.match(i)]
            if cis:
                if len(cis) != len(ids):
                    self.send("RUNNER: WAITING mixes ci:<pr> with question ids; wait on one kind per line. "
                              "End the turn again with only the ids you need first.")
                    quiet = True
                    continue
                again = [int(i[3:]) for i in cis if int(i[3:]) in self.ci_never_started]
                if again:
                    # CI never started on this PR and one retrigger already failed: waiting again gets the same verdict
                    # at the same cost. Refuse the wait; the session must report the blocker instead.
                    self.send("\n".join(f"CI RESULT {n}: never-started {CI_NEVER_STARTED_NEXT.format(n=n)} "
                                         "(refused: already reported; the runner does not wait on it again)" for n in again))
                    cis = [i for i in cis if int(i[3:]) not in again]
                    if not cis:
                        continue
                    ids = [i for i in ids if i in cis]
                res = self.ci_wait([int(i[3:]) for i in cis])
                if res is None:
                    return "stopped", ""
                if res == "message":
                    self.save(state="running")
                    self.deliver_inbox()
                    continue
                if not self.take_slot(first=True):
                    return "stopped", ""
                self.event("CI result: " + ", ".join(f"#{n} {v}" for n, v, _ in res), "running")
                self.send("\n".join((f"CI RESULT {n}: never-started CI did not run on #{n} (it did not fail): {t} "
                                          + (CI_NEVER_STARTED_NEXT if n in self.ci_never_started else CI_PARENT_NEXT).format(n=n))
                                         if v == "never-started"
                                         else f"CI RESULT {n}: {v} {t}".rstrip() for n, v, t in res))
                continue
            qids = [i for i in ids if QUESTION_ID.search(i)]
            if not qids:
                # `RUNNER: WAITING gates,background,task` is a wait on background work,
                # not on the owner: keep the session open, as for a turn with no RUNNER: line.
                self.log(f"RUNNER: WAITING names no question ({rest[:80] or 'empty'}); waiting for background work to re-invoke it")
                quiet = True
                continue
            ids = qids
            self.save(state="waiting", questions=ids, waiting_for="owner")
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
        # The exec'd process resumes the recorded session only when this one meant to (mark_resume after a kill or a
        # stall). After a clean stage end (a checkpoint split) the next stage starts fresh from the hand-off, as it would
        # without the restart: keeping the session here made the new process --resume the full, split context.
        self.save(state="running", **({} if self.resume_sid else {"session": ""}))
        args = [a for a in sys.argv[1:] if a != "--detach"]
        if "--foreground" not in args and sys.argv[1:2] != ["chat"]:
            args.append("--foreground")
        try:
            sys.stdout.flush(); sys.stderr.flush()
        except Exception:
            pass
        os.environ["ASD_REEXEC_HELD"] = self.key   # the exec'd process keeps the slot this one holds (see take_slot)
        os.execv(sys.executable, [sys.executable, path, *args])

    def on_term(self, *_a) -> None:
        self.stopping = True
        kill_group(self.proc)
        for p in self.children:
            if p.poll() is None:
                p.kill()

    def run(self) -> int:
        self.lock = try_lock(self.key)   # held until this process ends
        if self.lock is None:
            say(f"{self.key} already has a runner (it holds the lock); this one exits")
            return 0
        prev = read_json(self.state_path, {})
        # a failed run's runner is alive while it waits on the failure question: Resume must not start a second one
        if prev.get("state") in LIVE_STATES + ("failed", "paused") and runner_alive(prev.get("pid")) and prev.get("pid") != os.getpid():
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
        self.state.pop("parked", None)
        resuming = prev.get("state") in ("running", "testing", "waiting", "queued", "paused") and bool(prev.get("session"))
        if self.command == "work-on" and not self.items and not resuming:
            built = merged_build(self.root, self.dossier)
            if built:
                # Never a second build of a merged change, whoever started it (auto_work, a button, revive): no session,
                # no slot, no question to the owner.
                return self.finish("done", built, 0)
        if self.command == "work-on" and not self.items and not resuming and os.environ.get("ASD_RUNNER_PARK", "1") != "0":
            waits = open_blockers(self.root, self.dossier)
            if waits:
                # A blocker is not done (a re-plan that added one, a start pressed too early): no session, no slot. The
                # run parks; `revive` starts it again by itself once every blocker is done.
                self.save(parked={"blockers": waits, "since": now()})
                return self.finish("parked", "parked: waits for " + ", ".join(waits) + "; /work-on starts by itself once they are done", 0)
        self.save()
        self.event(f"start /{self.command} {self.dossier} in {self.root}", "running")
        self.start_drivers(prev)
        started = 0
        if prev.get("state") in ("running", "testing", "waiting", "queued", "paused") and prev.get("session") and prev.get("account") == self.account["name"]:
            # The last runner process died (a logout, a reboot) with its session recorded: pick that session up again.
            self.state["session"] = str(prev["session"])
            self.mark_resume()
            self.state["session"] = ""
        net_tries = 0   # network errors in a row; a stage that ends well resets it
        kill_tries = 0  # outside kills in a row; likewise
        stall_tries = 0  # stalls of one stage in a row; the third goes to the owner
        lo_n = 0         # sign-out failures in a row that the auth check did not confirm
        while True:
            if self.stopping:
                return self.finish("stopped", "stopped", 3)
            if started >= self.max_stages:
                return self.finish("stopped", f"--max-stages {self.max_stages} reached", 4)
            started += 1
            self.save(stage=started, questions=[])
            if not self.take_slot(rehome=True):
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
                rc = self.finish("done", "done", 0)
                self.requeue_build()
                return rc
            if outcome == "replan":
                return self.replan(detail)
            if outcome == "stopped":
                self.end_process()
                return self.finish("stopped", "stopped", 3)
            if outcome == "loggedout":
                lo_n += 1
                if self.logged_out_stage(started, detail, lo_n):
                    if self.stopping:
                        return self.finish("stopped", "stopped", 3)
                    started -= 1
                    continue
                detail = f"the account's sign-in is refused: {detail}"
            else:
                lo_n = 0
            if outcome == "network":
                # A transient network or API-server error: pause, then run the same stage again from its checkpoints,
                # backing off, however long it lasts: an outage says nothing about the work. Only with
                # ASD_RUNNER_NET_RETRIES set does it become a failure the owner rules on.
                net_tries += 1
                self.mark_resume()
                limit_n = int(os.environ.get("ASD_RUNNER_NET_RETRIES") or 0)
                if auth_outage(detail):
                    limit_n = int(os.environ.get("ASD_RUNNER_AUTH_RETRIES") or AUTH_RETRIES)
                if not limit_n or net_tries <= limit_n:
                    wait = float(os.environ.get("ASD_RUNNER_NET_WAIT") or NETWORK_BACKOFF[min(net_tries, len(NETWORK_BACKOFF)) - 1])
                    until = time.time() + wait
                    at = datetime.datetime.fromtimestamp(until, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    self.save(state="paused", resumes_at=at)
                    what = "sign-in refused" if auth_outage(detail) else "network error"
                    self.event(f"paused: {what} ({detail[:160]}); retry {net_tries}{'/' + str(limit_n) if limit_n else ''} of stage {started} at {at}", "paused")
                    while time.time() < until and not self.stopping:
                        time.sleep(min(5.0, max(0.2, until - time.time())))
                    if self.stopping:
                        return self.finish("stopped", "stopped", 3)
                    self.save(resumes_at=None)
                    self.event(f"resumed after the network error; stage {started} again", "running")
                    started -= 1
                    continue
                detail = f"{'sign-in refused' if auth_outage(detail) else 'network errors'} on {limit_n} retries in a row: {detail}"
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
            if DUPLICATE_RUN_RE.match(detail):
                # Another live run owns this dossier and this one changed nothing: an owner question is noise. End quietly.
                return self.finish("failed", f"failed: {detail}", 1)
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

    def requeue_build(self) -> None:
        """A re-plan that finished starts the build again: a /plan run of a dossier that a /work-on run sent back (any
        `start /work-on` line before this run's start), once the plan review passed (`status: ready`), queues /work-on
        itself, detached, for a slot like any run. Local mode, or a plan already merged into this checkout: the plain
        /work-on. Committed mode with the plan PR still open: /work-on stacked on the plan branch (`plan-branch=`), which
        forks from the stub as always and cherry-picks the plan commits (work-on Phase 2). A plan that did not reach
        ready, or a ready copy found nowhere, is logged and left to the owner. `ASD_RUNNER_REQUEUE=0` turns it off."""
        if self.command != "plan" or self.items or self.seed or os.environ.get("ASD_RUNNER_REQUEUE", "1") == "0":
            return
        if not was_built_before(self.updates):
            return
        kind, branch = replan_followup(self.root, self.dossier, iso_epoch(self.state.get("started", "")))
        if kind == "none":
            self.event(f"re-plan finished, /work-on not requeued: {branch}", "done")
            return
        argv = [self.dossier, "--root", self.root, "--command", "work-on"] + (["--plan-branch", branch] if kind == "stacked" else [])
        try:
            detach(argv, self.root, self.dossier, self.key)
            self.event("re-plan finished: /work-on " + self.dossier + (f" requeued, stacked on {branch}" if kind == "stacked" else " requeued")
                       + " (it starts when a slot frees)", "done")
        except OSError as e:
            self.event(f"re-plan finished, but /work-on could not be requeued: {e}; start it from the dashboard", "done")

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
        if st.get("state") == "parked":
            unpark(path, st)
            continue
        if st.get("state") not in ("running", "testing", "waiting", "paused", "queued") or runner_alive(st.get("pid")) or not os.path.isdir(root):
            continue
        if time.time() - os.path.getmtime(path) > hours * 3600:
            continue
        if lock_held(os.path.basename(path)[:-len(".runner.json")]):
            continue   # a live runner holds the key (its record's pid is stale or reused): never a second one, never kill its child
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
        dossier = re.sub(r"\.(?:plan-D-\d+|resolve-\d+(?:-\d+)*)$", "", key[len(prefix):])
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
        if st.get("plan_branch"):
            args += ["--plan-branch", st["plan_branch"]]
        if st.get("model"):
            args += ["--model", st["model"]]
        subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
        print(f"run_dossier: restarted {key}")
    return 0


def unpark(path: str, st: dict) -> bool:
    """A parked /work-on whose blockers are all done now: start it again, detached (it queues for a slot like any run).
    No age limit: a blocker can take days. The record turns `queued` first, so the next tick does not start it twice."""
    root = st.get("root") or ""
    key = os.path.basename(path)[:-len(".runner.json")]
    prefix = main_repo_name(root) + "." if root else ""
    if not root or not os.path.isdir(root) or not key.startswith(prefix):
        return False
    dossier = key[len(prefix):]
    waits = open_blockers(root, dossier)
    if waits:
        if waits != (st.get("parked") or {}).get("blockers"):
            st["parked"] = {**(st.get("parked") or {}), "blockers": waits}
            write_json(path, st)
        return False
    argv = [dossier, "--root", root, "--command", "work-on"]
    if st.get("plan_branch"):
        argv += ["--plan-branch", st["plan_branch"]]
    if st.get("model"):
        argv += ["--model", st["model"]]
    st["state"] = "queued"
    st["pid"] = None
    st["session"] = ""   # a fresh build on the new base: never the old session
    write_json(path, st)
    append_line(os.path.join(runs_dir(), key + ".jsonl"), {"ts": now(), "kind": "runner", "text": "every blocker is done: /work-on starts again (it starts when a slot frees)", "state": "queued"})
    try:
        detach(argv, root, dossier, key)
    except OSError as e:
        st["state"] = "parked"
        write_json(path, st)
        append_line(os.path.join(runs_dir(), key + ".jsonl"), {"ts": now(), "kind": "runner", "text": f"could not start /work-on: {e}; the next tick tries again", "state": "parked"})
        return False
    print(f"run_dossier: unparked {key}")
    return True


def iso_epoch(ts: str) -> float:
    """`2026-11-16T10:57:10Z` as epoch seconds; 0 when it does not parse."""
    try:
        return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0.0


def was_built_before(updates_path: str) -> bool:
    """True when the run's updates hold a `start /work-on` line before the newest `start /plan` line: this /plan run is
    a re-plan of a dossier a build already worked on (sent back by RUNNER: REPLAN, or started again by the owner)."""
    starts = []
    try:
        for line in open(updates_path, encoding="utf-8"):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            m = re.match(r"start /([\w-]+) ", str(o.get("text", ""))) if o.get("kind") == "runner" else None
            if m:
                starts.append(m.group(1))
    except OSError:
        return False
    last_plan = max((i for i, c in enumerate(starts) if c == "plan"), default=-1)
    return "work-on" in starts[:max(last_plan, 0)]


def blocked_by_of(text: str) -> list[str]:
    """The ids in the front matter's `blocked_by` (an inline list or a block list); comments dropped."""
    m = re.match(r"---\n(.*?)\n---\n", text or "", re.S)
    if not m:
        return []
    fm = m.group(1)
    b = re.search(r"(?m)^blocked_by:[ \t]*\[([^\]]*)\]", fm)
    if b:
        items = b.group(1).split(",")
    else:
        b = re.search(r"(?m)^blocked_by:[ \t]*(?:#.*)?\n((?:[ \t]+-[^\n]*\n?)+)", fm)
        items = [ln.strip()[1:] for ln in b.group(1).splitlines()] if b else []
    return [i for i in (x.split("#")[0].strip().strip("'\"") for x in items) if i]


def open_blockers(root: str, dossier: str) -> list[str]:
    """The dossier's blockers that are not done yet, each as `W-023 (building)`; a blocker with no dossier file counts
    as `missing`. The same rule as the dossier graph (generate_open_work.py): only `done` frees a dependant."""
    d = os.path.join(root, ".discovery", "dossiers")

    def text_of(did: str) -> str | None:
        try:
            names = sorted(n for n in os.listdir(d) if re.match(re.escape(did) + r"(-.*)?\.md$", n))
            return dossier_text(os.path.join(d, names[0])) if names else None
        except OSError:
            return None
    own = text_of(dossier)
    out = []
    for b in blocked_by_of(own or ""):
        t = text_of(b)
        st = front_status(t) if t is not None else "missing"
        if st != "done":
            out.append(f"{b} ({st or 'no status'})")
    return out


def merged_build(root: str, dossier: str) -> str:
    """Why a /work-on would rebuild a change already merged, or "" when a build is due. The dossier's PR merged
    (`<key>.merge.json` state merged, written by merge_dossier.py) and the dossier file has not changed since: a copy
    that still reads `ready` on development only means its stub is not integrated yet (W-173). A re-plan commits the
    dossier again, after the merge, and lifts this."""
    rec = read_json(os.path.join(runs_dir(), key_of(root, dossier) + ".merge.json"), {}) or {}
    if rec.get("state") != "merged" or not rec.get("ts"):
        return ""
    d = os.path.join(root, ".discovery", "dossiers")
    try:
        names = sorted(n for n in os.listdir(d) if re.match(re.escape(dossier) + r"(-.*)?\.md$", n))
    except OSError:
        names = []
    if names:
        r = subprocess.run(["git", "-C", root, "log", "-1", "--format=%cI", "--", os.path.join(".discovery", "dossiers", names[0])],
                           capture_output=True, text=True)
        changed = r.stdout.strip()
        try:
            if changed and datetime.datetime.fromisoformat(changed) > datetime.datetime.fromisoformat(str(rec["ts"]).replace("Z", "+00:00")):
                return ""
        except ValueError:
            pass
    prs = ", ".join(f"#{n}" for n in rec.get("prs") or []) or "its PR"
    return (f"already built: {prs} merged into {rec.get('base') or 'its base'} at {rec['ts']}; nothing to build "
            f"(a copy reading ready on development only waits for the stub's integration)")


def front_status(text: str) -> str:
    m = re.match(r"---\n(.*?)\n---\n", text or "", re.S)
    s = re.search(r"(?m)^status:[ \t]*(\S+)", m.group(1)) if m else None
    return s.group(1) if s else ""


def replan_followup(root: str, dossier: str, since: float = 0.0) -> tuple[str, str]:
    """What a finished re-plan hands to /work-on: ("plain", "") when this checkout's dossier reads `ready` (local mode,
    or committed with the plan merged and pulled); ("stacked", "plan/<KEY>") when the checkout's copy is tracked and
    not ready, and a `plan/*` branch on origin committed at or after `since` carries this dossier as `ready` (the newest
    such branch); ("none", <why>) otherwise."""
    d = os.path.join(root, ".discovery", "dossiers")
    try:
        names = sorted(n for n in os.listdir(d) if re.match(re.escape(dossier) + r"(-.*)?\.md$", n))
    except OSError:
        names = []
    status = ""
    if names:
        try:
            status = front_status(dossier_text(os.path.join(d, names[0])) or "")
        except OSError:
            pass
        if status == "ready":
            return "plain", ""
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", os.path.join(".discovery", "dossiers", names[0])],
                                 cwd=root, capture_output=True, text=True, stdin=subprocess.DEVNULL).returncode == 0
        if not tracked:
            return "none", f"the dossier reads `{status or 'no status'}`, not ready"
    git = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    try:
        git("fetch", "--quiet", "origin", "+refs/heads/plan/*:refs/remotes/origin/plan/*")
        refs = git("for-each-ref", "--sort=-committerdate", "--format=%(committerdate:unix) %(refname:short)", "refs/remotes/origin/plan/").stdout.split("\n")
        for row in refs:
            parts = row.split(" ", 1)
            if len(parts) != 2 or not parts[0].isdigit() or int(parts[0]) < since - 60:
                continue
            ref = parts[1]
            files = git("ls-tree", "--name-only", ref, ".discovery/dossiers/").stdout.split("\n")
            mine = [f for f in files if re.match(r"\.discovery/dossiers/" + re.escape(dossier) + r"(-.*)?\.md$", f)]
            if mine and front_status(git("show", f"{ref}:{mine[0]}").stdout) == "ready":
                return "stacked", ref.split("/", 1)[1]
    except (OSError, subprocess.TimeoutExpired):
        return "none", "git could not list the plan branches"
    return "none", f"the dossier reads `{status or 'missing'}` here and no plan/* branch since the re-plan started carries it as ready"


def unknown_dossier(root: str, dossier: str, command: str) -> str:
    """Why a run must not start: the id names no dossier file (`.discovery/dossiers/<ID>.md` or `<ID>-<slug>.md` in
    the main checkout). A typo, or the literal `selftest` typed without its dashes, otherwise starts a real run
    that claims a driver quota place. /plan (a Jira key or a seed has no file yet) and /stub (a release name) are
    exempt, except for the literal `selftest`. "" when it may start."""
    if dossier == "selftest":
        return "`selftest` is not a dossier id: did you mean --selftest?"
    if command not in ("work-on", "fix-deferred") or os.environ.get("ASD_ALLOW_UNKNOWN_DOSSIER") == "1":   # the env is the selftest fixtures' (fake ids, no files)
        return ""
    folder = os.path.join(root, ".discovery", "dossiers")
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    if any(n == f"{dossier}.md" or (n.startswith(dossier + "-") and n.endswith(".md")) for n in names):
        return ""
    return f"no dossier file for {dossier} (looked for {os.path.join(folder, dossier)}.md and {dossier}-*.md); nothing started"


def already_built(root: str, dossier: str) -> str:
    """Why /work-on must not start: the dossier is in the merge queue, or has an open pull request and no build of its
    own to resume. Its status on development can still read `ready`: the record that says done is on its branch, not
    merged yet. Starting it anyway deadlocks an integration: the run waits for the stub, the stub waits for the merge,
    and the merge waits for the run. Not built: a draft left by a build that RUNNER: REPLAN sent back, once its re-plan
    finished (the newest record is that /plan run): the build was never finished, and /work-on picks it up again. "" when
    it may start, also when GitHub cannot be asked."""
    repo = main_repo_name(root)
    queue = read_json(os.path.join(runs_dir(), f"{repo}.merge-queue.json"), {}) or {}
    if any(i.get("id") == dossier for i in queue.get("items") or []):
        return f"{dossier} is in the merge queue; it is built"
    prev = read_json(os.path.join(runs_dir(), f"{repo}.{dossier}.runner.json"), {}) or {}
    if prev.get("command") == "work-on" and prev.get("state") in ("running", "testing", "waiting", "paused", "queued", "stopped", "failed"):
        return ""
    replanned = prev.get("command") == "plan" and was_built_before(os.path.join(runs_dir(), f"{repo}.{dossier}.jsonl"))
    try:
        p = subprocess.run(shlex.split(os.environ.get("ASD_GH_BIN") or "gh") + ["pr", "list", "--state", "open", "--limit", "200", "--json", "number,headRefName,isDraft"],
                           cwd=root, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        prs = json.loads(p.stdout or "[]") if p.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return ""
    pat = re.compile(r"(^|[^A-Za-z0-9])" + re.escape(dossier) + r"(?![0-9])", re.I)
    mine = [x for x in prs if pat.search(x.get("headRefName", "")) and not x["headRefName"].startswith(("plan/", "docs/"))]
    if replanned and all(x.get("isDraft") for x in mine):
        return ""
    mine = [x["number"] for x in mine]
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
json.dump({k: v for k, v in os.environ.items() if k.startswith("ASD_DRIVER_")}, open(os.path.join(d, f"drv.{n}"), "w"))
ask = os.path.join(os.environ["ASD_ASK_OWNER"])
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
argm = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else ""
if "--resume" in sys.argv and script.get("no_resume_result"):   # claude 2.1.x: no init, the error comes as a result event
    out({"type": "result", "subtype": "error_during_execution", "is_error": True, "session_id": "",
         "result": "No conversation found with session ID: " + sys.argv[sys.argv.index("--resume") + 1]})
    import time; time.sleep(30)
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
    if script.get("stall_progress"):   # a Bash tool_use that never returns while the CLI keeps sending progress lines
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tp", "name": "Bash", "input": {"command": "git pull"}}]}})
        out({"type": "system", "subtype": "task_started"})
        import time
        for _ in range(2000):
            time.sleep(0.3)
            out({"type": "tool_progress"})
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
            if a.get("progress"):   # the CLI's own progress lines while the call runs, as a real Bash call sends them
                out({"type": "system", "subtype": "task_started"})
                end = time.monotonic() + a["hold"]
                while time.monotonic() < end:
                    time.sleep(min(a["progress"], max(0.0, end - time.monotonic())))
                    out({"type": "tool_progress"})
            else:
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


def selftest_reserved(check) -> None:
    """One reserved slot per active repo: temp dirs only, never the owner's ~/.agentic."""
    tmp = tempfile.mkdtemp(prefix="asd-reserved-")
    keep = {k: os.environ.get(k) for k in ("ASD_ACTIVE_REPOS_FILE", "ASD_RESERVE_TTL")}
    os.environ["ASD_RESERVE_TTL"] = "0"
    try:
        def dossier(root: str, name: str, status: str) -> None:
            d = os.path.join(root, ".discovery", "dossiers")
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, name), "w").write(f"---\nid: {name[:-3]}\nstatus: {status}\n---\nbody\n")
        ra, rb = os.path.join(tmp, "a"), os.path.join(tmp, "b")
        dossier(ra, "A-1.md", "ready")
        dossier(rb, "B-1.md", "done")
        os.environ["ASD_ACTIVE_REPOS_FILE"] = os.path.join(tmp, "missing.json")
        check("reserved: a missing file reserves nothing and reads as None", read_active_repos() is None and reserved_repos() == set())
        q = lambda *ks: [(queue_order({"queued_since": f"2026-11-20T10:0{i}:00Z"}), k) for i, k in enumerate(ks)]
        # today's rule without a file: held < cap, nobody ahead
        check("reserved: no reservations: admitted below the cap", slot_admission("x", 4, {"x": 1, "y": 2}, [], None, set()) == (True, "shared"))
        check("reserved: no reservations: full at the cap", not slot_admission("x", 3, {"x": 1, "y": 2}, [], None, set())[0])
        check("reserved: no reservations: nobody ahead in the FIFO", not slot_admission("x", 4, {"y": 1}, q("x.W-1", "x.W-2"), queue_order({"queued_since": "2026-11-20T10:09:00Z"}), set())[0])
        write_json(os.environ["ASD_ACTIVE_REPOS_FILE"], {"repos": {"a": ra, "b": rb}})
        check("reserved: a repo with a not-done dossier reserves", reserved_repos() == {"a"})
        check("reserved: a repo with only done dossiers releases its slot", "b" not in reserved_repos())
        dossier(rb, "B-2.md", "blocked")
        check("reserved: any status but done counts (blocked)", reserved_repos() == {"a", "b"})
        write_json(os.environ["ASD_ACTIVE_REPOS_FILE"], {"repos": {"a": ra}})
        check("reserved: a repo that is not active reserves nothing", reserved_repos() == {"a"})
        # a idle and reserved: a shared run of repo x may not take the last slot
        ok, why = slot_admission("x", 4, {"x": 1, "y": 2}, [], None, {"a"})
        check("reserved: an idle reserved repo blocks the last shared slot", not ok and "shared slot (1 reserved for a)" in why)
        check("reserved: ...while one slot fewer in use is still shared", slot_admission("x", 4, {"x": 1, "y": 1}, [], None, {"a"})[0])
        check("reserved: ...and once the reserved repo runs, its slot is no longer deducted", slot_admission("x", 4, {"a": 1, "x": 1, "y": 1}, [], None, {"a"}) == (True, "shared"))
        # the reserved repo's first queued run jumps the shared FIFO, the second does not
        qq = [(queue_order({"queued_since": "2026-11-20T10:00:00Z"}), "x.W-1"), (queue_order({"queued_since": "2026-11-20T10:01:00Z"}), "a.A-1")]
        mine_a1 = queue_order({"queued_since": "2026-11-20T10:01:00Z"})
        ok, how = slot_admission("a", 4, {"x": 2, "y": 1}, qq[:1], mine_a1, {"a"})
        check("reserved: the first queued run of a reserved repo jumps the shared FIFO", ok and how == "reserved")
        mine_a2 = queue_order({"queued_since": "2026-11-20T10:02:00Z"})
        ok, why = slot_admission("a", 4, {"x": 2, "y": 1}, qq, mine_a2, {"a"})
        check("reserved: its second queued run does not (the first of its repo is ahead)", not ok and "earlier run of this repo" in why)
        check("reserved: a reserved repo still waits when every slot is in use", not slot_admission("a", 3, {"x": 2, "y": 1}, [], None, {"a"})[0])
        check("reserved: a reserved repo already running goes through the shared rule", not slot_admission("a", 4, {"a": 1, "x": 2}, [], None, {"a", "b"})[0]
              and slot_admission("a", 5, {"a": 1, "x": 2}, [], None, {"a", "b"})[0])
        # cap at or below the reservations: each reserved repo runs one, the shared pool is empty
        res = {"a", "b", "c"}
        check("reserved: cap below the reservations: each reserved repo runs one", all(slot_admission(r, 2, {}, [], None, res)[0] for r in res)
              and slot_admission("c", 2, {"a": 1, "b": 1}, [], None, res)[0])
        check("reserved: ...the shared pool is empty", not slot_admission("x", 3, {"a": 1, "b": 1, "c": 1}, [], None, res)[0]
              and not slot_admission("x", 2, {"a": 1}, [], None, res)[0])
        check("reserved: ...and a second run of a running reserved repo does not take a reserved slot", not slot_admission("a", 2, {"a": 1, "b": 1}, [], None, res)[0])
        check("reserved: a run back from testing over the cap does not keep the reserved repo waiting",
              slot_admission("a", 3, {"x": 3}, [], None, {"a"}, over=1) == (True, "reserved")
              and not slot_admission("a", 3, {"x": 3}, [], None, {"a"})[0])
        check("reserved: ...but the over-cap run still counts for the shared pool", not slot_admission("x", 3, {"x": 3}, [], None, {"a"}, over=1)[0])
        check("reserved: repo_of_key reads the repo off a run key", repo_of_key("my.repo.W-9") == "my.repo" and repo_of_key("r.W-9.plan-D-2") == "r")
        # the CLI writes the file (a temp one)
        os.environ["ASD_ACTIVE_REPOS_FILE"] = os.path.join(tmp, "cli.json")
        rc = cmd_active(["--add", "zed", "--root", ra]) | cmd_active(["--add", "yak", "--root", rb]) | cmd_active(["--remove", "zed"])
        check("reserved: the CLI adds and removes a repo", rc == 0 and read_active_repos() == {"yak": rb} and cmd_active(["--remove", "nope"]) == 1)
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def selftest_main_lock(check) -> None:
    """The runner's status write never edits a committed-mode main checkout: notes, or WORKTREE GONE for an open branch."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "app")
        d = os.path.join(root, ".discovery", "dossiers")
        os.makedirs(d)
        g = lambda *a: subprocess.run(["git", "-C", root, *a], capture_output=True, text=True)  # noqa: E731
        g("init", "-q")
        for did in ("W-77", "W-78"):
            open(os.path.join(d, f"{did}-x.md"), "w").write(f"---\nid: {did}\nstatus: ready\n---\n# {did}\n\n## Build log\n")
        g("add", "-A")
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        g("update-ref", "refs/remotes/origin/feat/W-78-x", "HEAD")
        seen: list[str] = []
        check("main lock: no open branch: the status write succeeds",
              set_dossier_status(root, "W-77", "done", seen.append))
        check("main lock: ...the committed dossier is untouched",
              "status: ready" in open(os.path.join(d, "W-77-x.md")).read() and not g("status", "--porcelain").stdout.strip())
        check("main lock: ...the notes file carries it",
              "status: done" in open(os.path.join(root, ".discovery", "notes", "W-77-x.md")).read())
        check("main lock: ...and readers see it overlaid", front_status(dossier_text(os.path.join(d, "W-77-x.md")) or "") == "done")
        seen.clear()
        check("main lock: open branch, worktree gone: nothing written, False",
              not set_dossier_status(root, "W-78", "planned", seen.append)
              and "status: ready" in open(os.path.join(d, "W-78-x.md")).read()
              and not os.path.exists(os.path.join(root, ".discovery", "notes", "W-78-x.md")))
        check("main lock: ...and the log says WORKTREE GONE with the branch", any("WORKTREE GONE" in m and "feat/W-78-x" in m for m in seen))
        check("main lock: one not-done dossier left (W-78) keeps the repo buildable", has_buildable_dossier(root))


def selftest() -> int:
    me = os.path.abspath(__file__)
    failures: list[str] = []
    # Leak guard: nothing a fixture asks or runs may reach the owner's real stores; no ruling of the owner's may shape an argv here.
    real_dirs = [os.environ.get("ASD_QUESTIONS_DIR") or os.path.expanduser("~/.agentic/questions"), runs_dir()]
    listing = lambda: {d: {f for f in os.listdir(d) if f.startswith(("repo.", "clone.", "W-"))} if os.path.isdir(d) else set() for d in real_dirs}
    before_real = listing()
    rulings_tmp = tempfile.mkdtemp(prefix="asd-rulings-")
    os.environ["ASD_RULINGS_DIR"] = rulings_tmp
    # The owner's active-repos file would reserve slots for real repos and starve the fixture runs (children inherit this).
    os.environ["ASD_ACTIVE_REPOS_FILE"] = os.path.join(rulings_tmp, "no-active-repos.json")

    def check(name: str, ok: bool) -> None:
        print(("ok   " if ok else "FAIL ") + name, flush=True)
        if not ok:
            failures.append(name)

    selftest_reserved(check)
    selftest_main_lock(check)

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
        env = dict(os.environ, ASD_ALLOW_UNKNOWN_DOSSIER="1", ASD_QUESTIONS_DIR=qdir, ASD_RUNS_DIR=rdir, ASD_FAKE_DIR=fdir, ASD_RUNNER_DEFERRED="0", ASD_RUNNER_SUMMARY="0", ASD_RUNNER_IDLE="1",
                   ASD_CLAUDE_BIN=f"{shlex.quote(sys.executable)} {shlex.quote(fake)}",
                   ASD_ASK_OWNER=ASK_OWNER, ASD_OWNER_POLL="0.1", ASD_RUNNER_GRACE="0.2",
                   # the runners of this test see no accounts file (the one default account), and the paced cap is off: the
                   # tests of pacing and of the pings set what they need
                   ASD_RUNNER_ADAPTIVE="0", ASD_ACCOUNTS=os.path.join(tmp, "no-accounts.json"), ASD_ACCOUNT_STATE=os.path.join(tmp, "e2e-state.json"),
                   ASD_USAGE_DIR=os.path.join(tmp, "usage-e2e"),
                   ASD_DRIVERS_FILE=os.path.join(tmp, "no-drivers.json"))   # never the owner's real ~/.agentic/drivers.json
        # the transcripts the fake sessions "left": a run only resumes a session whose transcript its account holds
        tproj0 = os.path.join(tmp, "tproj0")
        os.makedirs(os.path.join(tproj0, "p"))
        for i in range(1, 6):
            open(os.path.join(tproj0, "p", f"sess-{i}.jsonl"), "w").write("{}\n")
        env["ASD_TRANSCRIPTS_DIR"] = tproj0
        for k in [k for k in env if k.startswith("ASD_DRIVER_")]:
            del env[k]

        def scenario(procs: list[dict]) -> None:
            for f in os.listdir(fdir):
                if f == "count" or f.startswith(("in.", "pid.", "env.", "drv.")):
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
            check(f"replan {did}: a re-plan that did not reach ready does not requeue /work-on",
                  bool(until(lambda: any("/work-on not requeued" in t for t in texts(did)), 30)) and state_of(did).get("command") == "plan")

        # ---- 2a'. a finished re-plan that reached ready requeues /work-on --------
        dpath = os.path.join(root, ".discovery", "dossiers", "W-92-x.md")
        open(dpath, "w").write("---\nid: W-92\nstatus: ready\nblocked_by: []\n---\n# W-92\n")
        append_line(os.path.join(rdir, "repo.W-92.jsonl"), {"ts": now(), "kind": "runner", "text": f"start /work-on W-92 in {root}", "state": "running"})
        write_json(os.path.join(rdir, "repo.W-92.runner.json"), {"command": "work-on", "state": "done"})
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-92", "--command", "plan")
        check("requeue: the re-plan run ends done", p.wait(timeout=60) == 0)
        check("requeue: /work-on starts by itself and gets the plain command",
              bool(until(lambda: state_of("W-92").get("command") == "work-on" and state_of("W-92").get("state") == "done", 60))
              and msgs(2)[0] == f"/{PLUGIN}:work-on W-92" and any("/work-on W-92 requeued" in t for t in texts("W-92")))
        # ---- 2a''. a requeued /work-on whose blocker is not done parks (no session), and unparks once it is done ----
        ddir = os.path.join(root, ".discovery", "dossiers")
        bpath = os.path.join(ddir, "W-95-blocker.md")
        open(bpath, "w").write("---\nid: W-95\nstatus: building\n---\n")
        open(os.path.join(ddir, "W-94-x.md"), "w").write("---\nid: W-94\nstatus: ready\nblocked_by: [W-92, W-95]  # why\n---\n# W-94\n")
        check("blocked_by_of: inline, comment dropped", blocked_by_of("---\nid: X\nblocked_by: [W-1, 'W-2']  # c\n---\n") == ["W-1", "W-2"])
        check("blocked_by_of: block list", blocked_by_of("---\nid: X\nblocked_by:\n  - W-1\n  - W-2 # c\nstatus: ready\n---\n") == ["W-1", "W-2"])
        open(os.path.join(ddir, "W-92-x.md"), "w").write("---\nid: W-92\nstatus: done\n---\n")
        check("open_blockers: a done blocker frees, others are named with their status",
              open_blockers(root, "W-94") == ["W-95 (building)"] and blocked_by_of("---\nblocked_by: []\n---\n") == [])
        open(os.path.join(ddir, "W-96-x.md"), "w").write("---\nid: W-96\nstatus: ready\nblocked_by: [W-97]\n---\n")
        check("open_blockers: a blocker with no dossier file counts as missing", open_blockers(root, "W-96") == ["W-97 (missing)"])
        os.remove(os.path.join(ddir, "W-96-x.md"))
        # ---- a /work-on of a dossier whose PR already merged builds nothing and asks nothing (W-173) ----
        open(os.path.join(ddir, "W-188-x.md"), "w").write("---\nid: W-188\nstatus: ready\n---\n# W-188\n")
        write_json(os.path.join(rdir, "repo.W-188.merge.json"), {"state": "merged", "prs": [594], "base": "stub/2026-11-18", "ts": now()})
        p = start("W-188")
        check("merged: /work-on of a merged dossier ends done at once, no session, no question, reason named",
              p.wait(timeout=60) == 0 and state_of("W-188").get("state") == "done" and not state_of("W-188").get("session")
              and not state_of("W-188").get("questions") and any("already built: #594 merged into stub/2026-11-18" in t for t in texts("W-188")))
        os.remove(os.path.join(rdir, "repo.W-188.merge.json"))
        mroot = os.path.join(tmp, "mrepo")
        mdd = os.path.join(mroot, ".discovery", "dossiers")
        os.makedirs(mdd)
        subprocess.run(["git", "init", "-q", mroot], capture_output=True)
        open(os.path.join(mdd, "W-99-x.md"), "w").write("---\nid: W-99\nstatus: ready\n---\n")
        subprocess.run(["git", "-C", mroot, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", mroot, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "plan"], capture_output=True)
        mkey = os.path.join(rdir, key_of(mroot, "W-99") + ".merge.json")

        def mb():
            was = os.environ.get("ASD_RUNS_DIR")
            os.environ["ASD_RUNS_DIR"] = rdir
            try:
                return merged_build(mroot, "W-99")
            finally:
                os.environ.pop("ASD_RUNS_DIR") if was is None else os.environ.update(ASD_RUNS_DIR=was)
        write_json(mkey, {"state": "merged", "prs": [7], "base": "stub/x", "ts": "2000-01-01T00:00:00Z"})
        check("merged: a dossier committed again after the merge (a re-plan) is built again", mb() == "")
        write_json(mkey, {"state": "merged", "prs": [7], "base": "stub/x", "ts": "2999-01-01T00:00:00Z"})
        check("merged: a dossier unchanged since its merge is not built again", mb().startswith("already built: #7"))
        write_json(mkey, {"state": "failed", "prs": [7], "ts": "2999-01-01T00:00:00Z"})
        check("merged: a failed merge record does not block a build", mb() == "")

        def in_env(fn):
            saved = dict(os.environ)
            os.environ.update(env)
            try:
                return fn()
            finally:
                os.environ.clear()
                os.environ.update(saved)
        append_line(os.path.join(rdir, "repo.W-94.jsonl"), {"ts": now(), "kind": "runner", "text": f"start /work-on W-94 in {root}", "state": "running"})
        write_json(os.path.join(rdir, "repo.W-94.runner.json"), {"command": "work-on", "state": "done"})
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-94", "--command", "plan")
        check("park: the re-plan run ends done", p.wait(timeout=60) == 0)
        check("park: the requeued /work-on parks on the open blocker, no session",
              bool(until(lambda: state_of("W-94").get("command") == "work-on" and state_of("W-94").get("state") == "parked", 60))
              and (state_of("W-94").get("parked") or {}).get("blockers") == ["W-95 (building)"] and not state_of("W-94").get("session")
              and any("parked: waits for W-95 (building)" in t for t in texts("W-94")))
        spath = os.path.join(rdir, "repo.W-94.runner.json")
        check("unpark: nothing starts while the blocker is open", in_env(lambda: unpark(spath, state_of("W-94"))) is False and state_of("W-94").get("state") == "parked")
        open(bpath, "w").write("---\nid: W-95\nstatus: done\n---\n")
        check("unpark: a done blocker starts /work-on again, which builds", in_env(lambda: unpark(spath, state_of("W-94"))) is True
              and bool(until(lambda: state_of("W-94").get("state") == "done", 60)) and msgs(2)[0] == f"/{PLUGIN}:work-on W-94"
              and any("every blocker is done" in t for t in texts("W-94")))
        env["ASD_RUNNER_PARK"] = "0"
        open(bpath, "w").write("---\nid: W-95\nstatus: building\n---\n")
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-94")
        check("park: ASD_RUNNER_PARK=0 builds anyway", p.wait(timeout=60) == 0 and state_of("W-94").get("state") == "done")
        del env["ASD_RUNNER_PARK"]
        for n in ("W-94-x.md", "W-95-blocker.md", "W-92-x.md"):
            os.remove(os.path.join(ddir, n))

        open(dpath, "w").write("---\nid: W-93\nstatus: ready\n---\n")
        os.rename(dpath, dpath.replace("W-92", "W-93"))
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-93", "--command", "plan")
        check("requeue: a first /plan (no build before it) does not start /work-on",
              p.wait(timeout=60) == 0 and state_of("W-93").get("command") == "plan" and not any("requeued" in t for t in texts("W-93")))
        bad = subprocess.run([sys.executable, me, "--root", root, "--dossier", "W-92", "--plan-branch", "feat/x"], env=env, capture_output=True, text=True)
        check("--plan-branch takes only plan/<KEY>", bad.returncode == 2)

        # ---- 2a''. replan_followup on real git: plain, stacked on the newest ready plan branch, none ----
        g = lambda cwd, *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *a], cwd=cwd, capture_output=True, text=True)
        origin, work = os.path.join(tmp, "rf-origin.git"), os.path.join(tmp, "rf-work")
        g(tmp, "init", "-q", "--bare", origin)
        g(tmp, "clone", "-q", origin, work)
        os.makedirs(os.path.join(work, ".discovery", "dossiers"))
        rf = os.path.join(work, ".discovery", "dossiers", "W-7-qc.md")
        open(rf, "w").write("---\nid: W-7\nstatus: planned\n---\n")
        g(work, "add", "."); g(work, "commit", "-qm", "base"); g(work, "push", "-q", "origin", "HEAD:development")
        g(work, "checkout", "-qb", "plan/OLD"); open(rf, "w").write("---\nid: W-7\nstatus: planned\n---\nold\n")
        g(work, "commit", "-qam", "old plan"); g(work, "push", "-q", "origin", "plan/OLD")
        g(work, "checkout", "-q", "development") if g(work, "rev-parse", "--verify", "-q", "development").returncode == 0 else g(work, "checkout", "-q", "-b", "development", "origin/development")
        check("replan_followup: committed, no ready plan branch -> none", replan_followup(work, "W-7")[0] == "none")
        g(work, "checkout", "-qb", "plan/LABS-1", "origin/development"); open(rf, "w").write("---\nid: W-7\nstatus: ready\n---\nnew\n")
        g(work, "commit", "-qam", "plan"); g(work, "push", "-q", "origin", "plan/LABS-1")
        g(work, "checkout", "-q", "origin/development")
        check("replan_followup: committed, plan PR open -> stacked on its branch", replan_followup(work, "W-7") == ("stacked", "plan/LABS-1"))
        check("replan_followup: a plan branch older than the re-plan does not count", replan_followup(work, "W-7", time.time() + 3600)[0] == "none")
        open(rf, "w").write("---\nid: W-7\nstatus: ready\n---\n")
        check("replan_followup: the checkout's copy reads ready -> plain", replan_followup(work, "W-7") == ("plain", ""))
        check("was_built_before: a /work-on start before the newest /plan start", was_built_before(os.path.join(rdir, "repo.W-92.jsonl"))
              and not was_built_before(os.path.join(rdir, "repo.W-93.jsonl")) and not was_built_before(os.path.join(tmp, "absent.jsonl")))

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
        scenario([{"crash": 143}, {"no_resume_result": True}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-93")
        check("outside kill: 'No conversation found' as an error result is a fresh stage too, no owner question",
              p.wait(timeout=60) == 0 and state_of("W-93").get("state") == "done" and not os.path.exists(qfile("repo.W-93.Q1"))
              and any("could not be resumed (No conversation found" in t for t in texts("W-93"))
              and "--resume" not in open(os.path.join(fdir, "env.3")).read() and msgs(3)[0] == f"/{PLUGIN}:work-on W-93")
        # the run's recorded session sits under another account's config dir: a restart of the same account starts fresh
        write_json(os.path.join(rdir, "repo.W-95.runner.json"), {"pid": 2 ** 22 + 12347, "state": "running", "root": root, "command": "work-on",
                                                              "session": "sess-elsewhere", "account": "default"})
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-95")
        check("restart: a session whose transcript is not under the account's dir is not resumed; one event says so",
              p.wait(timeout=60) == 0 and "--resume" not in open(os.path.join(fdir, "env.1")).read()
              and msgs(1)[0] == f"/{PLUGIN}:work-on W-95"
              and sum("belongs to another account or was not found" in t for t in texts("W-95")) == 1)
        scenario([{"crash": 143}, {"crash": 137}, {"crash": 143}])
        p = start("W-91")
        check("outside kill: past ASD_RUNNER_KILL_RETRIES the owner rules", bool(until(lambda: os.path.exists(qfile("repo.W-91.Q1")))))
        owner_line("repo.W-91.Q1", "answer", "B", "B")
        check("outside kill: three in a row end failed", p.wait(timeout=60) == 1 and any("2 retries in a row" in t for t in texts("W-91")))

        # ---- 2b. a duplicate run (the dossier is owned by another live run) ends quietly: no owner question ----
        scenario([{"turns": [[{"final": "RUNNER: FAILED duplicate run: W-2 is already owned by the live run repo.seed-x, which is waiting on question W-2.Q2; this run changed nothing"}]]}])
        p = start("W-88")
        check("duplicate run: ends failed, exit 1, with no owner question and the reason logged",
              p.wait(timeout=60) == 1 and state_of("W-88").get("state") == "failed" and not os.path.exists(qfile("repo.W-88.Q1"))
              and any("duplicate run" in t for t in texts("W-88")))

        # ---- 2c. planning blocked on a PR: the runner waits for it to merge (no question), closed unmerged goes to the session ----
        gh_merge = os.path.join(tmp, "gh_merge.py")
        open(gh_merge, "w").write(
            "import sys, os, json\n"
            "if sys.argv[2] == 'list': print('[]'); sys.exit(0)\n"
            "f = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gh_merge.n')\n"
            "n = int(open(f).read()) if os.path.exists(f) else 0\n"
            "open(f, 'w').write(str(n + 1))\n"
            "pr = sys.argv[3]\n"
            "if pr == '636': print(json.dumps({'state': 'OPEN', 'mergedAt': None, 'baseRefName': 'stub/1'} if n < 2 else {'state': 'MERGED', 'mergedAt': '2026-11-24T12:00:00Z', 'baseRefName': 'stub/1'}))\n"
            "else: print(json.dumps({'state': 'CLOSED', 'mergedAt': None, 'baseRefName': 'stub/1'}))\n")
        for dos, pr, want in (("W-87", "636", "PR RESULT 636: merged into stub/1"), ("W-86", "637", "PR RESULT 637: closed without merging")):
            scenario([{"turns": [[{"final": f"stubs need PR {pr}\nRUNNER: WAITING merged:{pr}"}], [{"final": "RUNNER: DONE"}]]}])
            env.update(ASD_GH_BIN=f"{sys.executable} {gh_merge}", ASD_PR_POLL="0.2")
            rc = start(dos).wait(timeout=60)
            for k_ in ("ASD_GH_BIN", "ASD_PR_POLL"):
                env.pop(k_)
            got = [m for m in msgs(1) if m.startswith("PR RESULT")]
            check(f"merge wait: merged:{pr} is waited for by the runner, the verdict goes into the session, no owner question",
                  rc == 0 and len(got) == 1 and want in got[0] and not os.path.exists(qfile(f"repo.{dos}.Q1"))
                  and any(f"waiting for PR {pr} to merge" in t for t in texts(dos)))
        check("merge wait: closed unmerged tells the session to ask the owner (a real decision)", "ask the owner" in got[0])

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
        check("re-exec after a clean stage end: the next stage starts fresh, never --resume of the split session",
              "--resume" not in open(os.path.join(fdir, "env.2")).read())
        # a restart keeps its slot with the cap full; a fresh start queues behind; a dead holder frees its slot
        rd = runs_dir()
        cap_env = {k: env.get(k) for k in ("ASD_RUNNER_MAX_LIVE", "ASD_RUNNER_SLOT_POLL")}
        env["ASD_RUNNER_SLOT_POLL"] = "0.2"
        busy_files = [os.path.join(rd, f"other.B-{i}.runner.json") for i in range(3)]
        for bf in busy_files:
            write_json(bf, {"pid": os.getpid(), "state": "testing"})   # alive, holds no slot yet
        env["ASD_RUNNER_MAX_LIVE"] = str(slot_census("x")[0] + 3)   # runs left over from earlier cases count: the cap is 3 above them
        shutil.copy(me, copy)   # the copy is back to the original code: the append below is a new change
        scenario([{"turns": [[{"bash": "sleep 1", "hold": 3, "after": 2}, {"append": copy, "line": "# a second deployed fix"}, {"final": "RUNNER: STAGE-DONE one"}]]},
                  {"turns": [[{"final": "RUNNER: DONE"}]]}])
        rp = subprocess.Popen([sys.executable, copy, "--root", root, "--dossier", "W-97", "--foreground"], env=env,
                              stdout=open(os.path.join(tmp, "out.W-97.txt"), "w"), stderr=subprocess.STDOUT)
        until(lambda: any("stage 1 start" in t for t in texts("W-97")), 20)
        for bf in busy_files:
            write_json(bf, {"pid": os.getpid(), "state": "running"})   # now 3 of 3 slots are held, this run being one of 3 + itself
        try:
            ended = rp.wait(timeout=40)
        except subprocess.TimeoutExpired:
            rp.kill(); ended = None
        check("re-exec keeps the slot: the restarted run goes on with 3 of 3 busy, no queue",
              ended == 0 and any("runner code changed" in t for t in texts("W-97")) and any("keeps the slot" in t for t in texts("W-97"))
              and not any(t.startswith("queued") for t in texts("W-97")))
        class _R:   # a stand-in runner: take_slot only needs these
            key, stopping, ka_since, state = "repo.W-99", False, None, {}
            def __init__(me2): me2.calls = []; me2.root = root
            def save(me2, **kw): me2.calls.append(("save", kw))
            def event(me2, *a): me2.calls.append(("event", a[0]))
            def rehome_account(me2): me2.calls.append(("rehome",))
            def keepalive_tick(me2): me2.calls.append(("tick",))
        r = _R(); os.environ["ASD_REEXEC_HELD"] = r.key
        check("re-exec marker: keeps the slot and still picks the account (rehome)",
              Runner.take_slot(r, rehome=True) is True and ("rehome",) in r.calls and not os.environ.get("ASD_REEXEC_HELD"))
        r = _R(); os.environ["ASD_REEXEC_HELD"] = "other.key"
        os.environ["ASD_DISK_FLOOR_GIB"] = "99999999"
        r.stopping = True   # stop the loop at once: only whether the marker returned early matters
        check("re-exec marker: a disk below the floor does not return at once (falls to the queue loop)",
              Runner.take_slot(r) is False and ("rehome",) not in r.calls)
        r = _R(); os.environ["ASD_REEXEC_HELD"] = r.key; r.stopping = True
        check("re-exec marker: with the disk below the floor the held slot is not taken at once",
              Runner.take_slot(r, rehome=True) is False and ("rehome",) not in r.calls and not any(c[0] == "save" and c[1].get("state") == "running" for c in r.calls))
        class _Q(_R):   # waits once for a slot (the disk is below the floor), then is stopped
            command = ""
            def keepalive_tick(self): self.stopping = True
        saved_poll = os.environ.get("ASD_RUNNER_SLOT_POLL"); os.environ["ASD_RUNNER_SLOT_POLL"] = "0.01"
        queued_first = {}
        for cmd in ("resolve-merge", "after-merge", "work-on"):
            rq_ = _Q(); rq_.command = cmd
            Runner.take_slot(rq_)  # pyright: ignore[reportArgumentType]
            queued_first[cmd] = [c[1].get("queued_first") for c in rq_.calls if c[0] == "save" and c[1].get("state") == "queued"]
        if saved_poll is None: os.environ.pop("ASD_RUNNER_SLOT_POLL")
        else: os.environ["ASD_RUNNER_SLOT_POLL"] = saved_poll
        check("slots: a run the merge queue started (resolve-merge, after-merge) queues first, an ordinary run does not",
              queued_first == {"resolve-merge": [True], "after-merge": [True], "work-on": [None]})
        os.environ.pop("ASD_DISK_FLOOR_GIB")
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        fp = subprocess.Popen([sys.executable, copy, "--root", root, "--dossier", "W-98", "--foreground"], env=env,
                              stdout=open(os.path.join(tmp, "out.W-98.txt"), "w"), stderr=subprocess.STDOUT)
        check("fresh start queues behind the 3 busy runs", bool(until(lambda: state_of("W-98").get("state") == "queued", 20)))
        dead = subprocess.Popen(["/usr/bin/true"]); dead.wait()
        write_json(busy_files[0], {"pid": dead.pid, "state": "running"})   # a crashed holder
        check("a crashed holder's slot is reclaimed by the queued run", fp.wait(timeout=40) == 0 and state_of("W-98").get("state") == "done")
        for bf in busy_files:
            try: os.remove(bf)
            except OSError: pass
        for k, v in cap_env.items():
            env.pop(k, None) if v is None else env.__setitem__(k, v)
        vroot = os.path.join(tmp, "cache", "asd")
        def cache_dir(name: str, age: int, script: bool = True) -> str:
            """A plugin-cache version dir whose script was installed `age` seconds ago (the dir stays empty without it)."""
            os.makedirs(os.path.join(vroot, name, "scripts"))
            f = os.path.join(vroot, name, "scripts", "run_dossier.py")
            if script:
                open(f, "w").write("#")
                os.utime(f, (time.time() - age, time.time() - age))
            return f
        v9 = cache_dir("1.9.0", 400)
        v10 = cache_dir("1.10.0", 300)
        cache_dir("1.2.30", 500)
        cache_dir("1.11.0", 0, script=False)   # a version without the script does not count
        check("re-exec: under a plugin cache the most recently installed version wins, not the highest number",
              script_stamp(v9)[0] == v10 and script_stamp(v10)[0] == v10)
        s1 = cache_dir("224e8127ba5d", 200)
        check("re-exec: a SHA dir newer than the semver dirs wins, from a semver or a SHA runner",
              script_stamp(v10)[0] == s1 and script_stamp(s1)[0] == s1)
        s2 = cache_dir("0123456789ab", 100)
        cache_dir("fedcba987654", 0, script=False)   # a SHA dir without the script is ignored
        check("re-exec: the newest of two SHA dirs wins", script_stamp(s1)[0] == s2 and script_stamp(v9)[0] == s2)
        check("re-exec: a sibling dir without the script is ignored even when it is the newest dir",
              os.path.join("fedcba987654") not in script_stamp(s2)[0])
        mdir = os.path.join(tmp, "checkout", "scripts")
        os.makedirs(mdir)
        mf = os.path.join(mdir, "run_dossier.py")
        open(mf, "w").write("#")
        check("re-exec: a path outside a plugin cache is used as it is", script_stamp(mf)[0] == mf)

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
        write_json(os.path.join(rdir, "repo.W-78.runner.json"), {"state": "done", "command": "plan", "pid": 0})
        with open(os.path.join(rdir, "repo.W-78.jsonl"), "w") as fh:
            fh.write("".join(json.dumps({"kind": "runner", "text": f"start /{c} W-78 in {root}"}) + "\n" for c in ("work-on", "plan")))
        check("work-on: a ready (not draft) PR still refuses after a re-plan", "#427" in already_built_for_test(root, "W-78", env))
        with open(os.path.join(fdir, "gh_prs.py"), "w") as fh:
            fh.write("import json\nprint(json.dumps([{'number': 427, 'headRefName': 'feat/W-78-x', 'isDraft': True}]))\n")
        check("work-on: the draft of a build sent back to /plan does not refuse its requeue", already_built_for_test(root, "W-78", env) == "")
        with open(os.path.join(rdir, "repo.W-78.jsonl"), "w") as fh:
            fh.write(json.dumps({"kind": "runner", "text": f"start /plan W-78 in {root}"}) + "\n")
        check("work-on: a draft PR refuses after a plain /plan run", "#427" in already_built_for_test(root, "W-78", env))
        for f in ("repo.W-78.runner.json", "repo.W-78.jsonl"):
            os.remove(os.path.join(rdir, f))
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
        # ---- an owner reply on a queued run's question (answer or follow-up), or a message, queues it first ----
        oq, ork = os.environ.get("ASD_QUESTIONS_DIR"), os.environ.get("ASD_RUNS_DIR")
        os.environ["ASD_QUESTIONS_DIR"], os.environ["ASD_RUNS_DIR"] = qdir, rdir
        try:
            def qline(name: str, *ls: dict) -> None:
                with open(os.path.join(qdir, name), "w") as fh:
                    fh.write("".join(json.dumps(l) + "\n" for l in ls))
            ask = {"ts": "2026-11-21T15:22:29Z", "from": "agent", "kind": "question", "text": "q"}
            qline("repo.W-80.plan-D-1.Q3.jsonl", ask, {"ts": "2026-11-21T15:49:42.292Z", "from": "owner", "kind": "followup", "text": "f"})
            check("queue: an owner follow-up on a plan run's question after it queued counts as a reply",
                  owner_replied_since("repo.W-80.plan-D-1", "2026-11-21T15:32:16Z"))
            check("queue: ...one from before it queued does not, nor does it count for the source dossier's run",
                  not owner_replied_since("repo.W-80.plan-D-1", "2026-11-21T16:00:00Z") and not owner_replied_since("repo.W-80", "2026-11-21T15:32:16Z"))
            check("queue: sub-second owner times compare as times, not strings",
                  owner_replied_since("repo.W-80.plan-D-1", "2026-11-21T15:49:42Z"))
            qline("repo.W-81.Q1.jsonl", ask)
            check("queue: only the agent's own lines are no reply", not owner_replied_since("repo.W-81", "2026-11-21T15:00:00Z"))
            with open(inbox_path("repo.W-81"), "w") as fh:
                fh.write(json.dumps({"ts": "2026-11-21T16:00:00Z", "text": "go on"}) + "\n")
            check("queue: a dashboard message to the run counts as a reply", owner_replied_since("repo.W-81", "2026-11-21T15:00:00Z"))
            os.remove(inbox_path("repo.W-81"))
        finally:
            for k, v in (("ASD_QUESTIONS_DIR", oq), ("ASD_RUNS_DIR", ork)):
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
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
        check("testing: a poll on a detached run's state directory is a known wait", bool(TESTING_CMD.search("S=/var/folders/x/T/run_gates-detach/2026-ab; [ -f $S/exit ]")) and not TESTING_CMD.search("ls scripts"))
        p = start("W-92")
        went = until(lambda: state_of("W-92").get("state") == "testing", 20)
        check("testing: the state flips to testing while run_tests.py is in flight", bool(went))
        check("testing: CI waits are long waits by name",
              all(TESTING_CMD.search(c) for c in ("python3 scripts/wait_ci.py --pr 501 --timeout 570 2>&1 | tail -15",
                                                   "gh pr checks 501 --watch", "gh run watch 123"))
              and not any(TESTING_CMD.search(c) for c in ("gh pr checks 501", "gh run view 123", "cat wait_ci.pyc")))
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
        # the CLI's progress lines during a long call are not work: a quiet poll loop still flips (W-018, 2026-11-13)
        scenario([{"turns": [[{"bash": "for i in $(seq 1 58); do sleep 10; done", "hold": 6, "progress": 0.3, "after": 3}, {"final": "RUNNER: DONE"}]]}])
        p = start("W-94")
        check("testing: progress lines of the call in flight do not hide a quiet wait", bool(until(lambda: state_of("W-94").get("state") == "testing", 20)))
        p.wait(timeout=60)
        env.pop("ASD_TESTING_QUIET_SECS")  # ASD_RUNNER_MAX_LIVE stays set: the disk-floor cases below pop it
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

        # ---- an id that names no dossier file starts nothing; the literal `selftest` says --selftest ----
        udir = os.path.join(tmp, "unk")
        os.makedirs(os.path.join(udir, ".discovery", "dossiers"))
        open(os.path.join(udir, ".discovery", "dossiers", "W-7-slug.md"), "w").write("---\nid: W-7\n---\n")
        check("unknown dossier: a slugged file resolves", unknown_dossier(udir, "W-7", "work-on") == "")
        check("unknown dossier: no file is refused for work-on and fix-deferred",
              "no dossier file for W-8" in unknown_dossier(udir, "W-8", "work-on") and unknown_dossier(udir, "W-8", "fix-deferred") != "")
        check("unknown dossier: /plan and /stub need no file", unknown_dossier(udir, "PROJ-1", "plan") == "" and unknown_dossier(udir, "stub-1", "stub") == "")
        check("unknown dossier: the literal `selftest` suggests --selftest, for every command",
              all("did you mean --selftest?" in unknown_dossier(udir, "selftest", c) for c in ("work-on", "plan", "stub")))
        rs = subprocess.run([sys.executable, me, "selftest", "--root", udir, "--foreground"], capture_output=True, text=True,
                            env=dict(os.environ, ASD_DRIVERS_FILE=os.path.join(tmp, "no-drivers.json")))
        check("`run_dossier.py selftest` (no dashes) exits 2 and starts nothing",
              rs.returncode == 2 and "did you mean --selftest?" in rs.stderr and not glob.glob(os.path.join(rdir, "*selftest*")))
        # ---- an id a live seed run already claimed starts no second /plan (or /work-on); a finished seed run frees it ----
        pr_name = main_repo_name(udir)
        seed_key = f"{pr_name}.seed-claims"
        write_json(os.path.join(rdir, seed_key + ".runner.json"), {"command": "plan", "state": "running"})
        append_line(os.path.join(rdir, seed_key + ".jsonl"), {"kind": "stage", "stage": "phase 1", "text": "W-218 (claimed, final), key x"})
        open(os.path.join(udir, ".discovery", "dossiers", "W-218-hook.md"), "w").write("---\nid: W-218\nstatus: planned\n---\n")
        pe = dict(os.environ, ASD_DRIVERS_FILE=os.path.join(tmp, "no-drivers.json"))
        for cmd in ("plan", "work-on"):
            rc = subprocess.run([sys.executable, me, "W-218", "--root", udir, "--command", cmd, "--foreground"], capture_output=True, text=True, env=pe)
            check(f"claimed id: /{cmd} of an id a live seed run claimed is refused and starts nothing",
                  rc.returncode == 2 and f"seed-claims" in rc.stderr and "being planned" in rc.stderr and not os.path.exists(os.path.join(rdir, f"{pr_name}.W-218.runner.json")))
        check("claimed id: the claim is read by the same helper", planning_runs.claimed_why(pr_name, rdir, "W-218") != "" and planning_runs.claimed_why(pr_name, rdir, "W-219") == "")
        write_json(os.path.join(rdir, seed_key + ".runner.json"), {"command": "plan", "state": "done"})
        check("claimed id: a finished seed run frees the id", planning_runs.claimed_why(pr_name, rdir, "W-218") == "")
        os.remove(os.path.join(rdir, seed_key + ".runner.json")); os.remove(os.path.join(rdir, seed_key + ".jsonl"))
        # ---- driver modes: ~/.agentic/drivers.json (ASD_DRIVERS_FILE) exported into the claude child ----
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-95")
        drv = lambda n: read_json(os.path.join(fdir, f"drv.{n}"), None)
        done95 = p.wait(timeout=60) == 0
        check("drivers: no file means no ASD_DRIVER_* in the child and every mode off in the metadata",
              done95 and drv(1) == {} and state_of("W-95").get("drivers") == {n: "off" for n in driver_modes.NAMES})
        dfile = os.path.join(tmp, "drivers.json")
        write_json(dfile, {"pre_fanout_check": "shadow", "test_round": "SHADOW", "bogus": "on", "strength_gate_prep": "maybe"})
        env["ASD_DRIVERS_FILE"] = dfile
        env["ASD_DRIVER_TEST_ROUND"] = "on"   # an explicit variable in the runner's environment outranks the file
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-96")
        done96 = p.wait(timeout=60) == 0
        check("drivers: the file's modes are exported; an explicit ASD_DRIVER_* wins",
              done96 and drv(1) == {"ASD_DRIVER_PRE_FANOUT_CHECK": "shadow", "ASD_DRIVER_TEST_ROUND": "on"})
        check("drivers: the modes used are recorded in the runner metadata",
              state_of("W-96").get("drivers") == {"pre_fanout_check": "shadow", "strength_gate_prep": "off", "test_round": "on",
                                                      "vr_promote_preview": "off"})
        t96 = texts("W-96")
        check("drivers: an unknown name and an unknown mode are warned about in the runner log",
              any("unknown driver 'bogus'" in t for t in t96) and any("strength_gate_prep: unknown mode 'maybe'" in t for t in t96))
        del env["ASD_DRIVER_TEST_ROUND"]
        pinned97 = {n: "off" for n in driver_modes.NAMES}
        pinned97["test_round"] = "shadow"
        write_json(os.path.join(rdir, "repo.W-97.runner.json"), {"state": "running", "command": "work-on", "drivers": pinned97})
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-97")
        done97 = p.wait(timeout=60) == 0
        check("drivers: a restarted, unfinished run keeps the modes it recorded, not the file's current ones",
              done97 and drv(1) == {"ASD_DRIVER_TEST_ROUND": "shadow"} and state_of("W-97").get("drivers") == pinned97)
        pinned98 = dict(pinned97, pre_fanout_check="on")
        write_json(dfile, {"test_round": "on"})   # pre_fanout_check is now off in the file: the rollback wins over the pin
        write_json(os.path.join(rdir, "repo.W-98.runner.json"), {"state": "running", "command": "work-on", "drivers": pinned98})
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-98")
        done98 = p.wait(timeout=60) == 0
        check("drivers: a driver switched off in the file is off for a restarted run too; the others stay pinned",
              done98 and drv(1) == {"ASD_DRIVER_TEST_ROUND": "shadow"} and state_of("W-98").get("drivers") == pinned97)

        # ---- the run quota: only the next N fresh /work-on runs carry the file's modes ----
        qfile_ = os.path.join(tmp, "quota-drivers.json")
        env["ASD_DRIVERS_FILE"] = qfile_
        write_json(qfile_, {"test_round": "shadow", "pre_fanout_check": "on"})
        check("quota: no quota in the file means every run carries the modes (old behaviour)", driver_modes.load_quota(qfile_) is None)
        subprocess.run([sys.executable, os.path.join(os.path.dirname(me), "driver_modes.py"), "--file", qfile_, "limit", "2"], check=True, capture_output=True)
        qmodes = lambda d: state_of(d).get("drivers")
        qshadow = {n: "off" for n in driver_modes.NAMES}
        qshadow.update(test_round="shadow", pre_fanout_check="on")
        qoff = {n: "off" for n in driver_modes.NAMES}
        q3env: dict | None = None
        for did in ("W-q1", "W-q2", "W-q3"):
            scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
            start(did).wait(timeout=60)
            if did == "W-q3":
                q3env = drv(1)
        check("quota 2: the first two /work-on runs carry the modes", qmodes("W-q1") == qshadow and qmodes("W-q2") == qshadow)
        check("quota 2: the third /work-on run gets every driver off, and says why",
              qmodes("W-q3") == qoff and any("drivers: off (quota of 2 used up)" in t for t in texts("W-q3")))
        check("quota 2: the third run's child gets no ASD_DRIVER_*", q3env == {})
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        start("W-q4", "--command", "plan").wait(timeout=60)
        check("quota: a plan run gets every driver off and does not claim", qmodes("W-q4") == qoff and len((driver_modes.load_quota(qfile_) or {})["claimed"]) == 2)
        write_json(os.path.join(rdir, "repo.W-q1.runner.json"), {"state": "running", "command": "work-on", "drivers": qshadow})
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        start("W-q1").wait(timeout=60)
        check("quota: a restarted, claimed run keeps its modes after the quota is full, and does not claim again",
              qmodes("W-q1") == qshadow and len((driver_modes.load_quota(qfile_) or {})["claimed"]) == 2)
        # the script-only drivers are exempt from the quota (full quota, non-/work-on run) and never claim a place
        write_json(qfile_, {"test_round": "shadow", "vr_promote_preview": "shadow", "_quota": {"limit": 2, "claimed": [{"run": "x"}, {"run": "y"}]}})
        qfree = {n: "off" for n in driver_modes.NAMES}
        qfree.update(vr_promote_preview="shadow")
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        start("W-q5").wait(timeout=60)
        check("quota full: the script-only drivers still get their file mode, the token ones are off",
              qmodes("W-q5") == qfree and drv(1) == {"ASD_DRIVER_VR_PROMOTE_PREVIEW": "shadow"})
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        start("W-q6", "--command", "plan").wait(timeout=60)
        check("a plan run gets the script-only drivers too", qmodes("W-q6") == qfree)
        write_json(qfile_, {"vr_promote_preview": "shadow", "_quota": {"limit": 2, "claimed": []}})
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        start("W-q7").wait(timeout=60)
        check("a run carrying only script-only drivers claims no quota place",
              len((driver_modes.load_quota(qfile_) or {})["claimed"]) == 0 and drv(1) == {"ASD_DRIVER_VR_PROMOTE_PREVIEW": "shadow"})
        # (several processes claiming at once is proved in driver_modes.py --selftest)
        env["ASD_DRIVERS_FILE"] = os.path.join(tmp, "no-drivers.json")

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
        cf = "Failed to authenticate. API Error: 403 <!DOCTYPE html> Attention Required! | Cloudflare"
        check("auth outage: a Cloudflare 403 sign-in block retries like a network error, bounded; a usage limit and a plain 5xx are not auth",
              network_error(cf) and auth_outage(cf) and auth_outage("API Error: 401 authentication_error")
              and not auth_outage("API Error: 503 Service Unavailable") and not auth_outage("usage limit reached") and not network_error("TypeError: x is undefined"))
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

        # ---- the CI breakpoint: the runner runs wait_ci (a fake here), the verdict goes into the live session ----
        ci_fake = os.path.join(tmp, "wait_ci_fake.py")
        open(ci_fake, "w").write(
            "import os,sys\na=sys.argv[1:]\nopen(os.path.join(os.path.dirname(__file__),'ci.args'),'a').write(' '.join(a)+'\\n')\n"
            "n=int(a[a.index('--pr')+1])\n"
            "if n==71: print('all green'); sys.exit(0)\n"
            "if n==72: print('FAIL unit-tests'); print('assert 1 == 2'); sys.exit(1)\n"
            "if n==74: print('conflicting'); sys.exit(5)\n"
            "if n==75: print('no run for the head'); sys.exit(6)\n"
            "clean=os.path.exists(os.path.join(os.path.dirname(__file__),'parent.clean'))\n"
            "if n==76 and clean: print('all green'); sys.exit(0)\n"
            "if n==76: print('wait_ci: CI not started: PR #76 is stacked on #620 (branch x), which conflicts with stub/1'); sys.exit(7)\n"
            "if n==77: print('wait_ci: CI not started: PR #77 is stacked on #621 (branch y), which conflicts with stub/1'); sys.exit(7)\n"
            "print('still pending'); sys.exit(3)\n")
        # a conflicting parent (exit 7): the runner starts a resolve-merge run for it, waits until the parent is clean, then waits on the child's CI again
        res_fake = os.path.join(tmp, "parent_resolve_fake.py")
        open(res_fake, "w").write("import os,sys\nd=os.path.dirname(__file__)\nopen(os.path.join(d,'resolve.args'),'a').write(' '.join(sys.argv[1:])+'\\n')\n"
                                  "if '--items' in sys.argv and sys.argv[sys.argv.index('--items')+1]=='620': open(os.path.join(d,'parent.clean'),'w').write('1')\n"
                                  "print('started in the background')\n")
        gh_par = os.path.join(tmp, "gh_parent_fake.py")
        open(gh_par, "w").write("import os,sys,json\nd=os.path.dirname(__file__)\na=sys.argv[1:]\n"
                                "if a[:2]==['pr','view']:\n"
                                "    n=a[2]; clean=n=='620' and os.path.exists(os.path.join(d,'parent.clean'))\n"
                                "    print(json.dumps({'state':'OPEN','mergeStateStatus':'CLEAN' if clean else 'DIRTY','mergeable':'MERGEABLE' if clean else 'CONFLICTING'})); sys.exit(0)\n"
                                "print('[]')\n")
        dos_dir = os.path.join(root, ".discovery", "dossiers"); os.makedirs(dos_dir, exist_ok=True)
        open(os.path.join(dos_dir, "W-43-ci.md"), "w").write("---\nid: W-43\nstatus: ready\n---\n")
        for dos, prs, want in (("W-43", "ci:71", ["CI RESULT 71: green all green"]),
                               ("W-44", "ci:72", ["CI RESULT 72: red FAIL unit-tests | assert 1 == 2"]),
                               ("W-45", "ci:73", ["CI RESULT 73: timeout still pending"]),
                               ("W-48", "ci:75", ["CI RESULT 75: never-started CI did not run on #75 (it did not fail): no run for the head",
                                                  "RUNNER: FAILED CI never started on PR #75"]),
                               ("W-50", "ci:76", ["CI RESULT 76: green all green"]),
                               ("W-51", "ci:77", ["CI RESULT 77: never-started CI did not run on #77 (it did not fail): PR #77 is stacked on #621"]),
                               ("W-46", "ci:71,ci:72,ci:74", ["CI RESULT 71: green", "CI RESULT 72: red", "CI RESULT 74: red (wait_ci exit 5) conflicting"])):
            scenario([{"turns": [[{"final": f"pushed\nRUNNER: WAITING {prs}"}], [{"final": "RUNNER: DONE"}]]}])
            env.update(ASD_WAIT_CI_BIN=ci_fake, ASD_CI_TIMEOUT="123", ASD_PARENT_RESOLVE_BIN=f"{sys.executable} {res_fake}",
                       ASD_GH_BIN=f"{sys.executable} {gh_par}", ASD_PARENT_POLL="0.2", ASD_PARENT_GRACE="0.3")
            p = start(dos)
            rc = p.wait(timeout=60)
            for k_ in ("ASD_WAIT_CI_BIN", "ASD_CI_TIMEOUT", "ASD_PARENT_RESOLVE_BIN", "ASD_GH_BIN", "ASD_PARENT_POLL", "ASD_PARENT_GRACE"):
                env.pop(k_)
            sent = "\n".join(m for m in msgs(1) if m.startswith("CI RESULT"))
            check(f"ci breakpoint {prs}: waits without a question and sends {' / '.join(w.split(':')[0] + ':' + w.split(':')[1] for w in want)}",
                  rc == 0 and all(w in sent for w in want) and any(t.startswith("waiting on CI #") for t in texts(dos))
                  and not any("asked" in t for t in texts(dos)))
            if not (rc == 0 and all(w in sent for w in want)):
                print(f"   debug {dos}: rc={rc} sent={sent!r} texts={texts(dos)[-8:]}")
        res_args = open(os.path.join(tmp, "resolve.args")).read()
        check("ci parent conflict: the runner starts one resolve-merge run for the parent (not the child, not the queue) and the session is told nothing until CI answers",
              res_args.count("--command resolve-merge --items 620 ") == 1 and "W-50 " in res_args and "--items 76" not in res_args
              and any(t.startswith("parent #620 conflicts") and "no owner question" in t for t in texts("W-50"))
              and any(t.startswith("parent #620 no longer conflicts") for t in texts("W-50")))
        check("ci parent conflict: a parent the run could not resolve gives the session a never-started verdict that forbids resolving it itself or asking the owner",
              "--items 621" in res_args and not any("asked" in t for t in texts("W-51")))
        check("ci parent conflict: the verdict for a parent the runner could not resolve tells the session not to resolve it or ask",
              "do NOT resolve it yourself" in CI_PARENT_NEXT and "do NOT ask the owner" in CI_PARENT_NEXT)
        # exit 7 is not exit 6: a second wait on the parent-blocked PR is not refused
        scenario([{"turns": [[{"final": "RUNNER: WAITING ci:77"}], [{"final": "RUNNER: WAITING ci:77"}], [{"final": "RUNNER: DONE"}]]}])
        env.update(ASD_WAIT_CI_BIN=ci_fake, ASD_CI_TIMEOUT="123", ASD_PARENT_RESOLVE_BIN=f"{sys.executable} {res_fake}",
                   ASD_GH_BIN=f"{sys.executable} {gh_par}", ASD_PARENT_POLL="0.2", ASD_PARENT_GRACE="0.3")
        rc = start("W-52").wait(timeout=90)
        for k_ in ("ASD_WAIT_CI_BIN", "ASD_CI_TIMEOUT", "ASD_PARENT_RESOLVE_BIN", "ASD_GH_BIN", "ASD_PARENT_POLL", "ASD_PARENT_GRACE"):
            env.pop(k_)
        sent52 = [m for m in msgs(1) if m.startswith("CI RESULT 77")]
        check("ci parent conflict: the repeated wait after a parent verdict is not refused (the never-started guard is for exit 6 only)",
              rc == 0 and len(sent52) == 2 and not any("refused" in m for m in sent52))
        # the session waits on a never-started PR again anyway: refused, wait_ci not run a second time
        n75_before = open(os.path.join(tmp, "ci.args")).read().count("--pr 75")
        scenario([{"turns": [[{"final": "RUNNER: WAITING ci:75"}], [{"final": "RUNNER: WAITING ci:75"}], [{"final": "RUNNER: DONE"}]]}])
        env.update(ASD_WAIT_CI_BIN=ci_fake, ASD_CI_TIMEOUT="123")
        rc = start("W-49").wait(timeout=60)
        env.pop("ASD_WAIT_CI_BIN"); env.pop("ASD_CI_TIMEOUT")
        sent49 = [m for m in msgs(1) if m.startswith("CI RESULT 75")]
        check("ci never-started: the repeated wait is refused and wait_ci runs once",
              rc == 0 and len(sent49) == 2 and "refused" in sent49[1] and "never-started" in sent49[0]
              and open(os.path.join(tmp, "ci.args")).read().count("--pr 75") == n75_before + 1)
        ci_args = open(os.path.join(tmp, "ci.args")).read()
        check("ci breakpoint: wait_ci gets --pr, the long --timeout and the dossier as --log",
              "--pr 71 --timeout 123.0 --log " + os.path.join(dos_dir, "W-43-ci.md") in ci_args)
        check("ci breakpoint: mixing ci: with a question id is refused, the session carries on",
              (scenario([{"turns": [[{"final": "RUNNER: WAITING ci:71,repo.W-47.Q1"}], [{"final": "RUNNER: DONE"}]]}]) or True)
              and start("W-47").wait(timeout=60) == 0 and any("mixes ci:" in m for m in msgs(1)))

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
        env.pop("ASD_RUNNER_FWD_EVERY"); env["ASD_TRANSCRIPTS_DIR"] = tproj0
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
        env["ASD_TRANSCRIPTS_DIR"] = tproj0; env["ASD_RUNNER_IDLE"] = "1"
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

        # ---- plan --seed: a free-form /plan under a seed-<slug> key, the brief kept beside the run ----
        seedf = os.path.join(tmp, "brief.md")
        open(seedf, "w").write("Rate-limit the webhook route.\nDetails.\n")
        scenario([{"turns": [[{"final": "planned W-60\nRUNNER: DONE"}]]}])
        p = start("seed-webhook-limit", "--command", "plan", "--seed", seedf)
        rc = p.wait(timeout=60)
        kept = os.path.join(rdir, "repo.seed-webhook-limit.seed.md")
        check("seed: the brief is copied beside the run's records", rc == 0 and open(kept).read() == open(seedf).read())
        check("seed: the session gets /plan with the kept brief's path", msgs(1)[0].startswith(f"/{PLUGIN}:plan the description in {kept}"))
        check("seed: runs under <repo>.seed-<slug>, and no /work-on is requeued", state_of("seed-webhook-limit").get("state") == "done"
              and not any("requeued" in t for t in texts("seed-webhook-limit")))
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("seed-webhook-limit", "--command", "plan")
        check("seed: a restart without --seed finds the kept brief", p.wait(timeout=60) == 0 and msgs(1)[0].startswith(f"/{PLUGIN}:plan the description in {kept}"))
        for bad_args, why in ((["seed-nope", "--command", "plan"], "no stored seed"),
                              (["seed-webhook-limit", "--command", "work-on"], "a seed id with another command"),
                              (["W-61", "--command", "plan", "--seed", seedf], "--seed with a dossier id"),
                              (["seed-Bad_Slug", "--command", "plan", "--seed", seedf], "a slug outside seed-<lowercase words>"),
                              (["seed-empty", "--command", "plan", "--seed", os.devnull], "an empty seed")):
            r = subprocess.run([sys.executable, me, "--root", root, "--dossier", bad_args[0], "--foreground", *bad_args[1:]], env=env, capture_output=True, text=True, timeout=30)
            check(f"seed: refused: {why}", r.returncode == 2)

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
        check("background: each task carries the tool that launched it", [t["tool"] for t in background_tasks(mv)] == ["Bash"])
        rw = object.__new__(Runner)
        rw.turn_open, rw.stopping, rw.state = True, False, {"state": "testing"}
        setattr(rw, "proc", type("P", (), {"poll": lambda self: None})())
        rw.last_out = rw.last_work = time.monotonic() - 73 * 60
        rw.inflight_bash = {"t1": ("python3 other_tool.py --root . --dossier W-182", time.monotonic() - 73 * 60)}
        rw.bg_wait, rw.stall_bg_at = "x", 0.0
        rw.background = lambda: []
        check("stall watchdog: the previous process's 73 minute old Bash call reads as hung", rw.hung_bash(45 * 60) != "" and rw.stalled())
        rw.reset_watchdog()
        check("stall watchdog: a new process starts with a fresh idle clock and no Bash call in flight",
              rw.hung_bash(45 * 60) == "" and not rw.inflight_bash and not rw.stalled() and rw.bg_wait == "")
        # a fresh process whose stale clocks predate it (queue wait, resumed session's old events): never hung, never stalled
        rs = object.__new__(Runner)
        rs.turn_open, rs.stopping, rs.state = True, False, {"state": "testing"}
        setattr(rs, "proc", type("P", (), {"poll": lambda self: None})())
        rs.background = lambda: []
        rs.spawned_at = time.monotonic()
        rs.last_out = rs.last_work = time.monotonic() - 687 * 60
        rs.inflight_bash = {"t1": ("python3 fix_deferred_pr.py --root . --dossier W-182", time.monotonic() - 687 * 60)}
        rs.bg_wait, rs.stall_bg_at = "", 0.0
        check("stall watchdog: stage just started, resumed session's last event hours old -> no hung Bash, no stall",
              rs.hung_bash(45 * 60) == "" and not rs.stalled())
        rs.spawned_at = time.monotonic() - 100 * 60
        rs.last_out = rs.last_work = rs.spawned_at
        rs.inflight_bash = {"t1": ("python3 fix_deferred_pr.py --root . --dossier W-182", rs.spawned_at)}
        check("stall watchdog: a known test/CI wait is judged by its own longer limit, a plain Bash call by the stall limit",
              rs.hung_bash(45 * 60) == "" and rs.hung_bash(45 * 60) == ""
              and (rs.inflight_bash.update({"t2": ("sleep 99999", rs.spawned_at)}) or rs.hung_bash(45 * 60) == "sleep 99999"))
        rb = object.__new__(Runner)
        rb.turn_open, rb.inflight_bash, rb.bg_wait = False, {}, "background: Wait for PR 518 CI"
        check("background wait: a closed turn waiting only on background commands is blocked (slot released)", rb.blocked_on() == "background: Wait for PR 518 CI")
        rb.bg_wait = ""
        check("background wait: a closed turn with no background command wait is not blocked", rb.blocked_on() == "")
        check("wait label: names the CI wait, not the leading plugin-path assignment",
              wait_label("P=/Users/x/.claude/plugins/cache/a/b/4adf; cd /w && python3 $P/scripts/wait_ci.py --pr 640 --timeout 580") == "python3 wait_ci.py --pr 640 --timeout 580")
        check("wait label: a quiet call with no test command skips assignments and cd",
              wait_label("P=/p; W=/w; cd $W && make long-thing") == "make long-thing" and wait_label("") == "a Bash call")
        rb.turn_open, rb.bg_wait, rb.last_out, rb.state, rb.stopping = False, "x", 0.0, {}, False
        rb.track_event({"type": "assistant", "message": {"content": []}})
        check("background wait: the session's next event ends the background wait", rb.turn_open and rb.bg_wait == "")
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
        env["ASD_TRANSCRIPTS_DIR"] = tproj0
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
        check("repair gate: a /stub run has it on unless ASD_STACK_REPAIR=0",
              stack_repair.enabled("stub") and not stack_repair.enabled("work-on"))
        os.environ["ASD_STACK_REPAIR"] = "0"
        check("repair gate: ASD_STACK_REPAIR=0 turns it off for /stub too", not stack_repair.enabled("stub"))
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
        scenario([{"turns": [[{"final": "RUNNER: ENV vm limactl: instance nightwatch is stopped"}],
                             [{"final": "RUNNER: ENV stack fake-llm fixture is stale"}], [{"final": "RUNNER: DONE"}]]}])
        p = start("W-12")
        rc = p.wait(timeout=60)
        check("repair run: gate off, the ENV line is answered 'off' and no command runs",
              rc == 0 and any(m.startswith("RUNNER ENV vm: automatic repair is off") for m in msgs(1)) and calls() == [])
        check("repair run: a stale fake-llm fixture is routed to the runner's own handling, never an ask, gate off or not",
              any(m.startswith("RUNNER ENV stack: the fake-llm fixture is the runner's to handle") and "ask_owner" not in m
                  for m in msgs(1)) and calls() == [])
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

        # ---- the ENV stack path of a dossier run never ends in an owner question (handle_env, fakes only) ----
        def env_runner(command: str):
            r = object.__new__(Runner)
            r.command, r.root = command, root
            r.event = lambda _t: None
            return r
        real_lo, real_cfg = stack_env.leftover, stack_env.leftover_config
        seen: list = []
        outcome = {"state": "repaired", "name": "proj-w1", "why": "torn down with the repo's own down from /persistent"}
        def fake_leftover(tree, cfg, only="", run=None, log=None):
            seen.append((tree, only))
            return dict(outcome)
        stack_env.leftover, stack_env.leftover_config = fake_leftover, lambda _t: {"vm": "nightwatch", "leftover_projects": [{"name": "proj-w1"}]}
        refusal = "opmode-stack: foreign_project_state owner=/main repair=run_deploy_host_opmode-stack.sh_down"
        os.environ.pop("ASD_STACK_REPAIR", None)
        a = env_runner("work-on").handle_env("stack " + refusal, 1)
        check("env stack: a provably dead leftover project is repaired by the runner whatever ASD_STACK_REPAIR says",
              a.startswith("RUNNER ENV stack: repaired by the runner") and seen == [(root, refusal)] and "ask_owner" not in a)
        outcome.update(state="skipped", why="the suite window holder pid 7 (/main) is still alive")
        a = env_runner("work-on").handle_env("stack " + refusal, 1)
        check("env stack: a live holder / held lease / failed down answers skip-to-stub with the STACK-ENV head, never an owner question",
              "STACK-ENV: skipped \u2014 leftover project proj-w1" in a and "/stub full suite" in a
              and "Not an owner question" not in a and "not an owner question" in a and "Ask the owner" not in a)
        a = env_runner("work-on").handle_env("stack " + refusal, 5)
        check("env stack: reported again and again, still a skip", "STACK-ENV: skipped" in a and "Ask the owner" not in a)
        stack_env.leftover_config = lambda _t: None
        check("env stack: a repo with no leftover project configured skips, never asks",
              "STACK-ENV: skipped" in env_runner("work-on").handle_env("stack " + refusal, 1))
        a = env_runner("work-on").handle_env("stack suite_window_active holder=/other", 1)
        check("env stack: another run's live suite window is a skip, nothing repaired", "STACK-ENV: skipped" in a and "Ask the owner" not in a)
        a = env_runner("work-on").handle_env("stack nats unhealthy", 1)
        check("env stack: repair off for a dossier run now skips to the stub instead of asking",
              "STACK-ENV: skipped" in a and "Ask the owner" not in a)
        env_stub = env_runner("stub").handle_env("stack nats unhealthy", 1)
        check("env stack: a /stub run keeps its behaviour (repair on by default, never the skip)",
              "STACK-ENV: skipped" not in env_stub)
        os.environ["ASD_STACK_REPAIR"] = "0"
        env_stub = env_runner("stub").handle_env("stack nats unhealthy", 1)
        check("env stack: a /stub run with repair off still asks the owner as before",
              env_stub.startswith("RUNNER ENV stack: automatic repair is off") and "Ask the owner" in env_stub)
        os.environ.pop("ASD_STACK_REPAIR")
        check("env disk: unchanged, left to the owner", "Ask the owner" in env_runner("work-on").handle_env("disk 1 GiB free", 1))
        check("env vm: unchanged for a dossier run, repair off asks the owner",
              "Ask the owner" in env_runner("work-on").handle_env("vm stopped", 1))
        stack_env.leftover, stack_env.leftover_config = real_lo, real_cfg
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
        # per-account burn: an account's own rate counts once it has BURN_MIN_SAMPLES samples; before that the pooled rate
        fresh()
        write_json(BURN_FILE(), {"rates": {"five_hour": 3.0}, "last": {}})   # the shape before per-account rates
        r_old, s_old = burn_for("main")
        check("burn: an old burn.json (pooled rates only) still loads: pooled where it has a rate, the prior elsewhere",
              r_old == {"five_hour": 3.0, "seven_day": BURN_PRIOR["seven_day"]} and s_old == {"five_hour": "pooled", "seven_day": "prior"})
        fresh()
        learn_burn("main", win(10, 5000), 1000.0, running=2)
        for i in (1, 2):
            learn_burn("main", win(10 + 4 * i, 5000), 1000.0 + 1800 * i, running=2)    # 4 points/run-hour each
        check("burn: two samples on an account are not enough: its rate is still the pooled one",
              burn_for("main")[1]["five_hour"] == "pooled" and abs(burn_for("main")[0]["five_hour"] - burn_rates()["five_hour"]) < 1e-9)
        learn_burn("main", win(22, 5000), 1000.0 + 1800 * 3, running=2)
        rm, sm = burn_for("main")
        check("burn: the third sample switches the account to its own rate (4.0, not the pooled EWMA)",
              sm["five_hour"] == "account" and abs(rm["five_hour"] - 4.0) < 1e-6 and abs(burn_rates()["five_hour"] - 4.0) > 0.1)
        check("burn: another account with no samples still uses the pooled rate", burn_for("gmail")[1]["five_hour"] == "pooled")
        check("burn: the account's sample count is stored", read_json(BURN_FILE(), {})["by_account"]["main"]["five_hour"]["n"] == 3)
        put("main", 20, 3, 0, 30); put("gmail", 20, 3, 0, 30)
        rows = {r["account"]: r for r in compute_cap()["accounts"]}
        check("burn: compute_cap uses the account's own rate and says so in its row",
              rows["main"]["burn_from"]["five_hour"] == "account" and abs(rows["main"]["burn"]["five_hour"] - 4.0) < 1e-6
              and rows["gmail"]["burn_from"]["five_hour"] == "pooled" and rows["main"]["runs"] > rows["gmail"]["runs"])
        os.environ["ASD_BURN_MIN_SAMPLES"] = "4"
        check("burn: ASD_BURN_MIN_SAMPLES raises the bar", burn_for("main")[1]["five_hour"] == "pooled")
        os.environ.pop("ASD_BURN_MIN_SAMPLES")

        # account at slot grant: a run with nothing to resume goes to the account with the most paced headroom
        fresh()
        put("main", 92, 3, 61, 30); put("gmail", 8, 3, 1, 30)
        for i in range(3):   # three runs already working on main (this process stands for their live runners)
            write_json(os.path.join(pruns, f"busy{i}.runner.json"), {"pid": os.getpid(), "state": "running", "account": "main"})
        ha = Runner(pdir, "W-H1", "work-on", None, "auto", 1)
        ha.state["state"] = "queued"
        check("headroom: a fresh run starts on the first account (the setup)", ha.account["name"] == "main")
        ha.rehome_account()
        st_h = read_json(ha.state_path, {})
        check("headroom: a fresh run moves to the account with the most headroom (gmail), saved and logged",
              ha.account["name"] == "gmail" and st_h.get("account") == "gmail"
              and any("slot free on account gmail (most headroom" in str(x.get("detail") or x.get("text") or x) for x in lines_of(ha.updates)))
        hb = Runner(pdir, "W-H2", "work-on", None, "auto", 1)
        hb.state["state"] = "queued"
        hb.resume_sid, hb.resume_acct = "sess-x", "main"
        hb.rehome_account()
        check("headroom: a run with a session to resume stays on its account", hb.account["name"] == "main" and hb.resume_sid == "sess-x")
        mark_limited("gmail", T + 3600)
        os.remove(os.path.join(pruns, "slots-cap.json"))
        hc = Runner(pdir, "W-H3", "work-on", None, "auto", 1)
        hc.state["state"] = "queued"
        hc.rehome_account()
        check("headroom: a limited account is skipped (the fresh run stays on main)", hc.account["name"] == "main")
        check("headroom: headroom_pick keeps the current account on a tie", headroom_pick([{"account": "a", "runs": 2.0}, {"account": "b", "runs": 2.0}], {}, "b") == ("b", ""))
        check("headroom: ... and subtracts the runs already working on an account",
              headroom_pick([{"account": "a", "runs": 4.0}, {"account": "b", "runs": 3.0}], {"a": 2}, "a")[0] == "b")
        check("headroom: ... keeps the current account when it has no reading, and leaves a limited one",
              headroom_pick([{"account": "a", "unknown": True, "runs": 2.0}, {"account": "b", "runs": 3.0}], {}, "a")[0] == "a"
              and headroom_pick([{"account": "a", "limited": True, "runs": 0}, {"account": "b", "runs": 0.5}], {}, "a")[0] == "b")
        # ---- a signed-out account: recognised, moved away from or waited out, asked about once ----
        check("logged out: 'Not logged in · Please run /login' matches", logged_out("Not logged in · Please run /login"))
        check("logged out: 'Invalid API key · Please run /login' matches", logged_out("Invalid API key · Please run /login"))
        check("logged out: it is not a network error or an auth outage",
              not network_error("Not logged in · Please run /login") and not auth_outage("Invalid API key · Please run /login"))
        check("logged out: Cloudflare, 503 and usage-limit messages do not match",
              not any(logged_out(x) for x in ("403 Attention Required! | Cloudflare", "API Error: 503 service unavailable", "Failed to authenticate",
                                              "You've hit your usage limit", "Claude AI usage limit reached|1700000000"))
              and auth_outage("Attention Required! | Cloudflare") and network_error("API Error: 503"))
        lo_dir = os.path.join(pdir, "lo"); os.makedirs(lo_dir, exist_ok=True)
        lo_cnt = os.path.join(lo_dir, "calls")
        lo_stub = os.path.join(lo_dir, "status.py")
        open(lo_stub, "w").write(
            "import os,sys\nd=os.environ['CLAUDE_CONFIG_DIR']\nopen(%r,'a').write(os.path.basename(d)+'\\n')\n"
            "print('{\"loggedIn\": %%s, \"authMethod\": \"x\"}' %% ('false' if os.path.exists(os.path.join(d,'out')) else 'true'))\n" % lo_cnt)
        for d in ("cm", "cg"):
            os.makedirs(os.path.join(pdir, d), exist_ok=True)
        flag = os.path.join(pdir, "cm", "out")
        open(flag, "w").close()
        os.environ["ASD_AUTH_STATUS_CMD"] = f"{shlex.quote(sys.executable)} {shlex.quote(lo_stub)}"
        os.environ["ASD_AUTH_TTL"] = "60"
        accs = {a["name"]: a for a in load_accounts()}
        fresh()
        check("logged out: auth status reads loggedIn false for the signed-out account only", auth_status(accs["main"]) is False and auth_status(accs["gmail"]) is True)
        n_calls = len(open(lo_cnt).read().split())
        auth_status(accs["main"]); auth_status(accs["main"])
        check("logged out: the result is cached per account (no new calls inside the TTL)", len(open(lo_cnt).read().split()) == n_calls)
        put("main", 20, 3, 0, 30); put("gmail", 20, 3, 0, 30)
        rows = {r["account"]: r for r in compute_cap()["accounts"]}
        check("logged out: compute_cap marks the account limited and signed out, with no runs",
              rows["main"].get("limited") and rows["main"].get("signedout") and rows["main"]["runs"] == 0 and not rows["gmail"].get("limited"))
        check("logged out: headroom_pick never picks a signed-out account and leaves it",
              headroom_pick([{"account": "main", "limited": True, "signedout": True, "runs": 0}, {"account": "gmail", "runs": 0.5}], {}, "main")[0] == "gmail"
              and headroom_pick([{"account": "main", "signedout": True, "runs": 9.0}, {"account": "gmail", "runs": 0.5}], {}, "gmail")[0] == "gmail")
        # a fresh-stage run moves to the signed-in account
        asked = []

        class FakeAsk:
            returncode = 0
            def communicate(self):
                return "Q-1\n", ""

        def fake_ask(runner):
            runner.ask_cmd = lambda *a: (asked.append(a), FakeAsk())[1]
        lf = Runner(pdir, "W-L1", "work-on", None, "auto", 1)
        fake_ask(lf)
        ok_f = lf.logged_out_stage(1, "Not logged in · Please run /login", 1)
        check("logged out: a run with nothing to resume moves to the signed-in account and retries",
              ok_f is True and lf.account["name"] == "gmail" and read_json(lf.state_path, {}).get("account") == "gmail" and not asked
              and any("switched to account gmail: account main is signed out" in str(x) for x in lines_of(lf.updates)))
        # a resume run waits, asks once for two runs, then resumes by itself
        os.environ["ASD_AUTH_POLL"] = "0.3"
        runs, threads, res = [], [], {}
        for nm in ("W-L2", "W-L3"):
            rr = Runner(pdir, nm, "work-on", None, "auto", 1)
            fake_ask(rr)
            rr.resume_sid, rr.resume_acct = "sess-" + nm, "main"
            runs.append(rr)
            th = threading.Thread(target=lambda r=rr: res.update({r.key: r.logged_out_stage(1, "Not logged in · Please run /login", 1)}))
            th.start(); threads.append(th)
        time.sleep(1.0)
        check("logged out: a run that must resume waits (state waiting, not failed) with the sign-in instruction",
              all(read_json(r.state_path, {}).get("state") == "waiting" for r in runs) and not res
              and all(any("account main is signed out; run `CLAUDE_CONFIG_DIR=" in str(x) and "resumes by itself" in str(x) for x in lines_of(r.updates)) for r in runs))
        check("logged out: two runs on one account ask the owner once", len(asked) == 1 and os.path.exists(auth_question_path("main")))
        os.remove(flag)
        for th in threads:
            th.join(10)
        check("logged out: the waiting runs resume once the account is signed in, on the same account",
              sorted(res) == sorted(r.key for r in runs) and all(res.values()) and all(r.account["name"] == "main" and r.resume_sid for r in runs)
              and all(any("account main is signed in again" in str(x) for x in lines_of(r.updates)) for r in runs))
        check("logged out: signing in clears the once-per-account marker and the cap row", not os.path.exists(auth_question_path("main"))
              and not {r["account"]: r for r in compute_cap()["accounts"]}["main"].get("signedout"))
        for k in ("ASD_AUTH_STATUS_CMD", "ASD_AUTH_TTL", "ASD_AUTH_POLL"):
            os.environ.pop(k, None)
        for i in range(3):
            os.remove(os.path.join(pruns, f"busy{i}.runner.json"))
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
        check("keepalive: the skipped ping is logged once, with the run and the reason", len([t for t in texts("W-245") if t.startswith("keepalive skipped a ping for") and "paused for usage" in t]) == 1)
        check("keepalive: the run still finishes", ka_end(p, "W-245", qk))
        write_json(env["ASD_ACCOUNT_STATE"], {})
        os.makedirs(env["ASD_USAGE_DIR"], exist_ok=True)
        write_json(os.path.join(env["ASD_USAGE_DIR"], "default.json"), {"ts": now(), "status": "allowed", "windows": {
            "five_hour": {"used_percentage": 85, "resets_at": int(time.time() + 7200), "status": "allowed"}}})
        p, qk = ka_run("W-246", [[9000, 100]], ASD_KEEPALIVE_AFTER=0.3, ASD_KEEPALIVE_POLL=0.1)
        time.sleep(2.5)
        check("keepalive: no ping while the account's five-hour window is at 80% or more", not pinglines("W-246"))
        check("keepalive: the five-hour skip is logged with its reason", any(t.startswith("keepalive skipped a ping for") and "five-hour window at 85%" in t for t in texts("W-246")))
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
        scenario([{"stall_progress": True}, {"turns": [[{"final": "RUNNER: DONE"}]]}])
        p = start("W-323")
        rc = p.wait(timeout=90)
        t323 = texts("W-323")
        check("stall: a Bash call hung past the limit is a stall even while progress lines keep coming",
              rc == 0 and any(t.startswith("stalled: Bash call `git pull` hung") for t in t323)
              and any("stage 1 again from its checkpoints (stall 1 of 2)" in t for t in t323))
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

        # ---- one runner per key: the per-key lock ----
        scenario([{"turns": [[{"final": "RUNNER: DONE"}]]}] * 2)
        sleeper = subprocess.Popen(["sleep", "120"])
        held = try_lock("repo.W-330")
        check("lock: the first taker gets it, a second is refused, and a probe sees it held", held is not None and try_lock("repo.W-330") is None and lock_held("repo.W-330"))
        # a record whose pid is dead (revive would restart it) while a live runner holds the key's lock
        write_json(os.path.join(rdir, "repo.W-330.runner.json"), {"pid": 2 ** 22 + 12399, "state": "running", "root": root, "command": "work-on", "stage": 1, "claude_pid": sleeper.pid})
        r = subprocess.run([sys.executable, me, "revive"], env=env, capture_output=True, text=True, timeout=90)
        check("lock: revive starts no second runner for a key whose lock is held, and leaves the holder's claude child alone",
              "repo.W-330" not in r.stdout and alive(sleeper.pid) and state_of("W-330").get("pid") == 2 ** 22 + 12399)
        r = subprocess.run([sys.executable, me, "--root", root, "--dossier", "W-330", "--foreground"], env=env, capture_output=True, text=True, timeout=60)
        check("lock: a second runner for the key logs one line and exits 0, the record untouched",
              r.returncode == 0 and "already has a runner" in r.stdout and len(r.stdout.strip().splitlines()) == 1 and state_of("W-330").get("pid") == 2 ** 22 + 12399)
        # the Resume of a failed run (its runner alive, waiting on the failure question) is the same refusal
        write_json(os.path.join(rdir, "repo.W-330.runner.json"), {"pid": os.getpid(), "state": "failed", "root": root, "command": "work-on", "stage": 1})
        r = subprocess.run([sys.executable, me, "--root", root, "--dossier", "W-330", "--foreground"], env=env, capture_output=True, text=True, timeout=60)
        check("lock: Resume on a failed run whose runner still waits starts no second runner", r.returncode == 0 and "already has a runner" in r.stdout and state_of("W-330").get("state") == "failed")
        held.close()
        check("lock: released when its holder ends (a dead runner never blocks the key)", not lock_held("repo.W-330"))
        os.remove(os.path.join(rdir, "repo.W-330.runner.json"))
        sleeper.kill(); sleeper.wait()

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


def cmd_active(argv: list[str]) -> int:
    """`active`: show ~/.agentic/active-repos.json (ASD_ACTIVE_REPOS_FILE); `--add [NAME] [--root R]`, `--remove NAME`."""
    ap = argparse.ArgumentParser(prog="run_dossier.py active", description="The active repos: one reserved run slot each, and the only repos the dashboard shows.")
    ap.add_argument("--add", nargs="?", const="", metavar="NAME", help="add a repo (NAME defaults to the repository of --root or the current directory)")
    ap.add_argument("--root", help="with --add: the repository's main checkout (default: the current directory's repository)")
    ap.add_argument("--remove", metavar="NAME")
    a = ap.parse_args(argv)
    path, cur = active_repos_file(), read_active_repos()
    if a.add is None and a.remove is None:
        if cur is None:
            print(f"{path}: missing; no repo is reserved and the dashboard shows every repo")
            return 0
        res = reserved_repos()
        print(f"{path}:")
        for r, root in sorted(cur.items()):
            print(f"  {r}  {root}  {'reserved (has a dossier not done)' if r in res else 'no reservation (nothing buildable)'}")
        return 0
    repos = dict(cur or {})
    if a.add is not None:
        root = os.path.abspath(a.root) if a.root else repo_of(os.getcwd())
        if not root:
            print("run_dossier.py active: not inside a git repository; pass --root", file=sys.stderr)
            return 2
        if os.path.exists(os.path.join(root, ".git")):
            name = a.add or main_repo_name(root)
        else:
            name = a.add or os.path.basename(root.rstrip("/"))
        repos[name] = root
    if a.remove is not None:
        if a.remove not in repos:
            print(f"run_dossier.py active: {a.remove} is not in {path}", file=sys.stderr)
            return 1
        del repos[a.remove]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    write_json(path, {"repos": repos})
    print(f"{path}: {', '.join(sorted(repos)) or 'no repos'}")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "active":
        return cmd_active(argv[1:])
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
    ap.add_argument("--plan-branch", default="", help="work-on: build stacked on this unmerged plan branch (committed mode, after a re-plan): the plan commits are cherry-picked onto the build branch")
    ap.add_argument("--seed", default="", metavar="FILE", help="plan: a free-form /plan from this description file; the id is a slug "
                    "seed-<words> (no dossier exists yet). The file is copied beside the run's records, so restarts find it")
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
    if SEED_RE.fullmatch(a.dossier) and (a.command != "plan" or items):
        print("run_dossier.py: a seed-<slug> id is only a free-form /plan (--command plan --seed FILE, no --items)", file=sys.stderr)
        return 2
    if a.seed:
        if a.command != "plan" or items or not SEED_RE.fullmatch(a.dossier):
            print("run_dossier.py: --seed takes --command plan and an id seed-<slug> (lowercase words and dashes), no --items", file=sys.stderr)
            return 2
        why = store_seed(a.root, a.dossier, a.seed)
        if why:
            print(f"run_dossier.py: {why}", file=sys.stderr)
            return 2
        i = argv.index("--seed") if "--seed" in argv else -1
        argv = argv[:i] + argv[i + 2:] if i >= 0 else [x for x in argv if not x.startswith("--seed=")]
    elif a.command == "plan" and SEED_RE.fullmatch(a.dossier) and not os.path.isfile(seed_path(a.root, a.dossier)):
        print(f"run_dossier.py: no seed stored for {a.dossier}; pass --seed FILE", file=sys.stderr)
        return 2
    if a.plan_branch and (a.command != "work-on" or not re.fullmatch(r"plan/[\w./-]+", a.plan_branch) or ".." in a.plan_branch):
        print("run_dossier.py: --plan-branch takes a plan/<KEY> branch, with --command work-on", file=sys.stderr)
        return 2
    why = unknown_dossier(a.root, a.dossier, a.command)
    if why:
        print(f"run_dossier.py: {why}", file=sys.stderr)
        return 2
    runner_model(a.model)  # refuses a forbidden model before anything starts
    if a.command in ("plan", "work-on") and not items and not SEED_RE.fullmatch(a.dossier):
        # A seed or plan-from-deferred run that already claimed this id is still writing the dossier (it reads `planned`): a
        # second /plan would duplicate it. Applies to every start path: the dashboard's button, auto_work, a hand start.
        why = planning_runs.claimed_why(main_repo_name(a.root), runs_dir(), a.dossier, key_of(a.root, a.dossier))
        if why:
            print(f"run_dossier.py: not starting /{a.command}: {why}", file=sys.stderr)
            return 2
    if a.command == "work-on":
        why = already_built(a.root, a.dossier)
        if why:
            print(f"run_dossier.py: not starting /work-on: {why}", file=sys.stderr)
            return 2
    if not a.foreground:
        return detach(argv + ([] if "--root" in argv else ["--root", a.root]), a.root, a.dossier, run_key(a.root, a.dossier, a.command, items))
    return Runner(a.root, a.dossier, a.command, a.model, a.permission_mode, a.max_stages, items, a.plan_branch).run()


if __name__ == "__main__":
    sys.exit(main())
