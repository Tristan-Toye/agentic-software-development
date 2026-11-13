#!/usr/bin/env python3
"""Count orchestrator requests that only do build-log bookkeeping.

Re-run:  python3 ledger/optimisations/scripts/bookkeeping_requests.py [--since 2026-11-09] [--until 2026-11-13] [--json]

A "request" is one assistant API message (all JSONL lines with the same
message.id are merged). "Tool-using" = it holds at least one tool_use block.
"Bookkeeping-only" = every tool_use is a Bash call whose every command segment is
`dossier_edit.py log` or `ask_owner.py note`. Sidechain (sub-agent) lines are
skipped: only orchestrator sessions count. Read-only; prints no transcript text
beyond the first 60 chars of a log line's kind token.
"""
import argparse, collections, glob, json, os, re, sys

ROOTS = ["~/.claude-main/projects", "~/.claude-gmail/projects"]
LOG_RE = re.compile(r"dossier_edit\.py\s+log\b")
NOTE_RE = re.compile(r"ask_owner\.py\s+note\b")
import shlex
KIND_TOKEN = re.compile(r"^\W*([A-Z][A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)*)\b")


# Classification used in the dossier. A = the text is a tool event or a script's own output (no judgement);
# B = needs the model's judgement text; C = no consumer found, humans only. Unknown kinds count as B (conservative).
CLASS_A = {"RETURNED", "WORKFLOW", "SUITE", "SUITE-RUN", "RED-RUN", "STUB-RED-RUN", "CI", "SYNC", "MERGE", "PR",
           "WORKTREES", "FAILURE-SIGNALS", "LOG-SIGNALS", "SECURITY-TRIGGERS", "BLAST-RADIUS", "EXCLUDED", "RULES",
           "FLAKE-RISK", "TESTS-LENS", "PAYLOAD-LINT", "ADMISSION", "CHECKLIST-IDS", "REVIEW-LENS-ROUND", "QUESTION",
           "OWNER-RULING", "DEFERRED-FIXED", "DEFERRED-FIX", "DEFERRED-SOURCE", "DEFERRED-ASKED", "DEFERRED-DOSSIER",
           "COMMIT", "CONTRACT-COMMIT", "SPAWNED", "<stdin>"}
CLASS_C = {"ENV", "Phase-narrative", "PHASE", "WAVE", "other-prose", "STABILITY"}


def class_of(kind: str) -> str:
    return "A" if kind in CLASS_A else "C" if kind in CLASS_C else "B"


def kind_of(line: str) -> str:
    """The leading UPPER-CASE token of a log line ('RETURNED', 'DEFERRED-FIX', 'ARBITRATION'); 'Phase' for
    'Phase N: ...' prose; 'P1 RETURNED'-style lines are folded into RETURNED; else 'other-prose'."""
    s = line.strip().lstrip("-").strip()
    if re.search(r"\bRETURNED\b", s[:60]):
        return "RETURNED"
    if re.match(r"^r\d+[a-z]?\s+(style|architecture|performance|tests)\b", s):
        return "REVIEW-LENS-ROUND"
    if re.match(r"^D-\d+\b", s):
        return "DEFERRED-LEDGER-ITEM"
    m = KIND_TOKEN.match(s)
    if not m:
        return "other-prose"
    k = m.group(1)
    if k in ("Phase", "Stage", "Step"):
        return "Phase-narrative"
    if k.isupper() or "-" in k:
        return k
    return "other-prose"


def segments(cmd: str):
    """Top-level command segments; quotes and heredoc bodies are respected."""
    cmd = cmd.replace("\\\n", " ")
    cmd = re.split(r"<<-?\s*['\"]?\w+['\"]?", cmd)[0] + (" " if "<<" in cmd else "")
    out, cur, q, i = [], "", None, 0
    while i < len(cmd):
        c = cmd[i]
        if q:
            cur += c
            if c == q:
                q = None
        elif c in "'\"":
            q = c
            cur += c
        elif c == "\n" or c == ";" or cmd.startswith("&&", i) or cmd.startswith("||", i):
            out.append(cur)
            cur = ""
            i += 1 if (c in ";\n") else 2
            continue
        else:
            cur += c
        i += 1
    out.append(cur)
    keep = []
    for x in out:
        x = x.strip()
        # skip comments, VAR=value assignments, cd, echo/true separators: they cost nothing
        if not x or x.startswith("#") or re.match(r"^[A-Za-z_]\w*=\S*$", x) or re.match(r"^(cd|export|true|:)\b", x):
            continue
        keep.append(x)
    return keep


def _flatten(cmd: str) -> str:
    """Unquoted newlines become ' ; ' so shlex sees separate commands; heredoc bodies are cut."""
    cmd = cmd.replace("\\\n", " ")
    cmd = re.split(r"<<-?\s*['\"]?\w+['\"]?", cmd)[0]
    out, q = [], None
    for c in cmd:
        if q:
            out.append(c)
            if c == q:
                q = None
        elif c in "'\"":
            q = c
            out.append(c)
        elif c == "\n":
            out.append(" ; ")
        else:
            out.append(c)
    return "".join(out)


def log_lines(cmd: str):
    """The line arguments of every `dossier_edit.py log DOSSIER ARG...` in a command; '<stdin>' for `-`."""
    try:
        lex = shlex.shlex(_flatten(cmd), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        return ["<unparsed>"]
    out = []
    for i, t in enumerate(toks):
        if t.endswith("dossier_edit.py") and i + 1 < len(toks) and toks[i + 1] == "log":
            args = []
            for x in toks[i + 3:]:           # skip 'log' and the dossier
                if re.fullmatch(r"[;&|<>()]+", x):
                    break
                if re.fullmatch(r"\d", x):     # the 2 of `2>&1`
                    continue
                args.append(x)
            if args == ["-"]:
                out.append("<stdin>")
            else:
                out += [x for x in args if x != "-"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-11-09")
    ap.add_argument("--until", default="2026-11-13")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    files = []
    for r in ROOTS:
        files += glob.glob(os.path.join(os.path.expanduser(r), "**", "*.jsonl"), recursive=True)
    tot_files = used = 0
    reqs = tool_reqs = book = book_log = book_note = book_mixed = 0
    chain_ok = 0          # bookkeeping request directly after a request whose tools included Bash
    prev_bash_pairs = 0
    cache_read = book_cache_read = 0
    kinds = collections.Counter()
    any_lg = any_lg_cr = near = near_cr = 0   # tier 2: log/note present; tier 3: log/note + only trivial companions
    TRIVIAL = re.compile(r"^(echo|sleep|python3 -|ls|cat|test|\[|dossier_edit\.py section|.*dossier_edit\.py (section|set|add)\b)")
    lines_per_call = collections.Counter()
    calls_seen = 0
    toolsearch_only = toolsearch_cr = 0
    req_class = collections.Counter()
    next_kind = collections.Counter()
    kinds_all = collections.Counter()
    seen_ids = set()
    for f in files:
        tot_files += 1
        msgs = collections.OrderedDict()
        try:
            for l in open(f, errors="replace"):
                if '"type":"assistant"' not in l:
                    continue
                d = json.loads(l)
                if d.get("type") != "assistant" or d.get("isSidechain"):
                    continue
                ts = (d.get("timestamp") or "")[:10]
                if not (a.since <= ts < a.until):
                    continue
                m = d["message"]
                if m.get("model") == "<synthetic>":
                    continue
                e = msgs.setdefault(m.get("id") or d["uuid"], {"tools": [], "usage": m.get("usage") or {}})
                for c in m.get("content") or []:
                    if c.get("type") == "tool_use":
                        e["tools"].append(c)
                if m.get("usage"):
                    e["usage"] = m["usage"]
        except Exception as ex:
            print("skip", os.path.basename(f), ex, file=sys.stderr)
            continue
        if not msgs:
            continue
        used += 1
        prev = None
        order = list(msgs.values())
        for i_, e_ in enumerate(order):
            e_["next_tools"] = next((x["tools"] for x in order[i_ + 1:] if x["tools"]), None)
        for mid, e in msgs.items():
            if not e["tools"]:
                prev = e
                continue
            reqs += 1
            tool_reqs += 1
            cr = (e["usage"] or {}).get("cache_read_input_tokens") or 0
            cache_read += cr
            only = True
            if all(t.get("name") == "ToolSearch" for t in e["tools"]):
                toolsearch_only += 1
                toolsearch_cr += cr
            for t in e["tools"]:
                c = (t.get("input") or {}).get("command", "") if t.get("name") == "Bash" else ""
                if t.get("id") not in seen_ids and LOG_RE.search(c) and "--help" not in c:
                    seen_ids.add(t.get("id"))
                    for x in log_lines(c):
                        kinds_all[kind_of(x) if not x.startswith("<") else x] += 1
            allcmds = [(t.get("input") or {}).get("command", "") if t.get("name") == "Bash" else "" for t in e["tools"]]
            if any((LOG_RE.search(c) or NOTE_RE.search(c)) and "--help" not in c for c in allcmds):
                any_lg += 1
                any_lg_cr += cr
                segs_all = [x for c in allcmds for x in segments(c)]
                other_tools = [t for t in e["tools"] if t.get("name") not in ("Bash", "mcp__plugin_agentic-software-development_nav__phase_checkpoint")]
                extra = [x for x in segs_all if not (LOG_RE.search(x) or NOTE_RE.search(x))]
                if not other_tools and all(TRIVIAL.match(re.sub(r"^\S*python3\s+\S*/", "python3 ", x)) or TRIVIAL.match(x) for x in extra):
                    near += 1
                    near_cr += cr
            has_log = has_note = False
            for t in e["tools"]:
                cmd = (t.get("input") or {}).get("command", "") if t.get("name") == "Bash" else ""
                segs = segments(cmd)
                if not segs or not all((LOG_RE.search(s) or NOTE_RE.search(s)) and "--help" not in s for s in segs):
                    only = False
                    break
                has_log |= bool(LOG_RE.search(cmd))
                has_note |= bool(NOTE_RE.search(cmd))
            if only:
                book += 1
                book_cache_read += cr
                if has_log and has_note:
                    book_mixed += 1
                elif has_log:
                    book_log += 1
                else:
                    book_note += 1
                rk = set()
                for t in e["tools"]:
                    cmd = t["input"]["command"]
                    if NOTE_RE.search(cmd):
                        rk.add("B")
                    if LOG_RE.search(cmd):
                        rk |= {class_of(kind_of(x) if not x.startswith("<") else x) for x in log_lines(cmd)} or {"B"}
                req_class["A only" if rk == {"A"} else "C only" if rk == {"C"} else ("owner note present" if has_note else "B log lines, no note") if "B" in rk else "A+C"] += 1
                for t in e["tools"]:
                    cmd = t["input"]["command"]
                    if LOG_RE.search(cmd):
                        ls = log_lines(cmd)
                        lines_per_call[min(len(ls), 6)] += 1
                        calls_seen += 1
                        for x in ls:
                            kinds[kind_of(x) if not x.startswith("<") else x] += 1
                    if NOTE_RE.search(cmd):
                        kinds["(owner note)"] += 1
                nt = e.get("next_tools")
                if nt is None:
                    next_kind["none (end of session/turn)"] += 1
                else:
                    nk = nt[0].get("name", "?")
                    nk = "Bash(log/note)" if nk == "Bash" and all((LOG_RE.search(t.get("input", {}).get("command", "")) or NOTE_RE.search(t.get("input", {}).get("command", ""))) for t in nt) else nk.split("__")[-1]
                    next_kind[nk] += 1
                if prev is not None and any(t.get("name") == "Bash" for t in prev["tools"]):
                    chain_ok += 1
            prev = e
    res = dict(window=[a.since, a.until], files_scanned=tot_files, files_with_orchestrator_requests=used,
               tool_using_requests=tool_reqs, bookkeeping_only=book, tier2_any_log_or_note_present=any_lg, tier3_log_note_plus_trivial_companions=near,
               tier2_cache_read=any_lg_cr, tier3_cache_read=near_cr, share=round(book / max(tool_reqs, 1), 4),
               only_log=book_log, only_note=book_note, log_and_note=book_mixed,
               preceded_by_bash_request=chain_ok, cache_read_tokens_total=cache_read,
               cache_read_tokens_bookkeeping=book_cache_read,
               avg_cache_read_per_bookkeeping=round(book_cache_read / max(book, 1)),
               lines_per_log_call=dict(sorted(lines_per_call.items())), tool_of_next_request_after_bookkeeping_only=next_kind.most_common(), log_line_kinds_in_bookkeeping_only_requests=kinds.most_common(40),
               log_line_kinds_all_requests=kinds_all.most_common(45), log_lines_all=sum(kinds_all.values()))
    res['side_finding_toolsearch_only_requests'] = [toolsearch_only, toolsearch_cr]
    res['bookkeeping_only_requests_by_class'] = dict(req_class)
    cls = collections.Counter()
    for k, v in kinds_all.items():
        cls[class_of(k)] += v
    res["class_share_all_log_lines"] = {c: [n, round(n / max(sum(cls.values()), 1), 3)] for c, n in sorted(cls.items())}
    cls2 = collections.Counter()
    for k, v in kinds.items():
        if k != "(owner note)":
            cls2[class_of(k)] += v
    res["class_share_in_bookkeeping_only_requests"] = {c: [n, round(n / max(sum(cls2.values()), 1), 3)] for c, n in sorted(cls2.items())}
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        for k, v in res.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
