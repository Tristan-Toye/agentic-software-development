// Run: bun --no-install shared/test/runs-mcp.test.ts
// Starts a dashboard on a free port over empty temp stores, then the runs MCP server against it, and drives both.
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const root = join(import.meta.dir, "..", "..")
const fails: string[] = []
const check = (name: string, ok: boolean, why = "") => { if (!ok) fails.push(name + (why ? ` — ${why.slice(0, 300)}` : "")) }

const sdir = mkdtempSync(join(tmpdir(), "runs-mcp-q-"))
const runsDir = mkdtempSync(join(tmpdir(), "runs-mcp-r-"))
const q = { ts: "2026-11-06T10:00:00Z", from: "agent", kind: "question", repo: "demo", dossier: "W-2", root: "", text: "Which way?", blocks: "the build",
  options: [{ key: "A", label: "one", consequence: "c1" }, { key: "B", label: "two", consequence: "c2" }], recommendation: "A" }
writeFileSync(join(sdir, "demo.W-2.Q1.jsonl"), JSON.stringify(q) + "\n")
writeFileSync(join(sdir, "demo.W-2.Q2.jsonl"), JSON.stringify({ ...q, text: "And this?" }) + "\n")

const dash = Bun.spawn(["bun", "--no-install", join(root, "shared", "questions", "server.ts")], {
  env: { ...process.env, ASD_QUESTIONS_DIR: sdir, ASD_QUESTIONS_PORT: "0", ASD_QUESTIONS_NO_NOTIFY: "1", ASD_QUESTIONS_NO_SLACK: "1", ASD_SLACK_USER: "",
    ASD_RUNS_ROOTS: "", ASD_RUNS_DIR: runsDir, ASD_CLAUDE_PROJECTS: mkdtempSync(join(tmpdir(), "runs-mcp-p-")), ASD_RUNS_NO_GH: "1", ASD_STUBS_NO_GIT: "1",
    ASD_MERGE_DETECTOR: "0", ASD_STUB_FETCH_SECS: "0", ASD_VIEW_CACHE_DIR: mkdtempSync(join(tmpdir(), "runs-mcp-v-")), ASD_OPEN_WORK_DIR: mkdtempSync(join(tmpdir(), "runs-mcp-o-")) },
  stdout: "pipe", stderr: "pipe",
})
const dr = dash.stdout.getReader()
let dout = ""
while (!/listening http:\/\/127\.0\.0\.1:(\d+)/.test(dout)) {
  const { value, done } = await dr.read()
  if (done) break
  dout += new TextDecoder().decode(value)
}
const port = dout.match(/127\.0\.0\.1:(\d+)/)?.[1] ?? ""

function mcp(p: string) {
  const proc = Bun.spawn(["bun", "--no-install", join(root, "shared", "claude", "runs-mcp.ts")], { stdin: "pipe", stdout: "pipe", stderr: "pipe", env: { ...process.env, ASD_QUESTIONS_PORT: p, ASD_RUNS_MCP_TIMEOUT_MS: "20000" } })
  const reader = proc.stdout.getReader(); const dec = new TextDecoder(); let buf = ""; let id = 0
  const next = async (): Promise<any> => {
    while (!buf.includes("\n")) { const { value, done } = await reader.read(); if (done) throw new Error("server closed"); buf += dec.decode(value) }
    const at = buf.indexOf("\n"); const line = buf.slice(0, at); buf = buf.slice(at + 1); return JSON.parse(line)
  }
  const rpc = async (method: string, params?: object) => { proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: ++id, method, params }) + "\n"); return next() }
  const call = async (name: string, args: object = {}) => (await rpc("tools/call", { name, arguments: args })).result
  return { proc, rpc, call }
}

try {
  check("dashboard started", port !== "", dout)
  const m = mcp(port)
  const init = await m.rpc("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "t", version: "0" } })
  check("initialize: tools capability", init.result?.capabilities?.tools !== undefined)
  const list = await m.rpc("tools/list")
  const names = (list.result?.tools ?? []).map((t: any) => t.name)
  for (const n of ["runs_overview", "run_detail", "deferred", "stubs", "questions", "question", "runner_log", "answer_question", "reply_question", "message_run", "run_control", "plan_item", "merge_preview", "merge", "block_ruling", "write_summary", "refresh_usage"])
    check(`tools/list: ${n}`, names.includes(n))
  check("tools/list: every tool has an object schema", (list.result?.tools ?? []).every((t: any) => t.inputSchema?.type === "object"))

  const ov = await m.call("runs_overview")
  check("runs_overview on an empty store", !ov.isError && JSON.parse(ov.content[0].text).openQuestions === 2, JSON.stringify(ov))
  const qs = await m.call("questions")
  const ql = JSON.parse(qs.content[0].text)
  check("questions: open, oldest first, with options", ql.length === 2 && ql[0].options[0] === "A: one" && ql[0].recommendation === "A", qs.content[0].text)
  const rp = await m.call("reply_question", { id: "demo.W-2.Q2", text: "why two?" })
  const q2 = await m.call("question", { id: "demo.W-2.Q2" })
  check("reply_question: a follow-up in the thread, the question stays open", !rp.isError && /followup: why two\?/.test(q2.content[0].text) && JSON.parse(q2.content[0].text).state === "open", q2.content[0].text)
  const an = await m.call("answer_question", { id: "demo.W-2.Q1", option: "B" })
  const line = readFileSync(join(sdir, "demo.W-2.Q1.jsonl"), "utf8").trim().split("\n").pop()!
  check("answer_question: the owner's answer is stored", !an.isError && JSON.parse(line).option === "B" && JSON.parse(line).from === "owner", line)
  const again = await m.call("answer_question", { id: "demo.W-2.Q1", option: "A" })
  check("answer_question: an answered question refuses a second answer", again.isError === true && /refused/.test(again.content[0].text))
  const bad = await m.call("run_control", { repo: "demo", id: "W-9", action: "launch" })
  check("arguments are checked", bad.isError === true && /must be one of/.test(bad.content[0].text))
  const nod = await m.call("run_detail", { repo: "demo", id: "W-404" })
  check("an unknown dossier is refused, not an exception", nod.isError === true && /refused \(HTTP 404\)/.test(nod.content[0].text), JSON.stringify(nod))
  m.proc.kill()

  const down = mcp("1")
  await down.rpc("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "t", version: "0" } })
  const dn = await down.call("runs_overview")
  check("a dashboard that is down is said plainly", dn.isError === true && /not reachable/.test(dn.content[0].text), JSON.stringify(dn))
  down.proc.kill()
} finally {
  dash.kill()
}
if (fails.length) { console.log("FAIL\n  " + fails.join("\n  ")); process.exit(1) }
console.log("PASS (all cases)")
