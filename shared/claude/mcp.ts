// A minimal stdio MCP server (JSON-RPC 2.0, newline-delimited) for the Claude Code plugin.
// It serves the six tools of shared/tools.ts: code_outline, code_item, code_find,
// doc_section, code_replace, phase_checkpoint. Registered by the plugin's .mcp.json, so
// Claude Code names them mcp__plugin_agentic-software-development_nav__<tool>.
//
// The code_* tools run scripts/code_nav.py in CLAUDE_PROJECT_DIR (Claude Code sets it in
// a stdio server's environment to the project root), else the process's cwd.
// phase_checkpoint cannot cut Claude Code's history: it validates and confirms; its record
// is the tool call itself in the session transcript, which the SessionStart(compact) hook
// reads back after a compaction.
import { MAX_SUMMARY_CHARS, TOOL, validateCheckpoint } from "../checkpoint"
import { TOOLS, describeTool, inputSchema, toolByName, type ToolDef } from "../tools"

const SERVER = { name: "agentic-software-development-nav", version: "1.8.0" }
const SUPPORTED = ["2025-06-18", "2025-03-26", "2024-11-05"]

type Rpc = { jsonrpc?: string; id?: string | number | null; method?: string; params?: any }

const text = (s: string, isError = false) => ({ content: [{ type: "text", text: s }], ...(isError ? { isError: true } : {}) })

function checkArgs(def: ToolDef, args: unknown): string | undefined {
  if (!args || typeof args !== "object" || Array.isArray(args)) return "arguments must be an object"
  const a = args as Record<string, unknown>
  for (const p of def.params) {
    const v = a[p.name]
    if (v === undefined || v === null) {
      if (!p.optional) return `missing required argument "${p.name}"`
      continue
    }
    if (typeof v !== p.type) return `argument "${p.name}" must be a ${p.type}`
    if (p.enum && !p.enum.includes(v as string)) return `argument "${p.name}" must be one of ${p.enum.join(", ")}`
  }
  return undefined
}

async function callTool(name: string, args: unknown) {
  const def = toolByName(name)
  if (!def) return undefined
  const bad = checkArgs(def, args)
  if (bad) return text(`${name}: ${bad}`, true)
  const a = args as Record<string, any>
  if (name === TOOL) {
    const why = validateCheckpoint(a.phase, a.summary)
    if (why) return text(`${TOOL}: refused — ${why}`, true)
    return text(
      `${TOOL}: recorded after ${a.phase} (summary ${a.summary.length} of ${MAX_SUMMARY_CHARS} characters). ` +
        "Claude Code compacts on its own and cannot be cut from here; after the next compaction this checkpoint " +
        "and every earlier one are restored into your context with the original task.",
    )
  }
  const out = await def.execute!(a, { directory: process.env.CLAUDE_PROJECT_DIR || process.cwd() })
  return text(out, out.startsWith("code_nav failed"))
}

async function handle(msg: Rpc): Promise<object | undefined> {
  const reply = (result: object) => ({ jsonrpc: "2.0", id: msg.id, result })
  const fail = (code: number, message: string) => ({ jsonrpc: "2.0", id: msg.id ?? null, error: { code, message } })
  if (msg.id === undefined) return undefined // a notification: never answered
  switch (msg.method) {
    case "initialize": {
      const asked = msg.params?.protocolVersion
      return reply({
        protocolVersion: SUPPORTED.includes(asked) ? asked : SUPPORTED[0],
        capabilities: { tools: {} },
        serverInfo: SERVER,
      })
    }
    case "ping":
      return reply({})
    case "tools/list":
      return reply({
        tools: TOOLS.map((def) => ({ name: def.name, description: describeTool(def, "claude"), inputSchema: inputSchema(def) })),
      })
    case "tools/call": {
      try {
        const result = await callTool(String(msg.params?.name ?? ""), msg.params?.arguments ?? {})
        return result ? reply(result) : fail(-32602, `unknown tool: ${msg.params?.name}`)
      } catch (err) {
        return reply(text(`${msg.params?.name}: ${String(err)}`, true))
      }
    }
    default:
      return fail(-32601, `method not found: ${msg.method}`)
  }
}

let pending = Promise.resolve()
function onLine(line: string) {
  if (!line.trim()) return
  let msg: Rpc
  try {
    msg = JSON.parse(line)
  } catch {
    process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "parse error" } }) + "\n")
    return
  }
  // Requests are answered in arrival order.
  pending = pending.then(async () => {
    const out = await handle(msg)
    if (out) process.stdout.write(JSON.stringify(out) + "\n")
  })
}

const decoder = new TextDecoder()
let buffer = ""
for await (const chunk of Bun.stdin.stream()) {
  buffer += decoder.decode(chunk, { stream: true })
  let at: number
  while ((at = buffer.indexOf("\n")) !== -1) {
    onLine(buffer.slice(0, at))
    buffer = buffer.slice(at + 1)
  }
}
onLine(buffer)
await pending
