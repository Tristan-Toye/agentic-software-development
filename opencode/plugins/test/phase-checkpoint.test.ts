// Run: bun --no-install opencode/plugins/test/phase-checkpoint.test.ts
// Drives the plugin through its public surface: the tool, the read hook, then the
// transform hook. --no-install proves the SDK resolves the way opencode loads it.
import { mkdtempSync, readFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

process.env.XDG_DATA_HOME = mkdtempSync(join(tmpdir(), "pc-"))
const { PhaseCheckpointPlugin } = await import(join(import.meta.dir, "..", "phase-checkpoint.ts"))
const hooks: any = await PhaseCheckpointPlugin({} as never)
const t = hooks.tool.phase_checkpoint
const transform = hooks["experimental.chat.messages.transform"]

const S = "ses_1"
const user = (id: string, text: string) => ({ info: { id, sessionID: S, role: "user" }, parts: [{ type: "text", text }] })
const asst = (id: string, parts: any[] = [{ type: "text", text: id }]) => ({ info: { id, sessionID: S, role: "assistant" }, parts })
const history = () => [user("u0", "/work-on W-1"), asst("a1"), asst("a2"), user("u3", "answer"), asst("a4"), asst("a5"), asst("a6")]
const fails: string[] = []
const check = (name: string, ok: boolean) => ok || fails.push(name)
const ctx = (agent: string, messageID: string) => ({ sessionID: S, messageID, agent }) as any

// 1. Nothing recorded: the request is unchanged.
let out = { messages: history() }
await transform({}, out)
check("no checkpoint, no change", out.messages.length === 7)

// 2. Guards.
check("subagent refused", String(await t.execute({ phase: "P", summary: "x" }, ctx("implementer", "a4"))).includes("refused"))
check("long summary refused", String(await t.execute({ phase: "P", summary: "x".repeat(7000) }, ctx("work-on", "a4"))).includes("refused"))

// 3. A checkpoint made in a4 drops a1..u3, keeps u0 (with the summary), a4 and later.
const r = String(await t.execute({ phase: "Phase 2", summary: "stubs at abc123" }, ctx("work-on", "a4")))
check("recorded", r.includes("checkpoint 1 recorded"))
const original = history()
out = { messages: original.slice() }
await transform({}, out)
check("cut keeps u0,a4,a5,a6", out.messages.map((m: any) => m.info.id).join() === "u0,a4,a5,a6")
check("summary on u0", out.messages[0].parts.at(-1).text.includes("stubs at abc123") && out.messages[0].parts.at(-1).synthetic)
check("stored u0 untouched", original[0].parts.length === 1)

// 4. A second checkpoint accumulates summaries and cuts later.
await t.execute({ phase: "Phase 3", summary: "contract review clean" }, ctx("work-on", "a6"))
out = { messages: history() }
await transform({}, out)
const txt = out.messages[0].parts.at(-1).text
check("second cut", out.messages.map((m: any) => m.info.id).join() === "u0,a6")
check("both summaries, in order", txt.indexOf("Phase 2") < txt.indexOf("Phase 3") && txt.includes("contract review clean"))

// 5. Renamed message ids: found by the marker in the tool output.
const renamed = history().map((m) => ({ ...m, info: { ...m.info, id: "x" + m.info.id } }))
renamed[6] = asst("xa6", [{ type: "tool", tool: "phase_checkpoint", state: { output: "phase_checkpoint: checkpoint 2 recorded" } }])
out = { messages: renamed }
await transform({}, out)
check("marker fallback", out.messages.map((m: any) => m.info.id).join() === "xu0,xa6")

// 6. Persisted: a fresh plugin instance (restart) applies the same cut.
const file = JSON.parse(readFileSync(join(process.env.XDG_DATA_HOME!, "opencode", "phase-checkpoint", `${S}.json`), "utf8"))
check("persisted two checkpoints", file.length === 2)

// 7. Phase names and the missed-boundary nudge (a fresh session).
const after = hooks["tool.execute.after"]
const S2 = "ses_2"
const read = async (n: number) => {
  const o = { title: "", output: `phase ${n} text`, metadata: {} }
  await after({ tool: "read", sessionID: S2, callID: "c", args: { filePath: `/p/references/work-on/phase-${n}.md` } }, o)
  return o.output
}
const ctx2 = (messageID: string) => ({ sessionID: S2, messageID, agent: "work-on" }) as any
check("unnamed phase refused", String(await t.execute({ phase: "stubs done", summary: "x" }, ctx2("m1"))).includes('"Phase <N>'))
check("first phase read: no nudge", (await read(2)) === "phase 2 text")
check("wrong phase refused", String(await t.execute({ phase: "Phase 3", summary: "x" }, ctx2("m2"))).includes("in use is Phase 2"))
check("missed boundary nudged", (await read(3)).includes("Phase 2 has no checkpoint"))
await t.execute({ phase: "Phase 3 — Mechanical check", summary: "ok" }, ctx2("m3"))
check("checkpointed boundary: no nudge", (await read(4)) === "phase 4 text")
check("other tools untouched", await (async () => {
  const o = { title: "", output: "x", metadata: {} }
  await after({ tool: "bash", sessionID: S2, callID: "c", args: { command: "cat references/work-on/phase-9.md" } }, o)
  return o.output === "x"
})())

console.log(fails.length ? "FAIL " + fails.join(" | ") : "PASS (all cases)")
process.exit(fails.length ? 1 : 0)
