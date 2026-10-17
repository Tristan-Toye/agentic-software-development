// Run: bun --no-install shared/test/mcp.test.ts
// Spawns the MCP server as Claude Code does (stdio, newline-delimited JSON-RPC) and drives it.
import { mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const root = join(import.meta.dir, "..", "..")
const dir = mkdtempSync(join(tmpdir(), "mcp-"))
writeFileSync(join(dir, "notes.md"), "# Notes\n\n## LRN-0001: first\n\nbody one\n\n## LRN-0002: second\n\nbody two\n")

const fails: string[] = []
const check = (name: string, ok: boolean) => ok || fails.push(name)

const proc = Bun.spawn(["bun", "--no-install", join(root, "shared", "claude", "mcp.ts")], {
  stdin: "pipe",
  stdout: "pipe",
  stderr: "pipe",
  env: { ...process.env, CLAUDE_PROJECT_DIR: dir },
})
const reader = proc.stdout.getReader()
const decoder = new TextDecoder()
let buf = ""
async function next(): Promise<any> {
  while (!buf.includes("\n")) {
    const { value, done } = await reader.read()
    if (done) throw new Error("server closed")
    buf += decoder.decode(value)
  }
  const at = buf.indexOf("\n")
  const line = buf.slice(0, at)
  buf = buf.slice(at + 1)
  return JSON.parse(line)
}
let id = 0
const send = (msg: object) => proc.stdin.write(JSON.stringify(msg) + "\n")
async function rpc(method: string, params?: object) {
  send({ jsonrpc: "2.0", id: ++id, method, params })
  const res = await next()
  check(`${method}: id echoed`, res.id === id && res.jsonrpc === "2.0")
  return res
}

const init = await rpc("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "t", version: "0" } })
check("initialize: tools capability", init.result.capabilities.tools !== undefined && init.result.serverInfo.name.length > 0)
check("initialize: version echoed", init.result.protocolVersion === "2025-06-18")
send({ jsonrpc: "2.0", method: "notifications/initialized" }) // a notification: no reply
check("ping", JSON.stringify((await rpc("ping")).result) === "{}")

const list = (await rpc("tools/list")).result.tools
check("tools/list: six", list.map((t: any) => t.name).join() === "code_outline,code_item,code_find,doc_section,code_replace,phase_checkpoint")
check(
  "tools/list: valid JSON Schema",
  list.every(
    (t: any) =>
      t.inputSchema.type === "object" &&
      typeof t.description === "string" &&
      t.description.length > 50 &&
      Array.isArray(t.inputSchema.required) &&
      t.inputSchema.required.every((r: string) => r in t.inputSchema.properties) &&
      Object.values(t.inputSchema.properties).every((p: any) => ["string", "number", "boolean"].includes(p.type) && typeof p.description === "string"),
  ),
)
check("tools/list: claude host sentence", list.find((t: any) => t.name === "phase_checkpoint").description.includes("does not cut"))

// doc_section on a fixture, relative to CLAUDE_PROJECT_DIR.
const sec = (await rpc("tools/call", { name: "doc_section", arguments: { path: "notes.md", heading: "LRN-0001" } })).result
check("doc_section: body", sec.isError === undefined && sec.content[0].text.includes("body one") && !sec.content[0].text.includes("body two"))
const bad = (await rpc("tools/call", { name: "code_outline", arguments: { path: "notes.txt" } })).result
check("code_* script failure is isError", bad.isError === true && bad.content[0].text.includes("code_nav failed"))
check("missing argument is isError", (await rpc("tools/call", { name: "doc_section", arguments: { path: "notes.md" } })).result.isError === true)
check("wrong type is isError", (await rpc("tools/call", { name: "code_item", arguments: { path: "a.rs", symbol: "x", mode: "huge" } })).result.isError === true)

// phase_checkpoint: validated by shared/checkpoint.ts.
const refused = (await rpc("tools/call", { name: "phase_checkpoint", arguments: { phase: "stubs done", summary: "x" } })).result
check("phase_checkpoint: bad phase name -> isError", refused.isError === true && refused.content[0].text.includes('"Phase <N>'))
check("phase_checkpoint: long summary -> isError", (await rpc("tools/call", { name: "phase_checkpoint", arguments: { phase: "Phase 1", summary: "x".repeat(6001) } })).result.isError === true)
const noNext = (await rpc("tools/call", { name: "phase_checkpoint", arguments: { phase: "Phase 4 — wave 1", summary: "wave 1 returned" } })).result
check("phase_checkpoint: a summary without a NEXT: line -> isError naming it", noNext.isError === true && noNext.content[0].text.includes("NEXT:"))
const ok = (await rpc("tools/call", { name: "phase_checkpoint", arguments: { phase: "Phase 4 — wave 1", summary: "wave 1 returned\nNEXT: rule the returns" } })).result
check("phase_checkpoint: confirmed as recorded and restored after compaction", ok.isError === undefined && ok.content[0].text.includes("recorded") && ok.content[0].text.includes("restored"))

// protocol errors
const unknown = await rpc("tools/call", { name: "nope", arguments: {} })
check("unknown tool: JSON-RPC error", unknown.error?.code === -32602)
check("unknown method: JSON-RPC error", (await rpc("resources/list")).error?.code === -32601)
proc.stdin.write("not json\n")
check("parse error answered", (await next()).error?.code === -32700)

proc.stdin.end()
check("exits on EOF", (await proc.exited) === 0)

console.log(fails.length ? "FAIL " + fails.join(" | ") : "PASS (all cases)")
process.exit(fails.length ? 1 : 0)
