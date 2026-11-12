#!/usr/bin/env python3
"""Visual review: the tool-quality counters log (VR-06, Q9 group A).

One JSON object per line, appended to `<repo>/.discovery/visual-review/metrics.jsonl`
by vr_c2_drift.py and vr_map.py. Adoption counters (Q9 group B) are written by the
dashboard, not here. A write failure never fails the tool: it prints one warning.

Line shape: {"ts": ISO-8601 UTC, "tool": "vr_map", "event": "check", "seconds": 0.4, ...numbers}

    vr_metrics.py --selftest
Importable: `log(repo, tool, event, fields, path=None)`, `default_path(repo)`, `read(path)`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

REL_PATH = os.path.join(".discovery", "visual-review", "metrics.jsonl")


def default_path(repo: str) -> str:
    return os.path.join(repo, REL_PATH)


def log(repo: str, tool: str, event: str, fields: dict, path: str | None = None) -> str | None:
    """Append one line. `path` overrides the default location. Returns the path written, or None on failure."""
    path = path or default_path(repo)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tool": tool, "event": event}
    rec.update(fields)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    except OSError as exc:
        print("vr_metrics: not written (%s)" % exc, file=sys.stderr)
        return None
    return path


def read(path: str) -> list:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


def _selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        p = log(d, "vr_map", "check", {"stale": 2, "seconds": 0.1})
        assert p == os.path.join(d, ".discovery", "visual-review", "metrics.jsonl"), p
        log(d, "vr_map", "recall", {"recall": 0.5})
        assert p is not None
        rows = read(p)
        assert [r["event"] for r in rows] == ["check", "recall"] and rows[0]["stale"] == 2 and "ts" in rows[0]
        assert log(d, "t", "e", {}, path=os.path.join(d, "o", "m.jsonl")) is not None
        blocker = os.path.join(d, "file")
        open(blocker, "w").close()
        assert log(d, "t", "e", {}, path=os.path.join(blocker, "x", "m.jsonl")) is None  # warns, no raise
    print("vr_metrics selftest ok")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    ap.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
