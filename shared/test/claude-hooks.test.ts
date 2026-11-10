// Run: bun --no-install shared/test/claude-hooks.test.ts
// Each Claude Code hook is run as Claude Code runs it: a process fed hook JSON on stdin,
// with a fixture transcript on disk. Checks stdout and the exit code.
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const root = join(import.meta.dir, "..", "..")
const tmp = mkdtempSync(join(tmpdir(), "hooks-"))
// HOME is the temp dir too: the pressure nudge reads ~/.claude/settings.json, which must not be the real one.
const env = { ...process.env, XDG_DATA_HOME: tmp, HOME: tmp, PHASE_CHECKPOINT_NUDGE_CALLS: "3" }
delete (env as any).CLAUDE_CONTEXT_GUARD_CONFIG
// A CLAUDE_PLUGIN_ROOT inherited from the shell (another plugin's, when run from a Claude Code session) wins over the
// repo root in pipelineRoot() and broke "reason uses the plugin root".
delete (env as any).CLAUDE_PLUGIN_ROOT
const fails: string[] = []
const check = (name: string, ok: boolean) => ok || fails.push(name)

function hook(file: string, stdin: string) {
  const p = Bun.spawnSync(["bun", "--no-install", join(root, "shared", "claude", file)], {
    stdin: new TextEncoder().encode(stdin),
    env,
    stdout: "pipe",
    stderr: "pipe",
  })
  return { out: p.stdout.toString().trim(), code: p.exitCode }
}
const json = (s: string) => JSON.parse(s)

// ---- fixture transcripts ----------------------------------------------------------
const NAV = "mcp__plugin_agentic-software-development_nav__"
const cmd = (name: string) => ({
  type: "user",
  message: { role: "user", content: `<command-message>${name}</command-message>\n<command-name>/agentic-software-development:${name}</command-name>\n<command-args>W-014</command-args>` },
})
const call = (id: string, phase: string, summary: string, name = NAV + "phase_checkpoint") => ({
  type: "assistant",
  message: { role: "assistant", content: [{ type: "tool_use", id, name, input: { phase, summary } }] },
})
const result = (id: string, isError = false) => ({
  type: "user",
  message: { role: "user", content: [{ type: "tool_result", tool_use_id: id, content: "ok", ...(isError ? { is_error: true } : {}) }] },
})
const write = (name: string, records: object[]) => {
  const path = join(tmp, name)
  writeFileSync(path, records.map((r) => JSON.stringify(r)).join("\n") + "\n")
  return path
}
const workOn = write("work-on.jsonl", [{ type: "mode" }, cmd("work-on")])
const plan = write("plan.jsonl", [cmd("deferred")]) // a non-orchestrating command (/plan now orchestrates too)
const dossier = "/repo/.discovery/dossiers/W-014.md"
const big = join(tmp, "big.rs")
writeFileSync(big, "fn a() {}\n".repeat(3000))

const pre = (over: object) =>
  JSON.stringify({ hook_event_name: "PreToolUse", session_id: "s-orch", transcript_path: workOn, cwd: tmp, tool_name: "Read", tool_input: { file_path: dossier }, ...over })

// ---- pre-tool-use -----------------------------------------------------------------
let r = hook("pre-tool-use.ts", pre({}))
let o = r.out ? json(r.out) : undefined
check("pre: dossier read denied", r.code === 0 && o?.hookSpecificOutput?.hookEventName === "PreToolUse" && o.hookSpecificOutput.permissionDecision === "deny")
check("pre: reason names the tool to use", o?.hookSpecificOutput?.permissionDecisionReason?.includes("dossier_edit.py section") === true)
check("pre: reason uses the plugin root", o?.hookSpecificOutput?.permissionDecisionReason?.includes(root) === true)
check("pre: dossier with limit allowed", hook("pre-tool-use.ts", pre({ tool_input: { file_path: dossier, offset: 1, limit: 40 } })).out === "")
check("pre: big file denied", hook("pre-tool-use.ts", pre({ tool_input: { file_path: big } })).out.includes("code_outline"))
check("pre: bash cat of big file denied", hook("pre-tool-use.ts", pre({ tool_name: "Bash", tool_input: { command: `cat ${big}` } })).out.includes('"deny"'))
check("pre: bash cargo via limactl denied", hook("pre-tool-use.ts", pre({ tool_name: "Bash", tool_input: { command: "limactl shell vm -- cargo test" } })).out.includes("run_tests.py"))
check("pre: ordinary bash allowed", hook("pre-tool-use.ts", pre({ tool_name: "Bash", tool_input: { command: "ls" } })).out === "")
check("pre: other tool ignored", hook("pre-tool-use.ts", pre({ tool_name: "Grep", tool_input: { pattern: "x" } })).out === "")
// Not the orchestrator: a subagent, another command's session, a session without a transcript.
check("pre: subagent is a no-op", hook("pre-tool-use.ts", pre({ agent_id: "a1", agent_type: "implementer" })).out === "")
// The unit test author's read boundary, by agent_type or by the subagent's record on disk.
const UT = "agentic-software-development:unit-test-author"
const blind = (path: string, over: object = {}) =>
  hook("pre-tool-use.ts", pre({ agent_id: "u1", agent_type: UT, tool_input: { file_path: path }, ...over })).out
check("pre: blind author may read its staged payload", blind("/wt/.agent-staging/payloads/UT.md") === "")
check("pre: blind author may read a test family", blind("/wt/crates/platform/tests/common/mod.rs") === "")
check("pre: blind author may not read a source body", blind("/wt/crates/platform/src/nats_auth.rs").includes('"deny"'))
check("pre: blind author may not read a dossier", blind("/wt/.discovery/dossiers/W-014.md").includes("GAP:"))
check("pre: .. cannot climb out of a test family", blind("/wt/tests/../src/lib.rs").includes('"deny"'))
check("pre: blind author's Bash is not this hook's business", blind("", { tool_name: "Bash", tool_input: { command: "cat src/lib.rs" } }) === "")
check("pre: another subagent reads freely", hook("pre-tool-use.ts", pre({ agent_id: "a1", agent_type: "agentic-software-development:implementer", tool_input: { file_path: "/wt/src/lib.rs" } })).out === "")
{
  // a workflow agent: no agent_type in the input, its record under <session>/subagents/workflows/<run>/
  const runDir = join(tmp, "s-orch", "subagents", "workflows", "wf_1")
  mkdirSync(runDir, { recursive: true })
  writeFileSync(join(runDir, "agent-w1.meta.json"), JSON.stringify({ agentType: UT, description: "UT" }))
  check("pre: a workflow agent's type is read from its record", blind("/wt/src/lib.rs", { agent_id: "w1", agent_type: undefined }).includes('"deny"'))
  check("pre: the record is found beside the agent's own transcript too", blind("/wt/src/lib.rs", { agent_id: "w1", agent_type: undefined, transcript_path: join(runDir, "agent-w1.jsonl") }).includes('"deny"'))
  check("pre: an agent with no record reads freely", blind("/wt/src/lib.rs", { agent_id: "w9", agent_type: undefined }) === "")
}
check("pre: other command's session is a no-op", hook("pre-tool-use.ts", pre({ session_id: "s-plan", transcript_path: plan })).out === "")
{
  // A runner stage starts with a bare "/plugin:plan W-1" line, no command tag; /plan sessions are guarded too.
  const bare = write("bare.jsonl", [{ type: "user", message: { role: "user", content: "/agentic-software-development:plan W-128 D-1" } }])
  const big = join(tmp, "big.md"); writeFileSync(big, "# t\n" + "x".repeat(30000) + "\n")
  const r = hook("pre-tool-use.ts", JSON.stringify({ session_id: "s-bare", transcript_path: bare, cwd: tmp, hook_event_name: "PreToolUse", tool_name: "Read", tool_input: { file_path: big } }))
  check("pre: a runner-style /plan session is guarded (a large whole read is refused)", r.out.includes("deny"))
}
check("pre: missing transcript is a no-op", hook("pre-tool-use.ts", pre({ session_id: "s-none", transcript_path: join(tmp, "nope.jsonl") })).out === "")
// The answer is cached per session: the state file outlives the transcript.
const state = JSON.parse(readFileSync(join(tmp, "agentic-software-development", "claude", "s-orch.json"), "utf8"))
check("pre: orchestrator answer cached", state.orchestrator === true)
check("pre: cached answer used without the transcript", hook("pre-tool-use.ts", pre({ transcript_path: join(tmp, "gone.jsonl") })).out.includes('"deny"'))
// The stateful reads: windows and phase files (ASD_READ_GUARD_BIG).
{
  const src = join(tmp, "win.rs"); writeFileSync(src, "fn a() {}\n".repeat(500))
  const phaseDir = join(tmp, "plugin", "references", "work-on"); mkdirSync(phaseDir, { recursive: true })
  const ph = join(phaseDir, "phase-4.md"); writeFileSync(ph, "# Phase 4\nbody\n")
  const rd = (session: string, input: object, extra: object = {}) =>
    hook("pre-tool-use.ts", pre({ session_id: session, tool_input: input, ...extra })).out
  check("win: a window over 200 lines of source is denied", rd("s-w1", { file_path: src, offset: 1, limit: 300 }).includes("code_item"))
  check("win: the identical call goes through", rd("s-w1", { file_path: src, offset: 1, limit: 300 }) === "")
  check("win: a 200-line window is allowed", rd("s-w2", { file_path: src, offset: 1, limit: 200 }) === "")
  for (const off of [10, 20]) check(`win: window ${off} of the same file allowed`, rd("s-w2", { file_path: src, offset: off, limit: 50 }) === "")
  const fourth = rd("s-w2", { file_path: src, offset: 30, limit: 50 })
  check("win: the 4th window of one file is denied (3 allowed)", fourth.includes('"deny"') && fourth.includes("code_outline"))
  check("win: another file's windows are counted apart", rd("s-w2", { file_path: join(tmp, "big.rs"), offset: 1, limit: 50 }) === "")
  check("win: a non-source file has no window limit", rd("s-w3", { file_path: join(tmp, "x.txt"), offset: 1, limit: 900 }) === "")
  check("win: a subagent's windows are not guarded", rd("s-w4", { file_path: src, offset: 1, limit: 900 }, { agent_id: "a1", agent_type: "agentic-software-development:implementer" }) === "")
  check("phase: first whole read allowed", rd("s-p1", { file_path: ph }) === "")
  const again = rd("s-p1", { file_path: ph })
  check("phase: second whole read denied with ref_section", again.includes('"deny"') && again.includes("ref_section") && again.includes("phase-4"))
  check("phase: the repeated call goes through", rd("s-p1", { file_path: ph }) === "")
  check("phase: a ranged read is not refused", rd("s-p1", { file_path: ph, offset: 1, limit: 5 }) === "")
  const sessState = join(tmp, "agentic-software-development", "claude", "s-p1.json")
  const compact = hook("session-start.ts", JSON.stringify({ hook_event_name: "SessionStart", source: "compact", session_id: "s-p1", transcript_path: workOn, cwd: tmp }))
  check("phase: a compaction resets the memory", compact.code === 0 && !("phaseReads" in JSON.parse(readFileSync(sessState, "utf8"))))
  check("phase: read again after the compaction is allowed", rd("s-p1", { file_path: ph }) === "")
  const off = { ...env, ASD_READ_GUARD_BIG: "0" }
  const offHook = (input: object) =>
    Bun.spawnSync(["bun", "--no-install", join(root, "shared", "claude", "pre-tool-use.ts")], { stdin: new TextEncoder().encode(pre({ session_id: "s-off", tool_input: input })), env: off, stdout: "pipe" }).stdout.toString().trim()
  check("env: ASD_READ_GUARD_BIG=0 allows a wide window", offHook({ file_path: src, offset: 1, limit: 400 }) === "")
  check("env: ASD_READ_GUARD_BIG=0 allows a phase file twice", offHook({ file_path: ph }) === "" && offHook({ file_path: ph }) === "")
}
// Broken input: exit 0, no output.
for (const bad of ["", "not json", "[]", "null", '{"tool_name":"Read"}', '{"session_id":"x","tool_name":"Read","tool_input":null,"transcript_path":5}']) {
  r = hook("pre-tool-use.ts", bad)
  check(`pre: broken input ${JSON.stringify(bad).slice(0, 20)} fails open`, r.code === 0 && r.out === "")
}

// ---- post-tool-use ----------------------------------------------------------------
const post = (over: object) =>
  JSON.stringify({ hook_event_name: "PostToolUse", session_id: "s-post", transcript_path: workOn, cwd: tmp, tool_name: "Bash", tool_input: { command: "ls" }, tool_response: {}, ...over })
const outs = [1, 2, 3, 4].map(() => hook("post-tool-use.ts", post({})).out)
check("post: calls 1-2 silent", outs[0] === "" && outs[1] === "")
o = outs[2] ? json(outs[2]) : undefined
check("post: nudge at N", o?.hookSpecificOutput?.hookEventName === "PostToolUse" && o.hookSpecificOutput.additionalContext.includes("3 tool calls since the last checkpoint"))
check("post: call 4 silent", outs[3] === "")
check("post: checkpoint resets silently", hook("post-tool-use.ts", post({ tool_name: NAV + "phase_checkpoint", tool_input: { phase: "Phase 4 — wave 1", summary: "s" } })).out === "")
const after = [1, 2, 3].map(() => hook("post-tool-use.ts", post({})).out)
check("post: count restarted after the checkpoint", after[0] === "" && after[1] === "" && after[2].includes("3 tool calls"))
const sub = [1, 2, 3].map(() => hook("post-tool-use.ts", post({ agent_id: "a1" })).out)
check("post: subagent never nudged", sub.every((s) => s === ""))
const other = [1, 2, 3].map(() => hook("post-tool-use.ts", post({ session_id: "s-plan2", transcript_path: plan })).out)
check("post: non-orchestrator never nudged", other.every((s) => s === ""))
for (const bad of ["", "{", "[]", '{"session_id":"q"}']) {
  r = hook("post-tool-use.ts", bad)
  check(`post: broken input ${JSON.stringify(bad)} fails open`, r.code === 0 && r.out === "")
}

// Under the runner: a checkpoint call is a `stage` line in $ASD_RUNS_DIR/<ASD_RUN_KEY>.jsonl.
{
  const runs = join(tmp, "runs")
  const runEnv = { ...env, ASD_RUN_KEY: "repo.W-014", ASD_RUNS_DIR: runs }
  const runHookPost = (stdin: string, e: object) =>
    Bun.spawnSync(["bun", "--no-install", join(root, "shared", "claude", "post-tool-use.ts")], { stdin: new TextEncoder().encode(stdin), env: e as any, stdout: "pipe", stderr: "pipe" })
  const cp = post({ session_id: "s-run", transcript_path: plan, tool_name: NAV + "phase_checkpoint", tool_input: { phase: "Phase 4 - wave 1", summary: "wave 1 merged\nNEXT: Phase 5" } })
  const p1 = runHookPost(cp, runEnv)
  const file = join(runs, "repo.W-014.jsonl")
  const rows = (() => { try { return readFileSync(file, "utf8").trim().split("\n").map((l) => JSON.parse(l)) } catch { return [] } })()
  check("run: checkpoint exits 0 and writes one stage line", p1.exitCode === 0 && rows.length === 1)
  check("run: stage line fields", rows[0]?.kind === "stage" && rows[0].stage === "Phase 4 - wave 1" && rows[0].text === "wave 1 merged" && /^\d{4}-\d\d-\d\dT[\d:]+Z$/.test(rows[0].ts))
  runHookPost(post({ session_id: "s-run", transcript_path: plan }), runEnv)
  check("run: other tools write nothing", readFileSync(file, "utf8").trim().split("\n").length === 1)
  runHookPost(cp, { ...env, ASD_RUNS_DIR: runs })
  check("run: no ASD_RUN_KEY writes nothing", readFileSync(file, "utf8").trim().split("\n").length === 1)
  const bad = runHookPost(cp, { ...runEnv, ASD_RUNS_DIR: join(file, "nope") })
  check("run: a write error never fails the hook", bad.exitCode === 0 && bad.stdout.toString().trim() === "")
}

// Automatic progress updates: telling tool calls become `update` lines, no model tokens.
{
  const { keyFromCommand, updateFor } = await import("../claude/progress.ts")
  const repo = join(tmp, "myrepo")
  mkdirSync(join(repo, ".discovery", "dossiers"), { recursive: true })
  Bun.spawnSync(["git", "init", "-q", repo])
  check("progress: key from a dossier path", keyFromCommand(`python3 x/dossier_edit.py log ${repo}/.discovery/dossiers/W-031-thing.md 'a'`, tmp) === "myrepo.W-031")
  check("progress: key from ask_owner --dossier/--root", keyFromCommand(`python3 ask_owner.py note --root ${repo} --dossier W-032 --text hi`, tmp) === "myrepo.W-032")
  const { keyFromStubRecord } = await import("../claude/progress.ts")
  check("progress: stub key from a stub/<date> record", keyFromStubRecord(`${repo}/.discovery/analysis/stub-2026-10-31.md`, tmp) === "myrepo.stub-2026-10-31")
  check("progress: stub key from a named-branch record", keyFromStubRecord(`cat ${repo}/.discovery/analysis/stub-2026-11-01-release-W-115-116.md`, tmp) === "myrepo.release-W-115-116")
  check("progress: no dossier, no key", keyFromCommand("ls -la", tmp) === undefined)
  check("progress: build-log line", updateFor("Bash", { command: "python3 dossier_edit.py log d.md 'GATES round 1: 93/93'" }, {}) === "log: GATES round 1: 93/93")
  check("progress: suite summary", updateFor("Bash", { command: "python3 run_tests.py --log x -- cargo test" }, { stdout: "...\npassed 120  failed 2  ignored 0  (cargo)\n" }) === "suite: passed 120  failed 2  ignored 0  (cargo)")
  check("progress: a detached suite is only started", updateFor("Bash", { command: "python3 run_tests.py --detach --vm v --workdir w -- cargo test" }, { stdout: "pid 1\nlog /x\n" }) === "suite started")
  check("progress: a background suite is only started", updateFor("Bash", { command: "python3 run_tests.py -- cargo test" }, { stdout: "Command running in background with ID: b1" }) === "suite started")
  for (const c of ["ps aux | grep -E 'run_tests.py|limactl' | head -5", "cat /tmp/x; ls /tmp/run_tests.py.log", "grep -n run_tests.py scripts/a.sh", "pgrep -f run_tests.py"])
    check(`progress: only naming the script is no suite: ${c}`, updateFor("Bash", { command: c }, { stdout: "x" }) === undefined)
  for (const c of ["python3 run_tests.py -- cargo test", "cd /r && python3 /p/scripts/run_tests.py --vm v -- x", 'python3 "$P/scripts/run_tests.py" -- x', "ASD=1 timeout 60 python3 -u run_tests.py -- x", "scripts/run_tests.py -- x"])
    check(`progress: starting the script is a suite: ${c}`, updateFor("Bash", { command: c }, { stdout: "x" }) === "suite run finished")
  check("progress: a status check beside a real run still counts the run", updateFor("Bash", { command: "ps aux | grep run_gates.py; python3 run_tests.py -- x" }, { stdout: "passed 1  failed 0  ignored 0" }) === "suite: passed 1  failed 0  ignored 0")
  check("progress: run_tests --help is no suite", updateFor("Bash", { command: "python3 run_tests.py --help" }, { stdout: "usage: run_tests.py" }) === undefined)
  {
    const { stepFor } = await import("../claude/progress")
    const t = (p: string) => (p.endsWith("P6.md") ? "Turn waits for the summary" : "")
    const wave = stepFor("Workflow", { name: "agentic-software-development:phase4-wave", args: JSON.stringify([{ agentType: "x:implementer", label: "FIX-P6", payloadPath: "/a/P6.md" }, { agentType: "x:unit-test-author", label: "UT", owned: ["a.rs", "b.rs"] }]) }, t) ?? ""
    check("step: a wave lists its agents as a table", wave.startsWith("Spawning 2 agents (phase4-wave):") && wave.includes("| implementer | FIX-P6 | Turn waits for the summary |") && wave.includes("| unit-test-author | UT | a.rs, b.rs |"), wave)
    check("step: a status check is not a test run", !String(stepFor("Bash", { command: "ps aux | grep -E 'run_tests.py|x'", description: "Check whether the full stack run is still going" }) ?? "").startsWith("Running tests"))
    check("step: tests, waits, gates, CI", stepFor("Bash", { command: "python3 run_tests.py --vm v -- cargo test -p x", description: "Run the D-1 proving test" }) === "Running tests: Run the D-1 proving test"
      && stepFor("Bash", { command: "python3 run_tests.py --wait /x.log" }) === "Waiting for the test run to finish" && stepFor("Bash", { command: "python3 run_gates.py --list g" }) === "Running the gates"
      && stepFor("Bash", { command: "python3 wait_ci.py 42" }) === "Waiting for CI on the pull request" && stepFor("Bash", { command: "ls" }) === undefined)
  }
  check("progress: PR opened", updateFor("Bash", { command: "gh pr create --base x" }, { stdout: "https://github.com/o/r/pull/12\n" }) === "PR opened: https://github.com/o/r/pull/12")
  check("progress: workflow started", updateFor("Workflow", { name: "agentic-software-development:phase4-wave", args: [{}, {}] }, {}) === "workflow started: agentic-software-development:phase4-wave — 2 agent(s)")
  const { spawnFor, endStatus } = await import("../claude/progress.ts")
  const bg = spawnFor({ subagent_type: "agentic-software-development:reviewer", description: "plan review", prompt: "SECRET PROMPT", run_in_background: true }, "Async agent launched successfully.\nagentId: a1b2c3d4e5 (internal ID)")
  check("progress: a background spawn is one line, running, with its agent id, never the prompt", bg?.text === "reviewer: plan review" && bg.status === "running" && bg.agent_id === "a1b2c3d4e5" && !JSON.stringify(bg).includes("SECRET"))
  check("progress: a foreground spawn is done", spawnFor({ subagent_type: "x:scribe", description: "log line" }, "report text")?.status === "done")
  check("progress: end status", endStatus("all good") === "done" && endStatus("GAP: no import path") === "gap" && endStatus("x\nCONTRACT-CHANGE: sig") === "contract-change")
  check("progress: GAP: none is a clean end", endStatus("Done.\nGAP: none\nCONTRACT-CHANGE: none.") === "done" && endStatus("- GAP: missing x") === "gap")
  check("progress: other commands stay quiet", updateFor("Bash", { command: "git status" }, { stdout: "x" }) === undefined && updateFor("Read", {}, {}) === undefined)
  // End to end, interactive: a /work-on session learns its key, then posts its suite run.
  const runs2 = join(tmp, "runs2")
  const e2 = { ...env, ASD_RUNS_DIR: runs2 }
  const run2 = (over: object) => Bun.spawnSync(["bun", "--no-install", join(root, "shared", "claude", "post-tool-use.ts")], { stdin: new TextEncoder().encode(post({ session_id: "s-prog", ...over })), env: e2 as any, stdout: "pipe", stderr: "pipe" })
  run2({ tool_input: { command: `python3 dossier_edit.py log ${repo}/.discovery/dossiers/W-033-x.md 'Phase 4 wave 1 out'` } })
  run2({ tool_input: { command: "python3 run_tests.py -- cargo test" }, tool_response: { stdout: "passed 3  failed 0  ignored 0  (cargo)" } })
  const rows2 = (() => { try { return readFileSync(join(runs2, "myrepo.W-033.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l)) } catch { return [] } })()
  check("progress: interactive run learns its key and posts", rows2.length === 2 && rows2.every((r) => r.kind === "update" && r.auto === true) && rows2[1].text.startsWith("suite: passed 3"))
  const quiet = Bun.spawnSync(["bun", "--no-install", join(root, "shared", "claude", "post-tool-use.ts")], { stdin: new TextEncoder().encode(post({ session_id: "s-prog2", transcript_path: plan, tool_input: { command: `python3 dossier_edit.py log ${repo}/.discovery/dossiers/W-034-x.md 'x'` } })), env: { ...e2, ASD_RUN_UPDATES: "0" } as any, stdout: "pipe", stderr: "pipe" })
  check("progress: ASD_RUN_UPDATES=0 turns it off", quiet.exitCode === 0 && !(() => { try { readFileSync(join(runs2, "myrepo.W-034.jsonl")); return true } catch { return false } })())
}

// SubagentStop: closes a spawn line by agent id in the session's run.
{
  const runs3 = join(tmp, "runs3")
  const stop = (over: object, e: object) => Bun.spawnSync(["bun", "--no-install", join(root, "shared", "claude", "subagent-stop.ts")], { stdin: new TextEncoder().encode(JSON.stringify({ hook_event_name: "SubagentStop", session_id: "s-stop", agent_id: "a1b2c3d4e5", agent_type: "agentic-software-development:implementer", last_assistant_message: "GAP: x", ...over })), env: e as any, stdout: "pipe", stderr: "pipe" })
  const p = stop({}, { ...env, ASD_RUNS_DIR: runs3, ASD_RUN_KEY: "repo.W-040" })
  const rows3 = (() => { try { return readFileSync(join(runs3, "repo.W-040.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l)) } catch { return [] } })()
  check("subagent-stop: one spawn-done line with status, exit 0, silent", p.exitCode === 0 && p.stdout.toString().trim() === "" && rows3.length === 1 && rows3[0].event === "spawn-done" && rows3[0].agent_id === "a1b2c3d4e5" && rows3[0].status === "gap" && rows3[0].text === "implementer finished")
  const q = stop({}, { ...env, ASD_RUNS_DIR: runs3 })
  check("subagent-stop: no run key, nothing written", q.exitCode === 0 && readFileSync(join(runs3, "repo.W-040.jsonl"), "utf8").trim().split("\n").length === 1)
}

// Context pressure: past 75% of the session's autoCompactWindow, one checkpoint request per cycle.
{
  const proj = join(tmp, "proj")
  mkdirSync(join(proj, ".claude"), { recursive: true })
  writeFileSync(join(proj, ".claude", "settings.json"), JSON.stringify({ autoCompactWindow: 200000 }))
  const usage = (k: number) => ({ type: "assistant", message: { role: "assistant", usage: { input_tokens: 10, cache_read_input_tokens: k * 1000, cache_creation_input_tokens: 0 }, content: [] } })
  const tx = (name: string, k: number) => write(name, [cmd("work-on"), usage(k)])
  const p = (k: number, cwd = proj) => hook("post-tool-use.ts", post({ session_id: "s-press", transcript_path: tx(`press-${k}.jsonl`, k), cwd })).out
  check("pressure: under 75% of the window is silent", p(140) === "")
  const first = p(160)
  check("pressure: past 75% asks for a checkpoint with a NEXT line", first.includes("before compaction") && first.includes("NEXT:") && first.includes("~160k of the 200k window"))
  check("pressure: asked once per cycle", !p(165).includes("before compaction"))
  check("pressure: the context falling back (a compaction) re-arms it", p(40) === "" && p(158).includes("before compaction"))
  check("pressure: no autoCompactWindow, no request", hook("post-tool-use.ts", post({ session_id: "s-press2", transcript_path: tx("press-x.jsonl", 900), cwd: tmp })).out === "")
  // a sidechain record is not the main thread's context
  const side = write("press-side.jsonl", [cmd("work-on"), usage(10), { ...usage(190), isSidechain: true }])
  check("pressure: a subagent's record is ignored", hook("post-tool-use.ts", post({ session_id: "s-press3", transcript_path: side, cwd: proj })).out === "")
}

// ---- session-start ----------------------------------------------------------------
const compacted = write("compacted.jsonl", [
  cmd("work-on"),
  call("t1", "Phase 1", "dossier at /w/W-014.md, branch feat/x"),
  result("t1"),
  call("t2", "stubs done", "bad phase name"),
  result("t2", true),
  call("t3", "Phase 4 — wave 1", "wave 1 verified, SHA abc123\nNEXT: launch wave 2 from /w/.agent-staging/payloads", "phase_checkpoint"),
  result("t3"),
  { type: "assistant", message: { role: "assistant", content: [{ type: "text", text: "mentions phase_checkpoint in prose" }] } },
])
const start = (over: object) =>
  JSON.stringify({ hook_event_name: "SessionStart", session_id: "s-orch", source: "compact", transcript_path: compacted, cwd: tmp, ...over })
r = hook("session-start.ts", start({}))
o = r.out ? json(r.out) : undefined
const ctx: string = o?.hookSpecificOutput?.additionalContext ?? ""
check("start: emits SessionStart additionalContext", r.code === 0 && o?.hookSpecificOutput?.hookEventName === "SessionStart")
check("start: the restore ends the checkpoints on the latest NEXT line", ctx.includes('## Resume here\n\nNEXT (from "Phase 4 — wave 1"): launch wave 2 from /w/.agent-staging/payloads'))
check("start: original task", ctx.includes("## Original task") && ctx.includes("W-014") && ctx.includes("work-on"))
check("start: summaries in order", ctx.indexOf("### Phase 1") !== -1 && ctx.indexOf("### Phase 1") < ctx.indexOf("### Phase 4 — wave 1") && ctx.includes("SHA abc123"))
check("start: a refused call is not restored", !ctx.includes("bad phase name"))
check("start: shared summary text", ctx.includes("## Completed phases (phase_checkpoint)"))
check("start: only on compact", hook("session-start.ts", start({ source: "startup" })).out === "")
check("start: no checkpoints, no output", hook("session-start.ts", start({ transcript_path: workOn })).out === "")
check("start: subagent no-op", hook("session-start.ts", start({ agent_id: "a1" })).out === "")
check("start: missing transcript fails open", (() => { const x = hook("session-start.ts", start({ transcript_path: join(tmp, "nope.jsonl") })); return x.code === 0 && x.out === "" })())
for (const bad of ["", "nope", "[]"]) {
  r = hook("session-start.ts", bad)
  check(`start: broken input ${JSON.stringify(bad)} fails open`, r.code === 0 && r.out === "")
}
// A large transcript is streamed: 60 MB of filler lines, the checkpoint at the end.
const filler = JSON.stringify({ type: "assistant", message: { role: "assistant", content: [{ type: "text", text: "x".repeat(2000) }] } }) + "\n"
const bigT = join(tmp, "huge.jsonl")
writeFileSync(bigT, JSON.stringify(cmd("work-on")) + "\n" + filler.repeat(30000) + JSON.stringify(call("z", "Phase 7 — round 1", "late")) + "\n")
const t0 = Date.now()
const huge = hook("session-start.ts", start({ transcript_path: bigT }))
check("start: 60 MB transcript streamed", huge.out.includes("### Phase 7 — round 1") && Date.now() - t0 < 4000)

// ---- session-start: the work in flight, and the build log --------------------------
const spawn = (id: string, name: string, input: object) => ({
  type: "assistant",
  message: { role: "assistant", content: [{ type: "tool_use", id, name, input }] },
})
const resultText = (id: string, text: string) => ({
  type: "user",
  message: { role: "user", content: [{ type: "tool_result", tool_use_id: id, content: text }] },
})
const notice = (text: string) => ({ type: "user", message: { role: "user", content: `<task-notification>${text}</task-notification>` } })
const proj = join(tmp, "proj")
const dossierDir = join(proj, ".discovery", "dossiers")
Bun.spawnSync(["mkdir", "-p", dossierDir])
const dossierFile = join(dossierDir, "W-014-flush.md")
writeFileSync(
  dossierFile,
  "---\nstatus: building\n---\n\n## Problem\n\nx\n\n## Build log\n\n" +
    Array.from({ length: 60 }, (_, i) => `- line ${i + 1} of the log`).join("\n") +
    "\n\n## Notes\n\n- not part of the log\n",
)
const midWave = write("mid-wave.jsonl", [
  cmd("work-on"),
  spawn("old", "Agent", { description: "before the checkpoint", subagent_type: "agentic-software-development:stub-materialiser", prompt: "p" }),
  resultText("old", "agentId: aold111 (for resuming)\nreport"),
  call("c1", "Phase 3", "contract committed"),
  result("c1"),
  spawn("s1", "Agent", { description: "P1 implementer", subagent_type: "agentic-software-development:implementer", prompt: "p" }),
  resultText("s1", "agentId: ab12cd34 (for resuming)\nRETURNED"),
  spawn("s2", "Agent", { description: "P2 implementer", subagent_type: "agentic-software-development:implementer", prompt: "p" }),
  resultText("s2", "Async agent launched successfully. agentId: ae56fa78"),
  spawn("s3", "Workflow", { name: "agentic-software-development:phase4-wave", args: [{ label: "P3" }] }),
  resultText("s3", JSON.stringify({ status: "async_launched", taskId: "wt9", runId: "run-77" })),
  spawn("s4", "Bash", { command: `python3 dossier_edit.py log ${dossierFile} 'x'` }),
  notice("<task-id>ae56fa78</task-id><status>completed</status>"),
])
r = hook("session-start.ts", start({ transcript_path: midWave, cwd: proj }))
o = r.out ? json(r.out) : undefined
const mid: string = o?.hookSpecificOutput?.additionalContext ?? ""
let restores = 1
check("flight: section present", mid.includes("## In flight since the last checkpoint (3)"))
check("flight: spawns before the checkpoint dropped", !mid.includes("before the checkpoint") && !mid.includes("aold111"))
check("flight: arrived agent with its ID", /"P1 implementer".*agent ab12cd34.*result arrived/.test(mid))
check("flight: background agent completed by its notification", /"P2 implementer".*agent ae56fa78.*result arrived/.test(mid))
check("flight: workflow run pending with run and task IDs", /Workflow "agentic-software-development:phase4-wave".*run run-77.*task wt9.*no result seen yet/.test(mid))
check("flight: build log tail, newest lines", mid.includes("## Build log, last lines") && mid.includes("line 60 of the log") && mid.includes("line 21 of the log") && !mid.includes("line 20 of the log"))
check("flight: build log stops at the next heading", !mid.includes("not part of the log"))
check("flight: checkpoints still restored", mid.includes("### Phase 3") && mid.includes("contract committed"))
check("flight: extras bounded to 6k", mid.length - mid.indexOf("## In flight") <= 6200)

// The dossier found by the /work-on ID alone (no path in the transcript), a pending spawn with no checkpoint.
const byId = write("by-id.jsonl", [cmd("work-on"), spawn("q1", "Task", { description: "UT-A", subagent_type: "agentic-software-development:unit-test-author", prompt: "p" })])
r = hook("session-start.ts", start({ transcript_path: byId, cwd: proj }))
restores++
o = r.out ? json(r.out) : undefined
const idCtx: string = o?.hookSpecificOutput?.additionalContext ?? ""
check("flight: no checkpoint but a spawn in flight still restores", idCtx.includes('"UT-A"') && idCtx.includes("no result seen yet"))
check("flight: dossier found from the /work-on ID", idCtx.includes("line 60 of the log"))

// A huge build log never blows the bound; a missing dossier just drops the section.
const hugeDossier = join(dossierDir, "W-099-big.md")
writeFileSync(hugeDossier, "## Build log\n\n" + Array.from({ length: 80 }, (_, i) => `- ${i} ${"y".repeat(1000)}`).join("\n") + "\n")
const bounded = write("bounded.jsonl", [cmd("work-on"), call("b1", "Phase 1", "s"), result("b1"), spawn("b2", "Bash", { command: `cat ${hugeDossier}` }), spawn("b3", "Agent", { description: "X", prompt: "p" })])
r = hook("session-start.ts", start({ transcript_path: bounded, cwd: proj }))
restores++
const bctx: string = (r.out ? json(r.out) : undefined)?.hookSpecificOutput?.additionalContext ?? ""
check("flight: huge build log bounded", bctx.length > 0 && bctx.length - bctx.indexOf("## In flight") <= 6200)
const gone = write("gone-dossier.jsonl", [cmd("work-on"), call("g1", "Phase 1", "s"), result("g1"), spawn("g2", "Bash", { command: "ls /nowhere/.discovery/dossiers/W-1.md" })])
r = hook("session-start.ts", start({ transcript_path: gone, cwd: join(tmp, "nowhere") }))
restores++
check("flight: missing dossier fails open", r.code === 0 && !r.out.includes("Build log, last lines") && r.out.includes("### Phase 1"))

// ---- the log ----------------------------------------------------------------------
const lines = readFileSync(join(tmp, "agentic-software-development", "claude", "claude-hooks.log"), "utf8").trim().split("\n")
const count = (e: string) => lines.filter((l) => l.split(" ")[1] === e).length
check("log: timestamped, one line per event", lines.every((l) => /^\d{4}-\d\d-\d\dT[\d:.]+Z (refused|nudge|recorded|restored) \S+ /.test(l)))
check("log: refusals (six orchestrator, one a runner-style /plan session; five blind-read; three stateful reads)", count("refused") === 14)
check("log: nudges (two call-count, two pressure, one call-count in the pressure session)", count("nudge") === 5)
check("log: restores", count("restored") === 2 + restores)
check("log: checkpoints recorded (the interactive one, and the runner stage's two: a runner stage is an orchestrator now)", count("recorded") === 3)

console.log(fails.length ? "FAIL " + fails.join(" | ") : "PASS (all cases)")
process.exit(fails.length ? 1 : 0)
