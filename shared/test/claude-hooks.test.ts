// Run: bun --no-install shared/test/claude-hooks.test.ts
// Each Claude Code hook is run as Claude Code runs it: a process fed hook JSON on stdin,
// with a fixture transcript on disk. Checks stdout and the exit code.
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs"
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

// ---- the log ----------------------------------------------------------------------
const lines = readFileSync(join(tmp, "agentic-software-development", "claude", "claude-hooks.log"), "utf8").trim().split("\n")
const count = (e: string) => lines.filter((l) => l.split(" ")[1] === e).length
check("log: timestamped, one line per event", lines.every((l) => /^\d{4}-\d\d-\d\dT[\d:.]+Z (refused|nudge|recorded|restored) \S+ /.test(l)))
check("log: refusals", count("refused") === 5)
check("log: nudges", count("nudge") === 2)
check("log: restores", count("restored") === 2)
check("log: checkpoint recorded", count("recorded") === 1)

console.log(fails.length ? "FAIL " + fails.join(" | ") : "PASS (all cases)")
process.exit(fails.length ? 1 : 0)
