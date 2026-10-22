// Run: bun --no-install shared/test/shared.test.ts
// The shared modules on their own: config, read guard, checkpoint texts, tool definitions,
// and the symlink proof — an opencode plugin imported through a link in another directory
// still resolves ../../shared (config and imports) through its real path.
import { cpSync, mkdirSync, mkdtempSync, readFileSync, symlinkSync, utimesSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { DEFAULTS, configLoader, protectRegexes } from "../config"
import { MAX_SUMMARY_CHARS, nextLine, nudgeEvery, phaseNumber, reminderText, summaryText, validateCheckpoint } from "../checkpoint"
import { BLIND_READ_DIRS, blindRead } from "../blind-read"
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
check("guard: cat piped into head is still a read window", g({ kind: "bash", command: `cat ${big} | head` })?.includes("code_item") === true)
// Bash reading windows on source and markdown point at the nav tools.
const nav = (command: string) => g({ kind: "bash", command })
check("nav: sed -n on source", nav("sed -n 10,40p /w/src/lib.rs")?.includes("code_item") === true)
check("nav: sed -n on a dossier names dossier_edit section", nav("sed -n 1,9p /w/.discovery/dossiers/W-1-x.md")?.includes("dossier_edit.py section") === true)
check("nav: sed -n on other markdown names doc_section", nav("sed -n 1,9p /w/docs/rules.md")?.includes("doc_section") === true)
check("nav: a quoted ; inside a sed script does not split it", nav("cd /w && sed -n '10,20p;30p' a.rs | head -3")?.includes("sed -n on a.rs") === true)
check("nav: awk NR range", nav("awk 'NR>=10 && NR<=20' a.py") !== undefined)
check("nav: rtk-wrapped sed", nav("rtk sed -n 1,9p a.py") !== undefined)
check("nav: grep with a big context window", nav("grep -n -A 20 foo /w/src/lib.rs")?.includes("context window") === true)
check("nav: grep with a small context window", nav("grep -C3 foo /w/src/lib.rs") === undefined)
check("nav: plain grep for usages", nav("grep -rn 'fn foo' crates/ --include=*.rs") === undefined)
check("nav: a small file whole", nav(`cat ${small}`) === undefined)
check("nav: a heredoc write", nav("cat > /w/notes.md <<'EOF'\nhello\nEOF") === undefined)
check("nav: a redirect target is not read", nav(`echo x > ${big}`) === undefined)
check("nav: sed -i edits, it does not read", nav("sed -i '' s/a/b/ /w/src/lib.rs") === undefined)
check("nav: yaml and toml are not its business", nav("sed -n 5,9p /w/deploy/x.yaml") === undefined)
check("nav: protected phase files pass", nav("sed -n 1,5p /w/references/work-on/phase-4.md") === undefined)
check("nav: tail -f follows a log", nav("tail -f /w/run.md") === undefined)
check("nav: off when nav_redirect is false", readGuard({ kind: "bash", command: "sed -n 1,9p a.rs" }, tmp, { ...DEFAULTS, nav_redirect: false }, "/pipe") === undefined)

// ---- checkpoint --------------------------------------------------------------------
check("checkpoint: phase number", phaseNumber("Phase 4 — wave 1") === 4 && phaseNumber("stubs") === undefined)
check("checkpoint: validate", validateCheckpoint("stubs", "x") !== undefined && validateCheckpoint("Phase 1", "x".repeat(MAX_SUMMARY_CHARS + 1)) !== undefined && validateCheckpoint("Phase 1", "ok\nNEXT: go") === undefined)
check("checkpoint: a summary needs a NEXT: line", validateCheckpoint("Phase 1", "ok")?.includes("NEXT:") === true)
check("checkpoint: NEXT read from a list item too", nextLine("done\n- NEXT: launch wave 2 in /x") === "launch wave 2 in /x")
const resumed = summaryText([{ phase: "Phase 1", summary: "a\nNEXT: old" }, { phase: "Phase 4 — wave 1", summary: "b\nNEXT: rule P1-P4" }])
check("checkpoint: the restore ends on the latest NEXT", resumed.includes('## Resume here\n\nNEXT (from "Phase 4 — wave 1"): rule P1-P4') && !resumed.includes("NEXT (from \"Phase 1\")"))
check("checkpoint: the resume block restates the read and spawn rules", resumed.includes("read with doc_section") && resumed.includes("payload pasted into the prompt"))
check("checkpoint: no NEXT, no resume block", !summaryText([{ phase: "Phase 1", summary: "a" }]).includes("Resume here"))
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
check("tools: schema", schema.type === "object" && schema.required.join() === "path,symbol" && schema.properties.mode.enum.length === 5 && schema.properties.mode.type === "string")
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

// ---- blind read: one boundary on both hosts -------------------------------------------
// The directories the hook admits equal the unit test author's opencode read map and the
// payload lint's HOOK_READ_SCOPE.
{
  const md = readFileSync(join(repo, "sub-agents", "unit-test-author.md"), "utf8")
  const block = /^  read:\n((?:    .*\n)+)/m.exec(md)?.[1] ?? ""
  const mapDirs = new Set(
    [...block.matchAll(/^    "(?:\*\/)?([^"*]+)\/\*": allow$/gm)].map((m) => m[1]),
  )
  check("blind read: the hook's dirs equal the opencode read map", mapDirs.size > 0 && [...mapDirs].sort().join() === [...BLIND_READ_DIRS].sort().join())
  const py = readFileSync(join(repo, "scripts", "check_payload.py"), "utf8")
  const scope = /HOOK_READ_SCOPE = \{\s*"unit-test-author": \(([^)]*)\)/.exec(py)?.[1] ?? ""
  const lintDirs = [...scope.matchAll(/"([^"]+)"/g)].map((m) => m[1])
  check("blind read: the lint's scope equals the hook's", lintDirs.sort().join() === [...BLIND_READ_DIRS].sort().join())
  check("blind read: relative path resolved against cwd", blindRead("src/lib.rs", "/wt") !== undefined && blindRead("tests/a.rs", "/wt") === undefined)
  check("blind read: a dir name only counts as a whole segment", blindRead("/wt/contests/x.rs", "/") !== undefined)
}

console.log(fails.length ? "FAIL " + fails.join(" | ") : "PASS (all cases)")
process.exit(fails.length ? 1 : 0)
