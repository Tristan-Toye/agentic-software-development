// Replays a recorded opencode session through context-guard, request by request, with the
// session's recorded phase_checkpoint cuts applied, and counts what the masking would cost.
// Run: bun opencode/plugins/test/replay-guard.ts <sessionID> <config.yaml>
//      PLUGIN_DIR=<dir> replays another version of context-guard.ts.
// Reads ~/.local/share/opencode (read-only); writes only a temp dir.
// Prints steps, mask moves, floor checks, cache breaks over 20k (and the tokens they re-bill)
// and the average request size: the inputs for tuning context-guard.yaml.
import { Database } from "bun:sqlite"
import { existsSync, mkdtempSync, readFileSync } from "node:fs"
import { homedir, tmpdir } from "node:os"
import { join } from "node:path"

const [sid, cfgPath] = process.argv.slice(2)
const tmp = mkdtempSync(join(tmpdir(), "replay-"))
process.env.XDG_DATA_HOME = tmp
process.env.OPENCODE_CONTEXT_GUARD_CONFIG = cfgPath
const real = join(homedir(), ".local", "share", "opencode")
const cpFile = join(real, "phase-checkpoint", `${sid}.json`)
const cps: any[] = existsSync(cpFile) ? JSON.parse(readFileSync(cpFile, "utf8")) : []

const db = new Database(join(real, "opencode.db"), { readonly: true })
const msgs = db.query("select id, data from message where session_id=? order by time_created").all(sid) as any[]
const parts = db.query("select message_id, data from part where session_id=? order by time_created").all(sid) as any[]
const byMsg = new Map<string, any[]>()
for (const p of parts) {
  const d = JSON.parse(p.data)
  if (d.type === "step-start" || d.type === "step-finish" || d.type === "patch") continue
  ;(byMsg.get(p.message_id) ?? byMsg.set(p.message_id, []).get(p.message_id)!).push(d)
}
const all = msgs.map((m) => {
  const info = JSON.parse(m.data)
  return { info: { ...info, id: m.id, sessionID: sid }, parts: byMsg.get(m.id) ?? [] }
})

const { ContextGuardPlugin } = await import(join(process.env.PLUGIN_DIR ?? join(import.meta.dir, ".."), "context-guard.ts"))
const cg: any = await ContextGuardPlugin({} as never)

// Request size as the model sees it: part text, call inputs and outputs, plus the system
// prompt (37k tokens at the first step of the recorded runs).
const SYSTEM = 37000
const partSize = (p: any) =>
  p?.type === "text" || p?.type === "reasoning" ? String(p.text ?? "").length
  : p?.type === "tool" ? JSON.stringify(p.state?.input ?? "").length + String(p.state?.output ?? "").length : 0
const requestSize = (ms: any[]) => SYSTEM + ms.reduce((a, m) => a + m.parts.reduce((b: number, p: any) => b + partSize(p), 0), 0) / 4
let prevSize = SYSTEM
let prevSent: string[] = []
let breaks = 0, brokenTokens = 0, steps = 0, sizeSum = 0
for (let k = 1; k < all.length; k++) {
  if (all[k].info.role !== "assistant") continue
  steps++
  const t0 = all[k].info.time?.created ?? 0
  // checkpoints recorded before this request
  const known = cps.filter((c) => Date.parse(c.at) < t0)
  // Feed the guard the simulated size of the previous request, not the recorded one.
  const lastA = [...all.slice(0, k)].reverse().find((m: any) => m.info.role === "assistant")
  if (lastA) lastA.info.tokens = { input: 0, output: 0, cache: { read: Math.round(prevSize), write: 0 } }
  const out = { messages: all.slice(0, k) }
  if (known.length) {
    // the checkpoint cut: keep the first message and everything from the latest call on
    const at = out.messages.findIndex((m: any) => m.info.id === known[known.length - 1].messageID)
    if (at > 1) out.messages = [out.messages[0], ...out.messages.slice(at)]
  }
  await cg["experimental.chat.messages.transform"]({}, out)
  // The cacheable prefix: serialised messages; a break is the first index that differs
  // from the previous request (beyond its length = pure append).
  const sent = out.messages.map((m: any) => JSON.stringify(m.parts))
  let i = 0
  while (i < prevSent.length && i < sent.length && sent[i] === prevSent[i]) i++
  const size = requestSize(out.messages)
  prevSize = size
  sizeSum += size
  if (i < prevSent.length) {
    const lost = requestSize(out.messages.slice(i)) - SYSTEM
    if (lost > 20000) { breaks++; brokenTokens += lost }
  }
  prevSent = sent
}
const log = readFileSync(join(tmp, "opencode", "context-guard", "context-guard.log"), "utf8").split("\n")
const n = (e: string) => log.filter((l) => l.split(" ")[1] === e).length
console.log(JSON.stringify({ steps, masks: n("mask"), floors: n("floor"), cacheBreaksOver20k: breaks,
  rebilledTokensK: Math.round(brokenTokens / 1000), avgRequestK: Math.round(sizeSum / steps / 1000) }))
