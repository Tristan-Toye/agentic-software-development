// The owner's standing "Always allow" rulings, kept per repo: `<repo>.json` in ASD_RULINGS_DIR (default
// ~/.agentic/rulings), {repo, rules: [{rule, kind: "allow", by, ts, question, run, tool, target}]}. scripts/run_dossier.py
// reads the same files and passes the rules as `permissions.allow` of every claude session it starts for that repo,
// with a `permissions.deny` for the refused categories (merge, force and protected-branch pushes, secret paths) that
// outranks them. A rule that could cover such a call is refused here (rulingRefused) and dropped there.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { hardBlock } from "../hard-block.ts"

export type Ruling = { rule: string; kind: "allow"; by: string; ts: string; question: string; run: string; tool: string; target: string }
export const rulingsDir = (): string => process.env.ASD_RULINGS_DIR || join(process.env.HOME || homedir(), ".agentic", "rulings")
const fileOf = (repo: string): string => join(rulingsDir(), `${repo.replace(/[^\w.-]/g, "_")}.json`)

const PROBES = ["gh pr merge 1", "gh pr merge 1 --squash --auto", "gh api -X PUT repos/o/r/pulls/1/merge", "gh api graphql -f query=mutation{mergePullRequest}",
  "git push --force", "git push -f origin x", "git push --force-with-lease origin x", "git push --mirror", "git push origin production", "git push origin main",
  "git push origin master", "git push origin development", "git push origin --delete production", "cat ~/.ssh/id_rsa", "cat ~/.aws/credentials", "sops -d secrets.yaml"]
const SECRET_RULE = /\.ssh|\.aws|\.gnupg|id_rsa|id_ed25519|\.env\b|secrets?\.|credentials|keychain/i

/** Why a rule must never be stored or applied ("" when it may): it allows, or could cover, a call in the refused categories. */
export function rulingRefused(rule: string): string {
  const m = rule.trim().match(/^([A-Z][A-Za-z]+)\((.+)\)$/s)
  if (!m) return "not a permission rule"
  const tool = m[1], inner = m[2].trim()
  if (!inner || /^\*+$/.test(inner) || /^\*\s/.test(inner)) return "allows everything"
  const ctx = { home: process.env.HOME || homedir(), cwd: process.cwd() }
  if (tool !== "Bash") return SECRET_RULE.test(inner) || hardBlock(tool, { file_path: inner, path: inner }, ctx) ? "names a secret path" : ""
  if (/^(bash|sh|zsh|python3?|node|bun|npx|npm|uv|pip3?|perl|ruby)\b(\s+\*|\*|$)/.test(inner)) return "a rule for a whole interpreter is dropped in auto mode; name the command"
  const rx = new RegExp("^" + inner.replace(":*", " *").replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*") + "$", "s")
  const hit = PROBES.find((p) => rx.test(p) || rx.test(p + " origin x") || new RegExp("^" + inner.replace(":*", " *").replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*"), "s").test(p))
  if (hit) return "covers a call the runner refuses: " + hit
  return hardBlock("Bash", { command: inner.replace(/\*/g, "x") }, ctx) ? "is a call the runner refuses" : ""
}

export function readRulings(repo: string): Ruling[] {
  try { const d = JSON.parse(readFileSync(fileOf(repo), "utf8")); return Array.isArray(d.rules) ? d.rules : [] } catch { return [] }
}
function write(repo: string, rules: Ruling[]): void {
  mkdirSync(rulingsDir(), { recursive: true })
  writeFileSync(fileOf(repo), JSON.stringify({ repo, rules }, null, 2) + "\n")
}
/** Stores an allow rule for the repo (once per exact rule). Returns "" or why it was refused. */
export function addRuling(repo: string, r: Omit<Ruling, "kind" | "ts"> & { ts?: string }): string {
  const bad = rulingRefused(r.rule); if (bad) return bad
  const rules = readRulings(repo)
  if (!rules.some((x) => x.rule === r.rule)) rules.push({ ...r, kind: "allow", ts: r.ts ?? new Date().toISOString().replace(/\.\d+Z$/, "Z") })
  write(repo, rules)
  return ""
}
/** Removes the exact rule; true when it was stored. */
export function removeRuling(repo: string, rule: string): boolean {
  const rules = readRulings(repo); const left = rules.filter((x) => x.rule !== rule)
  if (left.length === rules.length) return false
  write(repo, left); return true
}
export const hasRulings = (repo: string): boolean => existsSync(fileOf(repo)) && readRulings(repo).length > 0
