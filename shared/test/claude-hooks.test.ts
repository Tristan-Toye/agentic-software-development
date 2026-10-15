// Run: bun --no-install shared/test/claude-hooks.test.ts
// Each Claude Code hook is run as Claude Code runs it: a process fed hook JSON on stdin,
// with a fixture transcript on disk. Checks stdout and the exit code.
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const root = join(import.meta.dir, "..", "..")
const tmp = mkdtempSync(join(tmpdir(), "hooks-"))
const env = { ...process.env, XDG_DATA_HOME: tmp, PHASE_CHECKPOINT_NUDGE_CALLS: "3" }
delete (env as any).CLAUDE_CONTEXT_GUARD_CONFIG
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
const plan = write("plan.jsonl", [cmd("plan")])
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
check("pre: missing transcript is a no-op", hook("pre-tool-use.ts", pre({ session_id: "s-none", transcript_path: join(tmp, "nope.jsonl") })).out === "")
// The answer is cached per session: the state file outlives the transcript.
const state = JSON.parse(readFileSync(join(tmp, "agentic-software-development", "claude", "s-orch.json"), "utf8"))
check("pre: orchestrator answer cached", state.orchestrator === true)
check("pre: cached answer used without the transcript", hook("pre-tool-use.ts", pre({ transcript_path: join(tmp, "gone.jsonl") })).out.includes('"deny"'))
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

// ---- session-start ----------------------------------------------------------------
const compacted = write("compacted.jsonl", [
  cmd("work-on"),
  call("t1", "Phase 1", "dossier at /w/W-014.md, branch feat/x"),
  result("t1"),
  call("t2", "stubs done", "bad phase name"),
  result("t2", true),
  call("t3", "Phase 4 — wave 1", "wave 1 verified, SHA abc123", "phase_checkpoint"),
  result("t3"),
  { type: "assistant", message: { role: "assistant", content: [{ type: "text", text: "mentions phase_checkpoint in prose" }] } },
])
const start = (over: object) =>
  JSON.stringify({ hook_event_name: "SessionStart", session_id: "s-orch", source: "compact", transcript_path: compacted, cwd: tmp, ...over })
r = hook("session-start.ts", start({}))
o = r.out ? json(r.out) : undefined
const ctx: string = o?.hookSpecificOutput?.additionalContext ?? ""
check("start: emits SessionStart additionalContext", r.code === 0 && o?.hookSpecificOutput?.hookEventName === "SessionStart")
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
check("log: refusals (five orchestrator, five blind-read)", count("refused") === 10)
check("log: nudges", count("nudge") === 2)
check("log: restores", count("restored") === 2 + restores)
check("log: checkpoint recorded", count("recorded") === 1)

console.log(fails.length ? "FAIL " + fails.join(" | ") : "PASS (all cases)")
process.exit(fails.length ? 1 : 0)
