// Run: bun --no-install shared/test/workflows.test.ts
// The plugin's dynamic workflows (workflows/*.js): each must parse as a workflow script, carry
// a pure-literal `export const meta`, touch nothing the runtime forbids, and — run against a
// stubbed runtime with a fake `agent` — return the compact shape the orchestrator reads.
import { readdirSync, readFileSync } from "node:fs"
import { join } from "node:path"

const dir = join(import.meta.dir, "..", "..", "workflows")
const fails: string[] = []
const check = (name: string, ok: boolean) => ok || fails.push(name)

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor as new (...a: string[]) => (...v: unknown[]) => Promise<unknown>

// The meta literal: the object after `export const meta =`, up to its balanced close.
function metaLiteral(src: string): string | undefined {
  const m = /^export const meta = /.exec(src)
  if (!m) return undefined
  let depth = 0
  let quote = ""
  for (let i = m[0].length; i < src.length; i++) {
    const c = src[i]
    if (quote) {
      if (c === "\\") i++
      else if (c === quote) quote = ""
    } else if (c === "'" || c === '"') quote = c
    else if (c === "{" || c === "[") depth++
    else if (c === "}" || c === "]") {
      depth--
      if (depth === 0) return src.slice(m[0].length, i + 1)
    }
  }
  return undefined
}

// Pure: strings, braces, brackets, commas, colons and bare keys only — no value that is an
// identifier, call, spread, template, arrow, or arithmetic.
function isPureLiteral(lit: string): boolean {
  if (lit.includes("`")) return false
  const bare = lit.replace(/'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"/g, '""')
  return /^(?:[\s{}\[\],:]|""|[A-Za-z_$][\w$]*(?=\s*:))+$/.test(bare)
}

type Opts = { agentType?: string; label?: string; model?: string; schema?: unknown; phase?: string }

async function run(src: string, args: unknown, fake: (prompt: string, opts: Opts) => unknown) {
  const body = src.replace(/^export const meta = /, "const meta = ")
  const logs: string[] = []
  const calls: { prompt: string; opts: Opts }[] = []
  const agent = async (prompt: string, opts: Opts = {}) => {
    calls.push({ prompt, opts })
    return fake(prompt, opts)
  }
  // pipeline: each item flows through the stages on its own; a null stops that item.
  const pipeline = (items: unknown[], ...stages: ((v: any, item?: any, i?: number) => unknown)[]) =>
    Promise.all(
      items.map(async (item, i) => {
        let v: unknown = item
        for (const stage of stages) {
          v = await stage(v, item, i)
          if (v == null) return null
        }
        return v
      }),
    )
  const parallel = (thunks: (() => unknown)[]) => Promise.all(thunks.map((t) => t()))
  const fn = new AsyncFunction("agent", "parallel", "pipeline", "phase", "log", "args", body)
  const result = await fn(agent, parallel, pipeline, () => {}, (m: string) => logs.push(m), args)
  return { result: result as any, calls, logs }
}

const files = readdirSync(dir).filter((f) => f.endsWith(".js"))
check("workflows: phase4-wave and phase7-review ship", files.includes("phase4-wave.js") && files.includes("phase7-review.js"))

for (const file of files) {
  const src = readFileSync(join(dir, file), "utf8")
  const lit = metaLiteral(src)
  check(`${file}: starts with export const meta`, lit !== undefined)
  if (lit === undefined) continue
  check(`${file}: meta is a pure literal`, isPureLiteral(lit))
  const meta = new Function(`return (${lit})`)() as { name: string; description: string; phases?: { title: string }[] }
  check(`${file}: meta has name and description`, typeof meta.name === "string" && meta.name !== "" && typeof meta.description === "string" && meta.description !== "")
  check(`${file}: name matches the file`, file === `${meta.name}.js`)
  check(`${file}: phases are {title}`, (meta.phases ?? []).every((p) => typeof p.title === "string"))
  check(`${file}: nothing the runtime forbids`, !/\bDate\.now\b|\bMath\.random\b|new Date\(\s*\)|\bimport\s*\(|\brequire\s*\(|\bprocess\.|\bBun\./.test(src))
  check(`${file}: no import statement`, !/^\s*import\s/m.test(src))
  let parsed = true
  try {
    new AsyncFunction("agent", "parallel", "pipeline", "phase", "log", "args", src.replace(/^export const meta = /, "const meta = "))
  } catch {
    parsed = false
  }
  check(`${file}: parses as a script body`, parsed)
}

// ---- phase4-wave, against a fake runtime ---------------------------------------------
const wave = readFileSync(join(dir, "phase4-wave.js"), "utf8")
const item = (label: string, over: object = {}) => ({
  agentType: "agentic-software-development:implementer",
  label,
  prompt: `TASK ${label}\nOWNED_PATHS: src/${label}.rs`,
  root: `/wt/${label}`,
  branch: `feat/x-${label}`,
  base: "abc123",
  owned: ["src/" + label + ".rs"],
  kind: "implementer",
  pluginRoot: "/plugin",
  ...over,
})
const reports: Record<string, string | null> = {
  P1: "Done.\nNOTICED: none\nTOUCHED_BEYOND: none",
  P2: "UT blocked.\nGAP: the contract does not say what flush returns on an empty window\nNOTICED: none",
  P3: null,
  P4: "CONTRACT-CHANGE: flush(x) must take &mut self\nI stopped.",
}
const fakeWave = (prompt: string, opts: Opts) => {
  if (opts.agentType) return reports[opts.label as string] ?? null
  // the verifier: answer from the command it was given
  check("wave: verifier is cheap", opts.model === "haiku" && !!opts.schema)
  const label = /--agent '([^']+)'/.exec(prompt)?.[1] ?? "?"
  return {
    verdict: label === "P1" ? "clean" : "flagged",
    flags: label === "P1" ? [] : ["no-commits"],
    returned_line: `${label} RETURNED: abc on feat/x-${label}`,
  }
}
{
  const { result, calls } = await run(wave, [item("P1"), item("P2", { agentType: "agentic-software-development:unit-test-author", kind: "unit-test-author" }), item("P3"), item("P4")], fakeWave)
  const by = (l: string) => result.find((r: any) => r.label === l)
  check("wave: one compact entry per item, in order", Array.isArray(result) && result.map((r: any) => r.label).join() === "P1,P2,P3,P4")
  check("wave: entry shape", result.every((r: any) => ["label", "verdict", "flags", "returned_line", "report_excerpt", "report_path"].every((k) => k in r) && Array.isArray(r.flags)))
  check("wave: spawns carry agentType, label and the pasted prompt", calls.filter((c) => c.opts.agentType).length === 4 && calls.some((c) => c.opts.label === "P2" && c.opts.agentType === "agentic-software-development:unit-test-author" && c.prompt.startsWith("TASK P2")))
  check("wave: clean return", by("P1").verdict === "clean" && by("P1").flags.length === 0 && by("P1").returned_line.startsWith("P1 RETURNED") && by("P1").report_path.endsWith("/wt/P1/.agent-staging/reports/P1.txt"))
  check("wave: GAP line passes through verbatim and flags the item", by("P2").verdict === "flagged" && by("P2").flags.includes("gap") && by("P2").report_excerpt.includes("GAP: the contract does not say what flush returns on an empty window"))
  check("wave: CONTRACT-CHANGE passes through", by("P4").flags.includes("contract-change") && by("P4").report_excerpt.startsWith("CONTRACT-CHANGE: flush(x) must take &mut self"))
  check("wave: a null agent result is 'died' and the tree is still checked", by("P3").verdict === "died" && by("P3").flags.includes("no-report") && by("P3").report_path === "" && calls.some((c) => c.opts.model === "haiku" && c.prompt.includes("--agent 'P3'") && c.prompt.includes("returned no report")))
  check("wave: excerpt bounded", result.every((r: any) => r.report_excerpt.length <= 600))
  check("wave: never releases a slot or writes the log", calls.every((c) => !c.prompt.includes("--release") && !c.prompt.includes("--log ")))
  check("wave: verifier runs the plugin's verify_return.py with the paths given", calls.some((c) => c.prompt.includes("python3 '/plugin/scripts/verify_return.py' --root '/wt/P1' --branch 'feat/x-P1' --base 'abc123' --print-line --agent 'P1' --owned 'src/P1.rs'") && c.prompt.includes("--report '/wt/P1/.agent-staging/reports/P1.txt' --kind 'implementer'")))
}
{
  // a long GAP block is cut at 600 with a pointer to the file
  const long = Array.from({ length: 30 }, (_, i) => `GAP: line ${i} ${"z".repeat(40)}`).join("\n")
  const { result } = await run(wave, [item("P1")], (p, o) => (o.agentType ? long : { verdict: "clean", flags: [], returned_line: "P1 RETURNED: x" }))
  check("wave: long GAP block bounded with a pointer", result[0].report_excerpt.length <= 600 && result[0].report_excerpt.includes("read report_path"))
  // a verifier that dies after the agent returned
  const dead = await run(wave, [item("P1")], (p, o) => (o.agentType ? "ok" : null))
  check("wave: dead verifier flags the return, never clean", dead.result[0].verdict === "flagged" && dead.result[0].flags[0] === "verify-failed")
  // a spawn that throws is a dead agent, not a dead run
  const thrown = await run(wave, [item("P1"), item("P2")], (p, o) => {
    if (o.agentType && o.label === "P2") return null
    return o.agentType ? "fine" : { verdict: "clean", flags: [], returned_line: "r" }
  })
  check("wave: one dead agent does not stop the others", thrown.result[0].verdict === "clean" && thrown.result[1].verdict === "died")
  // argument checks
  check("wave: empty args refused", (await run(wave, [], () => null)).result[0].flags[0] === "bad-args")
  check("wave: missing pluginRoot refused", (await run(wave, [item("P1", { pluginRoot: "" })], () => null)).result[0].flags[0] === "bad-args")
  check("wave: more than 16 refused, nothing spawned", await (async () => { const r = await run(wave, Array.from({ length: 17 }, (_, i) => item("A" + i)), () => null); return r.result[0].flags[0] === "bad-args" && r.calls.length === 0 })())
  const ten = await run(wave, Array.from({ length: 10 }, (_, i) => item("B" + i)), (p, o) => (o.agentType ? "ok" : { verdict: "clean", flags: [], returned_line: "r" }))
  check("wave: ten agents run with the size note logged", ten.result.length === 10 && ten.logs.some((l) => l.includes("medium-run guideline")))
  // shell quoting: a label with a quote cannot break out of the command
  const q = await run(wave, [item("P1", { owned: "a b/c'd.rs" })], (p, o) => (o.agentType ? "ok" : { verdict: "clean", flags: [], returned_line: "r" }))
  check("wave: owned paths are shell-quoted", q.calls.some((c) => c.prompt.includes("--owned 'a b/c'\\''d.rs'")))
  const objectForm = await run(wave, { pluginRoot: "/plugin", items: [item("P1", { pluginRoot: undefined })] }, (p, o) => (o.agentType ? "ok" : { verdict: "clean", flags: [], returned_line: "r" }))
  check("wave: object form accepted", objectForm.result[0].verdict === "clean")
}

// ---- phase7-review ------------------------------------------------------------------
const review = readFileSync(join(dir, "phase7-review.js"), "utf8")
const lens = (name: string) => ({ lens: name, label: `r1 ${name}`, prompt: `LENS: ${name}\nSCOPE: src/a.rs`, round: 1, reportDir: "/x/.agent-staging/reviews" })
const doc = "# Change requests — architecture (round 1)\n\n## CR-1: split the cache from the loader\n- Location: src/a.rs:10\n"
const fakeReview = (prompt: string, opts: Opts) => {
  if (opts.agentType) {
    check("review: reviewer agent with a schema", opts.agentType === "agentic-software-development:reviewer" && !!opts.schema)
    if (prompt.startsWith("LENS: style")) return { verdict: "PASS", crs: [], document: "", notes: "clean" }
    if (prompt.startsWith("LENS: performance")) return null
    return {
      verdict: "CHANGES-REQUIRED",
      crs: [{ id: "CR-1", severity: "blocking", location: "src/a.rs:10", claim: "split the cache from the loader" }],
      document: doc,
    }
  }
  check("review: keeper is cheap", opts.model === "haiku")
  return { written: true }
}
{
  const { result, calls } = await run(review, [lens("style"), lens("architecture"), lens("performance")], fakeReview)
  const by = (l: string) => result.find((r: any) => r.lens === l)
  check("review: one entry per lens", result.length === 3)
  check("review: PASS lens writes no document", by("style").verdict === "PASS" && by("style").crs.length === 0 && by("style").document_path === "")
  check("review: CRs are compact with ledger ids", by("architecture").verdict === "CHANGES-REQUIRED" && by("architecture").crs[0].ledger_id === "CR-architecture-1" && by("architecture").crs[0].location === "src/a.rs:10" && by("architecture").crs[0].severity === "blocking")
  check("review: document kept under reportDir", by("architecture").document_path === "/x/.agent-staging/reviews/r1_architecture.md")
  check("review: the verbatim document travels to the keeper", calls.some((c) => c.opts.model === "haiku" && c.prompt.includes(doc)))
  check("review: a dead lens is 'died', never PASS", by("performance").verdict === "died" && by("performance").crs.length === 0)
  check("review: the compact result holds no document text", !JSON.stringify(result).includes("# Change requests"))
  check("review: result shape", result.every((r: any) => ["label", "lens", "round", "verdict", "crs", "answers", "notes", "document_path"].every((k) => k in r)))
  check("review: bad args refused", (await run(review, [{ lens: "style" }], () => null)).result[0].verdict === "died")
}

console.log(fails.length ? "FAIL " + fails.join(" | ") : "PASS (all cases)")
process.exit(fails.length ? 1 : 0)
