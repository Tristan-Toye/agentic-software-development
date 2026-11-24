#!/usr/bin/env python3
"""Visual review: the full diagram set of a dossier that is already past stage 3, as a PREVIEW only.

    vr_preview_all.py REPO DOSSIER-ID [--base REV]
    vr_preview_all.py --selftest

Stage 3 now shows every diagram part (L0, L1, L2, decision flow, sequence, architecture affected, architecture new), each real content
or a stated `none - <reason>`. A dossier approved before that rule has only some of them. This builds the whole set again so the owner
can look at every part and comment on its usefulness, without touching the approved review:

  * everything goes to `.discovery/visual-review/<ID>/preview-all/` (a `.gitignore` of `*` and a `PREVIEW-ONLY` marker inside);
  * `review.json`, `l0.json`, `l1.json`, `l2.json`, `decision.json`, `arch/` and `seq-preview/` of the dossier directory are only READ;
    their hashes never change, the approval is never touched;
  * no model runs: L0/L1/L2/decision/new drafts are copied from the stage-3 files, the sequence drafts are built from l1.json
    (`vr_promote.build_seq_drafts`, no driver involved), every affected existing diagram (the dossier's `Diagrams affected:` stems plus
    the diagrams anchored in files changed since --base) is shown as it is committed, marked an auto draft, and a part with no content
    gets an automatic `none - <reason>` in `parts.json` that says the preview was generated after the fact;
  * Phase 9 and every gate ignore the directory (they read the dossier directory's own files and its `arch/` only). Nothing in it
    counts as an approval; the dashboard shows it as the separate "All diagrams (preview)" view with a comment box per part
    (comments are kept in `preview-all/comments.json`, not in review.json).

Exit codes: 0 written (the JSON on stdout lists `gaps`, the parts that still lack content or a reason); 2 bad arguments or no
visual-review directory for the dossier. No third-party imports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vr_graph  # noqa: E402
import vr_promote  # noqa: E402
import validate_pipeline  # noqa: E402

VR_REL = os.path.join(".discovery", "visual-review")
PREVIEW_ALL = "preview-all"
AUTO = "generated after stage 3 as a preview"


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _dump(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def build(repo: str, dossier_id: str, base: str | None) -> dict:
    root = Path(repo).resolve()
    src = os.path.join(str(root), VR_REL, dossier_id)
    if not os.path.isdir(src):
        raise SystemExit("no %s for %s: nothing to preview" % (VR_REL, dossier_id))
    out = os.path.join(src, PREVIEW_ALL)
    if os.path.isdir(out):
        shutil.rmtree(out)           # the preview is regenerated whole; the dossier's own files are never touched
    os.makedirs(out)
    with open(os.path.join(out, ".gitignore"), "w", encoding="utf-8") as fh:
        fh.write("*\n")
    with open(os.path.join(out, "PREVIEW-ONLY"), "w", encoding="utf-8") as fh:
        fh.write("A preview of every diagram part. Not an approval; Phase 9 ignores this directory.\n")
    parts: dict = {"schema_version": vr_graph.PARTS_VERSION, "plan": {"affected": [], "new": []}}
    done: dict = {}
    for name in ("l0.json", "l1.json", "l2.json", "decision.json"):
        if os.path.isfile(os.path.join(src, name)):
            shutil.copyfile(os.path.join(src, name), os.path.join(out, name))
            done[name] = "copied"
    if "decision.json" not in done:
        parts["decision_none"] = "No decisions in this change: no decision flow was drawn at stage 3 (%s)" % AUTO
    # sequence: built from l1.json with no driver
    l1p = os.path.join(src, "l1.json")
    seq = {}
    if os.path.isfile(l1p):
        try:
            _, seq, _ = vr_promote.build_seq_drafts(_load(l1p), set(), skip_new_stem=True)
        except (ValueError, OSError, KeyError, TypeError, AttributeError):
            seq = {}
    if seq:
        vr_promote.write_previews(out, seq)
        done["seq-preview"] = sorted(seq)
    else:
        parts["sequence_none"] = "No sequence: no L1 flow adds or changes a step, so there is no flow to draw (%s)" % AUTO
    # architecture affected: each is shown as committed
    info = validate_pipeline.affected_diagrams_stage3(root, base or "HEAD", dossier_id)
    arch_docs = root / "docs" / "architecture"
    parts["plan"]["affected"] = [a["stem"] for a in info["affected"]]
    unchanged = []
    for a in info["affected"]:
        stem = a["stem"]
        doc = arch_docs / (stem + ".json")
        if a["draftable"] and doc.is_file():
            g = _load(str(doc))
            meta = g.setdefault("meta", {})
            meta["updates_existing"] = True
            meta["base_docs_hash"] = hashlib.sha256(doc.read_bytes()).hexdigest()
            meta["auto_draft"] = "the committed diagram as it is: affected by %s; the author drafts the change when the preview is approved (%s)" % (
                ", ".join(a["why"]), AUTO)
            _dump(os.path.join(out, "arch", stem + ".json"), g)
            done.setdefault("affected", []).append(stem)
        else:
            unchanged.append({"stem": stem, "reason": "affected via %s; a %s diagram that is only shown as committed here (%s)" % (
                ", ".join(a["why"]), a.get("view") or "block", AUTO)})
    if unchanged:
        parts["unchanged"] = unchanged
    if not info["affected"]:
        parts["affected_none"] = ("No architecture diagram affected: %s (%s)" % (
            info["note"] or info["affected_none_reason"] or "no diagram names this change in its anchors or in the dossier", AUTO))
    # architecture new
    new = list(info["new"] or [])
    parts["plan"]["new"] = new
    for stem in new:
        for cand in (os.path.join(src, "arch", stem + ".json"), str(arch_docs / (stem + ".json"))):
            if os.path.isfile(cand):
                os.makedirs(os.path.join(out, "arch"), exist_ok=True)
                shutil.copyfile(cand, os.path.join(out, "arch", stem + ".json"))
                done.setdefault("new", []).append(stem)
                break
    if not new:
        parts["new_none"] = "No new diagram: %s (%s)" % (info["new_none_reason"] or "the dossier names no new diagram", AUTO)
    _dump(os.path.join(out, "parts.json"), parts)
    decision = "yes" if os.path.isfile(os.path.join(out, "decision.json")) else "no"
    gaps = [i for i in vr_graph.parts_issues(out, decision=decision, new=new, affected=parts["plan"]["affected"])]
    return {"ok": True, "dir": os.path.relpath(out, str(root)), "written": done, "gaps": gaps}


def selftest() -> int:
    fails: list = []

    def ok(name, cond, detail=""):
        print("%s  %s" % ("PASS" if cond else "FAIL", name))
        if not cond:
            fails.append(name + " " + str(detail))

    def git(d, *a):
        import subprocess
        subprocess.run(["git", "-C", d, *a], check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})

    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "r")
        os.makedirs(os.path.join(d, "src"))
        os.makedirs(os.path.join(d, "docs", "architecture"))
        open(os.path.join(d, "src", "a.rs"), "w").write("")
        _dump(os.path.join(d, "docs", "architecture", "c4-a.json"),
              {"view": "c4", "title": "A", "nodes": [{"id": "n1", "label": "A", "kind": "component",
                                                      "evidence": [{"tier": "source", "path": "src/a.rs", "line": 1}]}], "edges": []})
        git(d, "init", "-q", "-b", "main")
        git(d, "add", "-A")
        git(d, "commit", "-qm", "base")
        git(d, "branch", "base")
        open(os.path.join(d, "src", "a.rs"), "w").write("x")
        git(d, "add", "-A")
        git(d, "commit", "-qm", "change")
        os.makedirs(os.path.join(d, ".discovery", "dossiers"))
        open(os.path.join(d, ".discovery", "dossiers", "W-001-x.md"), "w").write(
            "## Approach\nDiagrams affected: none\nDiagrams new: none\n")
        vr = os.path.join(d, VR_REL, "W-001")
        _dump(os.path.join(vr, "l0.json"), {"view": "L0", "nodes": []})
        _dump(os.path.join(vr, "review.json"), {"hashes": {"L0": "abc"}, "verdicts": []})
        before = {f: hashlib.sha256(open(os.path.join(vr, f), "rb").read()).hexdigest() for f in os.listdir(vr)}
        res = build(d, "W-001", "base")
        out = os.path.join(vr, PREVIEW_ALL)
        ok("preview dir written", os.path.isfile(os.path.join(out, "parts.json")) and os.path.isfile(os.path.join(out, "PREVIEW-ONLY")), res)
        ok("l0 copied", os.path.isfile(os.path.join(out, "l0.json")))
        ok("anchored diagram shown as committed with its base hash", os.path.isfile(os.path.join(out, "arch", "c4-a.json"))
           and _load(os.path.join(out, "arch", "c4-a.json"))["meta"]["updates_existing"], res)
        ok("none parts carry reasons, no gap for decision/sequence/new",
           not [g for g in res["gaps"] if g.get("path") in ("decision", "sequence", "new")], res["gaps"])
        after = {f: hashlib.sha256(open(os.path.join(vr, f), "rb").read()).hexdigest() for f in before}
        ok("review.json and the stage-3 files are untouched", before == after)
        ok("no new files beside preview-all", sorted(os.listdir(vr)) == sorted(list(before) + [PREVIEW_ALL]), os.listdir(vr))
        res2 = build(d, "W-001", "base")
        ok("regenerating is idempotent", res2["written"] == res["written"])
        # phase 9 side: the gate reads the dossier dir only, so the preview's drafts are no stage-3 settlement
        unchanged, drafts = validate_pipeline.stage3_settlements(Path(d), "W-001")
        ok("Phase 9 ignores preview-all", not unchanged and not drafts, (unchanged, drafts))
    for f in fails:
        print("FAIL  " + f)
    print("%s - vr_preview_all %s" % ("PASS" if not fails else "FAIL", "behaves" if not fails else "misbehaves"))
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("repo", nargs="?")
    ap.add_argument("dossier", nargs="?")
    ap.add_argument("--base", default=None, help="the revision the change started from (anchored diagrams are found against it)")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not (args.repo and args.dossier):
        ap.error("REPO and DOSSIER-ID are required")
    if not vr_promote.re.match(r"^[\w.-]+$", args.dossier):
        ap.error("dossier id may hold only letters, digits, . _ -")
    res = build(args.repo, args.dossier, args.base)
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
