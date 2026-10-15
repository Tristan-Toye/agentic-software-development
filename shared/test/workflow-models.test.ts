// The model table in each workflow must match the `claude:` block of every sub-agent file:
// a workflow agent() without `model` would inherit the orchestrator's model.
// Run: bun --no-install shared/test/workflow-models.test.ts
import { readdirSync, readFileSync } from "node:fs"
import { join } from "node:path"

const root = join(import.meta.dir, "..", "..")
const want: Record<string, { model: string; effort: string }> = {}
for (const f of readdirSync(join(root, "sub-agents")).filter((n) => n.endsWith(".md"))) {
  const fm = readFileSync(join(root, "sub-agents", f), "utf8").split("\n---")[0]
  const block = fm.match(/^claude:\n((?: {2}.*\n?)+)/m)?.[1] ?? ""
  const model = block.match(/^ {2}model:\s*(\S+)/m)?.[1]
  const effort = block.match(/^ {2}effort:\s*(\S+)/m)?.[1]
  if (model) want[`agentic-software-development:${f.slice(0, -3)}`] = { model, effort: effort ?? "" }
}
let failed = 0
for (const wf of ["phase4-wave.js", "phase7-review.js"]) {
  const text = readFileSync(join(root, "workflows", wf), "utf8")
  const got: Record<string, { model: string; effort: string }> = {}
  for (const m of text.matchAll(/'(agentic-software-development:[\w-]+)': \{ model: '(\w+)', effort: '(\w*)' \}/g)) {
    got[m[1]] = { model: m[2], effort: m[3] }
  }
  const same = JSON.stringify(Object.entries(got).sort()) === JSON.stringify(Object.entries(want).sort())
  if (!same) {
    failed++
    console.log(`FAIL ${wf}: model table differs from the sub-agent files\n  want ${JSON.stringify(want)}\n  got  ${JSON.stringify(got)}`)
  }
  if (!/agent\(spawnPrompt\(item\)[^)]*\.\.\.modelFor\(/s.test(text)) {
    failed++
    console.log(`FAIL ${wf}: the spawn agent() call does not pin a model`)
  }
  if (Object.values(got).some((v) => !["sonnet", "haiku"].includes(v.model))) {
    failed++
    console.log(`FAIL ${wf}: a workflow agent runs on a model other than sonnet or haiku`)
  }
}
console.log(failed ? `FAIL (${failed})` : "PASS (all cases)")
process.exit(failed ? 1 : 0)
