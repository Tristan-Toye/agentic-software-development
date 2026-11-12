// Run: bun --no-install shared/test/ledger.test.ts
// The learned-rules ledger parser (shared/ledger.ts) and the phase_checkpoint output it feeds,
// with and without tags and with and without a ledger.
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { MAX_LINE_CHARS, nextPhaseKey, parseLedger, rulesBlock } from "../ledger"

const root = join(import.meta.dir, "..", "..")
const fails: string[] = []
const check = (name: string, ok: boolean) => ok || fails.push(name)

const long = "A very long title " + "x".repeat(300)
const LEDGER = [
  "# Learned rules",
  "",
  "### LRN-0011: Platform-gated controls are enforced everywhere",
  "<!-- phases: 2, 4 -->",
  "**must have** — body one.",
  "",
  "### LRN-0012: Always-on rule",
  "<!-- phases: all -->",
  "body two",
  "",
  "### LRN-0013: Plan-time rule",
  "<!-- phases: plan, 3 -->",
  "body three",
  "",
  "### LRN-0014: Old rule",
  "<!-- phases: 4 -->",
  "**superseded by LRN-0015**, which keeps it whole.",
  "",
  "### LRN-0016: No tag at all",
  "body four",
  "",
  `### LRN-0017: ${long}`,
  "<!-- phases: 4 -->",
  "body five",
  "",
].join("\n")

const rules = parseLedger(LEDGER)
check("parse: tagged entries only", rules.map((r) => r.id).join() === "LRN-0011,LRN-0012,LRN-0013,LRN-0014,LRN-0017")
check("parse: phases split and trimmed", rules[0].phases.join() === "2,4" && rules[2].phases.join() === "plan,3")
check("parse: superseded flagged", rules[3].superseded && !rules[0].superseded)
check("parse: no tag, no entry", parseLedger("### LRN-0001: x\nbody\n").length === 0 && parseLedger("").length === 0)

check("next: the NEXT line's phase wins", nextPhaseKey("Phase 4 — wave 2", "done\nNEXT: Phase 5 — read phase-5.md") === "5")
check("next: phase file name", nextPhaseKey("Phase 3", "x\nNEXT: read references/work-on/phase-4.md and fan out") === "4")
check("next: stage inside a phase keeps its phase", nextPhaseKey("Phase 4 — wave 1", "x\nNEXT: launch wave 2") === "4")
check("next: plan checkpoints", nextPhaseKey("Phase 2 — plan: investigate", "x\nNEXT: scope") === "plan")
check("next: no phase at all", nextPhaseKey("stubs done", "x\nNEXT: go") === undefined)

const p4 = rulesBlock(LEDGER, "4")
check("block: heading names the phase and doc_section", p4.includes("Learned rules for Phase 4 (full text: doc_section docs/learned-rules.md <id>):"))
check("block: matches the tag and `all`", p4.includes("LRN-0011 Platform-gated") && p4.includes("LRN-0012 Always-on rule"))
check("block: skips other phases, superseded and untagged", !p4.includes("LRN-0013") && !p4.includes("LRN-0014") && !p4.includes("LRN-0016"))
check("block: one line per rule, capped", p4.split("\n").slice(3).every((l) => l.length <= MAX_LINE_CHARS) && p4.includes("…"))
check("block: plan key", rulesBlock(LEDGER, "plan").includes("Learned rules for plan") && rulesBlock(LEDGER, "plan").includes("LRN-0013"))
check("block: nothing matches, nothing appended", rulesBlock(LEDGER, "9").includes("LRN-0012") && rulesBlock(LEDGER.replace("all", "7"), "9") === "")

// The MCP tool end to end: with a ledger, with an untagged ledger, with none.
async function checkpoint(dir: string, phase: string, summary: string): Promise<string> {
  const proc = Bun.spawn(["bun", "--no-install", join(root, "shared", "claude", "mcp.ts")], {
    stdin: "pipe",
    stdout: "pipe",
    stderr: "pipe",
    env: { ...process.env, CLAUDE_PROJECT_DIR: dir },
  })
  const call = { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "phase_checkpoint", arguments: { phase, summary } } }
  proc.stdin.write(JSON.stringify(call) + "\n")
  proc.stdin.end()
  const out = await new Response(proc.stdout).text()
  await proc.exited
  return JSON.parse(out.split("\n")[0]).result.content[0].text
}
const base = mkdtempSync(join(tmpdir(), "ledger-"))
const withLedger = join(base, "tagged")
mkdirSync(join(withLedger, "docs"), { recursive: true })
writeFileSync(join(withLedger, "docs", "learned-rules.md"), LEDGER)
const untagged = join(base, "untagged")
mkdirSync(join(untagged, "docs"), { recursive: true })
writeFileSync(join(untagged, "docs", "learned-rules.md"), "### LRN-0001: a\nbody\n")
const none = join(base, "none")
mkdirSync(none)

const plain = await checkpoint(none, "Phase 3 — mechanical check", "x\nNEXT: Phase 4 fan-out")
const tagged = await checkpoint(withLedger, "Phase 3 — mechanical check", "x\nNEXT: Phase 4 fan-out")
check("mcp: no ledger behaves as before", plain.includes("recorded after Phase 3") && !plain.includes("Learned rules"))
check("mcp: untagged ledger behaves as before", (await checkpoint(untagged, "Phase 3 — mechanical check", "x\nNEXT: Phase 4 fan-out")) === plain)
check("mcp: tagged ledger appends the next phase's rules", tagged.startsWith(plain) && tagged.includes("Learned rules for Phase 4") && tagged.includes("LRN-0011 ") && !tagged.includes("LRN-0013"))
check("mcp: plan checkpoint lists plan rules", (await checkpoint(withLedger, "Phase 1 — plan: intake", "x\nNEXT: investigate")).includes("LRN-0013 Plan-time rule"))
check("mcp: `all` rules list at any phase", (await checkpoint(withLedger, "Phase 8 — adrs", "x\nNEXT: Phase 8 continues")).includes("LRN-0012 "))

console.log(fails.length ? "FAIL " + fails.join(" | ") : "PASS (all cases)")
process.exit(fails.length ? 1 : 0)
