#!/usr/bin/env python3
"""Host-wide build slots: at most N gate or host test commands build at once, across every run on this machine.

Each /work-on run builds in its own worktree with its own target directory, and a run in `testing` has
released its token slot, so nothing else bounds how many builds start together. With fifteen runs testing
at once the host ran fifteen cargo builds (48 rustc), load 83 on 12 cores and swap 20 of 21.5 GB: every
build slowed to a crawl and the machine risked killing processes. A slot is one lock file under
`$ASD_BUILD_SLOT_DIR` (default `~/.agentic/locks/build-slots`); `slot()` waits until one of the N files can
be locked (`fcntl.flock`, released by the kernel when the process dies, so a killed run never leaks a
slot) and holds it for the command.

N is `$ASD_BUILD_SLOTS` when set (0 turns the limit off), else a quarter of the CPUs, at least 1.

    build_slots.py --status      which slots are held, by which process
    build_slots.py --selftest
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import sys
import time

POLL = 2.0
HELD = "ASD_BUILD_SLOT_HELD"  # set in the environment of every command run under a slot


def held_env(env: dict | None = None) -> dict:
    """`env` (default this process's) with HELD set, for a child started under a slot."""
    return dict(os.environ if env is None else env, **{HELD: "1"})


def slot_count() -> int:
    raw = os.environ.get("ASD_BUILD_SLOTS", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return max(1, (os.cpu_count() or 4) // 4)


def slot_dir() -> str:
    return os.environ.get("ASD_BUILD_SLOT_DIR") or os.path.expanduser("~/.agentic/locks/build-slots")


def _try(path: str, label: str):
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, ("%d %d %s\n" % (os.getpid(), int(time.time()), label)).encode())
    return fd


@contextlib.contextmanager
def slot(label: str = "", log=None, count: int | None = None):
    """Hold one host build slot for the block. `log(msg)` is told once when the block has to wait.
    Yields the seconds spent waiting. With a count of 0 it yields 0.0 at once."""
    n = slot_count() if count is None else count
    if n <= 0 or os.environ.get(HELD):
        # A command started under a held slot (a gate that calls run_tests.py) runs in its parent's slot:
        # waiting for a second one could deadlock N such parents against each other.
        yield 0.0
        return
    d = slot_dir()
    os.makedirs(d, exist_ok=True)
    start = time.monotonic()
    told = False
    fd = None
    while fd is None:
        for i in range(n):
            fd = _try(os.path.join(d, "slot-%d" % i), label)
            if fd is not None:
                break
        if fd is None:
            if not told and log:
                log("waiting for a host build slot (%d in use; ASD_BUILD_SLOTS)" % n)
                told = True
            time.sleep(POLL)
    try:
        yield time.monotonic() - start
    finally:
        with contextlib.suppress(OSError):
            os.ftruncate(fd, 0)
        os.close(fd)


def status() -> int:
    n, d = slot_count(), slot_dir()
    print("%d slot(s) in %s" % (n, d))
    for i in range(n):
        p = os.path.join(d, "slot-%d" % i)
        fd = _try(p, "status probe") if os.path.exists(p) else None
        if fd is not None or not os.path.exists(p):
            if fd is not None:
                os.ftruncate(fd, 0)
                os.close(fd)
            print("  slot-%d free" % i)
            continue
        with open(p, encoding="utf-8", errors="replace") as fh:
            print("  slot-%d held: %s" % (i, fh.read().strip() or "?"))
    return 0


def selftest() -> int:
    import subprocess
    import tempfile
    import threading

    fails = []

    def ok(name, cond, detail: object = ""):
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %r" % (detail,)))
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as td:
        os.environ["ASD_BUILD_SLOT_DIR"] = td
        os.environ["ASD_BUILD_SLOTS"] = "2"
        ok("count from env", slot_count() == 2)
        os.environ["ASD_BUILD_SLOTS"] = "junk"
        ok("bad env falls back to cpus/4", slot_count() == max(1, (os.cpu_count() or 4) // 4))
        os.environ["ASD_BUILD_SLOTS"] = "0"
        with slot("off") as w:
            ok("count 0 is no limit", w == 0.0 and not os.listdir(td))

        # Two slots: two holders run together, a third waits until one leaves.
        held, order, lock = [], [], threading.Lock()
        release = threading.Event()

        def holder(tag):
            with slot(tag, count=2):
                with lock:
                    held.append(tag)
                    order.append(("in", tag))
                release.wait(10)
                with lock:
                    held.remove(tag)
                    order.append(("out", tag))

        msgs = []
        a = threading.Thread(target=holder, args=("a",))
        b = threading.Thread(target=holder, args=("b",))
        a.start(); b.start()
        for _ in range(100):
            if len(held) == 2:
                break
            time.sleep(0.02)
        ok("two holders at once", len(held) == 2, held)
        got = []

        def third():
            with slot("c", log=msgs.append, count=2) as w:
                got.append((w, list(held)))

        c = threading.Thread(target=third)
        c.start()
        time.sleep(0.3)
        ok("third waits while both are held", not got and len(msgs) == 1, (got, msgs))
        release.set()
        a.join(); b.join(); c.join(5)
        ok("third runs once a slot frees", got and got[0][0] > 0.2, got)
        os.environ[HELD] = "1"
        with slot("nested", count=1) as w1:
            with slot("nested2", count=1) as w2:
                ok("a child of a held slot never waits", w1 == 0.0 and w2 == 0.0)
        del os.environ[HELD]
        ok("held_env marks the child", held_env({"A": "1"}) == {"A": "1", HELD: "1"})

        # A dead holder never leaks its slot: the kernel drops the flock with the process.
        code = ("import sys; sys.path.insert(0, %r); import build_slots, os\n"
                "with build_slots.slot('child', count=1):\n    os._exit(0)\n" % os.path.dirname(os.path.abspath(__file__)))
        subprocess.run([sys.executable, "-c", code], check=False, timeout=30)
        t0 = time.monotonic()
        with slot("after", count=1) as w:
            ok("slot free after its holder died", w < POLL and time.monotonic() - t0 < POLL)
            with open(os.path.join(td, "slot-0"), encoding="utf-8") as fh:
                ok("holder written into the slot file", fh.read().split()[0] == str(os.getpid()))
    print("%d failure(s)" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--status" in sys.argv:
        sys.exit(status())
    print(__doc__)
    sys.exit(2)
