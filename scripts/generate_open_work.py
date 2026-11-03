#!/usr/bin/env python3
"""Generate the open-work HTML overview from dossier front matter.

Deterministic, and read-only toward pipeline state: reads the YAML front
matter of `.discovery/dossiers/*.md` through the one parser
(`validate_pipeline.split_front_matter`), computes the overview sections
`/overview-dossiers` reports in chat, and writes one self-contained HTML file
(inline CSS and JS, no external assets). It never writes a dossier, an ADR,
or `state` — the only file it writes is the report.

Usage:
    generate_open_work.py [--root .discovery] [--out PATH] [--worktrees]
                          [--embed] [--link-base URL] [--direction lr|tb]
                          [--graph flows|focus]
                          [--simplify-default] [--open] [--quiet]
    generate_open_work.py --uninstall         # remove hooks an old --install wrote
    generate_open_work.py --selftest

Default output: <root>/analysis/open-work.html. The script writes that one
file and nothing else. Refreshing it is not its job: the pipeline dashboard
(shared/questions/, references/owner-questions.md) re-runs it on change with
`--root <repo>/.discovery --worktrees --out <file> --quiet` and embeds the
result in an iframe.

`--worktrees` overlays the live copy of each dossier from the repository's
sibling worktrees (references/formats/README.md § "Two modes"): when `.discovery/`
is committed, this checkout's copy changes only when a PR merges, while a
build in flight lives in `../<repo>-<ID>` and a plan under review in
`../<repo>-plan-<KEY>`. An overlaid row carries a `live from` flag.
`--embed` omits the page's own header, so the report sits inside an iframe
that has a title bar of its own; it follows the system light/dark setting
(`?theme=light|dark` on the iframe URL overrides it).
`--link-base URL` (a path such as `/repo/web` or an http(s) URL) makes every
dossier reference in the report (graph nodes, table rows, dependency chips)
navigate the TOP window to `<URL>/<ID>` instead of opening the report's own
detail sheet; the pipeline dashboard passes `--link-base /repo/<repo>` with
`--embed`, so a click in the graph lands on that dossier's dashboard route.
Without the flag the report behaves as before.
`--direction lr|tb` picks the graph layout: `lr` (default) puts dependency
ranks in columns, left to right; `tb` draws ONE layered graph top to bottom
(all flows merged, roots sharing one display-only "Start" and one "Earlier work"
node): longest-path ranks, a dummy waypoint for every rank an edge skips so long
edges run through the gaps between boxes, barycenter/median crossing reduction,
nodes at natural size with straight dummy chains. Edges leave a box's bottom
centre and enter the next one's top centre and never cross a box; nothing wraps
or shrinks, a drawing wider than the panel scrolls inside the panel.
`--simplify-default` opens the report with the Simplify toggle on;
the toggle still works, and a choice the user remembered in localStorage wins.
`--graph flows|focus` picks the drawing that replaces the "Dependency flows"
section: `flows` (default) is the node-and-edge drawing above; `focus` is the
focus map. Lanes by what you can do, top to bottom: In progress (building,
review, pr), Ready now (ready, every prerequisite done), Blocked (an
unfinished prerequisite, each chip naming its first blocker), Not planned yet
(planned, plus display-only chips for missing dossiers); Done and dropped sit
in a last lane that Simplify hides. Each dossier is a chip (status dot, id,
one-line title, "needs N" unfinished prerequisites, "unblocks M" direct
dependents), most dependents first, wrapping inside the lane, so it fits any
width. A ribbon above the lanes shows the critical path, the longest chain of
unfinished dossiers ("4 to go", "+n similar" on ties). No edges are drawn until
a chip takes hover, keyboard focus or a first touch tap: its transitive chain
stays bright, the rest dims, connectors are drawn in an SVG overlay between
those chips only, and a small panel lists Needs and Unblocks with statuses and
an Open link. Click opens the dossier (`--link-base`); Esc or a click on empty
space leaves focus. `--direction` is ignored in focus mode.
The dashboard passes `--graph focus --simplify-default` with `--embed`.

The template lives at references/templates/open-work.html, next to this
script's parent. Its dependency graph includes every dossier, with display-only
root stubs. The remembered Simplify toggle hides done and dropped history except
for prerequisites directly bordering an unfinished dossier; it never edits
pipeline dependencies.

`--uninstall` exists only to clean up after the git hooks and the Claude Code
hook that earlier versions' `--install` wrote; nothing writes them any more.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_pipeline import sections, split_front_matter  # noqa: E402

STATUSES = ["planned", "ready", "building", "review", "pr", "done", "dropped"]
IN_FLIGHT = {"building", "review"}
ABANDONED_AFTER_DAYS = 2   # `updated` is a date, so >24h is certain at 2 days

TEMPLATE = (Path(__file__).resolve().parent.parent
            / "references" / "templates" / "open-work.html")

GIT_HOOKS = ["post-commit", "post-merge", "post-checkout", "post-rewrite"]
SENTINEL_OPEN = "# >>> open-work report (generate_open_work.py) >>>"
SENTINEL_CLOSE = "# <<< open-work report <<<"

LAUNCHER_NAME = "open-work-report.py"
# Either name in a hook command means the hook is ours: the launcher is what
# the last --install wrote, the generator itself is what older versions wrote.
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


def sibling_worktrees(repo: Path) -> list[Path]:
    """Every other worktree of the repository at `repo`, from git itself."""
    out = _git(repo, "worktree", "list", "--porcelain")
    if not out:
        return []
    here = repo.resolve()
    paths = [Path(line[len("worktree "):].strip())
             for line in out.splitlines() if line.startswith("worktree ")]
    return [p for p in paths if p.resolve() != here]


# How far a dossier has come; `dropped` is terminal like `done`.
_PROGRESS = {"planned": 0, "ready": 1, "building": 2, "review": 3, "pr": 4, "done": 5, "dropped": 5}


def _newer(live: dict, base: dict) -> bool:
    """Whether a worktree's copy is ahead of this checkout's: a later `updated`,
    or the same date with a status further along. `updated` is a date, so two
    copies changed on one day are told apart by how far each has come."""
    lu, bu = str(live.get("updated") or ""), str(base.get("updated") or "")
    if lu != bu:
        return lu > bu
    return _PROGRESS.get(live.get("status"), 0) > _PROGRESS.get(base.get("status"), 0)


def overlay_worktrees(root: Path, dossiers: list[dict]) -> list[tuple[str, str]]:
    """Replace each dossier with its live copy from a sibling worktree.

    Candidates for one id are ranked by `updated`, then by the worktree named
    for the id, so an implementer's `../<repo>-<ID>-P1` copy — forked from the
    base worktree and frozen there — never outranks the base worktree's own.
    The winner replaces this checkout's copy only when it is newer (`_newer`):
    a stale worktree forked before the dossier moved on is reported, never
    shown in its place. A copy in the same state as this checkout's is left
    alone. Returns (id, note) pairs describing each overlay.
    """
    repo = root.parent
    candidates: dict[str, list[tuple[str, dict]]] = {}
    for wt in sibling_worktrees(repo):
        wt_root = wt / root.name
        if not (wt_root / "dossiers").is_dir():
            continue
        label = os.path.relpath(wt.resolve(), repo.resolve())
        live, _errors = load_dossiers(wt_root)
        for d in live:
            candidates.setdefault(d["id"], []).append((label, d))

    notes: list[tuple[str, str]] = []
    index = {d["id"]: i for i, d in enumerate(dossiers)}
    for did, options in sorted(candidates.items()):
        label, live = max(options, key=lambda o: (str(o[1]["updated"] or ""),
                                                  o[0].endswith(f"-{did}")))
        live["live_from"] = label
        if did in index:
            base = dossiers[index[did]]
            if base["status"] == live["status"] and \
                    str(base["updated"]) == str(live["updated"]):
                continue
            if not _newer(live, base):
                # An older copy — a worktree forked before this checkout moved
                # on — never replaces the checkout's own state.
                notes.append((did, f"{did}: worktree {label} holds an older copy "
                                   f"({live['status']}, updated {live['updated']}); "
                                   f"this checkout's {base['status']} stands."))
                continue
            dossiers[index[did]] = live
            notes.append((did, f"{did} is shown live from {label} "
                               f"({live['status']}); this checkout has "
                               f"{base['status']}."))
        else:
            index[did] = len(dossiers)
            dossiers.append(live)
            notes.append((did, f"{did} exists only in worktree {label} "
                               f"({live['status']}) — a plan not merged yet."))
    return notes


def classify(dossiers: list[dict], errors: list[dict], root: Path,
             today: dt.date) -> dict:
    """The overview /overview-dossiers defines, as one JSON payload for the template.

    Section semantics mirror commands/overview-dossiers.md exactly: ready means
    `status: ready` with every blocker done; blocked means `status: ready`
    with a blocker that is not done; stale worktrees are recorded paths that
    disagree with the disk.
    """
    by_id = {d["id"]: d for d in dossiers}
    sections: dict[str, list[str]] = {k: [] for k in (
        "ready", "in_flight", "waiting_pr", "blocked", "planned",
        "done", "dropped")}

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
        elif status in IN_FLIGHT:
            sections["in_flight"].append(d["id"])
            if d["age_days"] is not None and \
                    d["age_days"] >= ABANDONED_AFTER_DAYS:
                d["flags"].append({
                    "severity": "warning", "label": "possibly abandoned",
                    "detail": f"No update for {d['age_days']} days while "
                              f"{status}.",
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
            d["flags"].append({
                "severity": "critical", "label": "unknown status",
                "detail": f"\"{status}\" is not one of {', '.join(STATUSES)}.",
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
            elif status == "building" and not exists:
                d["flags"].append({
                    "severity": "serious", "label": "worktree missing",
                    "detail": f"{wt} does not exist on disk.",
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
        "next_action": next_action,
        "counts": {"total": len(dossiers),
                   "by_status": {k: v for k, v in by_status.items() if v}},
    }


def build_payload(root: Path, now: dt.datetime, worktrees: bool = False,
                  embed: bool = False, link_base: str = "",
                  direction: str = "lr",
                  simplify_default: bool = False,
                  graph: str = "flows") -> dict:
    dossiers, errors = load_dossiers(root)
    if worktrees:
        overlay_worktrees(root, dossiers)
    payload = classify(dossiers, errors, root, now.date())
    for d in dossiers:
        if d.get("live_from"):
            d["flags"].append({
                "severity": "good", "label": f"live from {d['live_from']}",
                "detail": "This row is the worktree's copy; the checked-out "
                          "branch changes only when a PR merges.",
            })
    payload.update({
        "worktrees": worktrees,
        "embed": embed,
        "link_base": link_base.rstrip("/"),
        "direction": direction if direction in ("lr", "tb") else "lr",
        "simplify_default": bool(simplify_default),
        **({"graph": "focus"} if graph == "focus" else {}),
        "generated_at": now.isoformat(timespec="seconds"),
        "generated_at_human": now.strftime("%-d %b %Y, %H:%M"),
        "root": str(root),
        "dossiers": dossiers,
        "parse_errors": errors,
    })
    return payload


def render(template_text: str, payload: dict) -> str:
    if "{{DATA_JSON}}" not in template_text:
        raise ValueError("template has no {{DATA_JSON}} placeholder")
    blob = json.dumps(payload, ensure_ascii=False, default=str)
    # `</` must not appear inside the inline <script> block.
    html = template_text.replace("{{DATA_JSON}}", blob.replace("</", "<\\/"))
    if payload.get("embed"):
        # Read by the template before first paint, to skip the page's own
        # header and follow the system light/dark setting.
        html = html.replace('<html lang="en">', '<html lang="en" data-embed="1">', 1)
    return html


def write_report(out: Path, html: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")


def _git(repo: Path, *args: str) -> str | None:
    import subprocess
    try:
        done = subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True, check=False)
    except (OSError, ValueError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


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


def uninstall(root: Path) -> int:
    """Remove everything an earlier `--install` wrote, and say what changed.

    That was: the sentinel-delimited block in the git hooks post-commit,
    post-merge, post-checkout and post-rewrite (the hook file itself when the
    block was all it held), the `.claude/hooks/open-work-report.py` launcher,
    and the PostToolUse entry in `.claude/settings.local.json` whose command
    names the launcher or this generator. Anything else in those files stays.
    """
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

    launcher = repo / ".claude" / "hooks" / LAUNCHER_NAME
    if launcher.exists():
        launcher.unlink()
        changed.append(f"removed {launcher}")

    for name in GIT_HOOKS:
        path = hooks_dir / name
        text = path.read_text(encoding="utf-8") if path.exists() else ""
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

    settings = repo / ".claude" / "settings.local.json"
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8") or "{}")
        except ValueError:
            print(f"error: {settings} is not valid JSON — left untouched",
                  file=sys.stderr)
            for line in changed:
                print(line)
            return 1
        entries = (data.get("hooks") or {}).get("PostToolUse") or []
        mine = [e for e in entries
                if any(any(n in (h.get("command") or "") for n in HOOK_NAMES)
                       for h in e.get("hooks", []))]
        if mine:
            data["hooks"]["PostToolUse"] = [e for e in entries if e not in mine]
            if not data["hooks"]["PostToolUse"]:
                del data["hooks"]["PostToolUse"]
            if not data["hooks"]:
                del data["hooks"]
            settings.write_text(json.dumps(data, indent=2) + "\n",
                                encoding="utf-8")
            changed.append(f"unhooked {settings}")

    for line in changed:
        print(line)
    if not changed:
        print("nothing to do — already uninstalled")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".discovery")
    ap.add_argument("--out", default=None,
                    help="default: <root>/analysis/open-work.html")
    ap.add_argument("--worktrees", action="store_true",
                    help="overlay each dossier's live copy from the "
                         "repository's sibling worktrees (committed mode)")
    ap.add_argument("--embed", action="store_true",
                    help="omit the page's own header, for use inside an "
                         "iframe; follows the system light/dark setting")
    ap.add_argument("--link-base", default="", metavar="URL",
                    help="navigate the top window to <URL>/<ID> when a "
                         "dossier is clicked (a /path or an http(s) URL)")
    ap.add_argument("--direction", choices=("lr", "tb"), default="lr",
                    help="graph layout: lr (default, ranks are columns) or "
                         "tb (ranks are rows, fits a narrow column)")
    ap.add_argument("--graph", choices=("flows", "focus"), default="flows",
                    help="dependency drawing: flows (default, node-and-edge) "
                         "or focus (lanes of chips, edges on hover)")
    ap.add_argument("--simplify-default", action="store_true",
                    help="open with the Simplify toggle on (a choice the "
                         "user remembered in localStorage still wins)")
    ap.add_argument("--open", action="store_true",
                    help="open the report in the default browser")
    ap.add_argument("--quiet", action="store_true",
                    help="print nothing on success")
    ap.add_argument("--uninstall", action="store_true",
                    help="remove the git hooks and Claude Code hook an "
                         "earlier --install wrote")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.link_base and not re.match(r"^(/(?!/)|https?://)[\w./:~%-]*$",
                                       args.link_base):
        ap.error("--link-base must be a /path or an http(s) URL")

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

    if args.uninstall:
        return uninstall(root)

    out = (Path(args.out) if args.out
           else root / "analysis" / "open-work.html").resolve()
    payload = build_payload(root, dt.datetime.now(), worktrees=args.worktrees,
                            embed=args.embed, link_base=args.link_base,
                            direction=args.direction,
                            simplify_default=args.simplify_default,
                            graph=args.graph)
    write_report(out, render(TEMPLATE.read_text(encoding="utf-8"), payload))
    if not args.quiet:
        print(out)
    if args.open:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())
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
    """Check the template's flow splitting, completed frontier and root stubs.

    The functions are lifted out of the template and run under node, which keeps
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
    graph_match = re.search(
        r"^function dependencyGraph\(dossiers, simplified, shared\) \{\n.*?^\}$",
        template, re.DOTALL | re.MULTILINE)
    if not graph_match:
        return ["template no longer defines dependencyGraph(dossiers, simplified, shared)"]
    if not shutil.which("node"):
        print("selftest: node missing — skipped the dependency graph checks",
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
    frontier_checks = r"""
const assert = require("node:assert/strict");
const dossier = (id, status, blocked_by = []) => ({id, status, blocked_by});
const dossiers = [
  dossier("W-001", "done"),
  dossier("W-002", "done", ["W-001"]),
  dossier("W-003", "ready", ["W-002"]),
  dossier("W-004", "ready", ["W-003"]),
  dossier("W-005", "building"),
  dossier("W-006", "review"),
  dossier("W-007", "pr"),
  dossier("W-008", "planned"),
  dossier("W-009", "dropped"),
  dossier("W-010", "done", ["W-001"]),
  dossier("W-011", "done"),
  dossier("W-012", "done"),
  dossier("W-013", "ready", ["W-002", "W-012", "W-MISSING"]),
];
const original = JSON.stringify(dossiers);
const full = dependencyGraph(dossiers, false);
const simple = dependencyGraph(dossiers, true);
const realIds = graph => graph.ids.filter(id => !graph.nodes[id].stub).sort();
assert.deepEqual(realIds(full), dossiers.map(d => d.id));
assert.deepEqual(realIds(simple), ["W-002", "W-003", "W-004", "W-005", "W-006", "W-007", "W-008", "W-012", "W-013"]);
assert.equal(simple.hiddenFinished, 4);
for (const graph of [full, simple]) {
  for (const id of realIds(graph))
    assert.ok(graph.edges.some(([, to]) => to === id), `${id} needs a dependency or stub`);
  for (const [from, to] of graph.edges)
    assert.ok(graph.nodes[from] && graph.nodes[to], "all edge endpoints must be visible");
  const groups = flows(graph.ids, graph.edges);
  assert.equal(groups.length, graph === full ? 7 : 5, "stubs must not join independent flows");
  assert.equal(groups.flatMap(g => g.ids).length, graph.ids.length);
}
assert.ok(simple.edges.some(([a, b]) => a === "W-002" && b === "W-003"));
assert.ok(simple.edges.some(([a, b]) => a === "W-003" && b === "W-004"));
assert.deepEqual(simple.nodes["W-MISSING"], {id: "W-MISSING", label: "W-MISSING", title: "Missing dossier", status: "missing", stub: true});
const history = simple.edges.find(([, to]) => to === "W-002")[0];
assert.equal(simple.nodes[history].label, "Earlier work");
assert.equal(simple.nodes[history].status, "done");
const start = simple.edges.find(([, to]) => to === "W-005")[0];
assert.equal(simple.nodes[start].label, "Start");
assert.equal(JSON.stringify(dossiers), original, "rendering must not mutate dossier dependencies");
assert.deepEqual(dependencyGraph(dossiers, false), full, "full mode restores all history");
assert.deepEqual(dependencyGraph([], true).ids, []);
assert.deepEqual(dependencyGraph(dossiers.filter(d => d.status === "done"), true).ids, []);
assert.deepEqual(dependencyGraph(dossiers.filter(d => d.status === "dropped"), true).ids, []);
const finishedChain = [dossier("W-300", "done"), dossier("W-301", "dropped", ["W-300"])];
assert.deepEqual(dependencyGraph(finishedChain, true).ids, [], "done/dropped-only flows are hidden");
const droppedChain = [...finishedChain, dossier("W-302", "dropped", ["W-301"]), dossier("W-303", "ready", ["W-302"])];
const droppedFrontier = dependencyGraph(droppedChain, true);
assert.deepEqual(realIds(droppedFrontier), ["W-302", "W-303"], "only the dropped prerequisite at the unfinished frontier remains");
assert.equal(droppedFrontier.hiddenFinished, 2);
assert.ok(droppedFrontier.edges.some(([a, b]) => a === "W-302" && b === "W-303"));
assert.deepEqual(realIds(dependencyGraph(droppedChain, false)), ["W-300", "W-301", "W-302", "W-303"], "full mode restores dropped history");
const missing = dependencyGraph([dossier("W-100", "ready", ["W-NOT-FOUND"])], true);
assert.deepEqual(missing.edges, [["W-NOT-FOUND", "W-100"]]);
const cycle = dependencyGraph([dossier("W-200", "ready", ["W-201"]), dossier("W-201", "ready", ["W-200"])], true);
assert.deepEqual(realIds(cycle), ["W-200", "W-201"]);
"""
    script = (f"{match.group(0)}\n{graph_match.group(0)}\n"
              f"{frontier_checks}\n"
              f"const ids = {json.dumps(sorted(graph))};\n"
              f"const edges = {json.dumps(edges)};\n"
              "console.log(JSON.stringify(flows(ids, edges).map("
              "g => ({ids: g.ids, roots: g.roots, edges: g.edges.length}))));")
    done = subprocess.run(["node", "-e", script], capture_output=True,
                          text=True, check=False)
    if done.returncode != 0:
        return [f"dependency graph check failed under node: {done.stderr.strip()}"]
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


def _check_focus_js() -> list[str]:
    """Lane assignment, counters, ordering and critical path of the focus map."""
    import shutil
    import subprocess

    template = TEMPLATE.read_text(encoding="utf-8")
    m = re.search(r"^function focusModel\(dossiers\) \{\n.*?^\}$", template,
                  re.DOTALL | re.MULTILINE)
    if not m:
        return ["template no longer defines focusModel(dossiers)"]
    if not shutil.which("node"):
        print("selftest: node missing — skipped the focus map checks",
              file=sys.stderr)
        return []
    checks = r"""
const assert = require("node:assert/strict");
const D = (id, status, blocked_by = [], title = "T " + id) => ({id, status, blocked_by, title});
const ds = [
  D("W-1", "done"), D("W-2", "ready", ["W-1"]), D("W-3", "ready", ["W-2"]),
  D("W-4", "ready", ["W-3", "W-1"]), D("W-5", "building", ["W-3"]), D("W-6", "review"),
  D("W-7", "pr"), D("W-8", "planned"), D("W-9", "dropped"), D("W-10", "ready", ["W-GONE"]),
  D("W-11", "ready"), D("W-12", "ready", ["W-11"]),
];
const m = focusModel(ds);
const lane = id => m.items[id].lane;
assert.equal(lane("W-2"), "ready");
assert.equal(lane("W-3"), "blocked");
assert.equal(lane("W-4"), "blocked");
assert.equal(lane("W-5"), "progress");
assert.equal(lane("W-6"), "progress");
assert.equal(lane("W-7"), "progress");
assert.equal(lane("W-8"), "planned");
assert.equal(lane("W-1"), "finished");
assert.equal(lane("W-9"), "finished");
assert.equal(lane("W-10"), "blocked", "a missing prerequisite blocks");
assert.equal(m.items["W-GONE"].lane, "planned");
assert.equal(m.items["W-GONE"].status, "missing");
assert.equal(m.items["W-10"].blocker, "W-GONE");
assert.equal(m.items["W-4"].needs, 1);
assert.equal(m.items["W-4"].blocker, "W-3");
assert.equal(m.items["W-2"].needs, 0);
assert.equal(m.items["W-2"].unblocks, 1);
assert.equal(m.items["W-1"].unblocks, 2);
assert.equal(m.items["W-3"].unblocks, 2);
assert.deepEqual(m.lanes.ready.map(i => i.id), ["W-11", "W-2"], "tie on 1 dependent: id order, string compare");
assert.deepEqual(m.lanes.blocked.map(i => i.id), ["W-3", "W-10", "W-12", "W-4"], "most dependents first, then id");
assert.deepEqual(m.critical.path, ["W-2", "W-3", "W-4"]);
assert.equal(m.critical.len, 3);
assert.equal(m.critical.similar, 1, "W-2>W-3>W-5 ties with W-2>W-3>W-4");
assert.deepEqual([...m.up("W-4")].sort(), ["W-1", "W-2", "W-3"]);
assert.deepEqual([...m.down("W-2")].sort(), ["W-3", "W-4", "W-5"]);
const solo = focusModel([D("A", "ready"), D("B", "ready")]);
assert.equal(solo.critical.len, 1);
const cyc = focusModel([D("A", "ready", ["B"]), D("B", "ready", ["A"])]);
assert.ok(cyc.critical.len >= 1);
assert.deepEqual(focusModel([]).critical.path, []);
"""
    script = f"{m.group(0)}\n{checks}"
    done = subprocess.run(["node", "-e", script], capture_output=True,
                          text=True, check=False)
    if done.returncode != 0:
        return [f"focus map check failed under node: {done.stderr.strip()[:600]}"]
    return []


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
        payload = build_payload(root, today)
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

        check("attention" not in payload and "signals" not in payload
              and "fingerprint" not in payload,
              "no attention, signals or fingerprint in the payload")
        w3f = {f["label"] for d in payload["dossiers"] if d["id"] == "W-003"
               for f in d["flags"]}
        check({"worktree missing", "possibly abandoned"} <= w3f,
              f"per-row flags stay: {w3f}")

        w3 = next(d for d in payload["dossiers"] if d["id"] == "W-003")
        check(w3["age_days"] == 10, f"age_days: {w3['age_days']}")
        check(w3["problem"].startswith("FACT: x"), f"problem: {w3['problem']}")
        check(w3["approach"] == "One lock, so parallel callers share one "
                                "drain.", "approach skips the code fence")

        html = render(TEMPLATE.read_text(encoding="utf-8"), payload)
        check("{{DATA_JSON}}" not in html, "placeholder replaced")
        check('"W-001"' in html, "payload embedded")
        check("</html>" in html, "template intact")
        check('"link_base": ""' in html, "no link base without the flag")
        lb = render(TEMPLATE.read_text(encoding="utf-8"),
                    build_payload(root, today, embed=True,
                                  link_base="/repo/web/"))
        check('"link_base": "/repo/web"' in lb, "link base embedded, trimmed")
        check('"direction": "lr"' in html and '"simplify_default": false' in html,
              "defaults: left-to-right, Simplify off")
        tbh = render(TEMPLATE.read_text(encoding="utf-8"),
                     build_payload(root, today, direction="tb",
                                   simplify_default=True))
        check('"direction": "tb"' in tbh and '"simplify_default": true' in tbh,
              "--direction tb and --simplify-default reach the page")
        check("const TB = DATA.direction" in tbh and "DATA.simplify_default" in tbh
              and "open-work-simplify" in tbh,
              "template reads the direction and the Simplify default; "
              "a remembered choice still wins")
        # tb layout: one layered drawing; no edge segment crosses a node box.
        node = shutil.which("node")
        if node:
            m = re.search(r"function layeredLayout\(.*?\n}\n", tbh, re.S)
            js = (m.group(0) if m else "") + """
const fixtures = [];
// config-harness-like: shared Start, a 12-wide rank, edges spanning 3+ ranks
{
  const ids = ["S", ...Array.from({length: 12}, (_, i) => "w" + i), "m1", "m2", "m3", "z", "y"];
  const e = [];
  for (let i = 0; i < 12; i++) e.push(["S", "w" + i]);
  e.push(["w0", "m1"], ["w1", "m1"], ["m1", "m2"], ["m2", "m3"], ["m3", "z"],
         ["w5", "z"], ["S", "z"], ["w11", "y"], ["w2", "y"], ["m1", "y"], ["S", "y"]);
  fixtures.push([ids, e]);
}
// pseudo-random layered DAGs with many long edges
let seed = 7; const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
for (let k = 0; k < 25; k++) {
  const n = 8 + Math.floor(rnd() * 30), ids = Array.from({length: n}, (_, i) => "n" + i), e = [];
  for (let j = 1; j < n; j++) for (let t = 0; t < 1 + Math.floor(rnd() * 3); t++) {
    const i = Math.floor(rnd() * j); if (!e.some(x => x[0] === ids[i] && x[1] === ids[j])) e.push([ids[i], ids[j]]);
  }
  fixtures.push([ids, e]);
}
function sample(pts) {                       // points along the path the page draws
  const out = [];
  for (let i = 1; i < pts.length; i++) {
    const [x1, y1] = pts[i - 1], [x2, y2] = pts[i], my = (y1 + y2) / 2;
    for (let t = 0; t <= 1.0001; t += 0.02) {
      const u = 1 - t, y = u*u*u*y1 + 3*u*u*t*my + 3*u*t*t*my + t*t*t*y2;
      const x = x1 === x2 ? x1 : u*u*u*x1 + 3*u*u*t*x1 + 3*u*t*t*x2 + t*t*t*x2;
      out.push([x, y]);
    }
  }
  return out;
}
let through = 0, overlap = 0, edges = 0, wide = 0, size = [0, 0], badPath = 0;
for (const [ids, e] of fixtures) {
  const L = layeredLayout(ids, e);
  size = [L.nodeW, L.nodeH];
  const boxes = ids.map(i => L.pos[i]);
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const [a, b] = [boxes[i], boxes[j]];
    if (a[0] < b[0] + 168 && b[0] < a[0] + 168 && a[1] < b[1] + 48 && b[1] < a[1] + 48) overlap++;
  }
  L.paths.forEach((pts, ei) => {
    edges++;
    if (!L.edgeD[ei].startsWith("M ")) badPath++;
    for (const [x, y] of sample(pts))
      for (const [bx, by] of boxes)
        if (x > bx + 1 && x < bx + 167 && y > by + 1 && y < by + 47) through++;
  });
  const xs = ids.map(i => L.pos[i][0]);
  if (Math.min(...xs) >= 0 && L.width >= Math.max(...xs) + 168) wide++;
}
const F = layeredLayout(...fixtures[0]);
const ys = new Set(fixtures[0][0].slice(1, 13).map(i => F.pos[i][1]));
console.log(JSON.stringify({through, overlap, edges, wide, n: fixtures.length, size,
  oneRow: ys.size, width: F.width, badPath}));"""
            r = subprocess.run([node, "-e", js], capture_output=True, text=True)
            try:
                got = json.loads(r.stdout)
            except ValueError:
                got = {}
                failures.append("tb layout test did not run: " + r.stderr[-300:])
            check(got.get("edges", 0) > 50 and got.get("through") == 0,
                  "tb: no edge segment passes through any node box "
                  f"({got.get('edges')} edges over {got.get('n')} fixtures)")
            check(got.get("overlap") == 0 and got.get("wide") == got.get("n"),
                  "tb: nodes never overlap and all lie inside the drawing")
            check(got.get("oneRow") == 1 and got.get("width", 0) > 770,
                  "tb: a 12-wide rank stays one row (no wrapping, no shrinking)")
            check(got.get("size") == [168, 48] and got.get("badPath") == 0,
                  "tb node size equals the lr node size (never scaled)")
        check("dg-band" not in tbh and "tbLayout" not in tbh,
              "tb: the wrapped-rank bands are gone")
        check(build_payload(root, today, direction="up")["direction"] == "lr",
              "an unknown direction falls back to lr")
        check("LINK_BASE" in lb and "top.location" in lb,
              "template navigates the top window when a link base is set")

        # A title that tries to close the script block must stay inert.
        write("W-008-evil.md", _dossier(
            "W-008", title="</script><script>alert(1)</script>"))
        evil = render(TEMPLATE.read_text(encoding="utf-8"),
                      build_payload(root, today))
        check("</script><script>alert(1)" not in evil,
              "script-close sequence is escaped in the JSON blob")

        # ---- worktree overlay: the live copy beats the checked-out one
        if shutil.which("git"):
            repo = Path(tmp) / "wt" / "repo"
            (repo / ".discovery" / "dossiers").mkdir(parents=True)
            g = lambda *a: _git(repo, "-c", "user.email=t@example.invalid",  # noqa: E731
                                "-c", "user.name=t", *a)
            (repo / ".discovery" / "dossiers" / "W-001-a.md").write_text(
                _dossier("W-001", status="ready", updated="2026-08-10"))
            (repo / ".discovery" / "dossiers" / "W-003-c.md").write_text(
                _dossier("W-003", status="ready", updated="2026-08-09"))
            ok_git = (g("init", "-q", "-b", "main") is not None
                      and g("add", "-A") is not None
                      and g("commit", "-q", "-m", "base") is not None
                      and g("worktree", "add", "-q", "../repo-W-001", "-b", "build/W-001") is not None
                      and g("worktree", "add", "-q", "../repo-W-001-P1", "-b", "build/W-001-p1") is not None
                      and g("worktree", "add", "-q", "../repo-plan-KEY", "-b", "plan/KEY") is not None)
            check(ok_git, "overlay fixture: git worktrees created")
            if ok_git:
                (repo.parent / "repo-W-001" / ".discovery" / "dossiers" / "W-001-a.md").write_text(
                    _dossier("W-001", status="building", updated="2026-08-11",
                             worktree="../repo-W-001"))
                (repo.parent / "repo-W-001-P1" / ".discovery" / "dossiers" / "W-001-a.md").write_text(
                    _dossier("W-001", status="building", updated="2026-08-11",
                             worktree="../repo-W-001"))
                (repo.parent / "repo-plan-KEY" / ".discovery" / "dossiers" / "W-002-b.md").write_text(
                    _dossier("W-002", status="planned", updated="2026-08-11"))
                # The checkout moves W-003 on to done after the plan worktree
                # forked: the worktree's older `ready` copy must not win.
                (repo / ".discovery" / "dossiers" / "W-003-c.md").write_text(
                    _dossier("W-003", status="done", updated="2026-08-12"))
                plain = build_payload(repo / ".discovery", today, worktrees=False)
                live = build_payload(repo / ".discovery", today, worktrees=True)
                by_id = {d["id"]: d for d in live["dossiers"]}
                check(plain["sections"]["ready"] == ["W-001"]
                      and "W-002" not in {d["id"] for d in plain["dossiers"]},
                      "without --worktrees the checkout's copy stands")
                check(by_id["W-001"]["status"] == "building"
                      and by_id["W-001"]["live_from"] == "../repo-W-001",
                      f"W-001 overlaid from the base worktree: {by_id['W-001'].get('live_from')}")
                check(by_id.get("W-002", {}).get("live_from") == "../repo-plan-KEY",
                      "W-002 appears from the plan worktree")
                check(live["sections"]["in_flight"] == ["W-001"]
                      and live["sections"]["planned"] == ["W-002"],
                      f"overlaid sections: {live['sections']}")
                check(any(f["label"] == "live from ../repo-W-001"
                          for f in by_id["W-001"]["flags"]),
                      "overlaid row carries a live-from flag")
                check(by_id["W-003"]["status"] == "done" and "live_from" not in by_id["W-003"],
                      f"an older worktree copy never replaces a newer checkout copy: {by_id['W-003']}")
        else:
            print("selftest: git missing — skipped the worktree overlay check",
                  file=sys.stderr)

        # Empty root renders the empty state without error.
        empty_root = Path(tmp) / "empty" / ".discovery"
        (empty_root / "dossiers").mkdir(parents=True)
        ep = build_payload(empty_root, today)
        check(ep["counts"]["total"] == 0 and ep["next_action"] is None,
              "empty root payload")
        render(TEMPLATE.read_text(encoding="utf-8"), ep)

        # ---- the report on disk: one self-contained file, the dashboard's
        # exact invocation, and --embed
        import contextlib
        import io
        (root / "dossiers" / "W-008-evil.md").unlink()
        out = Path(tmp) / "dash" / "open-work.html"
        ran = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--root", str(root),
             "--worktrees", "--out", str(out), "--quiet"],
            capture_output=True, text=True)
        check(ran.returncode == 0 and ran.stdout == "" and out.is_file(),
              f"the dashboard's invocation writes the report quietly: {ran}")
        check([x.name for x in out.parent.iterdir()] == ["open-work.html"],
              "the report is the only file written")
        page = out.read_text(encoding="utf-8")
        check(not re.search(r'(src|href)="https?:|url\(https?:|@import', page),
              "no external assets")
        check('data-embed="1"' not in page and '"embed": false' in page,
              "the default page keeps its header")
        for gone in ("Needs attention", "Health signals", "live-chip",
                     "open-work-state", "--serve"):
            check(gone not in page, f"the page no longer carries {gone!r}")
        emb = render(TEMPLATE.read_text(encoding="utf-8"),
                     build_payload(root, today, embed=True))
        check('<html lang="en" data-embed="1">' in emb and '"embed": true' in emb,
              "--embed marks the page")
        check("(!EMBED)" in emb, "the template skips its header when embedded")
        ran = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--root", str(root),
             "--out", str(out), "--embed", "--quiet"],
            capture_output=True, text=True)
        check(ran.returncode == 0 and 'data-embed="1"' in out.read_text(encoding="utf-8"),
              f"--embed on the command line: {ran}")
        old = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--serve"],
            capture_output=True, text=True)
        check(old.returncode != 0, "--serve is gone")

        # ---- --uninstall removes everything an earlier --install wrote.
        # The fixture is written by hand, in the shape that --install produced.
        repo = Path(tmp) / "repo"
        (repo / ".discovery" / "dossiers").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True,
                       capture_output=True)
        r_root = repo / ".discovery"
        block = (f"{SENTINEL_OPEN}\n\"/usr/bin/python3\" \"/x/generate_open_work.py\" "
                 f"--root \"{r_root}\" --quiet || true\n{SENTINEL_CLOSE}\n")
        hooks = repo / ".git" / "hooks"
        (hooks / "post-commit").write_text(
            "#!/bin/sh\necho pre-existing\n" + block)
        for name in ("post-merge", "post-checkout", "post-rewrite"):
            (hooks / name).write_text("#!/bin/sh\n" + block)
        (repo / ".claude" / "hooks").mkdir(parents=True)
        (repo / ".claude" / "hooks" / LAUNCHER_NAME).write_text("#!/usr/bin/env python3\n")
        (repo / ".claude" / "settings.local.json").write_text(json.dumps({
            "permissions": {"allow": ["Bash(ls:*)"]},
            "hooks": {"PostToolUse": [
                {"matcher": "Write|Edit|MultiEdit", "hooks": [
                    {"type": "command",
                     "command": f'"/usr/bin/python3" "/r/.claude/hooks/{LAUNCHER_NAME}" --hook'}]},
                {"matcher": "Bash", "hooks": [
                    {"type": "command", "command": "echo mine"}]}]}}))

        def run_uninstall() -> str:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = uninstall(r_root)
            check(rc == 0, f"uninstall rc: {rc}")
            return buf.getvalue()

        said = run_uninstall()
        settings = json.loads((repo / ".claude" / "settings.local.json").read_text())
        check(settings["permissions"]["allow"] == ["Bash(ls:*)"],
              "uninstall keeps the settings it did not write")
        check(len(settings["hooks"]["PostToolUse"]) == 1
              and settings["hooks"]["PostToolUse"][0]["matcher"] == "Bash",
              f"uninstall drops only our PostToolUse entry: {settings}")
        pc = (hooks / "post-commit").read_text()
        check("echo pre-existing" in pc and SENTINEL_OPEN not in pc,
              "a pre-existing git hook stays, minus our block")
        check(not any((hooks / n).exists()
                      for n in ("post-merge", "post-checkout", "post-rewrite")),
              "a git hook that held only our block is removed outright")
        check(not (repo / ".claude" / "hooks" / LAUNCHER_NAME).exists(),
              "the launcher is removed")
        check("nothing to do" in run_uninstall(), "uninstall is idempotent")

        failures.extend(_check_flows_js())
        failures.extend(_check_focus_js())
        tpl = TEMPLATE.read_text(encoding="utf-8")
        lr_html = render(tpl, build_payload(root, today))
        fm_html = render(tpl, build_payload(root, today, graph="focus"))
        check('"graph"' not in lr_html.split('id="open-work-data"')[1]
              .split("</script>")[0],
              "the lr payload carries no graph key")
        check(lr_html == render(tpl, build_payload(
            root, today, direction="lr", graph="flows")),
              "lr output is stable across the new flag")
        fm_data = fm_html.split('id="open-work-data"')[1].split("</script>")[0]
        check('"graph": "focus"' in fm_data or '"graph":"focus"' in fm_data,
              "focus payload sets graph")
        check('class="fm-edges"' not in fm_html and "<path" not in fm_html,
              "focus output has no edge SVG until interaction")

    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    print("selftest:", "FAIL" if failures else "ok",
          file=sys.stderr if failures else sys.stdout)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
