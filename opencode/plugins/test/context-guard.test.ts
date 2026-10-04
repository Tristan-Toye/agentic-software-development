// Run: bun --no-install opencode/plugins/test/context-guard.test.ts
// Drives the plugin through its hooks: chat.params, tool.execute.before and the
// messages transform. The config comes from context-guard.test.yaml via the env override.
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const tmp = mkdtempSync(join(tmpdir(), "cg-"))
process.env.XDG_DATA_HOME = tmp
process.env.OPENCODE_CONTEXT_GUARD_CONFIG = join(import.meta.dir, "context-guard.test.yaml")
const pluginPath = join(import.meta.dir, "..", "context-guard.ts")
const { ContextGuardPlugin } = await import(pluginPath)
const hooks: any = await ContextGuardPlugin({} as never)
const transform = hooks["experimental.chat.messages.transform"]
const before = hooks["tool.execute.before"]
const params = hooks["chat.params"]

const fails: string[] = []
const check = (name: string, ok: boolean) => ok || fails.push(name)

// ---- masking -----------------------------------------------------------------------

const user = (S: string, agent: string) => ({ info: { id: "u0", sessionID: S, role: "user", agent }, parts: [{ type: "text", text: "task ".repeat(400) }] })
const tp = (tool: string, input: any, n: number) => ({
  type: "tool", tool, callID: "c" + n, state: { status: "completed", input, output: "x".repeat(1000) + n },
})
// Step n: reasoning + a bash call (a1 reads a phase file, a2 edits). `tokens` is what the
// provider reported for it.
function step(S: string, n: number, tokens = 100) {
  const call = n === 1 ? tp("read", { filePath: "/r/references/work-on/phase-3.md" }, n)
    : n === 2 ? tp("edit", { filePath: "/r/a.rs" }, n)
    : tp("bash", { command: "ls " + n }, n)
  return {
    info: { id: "a" + n, sessionID: S, role: "assistant", tokens: { input: tokens, output: 10, cache: { read: 0, write: 0 } } },
    parts: [{ type: "reasoning", text: "think ".repeat(200) + n }, call, { type: "text", text: "step " + n }],
  }
}
const history = (S: string, agent: string, upto: number, lastTokens: number) => [
  user(S, agent),
  ...Array.from({ length: upto }, (_, i) => step(S, i + 1, i + 1 === upto ? lastTokens : 100)),
]
const run = async (msgs: any[]) => {
  const out = { messages: msgs.slice() }
  await transform({}, out)
  return out.messages
}
const masked = (p: any) => typeof p.state?.output === "string" && p.state.output.startsWith("[context-guard:")
const toolOf = (msgs: any[], id: string) => msgs.find((m) => m.info.id === id).parts.find((p: any) => p.type === "tool")
const reasonOf = (msgs: any[], id: string) => msgs.find((m) => m.info.id === id).parts.find((p: any) => p.type === "reasoning")

// 1. Below the trigger nothing changes.
const S = "ses_w"
let stored = history(S, "work-on", 6, 1000)
let snapshot = JSON.stringify(stored)
let out = await run(stored)
check("below trigger: unchanged", JSON.stringify(out) === snapshot)

// 2. Above the trigger: tool outputs older than the newest 2 steps, reasoning older than 4.
stored = history(S, "work-on", 6, 6000)
snapshot = JSON.stringify(stored)
out = await run(stored)
check("stored messages unchanged", JSON.stringify(stored) === snapshot)
check("a3 bash masked", masked(toolOf(out, "a3")))
check("mask text names tool and size", toolOf(out, "a3").state.output.includes("bash output of 1001 chars masked"))
check("call input kept", toolOf(out, "a3").state.input.command === "ls 3")
check("a5, a6 outputs kept", [5, 6].every((n) => !masked(toolOf(out, "a" + n))))
check("protected read of phase file kept", !masked(toolOf(out, "a1")))
check("protected edit tool kept", !masked(toolOf(out, "a2")))
check("first user message kept", JSON.stringify(out[0]) === JSON.stringify(stored[0]))
check("reasoning masked older than 4 steps", reasonOf(out, "a1").text === "[earlier reasoning omitted]")
check("newer reasoning kept", reasonOf(out, "a3").text.startsWith("think") && reasonOf(out, "a6").text.startsWith("think"))
check("text parts kept", out[1].parts.some((p: any) => p.type === "text" && p.text === "step 1"))

// 3. Append-only until the next trigger: the masked prefix is byte-identical.
const grown = [...history(S, "work-on", 6, 100), step(S, 7, 1000), step(S, 8, 2000)]
const out2 = await run(grown)
check("prefix identical after append", JSON.stringify(out2.slice(0, 7).map((m: any) => m.parts)) === JSON.stringify(out.slice(0, 7).map((m: any) => m.parts)))
check("a5 still unmasked below trigger", !masked(toolOf(out2, "a5")))
// Next trigger moves the watermark: a4..a6 now masked, a7+ kept.
const out3 = await run([...grown, step(S, 9, 7000)])
check("next trigger masks more", masked(toolOf(out3, "a6")) && !masked(toolOf(out3, "a8")) && !masked(toolOf(out3, "a9")))
check("tool a7 masked at watermark a7", masked(toolOf(out3, "a7")))
check("reasoning watermark moved", reasonOf(out3, "a5").text === "[earlier reasoning omitted]" && reasonOf(out3, "a6").text.startsWith("think"))

// 4. Persisted watermark: a fresh plugin instance keeps the same masked set.
const persisted = JSON.parse(readFileSync(join(tmp, "opencode", "context-guard", `${S}.json`), "utf8"))
check("persisted watermark", persisted.tool === "a7")
const fresh: any = await (await import(pluginPath + "?fresh=1")).ContextGuardPlugin({} as never)
const freshOut = { messages: [...grown, step(S, 9, 100)] }
await fresh["experimental.chat.messages.transform"]({}, freshOut)
check("fresh instance applies persisted mask", masked(toolOf(freshOut.messages, "a7")) && !masked(toolOf(freshOut.messages, "a8")))

// 5. Reasoning is masked only for listed agents; unlisted agents are untouched.
out = await run(history("ses_impl", "implementer", 6, 6000))
check("implementer: tools masked", masked(toolOf(out, "a3")))
check("implementer: reasoning kept", reasonOf(out, "a1").text.startsWith("think"))
const rev = history("ses_rev", "reviewer", 6, 6000)
check("reviewer (not in mask_agents): unchanged", JSON.stringify(await run(rev)) === JSON.stringify(rev))

// ---- guard -------------------------------------------------------------------------

const small = join(tmp, "small.rs")
const big = join(tmp, "big.rs")
const bigMd = join(tmp, "big.md")
writeFileSync(small, "fn a() {}\n")
writeFileSync(big, "fn a() {}\n".repeat(100))
writeFileSync(bigMd, "# t\n".repeat(200))
await params({ sessionID: "g_work", agent: "work-on" })
await params({ sessionID: "g_other", agent: "implementer" })
const attempt = async (sessionID: string, tool: string, args: any) => {
  try {
    await before({ tool, sessionID, callID: "c" }, { args })
    return undefined
  } catch (e) {
    return String((e as Error).message)
  }
}
const dossier = "/repo/.discovery/dossiers/feature-x.md"
const cargo = "limactl shell nightwatch -- cargo test -p foo"

check("dossier read refused", (await attempt("g_work", "read", { filePath: dossier }))?.includes("dossier_edit.py section") === true)
check("dossier read with limit allowed", (await attempt("g_work", "read", { filePath: dossier, offset: 1, limit: 50 })) === undefined)
check("dossier: other agent allowed", (await attempt("g_other", "read", { filePath: dossier })) === undefined)
check("unknown session allowed", (await attempt("g_unknown", "read", { filePath: dossier })) === undefined)

const bigRefusal = await attempt("g_work", "read", { filePath: big })
check("big source read refused", bigRefusal?.includes("code_outline") === true && bigRefusal.includes("1000 bytes") && bigRefusal.includes("100 lines"))
check("big markdown read names doc_section", (await attempt("g_work", "read", { filePath: bigMd }))?.includes("doc_section") === true)
check("big read with limit allowed", (await attempt("g_work", "read", { filePath: big, limit: 100 })) === undefined)
check("small read allowed", (await attempt("g_work", "read", { filePath: small })) === undefined)
check("missing file allowed", (await attempt("g_work", "read", { filePath: join(tmp, "nope.rs") })) === undefined)
check("big read: other agent allowed", (await attempt("g_other", "read", { filePath: big })) === undefined)

check("cargo test via limactl refused", (await attempt("g_work", "bash", { command: cargo }))?.includes("run_tests.py --vm nightwatch") === true)
check("cargo nextest via limactl refused", (await attempt("g_work", "bash", { command: "limactl shell vm cargo nextest run" })) !== undefined)
check("cargo test through run_tests.py allowed", (await attempt("g_work", "bash", { command: `python3 scripts/run_tests.py -- ${cargo}` })) === undefined)
check("plain cargo test allowed", (await attempt("g_work", "bash", { command: "cargo test" })) === undefined)
check("cargo: other agent allowed", (await attempt("g_other", "bash", { command: cargo })) === undefined)

check("cat of big file refused", (await attempt("g_work", "bash", { command: `cat ${big}` }))?.includes("code_outline") === true)
check("cat of small file allowed", (await attempt("g_work", "bash", { command: `cat ${small}` })) === undefined)
check("cat piped allowed", (await attempt("g_work", "bash", { command: `cat ${big} | head -5` })) === undefined)
check("cat: other agent allowed", (await attempt("g_other", "bash", { command: `cat ${big}` })) === undefined)

// ---- log ---------------------------------------------------------------------------

const lines = readFileSync(join(tmp, "opencode", "context-guard", "context-guard.log"), "utf8").trim().split("\n")
const ev = (e: string) => lines.filter((l) => l.split(" ")[1] === e)
check("log: timestamped", lines.every((l) => /^\d{4}-\d\d-\d\dT[\d:.]+Z /.test(l)))
check("log: mask lines for ses_w (two moves)", ev("mask").filter((l) => l.includes(" ses_w ")).length === 2)
check("log: mask line format", ev("mask").some((l) => / mask ses_w watermark a4: ~\d+k -> ~\d+k tokens, 2 outputs, 2 reasoning$/.test(l)))
check("log: refused line format", ev("refused").some((l) => / refused g_work work-on read: read a dossier by section/.test(l)))
check("log: refusals count", ev("refused").length === 6)

console.log(fails.length ? "FAIL " + fails.join(" | ") : "PASS (all cases)")
process.exit(fails.length ? 1 : 0)
