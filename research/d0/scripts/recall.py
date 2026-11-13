#!/usr/bin/env python3
"""D0 recall check: would metadata / cargo-modules / fn items surface what a merged change touched? stdlib only."""
import re, subprocess, sys, json, os
CH, W = sys.argv[1], sys.argv[2]
def git(*a): return subprocess.run(["/usr/bin/git", "-C", CH, *a], capture_output=True, text=True).stdout
members = {l.split("/")[1] for l in open(CH + "/Cargo.toml").read().split("members = [")[1].split("]")[0].replace('"', "").replace(",", "").split() if "/" in l}
dots = {}
for c in ["harness", "platform", "protocol", "composition-root", "test-support"]:
    dots[c] = open(f"{W}/cmfull-{c}.dot").read()
def mods_of(c): return set(re.findall(r'"([^"]+)" \[label="[a-z()]* ?mod\|', dots[c])) | set(re.findall(r'"([^"]+)" \[label="pub(?:\([a-z]+\))? mod\|', dots[c]))
def fns_of(c): return {m.rsplit("::", 1)[-1] for m in re.findall(r'"([^"]+)" \[label="[^"]*fn\|', dots[c])}
res = []
for h, label in [("c240c954", "#391 platform pool metrics"), ("7b433c2c", "#330 llm key denylist"), ("d69d30d5", "#340 git mirror maintenance")]:
    files = [f for f in git("diff", "--name-only", h + "^1", h, "--", "crates").split() if "/tests/" not in f]
    crates = {f.split("/")[1] for f in files}
    src = [f for f in files if re.match(r"crates/[^/]+/src/.*\.rs$", f)]
    # layer 2: file -> module path, lib target only
    hit, miss = [], []
    for f in src:
        c, rest = f.split("/")[1], f.split("/src/", 1)[1][:-3]
        crate = c.replace("-", "_")
        if rest in ("lib", "main") : mp = crate
        elif rest.startswith("bin/") or rest.startswith("examples/"): miss.append((f, "bin target, not in --lib")); continue
        else:
            parts = rest.split("/"); parts = parts[:-1] if parts[-1] == "mod" else parts; mp = "::".join([crate] + parts)
        (hit if (c in dots and (mp in mods_of(c) or mp == crate)) else miss).append((f, mp) if c in dots else (f, "crate not extracted"))
    # fn names from hunk headers
    fns = set()
    d = git("diff", "-U0", h + "^1", h, "--", "crates/*/src/*.rs", "crates/*/src/**/*.rs")
    cur = None
    for l in d.splitlines():
        if l.startswith("+++ b/"): cur = l[6:].split("/")[1]
        m = re.match(r"@@[^@]*@@.*?\bfn ([a-z_0-9]+)", l)
        if m and cur in dots: fns.add((cur, m.group(1)))
    fhit = {x for x in fns if x[1] in fns_of(x[0])}
    res.append({"change": label, "commit": h, "touched_crates": sorted(crates), "crate_recall": f"{len(crates & members)}/{len(crates)}",
        "src_files": len(src), "module_recall": f"{len(hit)}/{len(src)}", "module_misses": miss[:6],
        "fn_names_from_hunks": len(fns), "fn_in_ra_graph": f"{len(fhit)}/{len(fns)}"})
print(json.dumps(res, indent=1))
