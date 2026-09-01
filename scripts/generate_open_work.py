#!/usr/bin/env python3
"""Generate the open-work HTML overview from dossier front matter.

Deterministic, and read-only toward pipeline state: reads the YAML front
matter of `.discovery/dossiers/*.md` through the one parser
(`validate_pipeline.split_front_matter`), computes the same overview sections
`/open-work` reports in chat, and writes one self-contained HTML file
(inline CSS and JS, no external assets). It never writes a dossier, an ADR,
or `state` — the only file it writes is the report.

Usage:
    generate_open_work.py [--root .discovery] [--out PATH]
                          [--signals PATH.json] [--open] [--quiet]
    generate_open_work.py --serve [PORT]      # live report, watches dossiers
    generate_open_work.py --watch [SECONDS]   # regenerate on change, no server
    generate_open_work.py --install | --uninstall   # always-on git/Claude hooks
    generate_open_work.py --hook              # PostToolUse entry point (stdin)
    generate_open_work.py --selftest

Default output: <root>/analysis/open-work.html
The template lives at references/templates/open-work.html, next to this
script's parent. `--signals` takes a JSON list the caller mined from the
Build logs (this script does not read dossier bodies); each item is
{"signal": str, "dossier": str, "detail": str, "severity":
"good|warning|serious|critical"} and is rendered verbatim.

Beside the HTML the script writes two small files. `<stem>-state.json` holds the
fingerprint of everything the report shows apart from when it was generated, and
is how a report already open in a browser notices a newer one exists: served
over http it polls that file and reloads only on a fingerprint change. The HTML
is always written before it, so a poller that sees a new fingerprint never races
the report it points at. `<stem>-signals.json` remembers the last `--signals`
set, so a refresh triggered by a hook keeps showing the signals `/open-work`
mined instead of dropping the section.

The fingerprint covers the template as well as the data, because a plugin update
that changes only the markup still changes the page. `--install` never writes the
plugin's versioned install directory into a hook for the same reason: it writes a
launcher that resolves the current install on every run, and re-running it
repoints a hook an older version pinned.

No third-party imports: this runs wherever python3 does. Imports needed only
by the server are local to it, because `--hook` runs once per file write in a
session and its startup cost is the thing to protect.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_pipeline import sections, split_front_matter  # noqa: E402

STATUSES = ["planned", "ready", "building", "review", "pr", "done", "dropped"]
IN_FLIGHT = {"building", "review"}
ABANDONED_AFTER_DAYS = 2   # `updated` is a date, so >24h is certain at 2 days

TEMPLATE = (Path(__file__).resolve().parent.parent
            / "references" / "templates" / "open-work.html")

STATE_SUFFIX = "-state.json"
SIGNALS_SUFFIX = "-signals.json"
WATCH_SECONDS = 2.0
SERVE_PORT = 8787
GIT_HOOKS = ["post-commit", "post-merge", "post-checkout", "post-rewrite"]
SENTINEL_OPEN = "# >>> open-work report (generate_open_work.py) >>>"
SENTINEL_CLOSE = "# <<< open-work report <<<"

# `CLAUDE_CONFIG_DIR` relocates Claude Code's whole config directory, the plugin
# cache and this manifest with it, so `~/.claude` is a default and not a given.
PLUGIN_MANIFEST = (Path(os.environ.get("CLAUDE_CONFIG_DIR")
                        or Path.home() / ".claude")
                   / "plugins" / "installed_plugins.json")
LAUNCHER_NAME = "open-work-report.py"
# Either name in a hook command means the hook is ours: the launcher is what
# --install writes now, the generator itself is what older versions wrote.
HOOK_NAMES = (LAUNCHER_NAME, "generate_open_work.py")


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if value in (None, "", []):
        return []
    return [str(value)]


def _parse_date(value: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _first_paragraph(text: str, limit: int = 420) -> str:
    """First prose paragraph of a section, whitespace-collapsed, truncated."""
    for para in re.split(r"\n\s*\n", text or ""):
        p = " ".join(para.split())
        if p and not p.startswith(("```", "|", "#")):
            return p if len(p) <= limit else p[:limit - 1].rstrip() + "…"
    return ""


def load_dossiers(root: Path) -> tuple[list[dict], list[dict]]:
    """Read front matter of every dossier — plus the first paragraph of
    `## Problem` and `## Approach`, the intent summary the report shows when
    a dossier is clicked. Returns (dossiers, parse_errors)."""
    dossiers, errors = [], []
    dossier_dir = root / "dossiers"
    for path in sorted(dossier_dir.glob("*.md")):
        try:
            fm, body, _line = split_front_matter(
                path.read_text(encoding="utf-8", errors="replace"))
        except ValueError as exc:
            errors.append({"file": path.name, "error": str(exc)})
            continue
        secs = sections(body)
        dossiers.append({
            "id": str(fm.get("id") or path.stem),
            "title": str(fm.get("title") or ""),
            "status": str(fm.get("status") or "planned"),
            "created": fm.get("created"),
            "updated": fm.get("updated"),
            "jira": fm.get("jira"),
            "branch": fm.get("branch"),
            "worktree": fm.get("worktree"),
            "pr": fm.get("pr"),
            "blocked_by": _as_list(fm.get("blocked_by")),
            "adrs": _as_list(fm.get("adrs")),
            "file": path.name,
            "problem": _first_paragraph(secs.get("## Problem", "")),
            "approach": _first_paragraph(secs.get("## Approach", "")),
        })
    return dossiers, errors


def classify(dossiers: list[dict], errors: list[dict], root: Path,
             today: dt.date) -> dict:
    """The overview /open-work defines, as one JSON payload for the template.

    Section semantics mirror commands/open-work.md exactly: ready means
    `status: ready` with every blocker done; blocked means `status: ready`
    with a blocker that is not done; stale worktrees are recorded paths that
    disagree with the disk.
    """
    by_id = {d["id"]: d for d in dossiers}
    sections: dict[str, list[str]] = {k: [] for k in (
        "ready", "in_flight", "waiting_pr", "blocked", "planned",
        "done", "dropped")}
    attention: list[dict] = []

    for err in errors:
        attention.append({
            "severity": "critical", "dossier": err["file"],
            "text": f"{err['file']} has unparseable front matter: "
                    f"{err['error']}",
        })

    for d in dossiers:
        upd = _parse_date(d["updated"])
        d["age_days"] = (today - upd).days if upd else None
        d["flags"] = []
        status = d["status"]

        blockers = [{"id": b, "status": (by_id.get(b) or {}).get(
            "status", "missing")} for b in d["blocked_by"]]
        open_blockers = [b for b in blockers if b["status"] != "done"]

        if status == "ready":
            (sections["blocked"] if open_blockers
             else sections["ready"]).append(d["id"])
            for b in open_blockers:
                sev = "serious" if b["status"] == "missing" else "warning"
                d["flags"].append({
                    "severity": sev, "label": f"blocked by {b['id']}",
                    "detail": f"{b['id']} is {b['status']}; it must be done "
                              f"before {d['id']} can build.",
                })
                attention.append({
                    "severity": sev, "dossier": d["id"],
                    "text": f"{d['id']} is blocked by {b['id']} "
                            f"({b['status']}).",
                })
        elif status in IN_FLIGHT:
            sections["in_flight"].append(d["id"])
            if d["age_days"] is not None and \
                    d["age_days"] >= ABANDONED_AFTER_DAYS:
                d["flags"].append({
                    "severity": "warning", "label": "possibly abandoned",
                    "detail": f"No update for {d['age_days']} days while "
                              f"{status}.",
                })
                attention.append({
                    "severity": "warning", "dossier": d["id"],
                    "text": f"{d['id']} is {status} but untouched for "
                            f"{d['age_days']} days — possibly abandoned.",
                })
        elif status == "pr":
            sections["waiting_pr"].append(d["id"])
            d["flags"].append({
                "severity": "good", "label": "resume: /work-on " + d["id"],
                "detail": "The branch is pushed. /work-on records the PR URL "
                          "and removes the worktree.",
            })
        elif status in sections:
            sections[status].append(d["id"])
        else:
            attention.append({
                "severity": "critical", "dossier": d["id"],
                "text": f"{d['id']} has unknown status \"{status}\".",
            })

        # Recorded worktree vs the disk.
        wt = d["worktree"]
        if wt:
            exists = (root.parent / str(wt)).exists() or Path(str(wt)).exists()
            if status in ("done", "dropped"):
                d["flags"].append({
                    "severity": "warning", "label": "stale worktree",
                    "detail": f"{wt} is recorded on a {status} dossier.",
                })
                attention.append({
                    "severity": "warning", "dossier": d["id"],
                    "text": f"{d['id']} is {status} but still records "
                            f"worktree {wt}.",
                })
            elif status == "building" and not exists:
                d["flags"].append({
                    "severity": "serious", "label": "worktree missing",
                    "detail": f"{wt} does not exist on disk.",
                })
                attention.append({
                    "severity": "serious", "dossier": d["id"],
                    "text": f"{d['id']} is building but its worktree {wt} "
                            f"does not exist.",
                })

    next_action = None
    if sections["ready"]:
        nid = sections["ready"][0]
        next_action = {"command": f"/work-on {nid}",
                       "reason": f"{nid} — {by_id[nid]['title']} is ready "
                                 f"to build."}
    elif sections["planned"]:
        nid = sections["planned"][0]
        next_action = {"command": f"/plan {nid}",
                       "reason": f"{nid} — {by_id[nid]['title']} has no "
                                 f"passed plan review yet."}
    elif sections["waiting_pr"]:
        nid = sections["waiting_pr"][0]
        next_action = {"command": f"/work-on {nid}",
                       "reason": f"{nid} waits for its PR URL; resuming "
                                 f"records it and removes the worktree."}

    by_status = {s: sum(1 for d in dossiers if d["status"] == s)
                 for s in STATUSES}
    return {
        "sections": sections,
        "attention": attention,
        "next_action": next_action,
        "counts": {"total": len(dossiers),
                   "by_status": {k: v for k, v in by_status.items() if v}},
    }


class Signals(NamedTuple):
    """Health signals mined from Build logs, and when they were mined.

    This script cannot produce them — only `/open-work` reads dossier bodies —
    so a refresh it did not trigger reuses the last mined set and says how old
    it is. Dropping the section instead would read as "no signals fired",
    which is a different and false claim.
    """
    items: tuple = ()
    mined_at: str | None = None


def as_signals(value: Signals | list[dict] | None) -> Signals:
    if isinstance(value, Signals):
        return value
    return Signals(tuple(value or ()))


def build_payload(root: Path, now: dt.datetime,
                  signals: Signals | list[dict] | None = None,
                  state_file: str | None = None) -> dict:
    dossiers, errors = load_dossiers(root)
    mined = as_signals(signals)
    payload = classify(dossiers, errors, root, now.date())
    payload.update({
        "generated_at": now.isoformat(timespec="seconds"),
        "generated_at_human": now.strftime("%-d %b %Y, %H:%M"),
        "root": str(root),
        "dossiers": dossiers,
        "signals": list(mined.items),
        "signals_mined_at_human": _human(mined.mined_at),
        "parse_errors": errors,
        "state_file": state_file,
        "template": template_digest(),
    })
    payload["fingerprint"] = fingerprint(payload)
    return payload


def template_digest(template: Path | None = None) -> str:
    """Short hash of the template this report would be rendered through.

    In the payload, so the fingerprint answers "would the page on disk look
    different" rather than only "did the pipeline change". Without it a plugin
    update that touches nothing but the template is invisible: `refresh` sees an
    unchanged fingerprint, declines to rewrite, and the old markup survives
    until a dossier happens to move. Re-read rather than cached, so editing the
    template under `--serve` reloads the page.
    """
    return hashlib.sha256((template or TEMPLATE).read_bytes()).hexdigest()[:16]


def _human(iso: str | None) -> str | None:
    try:
        return dt.datetime.fromisoformat(str(iso)).strftime("%-d %b %Y, %H:%M")
    except (TypeError, ValueError):
        return None


def fingerprint(payload: dict) -> str:
    """Hash of everything the report shows except the generation timestamp.

    The live report reloads on a fingerprint change, so an unchanged pipeline
    regenerated a second later has to hash the same, or every watch tick would
    look like news. The ages and staleness flags the page prints are computed
    from the date, so crossing midnight *does* change this — which is right, and
    at most once a day. `template` is in here too: a new template is a different
    page even when the pipeline behind it has not moved.
    """
    volatile = {"generated_at", "generated_at_human", "fingerprint"}
    stable = {k: v for k, v in payload.items() if k not in volatile}
    blob = json.dumps(stable, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def render(template_text: str, payload: dict) -> str:
    if "{{DATA_JSON}}" not in template_text:
        raise ValueError("template has no {{DATA_JSON}} placeholder")
    blob = json.dumps(payload, ensure_ascii=False, default=str)
    # `</` must not appear inside the inline <script> block.
    return template_text.replace("{{DATA_JSON}}", blob.replace("</", "<\\/"))


# --------------------------------------------------------------------------
# the report on disk: HTML plus the state file the live page polls
# --------------------------------------------------------------------------

def state_path(out: Path) -> Path:
    return out.with_name(out.stem + STATE_SUFFIX)


def signals_path(out: Path) -> Path:
    return out.with_name(out.stem + SIGNALS_SUFFIX)


def load_signals(out: Path, explicit: Path | None,
                 now: dt.datetime) -> Signals:
    """The signals this report should show.

    Given `--signals`, those are the freshly mined ones, and they are also
    remembered beside the report — otherwise the next refresh, which comes from
    a hook rather than from `/open-work`, would drop the section. Without
    `--signals`, the remembered set, carrying the date it was mined.
    """
    if explicit is not None:
        items = json.loads(explicit.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            raise ValueError("--signals must be a JSON list")
        mined = Signals(tuple(items), now.isoformat(timespec="seconds"))
        out.parent.mkdir(parents=True, exist_ok=True)
        signals_path(out).write_text(json.dumps(
            {"mined_at": mined.mined_at, "signals": items}, indent=2) + "\n",
            encoding="utf-8")
        return mined
    try:
        saved = json.loads(signals_path(out).read_text(encoding="utf-8"))
        return Signals(tuple(saved["signals"]), saved.get("mined_at"))
    except (OSError, ValueError, KeyError, TypeError):
        return Signals()


def build_report(root: Path, out: Path, signals: Signals,
                 now: dt.datetime) -> dict:
    """The report `out` would contain right now — rendered, not yet written."""
    payload = build_payload(root, now, signals, state_file=state_path(out).name)
    return {"fingerprint": payload["fingerprint"],
            "generated_at": payload["generated_at"],
            "html": render(TEMPLATE.read_text(encoding="utf-8"), payload)}


def write_report(out: Path, report: dict) -> None:
    """Write the HTML, then the state file — never the other way round. The
    state file is the live page's signal that a newer report exists, so it must
    not name a fingerprint the HTML on disk does not yet have."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report["html"], encoding="utf-8")
    state_path(out).write_text(json.dumps({
        "fingerprint": report["fingerprint"],
        "generated_at": report["generated_at"],
        "report": out.name,
    }, indent=2) + "\n", encoding="utf-8")


def written_fingerprint(out: Path) -> str | None:
    """The fingerprint of the report already on disk, if it is readable."""
    try:
        state = json.loads(state_path(out).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state.get("fingerprint") if isinstance(state, dict) else None


def refresh(root: Path, out: Path, signals: Signals,
            last: str | None) -> tuple[str, bool]:
    """Regenerate only when the data actually changed. Returns the fingerprint
    now on disk and whether this call was the one that wrote it."""
    report = build_report(root, out, signals, dt.datetime.now())
    if report["fingerprint"] == last:
        return report["fingerprint"], False
    write_report(out, report)
    return report["fingerprint"], True


# --------------------------------------------------------------------------
# live modes
# --------------------------------------------------------------------------

def watch(root: Path, out: Path, signals: Signals,
          interval: float, quiet: bool) -> int:
    """Poll the dossiers and rewrite the report when they change.

    The change test is the payload fingerprint rather than file mtimes: a
    dossier rewritten with identical front matter is not a change worth
    reloading a reader's page for, and mtime alone cannot tell the difference.
    """
    last = written_fingerprint(out)
    try:
        while True:
            try:
                last, wrote = refresh(root, out, signals, last)
                if wrote and not quiet:
                    print(f"{dt.datetime.now():%H:%M:%S}  {out}  {last}",
                          flush=True)
            except OSError as exc:
                # A dossier caught mid-write is normal; the next tick sees it
                # whole, so a warning is enough and stopping would be wrong.
                if not quiet:
                    print(f"warning: {exc}", file=sys.stderr, flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def serve(directory: Path, port: int, quiet: bool):
    """Serve `directory` on localhost and return the running server.

    A report opened as `file://` cannot fetch its own sibling state file — the
    browser refuses the cross-origin request — so serving it is what makes the
    page able to notice its own staleness. Localhost only: this is a local
    development report, not something to expose.

    The imports and the handler live in here on purpose: `--hook` runs once per
    file write in a session and should not pay to import an HTTP server.
    """
    import functools
    import http.server
    import threading

    class Handler(http.server.SimpleHTTPRequestHandler):
        """No caching, and quiet by default — the page polls every couple of
        seconds and a log line per poll would bury anything real."""

        def end_headers(self):
            self.send_header("Cache-Control", "no-store, must-revalidate")
            super().end_headers()

        def log_message(self, fmt, *args):
            if not quiet:
                super().log_message(fmt, *args)

    handler = functools.partial(Handler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def hook_event(root: Path, out: Path, signals: Signals) -> int:
    """PostToolUse entry point: regenerate iff the event wrote a dossier.

    Reads the Claude Code hook JSON on stdin. Anything else is a no-op, because
    this runs after every Write and Edit in a session and must cost nothing the
    rest of the time. It always exits 0 — a broken report is not a reason to
    interrupt someone's session.
    """
    try:
        event = json.load(sys.stdin)
        touched = ((event.get("tool_input") or {}).get("file_path") or "")
        if not touched:
            return 0
        path = Path(touched).resolve()
        if path.suffix != ".md":
            return 0
        if (root / "dossiers").resolve() != path.parent:
            return 0
        refresh(root, out, signals, written_fingerprint(out))
    except Exception as exc:                              # noqa: BLE001
        print(f"open-work report skipped: {exc}", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------
# always-on setup: git hooks plus a Claude Code PostToolUse hook
# --------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> str | None:
    import subprocess
    try:
        done = subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True, check=False)
    except (OSError, ValueError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


LAUNCHER_SOURCE = '''#!/usr/bin/env python3
"""Run the open-work report generator from the *installed* agentic-software-development plugin.

Generated by `generate_open_work.py --install`. Do not edit — re-run --install.

A hook must never name the plugin's cache directory directly. Claude Code
installs every marketplace commit into its own directory, and the generator
resolves its HTML template relative to itself, so a hook pinned to one commit
keeps re-rendering the report through that commit's template long after the
plugin has moved on — the report looks right when a command renders it and
reverts the next time the hook fires. This resolves the current install on
every run instead.

There is deliberately NO fallback path. An earlier version baked the install
directory of whichever plugin commit ran --install into this file, as a
fallback for a manifest that could not be read. That fallback was both
machine-specific and version-pinned: it made this file unsafe to commit, and
on the one occasion it was ever reached it would have rendered through the
stale template this docstring exists to warn about. A fallback that
reintroduces the bug is worse than no fallback.

Arguments pass straight through, stdin included. Exits 0 when the plugin cannot
be found: a missing report is not a reason to fail a commit or a tool call.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

PLUGIN = "@@PLUGIN@@"
MANIFEST = (pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR")
                         or pathlib.Path.home() / ".claude")
            / "plugins" / "installed_plugins.json")
SCRIPT = pathlib.PurePath("scripts", "generate_open_work.py")


def installed() -> str | None:
    """The install directory the manifest currently names for PLUGIN."""
    try:
        entries = json.loads(
            MANIFEST.read_text(encoding="utf-8"))["plugins"][PLUGIN]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    for entry in entries if isinstance(entries, list) else []:
        path = entry.get("installPath") if isinstance(entry, dict) else None
        if path:
            return str(path)
    return None


def generator() -> pathlib.Path | None:
    """The generator to run: whichever install the manifest names right now."""
    base = installed()
    if base and (path := pathlib.Path(base) / SCRIPT).is_file():
        return path
    return None


script = generator()
if script is None:
    print(f"open-work: {MANIFEST} names no installed {PLUGIN} generator",
          file=sys.stderr)
    sys.exit(0)
sys.exit(subprocess.run([sys.executable, str(script), *sys.argv[1:]]).returncode)
'''


def plugin_install(script: Path, manifest: Path | None = None) -> Path | None:
    """The plugin install directory `script` lives in, if it lives in one.

    Claude Code installs a plugin into a directory named after the marketplace
    commit — `.../plugins/cache/<marketplace>/<plugin>/<sha>/` — and a fresh
    directory appears on every update, leaving the old ones on disk. So a hook
    that names the directory it was installed from is pinned to that commit for
    good. The manifest is the authority on which directory is current, and on
    whether this is a plugin install at all rather than a working checkout.

    Both sides are resolved before comparing: the manifest records the path as
    configured, so on a machine where any parent of the cache is a symlink the
    two spellings of the same directory would otherwise not match.
    """
    try:
        installs = json.loads((manifest or PLUGIN_MANIFEST)
                              .read_text(encoding="utf-8"))["plugins"]
    except (OSError, ValueError, KeyError):
        return None
    if not isinstance(installs, dict):
        return None
    parents = set(script.resolve().parents)
    for entries in installs.values():
        for entry in entries if isinstance(entries, list) else []:
            path = entry.get("installPath") if isinstance(entry, dict) else None
            if path and (resolved := Path(path).resolve()) in parents:
                return resolved
    return None


def _plugin_key(install_dir: Path) -> str:
    """`<plugin>@<marketplace>`, the key the manifest lists an install under.

    From `.../cache/<marketplace>/<plugin>/<version>`, the shape Claude Code
    installs into and `plugin_install` only ever returns.
    """
    marketplace, plugin = install_dir.parts[-3], install_dir.parts[-2]
    return f"{plugin}@{marketplace}"


def _launcher(repo: Path, install_dir: Path) -> tuple[Path, bool]:
    """Write the version-resolving launcher into `repo`, and say if it changed.

    Repo-local rather than beside the plugin, because the plugin's own directory
    is the thing being versioned away.

    The only value substituted in is the plugin KEY — `plugin@marketplace` —
    which is the same on every machine. `install_dir` is read for that key and
    never written into the file. So the launcher holds no machine path and no
    version, which is what makes the claim in the next sentence true rather
    than merely intended: it stays correct across plugin updates, and it is
    safe to commit.

    An earlier version also substituted `install_dir` as a fallback, which made
    both halves of that claim false while the docstring went on asserting them.
    """
    path = repo / ".claude" / "hooks" / LAUNCHER_NAME
    source = LAUNCHER_SOURCE.replace("@@PLUGIN@@", _plugin_key(install_dir))
    if path.exists() and path.read_text(encoding="utf-8") == source:
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path, True


def _hook_block(script: Path, root: Path, out: Path) -> str:
    # The interpreter by absolute path, not `python3`: a git hook fired from a
    # GUI client does not necessarily inherit a shell PATH. `|| true` because a
    # report that cannot be written must never fail somebody's commit.
    return (f"{SENTINEL_OPEN}\n"
            f'"{sys.executable}" "{script}" --root "{root}" --out "{out}" '
            f"--quiet || true\n"
            f"{SENTINEL_CLOSE}\n")


def _strip_block(text: str) -> str:
    """Remove our sentinel-delimited block, leaving anything else intact."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        if line.strip() == SENTINEL_OPEN:
            skipping = True
        if not skipping:
            out.append(line)
        if line.strip() == SENTINEL_CLOSE:
            skipping = False
    return "".join(out)


def install(root: Path, out: Path, remove: bool = False) -> int:
    """Wire the report to regenerate itself, and report exactly what changed.

    Two triggers, because neither covers the other. The Claude Code PostToolUse
    hook fires the moment `/plan` or `/work-on` writes a dossier, which is when
    a status actually flips. The git hooks cover the changes that arrive without
    a tool call — a pull, a branch switch, someone else's commit.

    The Claude hook goes in `.claude/settings.local.json`, not the tracked
    `settings.json`: it holds absolute paths from this machine, which would be
    wrong for everyone else in the repo.

    Re-running this repairs rather than skips: a hook whose command is not the
    one we would write now gets rewritten. That is how an install pinned to an
    older plugin commit — every install this wrote before the launcher existed —
    heals, instead of being reported as already done.
    """
    script = Path(__file__).resolve()
    top = _git(root.parent, "rev-parse", "--show-toplevel")
    common = _git(root.parent, "rev-parse", "--git-common-dir")
    if not top:
        print(f"error: {root.parent} is not inside a git repository",
              file=sys.stderr)
        return 1
    repo = Path(top)
    hooks_dir = (Path(common) if common else repo / ".git")
    if not hooks_dir.is_absolute():
        hooks_dir = (repo / hooks_dir)
    hooks_dir = hooks_dir.resolve() / "hooks"
    changed: list[str] = []

    # ---- what the hooks should call: the launcher when this generator is a
    # versioned plugin install, the generator itself when it is a checkout.
    entry_point = script
    launcher = repo / ".claude" / "hooks" / LAUNCHER_NAME
    if remove:
        if launcher.exists():
            launcher.unlink()
            changed.append(f"removed {launcher}")
    elif (install_dir := plugin_install(script)) is not None:
        entry_point, wrote = _launcher(repo, install_dir)
        if wrote:
            changed.append(f"wrote {entry_point}")

    # ---- git hooks
    block = _hook_block(entry_point, root, out)
    for name in GIT_HOOKS:
        path = hooks_dir / name
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if remove:
            if SENTINEL_OPEN not in text:
                continue
            if SENTINEL_CLOSE not in text:
                # Half a block means the file was hand-edited. Stripping from
                # the opening sentinel to EOF would take whatever they wrote
                # with it, so say so and leave it for them.
                print(f"warning: {path} has no closing sentinel — "
                      "remove the block by hand", file=sys.stderr)
                continue
            stripped = _strip_block(text)
            if stripped.strip() in ("", "#!/bin/sh"):
                path.unlink()
                changed.append(f"removed {path}")
            else:
                path.write_text(stripped, encoding="utf-8")
                changed.append(f"unhooked {path}")
        elif block in text:
            continue                                   # already installed
        elif SENTINEL_OPEN in text and SENTINEL_CLOSE in text:
            # Installed, but calling something else — an older generator path.
            path.write_text(_strip_block(text).rstrip("\n") + "\n" + block,
                            encoding="utf-8")
            changed.append(f"repointed {path}")
        else:
            hooks_dir.mkdir(parents=True, exist_ok=True)
            head = text if text else "#!/bin/sh\n"
            if not head.endswith("\n"):
                head += "\n"
            path.write_text(head + block, encoding="utf-8")
            path.chmod(0o755)
            changed.append(f"{'appended to' if text else 'created'} {path}")

    # ---- Claude Code PostToolUse hook
    settings = repo / ".claude" / "settings.local.json"
    data: dict = {}
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8") or "{}")
        except ValueError:
            print(f"error: {settings} is not valid JSON — left untouched",
                  file=sys.stderr)
            for line in changed:
                print(line)
            return 1
    entries = (data.setdefault("hooks", {}).setdefault("PostToolUse", []))
    mine = [e for e in entries
            if any(any(n in (h.get("command") or "") for n in HOOK_NAMES)
                   for h in e.get("hooks", []))]
    command = (f'"{sys.executable}" "{entry_point}" '
               f'--root "{root}" --out "{out}" --hook')
    if remove:
        if mine:
            data["hooks"]["PostToolUse"] = [e for e in entries if e not in mine]
            if not data["hooks"]["PostToolUse"]:
                del data["hooks"]["PostToolUse"]
            if not data["hooks"]:
                del data["hooks"]
            settings.write_text(json.dumps(data, indent=2) + "\n",
                                encoding="utf-8")
            changed.append(f"unhooked {settings}")
    elif not any(command == h.get("command")
                 for e in mine for h in e.get("hooks", [])):
        # Ours but pointing elsewhere, or not there at all: one entry, current.
        data["hooks"]["PostToolUse"] = [e for e in entries if e not in mine] + [{
            "matcher": "Write|Edit|MultiEdit",
            "hooks": [{"type": "command", "command": command}],
        }]
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        changed.append(f"{'repointed' if mine else 'added'} PostToolUse hook "
                       f"in {settings}")

    for line in changed:
        print(line)
    if not changed:
        print("nothing to do — already " +
              ("uninstalled" if remove else "installed"))
    elif not remove:
        print("\nA new Claude Code session picks up the settings hook; "
              "restart this one for it to take effect.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".discovery")
    ap.add_argument("--out", default=None,
                    help="default: <root>/analysis/open-work.html")
    ap.add_argument("--signals", default=None,
                    help="JSON list of health signals mined from Build logs")
    ap.add_argument("--open", action="store_true",
                    help="open the report in the default browser")
    ap.add_argument("--quiet", action="store_true",
                    help="print nothing on success")
    ap.add_argument("--watch", nargs="?", type=float, const=WATCH_SECONDS,
                    metavar="SECONDS",
                    help="regenerate whenever a dossier changes")
    ap.add_argument("--serve", nargs="?", type=int, const=SERVE_PORT,
                    metavar="PORT",
                    help="serve the report on localhost and watch for changes "
                         f"(default port {SERVE_PORT}, 0 picks a free one)")
    ap.add_argument("--hook", action="store_true",
                    help="Claude Code PostToolUse entry point: reads the hook "
                         "JSON on stdin and regenerates only for dossier "
                         "writes")
    ap.add_argument("--install", action="store_true",
                    help="wire git hooks and a Claude Code hook so the report "
                         "keeps itself current")
    ap.add_argument("--uninstall", action="store_true",
                    help="remove what --install added")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    root = Path(args.root).resolve()
    if not (root / "dossiers").is_dir():
        # Walk up from cwd so the script works from any subdirectory.
        for cand in [Path.cwd(), *Path.cwd().resolve().parents]:
            if (cand / ".discovery" / "dossiers").is_dir():
                root = cand / ".discovery"
                break
    if not root.is_dir():
        print(f"error: no .discovery root at {args.root}", file=sys.stderr)
        return 1

    out = (Path(args.out) if args.out
           else root / "analysis" / "open-work.html").resolve()
    now = dt.datetime.now()
    try:
        signals = load_signals(
            out, Path(args.signals) if args.signals else None, now)
    except (OSError, ValueError) as exc:
        print(f"error: --signals: {exc}", file=sys.stderr)
        return 1

    if args.hook:
        return hook_event(root, out, signals)
    if args.install or args.uninstall:
        return install(root, out, remove=args.uninstall)

    if args.serve is not None:
        httpd = serve(out.parent, args.serve, args.quiet)
        url = (f"http://127.0.0.1:{httpd.server_address[1]}/{out.name}")
        refresh(root, out, signals, None)
        print(f"{url}\nwatching {root / 'dossiers'} — Ctrl-C to stop",
              flush=True)
        if args.open:
            import webbrowser
            webbrowser.open(url)
        return watch(root, out, signals,
                     args.watch or WATCH_SECONDS, args.quiet)

    write_report(out, build_report(root, out, signals, now))
    if not args.quiet:
        print(out)
    if args.open:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())
    if args.watch is not None:
        print(f"watching {root / 'dossiers'} — Ctrl-C to stop", flush=True)
        return watch(root, out, signals, args.watch, args.quiet)
    return 0


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def _dossier(id_: str, **kw: object) -> str:
    fm = {"id": id_, "title": kw.get("title", f"Title of {id_}"),
          "status": kw.get("status", "ready"),
          "created": "2026-08-01", "updated": kw.get("updated", "2026-08-10"),
          "baseline_commit": "abc1234", "jira": kw.get("jira", "PROJ-1"),
          "branch": kw.get("branch", f"feat/{id_}"),
          "worktree": kw.get("worktree"), "pr": kw.get("pr")}
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {'null' if v is None else v}")
    lines.append("anchors:\n  - src/x.py:1")
    lines.append(f"blocked_by: [{', '.join(kw.get('blocked_by', []))}]")
    lines.append("adrs: []")
    lines.append("---\n\n## Problem\n\nFACT: x (`src/x.py:1`).\n\n"
                 "## Approach\n\n```python\nstub()\n```\n\n"
                 "One lock, so parallel callers share one drain.\n")
    return "\n".join(lines)


def _check_flows_js() -> list[str]:
    """Run the template's own `flows()` against a graph of three independent
    flows, so the one rectangle-per-flow rule is tested rather than asserted.

    The function is lifted out of the template and run under node, which keeps
    a single definition of the splitting rule. node is not a requirement of
    this script, so its absence skips the check instead of failing it.
    """
    import shutil
    import subprocess

    template = TEMPLATE.read_text(encoding="utf-8")
    match = re.search(r"^function flows\(ids, edges\) \{\n.*?^\}$",
                      template, re.DOTALL | re.MULTILINE)
    if not match:
        return ["template no longer defines flows(ids, edges)"]
    if not shutil.which("node"):
        print("selftest: node missing — skipped the flows() check",
              file=sys.stderr)
        return []

    # W-001→002→003 · W-010→011 · a diamond behind W-023 (the largest flow)
    graph = {
        "W-001": [], "W-002": ["W-001"], "W-003": ["W-002"],
        "W-010": [], "W-011": ["W-010"],
        "W-020": [], "W-021": ["W-020"], "W-022": ["W-020"],
        "W-023": ["W-021", "W-022"],
    }
    edges = [[b, i] for i, bs in graph.items() for b in bs]
    script = (f"const BY_ID = {json.dumps({k: {'blocked_by': v} for k, v in graph.items()})};\n"
              f"{match.group(0)}\n"
              f"const ids = {json.dumps(sorted(graph))};\n"
              f"const edges = {json.dumps(edges)};\n"
              "console.log(JSON.stringify(flows(ids, edges).map("
              "g => ({ids: g.ids, roots: g.roots, edges: g.edges.length}))));")
    done = subprocess.run(["node", "-e", script], capture_output=True,
                          text=True, check=False)
    if done.returncode != 0:
        return [f"flows() threw under node: {done.stderr.strip()}"]
    got = json.loads(done.stdout)

    bad = []
    if len(got) != 3:
        return [f"flows(): expected 3 rectangles, got {len(got)}: {got}"]
    want = [
        {"ids": ["W-020", "W-021", "W-022", "W-023"], "roots": ["W-020"], "edges": 4},
        {"ids": ["W-001", "W-002", "W-003"], "roots": ["W-001"], "edges": 2},
        {"ids": ["W-010", "W-011"], "roots": ["W-010"], "edges": 1},
    ]
    for i, (g, w) in enumerate(zip(got, want)):
        if g != w:
            bad.append(f"flows()[{i}]: {g} != {w}")
    return bad


def selftest() -> int:
    failures = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / ".discovery"
        (root / "dossiers").mkdir(parents=True)
        write = lambda n, t: (root / "dossiers" / n).write_text(t)  # noqa: E731
        write("W-001-a.md", _dossier("W-001", status="ready"))
        write("W-002-b.md", _dossier("W-002", status="ready",
                                     blocked_by=["W-001"]))
        write("W-003-c.md", _dossier("W-003", status="building",
                                     updated="2026-08-01",
                                     worktree="../gone-W-003"))
        write("W-004-d.md", _dossier("W-004", status="pr",
                                     worktree="../repo-W-004"))
        write("W-005-e.md", _dossier("W-005", status="done",
                                     worktree="../repo-W-005"))
        write("W-006-f.md", _dossier("W-006", status="planned"))
        write("W-007-bad.md", "no front matter here\n")

        today = dt.datetime(2026, 8, 11, 9, 0)
        payload = build_payload(root, today, signals=[
            {"signal": "GAP:", "dossier": "W-005", "detail": "one gap",
             "severity": "warning"}])
        S = payload["sections"]

        check(S["ready"] == ["W-001"], f"ready: {S['ready']}")
        check(S["blocked"] == ["W-002"], f"blocked: {S['blocked']}")
        check(S["in_flight"] == ["W-003"], f"in_flight: {S['in_flight']}")
        check(S["waiting_pr"] == ["W-004"], f"waiting_pr: {S['waiting_pr']}")
        check(S["planned"] == ["W-006"], f"planned: {S['planned']}")
        check(S["done"] == ["W-005"], f"done: {S['done']}")
        check(payload["counts"]["total"] == 6, "count excludes the bad file")
        check(payload["next_action"]["command"] == "/work-on W-001",
              f"next action: {payload['next_action']}")
        check(len(payload["parse_errors"]) == 1, "one parse error")

        texts = " | ".join(a["text"] for a in payload["attention"])
        check("blocked by W-001" in texts, "blocked attention row")
        check("does not exist" in texts, "missing-worktree attention row")
        check("still records worktree" in texts, "stale-worktree row")
        check("possibly abandoned" in texts, "abandoned attention row")
        check("unparseable front matter" in texts, "parse-error row")

        w3 = next(d for d in payload["dossiers"] if d["id"] == "W-003")
        check(w3["age_days"] == 10, f"age_days: {w3['age_days']}")
        check(payload["signals"][0]["dossier"] == "W-005", "signals verbatim")
        check(w3["problem"].startswith("FACT: x"), f"problem: {w3['problem']}")
        check(w3["approach"] == "One lock, so parallel callers share one "
                                "drain.", "approach skips the code fence")

        html = render(TEMPLATE.read_text(encoding="utf-8"), payload)
        check("{{DATA_JSON}}" not in html, "placeholder replaced")
        check('"W-001"' in html, "payload embedded")
        check("</html>" in html, "template intact")

        # A title that tries to close the script block must stay inert.
        write("W-008-evil.md", _dossier(
            "W-008", title="</script><script>alert(1)</script>"))
        evil = render(TEMPLATE.read_text(encoding="utf-8"),
                      build_payload(root, today))
        check("</script><script>alert(1)" not in evil,
              "script-close sequence is escaped in the JSON blob")

        # Empty root renders the empty state without error.
        empty_root = Path(tmp) / "empty" / ".discovery"
        (empty_root / "dossiers").mkdir(parents=True)
        ep = build_payload(empty_root, today)
        check(ep["counts"]["total"] == 0 and ep["next_action"] is None,
              "empty root payload")
        render(TEMPLATE.read_text(encoding="utf-8"), ep)

        # ---- fingerprint: blind to the timestamp, sensitive to the data
        (root / "dossiers" / "W-008-evil.md").unlink()
        fp_a = build_payload(root, today)["fingerprint"]
        fp_b = build_payload(root, dt.datetime(2026, 8, 11, 17, 30))
        check(fp_a == fp_b["fingerprint"],
              "regenerating later the same day is not a change")
        # A new day is a change, and should be: every age the page prints, and
        # every possibly-abandoned flag, is computed from the date.
        check(build_payload(root, dt.datetime(2026, 8, 12, 9, 0))["fingerprint"]
              != fp_a, "a new day changes the ages the page shows")
        write("W-006-f.md", _dossier("W-006", status="ready"))
        check(build_payload(root, today)["fingerprint"] != fp_a,
              "fingerprint follows a status change")
        write("W-006-f.md", _dossier("W-006", status="planned"))

        # ---- the template counts too: a plugin update that only restyles the
        # page must still make a report on disk look stale, or the hook keeps
        # declining to rewrite and the old markup outlives the update.
        check(build_payload(root, today)["fingerprint"] == fp_a,
              "an unchanged pipeline hashes back to where it was")
        check(build_payload(root, today)["template"] == template_digest(),
              "the payload carries the template digest")
        restyled = Path(tmp) / "restyled.html"
        restyled.write_text(TEMPLATE.read_text(encoding="utf-8")
                            + "\n<!-- restyled -->\n", encoding="utf-8")
        check(template_digest(restyled) != template_digest(),
              "the digest follows the template file")
        same_data = dict(build_payload(root, today))
        same_data["template"] = template_digest(restyled)
        check(fingerprint(same_data) != fp_a,
              "a new template changes the fingerprint on its own")

        # ---- the report on disk: HTML plus the state file the page polls
        out = root / "analysis" / "open-work.html"
        check(state_path(out).name == "open-work-state.json",
              f"state file name: {state_path(out).name}")
        fp, wrote = refresh(root, out, None, None)
        check(wrote and out.exists() and state_path(out).exists(),
              "first refresh writes both files")
        check(written_fingerprint(out) == fp, "state file records fingerprint")
        check(state_path(out).name in out.read_text(encoding="utf-8"),
              "the page is told the name of its state file")
        stamp = out.stat().st_mtime_ns
        _, wrote_again = refresh(root, out, None, fp)
        check(not wrote_again and out.stat().st_mtime_ns == stamp,
              "an unchanged pipeline is not rewritten")
        write("W-009-new.md", _dossier("W-009", status="ready"))
        fp2, wrote_new = refresh(root, out, None, fp)
        check(wrote_new and fp2 != fp, "a new dossier rewrites the report")

        # ---- signals survive a refresh nobody mined them for
        mined = Path(tmp) / "signals.json"
        mined.write_text(json.dumps(
            [{"signal": "GAP:", "dossier": "W-002", "detail": "one gap",
              "severity": "warning"}]))
        fresh = load_signals(out, mined, today)
        check(len(fresh.items) == 1 and fresh.mined_at == today.isoformat(),
              f"--signals is read and stamped: {fresh}")
        remembered = load_signals(out, None, later := dt.datetime(2026, 8, 14))
        check(remembered.items == fresh.items
              and remembered.mined_at == fresh.mined_at,
              f"a hook-driven refresh keeps the mined signals: {remembered}")
        page = build_payload(root, later, remembered)
        check(page["signals_mined_at_human"] == "11 Aug 2026, 09:00",
              f"the page is told when: {page['signals_mined_at_human']}")
        check(build_payload(root, later)["signals_mined_at_human"] is None,
              "no signals means no 'mined at' claim")
        signals_path(out).unlink()
        check(load_signals(out, None, today) == Signals(),
              "nothing remembered is not an error")
        try:
            mined.write_text('{"not": "a list"}')
            load_signals(out, mined, today)
            check(False, "a non-list --signals should raise")
        except ValueError:
            pass

        # ---- --hook only fires for a dossier write, and never raises
        import contextlib
        import io

        def hook(payload_json: str) -> bool:
            """True when the event caused a rewrite."""
            before = out.stat().st_mtime_ns
            (root / "dossiers" / "W-010-x.md").write_text(
                _dossier("W-010", status="ready"))
            stdin, sys.stdin = sys.stdin, io.StringIO(payload_json)
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    rc = hook_event(root, out, None)
            finally:
                sys.stdin = stdin
                (root / "dossiers" / "W-010-x.md").unlink()
            check(rc == 0, "hook always exits 0")
            return out.stat().st_mtime_ns != before

        dossier_file = str(root / "dossiers" / "W-010-x.md")
        check(hook(json.dumps({"tool_input": {"file_path": dossier_file}})),
              "a dossier write regenerates the report")
        check(not hook(json.dumps(
            {"tool_input": {"file_path": str(root / "analysis" / "notes.md")}})),
            "a write outside dossiers/ is ignored")
        check(not hook(json.dumps({"tool_input": {"file_path": dossier_file
                                                  + ".txt"}})),
              "a non-markdown write is ignored")
        check(not hook("not json at all"), "malformed hook input is ignored")
        check(not hook(json.dumps({})), "an event with no file_path is ignored")

        import subprocess

        # ---- the launcher --install points hooks at, over a stand-in config
        # directory holding two versions of the plugin. This is the whole point
        # of it: a hook wired before an update has to follow the update.
        config = Path(tmp) / "config"
        cache = config / "plugins" / "cache" / "mp" / "agentic-software-development"
        manifest = config / "plugins" / "installed_plugins.json"
        key = "agentic-software-development@mp"
        for version in ("old1", "new2"):
            (cache / version / "scripts").mkdir(parents=True)
            (cache / version / "scripts" / "generate_open_work.py").write_text(
                f"import sys\nprint('{version}', *sys.argv[1:])\n")

        def publish(version: str) -> None:
            manifest.write_text(json.dumps({"plugins": {key: [
                {"installPath": str(cache / version)}]}}), encoding="utf-8")

        publish("old1")
        old = cache / "old1" / "scripts" / "generate_open_work.py"
        check(_plugin_key(cache / "old1") == key,
              f"plugin key from an install path: {_plugin_key(cache / 'old1')}")
        # tempfile hands out a symlinked path on macOS, so this also covers the
        # two spellings of one directory that `plugin_install` has to reconcile.
        check(plugin_install(old, manifest) == (cache / "old1").resolve(),
              f"a generator inside an install is recognised: {old}")
        check(plugin_install(Path(__file__).resolve(), manifest) is None,
              "a working checkout is not a plugin install")

        launcher_repo = Path(tmp) / "launched"
        lp, wrote = _launcher(launcher_repo, cache / "old1")
        check(wrote and lp.name == LAUNCHER_NAME and lp.parent.name == "hooks",
              f"launcher written to a stable repo path: {lp}")
        check(not _launcher(launcher_repo, cache / "old1")[1],
              "rewriting an unchanged launcher is a no-op")

        def launch(**env) -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, str(lp), "--root", "X"],
                                  capture_output=True, text=True,
                                  env={**os.environ, **env})

        ran = launch(CLAUDE_CONFIG_DIR=str(config))
        check(ran.returncode == 0 and "old1 --root X" in ran.stdout,
              f"the launcher runs the generator and passes arguments: {ran}")
        publish("new2")
        ran = launch(CLAUDE_CONFIG_DIR=str(config))
        check("new2 --root X" in ran.stdout,
              f"a hook wired before an update follows the update: {ran}")
        # No manifest to read: nothing runs, and the note names the manifest.
        # There is deliberately no fallback. Reaching one would need a baked
        # absolute path, and a baked path is both machine-specific and
        # version-pinned — it made this file unsafe to commit, and on the one
        # occasion it was ever reached it would have rendered the report
        # through the stale template the launcher exists to avoid. The two
        # properties are exclusive: resilience to an unreadable config dir has
        # nothing left to derive a location from.
        ran = launch(CLAUDE_CONFIG_DIR=str(Path(tmp) / "nowhere"))
        check(ran.returncode == 0
              and "old1" not in ran.stdout and "new2" not in ran.stdout
              and "names no installed" in ran.stderr
              and "installed_plugins.json" in ran.stderr,
              f"an unreadable manifest runs nothing and says which file: {ran}")
        gone, _ = _launcher(launcher_repo, cache / "deleted")
        ran = subprocess.run([sys.executable, str(gone)],
                             capture_output=True, text=True,
                             env={**os.environ,
                                  "CLAUDE_CONFIG_DIR": str(Path(tmp) / "no")})
        check(ran.returncode == 0 and "no installed" in ran.stderr,
              f"an unresolvable plugin is a note, not a failure: {ran}")

        # ---- --install / --uninstall round trip, in a throwaway repo
        repo = Path(tmp) / "repo"
        (repo / ".discovery" / "dossiers").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True,
                       capture_output=True)
        r_root, r_out = (repo / ".discovery",
                         repo / ".discovery" / "analysis" / "open-work.html")
        (repo / ".claude").mkdir()
        (repo / ".claude" / "settings.local.json").write_text(
            '{"permissions": {"allow": ["Bash(ls:*)"]}}\n')
        (repo / ".git" / "hooks" / "post-commit").write_text(
            "#!/bin/sh\necho pre-existing\n")

        def run_install(**kw) -> str:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = install(r_root, r_out, **kw)
            check(rc == 0, f"install rc: {rc} {kw}")
            return buf.getvalue()

        said = run_install()
        settings = json.loads(
            (repo / ".claude" / "settings.local.json").read_text())
        entries = settings["hooks"]["PostToolUse"]
        check(settings["permissions"]["allow"] == ["Bash(ls:*)"],
              "install keeps the settings it did not write")
        check(len(entries) == 1 and "--hook" in entries[0]["hooks"][0]["command"],
              f"PostToolUse hook installed: {entries}")
        pc = (repo / ".git" / "hooks" / "post-commit").read_text()
        check("echo pre-existing" in pc and SENTINEL_OPEN in pc,
              "an existing git hook is appended to, not replaced")
        check(f'--out "{r_out}"' in pc
              and f'--out "{r_out}"' in entries[0]["hooks"][0]["command"],
              "both triggers write the same report")
        check((repo / ".git" / "hooks" / "post-merge").exists(),
              "missing git hooks are created")
        check((repo / ".git" / "hooks" / "post-merge").stat().st_mode & 0o111,
              "created git hooks are executable")
        check("settings.local.json" in said, "install says what it changed")

        check("nothing to do" in run_install(), "install is idempotent")

        # ---- a hook pointing at an older generator is repaired, not skipped.
        # This is the failure it exists for: Claude Code installs every plugin
        # commit into its own directory, so a hook naming one keeps rendering
        # the report through that commit's template forever.
        stale = f'"{sys.executable}" "/old/cache/sha/{HOOK_NAMES[1]}" --hook'
        settings_path = repo / ".claude" / "settings.local.json"
        data = json.loads(settings_path.read_text())
        data["hooks"]["PostToolUse"] = [
            {"matcher": "Write|Edit|MultiEdit",
             "hooks": [{"type": "command", "command": stale}]}]
        settings_path.write_text(json.dumps(data, indent=2) + "\n")
        stale_pc = repo / ".git" / "hooks" / "post-commit"
        stale_pc.write_text(
            _strip_block(stale_pc.read_text()).rstrip("\n") + "\n"
            + _hook_block(Path("/old/x.py"), r_root, r_out))

        said = run_install()
        entries = json.loads(settings_path.read_text())["hooks"]["PostToolUse"]
        check(len(entries) == 1 and entries[0]["hooks"][0]["command"] != stale,
              f"a stale PostToolUse hook is repointed, not duplicated: {entries}")
        check("repointed" in said, f"install says it repointed: {said}")
        pc = stale_pc.read_text()
        check("/old/x.py" not in pc and pc.count(SENTINEL_OPEN) == 1
              and "echo pre-existing" in pc,
              "a stale git hook block is replaced in place")
        check("nothing to do" in run_install(),
              "install is idempotent again after a repair")

        run_install(remove=True)
        settings = json.loads(
            (repo / ".claude" / "settings.local.json").read_text())
        check("hooks" not in settings, f"uninstall drops the hook: {settings}")
        check(settings["permissions"]["allow"] == ["Bash(ls:*)"],
              "uninstall keeps the settings it did not write")
        pc_path = repo / ".git" / "hooks" / "post-commit"
        check(pc_path.exists() and SENTINEL_OPEN not in pc_path.read_text()
              and "echo pre-existing" in pc_path.read_text(),
              "uninstall leaves a pre-existing git hook behind, minus our block")
        check(not (repo / ".git" / "hooks" / "post-merge").exists(),
              "a git hook we created is removed outright")
        check("nothing to do" in run_install(remove=True),
              "uninstall is idempotent")

        failures.extend(_check_flows_js())

    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    print("selftest:", "FAIL" if failures else "ok",
          file=sys.stderr if failures else sys.stdout)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
