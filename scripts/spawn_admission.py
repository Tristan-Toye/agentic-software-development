#!/usr/bin/env python3
"""Admission control for sub-agent waves: a machine-wide semaphore and a quota ledger.

An eight-agent wave was spawned in one message. Four agents streamed and
finished; four died at their FIRST API call with `Rate limit reached`, and
the run lost the wave plus seventy minutes waiting out a window — while three
sibling `/work-on` sessions on the same machine drew on the same account, and
five days earlier the same provider had answered `Limit Exhausted. Your limit
will reset at 2026-09-17 17:38:07`, a deadline the flow carried only as prose.

Nothing in the flow measured the batch BEFORE spawning, and nothing shared
what one session learned with the next. This script is that measurement and
that memory, outside every process:

    $ASD_ADMISSION_DIR, or $XDG_STATE_HOME/agentic-software-development/admission
    (default ~/.local/state/...), one directory per <provider>__<model>:
        slots/<n>.json   a held slot: holder, pid, acquired_at
        ledger.json      cap, learned ceiling, recorded errors with their
                         reset deadlines, the waves in flight
        .lock            flock — acquire and release are atomic

Every repo, plugin instance and session on the machine shares it. Point
`ASD_ADMISSION_DIR` at a synced directory to share it across machines; a
locally enforced cap can never see another machine, so the provider's own
error stays the account-global signal, and `record-error` is how it reaches
everyone.

Commands — exit 0 admitted, 1 refused or deferred (split the wave or wait,
per work-on.md Phase 4), 2 unusable:

    acquire      --provider P --model M --n N --holder H [--pid PID]
                 [--cap C] [--stale-after SECONDS]
        Refuses while a recorded reset deadline lies ahead, naming it.
        Refuses when held + N exceeds the cap, naming the free slots.
        Otherwise holds N slots. A slot whose pid is dead, or older than
        --stale-after (default 4h), is reclaimed first — a crashed holder
        never blocks the machine.
    release      --provider P --model M (--holder H | --slots 3,4)
        Frees the slots. A wave released with no error recorded since it
        was admitted raises the learned ceiling to its peak concurrency.
    record-error --provider P --model M "<the provider's error, verbatim>"
                 [--cooldown SECONDS]
        Records the error, parses a `reset at <timestamp>` or `reset in
        <n> minutes` deadline (default: now + --cooldown, 60s), and lowers
        the cap to one below what was held when the error arrived.
    status       --provider P --model M
    --selftest

The cap in force is the first of: --cap, the cap an error set, the learned
ceiling WHEN IT EXCEEDS DEFAULT_CAP, DEFAULT_CAP. The ceiling only ever comes
from evidence — a wave that completed — so the next run starts from what the
machine has seen, never from a guess. That evidence reads one way only: a
completed wave proves the machine can go AT LEAST that wide, never that it
cannot go wider, so a learned ceiling below DEFAULT_CAP is floored at
DEFAULT_CAP. Without the floor, a run whose first spawn is a single
pre-fan-out agent teaches `ceiling = 1`, every later `acquire --n 4` is
refused, and nothing can ever raise the ceiling again because no wave larger
than one is admitted — a machine pinned at one slot with no error recorded.
Only a recorded provider error lowers the cap. Both cap and ceiling are
printed on every acquire, for the ADMISSION: line.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows has no flock
    fcntl = None  # type: ignore[assignment]

DEFAULT_CAP = 4
DEFAULT_STALE_AFTER = 4 * 3600
DEFAULT_COOLDOWN = 60
MAX_ERRORS_KEPT = 50

RESET_AT_RE = re.compile(
    r"reset(?:s)?\s+(?:at|on)\s+(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?"
    r"(?:\.\d+)?(?:\s?(?:Z|UTC|[+-]\d{2}:?\d{2}))?)",
    re.IGNORECASE,
)
RESET_IN_RE = re.compile(
    r"reset(?:s)?\s+in\s+(?P<n>\d+)\s*(?P<unit>s|secs?|seconds?|m|mins?|minutes?|h|hours?)\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# where the state lives
# --------------------------------------------------------------------------


def state_dir() -> Path:
    explicit = os.environ.get("ASD_ADMISSION_DIR")
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base).expanduser() / "agentic-software-development" / "admission"


def _safe(part: str) -> str:
    return re.sub(r"[^\w.-]+", "_", part.strip()) or "_"


def key_dir(provider: str, model: str) -> Path:
    return state_dir() / f"{_safe(provider)}__{_safe(model)}"


class Locked:
    """Exclusive access to one provider+model directory."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.handle: io.TextIOWrapper | None = None

    def __enter__(self) -> "Locked":
        (self.directory / "slots").mkdir(parents=True, exist_ok=True)
        self.handle = open(self.directory / ".lock", "a+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(self.handle, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        assert self.handle is not None
        if fcntl is not None:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


def empty_ledger() -> dict:
    return {"cap": None, "ceiling": 0, "errors": [], "waves": {}, "last_error_at": None}


def load_ledger(directory: Path) -> dict:
    try:
        data = json.loads((directory / "ledger.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_ledger()
    ledger = empty_ledger()
    ledger.update(data if isinstance(data, dict) else {})
    return ledger


def save_ledger(directory: Path, ledger: dict) -> None:
    tmp = directory / "ledger.json.tmp"
    tmp.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(directory / "ledger.json")


def held_slots(directory: Path) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for path in sorted((directory / "slots").glob("*.json")):
        try:
            out[int(path.stem)] = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
    return out


def pid_alive(pid: int | None) -> bool | None:
    """True/False when it can be told; None when no pid was recorded."""
    if not pid:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def reclaim(directory: Path, stale_after: float, now: float) -> list[str]:
    """Free every slot whose holder is provably gone or too old to be live."""
    notes: list[str] = []
    for number, slot in held_slots(directory).items():
        age = now - float(slot.get("acquired_at") or 0)
        alive = pid_alive(slot.get("pid"))
        reason = None
        if alive is False:
            reason = f"pid {slot.get('pid')} is gone"
        elif age > stale_after:
            reason = f"held for {int(age // 60)} min, past --stale-after"
        if reason:
            (directory / "slots" / f"{number}.json").unlink(missing_ok=True)
            notes.append(f"reclaimed slot {number} (holder {slot.get('holder')!r}: {reason})")
    return notes


# --------------------------------------------------------------------------
# deadlines
# --------------------------------------------------------------------------


def parse_reset(text: str, recorded_at: float) -> float | None:
    """The reset deadline a provider error names, as an epoch, or None."""
    match = RESET_AT_RE.search(text)
    if match:
        raw = match.group("ts").strip().replace(" UTC", "+00:00").replace("UTC", "+00:00")
        raw = raw.replace("Z", "+00:00")
        if " " in raw and "T" not in raw:
            raw = raw.replace(" ", "T", 1)
        raw = raw.replace(" ", "")
        try:
            when = dt.datetime.fromisoformat(raw)
        except ValueError:
            when = None
        if when is not None:
            return when.timestamp()  # naive -> local time, aware -> its offset
    match = RESET_IN_RE.search(text)
    if match:
        n = int(match.group("n"))
        unit = match.group("unit").lower()
        factor = 3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1
        return recorded_at + n * factor
    return None


def pending_deferral(provider: str, model: str, now: float | None = None) -> tuple[float, str] | None:
    """(deadline, error text) of the latest recorded reset still ahead, or None.

    Read-only and lock-free, so validate_pipeline.py --pre-fanout can ask.
    """
    now = time.time() if now is None else now
    ledger = load_ledger(key_dir(provider, model))
    best: tuple[float, str] | None = None
    for err in ledger.get("errors", []):
        reset_at = err.get("reset_at")
        if isinstance(reset_at, (int, float)) and reset_at > now:
            if best is None or reset_at > best[0]:
                best = (float(reset_at), str(err.get("text", "")))
    return best


def _fmt(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def effective_cap(ledger: dict, explicit: int | None) -> tuple[int, str]:
    if explicit:
        return explicit, "--cap"
    if ledger.get("cap"):
        return int(ledger["cap"]), "set by a recorded error"
    # A learned ceiling is evidence the machine can go at least this wide,
    # never evidence it cannot go wider: below DEFAULT_CAP it floors nothing.
    ceiling = int(ledger.get("ceiling") or 0)
    if ceiling > DEFAULT_CAP:
        return ceiling, "the learned ceiling"
    return DEFAULT_CAP, "the default"


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_acquire(args: argparse.Namespace) -> int:
    if args.n < 1:
        print("UNUSABLE --n must be at least 1")
        return 2
    directory = key_dir(args.provider, args.model)
    now = time.time()
    with Locked(directory):
        ledger = load_ledger(directory)
        for note in reclaim(directory, args.stale_after, now):
            print(note)
        deferred = pending_deferral(args.provider, args.model, now)
        if deferred:
            deadline, text = deferred
            minutes = max(0, int((deadline - now) // 60))
            print(
                f"deferred: {args.provider}/{args.model} resets at {_fmt(deadline)} "
                f"(in {minutes} min) — recorded from {text[:90]!r}. Wait it out; "
                "never spawn into a closed window."
            )
            return 1
        held = held_slots(directory)
        cap, cap_source = effective_cap(ledger, args.cap)
        free = cap - len(held)
        if args.n > free:
            holders = sorted({str(s.get("holder")) for s in held.values()})
            print(
                f"refused: {max(free, 0)} free of cap {cap} ({cap_source}), asked {args.n}; "
                f"held by {', '.join(holders) or 'nobody'} — split the wave to fit or "
                "serialise (work-on.md Phase 4 ladder)"
            )
            return 1
        granted: list[int] = []
        number = 1
        while len(granted) < args.n:
            if number not in held:
                (directory / "slots" / f"{number}.json").write_text(
                    json.dumps({
                        "holder": args.holder, "pid": args.pid, "acquired_at": now,
                        "provider": args.provider, "model": args.model,
                    }) + "\n",
                    encoding="utf-8",
                )
                granted.append(number)
            number += 1
        wave = ledger["waves"].get(args.holder) or {"acquired_at": now, "peak": 0, "slots": []}
        wave["peak"] = max(int(wave.get("peak") or 0), len(held) + args.n)
        wave["slots"] = sorted(set(wave.get("slots", [])) | set(granted))
        ledger["waves"][args.holder] = wave
        save_ledger(directory, ledger)
        ceiling = ledger.get("ceiling") or 0
        print(
            f"granted: {args.provider}/{args.model} slots {','.join(map(str, granted))} "
            f"(held {len(held) + args.n} of cap {cap}, {cap_source}; ceiling "
            f"{ceiling if ceiling else 'none learned yet'})"
        )
        return 0


def cmd_release(args: argparse.Namespace) -> int:
    directory = key_dir(args.provider, args.model)
    if not args.holder and not args.slots:
        print("UNUSABLE release needs --holder or --slots")
        return 2
    wanted = {int(x) for x in args.slots.split(",") if x.strip()} if args.slots else set()
    with Locked(directory):
        ledger = load_ledger(directory)
        held = held_slots(directory)
        targets = [
            n for n, s in held.items()
            if (args.holder and s.get("holder") == args.holder) or n in wanted
        ]
        for number in targets:
            (directory / "slots" / f"{number}.json").unlink(missing_ok=True)
        learned = ""
        if args.holder:
            wave = ledger["waves"].pop(args.holder, None)
            if wave and float(ledger.get("last_error_at") or 0) < float(wave.get("acquired_at") or 0):
                if int(wave.get("peak") or 0) > int(ledger.get("ceiling") or 0):
                    ledger["ceiling"] = int(wave["peak"])
                    learned = f"; ceiling learned: {ledger['ceiling']}"
        save_ledger(directory, ledger)
        if not targets:
            print(f"nothing held for {args.holder or wanted} under {args.provider}/{args.model}")
            return 1
        print(
            f"released: {len(targets)} slot(s) — {','.join(map(str, sorted(targets)))}; "
            f"ceiling {ledger.get('ceiling') or 'none learned yet'}{learned}"
        )
        return 0


def cmd_record_error(args: argparse.Namespace) -> int:
    directory = key_dir(args.provider, args.model)
    now = time.time()
    with Locked(directory):
        ledger = load_ledger(directory)
        held = len(held_slots(directory))
        reset_at = parse_reset(args.text, now)
        source = "named by the error" if reset_at is not None else f"--cooldown {args.cooldown}s"
        if reset_at is None:
            reset_at = now + args.cooldown
        ledger["errors"].append({
            "at": now, "text": args.text[:500], "reset_at": reset_at, "held": held,
        })
        ledger["errors"] = ledger["errors"][-MAX_ERRORS_KEPT:]
        ledger["last_error_at"] = now
        cap_note = "cap unchanged (nothing was held)"
        if held:
            ledger["cap"] = max(1, held - 1)
            cap_note = f"cap now {ledger['cap']} (one below the {held} held when it arrived)"
        save_ledger(directory, ledger)
        print(f"recorded: {args.provider}/{args.model} defers until {_fmt(reset_at)} ({source}); {cap_note}")
        return 0


def cmd_status(args: argparse.Namespace) -> int:
    directory = key_dir(args.provider, args.model)
    now = time.time()
    ledger = load_ledger(directory)
    held = held_slots(directory)
    cap, cap_source = effective_cap(ledger, None)
    print(f"{args.provider}/{args.model} — {directory}")
    print(f"  cap {cap} ({cap_source}); ceiling {ledger.get('ceiling') or 'none learned yet'}")
    print(f"  held {len(held)}:")
    for number, slot in held.items():
        age = int((now - float(slot.get("acquired_at") or now)) // 60)
        print(f"    slot {number}: {slot.get('holder')!r} pid {slot.get('pid')} for {age} min")
    deferred = pending_deferral(args.provider, args.model, now)
    if deferred:
        print(f"  deferral until {_fmt(deferred[0])} — {deferred[1][:90]!r}")
    else:
        print("  no deferral pending")
    print(f"  errors recorded: {len(ledger.get('errors', []))}")
    return 0


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        if not cond:
            failures.append(f"{name}: {detail.strip()}")

    saved = os.environ.get("ASD_ADMISSION_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ASD_ADMISSION_DIR"] = tmp
        pm = ["--provider", "zai-coding-plan", "--model", "glm-5.3-flash"]
        try:
            rc, out = _run(["acquire", *pm, "--n", "4", "--holder", "A"])
            check("full wave of 4 admitted at the default cap", rc == 0 and "granted" in out, out)
            rc, out = _run(["acquire", *pm, "--n", "1", "--holder", "B"])
            check("a fifth slot is refused, free count named", rc == 1 and "0 free of cap 4" in out, out)
            rc, out = _run(["release", *pm, "--holder", "A"])
            check("release frees and learns the ceiling", rc == 0 and "ceiling learned: 4" in out, out)

            # two sessions share the ledger
            rc, _ = _run(["acquire", *pm, "--n", "2", "--holder", "A"])
            rc2, out2 = _run(["acquire", *pm, "--n", "2", "--holder", "B"])
            rc3, out3 = _run(["acquire", *pm, "--n", "1", "--holder", "B"])
            check("session B sees session A's two slots", rc == 0 and rc2 == 0 and rc3 == 1 and "held by A, B" in out3, out3)
            rc, out = _run(["status", *pm])
            check("status lists both holders", "'A'" in out and "'B'" in out, out)
            _run(["release", *pm, "--holder", "A"])
            _run(["release", *pm, "--holder", "B"])

            # a crashed holder's slot is reclaimed
            rc, _ = _run(["acquire", *pm, "--n", "2", "--holder", "S", "--pid", "999999"])
            rc2, out2 = _run(["acquire", *pm, "--n", "4", "--holder", "T"])
            check("dead-pid slots are reclaimed before the count", rc == 0 and rc2 == 0 and "reclaimed slot" in out2, out2)
            _run(["release", *pm, "--holder", "T"])

            # a quota error naming its reset defers every later acquire
            future = _fmt(time.time() + 3600)
            rc, out = _run(["record-error", *pm, f"Weekly/Monthly Limit Exhausted. Your limit will reset at {future}"])
            check("reset deadline parsed from the error", rc == 0 and future[:16] in out, out)
            rc, out = _run(["acquire", *pm, "--n", "1", "--holder", "U"])
            check("acquire defers until the named reset", rc == 1 and "deferred" in out and future[:16] in out, out)
            rc, _ = _run(["acquire", *pm, "--n", "1", "--holder", "U", "--cap", "9"])
            check("an explicit cap does not override a deferral", rc == 1)

            # a past reset does not defer; a bare rate limit gets the cool-down
            pm2 = ["--provider", "zai-coding-plan", "--model", "glm-5.3"]
            past = _fmt(time.time() - 3600)
            _run(["record-error", *pm2, f"Limit Exhausted. Your limit will reset at {past}"])
            rc, out = _run(["acquire", *pm2, "--n", "1", "--holder", "V"])
            check("a past reset does not defer", rc == 0, out)
            _run(["release", *pm2, "--holder", "V"])
            rc, out = _run(["record-error", *pm2, "AI_APICallError: Rate limit reached for requests", "--cooldown", "120"])
            rc2, out2 = _run(["acquire", *pm2, "--n", "1", "--holder", "V"])
            check("a rate limit without a timestamp defers for the cool-down", rc == 0 and rc2 == 1 and "deferred" in out2, out2)
            pm3 = ["--provider", "other", "--model", "m"]
            _run(["record-error", *pm3, "Rate limit reached", "--cooldown", "0"])
            rc, _ = _run(["acquire", *pm3, "--n", "1", "--holder", "W"])
            check("a zero cool-down does not defer", rc == 0)

            # an error while slots are held lowers the cap to one below
            pm4 = ["--provider", "p4", "--model", "m4"]
            _run(["acquire", *pm4, "--n", "4", "--holder", "X"])
            rc, out = _run(["record-error", *pm4, "Rate limit reached", "--cooldown", "0"])
            check("cap lowered to held minus one", rc == 0 and "cap now 3" in out, out)
            rc, out = _run(["release", *pm4, "--holder", "X"])
            check("a wave with an error does not raise the ceiling", rc == 0 and "ceiling learned:" not in out, out)
            rc, out = _run(["acquire", *pm4, "--n", "4", "--holder", "Y"])
            check("the lowered cap refuses the old wave size", rc == 1 and "cap 3" in out, out)
            rc, out = _run(["acquire", *pm4, "--n", "3", "--holder", "Y"])
            check("a wave that fits the lowered cap is admitted", rc == 0, out)

            # a one-slot wave on a fresh ledger never pins the machine at one:
            # the learned ceiling floors nothing below DEFAULT_CAP
            pm5 = ["--provider", "p5", "--model", "m5"]
            rc, out = _run(["acquire", *pm5, "--n", "1", "--holder", "C"])
            check("single pre-fan-out slot admitted on a fresh ledger", rc == 0, out)
            rc, out = _run(["release", *pm5, "--holder", "C"])
            check("releasing the single slot learns ceiling 1", rc == 0 and "ceiling learned: 1" in out, out)
            rc, out = _run(["acquire", *pm5, "--n", str(DEFAULT_CAP), "--holder", "D"])
            check(
                "a full DEFAULT_CAP wave is still admitted after a one-slot ceiling",
                rc == 0 and f"cap {DEFAULT_CAP}" in out and "the default" in out,
                out,
            )
            _run(["release", *pm5, "--holder", "D"])
            # a ceiling above DEFAULT_CAP still widens the cap on evidence
            rc, _ = _run(["acquire", *pm5, "--n", "6", "--holder", "E", "--cap", "6"])
            rc2, out2 = _run(["release", *pm5, "--holder", "E"])
            check("a six-slot wave learns ceiling 6", rc == 0 and rc2 == 0 and "ceiling learned: 6" in out2, out2)
            rc, out = _run(["acquire", *pm5, "--n", "5", "--holder", "F"])
            check("a ceiling above the default is the cap in force", rc == 0 and "cap 6, the learned ceiling" in out, out)
            _run(["release", *pm5, "--holder", "F"])

            # the module API validate_pipeline.py --pre-fanout uses
            pend = pending_deferral("zai-coding-plan", "glm-5.3-flash")
            check("pending_deferral reports the live deadline", pend is not None and pend[0] > time.time())
            check("pending_deferral is None with nothing recorded", pending_deferral("nobody", "none") is None)

            # reset-in parsing
            check("reset in minutes parses", parse_reset("quota resets in 15 minutes", 1000.0) == 1000.0 + 900)
            check("no deadline -> None", parse_reset("Rate limit reached", 1000.0) is None)
        finally:
            if saved is None:
                os.environ.pop("ASD_ADMISSION_DIR", None)
            else:
                os.environ["ASD_ADMISSION_DIR"] = saved

    for item in failures:
        print(f"SELFTEST FAIL  {item}")
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    sub = ap.add_subparsers(dest="command")

    def common(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--provider", required=True)
        parser.add_argument("--model", required=True)

    acquire = sub.add_parser("acquire", help="hold N slots, or be refused/deferred")
    common(acquire)
    acquire.add_argument("--n", type=int, required=True, help="spawns in the wave")
    acquire.add_argument("--holder", required=True, help="who holds them, e.g. W-014-wave1")
    acquire.add_argument("--pid", type=int, default=None, help="the holder's process, when one outlives this call")
    acquire.add_argument("--cap", type=int, default=None, help="override the cap for this acquire")
    acquire.add_argument("--stale-after", type=float, default=DEFAULT_STALE_AFTER,
                         help=f"seconds after which a held slot is reclaimed (default {DEFAULT_STALE_AFTER})")
    acquire.set_defaults(func=cmd_acquire)

    release = sub.add_parser("release", help="free the slots of a holder, or named slots")
    common(release)
    release.add_argument("--holder", default=None)
    release.add_argument("--slots", default=None, help="comma-separated slot numbers")
    release.set_defaults(func=cmd_release)

    record = sub.add_parser("record-error", help="record a provider limit error and its reset")
    common(record)
    record.add_argument("text", help="the provider's error, verbatim")
    record.add_argument("--cooldown", type=float, default=DEFAULT_COOLDOWN,
                        help=f"deferral when the error names no reset (default {DEFAULT_COOLDOWN}s)")
    record.set_defaults(func=cmd_record_error)

    status = sub.add_parser("status", help="what is held, the cap, the ceiling, any deferral")
    common(status)
    status.set_defaults(func=cmd_status)

    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.command:
        ap.print_help()
        return 2
    try:
        return int(args.func(args))
    except OSError as exc:
        print(f"UNUSABLE {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
