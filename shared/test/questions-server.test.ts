// Owner-questions server: store, API, page and the Slack envelope handlers (no network, no Slack).
// Run: bun --no-install shared/test/questions-server.test.ts
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, appendFileSync, rmSync, realpathSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { appendOwner, cut, parseQuestion, readAll, writeSidecar, idForThread, AnsweredError } from "../questions/store.ts"
import { channelNameFor, interpretEnvelope, questionBlocks, replyKind } from "../questions/slack.ts"
import { remindersDue } from "../questions/server.ts"
import { undecidedDeferred, parseFrontMatter, lastPhase, stagePhase, lastLine, buildLog, pickDossiers, pickDossiersByRepo, questionRepo, viewFor, tailTranscript, transcriptCwd, loadPhaseNames, phaseNameOf, isRunning, answeredBy, repoLabel, parseDeferred, deferredSince, parseReportTable, withReport, withBranchCopies, isBuildBranch, readDossier, activityOf, toolLabel, linkPlannedItems, deferredSources, withOwnerRulings, ownerRulingsFile } from "../questions/runs.ts"
import { prNumberOf, prFieldNumber, dossiersOnMerges, stubStage, stubState, recordFile, openStubs, stubMembers, fetchStubRefs } from "../questions/stubs.ts"
import { slackMessageFor, pendingSlack } from "../questions/runslack.ts"
import { readUpdates, readRunner, summaryOf, renderMarkdown, readConversation, readBlocks, checkRule } from "../questions/runstore.ts"
import { blockedTarget } from "../claude/permission-denied.ts"
import { mergeSpawns, inTimeline } from "../questions/timeline.ts"
import { nextMerges, dossierOfPr } from "../questions/merges.ts"
import { agentCounts, waitingForSlots, runStatusText, statusBlocks, openerBlocks } from "../questions/runstatus.ts"
import { mergeTimeline, parseRecord, logEntries, classify, groupByPhase, nowPanel, splitSections, commandStep } from "../questions/timeline.ts"
import { blockedByOf, controlsFor, runTarget } from "../questions/controls.ts"
import { readAccounts } from "../questions/accounts.ts"
import { execFileSync } from "node:child_process"

let failed = 0
function check(name: string, ok: boolean, detail = ""): void {
  if (!ok) failed++
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : ` ${detail}`}`)
}

{
  const log = ["DEFERRED: 3 — sources: own reads", "- D-1 bug Retry ignores 429 — src/a.rs:40 — high — out of scope", "- D-2 ssot Two timeouts — src/b.rs:9 — low — later",
    "- D-3 architecture Split the planner — src/p.rs:1 — medium — too big", "- D-1 bug duplicate line wins nothing — x — y — z",
    "DEFERRED-TRIAGE: D-3 → dossier — crosses the planner contract; seed: /plan W-1 D-3", "DEFERRED-TRIAGE: D-1 → direct — honour Retry-After",
    "DEFERRED-FIX: PR https://github.com/o/r/pull/9 — fixed 1, to dossier 1", "DEFERRED-FIXED: D-1 abc1234"].join("\n")
  const pd = parseDeferred(log)
  check("deferred: ledger items, first line per id, states and the stacked PR",
    pd.items.length === 3 && pd.items[0].title === "Retry ignores 429" && pd.items[0].loc === "src/a.rs:40" && pd.items[0].state === "fixed" && pd.items[0].note === "abc1234"
    && pd.items[1].state === "open" && pd.items[2].state === "dossier" && pd.pr === "https://github.com/o/r/pull/9", JSON.stringify(pd))
  check("phase: a phase named mid-sentence is not progress; a line-start marker is", lastPhase("- OWNER-RULING: draft PR at Phase 6 yes\n- Phase 2 closed: contract committed\nnotes about Phase 7") === "2" && lastPhase("PHASE 9: synced") === "9")
check("phase: the newest stage line wins", stagePhase([{ kind: "update", text: "Phase 6 soon" }, { kind: "stage", stage: "Phase 2 — contract", text: "x" }, { kind: "stage", stage: "Phase 1 — gates", text: "y" }]) === "2")
{
  const rep = "## Deferred issues — W-9\n\n| ID | Kind | Title | Risk | Origin | Contract | Effort |\n|---|---|---|---|---|---|---|\n| D-1 | latent-bug | Make `x` cover links | medium | new | external | M |\n| D-3 | ssot | Fix doc | low | old | none | S |\n\n| other | table |\n|---|---|\n| D-9 | x |\n"
  const t = parseReportTable(rep)
  check("deferred report: the summary table by its header names", t.size === 2 && t.get("D-1")?.contract === "external" && t.get("D-1")?.effort === "M" && t.get("D-1")?.title === "Make x cover links" && t.get("D-3")?.risk === "low")
  const wr = withReport(parseDeferred("- D-1 bug a — f:1 — low — x\n- D-2 friction b — f:2 — low — y").items, rep)
  check("deferred report: ledger items gain contract and effort; the rest is not product code", wr[0].contract === "external" && wr[0].inReport === true && wr[1].inReport === false && withReport(wr, "")[1].inReport === false)
}
{
  const led = parseDeferred("- D-1 bug a — f:1 — low: x — y — contract: external — effort: M\n- D-2 friction b — f:2 — medium — z — contract: internal: the pool seam — effort: s (minutes)\n- D-3 style c — f:3 — low — w").items
  check("deferred: ledger lines carry contract and effort, kept out of the why", led[0].contract === "external" && led[0].effort === "M" && led[0].why === "y" && led[0].risk === "low: x" && led[1].contract === "internal" && led[1].effort === "S" && led[1].why === "z" && led[2].contract === undefined && led[2].effort === undefined)
  const rep2 = "| ID | Kind | Title | Risk | Origin | Contract | Effort |\n|---|---|---|---|---|---|---|\n| D-1 | bug | a | low | new | none | S |\n"
  const wr2 = withReport(led, rep2)
  check("deferred: the report supersedes the ledger's contract and effort; items it lacks keep theirs", wr2[0].contract === "none" && wr2[0].effort === "S" && wr2[1].contract === "internal" && wr2[1].effort === "S" && wr2[1].inReport === false)
}
{
  const ah = mkdtempSync(join(tmpdir(), "acct-"))
  mkdirSync(join(ah, "u")); mkdirSync(join(ah, "m"))
  writeFileSync(join(ah, "accounts.json"), JSON.stringify({ accounts: [{ name: "a", dir: join(ah, "m") }, { name: "b", dir: join(ah, "x") }] }))
  writeFileSync(join(ah, "m", ".claude.json"), JSON.stringify({ oauthAccount: { emailAddress: "a@x.test", organizationName: "Org A" } }))
  const now = Date.parse("2026-11-04T12:00:00Z")
  writeFileSync(join(ah, "state.json"), JSON.stringify({ a: { limited_until: now / 1000 + 3600 } }))
  writeFileSync(join(ah, "u", "b.json"), JSON.stringify({ ts: "2026-11-04T11:59:00Z", windows: { five_hour: { used_percentage: 9, resets_at: now / 1000 + 600, status: "allowed" }, seven_day: { used_percentage: 40, resets_at: now / 1000 - 60, status: "allowed" } } }))
  const env0 = { a: process.env.ASD_ACCOUNTS, s: process.env.ASD_ACCOUNT_STATE, u: process.env.ASD_USAGE_DIR }
  process.env.ASD_ACCOUNTS = join(ah, "accounts.json"); process.env.ASD_ACCOUNT_STATE = join(ah, "state.json"); process.env.ASD_USAGE_DIR = join(ah, "u")
  const acs = readAccounts([{ key: "r.W-1", account: "b" }, { key: "r.W-2", account: "a" }], now)
  check("accounts: login, limit, next account, windows (a passed reset is unknown), runs per account",
    acs.length === 2 && acs[0].email === "a@x.test" && acs[0].limitedUntil !== "" && !acs[0].next && acs[1].next && acs[1].fiveHour?.usedPct === 9 && acs[1].week?.usedPct === null && acs[1].week?.status === "reset" && acs[0].fiveHour === null && acs[1].runs.join() === "r.W-1" && acs[0].runs.join() === "r.W-2", JSON.stringify(acs))
  for (const [k, v] of [["ASD_ACCOUNTS", env0.a], ["ASD_ACCOUNT_STATE", env0.s], ["ASD_USAGE_DIR", env0.u]] as const) { if (v === undefined) delete process.env[k]; else process.env[k] = v }
  rmSync(ah, { recursive: true, force: true })
}
{
  const U2 = (ts: string, kind: string, text: string, extra: object = {}) => ({ ts, kind, text, ...extra }) as any
  const ups = [U2("2026-11-04T09:20:00Z", "stage", "triage done", { stage: "Fix-deferred — reusing Phase 7 fix machinery" }), U2("2026-11-04T09:16:00Z", "update", "Started fixing W-1's deferred issues."),
    U2("2026-11-04T09:15:07Z", "runner", "start /fix-deferred W-1 in /r"), U2("2026-11-04T07:24:24Z", "stage", "PR ready", { stage: "Phase 9 — PR" }), U2("2026-11-04T07:00:00Z", "stage", "x", { stage: "Phase 8 — ADRs" })]
  const secs = splitSections(mergeTimeline({ updates: ups, questions: [] }))
  const impl = groupByPhase(secs[0].entries), fd = groupByPhase(secs[1].entries, "Started")
  check("timeline: a fix-deferred run is a tab of its own; the dossier's phases stay in Implement dossier", secs.length === 2 && secs[0].label === "Implement dossier" && secs[1].cmd === "fix-deferred" && secs[1].label === "Fix deferred issues"
    && impl.map((x) => x.label).join("|") === "Phase 9|Phase 8" && fd.map((x) => x.label).join("|") === "Step 4 — Fix, test first|Started" && fd[1].items.some((i: any) => i.entry?.text === "Started fixing W-1's deferred issues."), JSON.stringify([impl.map((x) => x.label), fd.map((x) => x.label)]))
  const secs2 = splitSections(mergeTimeline({ updates: [U2("2026-11-04T10:00:00Z", "stage", "y", { stage: "Phase 9 — PR again" }), U2("2026-11-04T09:59:00Z", "runner", "start /work-on W-1 in /r"), ...ups], questions: [] }))
  check("timeline: a /work-on run after a fix-deferred run goes back to Implement dossier", groupByPhase(secs2[0].entries)[0].label.startsWith("Phase 9") && secs2[1].entries.every((e) => e.cmd === "fix-deferred"), JSON.stringify(groupByPhase(secs2[0].entries).map((x) => x.label)))
  check("timeline: fix-deferred steps by number, else by their words", commandStep("fix-deferred", "Fix-deferred — Step 3 — The stacked branch")?.n === 3 && commandStep("fix-deferred", "Phase 7 — Fix-deferred: review fixed, waiting on Q22")?.n === 5
    && commandStep("fix-deferred", "Phase 2 — Fix-deferred — Triage")?.n === 2 && commandStep("fix-deferred", "Phase 6 — Fix-deferred — The pull request")?.n === 6 && commandStep("fix-deferred", "Phase 7 — Fix-deferred: triage and payloads")?.n === 2 && commandStep("work-on", "x") === null)
  const ups3 = [U2("2026-11-04T12:00:00Z", "runner", "start /fix-deferred W-1 in /r"), ...ups]
  check("timeline: a restarted fix-deferred run carries on in its tab", splitSections(mergeTimeline({ updates: ups3, questions: [] })).length === 2)
}
{
  const bd = mkdtempSync(join(tmpdir(), "blk-"))
  const L = (o: any) => JSON.stringify({ kind: "update", auto: true, ...o })
  writeFileSync(join(bd, "r.W-1.jsonl"), [L({ ts: "1", event: "blocked", tool: "Bash", target: "ssh vm rebuild", reason: "[Shared infra]", tool_use_id: "t1" }), L({ ts: "2", event: "blocked", tool: "Bash", target: "ssh vm rebuild", reason: "again", tool_use_id: "t2" }),
    L({ ts: "3", event: "blocked", tool: "Bash", target: "cat secret", reason: "x", tool_use_id: "t3" }), L({ ts: "4", event: "block-ruling", tool_use_id: "t3", scope: "deny", target: "cat secret" }), JSON.stringify({ ts: "5", kind: "update", text: "note" })].join("\n") + "\n")
  const bks = readBlocks("r.W-1", bd)
  check("blocks: unruled refusals, newest first, one per target; a ruled one is gone", bks.length === 1 && bks[0].id === "t2" && bks[0].reason === "again")
  appendFileSync(join(bd, "r.W-1.jsonl"), L({ ts: "6", event: "block-ruling", tool_use_id: "t2", scope: "always", target: "ssh vm rebuild" }) + "\n")
  check("blocks: an always-allow ruling clears every refusal of that target", readBlocks("r.W-1", bd).length === 0)
  // A refusal from an earlier stage process is not open: the runner's (python) line format, with spaces.
  writeFileSync(join(bd, "r.W-2.jsonl"), [L({ ts: "1", event: "blocked", tool: "Bash", target: "docker compose build", reason: "x", tool_use_id: "s1" }),
    JSON.stringify({ ts: "2", kind: "runner", text: "stage 6 start", stage: "6" }, null, 1).replace(/\n\s*/g, " "),
    L({ ts: "3", event: "blocked", tool: "Bash", target: "ssh vm", reason: "y", tool_use_id: "s2" })].join("\n") + "\n")
  const b2 = readBlocks("r.W-2", bd)
  check("blocks: only the current stage process's refusals are open", b2.length === 1 && b2[0].id === "s2", JSON.stringify(b2))
  appendFileSync(join(bd, "r.W-2.jsonl"), JSON.stringify({ ts: "4", kind: "runner", text: "start /fix-deferred W-2 in /r" }) + "\n")
  check("blocks: a restarted run starts with none", readBlocks("r.W-2", bd).length === 0)
  rmSync(bd, { recursive: true, force: true })
  check("blocks: a rule must be narrow", checkRule("Bash(ssh vm-test *)") === "" && checkRule("Bash(*)") !== "" && checkRule("Bash(python3 *)") !== "" && checkRule("Bash(python3 scripts/x.py --y)") === "" && checkRule("ssh x") !== "" && checkRule("Read(/etc/x)") === "")
  check("blocks: the hook records the exact command, a path, else the input", blockedTarget("Bash", { command: "ssh vm x" }) === "ssh vm x" && blockedTarget("Write", { file_path: "/a/b" }) === "/a/b" && blockedTarget("X", { a: 1 }) === '{"a":1}')
}
check("branches: a build branch names the id in any case; plan, stub and trunk branches never do", isBuildBranch("feat/w-130-x", "W-130") && isBuildBranch("origin/feat/W-128-a", "W-128") && isBuildBranch("fix/W-12-deferred", "W-12") && !isBuildBranch("feat/W-1300-x", "W-130") && !isBuildBranch("plan/W-130-x", "W-130") && !isBuildBranch("origin/stub/W-130", "W-130") && !isBuildBranch("development", "W-130"))
{
  const g = mkdtempSync(join(tmpdir(), "brc-"))
  const git = (...a: string[]) => execFileSync("git", ["-C", g, "-c", "user.email=t@x.invalid", "-c", "user.name=t", ...a], { stdio: "pipe" })
  mkdirSync(join(g, ".discovery", "dossiers"), { recursive: true })
  const f = join(g, ".discovery", "dossiers", "W-5-x.md")
  writeFileSync(f, "---\nid: W-5\nstatus: ready\n---\n# W-5\n")
  git("init", "-q", "-b", "development"); git("add", "-A"); git("commit", "-q", "-m", "base")
  git("checkout", "-q", "-b", "feat/w-5-x")
  writeFileSync(f, "---\nid: W-5\nstatus: done\npr: 7\n---\n# W-5\n## Build log\n- one\n- two\n- D-1 bug a — f:1 — low — x\n")
  git("commit", "-q", "-am", "done"); git("checkout", "-q", "development")
  const picked = await withBranchCopies(g, pickDossiers([g]))
  check("branches: a finished build's branch copy replaces the checkout's older one", picked.length === 1 && picked[0].front.status === "done" && picked[0].branch === "feat/w-5-x" && picked[0].file.startsWith("git:") && readDossier(picked[0].file).includes("status: done") && readDossier(f).includes("status: ready"))
  rmSync(g, { recursive: true, force: true })
}
{
  const cd = mkdtempSync(join(tmpdir(), "conv-"))
  const L = (o: any) => JSON.stringify(o)
  writeFileSync(join(cd, "r.W-1.jsonl"), [L({ ts: "2026-11-04T07:24:24Z", kind: "update", text: "W-1 is done; PR ready." }), L({ ts: "2026-11-04T07:24:25Z", kind: "update", auto: true, text: "log: x" }), L({ ts: "2026-11-04T07:24:32Z", kind: "runner", text: "stage 9 end" }), L({ ts: "2026-11-04T07:26:06Z", kind: "update", text: "Deferred report ready" }), L({ ts: "2026-11-04T07:26:30Z", kind: "runner", text: "done" })].join("\n") + "\n")
  const c0 = readConversation("r.W-1", true, cd)
  check("conversation: an older run's reply is the agent's last note before its last stage end", c0.result?.text === "W-1 is done; PR ready." && c0.chat.length === 0 && !c0.busy && readConversation("r.W-1", false, cd).result === null)
  appendFileSync(join(cd, "r.W-1.jsonl"), [L({ ts: "2026-11-04T08:00:00Z", kind: "result", text: "All done.", command: "work-on" }), L({ ts: "2026-11-04T07:59:00Z", kind: "chat", from: "owner", text: "old" }), L({ ts: "2026-11-04T08:01:00Z", kind: "chat", from: "owner", text: "why?" }), L({ ts: "2026-11-04T08:02:00Z", kind: "chat", from: "agent", text: "because" })].join("\n") + "\n")
  writeFileSync(join(cd, "r.W-1.chat.json"), JSON.stringify({ state: "thinking", pid: 4242 }))
  const c1 = readConversation("r.W-1", true, cd, (pid) => pid === 4242)
  check("conversation: the result line wins, chat after it in order, busy while the chat pid lives", c1.result?.text === "All done." && c1.chat.map((x) => x.from + ":" + x.text).join() === "owner:why?,agent:because" && c1.busy && !readConversation("r.W-1", true, cd, () => false).busy)
  check("conversation: chat and result lines stay out of the update stream", readUpdates("r.W-1", cd).every((u) => u.kind !== ("chat" as any)) && readUpdates("r.W-1", cd).length === 5)
  rmSync(cd, { recursive: true, force: true })
}
{
  const pd = parseDeferred("- D-1 bug a — f:1 — low — x\n- D-2 bug b — f:2 — low — y\n- D-3 bug c — f:3 — low — z\n- DEFERRED-FIX: branch fix/W-1-deferred on feat/w-1 @ abc\n- DEFERRED-TRIAGE: D-1 → direct — extract it\n- DEFERRED-TRIAGE: D-2 → direct — rename\n- DEFERRED-FIXED: D-2 1234567\n- DEFERRED-FIX: PR https://x/pull/429 — fixed 1, to dossier 0")
  check("deferred: a direct triage is being fixed until its FIXED line; the fix branch and PR are read", pd.items[0].state === "fixing" && pd.items[0].note === "extract it" && pd.items[1].state === "fixed" && pd.items[2].state === "open" && pd.branch === "fix/W-1-deferred" && pd.pr === "https://x/pull/429")
}
{
  const pr = parseDeferred("- D-2 gap a — f:1 — low — x\n- D-9 perf b — f:2 — low — y\n- D-4 bug c — f:3 — low — z\n- QUESTION: r.W-1.Q6 — D-2 dossier, fix here or leave open\n- QUESTION: r.W-1.Q13 — D-9 dossier, fix here or leave open\n- DEFERRED-ASKED: D-4 r.W-1.Q20\n- OWNER-RULING (2026-11-04): r.W-1.Q6 — D-2 stays open\n- OWNER-RULING (2026-11-04): r.W-1.Q13 — D-9 measure it now and come back to the owner").items
  check("deferred: questions and rulings — left open by the owner, worked on after another ruling, asked and waiting", pr[0].keptOpen === true && pr[0].state === "open" && pr[0].question === "r.W-1.Q6" && pr[1].state === "fixing" && pr[1].note.includes("measure it now") && pr[2].question === "r.W-1.Q20" && pr[2].state === "open" && !pr[2].keptOpen)
}
check("deferred: dropped items are deprecated, a dossier link names the new dossier", (() => { const x = parseDeferred("- D-1 bug a — f:1 — low — x\n- D-2 bug b — f:2 — low — y\nDEFERRED-DROPPED: D-1 — code removed\nDEFERRED-DOSSIER: D-2 → W-150").items; return x[0].state === "deprecated" && x[0].note === "code removed" && x[1].state === "dossier" && x[1].dossier === "W-150" })())
check("deferred: old dossiers are ignored unless the runner ran them", !deferredSince("2026-10-20", null) && deferredSince("2026-11-03", null) && deferredSince("2026-10-01", { state: "done" }))
check("deferred: an item marked RESOLVED in place is closed", parseDeferred("- D-1 RESOLVED: renumbered — x").items[0].state === "fixed")
  const c = (status: string, open: number, state?: string) => controlsFor({ status, blockedBy: [], runner: state ? ({ state } as any) : null, deferredOpen: open }, () => "done")["fix-deferred"].show
  check("deferred: Fix all shows on pr/done with open items and no live runner", c("done", 2) && c("pr", 1) && !c("done", 0) && !c("building", 2) && !c("done", 2, "running") && c("done", 2, "done"))
  const sm = (t: string) => slackMessageFor({ ts: "t", kind: "runner", text: t } as any, { repo: "r", id: "W-1", title: "x", link: "L" })
  check("deferred: the run thread gets the deferred count, not a zero", (sm("deferred report: 3 issue(s)")?.text ?? "").startsWith("📋 3 deferred issues") && sm("deferred report: 0 issue(s)") === null)
}
check("replies: typed text is always a follow-up; the question stays open", replyKind("why A?").kind === "followup" && replyKind(" go with B ").kind === "followup" && replyKind(" go with B ").text === "go with B")
{
  {
    const J = (o: any) => JSON.stringify(o)
    const qc = parseQuestion("r.W-1.Q9", [J({ ts: "1", from: "agent", kind: "question", text: "Which?", options: [{ key: "A", label: "one", consequence: "c" }, { key: "B", label: "two", consequence: "c" }] }),
      J({ ts: "2", from: "owner", kind: "followup", text: "go for D instead" }),
      J({ ts: "3", from: "agent", kind: "reply", text: "You mean D: keep the overlap.", options: [{ key: "D", label: "Confirm: keep the overlap", consequence: "no wait" }, { key: "A", label: "dup", consequence: "x" }] })].join("\n"))
    const qr = parseQuestion("r.W-1.Q10", [J({ ts: "1", from: "agent", kind: "question", text: "Which?", recommendation: "A", options: [{ key: "A", label: "one", consequence: "c" }, { key: "D", label: "four", consequence: "c" }] }),
      J({ ts: "2", from: "agent", kind: "reply", text: "Measured: D is better.", recommend: "D" })].join("\n"))
    check("question chat: a reply moves the recommendation", qr!.recommendation === "D")
    check("question chat: a reply's option becomes a button; the question stays open until one is pressed", qc!.state === "open" && qc!.options.map((o) => o.key).join() === "A,B,D" && qc!.options[2].label === "Confirm: keep the overlap")
  }
  const qa = parseQuestion("r.W-1.Q1", [JSON.stringify({ ts: "1", from: "agent", kind: "question", text: "Which?" }), JSON.stringify({ ts: "2", from: "owner", kind: "followup", text: "use B" }), JSON.stringify({ ts: "3", from: "agent", kind: "answer", accepted: true, text: "I will use B.", owner_text: "use B" })].join("\n"))
  const qo = parseQuestion("r.W-1.Q2", [JSON.stringify({ ts: "1", from: "agent", kind: "question", text: "Which?" }), JSON.stringify({ ts: "2", from: "owner", kind: "followup", text: "use B" }), JSON.stringify({ ts: "3", from: "agent", kind: "answer", text: "not accepted" })].join("\n"))
  check("store: a typed message the agent accepted closes the question; an agent line without accepted does not", qa?.state === "answered" && qa.answer?.text === "I will use B." && qo?.state === "open")
}
check("slack: channel names from repo names", channelNameFor("config-harness") === "config-harness" && channelNameFor("Documents/Twipe/repos") === "documents-twipe-repos" && channelNameFor("A__b.c") === "a__b-c" && channelNameFor("x".repeat(100)).length === 79)
{
  const env = (channel_type: string, thread: boolean) => ({ type: "events_api", envelope_id: "e", payload: { event: { type: "message", channel_type, channel: "C1", user: "U1", text: "why?", ts: "2", ...(thread ? { thread_ts: "1" } : {}) } } })
  const look = (c: string, t: string) => (c === "C1" && t === "1" ? "r.W-1.Q1" : null)
  check("slack: a thread reply in a repo channel is a follow-up", (interpretEnvelope(env("group", true), look, "U1").action as any).kind === "followup")
  check("slack: other talk in a repo channel is ignored, a stray DM gets the pointer", interpretEnvelope(env("group", false), look, "U1").action.type === "none" && interpretEnvelope(env("im", false), look, "U1").action.type === "pointer")
}

{
  const m = 60 * 1000
  const due = [0, 29, 30, 89, 90, 209, 210, 330].map((x) => remindersDue(x * m))
  check("reminders at 30 min, +1 h, then every 2 h", JSON.stringify(due) === JSON.stringify([0, 0, 1, 1, 2, 2, 3, 4]), JSON.stringify(due))
}

const dir = mkdtempSync(join(tmpdir(), "asd-oq-"))
const qLine = {
  ts: "2026-11-02T10:00:00Z", from: "agent", kind: "question", text: "Which store?", dossier: "W-1", repo: "demo", root: "/tmp/demo",
  options: [{ key: "A", label: "sqlite", consequence: "fast" }, { key: "B", label: "files", consequence: "simple" }], recommendation: "B", blocks: "the writer",
}
// Hand-written fixture whose name differs from any id in its content.
writeFileSync(join(dir, "hand.written.Q9.jsonl"), JSON.stringify(qLine) + "\nnot json\n" + JSON.stringify({ ts: "2026-11-02T10:05:00Z", from: "owner", kind: "followup", text: "why?", via: "cli" }) + "\n")

// store
const q = parseQuestion("x", readFileSync(join(dir, "hand.written.Q9.jsonl"), "utf8"))!
check("store: parses a question and skips a torn line", q.lines.length === 2 && q.options.length === 2 && q.repo === "demo")
check("store: open until an answer", q.state === "open" && q.answer === null)
check("store: id comes from the file name", readAll(dir)[0].id === "hand.written.Q9")
check("store: cut at 3500", cut("x".repeat(4000)).length === 3506 && cut("x".repeat(3500)).length === 3500)
const a1 = appendOwner(dir, "hand.written.Q9", { kind: "answer", text: "B it is", option: "B", via: "page" })
check("store: answer makes it answered", a1.state === "answered" && a1.answer?.option === "B" && a1.answer?.via === "page")
let blocked = false
try { appendOwner(dir, "hand.written.Q9", { kind: "answer", text: "other", option: "A", via: "page" }) } catch (e) { blocked = e instanceof AnsweredError }
check("store: a second answer is refused", blocked)
appendFileSync(join(dir, "hand.written.Q9.jsonl"), JSON.stringify({ ts: "z", from: "owner", kind: "answer", text: "late", option: "A", via: "cli" }) + "\n")
check("store: the first answer stays final", readAll(dir)[0].answer?.text === "B it is")
writeSidecar(dir, "hand.written.Q9", { channel: "D1", ts: "111.222" })
check("store: sidecar maps a thread to the id", idForThread(dir, "D1", "111.222") === "hand.written.Q9" && idForThread(dir, "D1", "9") === null)

// Slack envelopes
const lookup = (c: string, t: string) => (c === "D1" && t === "111.222" ? "hand.written.Q9" : null)
const ev = (e: object) => ({ envelope_id: "E1", type: "events_api", payload: { event: { type: "message", channel_type: "im", channel: "D1", ts: "5.1", user: "U1", ...e } } })
check("slack: hello is acked", JSON.stringify(interpretEnvelope({ type: "hello" }, lookup).ack) === "null")
let r = interpretEnvelope({ envelope_id: "E0", type: "disconnect" }, lookup)
check("slack: disconnect acks and reconnects", r.ack?.envelope_id === "E0" && r.action.type === "reconnect")
r = interpretEnvelope(ev({ text: "what about cost?", thread_ts: "111.222" }), lookup)
check("slack: thread reply is a follow-up", r.ack?.envelope_id === "E1" && r.action.type === "owner" && r.action.kind === "followup" && r.action.id === "hand.written.Q9")
r = interpretEnvelope(ev({ text: "Answer: go with A", thread_ts: "111.222" }), lookup)
check("slack: a typed reply, even with answer: in front, stays a follow-up", r.action.type === "owner" && r.action.kind === "followup" && r.action.text === "Answer: go with A" && !r.action.option)
r = interpretEnvelope(ev({ text: "hello" }), lookup)
check("slack: DM outside a thread gets a pointer", r.action.type === "pointer")
r = interpretEnvelope(ev({ text: "x", thread_ts: "999.9" }), lookup)
check("slack: reply in an unknown thread gets a pointer", r.action.type === "pointer")
check("slack: bot messages are ignored", interpretEnvelope(ev({ text: "x", bot_id: "B1", thread_ts: "111.222" }), lookup).action.type === "none")
check("slack: subtyped messages are ignored", interpretEnvelope(ev({ text: "x", subtype: "message_changed", thread_ts: "111.222" }), lookup).action.type === "none")
check("slack: other users are ignored when an owner is set", interpretEnvelope(ev({ text: "x", user: "U2", thread_ts: "111.222" }), lookup, "U1").action.type === "none")
r = interpretEnvelope({ envelope_id: "E2", type: "interactive", payload: { type: "block_actions", user: { id: "U1" }, channel: { id: "D1" }, actions: [{ value: "hand.written.Q9|A" }] } }, lookup)
check("slack: button press is an answer with that option", r.ack?.envelope_id === "E2" && r.action.type === "owner" && r.action.kind === "answer" && r.action.option === "A" && r.action.id === "hand.written.Q9")
// Slack status on demand
r = interpretEnvelope(ev({ text: "status" }), lookup)
check("slack: status in the DM asks for every running dossier", r.action.type === "status" && !(r.action as any).id && (r.action as any).ts === "5.1")
r = interpretEnvelope(ev({ text: "Status w-128" }), lookup)
check("slack: status W-128 names the dossier", r.action.type === "status" && (r.action as any).id === "W-128")
r = interpretEnvelope({ envelope_id: "E9", type: "events_api", payload: { event: { type: "message", channel_type: "group", channel: "G1", ts: "7.1", thread_ts: "6.1", user: "U1", text: "update" } } }, lookup)
check("slack: status in a run thread keeps the thread", r.action.type === "status" && (r.action as any).threadTs === "6.1")
r = interpretEnvelope(ev({ text: "status", thread_ts: "111.222" }), lookup)
check("slack: status inside a question thread stays a follow-up", r.action.type === "owner")
r = interpretEnvelope(ev({ text: "what is the status of the build?" }), lookup)
check("slack: a sentence mentioning status is not the command", r.action.type === "pointer")
r = interpretEnvelope({ envelope_id: "E3", type: "interactive", payload: { type: "block_actions", user: { id: "U1" }, channel: { id: "G1" }, container: { message_ts: "8.1" }, message: { ts: "8.1", blocks: [{ type: "section", text: { text: "*Status — config-harness*" } }] }, actions: [{ action_id: "run_status", value: "config-harness|" }] } }, lookup)
check("slack: Refresh on a status message refreshes it in place, with its scope", r.action.type === "status" && (r.action as any).refresh === true && (r.action as any).repo === "config-harness" && !(r.action as any).id)
r = interpretEnvelope({ envelope_id: "E4", type: "interactive", payload: { type: "block_actions", user: { id: "U1" }, channel: { id: "G1" }, container: { message_ts: "6.1" }, message: { ts: "6.1", blocks: [{ type: "section", text: { text: "▶ config-harness · W-1" } }] }, actions: [{ action_id: "run_status", value: "config-harness|W-1" }] } }, lookup)
check("slack: Status on a thread opener answers in a new message for that run", r.action.type === "status" && (r.action as any).refresh === false && (r.action as any).id === "W-1")
{
  const T = (m: number) => new Date(Date.parse("2026-11-03T18:00:00Z") + m * 60000).toISOString()
  const ups: any[] = [
    { ts: T(0), kind: "runner", text: "stage 4 start" },
    { ts: T(1), kind: "update", auto: true, event: "spawn", status: "running", agent_id: "a1", text: "implementer P1" },
    { ts: T(1), kind: "update", auto: true, event: "spawn", status: "running", agent_id: "a2", text: "implementer P2" },
    { ts: T(1), kind: "update", auto: true, event: "spawn", status: "running", agent_id: "a3", text: "test author" },
    { ts: T(5), kind: "update", auto: true, event: "spawn-done", status: "done", agent_id: "a1", text: "done" },
    { ts: T(6), kind: "update", auto: true, event: "spawn-done", status: "gap", agent_id: "a3", text: "gap" },
    { ts: T(7), kind: "update", text: "Wave one is out.\n\nMore detail later." },
  ].reverse()
  const a = agentCounts(ups)
  check("status: agents of the current stage, running and finished", a.running === 1 && a.done === 2 && a.flagged === 1, JSON.stringify(a))
  check("status: no slot wait once agents spawned", !waitingForSlots(ups))
  const wait = [{ ts: T(8), kind: "update", auto: true, event: "log", text: "log: ADMISSION-WAIT: refused — 0 of 16 slots free" }, ...ups] as any
  check("status: a refused admission after the last spawn is a slot wait", waitingForSlots(wait))
  const run = { repo: "config-harness", id: "W-1", title: "A <title>", state: "running", phase: "4", phaseName: "Fan-out", account: "b", started: T(0), context: 63000, questions: [{ id: "Q1", text: "Pick one" }], updates: ups, link: "http://x/r" }
  const t = runStatusText(run, Date.parse(T(30)))
  check("status: one run's text", t.includes("in progress · Phase 4 · Fan-out · account b") && t.includes("1 running, 2 finished (1 with a gap") && t.includes("Wave one is out.") && !t.includes("More detail") && t.includes("context 63k") && t.includes("❓ *Q1* Pick one") && t.includes("A &lt;title&gt;") && t.includes("⚠ quiet"), t)
  const sb = statusBlocks([run], { repo: "config-harness" }, Date.parse(T(30)))
  check("status: blocks carry a heading, the run and a Refresh button with the scope", sb.blocks[0].text.text.startsWith("*Status — config-harness*") && sb.blocks.at(-1).elements[0].value === "config-harness|" && sb.text.includes("W-1 running"))
  check("status: nothing running says so", JSON.stringify(statusBlocks([], {}, 0).blocks).includes("No dossier is running."))
  check("status: the thread opener has a Status button for its run", openerBlocks("▶ x", "r", "W-2")[1].elements[0].value === "r|W-2")
}
const blocks = questionBlocks(readAll(dir)[0], 7317)
check("slack: blocks are section, actions (one button per option), context", blocks.map((b: any) => b.type).join() === "section,actions,context" && blocks[1].elements.length === 2 && blocks[1].elements[0].value === "hand.written.Q9|A")

// runs: fixtures (dossiers in two checkouts, one transcript)
const rdir = mkdtempSync(join(tmpdir(), "asd-oq-runs-"))
const dossier = (status: string, log: string) => `---\nid: W-7\ntitle: "Seven"\nstatus: ${status}\njira: W-7\nupdated: 2026-11-02\n---\n# W-7\n\n## Build log\n${log}`
const repoA = join(rdir, "repoA"), repoB = join(rdir, "repoB")
for (const r of [repoA, repoB]) mkdirSync(join(r, ".discovery", "dossiers"), { recursive: true })
writeFileSync(join(repoA, ".discovery", "dossiers", "W-7-seven.md"), dossier("ready", "- started\n"))
writeFileSync(join(repoB, ".discovery", "dossiers", "W-7-seven.md"), dossier("building", "- Phase 3 contract\n- PHASE 4a fan-out\n- Phase 4 wave one returned\n\n"))
writeFileSync(join(repoA, ".discovery", "dossiers", "W-8-eight.md"), "---\nid: W-8\ntitle: Eight\nstatus: planned\n---\nno log\n")
const fm = parseFrontMatter(dossier("building", "x\n"))
check("runs: front matter scalars", fm.id === "W-7" && fm.title === "Seven" && fm.status === "building" && fm.jira === "W-7" && fm.updated === "2026-11-02")
check("runs: last phase match wins, else empty", lastPhase("- Phase 1 closed\nPHASE 4a: wave\nPhase 4 done") === "4" && lastPhase("nothing") === "")
check("runs: last non-empty line, 160 chars", lastLine(buildLog(dossier("x", "- a\n- " + "z".repeat(200) + "\n\n"))).length === 160 && lastLine(buildLog(dossier("x", "- a\n- b\n\n"))) === "b")
check("runs: a wrapped last entry is joined, a trailing fence skipped", lastLine(buildLog(dossier("x", "- a\n- Phase 6 closed: suite\n  green on abc\n"))) === "Phase 6 closed: suite green on abc" && lastLine(buildLog(dossier("x", "- a\n### Round 1\n```text\nCR-1\n```\n"))) === "Round 1")
const picked = pickDossiers([repoA, repoB])
const w7 = picked.find((d) => d.front.id === "W-7")!
check("runs: longest build log wins and records its worktree", picked.length === 2 && w7.worktree === repoB && w7.front.status === "building")
const projects = join(rdir, "projects"), pdir = join(projects, "-x-repoB")
mkdirSync(pdir, { recursive: true })
const tl = (o: object) => JSON.stringify(o) + "\n"
const asst = (id: string, inp: number, cr: number, cc: number) => tl({ type: "assistant", timestamp: "2026-11-02T10:00:00Z", message: { id, role: "assistant", usage: { input_tokens: inp, cache_read_input_tokens: cr, cache_creation_input_tokens: cc, output_tokens: 5 } } })
const tpath = join(pdir, "s1.jsonl")
writeFileSync(tpath, tl({ type: "user", cwd: join(repoB, "sub"), message: {} }) + asst("m1", 1, 100, 10) + tl({ type: "user", message: {} }) + asst("m2", 3, 140000, 3000) + asst("m2", 3, 140000, 3000) + "{torn")
const tt = tailTranscript(tpath)
check("runs: transcript tail gives context size and request count", tt.context === 143003 && tt.requests === 2 && tt.exact, JSON.stringify(tt))
check("runs: transcript cwd read from the head", transcriptCwd(tpath) === join(repoB, "sub"))
writeFileSync(join(pdir, "big.jsonl"), tl({ type: "user", cwd: "/elsewhere" }) + "x".repeat(600 * 1024) + "\n" + asst("m9", 1, 2, 3))
const big = tailTranscript(join(pdir, "big.jsonl"))
check("runs: a large file is read from its tail only", big.context === 6 && big.requests === 1 && !big.exact, JSON.stringify(big))

// phase names, running filter, answered linking
const pn = loadPhaseNames(join(import.meta.dir, "..", "..", "references", "work-on"))
check("runs: phase names come from the first heading of phase-<n>.md", pn["4"] === "Fan out: concurrent and blind" && pn["0"]?.startsWith("Select") && phaseNameOf(pn, "4a") === pn["4"] && phaseNameOf(pn, "plan") === "Plan" && phaseNameOf(pn, "") === "", JSON.stringify(pn).slice(0, 200))
check("runs: running filter is building, review, pr", ["building", "review", "pr"].every(isRunning) && !["planned", "ready", "done", "dropped"].some(isRunning))
const aq = (id: string, dossier: string, state: "open" | "answered") => ({ id, state, repo: "r", dossier, root: "", text: "Which?", options: [], recommendation: "", blocks: "", ts: "t0", answer: state === "answered" ? { ts: "t1", from: "owner", kind: "answer", text: "A it is", option: "A" } : null, lines: [] }) as any
const ab = answeredBy([aq("r.W-7.Q1", "W-7", "answered"), aq("r.W-7.Q2", "W-7", "open"), aq("r.W-9.Q1", "W-9", "answered")])
check("runs: an answered question links to its dossier, open ones are not listed", ab.get("W-7")?.length === 1 && ab.get("W-7")![0].id === "r.W-7.Q1" && ab.get("W-7")![0].answer === "A it is" && ab.get("W-9")?.length === 1)

// stubs: pure parsers
check("stubs: merge subject to PR number", prNumberOf("Merge pull request #41 from org/feat-x") === 41 && prNumberOf("feat: thing (#42)") === 42 && prNumberOf("Merge branch 'x'") === null)
check("stubs: pr field forms", prFieldNumber("https://github.com/o/r/pull/7") === 7 && prFieldNumber("#8") === 8 && prFieldNumber("") === null && prFieldNumber(undefined) === null)
check("stubs: merges map to dossiers via pr:", JSON.stringify(dossiersOnMerges(["Merge pull request #41 from a/b", "x (#42)", "Merge pull request #99 from c/d", "Merge pull request #41 again"], new Map([[41, "W-1"], [42, "W-2"]]))) === '["W-1","W-2"]')
check("stubs: a merged plan is not an implemented dossier", JSON.stringify(dossiersOnMerges(["Merge pull request #392 from o/plan/W-123-stub-prune", "Merge pull request #405 from o/feat/W-127-x"], new Map([[392, "W-123"], [405, "W-127"]]))) === '["W-127"]')
check("stubs: a fix branch in the merge subject names its dossier", JSON.stringify(dossiersOnMerges(["Merge pull request #404 from o/fix/W-122-stub2026-11-01-census", "Merge pull request #7 from o/feat/X-9"], new Map(), new Set(["W-122"]))) === '["W-122"]')
{
  const m = mkdtempSync(join(tmpdir(), "asd-oq-rec-")); mkdirSync(join(m, ".discovery", "analysis"), { recursive: true })
  writeFileSync(join(m, ".discovery", "analysis", "stub-2026-11-01-release-W-1-2.md"), "# r\n## Step 5 — full run\n")
  writeFileSync(join(m, ".discovery", "analysis", "stub-2026-10-31.md"), "# r\n")
  check("stubs: record file by branch slug, or by date for stub/*", (recordFile(m, "release/W-1-2") ?? "").endsWith("stub-2026-11-01-release-W-1-2.md") && (recordFile(m, "stub/2026-10-31") ?? "").endsWith("stub-2026-10-31.md") && recordFile(m, "release/other") === null)
}
const rec = "# Stub 2026-11-02\n## Step 1 - cut\nx\n## Step 2 \u2014 merge dossiers\n### Round 1\nfoo\n## Notes\n"
check("stubs: stage is the last Step or Round heading", stubStage(rec) === "Round 1" && stubStage("## Step 1 \u2014 a\n## Step 2 \u2014 b\n") === "Step 2 \u2014 b" && stubStage("nothing") === "")
check("stubs: state from the PR", stubState(null) === "accepting" && stubState({ state: "OPEN" }) === "integrating" && stubState({ state: "MERGED" }) === "merged" && stubState({ state: "CLOSED" }) === "accepting")

// run store: updates, runner liveness, summary, markdown
const udir = mkdtempSync(join(tmpdir(), "asd-oq-upd-"))
writeFileSync(join(udir, "repoB.W-7.jsonl"), [{ ts: "1", kind: "runner", text: "started" }, "torn{", { ts: "2", kind: "stage", text: "wave", stage: "4" }, { ts: "3", kind: "bogus", text: "x" }, { ts: "4", kind: "update", text: "suite green" }].map((x) => typeof x === "string" ? x : JSON.stringify(x)).join("\n") + "\n")
const ups = readUpdates("repoB.W-7", udir)
check("runstore: updates newest first, torn and unknown kinds skipped", ups.map((u) => u.text).join() === "suite green,wave,started" && ups[1].stage === "4")
check("runstore: no file means no updates", readUpdates("nope.X-1", udir).length === 0)
writeFileSync(join(udir, "repoB.W-7.runner.json"), JSON.stringify({ pid: 4242, started: "s", state: "running", stage: "4" }))
check("runstore: a live pid keeps running, a dead pid reads as stopped", readRunner("repoB.W-7", udir, () => true)?.state === "running" && readRunner("repoB.W-7", udir, () => false)?.state === "stopped" && readRunner("none.X", udir) === null)
writeFileSync(join(udir, "repoB.W-7.runner.json"), JSON.stringify({ pid: 4242, state: "done" }))
check("runstore: a finished state is kept whatever the pid", readRunner("repoB.W-7", udir, () => false)?.state === "done")
check("runstore: summary prefers the Problem section, caps at 300", summaryOf("---\nid: A\n---\n# A\nIntro line.\n\n## Problem\nThe cache\nis stale. More.\n\n## Approach\nx") === "The cache is stale. More." && summaryOf("---\nid: A\n---\n# T\n\nFirst para\nwraps.\n\nSecond.") === "First para wraps." && summaryOf("# T\n" + "z".repeat(400)).length === 300)
check("runstore: markdown is escaped", renderMarkdown("# H <b>\n\n- a `c<d>`\n\n```\n<script>x</script>\n```\n| a | b |").includes("&lt;b&gt;") && !renderMarkdown("<script>alert(1)</script>").includes("<script>") && renderMarkdown("- a\n- b").includes("<li>b</li>"))
{
  const h = renderMarkdown("See [#431](https://github.com/o/r/pull/431) [x](javascript:alert(1))\n\n| Item | Outcome |\n|---|---|\n| D-1 | `a\\|b` **fixed** |\n\n- top\n  - nested\n1. one")
  check("runstore: pipe tables become tables, http links become links, nested lists nest", h.includes("<th>Outcome</th>") && h.includes("<td><code>a|b</code> <strong>fixed</strong></td>")
    && h.includes('<a href="https://github.com/o/r/pull/431" target="_blank" rel="noopener">#431</a>') && !h.includes('href="javascript') && h.includes("<li>top<ul><li>nested</li></ul></li>") && h.includes("<ol><li>one</li></ol>"))
  check("runstore: a pipe block without a separator row stays preformatted", renderMarkdown("| a |\n| b |").startsWith("<pre>"))
}
check("controls: a dossier a plan run minted resumes and stops under its source", runTarget("r", "W-155", "r.W-145.plan-D-1").dossier === "W-145" && runTarget("r", "W-155", "r.W-145.plan-D-1").stop === "W-145.plan-D-1"
  && runTarget("r", "W-1", "r.W-1").dossier === "W-1" && runTarget("r", "W-1", "r.W-1").stop === "W-1" && runTarget("r", "W-1").dossier === "W-1")
check("controls: Start on a minted dossier builds that dossier, never its source", runTarget("r", "W-147", "r.W-130.plan-D-1", "start").dossier === "W-147"
  && runTarget("r", "W-147", "r.W-130.plan-D-1", "resume").dossier === "W-130" && runTarget("r", "W-147", "r.W-130.plan-D-1", "stop").stop === "W-130.plan-D-1")
{
  const it = (id: string, extra: object = {}) => ({ id, state: "open", ...extra }) as any
  const items = [it("D-1"), it("D-2", { question: "q.Q1" }), it("D-3", { question: "q.Q2" }), it("D-4", { keptOpen: true }), it("D-5", { state: "fixing" })]
  const open = new Set(["q.Q2"])
  check("deferred: with no fix run every open item not left open counts", undecidedDeferred(items, false, open) === 3)
  check("deferred: during a fix run only items with a question still open count", undecidedDeferred(items, true, open) === 1)
}

// timeline: merge, order and phase boundaries (pure)
{
  const U = (ts: string, kind: string, text: string, extra: object = {}) => ({ ts, kind, text, ...extra }) as any
  // readUpdates returns newest first
  const ups = [U("2026-11-02T10:05:00Z", "update", "suite green"), U("2026-11-02T10:04:00Z", "stage", "wave one", { stage: "Phase 4a fan-out" }), U("2026-11-02T10:03:00Z", "update", "auto note", { auto: true }), U("2026-11-02T10:02:00Z", "stage", "contract", { stage: "Phase 3" }), U("2026-11-02T10:01:00Z", "runner", "started")]
  const tq: any = { id: "r.W-7.Q1", state: "answered", repo: "r", dossier: "W-7", root: "", text: "Which?", options: [], recommendation: "", blocks: "", ts: "2026-11-02T10:02:30Z", answer: null, lines: [
    { ts: "2026-11-02T10:02:30Z", from: "agent", kind: "question", text: "Which?" }, { ts: "2026-11-02T10:03:30Z", from: "owner", kind: "followup", text: "why?" }, { ts: "2026-11-02T10:04:30Z", from: "owner", kind: "answer", text: "A it is", option: "A" }] }
  const tl = mergeTimeline({ updates: ups, questions: [tq], names: { "3": "Contract", "4": "Fan out" }, view: "all" })
  check("timeline: newest first, questions and thread at their own timestamps, boundaries at phase changes", tl.map((e) => e.type === "boundary" ? "B:" + e.label : e.type + ":" + e.text).join("|") ===
    ["update:suite green", "answer:A it is", "stage:wave one", "B:Phase 4 — Fan out", "message:why?", "update:auto note", "question:Which?", "stage:contract", "B:Phase 3 — Contract", "runner:started"].join("|"), tl.map((e) => e.type + ":" + (e.label ?? e.text)).join("|"))
  check("timeline: auto lines are flagged and a 4a after 4 is no new boundary", tl.find((e) => e.text === "auto note")?.auto === true && tl.filter((e) => e.type === "boundary").length === 2 &&
    mergeTimeline({ updates: [U("3", "stage", "b", { stage: "Phase 4a" }), U("2", "stage", "a", { stage: "PHASE 4" })], questions: [] }).filter((e) => e.type === "boundary").length === 1)
  const lg = logEntries("## Build log\n- Phase 3 contract\n  wrapped\n- second\n```text\nx\n```\n")
  check("timeline: build-log lines parse, and the timeline never falls back to them", JSON.stringify(lg) === '["Phase 3 contract wrapped","second"]' && mergeTimeline({ updates: [], questions: [] }).length === 0 && mergeTimeline({ updates: ups, questions: [] }).every((e) => e.type !== "log"))
  const re = parseRecord("# Stub\n## Step 1 — cut (2026-11-01)\nbranch cut\n### Round 1 (2026-11-02)\nfoo\n## Notes\nbar\n", "2026-10-31")
  check("timeline: a stub record gives one entry per heading, Step headings as boundaries, undated by position", re.length === 3 && re[0].type === "boundary" && re[0].ts === "2026-11-01" && re[1].type === "entry" && re[1].ts === "2026-11-02" && re[2].ts === "2026-11-02" && re[2].text === "bar" && parseRecord("## A\nx", "2026-10-31")[0].ts === "2026-10-31")
  const stub = mergeTimeline({ updates: [], questions: [], extra: re })
  check("timeline: the stub timeline is newest first", stub[0].label === "Notes" && stub[stub.length - 1].type === "boundary")
}

// classification, grouping, Now panel (pure)
{
  const U = (ts: string, kind: string, text: string, extra: object = {}) => ({ ts, kind, text, ...extra }) as any
  const E = (e: object) => ({ ts: "", text: "", ...e }) as any
  const k = (kind: string, text: string, auto = false) => classify(E({ type: kind, text, auto: auto || undefined })).kind
  check("classify: runner lines", k("runner", "start /work-on W-1 in /r") === "start" && k("runner", "waiting on Q1") === "waiting" && k("runner", "answered Q1") === "answered" && k("runner", "failed: boom") === "failed" && k("runner", "done") === "done" && k("runner", "stopped") === "stopped" && k("runner", "stage 3 start") === "info")
  check("classify: update lines, auto key events still stand out", k("update", "run_tests: suite green (42 passed)", true) === "suite-green" && k("update", "run_tests: 3 tests failed", true) === "suite-red" && k("update", "run_gates: gates passed", true) === "gate" && k("update", "PR #12 opened", true) === "pr" && k("update", "workflow started: review", true) === "workflow" && k("update", "dossier_edit log line", true) === "routine" && k("update", "wave one returned") === "note")
  const ev = (text: string, event: string) => classify(E({ type: "update", text, auto: true, event } as any)).kind
  check("classify: an agent note is prose, never a suite, gate or PR event", k("update", "The repository gates never had one clean full pass: one gate failed, then passed alone after a test fixture rebuild.") === "note" && k("update", "W-128 is done and its PR is ready for your review.") === "done" && k("update", "the PR was opened") === "note")
{
  const J = (o: any) => JSON.stringify(o)
  const A = (ts: string, blocks: any[]) => J({ type: "assistant", timestamp: ts, message: { content: blocks } })
  const U = (ts: string, content: any) => J({ type: "user", timestamp: ts, message: { content } })
  const bash = (id: string, description: string, bg = false) => ({ type: "tool_use", id, name: "Bash", input: { command: "x", description, ...(bg ? { run_in_background: true } : {}) } })
  const res = (id: string, text: string) => ({ type: "tool_result", tool_use_id: id, content: text })
  const lines = [
    A("2026-11-04T18:53:22Z", [bash("t1", "Run the VM suite", true)]), U("2026-11-04T18:53:25Z", [res("t1", "Command running in background with ID: b1. Output is being written to: /x")]),
    A("2026-11-04T18:53:30Z", [{ type: "tool_use", id: "t2", name: "Workflow", input: { name: "agentic-software-development:phase4-wave" } }]), U("2026-11-04T18:53:31Z", [res("t2", "Workflow launched in background. Task ID: w1")]),
    U("2026-11-04T19:00:00Z", "<task-notification>\n<task-id>w1</task-id>\n<tool-use-id>t2</tool-use-id>\n</task-notification>"),
    A("2026-11-04T19:01:00Z", [bash("t3", "Read the digest")]),
  ]
  const a = activityOf(lines)
  check("activity: the call in flight, the background work still unnotified", a.doing === "Read the digest" && a.doingSince === "2026-11-04T19:01:00Z" && a.background.length === 1 && a.background[0].label === "Run the VM suite" && a.lastTs === "2026-11-04T19:01:00Z", J(a))
  const b = activityOf([...lines, U("2026-11-04T19:01:05Z", [res("t3", "ok")])])
  check("activity: a finished call leaves only the background wait", b.doing === "" && b.background.length === 1, J(b))
  check("activity: tool labels", toolLabel("Skill", { skill: "p:phase7-review" }) === "skill phase7-review" && toolLabel("Agent", { subagent_type: "p:implementer" }) === "agent implementer" && toolLabel("Workflow", { name: "p:phase4-wave" }) === "workflow p:phase4-wave")
}
check("timeline: a step line and a counted suite result are on the timeline; a bare suite line is not", inTimeline({ type: "update", auto: true, event: "step", text: "Running tests", ts: "" } as any) && inTimeline({ type: "update", auto: true, event: "suite", text: "suite: passed 3  failed 0", ts: "" } as any) && !inTimeline({ type: "update", auto: true, event: "suite", text: "suite started", ts: "" } as any))
{
  const P = (number: number, headRefName: string) => ({ number, headRefName, mergedAt: "2026-11-05T09:00:00Z", base: "stub/2026-11-03" })
  const ds = [{ id: "W-128", pr: "427" }, { id: "W-130" }, { id: "W-141" }]
  check("merges: a PR is its dossier's by pr: number or by the id in its head branch; a plan head is none", dossierOfPr(P(427, "x"), ds) === "W-128" && dossierOfPr(P(429, "fix/W-128-deferred"), ds) === "W-128" && dossierOfPr(P(1, "feat/w-130-thing"), ds) === "W-130" && dossierOfPr(P(2, "plan/W-130"), ds) === undefined)
  const first = nextMerges(null, [P(427, "feat/w-128-a")], ds, () => false, "t0")
  check("merges: the first look only records a baseline", first.start.length === 0 && first.seen["427"].state === "baseline")
  const second = nextMerges(first.seen, [P(427, "feat/w-128-a"), P(429, "fix/W-128-deferred"), P(431, "fix/W-141-deferred"), P(432, "feat/w-141-b")], ds, (id) => id === "W-141", "t1")
  check("merges: a new merge starts one run per dossier; a busy dossier waits", JSON.stringify(second.start) === JSON.stringify([{ id: "W-128", prs: [429] }]) && second.seen["429"].state === "started" && second.seen["431"].state === "pending" && second.seen["432"].state === "pending", JSON.stringify(second))
  const third = nextMerges(second.seen, [], ds, () => false, "t2")
  check("merges: once free, the waiting dossier gets all its PRs in one run, and a started PR never starts again", JSON.stringify(third.start) === JSON.stringify([{ id: "W-141", prs: [431, 432] }]))
}
{
  // The dashboard's own fetch: a merge pushed to the stub reaches origin/stub/* in the main checkout; nothing else moves.
  const gd = mkdtempSync(join(tmpdir(), "fetch-"))
  const g = (cwd: string, ...a: string[]) => Bun.spawnSync(["git", "-C", cwd, "-c", "user.email=t@t", "-c", "user.name=t", ...a], { stdout: "pipe", stderr: "pipe" }).stdout.toString().trim()
  Bun.spawnSync(["git", "init", "-q", "--bare", "-b", "development", join(gd, "origin.git")])
  Bun.spawnSync(["git", "clone", "-q", join(gd, "origin.git"), join(gd, "pusher")])
  const P = join(gd, "pusher")
  g(P, "commit", "-q", "--allow-empty", "-m", "base"); g(P, "push", "-q", "origin", "HEAD:development", "HEAD:stub/2026-11-03")
  Bun.spawnSync(["git", "clone", "-q", join(gd, "origin.git"), join(gd, "main")])
  const M = join(gd, "main")
  const head0 = g(M, "rev-parse", "HEAD")
  g(P, "commit", "-q", "--allow-empty", "-m", "Merge pull request #7 from o/feat/w-7"); g(P, "push", "-q", "origin", "HEAD:stub/2026-11-03")
  const moved = await fetchStubRefs(M, 1e15)
  check("fetch: a merge pushed to the stub reaches origin/stub/* in the main checkout", moved && g(M, "log", "-1", "--format=%s", "origin/stub/2026-11-03") === "Merge pull request #7 from o/feat/w-7")
  check("fetch: the checked-out branch does not move", g(M, "rev-parse", "HEAD") === head0)
  check("fetch: at most once per interval", (await fetchStubRefs(M, 1e15 + 1000)) === false)
  rmSync(gd, { recursive: true, force: true })
}
{
  // An item ruled to its own dossier links to the dossier /plan made from it, from that dossier's DEFERRED-SOURCE;
  // the same issue in another dossier follows its seed; a live /plan run shows as planning.
  const led = (id: string, extra: string) => parseDeferred(`DEFERRED:\n- D-1 bug a — x.rs:1 — r — w\n- D-7 bug b — y.rs:2 — r — w\n${extra}`)
  const mk = (id: string, items: any[]) => ({ repo: "r", id, deferred: { items } }) as any
  const w128 = mk("W-128", led("W-128", "- DEFERRED-TRIAGE: D-1 → dossier — design; seed: /plan W-128 D-1\n").items)
  const w130 = mk("W-130", led("W-130", "- DEFERRED-TRIAGE: D-7 → dossier — same as W-128 D-1; seed: /plan W-128 D-1\n").items)
  const late = parseDeferred("DEFERRED:\n- D-1 bug a — x.rs:1 — r — w\n- DEFERRED-TRIAGE: D-11 → dossier — a wake port across processes; seed: /plan W-141 D-11\n")
  check("deferred: an item triaged without a ledger line is still an item, routed, with its seed", late.items.length === 2 && late.items[1].id === "D-11" && late.items[1].state === "dossier" && late.items[1].title === "a wake port across processes" && late.items[1].seed?.src === "W-141", JSON.stringify(late.items[1]))
  check("plan link: DEFERRED-SOURCE lines are read", JSON.stringify(deferredSources("x\n- DEFERRED-SOURCE: W-128 D-1\n")) === JSON.stringify([{ src: "W-128", item: "D-1" }]))
  linkPlannedItems([w128, w130], new Map(), (k) => (k === "r.W-128.plan-D-1" ? "running" : undefined))
  check("plan link: a live /plan run shows on the item and on the same issue elsewhere", w128.deferred.items[0].planning === "running" && w130.deferred.items[1].planning === "running" && w130.deferred.items[1].seed.src === "W-128")
  linkPlannedItems([w128, w130], new Map([["r/W-128/D-1", "W-150"]]), () => "done")
  check("plan link: once planned, both items link to the one new dossier", w128.deferred.items[0].dossier === "W-150" && w130.deferred.items[1].dossier === "W-150")
}
{
  const { limitSuperseded } = await import("../questions/accounts.ts")
  check("accounts: a limit marked before an open reading no longer shows", limitSuperseded({ marked: "2026-11-06T08:53:41Z" }, { ts: "2026-11-06T11:01:17Z", windows: { five_hour: { used_percentage: 2, status: "allowed" }, seven_day: { used_percentage: 0, status: "allowed" } } })
    && !limitSuperseded({ marked: "2026-11-06T08:53:41Z" }, { ts: "2026-11-06T08:00:00Z", windows: { five_hour: { used_percentage: 2, status: "allowed" } } }))
}
{
  // Leave open on the dashboard: an open item is kept open (not counted), Reopen undoes it; a fixed item is untouched.
  const od = mkdtempSync(join(tmpdir(), "owner-"))
  const led = parseDeferred("DEFERRED:\n- D-1 bug a — x.rs:1 — r — w\n- D-2 bug b — y.rs:2 — r — w\n- DEFERRED-FIXED: D-1 abc\n")
  writeFileSync(ownerRulingsFile("r.W-1", od), JSON.stringify({ "D-2": { keepOpen: true }, "D-1": { keepOpen: true } }))
  const kept = withOwnerRulings({ items: led.items, pr: "", report: false, branch: "" } as any, "r.W-1", od)
  check("leave open: an open item is kept open; a fixed one is untouched", kept.items[1].keptOpen === true && kept.items[1].ownerKept === true && kept.items[0].state === "fixed" && !kept.items[0].keptOpen)
  writeFileSync(ownerRulingsFile("r.W-1", od), JSON.stringify({ "D-2": { keepOpen: false } }))
  check("leave open: reopen undoes it", withOwnerRulings({ items: kept.items, pr: "", report: false, branch: "" } as any, "r.W-1", od).items[1].keptOpen === false)
}
  check("classify: a hook suite line goes by its failed count", ev("suite: passed 31  failed 0  ignored 0  (cargo)", "suite") === "suite-green" && ev("suite: passed 2  failed 41  ignored 0  (dotnet)", "suite") === "suite-red" && ev("suite run finished", "suite") === "routine" && ev("gates: 7 passed", "gates") === "gate" && ev("PR opened: https://x/pull/1", "pr") === "pr")
  check("classify: key flags and routine flags", classify(E({ type: "stage", text: "x" })).key && classify(E({ type: "question", text: "x" })).key && !classify(E({ type: "update", text: "x", auto: true })).key && classify(E({ type: "update", text: "x", auto: true })).routine && !classify(E({ type: "update", text: "x" })).routine)
  const ups = [
    U("2026-11-02T10:30:00Z", "update", "r1", { auto: true }), U("2026-11-02T10:29:00Z", "update", "r2", { auto: true }), U("2026-11-02T10:28:00Z", "update", "r3", { auto: true }), U("2026-11-02T10:27:00Z", "update", "run_tests: suite green", { auto: true }),
    U("2026-11-02T10:20:00Z", "stage", "wave: all three returned\nmore", { stage: "Phase 4" }), U("2026-11-02T10:10:00Z", "stage", "contract committed", { stage: "Phase 3" }), U("2026-11-02T10:05:00Z", "update", "single routine", { auto: true }), U("2026-11-02T10:01:00Z", "runner", "start /work-on W-7 in /r"),
  ]
  const tq: any = { id: "r.W-7.Q1", state: "answered", repo: "r", dossier: "W-7", root: "", text: "Which?", options: [], recommendation: "", blocks: "", ts: "2026-11-02T10:12:00Z", answer: null, lines: [
    { ts: "2026-11-02T10:12:00Z", from: "agent", kind: "question", text: "Which?" }, { ts: "2026-11-02T10:25:00Z", from: "owner", kind: "answer", text: "A", option: "A" }] }
  const entries = mergeTimeline({ updates: ups, questions: [tq], names: { "3": "Contract", "4": "Fan out" }, view: "all" })
  const groups = groupByPhase(entries)
  check("group: phases newest first, only the first is current", groups.map((g) => g.label).join("|") === "Phase 4 — Fan out|Phase 3 — Contract|Setup" && groups[0].current && !groups[1].current && !groups[2].current, groups.map((g) => g.label).join("|"))
  check("group: summary is the first line of the checkpoint, counts per phase", groups[0].summary === "wave: all three returned" && groups[1].summary === "contract committed" && groups[1].counts.questions === 1 && groups[0].counts.updates === 5, JSON.stringify(groups.map((g) => [g.summary, g.counts])))
  check("group: a question and its answer belong to the phase of the question", groups[1].items.some((i) => i.kind === "question") && groups[1].items.some((i) => i.kind === "answered") && !groups[0].items.some((i) => i.kind === "answered"))
  check("group: runs of routine lines collapse into +N, a lone one stays, key events stay out", groups[0].items.map((i) => i.kind).join("|") === "more|suite-green|stage" && (groups[0].items[0] as any).entries.length === 3 && groups[2].items.some((i) => i.kind === "routine" || i.kind === "more"), groups[0].items.map((i) => i.kind).join("|"))
  check("group: finished phases carry a duration, the oldest line of a phase is last", groups[1].durationMs === 10 * 60_000 && groups[0].items[groups[0].items.length - 1].kind === "stage", String(groups[1].durationMs))
  const np = nowPanel(entries, groups, { runner: "running", waiting: ["r.W-7.Q2"], fallbackPhase: "4" })
  check("now: phase, step, since, newest meaningful update (not routine), runner, waiting", np.phase.startsWith("Phase 4") && np.step === 4 && np.since === "2026-11-02T10:20:00Z" && np.doing?.text === "run_tests: suite green" && np.doing.kind === "suite-green" && np.runner === "running" && np.waiting[0] === "r.W-7.Q2", JSON.stringify(np))
  const ne = nowPanel([], [], { fallbackPhase: "plan" })
  check("now: an empty run has no groups and no current update", groupByPhase([]).length === 0 && ne.doing === null && ne.phase === "" && ne.step === null)
  const sg = groupByPhase(mergeTimeline({ updates: [U("2026-11-02T11:00:00Z", "update", "stub step done")], questions: [], extra: parseRecord("## Step 1 — cut (2026-11-01)\nbranch cut\n## Step 2 — integrate (2026-11-02)\nfirst\n", "2026-10-31").map((e) => ({ ...e, text: "" })), unit: "Step" }), "Before the first step")
  check("group: a stub groups by Step headings", sg.map((g) => g.label).join("|") === "Step 2 — integrate (2026-11-02)|Step 1 — cut (2026-11-01)" && sg[0].items.some((i) => i.kind !== "more" && i.entry.text === "stub step done"), sg.map((g) => g.label).join("|"))
}

// repos: ids are unique only inside one repo
{
  const t = mkdtempSync(join(tmpdir(), "asd-oq-two-"))
  for (const r of ["alpha", "beta"]) {
    mkdirSync(join(t, r, ".discovery", "dossiers"), { recursive: true })
    writeFileSync(join(t, r, ".discovery", "dossiers", "W-001-x.md"), `---\nid: W-001\ntitle: ${r} one\nstatus: ready\n---\n# W-001\n`)
  }
  const picked2 = await pickDossiersByRepo([join(t, "alpha"), join(t, "beta")], async (wt) => wt)
  check("repos: two repos sharing a dossier id both keep it", picked2.length === 2 && picked2.map((d) => d.front.title).sort().join("|") === "alpha one|beta one" && picked2.every((d) => d.main === d.worktree))
  const runsX = [{ repo: "alpha", id: "W-001", main: "/a", worktree: "/a" }, { repo: "beta", id: "W-001", main: "/b", worktree: "/b-W-001" }, { repo: "beta", id: "W-002", main: "/b", worktree: "/b" }]
  const qq = (repo: string, root: string, dossier = "W-001") => ({ repo, root, dossier })
  check("repos: a question belongs to the repo that named it or whose checkout holds its root", questionRepo(qq("alpha", ""), runsX) === "alpha" && questionRepo(qq("zzz", "/b-W-001/src"), runsX) === "beta" && questionRepo(qq("zzz", ""), runsX) === undefined && questionRepo(qq("zzz", "", "W-002"), runsX) === "beta")
  rmSync(t, { recursive: true, force: true })
  check("view: done shows the dossier, running or with updates the run, otherwise the dossier", viewFor("done", false, 5) === "dossier" && viewFor("building", false, 0) === "run" && viewFor("ready", true, 0) === "run" && viewFor("ready", false, 2) === "run" && viewFor("ready", false, 0) === "dossier" && viewFor("planned", false, 1) === "run")
  const mkStub = (branch: string, state: string, cut: string) => ({ branch, cut, state, stage: "", refAge: cut, pr: null, dossiers: [] }) as any
  {
    const st = [{ branch: "stub/2026-10-24", cut: "2026-10-24", state: "integrating" as const, pr: {} }, { branch: "stub/2026-10-31", cut: "2026-10-31", state: "accepting" as const, pr: null }, { branch: "stub/2026-09-01", cut: "2026-09-01", state: "merged" as const, pr: {} }]
    const ds = [{ id: "W-1", status: "building", pr: "#41" }, { id: "W-2", status: "review", pr: "" }, { id: "W-3", status: "building" }, { id: "W-4", status: "ready" }, { id: "W-5", status: "planned", live: false }, { id: "W-6", status: "building", pr: "" }]
    const m = stubMembers(st, ds, { "stub/2026-10-24": [{ number: 41, headRefName: "feat/x" }, { number: 50, headRefName: "plan/W-6-plan" }, { number: 51, headRefName: "feat/W-2-thing" }, { number: 52, headRefName: "feat/W-4-r" }], "stub/2026-10-31": [] })
    check("stubMembers: a PR into the stub places its dossier (pr: field or id in head)", m["stub/2026-10-24"]?.map((x) => x.id).sort().join() === "W-1,W-2" && m["stub/2026-10-24"].every((x) => !x.noPr))
    check("stubMembers: building without a PR goes under the accepting stub with noPr", m["stub/2026-10-31"]?.map((x) => x.id + ":" + x.noPr).sort().join() === "W-3:true,W-6:true")
    check("stubMembers: plan merges never count and ready/planned never appear", !JSON.stringify(m).includes("W-4") && !JSON.stringify(m).includes("W-5") && !m["stub/2026-10-24"].some((x) => x.id === "W-6") && !m["stub/2026-09-01"])
    const m2 = stubMembers([{ branch: "stub/a", cut: "2026-01-01", state: "integrating", pr: {} }], [{ id: "W-9", status: "ready", live: true }], {})
    check("stubMembers: with no accepting stub the newest open stub takes it; a live runner counts", m2["stub/a"]?.[0]?.id === "W-9" && m2["stub/a"][0].noPr)
    check("stubMembers: no open stub, no members", Object.keys(stubMembers([{ branch: "stub/z", cut: "2026-01-01", state: "merged", pr: {} }], [{ id: "W-3", status: "building" }], {})).length === 0)
  }
  const os = openStubs([mkStub("stub/2026-10-24", "merged", "2026-10-24"), mkStub("stub/2026-10-31", "accepting", "2026-10-31"), mkStub("release/W-115", "integrating", "2026-10-29")])
  check("stubs: every stub that is not merged is open, newest first", os.map((x) => x.branch).join("|") === "stub/2026-10-31|release/W-115")
}

// Slack progress thread: which lines become messages (pure, no network)
{
  const o = { repo: "demo", id: "W-7", title: "Seven", link: "http://127.0.0.1:1/repo/demo/W-7" }
  const U = (kind: string, text: string, extra: object = {}) => ({ ts: "t", kind, text, ...extra }) as any
  const m = (u: any) => slackMessageFor(u, o)
  check("slack run: the first start line opens the thread with repo, id and title", m(U("runner", "start /work-on W-7 in /r"))?.role === "open" && m(U("runner", "start /work-on W-7 in /r"))!.text.startsWith("▶ demo · W-7 — Seven") && m(U("runner", "start /work-on W-7 in /r"))!.text.includes(o.link))
  check("slack run: stage done, waiting, answered, failed and done reply", m(U("stage", "contract committed", { stage: "Phase 3" }))?.role === "reply" && m(U("runner", "waiting on Q1", { state: "waiting" }))?.text.includes("Waiting on you") === true && m(U("runner", "answered Q1"))?.role === "reply" && m(U("runner", "failed: boom", { state: "failed" }))?.role === "reply" && m(U("runner", "done", { state: "done" }))?.text.includes("Done") === true)
  check("slack run: auto routine lines and plain notes never post", m(U("update", "suite green", { auto: true })) === null && m(U("stage", "x", { auto: true })) === null && m(U("update", "a note")) === null && m(U("runner", "stage 3 start")) === null)
  const chrono = [U("update", "early", {}), U("runner", "waiting on Q0"), U("runner", "start /work-on W-7"), U("update", "r", { auto: true }), U("stage", "contract", { stage: "Phase 3" }), U("runner", "start /work-on W-7 (resume)"), U("runner", "done", { state: "done" })]
  const p = pendingSlack(chrono, 0, false, o)
  check("slack run: a thread starts at its opener, a resume does not open a second one", p.upto === 7 && p.msgs.map((x) => x.role).join("|") === "open|reply|reply" && pendingSlack(chrono, 5, true, o).msgs.length === 1 && pendingSlack(chrono, 7, true, o).msgs.length === 0, p.msgs.map((x) => x.role).join("|"))
}
// controls: eligibility
{
  const st = (m: Record<string, string>) => (id: string) => m[id]
  const run = (status: string, blockedBy: string[], runner: any = null) => ({ status, blockedBy, runner })
  const rn = (state: string, root = "/r") => ({ state, stage: "", pid: 1, started: "", root }) as any
  check("controls: blocked_by ids are read from a list value", JSON.stringify(blockedByOf("[W-1, W-22]")) === '["W-1","W-22"]' && blockedByOf("[]").length === 0 && blockedByOf(undefined).length === 0)
  check("controls: start needs ready and every blocker done", controlsFor(run("ready", []), st({})).start.ok && controlsFor(run("ready", ["W-1"]), st({ "W-1": "done" })).start.ok && !controlsFor(run("ready", ["W-1"]), st({ "W-1": "building" })).start.ok && controlsFor(run("ready", ["W-1", "W-2"]), st({ "W-1": "done" })).start.reason === "blocked by W-2" && !controlsFor(run("building", []), st({})).start.show)
  check("controls: a planned dossier's Start plans it, a ready one's builds it; resume reruns the flow that stopped", controlsFor(run("planned", []), st({})).start.show && controlsFor(run("planned", []), st({})).start.command === "plan" && controlsFor(run("planned", ["W-9"]), st({ "W-9": "building" })).start.ok
    && controlsFor(run("ready", []), st({})).start.command === "work-on" && controlsFor(run("planned", [], { ...rn("stopped"), command: "plan" } as any), st({})).resume.command === "plan")
  check("controls: resume for a stopped or failed runner of a dossier that is not done", controlsFor(run("building", [], rn("stopped")), st({})).resume.show && controlsFor(run("ready", [], rn("failed")), st({})).resume.ok && !controlsFor(run("done", [], rn("stopped")), st({})).resume.show && !controlsFor(run("building", [], rn("running")), st({})).resume.show && !controlsFor(run("building", [], rn("stopped", "")), st({})).resume.ok && !controlsFor(run("ready", [], rn("failed")), st({})).start.show)
  check("controls: stop for a running or waiting runner only", controlsFor(run("building", [], rn("waiting")), st({})).stop.show && controlsFor(run("building", [], rn("running")), st({})).stop.show && !controlsFor(run("building", [], rn("stopped")), st({})).stop.show && !controlsFor(run("ready", []), st({})).stop.show)
}
check("stubs: no open-PR inclusion left in the code", !readFileSync(join(import.meta.dir, "..", "questions", "stubs.ts"), "utf8").includes("openPrsTargeting"))

// stubs: a real git fixture (bare origin + clone) with a stub branch, a merge and a record
const git = (cwd: string, ...a: string[]) => execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", ...a], { cwd, stdio: "pipe" }).toString()
const origin = join(rdir, "origin.git"), repoC = join(rdir, "repoC")
mkdirSync(origin); git(origin, "init", "--bare", "-b", "development")
git(rdir, "clone", origin, repoC)
git(repoC, "checkout", "-b", "development"); writeFileSync(join(repoC, "a.txt"), "a"); git(repoC, "add", "."); git(repoC, "commit", "-m", "init"); git(repoC, "push", "origin", "development")
git(repoC, "checkout", "-b", "stub/2026-11-02"); git(repoC, "push", "origin", "stub/2026-11-02")
git(repoC, "checkout", "-b", "feat/w9"); writeFileSync(join(repoC, "b.txt"), "b"); git(repoC, "add", "."); git(repoC, "commit", "-m", "work")
git(repoC, "checkout", "stub/2026-11-02"); git(repoC, "merge", "--no-ff", "feat/w9", "-m", "Merge pull request #12 from o/feat-w9"); git(repoC, "push", "origin", "stub/2026-11-02")
git(repoC, "fetch", "origin")
mkdirSync(join(repoC, ".discovery", "dossiers"), { recursive: true }); mkdirSync(join(repoC, ".discovery", "analysis"), { recursive: true })
writeFileSync(join(repoC, ".discovery", "dossiers", "W-9-nine.md"), "---\nid: W-9\ntitle: Nine\nstatus: done\npr: https://x/pull/12\n---\n# W-9\n\n## Problem\nNine is broken.\n")
writeFileSync(join(repoC, ".discovery", "dossiers", "W-10-ten.md"), "---\nid: W-10\ntitle: Ten\nstatus: ready\n---\n# W-10\n\nTen paragraph.\n")
writeFileSync(join(repoC, ".discovery", "analysis", "stub-2026-11-02-x.md"), rec)
writeFileSync(join(repoC, ".discovery", "dossiers", "W-11-eleven.md"), "---\nid: W-11\ntitle: Eleven\nstatus: ready\nblocked_by: [W-9]\n---\n# W-11\n\nEleven paragraph.\n")
writeFileSync(join(repoC, ".discovery", "dossiers", "W-12-twelve.md"), "---\nid: W-12\ntitle: Twelve\nstatus: ready\nblocked_by: [W-9, W-10]\n---\n# W-12\n")
writeFileSync(join(repoC, ".discovery", "dossiers", "W-13-thirteen.md"), "---\nid: W-13\ntitle: Thirteen\nstatus: building\n---\n# W-13\n\n## Build log\n- Phase 3 x\n")
writeFileSync(join(repoC, ".discovery", "dossiers", "W-14-fourteen.md"), "---\nid: W-14\ntitle: Fourteen\nstatus: review\npr: https://x/pull/13\n---\n# W-14\n")
writeFileSync(join(repoC, ".discovery", "analysis", "stub-2026-11-02.md"), rec)
const recordedRoot = join(rdir, "recorded-root")
const argvLog = join(rdir, "argv.log"), fakeRunner = join(rdir, "fake_runner.py")
writeFileSync(fakeRunner, "import os, sys\nopen(os.environ['ASD_FAKE_ARGV'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\nprint('fake runner ok, pid 4242')\n")
// views: Timeline vs Activity, spawn rows, legacy lines, non-git repo label (pure)
{
  const A = (ts: string, text: string, extra: object = {}) => ({ ts, kind: "update", text, auto: true, ...extra }) as any
  const N = (ts: string, text: string, kind = "update") => ({ ts, kind, text }) as any
  const ups = [ // newest first, as the store returns them
    A("2026-11-02T10:12:00Z", "other thing", { event: "other" }), A("2026-11-02T10:11:00Z", "tool crashed and failed", { event: "other" }),
    A("2026-11-02T10:10:00Z", "x: orphan", { event: "spawn-done", agent_id: "zz", status: "done" }),
    A("2026-11-02T10:09:00Z", "workflow started: build — 3 agents", { event: "workflow" }),
    A("2026-11-02T10:08:00Z", "PR 12 opened", { event: "pr" }), A("2026-11-02T10:07:30Z", "gates: green", { event: "gates" }), A("2026-11-02T10:07:00Z", "suite: 10 passed", { event: "suite" }),
    A("2026-11-02T10:06:00Z", "no id agent: lone", { event: "spawn", status: "done" }),
    A("2026-11-02T10:05:30Z", "implementer: pkg b", { event: "spawn-done", agent_id: "b", status: "contract-change" }),
    A("2026-11-02T10:05:00Z", "unit-test-author: pkg a", { event: "spawn-done", agent_id: "a", status: "gap" }),
    A("2026-11-02T10:04:00Z", "implementer: pkg b", { event: "spawn", agent_id: "b", status: "running" }),
    A("2026-11-02T10:03:30Z", "reviewer: never finished", { event: "spawn", agent_id: "c", status: "running" }),
    A("2026-11-02T10:03:00Z", "unit-test-author: pkg a", { event: "spawn", agent_id: "a", status: "running" }),
    A("2026-11-02T10:02:00Z", "log: wrote section", { event: "log" }), N("2026-11-02T10:01:30Z", "agent note"), N("2026-11-02T10:01:00Z", "runner: started", "runner"),
    N("2026-11-02T10:00:00Z", "wave", "stage")]
  const tl = mergeTimeline({ updates: ups, questions: [] }), ac = mergeTimeline({ updates: ups, questions: [], view: "activity" })
  const texts = (es: any[]) => es.filter((e) => e.type !== "boundary").map((e) => e.text)
  check("views: Timeline is for people — notes, PRs and one line per spawn; no commands, checkpoints or runner bookkeeping",
    ["agent note", "PR 12 opened"].every((t) => texts(tl).includes(t)) && texts(tl).filter((t) => t.includes(": pkg")).length === 2 &&
    !texts(tl).some((t) => ["log: wrote section", "other thing", "x: orphan", "runner: started", "wave", "suite: 10 passed", "gates: green", "workflow started: build — 3 agents", "tool crashed and failed"].includes(t)), JSON.stringify(texts(tl)))
  check("views: Activity is every automatic line, raw (log, other, spawn and spawn-done), and no notes", texts(ac).length === 14 && texts(ac).includes("log: wrote section") && texts(ac).includes("other thing") && texts(ac).filter((t) => t.includes("pkg")).length === 4 && !texts(ac).includes("agent note"), JSON.stringify(texts(ac)))
  const sp = (id: string) => tl.find((e) => e.event === "spawn" && e.agentId === id)!
  check("spawn: merged with its spawn-done: status from the done line, duration from both timestamps", sp("a").status === "gap" && sp("a").durationMs === 120_000 && sp("b").status === "contract-change" && sp("b").durationMs === 90_000)
  check("spawn: no done line keeps its own status, no duration", sp("c").status === "running" && sp("c").durationMs === undefined)
  check("spawn: no agent_id keeps its own status", tl.find((e) => e.text === "no id agent: lone")?.status === "done" && mergeSpawns([...ups].reverse()).length === ups.length - 2)
  const legacy = [A("2026-11-02T11:05:00Z", "spawned reviewer: old"), A("2026-11-02T11:04:00Z", "workflow x started"), A("2026-11-02T11:03:00Z", "PR 3 opened"), A("2026-11-02T11:02:00Z", "gates: ok"), A("2026-11-02T11:01:00Z", "suite: ok"), A("2026-11-02T11:00:00Z", "log: old"), A("2026-11-02T10:59:00Z", "something")]
  const lt = mergeTimeline({ updates: legacy, questions: [] }), la = mergeTimeline({ updates: legacy, questions: [], view: "activity" })
  check("legacy: lines without event are classified by prefix", JSON.stringify(lt.map((e) => e.event)) === JSON.stringify(["spawn", "pr"]) && lt[0].status === undefined && lt[0].text === "reviewer: old" && la.length === 7 && la[6].event === "other" && la[1].event === "workflow")
  check("groups: Timeline is not collapsed, Activity collapses routine runs", groupByPhase(tl, "Setup", false).every((g) => g.items.every((i) => i.kind !== "more")) && groupByPhase(ac).some((g) => g.items.some((i) => i.kind === "more")))
  const home = mkdtempSync(join(tmpdir(), "asd-oq-home-")); mkdirSync(join(home, "Twipe", "repos", ".discovery", "dossiers"), { recursive: true }); mkdirSync(join(home, "git", ".git"), { recursive: true })
  check("repo label: a non-git root shows its path under the home dir; a git repo its basename", repoLabel(join(home, "Twipe", "repos"), home) === "Twipe/repos" && repoLabel(join(home, "git"), home) === "git" && repoLabel("/elsewhere/x", home) === "x")
  rmSync(home, { recursive: true, force: true })
}

const odir = mkdtempSync(join(tmpdir(), "asd-oq-ow-")), runsDir = mkdtempSync(join(tmpdir(), "asd-oq-rd-"))
writeFileSync(join(runsDir, "repoC.W-10.runner.json"), JSON.stringify({ pid: process.pid, state: "waiting", stage: "3", started: "s" }))
writeFileSync(join(runsDir, "repoC.W-13.runner.json"), JSON.stringify({ pid: 2 ** 22 + 77, state: "running", stage: "3", started: "s", root: recordedRoot }))
writeFileSync(join(runsDir, "repoC.W-10.runner.log"), Array.from({ length: 300 }, (_, i) => "line " + (i + 1)).join("\n") + "\n")
writeFileSync(join(runsDir, "repoB.W-7.jsonl"), [{ ts: "2026-11-02T10:00:00Z", kind: "stage", text: "contract", stage: "Phase 3" }, { ts: "2026-11-02T10:05:00Z", kind: "stage", text: "wave", stage: "Phase 4" }, { ts: "2026-11-02T10:06:00Z", kind: "update", text: "tiny" }].map((x) => JSON.stringify(x)).join("\n") + "\n")
writeFileSync(join(runsDir, "repoA.W-8.jsonl"), JSON.stringify({ ts: "2026-11-02T09:00:00Z", kind: "update", text: "planning started" }) + "\n")
writeFileSync(join(runsDir, "repoC.stub-2026-11-02.jsonl"), JSON.stringify({ ts: "2026-11-02T11:00:00Z", kind: "update", text: "stub step done" }) + "\n")
writeFileSync(join(runsDir, "repoC.W-10.jsonl"), JSON.stringify({ ts: "2026-11-02T10:00:00Z", kind: "update", text: "ten started" }) + "\n")

// server over HTTP
const sdir = mkdtempSync(join(tmpdir(), "asd-oq-srv-"))
writeFileSync(join(sdir, "demo.W-2.Q1.jsonl"), JSON.stringify(qLine) + "\n")
const proc = Bun.spawn(["/opt/homebrew/bin/bun", "--no-install", join(import.meta.dir, "..", "questions", "server.ts")], {
  env: { ...process.env, ASD_QUESTIONS_DIR: sdir, ASD_QUESTIONS_PORT: "0", ASD_QUESTIONS_NO_NOTIFY: "1", ASD_QUESTIONS_NO_SLACK: "1", ASD_SLACK_USER: "", ASD_RUNS_ROOTS: `${repoA}:${repoB}:${repoC}`, ASD_OPEN_WORK_DIR: odir, ASD_RUNS_DIR: runsDir, ASD_CLAUDE_PROJECTS: projects, ASD_RUNS_NO_GH: "1", ASD_RUNNER_BIN: fakeRunner, ASD_FAKE_ARGV: argvLog },
  stdout: "pipe", stderr: "pipe",
})
const reader = proc.stdout.getReader()
let out = ""
while (!/listening http:\/\/127\.0\.0\.1:(\d+)/.test(out)) {
  const { value, done } = await reader.read()
  if (done) break
  out += new TextDecoder().decode(value)
}
const base = out.match(/listening (http:\/\/127\.0\.0\.1:\d+)/)?.[1] ?? ""
const post = (id: string, body: unknown) => fetch(`${base}/api/questions/${id}/messages`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })
try {
  check("server: started", base !== "", out)
  const page = await fetch(base + "/")
  const html = await page.text()
  check("server: page is served", page.status === 200 && html.includes("Owner questions") && html.includes("color-scheme:dark") && !/<script[^>]+src=/.test(html))
  const list: any[] = await (await fetch(`${base}/api/questions?state=open`)).json()
  check("server: GET open lists the question", list.length === 1 && list[0].id === "demo.W-2.Q1" && list[0].state === "open")
  check("server: GET one question", (await (await fetch(`${base}/api/questions/demo.W-2.Q1`)).json() as any).dossier === "W-1")
  check("server: unknown id is 404", (await fetch(`${base}/api/questions/nope`)).status === 404)
  check("server: bad kind is 400", (await post("demo.W-2.Q1", { kind: "reply", text: "x" })).status === 400)
  check("server: unknown option is 400", (await post("demo.W-2.Q1", { kind: "answer", option: "Z" })).status === 400)
  const f = await post("demo.W-2.Q1", { kind: "followup", text: "details?" })
  check("server: follow-up appended with via page", f.status === 201 && JSON.parse(readFileSync(join(sdir, "demo.W-2.Q1.jsonl"), "utf8").trim().split("\n").pop()!).via === "page")
  const a = await post("demo.W-2.Q1", { kind: "answer", option: "A", text: "sqlite" })
  check("server: first answer accepted", a.status === 201 && ((await a.json()) as any).state === "answered")
  check("server: second answer is 409", (await post("demo.W-2.Q1", { kind: "answer", option: "B", text: "files" })).status === 409)
  check("server: answered drops out of state=open", ((await (await fetch(`${base}/api/questions?state=open`)).json()) as any[]).length === 0)
  const rv: any = await (await fetch(`${base}/api/runs`)).json()
  const r7 = rv.runs?.find((r: any) => r.id === "W-7" && r.repo === "repoB")
  check("server: /api/runs lists dossiers from the injected roots", rv.runs?.length === 9 && r7?.status === "building" && r7.phase === "4" && r7.repo === "repoB", JSON.stringify(rv).slice(0, 300))
  check("server: /api/runs session activity and context", r7?.session.state === "active" && r7.session.context === 143003 && r7.session.requests === 2 && r7.pr === null, JSON.stringify(r7?.session))
  check("server: /api/runs carries the open-question link", rv.runs.every((r: any) => Array.isArray(r.questions)) && typeof rv.scannedAt === "string")
  check("server: page has the Runs view and no external resources", html.includes("Open questions") && html.includes("/api/runs") && html.includes("/api/stubs") && !/https?:\/\//.test(html))
  const qo = html.match(/function queueOpen\(\) \{[^\n]*\}/)?.[0] ?? ""
  const order = qo ? new Function("S", qo + "; return queueOpen().map(q => q.id)")({ q: [{ id: "c", state: "open", ts: "2026-11-02T10:03:00Z" }, { id: "a", state: "open", ts: "2026-11-02T10:01:00Z" }, { id: "x", state: "answered", ts: "2026-11-02T10:00:00Z" }, { id: "b", state: "open", ts: "2026-11-02T10:02:00Z" }] }) : []
  check("home: the open questions are a queue, oldest first, and no longer a card", JSON.stringify(order) === '["a","b","c"]' && !html.includes("home:questions") && html.includes("No open questions.") && html.includes("waiting "), JSON.stringify(order))
  const r10 = rv.runs.find((r: any) => r.id === "W-10"), r9 = rv.runs.find((r: any) => r.id === "W-9")
  check("server: a ready dossier with a waiting runner is running, with its updates and summary", r10?.running === true && r10.runner?.state === "waiting" && r10.updates[0]?.text === "ten started" && r10.summary === "Ten paragraph." && r7.running === true && r9.running === false && r9.repo === "repoC", JSON.stringify(r10).slice(0, 300))
  check("server: stage carries the phase name", r7.phaseName === pn["4"])
  check("server: answered questions link to their dossier in /api/runs", rv.runs.find((r: any) => r.id === "W-1") === undefined)
  const sv: any = await (await fetch(`${base}/api/stubs`)).json()
  const c = sv.repos?.find((x: any) => x.repo === "repoC")?.stubs?.[0]
  check("server: /api/stubs lists the stub with its dossier, state and stage", c?.branch === "stub/2026-11-02" && c.state === "accepting" && c.dossiers.length === 1 && c.dossiers[0].id === "W-9" && c.dossiers[0].status === "done" && !!c.refAge, JSON.stringify(sv).slice(0, 400))
  const ow = await fetch(`${base}/open-work/repoC`)
  check("server: /open-work/<repo> serves the generated report", ow.status === 200 && (await ow.text()).length > 100 && sv.openWork?.some((o: any) => o.repo === "repoC"))
  check("server: stubs list only dossiers merged on them", c.dossiers.every((d: any) => d.via === "merged") && !c.dossiers.some((d: any) => d.id === "W-14"))
  const r11 = rv.runs.find((r: any) => r.id === "W-11"), r12 = rv.runs.find((r: any) => r.id === "W-12"), r13 = rv.runs.find((r: any) => r.id === "W-13")
  check("server: /api/runs carries the controls", r11.controls.start.ok && realpathSync(r11.main) === realpathSync(repoC), JSON.stringify(r11.controls))
  check("server: a blocker that is not done blocks Start", r12.controls.start.show && !r12.controls.start.ok && r12.controls.start.reason === "blocked by W-10" && r10.controls.stop.show && r13.runner.state === "stopped" && r13.controls.resume.ok, JSON.stringify([r12.controls, r13.runner]))
  check("server: two repos sharing W-7 both appear, each with its own status", rv.runs.filter((r: any) => r.id === "W-7").map((r: any) => r.repo + ":" + r.status).sort().join("|") === "repoA:ready|repoB:building")
  const owr = await (await fetch(`${base}/open-work/repoC`)).text()
  check("server: the repo page's report is generated with --link-base /repo/<repo>", owr.includes('"link_base": "/repo/repoC"') && owr.includes('data-embed="1"'))
  check("server: /api/stubs carries members per stub", sv.repos.find((x: any) => x.repo === "repoC")?.stubs.every((x: any) => Array.isArray(x.members)) === true)
  const rpage = await (await fetch(`${base}/repo/repoC`)).text()
  check("server: the repo page is two columns (report left, stub tree right, sticky, stacked under 1000px)", rpage.includes("grid-template-columns:minmax(0,1fr) 360px") && rpage.includes("position:sticky") && rpage.includes("max-width:1000px") && rpage.includes("function sideColumn") && rpage.includes("Merged stubs") && !rpage.includes("function liveCard") && !rpage.includes("function stubCard") && !rpage.includes("'Stubs', stubs.length"))
  const gr = await fetch(`${base}/repo/repoC/graph`, { redirect: "manual" })
  check("server: /repo/<r>/graph redirects to the repo page", gr.status === 302 && gr.headers.get("location") === "/repo/repoC")
  check("server: the dossier route chooses dossier or run view", rv.runs.find((r: any) => r.id === "W-9").view === "dossier" && rv.runs.find((r: any) => r.id === "W-11").view === "dossier" && rv.runs.find((r: any) => r.id === "W-13").view === "run" && rv.runs.find((r: any) => r.id === "W-10").view === "run" && r7.view === "run")
  check("server: /api/stubs lists the open (not merged) stubs per repo", sv.repos.find((x: any) => x.repo === "repoC")?.open?.includes("stub/2026-11-02") === true)
  const tj7: any = await (await fetch(`${base}/api/timeline/repoB/W-7`)).json()
  check("server: /api/timeline carries phase groups, the Now panel and the build log apart", tj7.groups?.[0]?.current === true && tj7.groups[0].label.startsWith("Phase 4") && tj7.now.phase.startsWith("Phase 4") && tj7.now.step === 4 && Array.isArray(tj7.log) && tj7.empty === false, JSON.stringify(tj7.now))
  const tj13: any = await (await fetch(`${base}/api/timeline/repoC/W-13`)).json()
  check("server: a run without updates has an empty timeline, not its build log", tj13.empty === true && tj13.entries.length === 0 && tj13.groups.length === 0 && tj13.log.length > 0, JSON.stringify(tj13.entries).slice(0, 200))
  for (const p of ["/questions", "/repo/repoC", "/repo/repoC/W-9", "/repo/repoC/W-9/dossier", "/repo/repoC/stub/stub/2026-11-02"]) {
    const pr = await fetch(base + p); const t = await pr.text()
    check("server: page route " + p + " is served", pr.status === 200 && t.includes("Owner questions") && t.includes("EventSource") && !/https?:\/\//.test(t))
  }
  const ev = await fetch(`${base}/api/timeline/repoB/W-7`), tj: any = await ev.json()
  check("server: /api/timeline merges updates with phase boundaries, newest first", ev.status === 200 && tj.planning === false && tj.entries.map((e: any) => e.type === "boundary" ? "B:" + e.label : e.text).join("|") === "tiny|B:Phase 4 \u2014 " + pn["4"] + "|B:Phase 3 \u2014 " + pn["3"], JSON.stringify(tj.entries))
  const pj: any = await (await fetch(`${base}/api/timeline/repoA/W-8`)).json()
  check("server: a planned dossier with updates is a planning timeline", pj.planning === true && pj.entries[0].text === "planning started")
  const dj: any = await (await fetch(`${base}/api/dossier/repoC/W-9`)).json()
  check("server: /api/dossier renders the markdown", dj.html.includes("<h2>Problem</h2>") && !dj.html.includes("id: W-9") && dj.run.id === "W-9")
  const sj: any = await (await fetch(`${base}/api/stub?repo=repoC&branch=${encodeURIComponent("stub/2026-11-02")}`)).json()
  check("server: /api/stub timeline from the record and the stub updates, newest first", sj.hasRecord === true && sj.entries[0].text === "stub step done" && sj.entries.some((e: any) => e.type === "boundary" && e.label.startsWith("Step 2")) && sj.stub.branch === "stub/2026-11-02", JSON.stringify(sj.entries).slice(0, 400))
  check("server: unknown stub is 404", (await fetch(`${base}/api/stub?repo=repoC&branch=nope`)).status === 404)
  const lg = await fetch(`${base}/api/runs/repoC/W-10/log`), lt = await lg.text()
  check("server: runner log is the last 200 lines", lg.status === 200 && lt.includes("line 300") && !lt.includes("line 100\n") && lt.split("\n").length <= 202 && (await fetch(`${base}/api/runs/repoC/W-11/log`)).status === 404)
  const ctl = (p: string, headers: Record<string, string> = {}) => fetch(`${base}/api/runs/${p}`, { method: "POST", headers })
  const s1 = await ctl("repoC/W-11/start")
  const s1j: any = await s1.json()
  check("server: Start runs the runner with the repo's main checkout and returns its line", s1.status === 200 && s1j.ok === true && s1j.output.includes("fake runner ok"), JSON.stringify(s1j))
  check("server: Start is refused when blocked, when not ready, with a foreign origin, and by GET", (await ctl("repoC/W-12/start")).status === 409 && (await ctl("repoC/W-9/start")).status === 409 && (await ctl("repoC/W-11/start", { origin: "http://evil.example" })).status === 403 && (await fetch(`${base}/api/runs/repoC/W-11/start`)).status === 405 && (await ctl("repoC/NOPE/start")).status === 404)
  const s2 = await ctl("repoC/W-13/resume"), s3 = await ctl("repoC/W-10/stop")
  check("server: Resume and Stop succeed; Resume of a running dossier does not", s2.status === 200 && s3.status === 200 && (await ctl("repoC/W-10/resume")).status === 409 && (await ctl("repoC/W-11/stop")).status === 409)
  const argv = readFileSync(argvLog, "utf8").trim().split("\n")
  check("server: the runner got the right argv for start, resume (recorded root) and stop", argv.length === 3 && argv[0] === "W-11 --root " + realpathSync(repoC) + " --command work-on" && argv[1] === "W-13 --root " + recordedRoot + " --command work-on" && argv[2] === "stop W-10 --root " + realpathSync(repoC), JSON.stringify(argv))
  {
    writeFileSync(join(runsDir, "repoC.W-9.runner.json"), JSON.stringify({ pid: 1, state: "done", stage: 9, root: recordedRoot, started: "2026-11-04T07:00:00Z" }))
    writeFileSync(join(runsDir, "repoC.W-9.jsonl"), JSON.stringify({ ts: "2026-11-04T08:00:00Z", kind: "result", text: "**All done.**", command: "work-on" }) + "\n")
    const tj: any = await (await fetch(`${base}/api/timeline/repoC/W-9`)).json()
    check("server: a finished run's timeline carries its reply, rendered, and offers the chat", tj.conv?.result?.text === "**All done.**" && tj.conv.result.html.includes("<strong>All done.</strong>") && tj.conv.canChat === true, JSON.stringify(tj.conv))
    const say = (p: string, body: unknown) => fetch(`${base}/api/runs/${p}/chat`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })
    const c1 = await say("repoC/W-9", { text: "why?" })
    check("server: chat hands the message to the runner with the run's recorded root; empty text is refused", c1.status === 200 && readFileSync(argvLog, "utf8").trim().split("\n").pop() === "chat W-9 --root " + recordedRoot + " --text why?" && (await say("repoC/W-9", { text: " " })).status === 400 && (await say("repoC/NOPE", { text: "x" })).status === 404)
    writeFileSync(join(runsDir, "repoC.W-9.summary.md"), "**In one line:** nine works.\n")
    const tj2: any = await (await fetch(`${base}/api/timeline/repoC/W-9`)).json()
    check("server: the product summary is rendered into the timeline payload", tj2.summary?.html.includes("<strong>In one line:</strong>") === true, JSON.stringify(tj2.summary))
    const sm = (p: string) => fetch(`${base}/api/runs/${p}/summary`, { method: "POST" })
    const sm1 = await sm("repoC/W-9")
    check("server: Write product summary starts the runner for a built dossier, never for one not built", sm1.status === 200 && (await sm("repoC/W-10")).status === 409)
    const fx = await fetch(`${base}/api/runs/repoC/W-9/fix-deferred`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ items: ["D-1"] }) })
    check("server: fix selected is refused for a dossier with no open deferred items", fx.status === 409)
    appendFileSync(join(runsDir, "repoC.W-9.jsonl"), JSON.stringify({ ts: "2026-11-04T08:10:00Z", kind: "update", auto: true, event: "blocked", tool: "Bash", target: "ssh vm-test rebuild fake-llm", reason: "[Shared infrastructure]", tool_use_id: "toolu_9" }) + "\n")
    const bt: any = await (await fetch(`${base}/api/timeline/repoC/W-9`)).json()
    const rule = (b: unknown) => fetch(`${base}/api/runs/repoC/W-9/blocks/toolu_9`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b) })
    const r0 = await rule({ action: "once" })
    check("server: a block shows on the dossier; Allow once needs the agent's open question", bt.blocks?.[0]?.target === "ssh vm-test rebuild fake-llm" && bt.blockQuestion === "" && r0.status === 409 && (await rule({ action: "always", rule: "Bash(*)" })).status === 400)
    writeFileSync(join(sdir, "repoC.W-9.Q7.jsonl"), JSON.stringify({ ts: "2026-11-04T08:11:00Z", from: "agent", kind: "question", text: "May I rebuild the fixture?", repo: "repoC", dossier: "W-9", root: realpathSync(repoC), options: [{ key: "A", label: "yes" }, { key: "B", label: "no" }], recommendation: "A", blocks: "x" }) + "\n")
    const r1 = await rule({ action: "always", rule: "Bash(ssh vm-test *)" })
    const cfg = JSON.parse(readFileSync(join(realpathSync(repoC), ".claude", "settings.local.json"), "utf8"))
    const qa = readFileSync(join(sdir, "repoC.W-9.Q7.jsonl"), "utf8")
    check("server: Always allow saves the narrow rule, answers the open question naming the command, and clears the block",
      r1.status === 200 && cfg.permissions?.allow?.includes("Bash(ssh vm-test *)") && qa.includes("ssh vm-test rebuild fake-llm") && qa.includes('"kind":"answer"') && ((await (await fetch(`${base}/api/timeline/repoC/W-9`)).json()) as any).blocks.length === 0, await r1.text().catch(() => ""))
  }
  {
    const sse = await fetch(`${base}/api/events`), rd = sse.body!.getReader(), dec = new TextDecoder()
    const readUntil = async (re: RegExp, ms: number): Promise<string> => {
      let got = ""
      const end = Date.now() + ms
      while (Date.now() < end && !re.test(got)) {
        const r = await Promise.race([rd.read(), new Promise<any>((res) => setTimeout(() => res({ timeout: true }), end - Date.now()))])
        if (r.timeout || r.done) break
        got += dec.decode(r.value)
      }
      return got
    }
    const first = await readUntil(/connected/, 10000)
    writeFileSync(join(sdir, "demo.W-3.Q1.jsonl"), JSON.stringify({ ...qLine, dossier: "W-3" }) + "\n")
    // A background scan may announce itself first; wait for the event that names the questions store.
    const second = await readUntil(/event: changed\ndata: [^\n]*questions[^\n]*\n\n/, 15000)
    check("server: /api/events streams and emits changed on a store change", sse.headers.get("content-type")?.includes("text/event-stream") === true && first.includes("connected") && second.includes("event: changed") && second.includes("questions"), first + second)
    writeFileSync(join(runsDir, "repoC.W-10.jsonl"), JSON.stringify({ ts: "2026-11-02T12:00:00Z", kind: "update", text: "more" }) + "\n", { flag: "a" })
    check("server: a run update emits runs", (await readUntil(/runs/, 5000)).includes("runs"))
    await rd.cancel()
  }
  check("server: unsafe or unknown open-work repo is 404", (await fetch(`${base}/open-work/nope`)).status === 404)
  const dp = await fetch(`${base}/dossier/repoC/W-9`)
  const dh = await dp.text()
  check("server: /dossier/<repo>/<id> renders read-only HTML", dp.status === 200 && dh.includes("Nine is broken.") && dh.includes("<h2>Problem</h2>") && (await fetch(`${base}/dossier/repoC/NOPE`)).status === 404 && (await fetch(`${base}/dossier/repoC/W-9`, { headers: { host: "evil.example" } })).status === 403)
  const bad = await fetch(`${base}/api/questions`, { headers: { host: "evil.example" } })
  check("server: foreign Host header refused", bad.status === 403)
} finally {
  proc.kill()
  rmSync(dir, { recursive: true, force: true })
  rmSync(sdir, { recursive: true, force: true })
  rmSync(rdir, { recursive: true, force: true })
  rmSync(odir, { recursive: true, force: true })
  rmSync(runsDir, { recursive: true, force: true })
  rmSync(udir, { recursive: true, force: true })
}
console.log(failed ? `FAIL (${failed})` : "PASS (all cases)")
process.exit(failed ? 1 : 0)
