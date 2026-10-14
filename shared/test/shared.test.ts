// Run: bun --no-install shared/test/shared.test.ts
// The shared modules on their own: config, read guard, checkpoint texts, tool definitions,
// and the symlink proof — an opencode plugin imported through a link in another directory
// still resolves ../../shared (config and imports) through its real path.
import { cpSync, mkdirSync, mkdtempSync, symlinkSync, utimesSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { DEFAULTS, configLoader, protectRegexes } from "../config"
import { MAX_SUMMARY_CHARS, nudgeEvery, phaseNumber, reminderText, summaryText, validateCheckpoint } from "../checkpoint"
import { readGuard } from "../read-guard"
import { TOOLS, describeTool, inputSchema } from "../tools"

const fails: string[] = []
const check = (name: string, ok: boolean) => ok || fails.push(name)
const tmp = mkdtempSync(join(tmpdir(), "shared-"))
const repo = join(import.meta.dir, "..", "..")

// ---- config ------------------------------------------------------------------------
const yaml = join(tmp, "c.yaml")
writeFileSync(yaml, "max_read_bytes: 100\nguard_agents: [a, b]\nprotect_paths: ['x\\.md', '(']\n")
const load = configLoader(yaml)
const cfg = load()
check("config: values read", cfg.max_read_bytes === 100 && cfg.guard_agents.join() === "a,b")
check("config: missing key defaults", cfg.trigger_tokens === DEFAULTS.trigger_tokens)
check("config: bad regex skipped", protectRegexes(cfg).length === 1 && protectRegexes(cfg)[0].test("x.md"))
writeFileSync(yaml, "max_read_bytes: [broken")
utimesSync(yaml, new Date(), new Date(Date.now() + 5000))
check("config: broken edit keeps last good", load().max_read_bytes === 100)
writeFileSync(yaml, "max_read_bytes: 7\n")
utimesSync(yaml, new Date(), new Date(Date.now() + 10000))
check("config: mtime change re-read", load().max_read_bytes === 7)
check("config: missing file gives defaults", configLoader(join(tmp, "none.yaml"))().max_read_bytes === DEFAULTS.max_read_bytes)
check("config: the shipped yaml parses to the defaults' shape", configLoader(join(repo, "shared", "context-guard.yaml"))().max_read_bytes === 20000)

// ---- read guard --------------------------------------------------------------------
const small = join(tmp, "small.rs")
const big = join(tmp, "big.rs")
writeFileSync(small, "fn a() {}\n")
writeFileSync(big, "fn a() {}\n".repeat(3000))
const g = (call: any, cwd = tmp) => readGuard(call, cwd, DEFAULTS, "/pipe")
check("guard: dossier read", g({ kind: "read", path: "/r/.discovery/dossiers/x.md" })?.includes("python3 /pipe/scripts/dossier_edit.py section") === true)
check("guard: dossier with limit", g({ kind: "read", path: "/r/.discovery/dossiers/x.md", limit: 5 }) === undefined)
check("guard: big file", g({ kind: "read", path: big })?.includes("code_outline") === true)
check("guard: relative path via cwd", g({ kind: "read", path: "big.rs" }) !== undefined)
check("guard: small file", g({ kind: "read", path: small }) === undefined)
check("guard: protected phase file", g({ kind: "read", path: "/r/references/work-on/phase-2.md" }) === undefined)
check("guard: cargo via limactl", g({ kind: "bash", command: "limactl shell vm -- cargo test" })?.includes("/pipe/scripts/run_tests.py") === true)
check("guard: limactl in an argument", g({ kind: "bash", command: 'x.py --f "limactl shell vm cargo test"' }) === undefined)
check("guard: cat big", g({ kind: "bash", command: `cat ${big}` }) !== undefined)
check("guard: cat piped", g({ kind: "bash", command: `cat ${big} | head` }) === undefined)

// ---- checkpoint --------------------------------------------------------------------
check("checkpoint: phase number", phaseNumber("Phase 4 — wave 1") === 4 && phaseNumber("stubs") === undefined)
check("checkpoint: validate", validateCheckpoint("stubs", "x") !== undefined && validateCheckpoint("Phase 1", "x".repeat(MAX_SUMMARY_CHARS + 1)) !== undefined && validateCheckpoint("Phase 1", "ok") === undefined)
check("checkpoint: nudge env", nudgeEvery({}) === 80 && nudgeEvery({ OPENCODE_PHASE_CHECKPOINT_NUDGE_CALLS: "3" }) === 3 && nudgeEvery({ PHASE_CHECKPOINT_NUDGE_CALLS: "0" }) === 0 && nudgeEvery({ PHASE_CHECKPOINT_NUDGE_CALLS: "x" }) === 80)
check("checkpoint: reminder", reminderText(80).includes("80 tool calls since the last checkpoint"))
const text = summaryText([{ phase: "Phase 1", summary: " a " }, { phase: "Phase 2", summary: "b" }])
check("checkpoint: summary in order", text.startsWith("## Completed phases") && text.indexOf("### Phase 1\n\na") < text.indexOf("### Phase 2"))

// ---- tools -------------------------------------------------------------------------
check("tools: six, named", TOOLS.map((t) => t.name).join() === "code_outline,code_item,code_find,doc_section,code_replace,phase_checkpoint")
const pc = TOOLS.find((t) => t.name === "phase_checkpoint")!
check("tools: host sentence differs, rest shared", describeTool(pc, "claude").includes("does not cut") && describeTool(pc, "opencode").includes("drops every earlier step") && describeTool(pc, "claude").includes("At most 6000 characters"))
check("tools: others identical on both hosts", TOOLS.filter((t) => !t.hostNote).every((t) => describeTool(t, "claude") === describeTool(t, "opencode")))
const schema: any = inputSchema(TOOLS.find((t) => t.name === "code_item")!)
check("tools: schema", schema.type === "object" && schema.required.join() === "path,symbol" && schema.properties.mode.enum.length === 4 && schema.properties.mode.type === "string")
check("tools: boolean param", (inputSchema(TOOLS.find((t) => t.name === "code_replace")!) as any).properties.with_docs.type === "boolean")

// ---- symlink -----------------------------------------------------------------------
// A copy of the plugin and shared/ with its own yaml (max_read_bytes: 100), the plugin
// linked from elsewhere: a 500-byte read is refused only if the plugin found the yaml
// through its real path and imported ../../shared the same way.
const fake = join(tmp, "fake-repo")
mkdirSync(join(fake, "opencode", "plugins"), { recursive: true })
cpSync(join(repo, "shared"), join(fake, "shared"), { recursive: true, filter: (s) => !s.includes("/test") })
cpSync(join(repo, "opencode", "plugins", "context-guard.ts"), join(fake, "opencode", "plugins", "context-guard.ts"))
writeFileSync(join(fake, "shared", "context-guard.yaml"), "max_read_bytes: 100\nguard_agents: [work-on]\n")
const linkDir = join(tmp, "config", "plugins")
mkdirSync(linkDir, { recursive: true })
symlinkSync(join(fake, "opencode", "plugins", "context-guard.ts"), join(linkDir, "context-guard.ts"))
const mid = join(tmp, "mid.txt")
writeFileSync(mid, "x".repeat(500))
const probe = `
const { ContextGuardPlugin } = await import(${JSON.stringify(join(linkDir, "context-guard.ts"))})
const h = await ContextGuardPlugin({})
await h["chat.params"]({ sessionID: "s", agent: "work-on" })
try { await h["tool.execute.before"]({ tool: "read", sessionID: "s", callID: "c" }, { args: { filePath: ${JSON.stringify(mid)} } }); console.log("allowed") }
catch (e) { console.log("refused " + e.message) }
`
const env = { ...process.env, XDG_DATA_HOME: tmp }
delete (env as any).OPENCODE_CONTEXT_GUARD_CONFIG
const run = Bun.spawnSync(["bun", "--no-install", "-e", probe], { env, stdout: "pipe", stderr: "pipe", cwd: tmp })
const out = run.stdout.toString().trim()
check("symlink: plugin loads through the link and reads the yaml next to ../../shared", out.startsWith("refused context-guard:") && out.includes("500 bytes"))
if (!out.startsWith("refused")) console.error("probe:", out, run.stderr.toString())

console.log(fails.length ? "FAIL " + fails.join(" | ") : "PASS (all cases)")
process.exit(fails.length ? 1 : 0)
