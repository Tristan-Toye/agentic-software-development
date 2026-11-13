#!/usr/bin/env python3
"""c2 drift check (D0 spike, VC-01 slice of VR-06).

Compares the Cargo workspace `members` with the containers drawn in a c2 Mermaid file.
Also compares the number word in the title ("six crates") with the member count.
Stdlib only. Exit 0 when equal, 1 on drift, 2 on a usage or parse error.

  c2_drift.py --repo PATH [--c2 PATH] [--ignore NAME ...] [--json]
  c2_drift.py --selftest
"""
import argparse, json, re, sys
from pathlib import Path

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


def parse_members(cargo_toml: str) -> list:
    """Return crate names from the `[workspace] members = [...]` array (last path segment)."""
    m = re.search(r"^members\s*=\s*\[(.*?)\]", cargo_toml, re.S | re.M)
    if not m:
        raise ValueError("no `members = [...]` array in Cargo.toml")
    body = re.sub(r"#.*", "", m.group(1))
    return [p.strip().rstrip("/").split("/")[-1] for p in re.findall(r'"([^"]+)"', body)]


def parse_containers(c2_md: str) -> tuple:
    """Return (container labels, title). A container is a Mermaid node `id["label"]`."""
    block = re.search(r"```mermaid\n(.*?)```", c2_md, re.S)
    if not block:
        raise ValueError("no mermaid block in c2 file")
    labels = re.findall(r'^\s*[A-Za-z_][\w-]*\["([^"]+)"\]\s*$', block.group(1), re.M)
    title = next((l for l in c2_md.splitlines() if l.startswith("# ")), "")
    return labels, title


def compare(members, containers, title, ignore=()):
    ign = set(ignore)
    mem, con = set(members) - ign, set(containers) - ign
    out = {"members": sorted(mem), "containers": sorted(con),
           "missing_from_diagram": sorted(mem - con), "not_a_member": sorted(con - mem)}
    word = next((w for w in re.findall(r"[a-z]+", title.lower()) if w in WORDS), None)
    out["title_count"] = WORDS[word] if word else None
    out["title_count_matches"] = (WORDS[word] == len(mem)) if word else None
    out["drift"] = bool(out["missing_from_diagram"] or out["not_a_member"] or out["title_count_matches"] is False)
    return out


def selftest() -> int:
    cargo = '[workspace]\nmembers = [\n  "crates/a",\n  # "crates/zz",\n  "crates/b-c",\n  "crates/d",\n]\n'
    md = '# c2 - the two crates\n```mermaid\nflowchart LR\n    a["a"]\n    bc["b-c"]\n    x["x"]\n    a --> bc\n```\n'
    assert parse_members(cargo) == ["a", "b-c", "d"], parse_members(cargo)
    labels, title = parse_containers(md)
    assert labels == ["a", "b-c", "x"], labels
    r = compare(parse_members(cargo), labels, title)
    assert r["missing_from_diagram"] == ["d"] and r["not_a_member"] == ["x"]
    assert r["title_count"] == 2 and r["title_count_matches"] is False and r["drift"]
    ok = compare(["a"], ["a"], "# c2 - one crate")
    assert not ok["drift"] and ok["title_count_matches"] is True
    assert not compare(["a", "wh"], ["a"], "# c2", ignore=["wh"])["drift"]
    for bad in ("no members here", ):
        try:
            parse_members(bad); raise SystemExit("expected ValueError")
        except ValueError:
            pass
    print("selftest ok")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="workspace root holding Cargo.toml")
    ap.add_argument("--c2", help="c2 file (default: <repo>/docs/architecture/c2-containers.md)")
    ap.add_argument("--ignore", nargs="*", default=[], help="crate names to skip (for example workspace-hack)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.repo:
        ap.error("--repo is required")
    try:
        repo = Path(a.repo)
        members = parse_members((repo / "Cargo.toml").read_text())
        c2 = Path(a.c2) if a.c2 else repo / "docs/architecture/c2-containers.md"
        labels, title = parse_containers(c2.read_text())
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    r = compare(members, labels, title, a.ignore)
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        print(f"members: {len(r['members'])}  containers in c2: {len(r['containers'])}")
        print("missing from the diagram:", ", ".join(r["missing_from_diagram"]) or "none")
        print("in the diagram, not a member:", ", ".join(r["not_a_member"]) or "none")
        if r["title_count"] is not None:
            print(f"title says {r['title_count']}; members are {len(r['members'])}: "
                  + ("match" if r["title_count_matches"] else "MISMATCH"))
        print("DRIFT" if r["drift"] else "OK")
    return 1 if r["drift"] else 0


if __name__ == "__main__":
    sys.exit(main())
