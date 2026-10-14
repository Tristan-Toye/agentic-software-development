#!/usr/bin/env python3
"""Print the opencode config that runs every pipeline agent on one model profile.

The pipeline's agents keep their model in their own markdown frontmatter, written for
GLM: `zai-coding-plan/glm-5.3` (the large tier) or `glm-5.3-flash` (the flash tier).
opencode/model-profiles.json maps each tier to a model per profile — a model ID, or
{"model": ID, ...} with provider options that opencode passes straight through (OpenAI's
`reasoningEffort`, for one). This script reads
the agents opencode actually loads (the files in ~/.config/opencode/agent/, linked to
this repo), finds each one's tier from its frontmatter, and prints the JSON that puts
every agent on the chosen profile's model for that tier:

    OPENCODE_CONFIG_CONTENT="$(python3 scripts/opencode_profile.py openai)" opencode

OPENCODE_CONFIG_CONTENT is the one config layer that overrides an agent file's
`model:` (an OPENCODE_CONFIG file does not). The shell functions `opencode-glm` and
`opencode-openai` wrap exactly that.

# Usage

    opencode_profile.py <profile>          print the config JSON for that profile
    opencode_profile.py --check            every linked agent has a known tier, no link
                                           dangles, every repo agent is linked
    opencode_profile.py --selftest

`--agent-dir` reads another agent directory (default ~/.config/opencode/agent, or
agents/ when only that exists); `--profiles` another profiles file.

Exit codes: 0 printed / clean; 1 --check found a defect; 2 unusable input.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PROFILES = os.path.join(REPO, "opencode", "model-profiles.json")
CONFIG_DIR = os.environ.get("OPENCODE_CONFIG_DIR") or os.path.expanduser("~/.config/opencode")
MODEL_LINE = re.compile(r"^model:\s*(\S+)\s*$", re.M)
AGENT_SOURCES = ("primary-agents", "sub-agents")


def default_agent_dir() -> str:
    for name in ("agent", "agents"):
        path = os.path.join(CONFIG_DIR, name)
        if os.path.isdir(path):
            return path
    return os.path.join(CONFIG_DIR, "agent")


def frontmatter_model(path: str) -> str | None:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    match = MODEL_LINE.search(text[: end if end != -1 else len(text)])
    return match.group(1) if match else None


def linked_agents(agent_dir: str) -> tuple[dict[str, str], list[str]]:
    """{agent name: frontmatter model} for every readable agent file, and the dangling links."""
    agents, dangling = {}, []
    for name in sorted(os.listdir(agent_dir)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(agent_dir, name)
        if not os.path.exists(path):
            dangling.append(name)
            continue
        model = frontmatter_model(path)
        if model:
            agents[name[:-3]] = model
    return agents, dangling


def tier_entry(value) -> dict:
    """A profile's tier value as an agent config entry: {"model": ...} plus any options."""
    return {"model": value} if isinstance(value, str) else dict(value)


def tier_map(profile: dict) -> dict[str, dict]:
    return {tier: tier_entry(v) for tier, v in profile.items() if not tier.startswith("_")}


def tiers(agents: dict[str, str], profiles: dict) -> tuple[dict[str, str], list[str]]:
    base = tier_map(profiles["profiles"][profiles["tiers_from"]])
    by_model = {entry["model"]: tier for tier, entry in base.items()}
    known, unknown = {}, []
    for name, model in agents.items():
        if model in by_model:
            known[name] = by_model[model]
        else:
            unknown.append(f"{name}: model {model} is no tier of the {profiles['tiers_from']!r} profile")
    return known, unknown


def config_for(profile: str, agent_dir: str, profiles: dict) -> dict:
    if profile not in profiles["profiles"]:
        raise ValueError(f"unknown profile {profile!r}; known: {', '.join(profiles['profiles'])}")
    agents, _ = linked_agents(agent_dir)
    known, _ = tiers(agents, profiles)
    target = tier_map(profiles["profiles"][profile])
    return {"agent": {name: target[tier] for name, tier in sorted(known.items()) if tier in target}}


def check(agent_dir: str, profiles: dict, repo: str = REPO) -> list[str]:
    defects = []
    agents, dangling = linked_agents(agent_dir)
    defects += [f"{name}: link points at a file that no longer exists; remove it" for name in dangling]
    _, unknown = tiers(agents, profiles)
    defects += unknown
    for name, target in profiles["profiles"].items():
        missing = set(tier_map(profiles["profiles"][profiles["tiers_from"]])) - set(tier_map(target))
        if missing:
            defects.append(f"profile {name!r} has no model for tier(s) {', '.join(sorted(missing))}")
    # Compared by `<source dir>/<file>`: the links point at the checkout opencode runs from,
    # which need not be the checkout this script runs in.
    linked = {"/".join(os.path.realpath(os.path.join(agent_dir, n)).split(os.sep)[-2:])
              for n in os.listdir(agent_dir) if n.endswith(".md")}
    for src in AGENT_SOURCES:
        folder = os.path.join(repo, src)
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            path = os.path.join(folder, name)
            if name.endswith(".md") and frontmatter_model(path) and f"{src}/{name}" not in linked:
                defects.append(f"{src}/{name} is not linked into {agent_dir}: opencode cannot spawn it")
    return defects


def selftest() -> int:
    failures = []

    def ok(name, cond, detail=""):
        print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"  {detail}"))
        if not cond:
            failures.append(name)

    profiles = {"tiers_from": "glm", "profiles": {
        "glm": {"large": "z/big", "flash": "z/small"},
        "openai": {"_comment": "x", "large": {"model": "o/big", "reasoningEffort": "high"}, "flash": "o/small"}}}
    with tempfile.TemporaryDirectory() as tmp:
        repo, agent_dir = os.path.join(tmp, "repo"), os.path.join(tmp, "agent")
        os.makedirs(os.path.join(repo, "sub-agents"))
        os.makedirs(os.path.join(repo, "primary-agents"))
        os.makedirs(agent_dir)

        def agent(rel, model):
            path = os.path.join(repo, rel)
            with open(path, "w") as fh:
                fh.write(f"---\ndescription: x\nmodel: {model}\n---\nbody\nmodel: not/this\n")
            return path

        lead = agent("primary-agents/plan.md", "z/big")
        worker = agent("sub-agents/worker.md", "z/small")
        agent("sub-agents/unlinked.md", "z/small")
        os.symlink(lead, os.path.join(agent_dir, "dossier.md"))     # the link name is the agent name
        os.symlink(worker, os.path.join(agent_dir, "worker.md"))
        cfg = config_for("openai", agent_dir, profiles)
        ok("profile maps each agent by its tier, under the link's name",
           cfg == {"agent": {"dossier": {"model": "o/big", "reasoningEffort": "high"}, "worker": {"model": "o/small"}}}, cfg)
        ok("only the frontmatter model counts", frontmatter_model(worker) == "z/small")
        defects = check(agent_dir, profiles, repo)
        ok("an unlinked repo agent is a defect", any("unlinked.md is not linked" in d for d in defects), defects)
        os.symlink(os.path.join(repo, "sub-agents", "gone.md"), os.path.join(agent_dir, "gone.md"))
        ok("a dangling link is a defect", any(d.startswith("gone.md") for d in check(agent_dir, profiles, repo)))
        agent("sub-agents/odd.md", "x/unknown")
        os.symlink(os.path.join(repo, "sub-agents", "odd.md"), os.path.join(agent_dir, "odd.md"))
        ok("a model outside the tiers is a defect", any("odd: model x/unknown" in d for d in check(agent_dir, profiles, repo)))
        ok("the odd agent is left on its own model", "odd" not in config_for("openai", agent_dir, profiles)["agent"])
        bad = {"tiers_from": "glm", "profiles": {"glm": profiles["profiles"]["glm"], "half": {"large": "h/big"}}}
        ok("a profile missing a tier is a defect", any("'half' has no model for tier(s) flash" in d for d in check(agent_dir, bad, repo)))
        try:
            config_for("nope", agent_dir, profiles)
            ok("an unknown profile refuses", False)
        except ValueError:
            ok("an unknown profile refuses", True)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("profile", nargs="?")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--agent-dir", default=None)
    ap.add_argument("--profiles", default=PROFILES)
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    try:
        with open(args.profiles, encoding="utf-8") as fh:
            profiles = json.load(fh)
        agent_dir = args.agent_dir or default_agent_dir()
        if args.check:
            defects = check(agent_dir, profiles)
            for d in defects:
                print(d)
            print(f"{'PASS' if not defects else 'FAIL'} — {len(defects)} defect(s) in {agent_dir}")
            return 1 if defects else 0
        if not args.profile:
            ap.error("give a profile, --check or --selftest")
        print(json.dumps(config_for(args.profile, agent_dir, profiles)))
        return 0
    except (OSError, ValueError, KeyError) as err:
        print(f"opencode_profile: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
