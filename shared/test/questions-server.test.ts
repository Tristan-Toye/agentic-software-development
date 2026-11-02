// Owner-questions server: store, API, page and the Slack envelope handlers (no network, no Slack).
// Run: bun --no-install shared/test/questions-server.test.ts
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, appendFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { appendOwner, cut, parseQuestion, readAll, writeSidecar, idForThread, AnsweredError } from "../questions/store.ts"
import { interpretEnvelope, questionBlocks } from "../questions/slack.ts"
import { remindersDue } from "../questions/server.ts"
import { parseFrontMatter, lastPhase, lastLine, buildLog, pickDossiers, tailTranscript, transcriptCwd, loadPhaseNames, phaseNameOf, isRunning, answeredBy } from "../questions/runs.ts"
import { prNumberOf, prFieldNumber, dossiersOnMerges, stubStage, stubState } from "../questions/stubs.ts"
import { readUpdates, readRunner, summaryOf, renderMarkdown } from "../questions/runstore.ts"
import { execFileSync } from "node:child_process"

let failed = 0
function check(name: string, ok: boolean, detail = ""): void {
  if (!ok) failed++
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : ` ${detail}`}`)
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
check("slack: answer: prefix is a free-text answer", r.action.type === "owner" && r.action.kind === "answer" && r.action.text === "go with A" && !r.action.option)
r = interpretEnvelope(ev({ text: "hello" }), lookup)
check("slack: DM outside a thread gets a pointer", r.action.type === "pointer")
r = interpretEnvelope(ev({ text: "x", thread_ts: "999.9" }), lookup)
check("slack: reply in an unknown thread gets a pointer", r.action.type === "pointer")
check("slack: bot messages are ignored", interpretEnvelope(ev({ text: "x", bot_id: "B1", thread_ts: "111.222" }), lookup).action.type === "none")
check("slack: subtyped messages are ignored", interpretEnvelope(ev({ text: "x", subtype: "message_changed", thread_ts: "111.222" }), lookup).action.type === "none")
check("slack: other users are ignored when an owner is set", interpretEnvelope(ev({ text: "x", user: "U2", thread_ts: "111.222" }), lookup, "U1").action.type === "none")
r = interpretEnvelope({ envelope_id: "E2", type: "interactive", payload: { type: "block_actions", user: { id: "U1" }, channel: { id: "D1" }, actions: [{ value: "hand.written.Q9|A" }] } }, lookup)
check("slack: button press is an answer with that option", r.ack?.envelope_id === "E2" && r.action.type === "owner" && r.action.kind === "answer" && r.action.option === "A" && r.action.id === "hand.written.Q9")
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
check("runs: last phase match wins, else empty", lastPhase("Phase 1 PHASE 4a Phase 4") === "4" && lastPhase("nothing") === "")
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
const odir = mkdtempSync(join(tmpdir(), "asd-oq-ow-")), runsDir = mkdtempSync(join(tmpdir(), "asd-oq-rd-"))
writeFileSync(join(runsDir, "repoC.W-10.runner.json"), JSON.stringify({ pid: process.pid, state: "waiting", stage: "3", started: "s" }))
writeFileSync(join(runsDir, "repoC.W-10.jsonl"), JSON.stringify({ ts: "2026-11-02T10:00:00Z", kind: "update", text: "ten started" }) + "\n")

// server over HTTP
const sdir = mkdtempSync(join(tmpdir(), "asd-oq-srv-"))
writeFileSync(join(sdir, "demo.W-2.Q1.jsonl"), JSON.stringify(qLine) + "\n")
const proc = Bun.spawn(["/opt/homebrew/bin/bun", "--no-install", join(import.meta.dir, "..", "questions", "server.ts")], {
  env: { ...process.env, ASD_QUESTIONS_DIR: sdir, ASD_QUESTIONS_PORT: "0", ASD_QUESTIONS_NO_NOTIFY: "1", ASD_QUESTIONS_NO_SLACK: "1", ASD_SLACK_USER: "", ASD_RUNS_ROOTS: `${repoA}:${repoB}:${repoC}`, ASD_OPEN_WORK_DIR: odir, ASD_RUNS_DIR: runsDir, ASD_CLAUDE_PROJECTS: projects, ASD_RUNS_NO_GH: "1" },
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
  check("server: page is served", page.status === 200 && html.includes("Owner questions") && html.includes("prefers-color-scheme") && !/<script[^>]+src=/.test(html))
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
  const r7 = rv.runs?.find((r: any) => r.id === "W-7")
  check("server: /api/runs lists dossiers from the injected roots", rv.runs?.length === 4 && r7?.status === "building" && r7.phase === "4" && r7.repo === "repoB", JSON.stringify(rv).slice(0, 300))
  check("server: /api/runs session activity and context", r7?.session.state === "active" && r7.session.context === 143003 && r7.session.requests === 2 && r7.pr === null, JSON.stringify(r7?.session))
  check("server: /api/runs carries the open-question link", rv.runs.every((r: any) => Array.isArray(r.questions)) && typeof rv.scannedAt === "string")
  check("server: page has the Runs view and no external resources", html.includes("Open questions") && html.includes("/api/runs") && html.includes("/api/stubs") && !/https?:\/\//.test(html))
  const r10 = rv.runs.find((r: any) => r.id === "W-10"), r9 = rv.runs.find((r: any) => r.id === "W-9")
  check("server: a ready dossier with a waiting runner is running, with its updates and summary", r10?.running === true && r10.runner?.state === "waiting" && r10.updates[0]?.text === "ten started" && r10.summary === "Ten paragraph." && r7.running === true && r9.running === false && r9.repo === "repoC", JSON.stringify(r10).slice(0, 300))
  check("server: stage carries the phase name", r7.phaseName === pn["4"])
  check("server: answered questions link to their dossier in /api/runs", rv.runs.find((r: any) => r.id === "W-1") === undefined)
  const sv: any = await (await fetch(`${base}/api/stubs`)).json()
  const c = sv.repos?.find((x: any) => x.repo === "repoC")?.stubs?.[0]
  check("server: /api/stubs lists the stub with its dossier, state and stage", c?.branch === "stub/2026-11-02" && c.state === "accepting" && c.dossiers.length === 1 && c.dossiers[0].id === "W-9" && c.dossiers[0].status === "done" && !!c.refAge, JSON.stringify(sv).slice(0, 400))
  const ow = await fetch(`${base}/open-work/repoC`)
  check("server: /open-work/<repo> serves the generated report", ow.status === 200 && (await ow.text()).length > 100 && sv.openWork?.some((o: any) => o.repo === "repoC"))
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
