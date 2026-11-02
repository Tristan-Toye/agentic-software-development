// Owner-questions server: store, API, page and the Slack envelope handlers (no network, no Slack).
// Run: bun --no-install shared/test/questions-server.test.ts
import { mkdtempSync, readFileSync, writeFileSync, appendFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { appendOwner, cut, parseQuestion, readAll, writeSidecar, idForThread, AnsweredError } from "../questions/store.ts"
import { interpretEnvelope, questionBlocks } from "../questions/slack.ts"
import { remindersDue } from "../questions/server.ts"

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

// server over HTTP
const sdir = mkdtempSync(join(tmpdir(), "asd-oq-srv-"))
writeFileSync(join(sdir, "demo.W-2.Q1.jsonl"), JSON.stringify(qLine) + "\n")
const proc = Bun.spawn(["/opt/homebrew/bin/bun", "--no-install", join(import.meta.dir, "..", "questions", "server.ts")], {
  env: { ...process.env, ASD_QUESTIONS_DIR: sdir, ASD_QUESTIONS_PORT: "0", ASD_QUESTIONS_NO_NOTIFY: "1", ASD_QUESTIONS_NO_SLACK: "1", ASD_SLACK_USER: "" },
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
  const bad = await fetch(`${base}/api/questions`, { headers: { host: "evil.example" } })
  check("server: foreign Host header refused", bad.status === 403)
} finally {
  proc.kill()
  rmSync(dir, { recursive: true, force: true })
  rmSync(sdir, { recursive: true, force: true })
}
console.log(failed ? `FAIL (${failed})` : "PASS (all cases)")
process.exit(failed ? 1 : 0)
