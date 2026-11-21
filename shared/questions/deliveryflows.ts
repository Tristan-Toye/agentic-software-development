// Delivery flows, per repository: two switches, `stub` and `kanban` (spec: references/owner-questions.md, "Delivery flows").
// The Python side reads the same file; a missing repo or key is stub on, kanban off, and both off reads as stub on.
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join } from "node:path"

export type Flows = { stub: boolean; kanban: boolean }
export type FlowView = { repo: string } & Flows
export const FLOWS = ["stub", "kanban"] as const

export const flowsFile = (): string => process.env.ASD_DELIVERY_FLOWS || join(homedir(), ".agentic", "delivery-flows.json")

function readAll(file: string): Record<string, any> | null {
  if (!existsSync(file)) return {}
  try { const j = JSON.parse(readFileSync(file, "utf8")); return j && typeof j === "object" && !Array.isArray(j) ? j : null } catch { return null }
}

/** The effective switches of a repository: stub unless set to false, kanban only when set to true, never both off. */
export function flowsOf(repo: string, file = flowsFile()): Flows {
  const e = readAll(file)?.[repo]
  const kanban = e?.kanban === true
  return { stub: e?.stub !== false || !kanban, kanban }
}

export const flowViews = (repos: string[], file = flowsFile()): FlowView[] => repos.map((repo) => ({ repo, ...flowsOf(repo, file) }))

/** Turn one flow of one repository on or off. Writes only that repository's entry, atomically; refuses turning both off or touching an unreadable file. */
export function setFlow(repo: string, flow: string, on: boolean, file = flowsFile()): { ok: boolean; error?: string; flows?: Flows } {
  if (!(FLOWS as readonly string[]).includes(flow)) return { ok: false, error: "the flow is stub or kanban" }
  const all = readAll(file)
  if (!all) return { ok: false, error: `${file} is not valid JSON; fix or remove it first` }
  const next: Flows = { ...flowsOf(repo, file), [flow]: on }
  if (!next.stub && !next.kanban) return { ok: false, error: "at least one flow must stay on: turn the other one on first" }
  all[repo] = { ...(all[repo] && typeof all[repo] === "object" ? all[repo] : {}), ...next }
  mkdirSync(dirname(file), { recursive: true })
  const tmp = `${file}.${process.pid}.tmp`
  writeFileSync(tmp, JSON.stringify(all, null, 1))
  renameSync(tmp, file)
  return { ok: true, flows: next }
}
