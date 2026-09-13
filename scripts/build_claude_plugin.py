#!/usr/bin/env python3
"""Derive the Claude Code plugin surface from the opencode source files.

Two harnesses want the same agents and the same commands, and they disagree
about exactly two front matter keys:

    model        opencode wants "provider/model"; Claude Code wants a Claude
                 model name or alias, and resolves an unknown value by falling
                 back to the inherited model.
    tool policy  opencode scopes tools with a `permission:` map, per path and
                 per command. Claude Code has `tools:` — whole tools, no
                 scoping. It ignores `permission:` entirely.

So one file cannot serve both, and opencode's own schema types `tools` as an
object, which a Claude-style `tools: Read, Write` string would violate. The
answer is one source and one derived artifact, never two maintained copies:

    sub-agents/*.md      ->  claude/agents/*.md
    primary-agents/*.md  ->  claude/commands/*.md

The source keeps its opencode front matter untouched and adds one `claude:`
block that states what Claude Code needs. The body is copied byte-identical.
`commands/*.md` are already harness-neutral and are not generated.

Drift is not documented, it is checked: `--check` regenerates into memory and
fails when a committed file does not match. Run it beside the other validators.

    build_claude_plugin.py            write claude/ from the sources
    build_claude_plugin.py --check    exit 1 when claude/ is stale
    build_claude_plugin.py --selftest check the checker

Exit codes: 0 clean, 1 stale or refused, 2 unusable input.

The blindness guard
-------------------
Blindness in this pipeline is structural. Under opencode a `permission:` entry
that is a map with `"*": deny` IS the boundary — unit-test-author cannot open
an implementation because the tool call is refused. Claude Code cannot express
that: it grants or withholds the whole tool. So the guard is mechanical:

    permission K is the scalar "deny"        -> K's tools MUST be absent
    permission K is a map with "*": deny     -> K's tools MUST be absent
    permission K is a map with "*": allow    -> narrowing is lost, warn only

A source that needs a tool the guard withholds must say so out loud, with a
reason, under `claude.widen:`. The reason is copied into the generated file, so
every place Claude Code runs wider than opencode is written down where the
agent definition lives. Nothing is widened silently.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# One table: opencode permission key -> the Claude Code tools it governs.
# `list` is absent on purpose: opencode's directory listing has no Claude Code
# counterpart, and Claude's Glob is opencode's `glob`, not its `list`. A source
# that denies `list` says nothing about Glob.
PERMISSION_TOOLS: dict[str, tuple[str, ...]] = {
    "read": ("Read",),
    "edit": ("Edit",),
    "write": ("Write",),
    "bash": ("Bash",),
    "glob": ("Glob",),
    "grep": ("Grep",),
    "webfetch": ("WebFetch",),
    "websearch": ("WebSearch",),
    "task": ("Agent",),
    "todowrite": ("TodoWrite",),
}

GENERATED_ROOT = REPO / "claude"
AGENT_SOURCES = REPO / "sub-agents"
COMMAND_SOURCES = REPO / "primary-agents"


class Unusable(Exception):
    """The input cannot be read. Never guessed around."""


# --------------------------------------------------------------------------
# front matter — the nested subset these files use, and the raw lines with it
# --------------------------------------------------------------------------


class FrontMatter:
    """Parsed front matter that also remembers each key's verbatim lines."""

    def __init__(self, data: dict[str, object], raw: dict[str, list[str]], body: str):
        self.data = data
        self.raw = raw
        self.body = body


def split_front_matter(text: str, where: str) -> FrontMatter:
    if not text.startswith("---\n"):
        raise Unusable(f"{where}: file does not start with a '---' front matter fence")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise Unusable(f"{where}: front matter is not closed by a '---' line")
    lines = text[4:end].split("\n")
    body = text[end + 5 :]

    data: dict[str, object] = {}
    raw: dict[str, list[str]] = {}
    top: list[tuple[str, list[str]]] = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            if top:
                top[-1][1].append(line)
            continue
        if line[0] not in " \t":
            key = line.split(":", 1)[0].strip().strip("\"'")
            top.append((key, [line]))
        elif top:
            top[-1][1].append(line)
        else:
            raise Unusable(f"{where}: indented line before any key: {line!r}")

    for key, block in top:
        while block and not block[-1].strip():
            block.pop()
        raw[key] = block
        data[key] = _value_of(block, where)
    return FrontMatter(data, raw, body)


def _value_of(block: list[str], where: str) -> object:
    """A scalar, a folded string, or a nested map — the subset these files use."""
    head = block[0]
    _, _, inline = head.partition(":")
    inline = inline.strip()
    rest = [ln for ln in block[1:] if ln.strip()]

    if inline in (">-", ">", "|", "|-") or (inline == "" and rest and ":" not in rest[0]):
        return " ".join(ln.strip() for ln in rest)
    if inline:
        return inline.strip("\"'")
    if not rest:
        return ""

    out: dict[str, object] = {}
    indent = len(rest[0]) - len(rest[0].lstrip())
    child: list[str] = []
    child_key: str | None = None
    for line in rest:
        here = len(line) - len(line.lstrip())
        if here == indent:
            if child_key is not None:
                out[child_key] = _value_of(child, where)
            child_key = line.split(":", 1)[0].strip().strip("\"'")
            child = [line]
        else:
            child.append(line)
    if child_key is not None:
        out[child_key] = _value_of(child, where)
    return out


# --------------------------------------------------------------------------
# the blindness guard
# --------------------------------------------------------------------------


def forbidden_tools(permission: object) -> dict[str, str]:
    """Tools Claude Code must not grant, mapped to why the source withholds them."""
    out: dict[str, str] = {}
    if not isinstance(permission, dict):
        return out
    for key, value in permission.items():
        tools = PERMISSION_TOOLS.get(key)
        if tools is None:
            continue
        if value == "deny":
            reason = f"`permission.{key}` is deny"
        elif isinstance(value, dict) and value.get("*") == "deny":
            reason = f"`permission.{key}` denies `*` — a blindness boundary"
        else:
            continue
        for tool in tools:
            out[tool] = reason
    return out


def lost_narrowings(permission: object) -> dict[str, str]:
    """Tools Claude Code grants whole where opencode grants them narrowed."""
    out: dict[str, str] = {}
    if not isinstance(permission, dict):
        return out
    for key, value in permission.items():
        tools = PERMISSION_TOOLS.get(key)
        if tools is None or not isinstance(value, dict) or value.get("*") == "deny":
            continue
        narrowed = [k for k, v in value.items() if k != "*" and v == "deny"]
        if not narrowed:
            continue
        for tool in tools:
            out[tool] = f"`permission.{key}` denies {', '.join(sorted(narrowed))}"
    return out


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------


def claude_block(fm: FrontMatter, where: str) -> dict[str, object]:
    block = fm.data.get("claude")
    if not isinstance(block, dict):
        raise Unusable(f"{where}: no `claude:` block — Claude Code needs model and tools")
    return block


def tool_list(block: dict[str, object], where: str) -> list[str]:
    raw = block.get("tools", "")
    if not isinstance(raw, str) or not raw.strip():
        raise Unusable(f"{where}: `claude.tools` is missing or not a list")
    return [t.strip() for t in raw.split(",") if t.strip()]


def render_agent(path: Path) -> str:
    where = path.relative_to(REPO).as_posix()
    fm = split_front_matter(path.read_text(), where)
    block = claude_block(fm, where)
    tools = tool_list(block, where)

    forbidden = forbidden_tools(fm.data.get("permission"))
    widen = block.get("widen") if isinstance(block.get("widen"), dict) else {}
    widened: list[str] = []
    for tool in tools:
        if tool not in forbidden:
            continue
        key = next((k for k, v in PERMISSION_TOOLS.items() if tool in v), tool.lower())
        reason = widen.get(key) if isinstance(widen, dict) else None
        if not reason:
            raise Unusable(
                f"{where}: `claude.tools` grants {tool} but {forbidden[tool]}. "
                f"Withhold it, or record the widening under `claude.widen.{key}`."
            )
        widened.append(f"#   {tool} — {reason}")

    header = [
        "---",
        "# Generated by scripts/build_claude_plugin.py — do not edit.",
        f"# Source: {where}",
    ]
    if widened:
        header.append("# Runs wider than the opencode permission map, on purpose:")
        header.extend(widened)
    for tool, note in sorted(lost_narrowings(fm.data.get("permission")).items()):
        if tool in tools:
            header.append(f"# Narrowing lost — Claude Code cannot scope {tool}: {note}")

    out = header + fm.raw["name"] + fm.raw["description"]
    out.append(f"tools: {', '.join(tools)}")
    for key in ("model", "effort"):
        if block.get(key):
            out.append(f"{key}: {block[key]}")
    out.append("---")
    return "\n".join(out) + "\n" + fm.body + _footer(tools, widen)


def _footer(tools: list[str], widen: object) -> str:
    """The last word on tools, because the body prose describes opencode.

    An agent body written for opencode names its permission map — and for a
    blind agent that description is wrong here, because Claude Code enforces
    the same boundary with a tool list instead. This footer is generated from
    the tool list itself, so it cannot drift from what the agent actually has.
    """
    granted = ", ".join(f"`{t}`" for t in tools)
    lines = ["", "---", ""]
    lines += textwrap.wrap(
        "**Under Claude Code.** Any permission map named above is the opencode "
        "form of your boundary. Here the boundary is the tool list, and your "
        f"tools are exactly: {granted}. Nothing else is available to you, "
        "whatever the text above implies.",
        width=76,
    )
    if isinstance(widen, dict) and widen:
        lines += ["", "Wider than opencode, on purpose:"]
        for key, reason in sorted(widen.items()):
            lines += textwrap.wrap(
                f"`{key}` — {reason}", width=76, initial_indent="- ", subsequent_indent="  "
            )
    return "\n".join(lines) + "\n"


def render_command(path: Path) -> str:
    where = path.relative_to(REPO).as_posix()
    fm = split_front_matter(path.read_text(), where)
    block = fm.data.get("claude") if isinstance(fm.data.get("claude"), dict) else {}

    # No `name:` — the slash command takes the file's basename, so
    # primary-agents/plan.md is /plan, the name the README already documents.
    out = [
        "---",
        "# Generated by scripts/build_claude_plugin.py — do not edit.",
        f"# Source: {where}",
    ]
    out += fm.raw["description"]
    for key in ("argument-hint",):
        if key in fm.raw:
            out += fm.raw[key]
    if isinstance(block, dict) and block.get("argument-hint"):
        out.append(f"argument-hint: {block['argument-hint']}")
    out.append("---")
    return "\n".join(out) + "\n" + fm.body


def render_manifest(agents: list[Path]) -> str:
    """The manifest's `agents` array, regenerated.

    Claude Code rejects a directory here — `agents` takes explicit file paths —
    so adding a sub-agent would otherwise mean remembering to edit the manifest
    by hand. It is derived instead, and `--check` catches a stale list.
    """
    path = REPO / ".claude-plugin" / "plugin.json"
    manifest = json.loads(path.read_text())
    manifest["agents"] = [f"./{a.relative_to(REPO).as_posix()}" for a in sorted(agents)]
    return json.dumps(manifest, indent=2) + "\n"


def build() -> dict[Path, str]:
    out: dict[Path, str] = {}
    for src in sorted(AGENT_SOURCES.glob("*.md")):
        out[GENERATED_ROOT / "agents" / src.name] = render_agent(src)
    for src in sorted(COMMAND_SOURCES.glob("*.md")):
        out[GENERATED_ROOT / "commands" / src.name] = render_command(src)
    manifest = REPO / ".claude-plugin" / "plugin.json"
    out[manifest] = render_manifest([p for p in out if p.parent.name == "agents"])
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="fail when claude/ is stale")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    try:
        generated = build()
    except Unusable as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    stale: list[str] = []
    for path, text in generated.items():
        current = path.read_text() if path.exists() else None
        if current == text:
            continue
        rel = path.relative_to(REPO).as_posix()
        if args.check:
            stale.append(rel if current is not None else f"{rel} (missing)")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f"wrote {rel}")

    expected = set(generated)
    for path in sorted(GENERATED_ROOT.rglob("*.md")):
        if path in expected:
            continue
        rel = path.relative_to(REPO).as_posix()
        if args.check:
            stale.append(f"{rel} (orphan — its source is gone)")
        else:
            path.unlink()
            print(f"removed {rel}")

    if stale:
        print("claude/ is stale — re-run scripts/build_claude_plugin.py:", file=sys.stderr)
        for item in stale:
            print(f"  {item}", file=sys.stderr)
        return 1
    if args.check:
        print(f"claude/ matches its {len(generated)} sources")
    return 0


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

_BLIND = """---
name: blind
description: >-
  A blind agent.
model: zai/flash
permission:
  read:
    "*": deny
    "tests/*": allow
  bash: deny
claude:
  model: haiku
  tools: Write
---
Body stays.
"""

_LEAK = _BLIND.replace("tools: Write", "tools: Read, Write")
_WIDE = _LEAK.replace(
    "  tools: Read, Write",
    "  tools: Read, Write\n  widen:\n    read: staging only, guarded by a deny rule",
)


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    fm = split_front_matter(_BLIND, "t")
    check("description folds", fm.data["description"] == "A blind agent.")
    check("body verbatim", fm.body == "Body stays.\n")
    check("nested map", fm.data["permission"]["read"]["*"] == "deny")  # type: ignore[index]
    check("scalar deny", fm.data["permission"]["bash"] == "deny")  # type: ignore[index]

    forbidden = forbidden_tools(fm.data["permission"])
    check("star-deny forbids Read", "Read" in forbidden)
    check("scalar deny forbids Bash", "Bash" in forbidden)
    check("Write stays grantable", "Write" not in forbidden)

    narrowed = lost_narrowings({"bash": {"*": "allow", "git push*": "deny"}})
    check("narrowing reported", narrowed.get("Bash", "").endswith("git push*"))
    check("blindness is not a narrowing", not lost_narrowings(fm.data["permission"]))

    tmp = REPO / "scripts" / ".selftest.md"
    try:
        tmp.write_text(_BLIND)
        text = render_agent(tmp)
        check("banner present", "do not edit" in text)
        check("tools emitted", "\ntools: Write\n" in text)
        check("claude model wins", "\nmodel: haiku\n" in text and "zai/flash" not in text)
        check("opencode keys dropped", "permission:" not in text.split("\n---\n")[0])
        check("body preserved", "\nBody stays.\n" in text)
        footer = " ".join(text.split("\n---\n")[-1].split())
        check("footer states tools", "your tools are exactly: `Write`." in footer)

        tmp.write_text(_LEAK)
        try:
            render_agent(tmp)
            failures.append("leak refused")
        except Unusable as exc:
            check("leak names the tool", "Read" in str(exc))

        tmp.write_text(_WIDE)
        text = render_agent(tmp)
        check("widening allowed", "\ntools: Read, Write\n" in text)
        check("widening recorded", text.count("staging only") == 2)
    finally:
        tmp.unlink(missing_ok=True)

    if failures:
        print("selftest FAILED:", file=sys.stderr)
        for name in failures:
            print(f"  {name}", file=sys.stderr)
        return 1
    print("selftest passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
