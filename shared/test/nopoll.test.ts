// Run: bun --no-install shared/test/nopoll.test.ts
// The no-polling guard of the building sub-agents: the rules (shared/no-poll.ts) and the hook as Claude Code runs it
// (shared/claude/no-poll.ts): refused and allowed commands, the scope (agent type, orchestrator, other agents), the
// config, the switch, and a negative control (the same refused call is allowed where the guard must not apply).
import { mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const { noPoll, sleepSeconds, realCommand, shortAgent } = await import("../no-poll.ts")
const fails: string[] = []
let n = 0
const check = (name: string, ok: boolean) => { n++; ok || fails.push(name) }
const refused = (c: string) => check(`refuse: ${c}`, !!noPoll("Bash", { command: c }))
const allowed = (c: string) => check(`allow: ${c}`, noPoll("Bash", { command: c }) === undefined)

for (const c of [
  "sleep 30", "sleep 6", "sleep 1m", "sleep 3 4", "/bin/sleep 60",
  "while true; do sleep 5; done", "until grep -q done build.log; do sleep 2; done", "for i in 1 2 3; do sleep 1; ls; done",
  "while [ ! -f /tmp/done ]; do sleep 1; done", "cargo build && while pgrep cargo; do sleep 3; done",
  "until [ -f out.log ]; do :; done", "while ! grep -q ok build.log; do :; done", "while kill -0 123; do :; done",
  "watch -n 5 cat build.log", "watch cargo test", "tail -f build.log", "tail -F build.log", "tail -fn 20 build.log", "tail --follow build.log",
  "timeout 600 tail -f build.log", "cargo test > build.log 2>&1 & ", "nohup cargo build &", "cat x | tail -f -",
  "FOO=1 sleep 60", "timeout 120 sleep 90",
]) refused(c)

for (const c of [
  "cargo build", "cargo test -p mycrate --lib", "timeout 600 cargo test -p mycrate", "timeout -k 5 300 pytest tests/unit", "dotnet build && dotnet test --no-build",
  "sleep 1", "sleep 5", "sleep 0.5", "ruff check . && mypy src", "tail -n 50 build.log", "tail -20 build.log | grep error", "cat build.log | head -40",
  "python3 scripts/run_tests.py --wait /tmp/t.log", "python3 scripts/run_tests.py -p mycrate --detach", "python3 scripts/run_gates.py --wait abc123",
  "for f in src/*.rs; do rustfmt --check $f; done", "while read -r l; do grep -q x <<< \"$l\"; done < list.txt", "git commit -m 'sleep 30 in a while loop; tail -f'",
  "echo 'watch this'", "cargo test 2>&1 | tail -30", "cargo build && echo ok &&", "make -j4 2>&1 | tee build.log", "grep -n sleep src/lib.rs", "git status",
  "", "ls &>/dev/null",
]) allowed(c)

check("refuse: run_in_background true", !!noPoll("Bash", { command: "cargo test", run_in_background: true }))
check("allow: run_in_background false", noPoll("Bash", { command: "cargo test", run_in_background: false }) === undefined)
check("allow: other tools", noPoll("Read", { command: "sleep 99", run_in_background: true }) === undefined)
check("bound is configurable (sleep 6 allowed at 10)", noPoll("Bash", { command: "sleep 6" }, 10) === undefined)
check("bound is configurable (sleep 3 refused at 2)", !!noPoll("Bash", { command: "sleep 3" }, 2))
check("reason says what to do instead", /--wait/.test(noPoll("Bash", { command: "tail -f x" }) ?? ""))
check("sleepSeconds: units", sleepSeconds(["1m"]) === 60 && sleepSeconds(["2", "3"]) === 5 && sleepSeconds(["x"]) === undefined)
check("realCommand: timeout wrapper stepped over", realCommand(["timeout", "-k", "5", "300", "cargo", "test"]).join(" ") === "cargo test")
check("shortAgent: prefix dropped", shortAgent("agentic-software-development:implementer") === "implementer")

// ---- the hook, as Claude Code runs it ---------------------------------------------------------------------------
const root = join(import.meta.dir, "..", "..")
const tmp = mkdtempSync(join(tmpdir(), "nopoll-"))
const run = (input: object | string, env: Record<string, string> = {}) => {
  const e: Record<string, string> = { ...(process.env as Record<string, string>), XDG_DATA_HOME: tmp, ...env }
  if (!("ASD_NO_POLL" in env)) delete e.ASD_NO_POLL
  const p = Bun.spawnSync(["bun", "--no-install", join(root, "shared", "claude", "no-poll.ts")], {
    stdin: new TextEncoder().encode(typeof input === "string" ? input : JSON.stringify(input)), env: e, stdout: "pipe", stderr: "pipe",
  })
  return { out: p.stdout.toString().trim(), code: p.exitCode }
}
const call = (command: string, extra: Record<string, unknown> = {}, input: Record<string, unknown> = {}) =>
  ({ hook_event_name: "PreToolUse", session_id: "s1", cwd: tmp, tool_name: "Bash", tool_input: { command, ...input }, ...extra })
const IMPL = { agent_id: "a1", agent_type: "agentic-software-development:implementer" }
const denied = (r: { out: string; code: number | null }) => {
  const o = r.out ? JSON.parse(r.out) : undefined
  return r.code === 0 && o?.hookSpecificOutput?.permissionDecision === "deny" && /^no polling:/.test(o.hookSpecificOutput.permissionDecisionReason)
}
check("hook: implementer polling is denied with the PreToolUse JSON", denied(run(call("until grep -q done b.log; do sleep 2; done", IMPL))))
check("hook: implementer tail -f is denied", denied(run(call("tail -f b.log", IMPL))))
check("hook: implementer run_in_background is denied", denied(run(call("cargo test", IMPL, { run_in_background: true }))))
check("hook: plugin prefix is optional", denied(run(call("watch ls", { agent_id: "a1", agent_type: "implementer" }))))
check("hook: stub-materialiser is in scope", denied(run(call("sleep 60", { agent_id: "a2", agent_type: "agentic-software-development:stub-materialiser" }))))
// Negative control: the same refused calls where the guard must not apply.
check("control: implementer foreground build is allowed", run(call("cargo test -p x", IMPL)).out === "")
check("control: implementer timeout wrapper is allowed", run(call("timeout 600 cargo test -p x", IMPL)).out === "")
check("control: implementer --wait is allowed", run(call("python3 scripts/run_tests.py --wait /tmp/l", IMPL)).out === "")
check("control: the orchestrator (no agent_id) may poll", run(call("tail -f b.log")).out === "")
check("control: the orchestrator may run in the background", run(call("cargo test", {}, { run_in_background: true })).out === "")
check("control: a non-plugin agent may poll", run(call("sleep 60", { agent_id: "a3", agent_type: "Explore" })).out === "")
check("control: a reviewer-type plugin agent is not in scope", run(call("sleep 60", { agent_id: "a4", agent_type: "agentic-software-development:reviewer" })).out === "")
check("control: ASD_NO_POLL=0 allows it", run(call("sleep 60", IMPL), { ASD_NO_POLL: "0" }).out === "")
check("control: a non-Bash tool is allowed", run({ ...call("x", IMPL), tool_name: "Read", tool_input: { file_path: "/x" } }).out === "")
check("hook: unparseable input fails open", (() => { const r = run("not json"); return r.code === 0 && r.out === "" })())
// The scope is the config's: another agent list moves it.
const cfg = join(tmp, "cfg.yaml")
writeFileSync(cfg, "no_poll_agents: [reviewer]\nno_poll_sleep_max: 30\n")
check("config: a configured agent is guarded", denied(run(call("tail -f x", { agent_id: "a4", agent_type: "agentic-software-development:reviewer" }), { CLAUDE_CONTEXT_GUARD_CONFIG: cfg })))
check("config: an agent left out is not", run(call("tail -f x", IMPL), { CLAUDE_CONTEXT_GUARD_CONFIG: cfg }).out === "")
check("config: the sleep bound is read", run(call("sleep 20", { agent_id: "a4", agent_type: "reviewer" }), { CLAUDE_CONTEXT_GUARD_CONFIG: cfg }).out === "")

if (fails.length) { for (const f of fails) console.log(`FAIL ${f}`); console.log(`${fails.length}/${n} failed`); process.exit(1) }
console.log(`PASS ${n} checks`)
