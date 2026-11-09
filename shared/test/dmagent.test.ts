// The owner's DM agent (shared/questions/dmagent.ts): session per thread, resume on a follow-up, argv and result parsing.
import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const dir = mkdtempSync(join(tmpdir(), "dmagent-"))
process.env.ASD_DM_AGENT_FILE = join(dir, "dm.json")
const { ALLOWED_TOOLS, askAgent, claudeArgs, clipForSlack, parseResult, promptFile, readStore } = await import("../questions/dmagent.ts")

let failed = 0
const check = (name: string, ok: boolean) => { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) failed++ }

const a1 = claudeArgs({ sessionId: "S1", resume: false, model: "sonnet", addDirs: ["/r"] })
check("argv: a new session pins its id, the prompt file and dontAsk", a1.includes("--session-id") && a1[a1.indexOf("--session-id") + 1] === "S1" && !a1.includes("--resume")
  && a1[a1.indexOf("--append-system-prompt-file") + 1] === promptFile() && a1[a1.indexOf("--permission-mode") + 1] === "dontAsk" && a1.includes("/r"))
const a2 = claudeArgs({ sessionId: "S1", resume: true, model: "sonnet", addDirs: [] })
check("argv: a follow-up resumes the session", a2[a2.indexOf("--resume") + 1] === "S1" && !a2.includes("--session-id"))
check("argv: the runs MCP server is allowed, writes are not", ALLOWED_TOOLS.includes("mcp__plugin_agentic-software-development_runs") && !ALLOWED_TOOLS.some((t) => /Edit|Write|gh pr merge|git push/.test(t)))
check("result: json result is the answer", JSON.stringify(parseResult('{"type":"result","result":"All good","is_error":false}', "", 0)) === JSON.stringify({ ok: true, text: "All good" }))
check("result: an error says why", parseResult('{"type":"result","subtype":"error_max_turns","is_error":true}', "", 1).ok === false && parseResult("", "boom", 1).text === "boom")
check("slack: long answers are clipped", clipForSlack("x".repeat(50_000)).length < 40_000)

const posts: string[] = [], updates: string[] = [], calls: string[][] = []
const deps = {
  post: async (_c: string, t: string, text: string) => { posts.push(`${t}:${text}`); return `P${posts.length}` },
  update: async (_c: string, ts: string, text: string) => { updates.push(`${ts}:${text}`) },
  configDir: () => "/cfg", addDirs: async () => [], log: () => {},
  spawn: async (argv: string[], env: Record<string, string>, stdin: string) => { calls.push([...argv, `ENV=${env.CLAUDE_CONFIG_DIR}`, `IN=${stdin}`]); return { stdout: JSON.stringify({ result: `answer ${calls.length}` }), stderr: "", code: 0 } },
}
await askAgent({ channel: "D1", ts: "10.1", text: "status?" }, deps)
const sid = readStore()["D1:10.1"]?.sessionId
check("turn: a top-level DM starts a session, stored by its thread", !!sid && calls[0].includes("--session-id") && calls[0].includes(sid!) && calls[0].includes("ENV=/cfg") && calls[0].includes("IN=status?"))
check("turn: the answer replaces the placeholder in the thread", posts[0].startsWith("10.1:") && updates[0] === "P1:answer 1")
await askAgent({ channel: "D1", ts: "10.5", threadTs: "10.1", text: "and W-1?" }, deps)
check("turn: a thread reply resumes the same session", calls[1].includes("--resume") && calls[1].includes(sid!) && readStore()["D1:10.1"].turns === 2)
await askAgent({ channel: "D1", ts: "11.1", text: "new question" }, deps)
check("turn: a new top-level DM is a new session", readStore()["D1:11.1"]?.sessionId !== sid && calls[2].includes("--session-id"))
// Two messages in one thread run one after the other.
let live = 0, maxLive = 0
const slow = { ...deps, spawn: async () => { live++; maxLive = Math.max(maxLive, live); await Bun.sleep(20); live--; return { stdout: '{"result":"ok"}', stderr: "", code: 0 } } }
await Promise.all([askAgent({ channel: "D1", ts: "12.1", text: "a" }, slow), askAgent({ channel: "D1", ts: "12.2", threadTs: "12.1", text: "b" }, slow)])
check("turn: one thread runs one turn at a time", maxLive === 1)

rmSync(dir, { recursive: true, force: true })
if (failed) { console.log(`${failed} failed`); process.exit(1) }
console.log("all passed")
