// The Claude logins the runner switches between, with their subscription windows. Contract: references/runner.md
// (Several accounts), references/owner-questions.md (Dashboard). Read-only: the runner writes every file read here.
import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

export type Window = { usedPct: number | null; resetsAt: string; status: string }
export type Account = {
  name: string; dir: string; email: string; org: string
  limitedUntil: string            // the runner's own record of a hit limit (accounts-state.json), when still ahead
  next: boolean                   // the account the next stage starts on
  sampledAt: string               // when the windows were last read (a stage's rate_limit_event, or `run_dossier.py usage`)
  fiveHour: Window | null; week: Window | null
  runs: string[]                  // live runs on it: "<repo>.<ID>"
  sameLoginAs?: string            // another registered account logged in with the same email: switching gains nothing
}
/**
 * A limit the runner recorded no longer holds once a later reading of the account says its windows are open (the
 * login changed, or the window reset early): the mark is from before that reading and its windows are "allowed".
 */
export function limitSuperseded(mark: { marked?: string } | undefined, u: any): boolean {
  if (!mark?.marked || !u?.ts || Date.parse(u.ts) <= Date.parse(mark.marked)) return false
  const ws = [u.windows?.five_hour, u.windows?.seven_day].filter(Boolean)
  return ws.length > 0 && ws.every((w: any) => w.status !== "rejected" && !(typeof w.used_percentage === "number" && w.used_percentage >= 100))
}

const home = (): string => process.env.HOME || homedir()
const expand = (p: string): string => (p.startsWith("~/") ? join(home(), p.slice(2)) : p)
const readJson = (p: string): any => { try { return JSON.parse(readFileSync(p, "utf8")) } catch { return null } }
export const accountsFile = (): string => process.env.ASD_ACCOUNTS || join(home(), ".agentic", "accounts.json")
export const accountStateFile = (): string => process.env.ASD_ACCOUNT_STATE || join(home(), ".agentic", "accounts-state.json")
export const usageDir = (): string => process.env.ASD_USAGE_DIR || join(home(), ".agentic", "usage")
const iso = (epochS: unknown): string => (typeof epochS === "number" && epochS > 0 ? new Date(epochS * 1000).toISOString() : "")

/** The login of a config dir: `<dir>/.claude.json`, or `~/.claude.json` for the default `~/.claude`. */
function login(dir: string): { email: string; org: string } {
  const f = dir.replace(/\/$/, "") === join(home(), ".claude") ? join(home(), ".claude.json") : join(dir, ".claude.json")
  const o = readJson(f)?.oauthAccount ?? {}
  return { email: String(o.emailAddress ?? ""), org: String(o.organizationName ?? "") }
}
function windowOf(w: any, now: number): Window | null {
  if (!w || typeof w !== "object") return null
  const resetsAt = iso(w.resets_at)
  // A window whose reset has passed is unknown until the next reading, not still full.
  if (resetsAt && Date.parse(resetsAt) <= now) return { usedPct: null, resetsAt, status: "reset" }
  return { usedPct: typeof w.used_percentage === "number" ? w.used_percentage : null, resetsAt, status: String(w.status ?? "") }
}

/**
 * Every registered account in preference order, the one the next stage starts on (the first not limited; when all are,
 * the one that frees first, as `pick_account` in run_dossier.py), its windows and the live runs on it.
 */
export function readAccounts(liveRuns: { key: string; account?: string }[], now = Date.now()): Account[] {
  const list: { name: string; dir: string }[] = (readJson(accountsFile())?.accounts ?? []).filter((a: any) => a?.name && a?.dir).map((a: any) => ({ name: String(a.name), dir: expand(String(a.dir)) }))
  const accs = list.length ? list : [{ name: "default", dir: join(home(), ".claude") }]
  const st = readJson(accountStateFile()) ?? {}
  const usage = (n: string): any => readJson(join(usageDir(), `${n}.json`)) ?? {}
  const freeAt = (n: string): number => (limitSuperseded(st?.[n], usage(n)) ? 0 : Number(st?.[n]?.limited_until ?? 0) * 1000)
  const usable = accs.find((a) => freeAt(a.name) <= now) ?? [...accs].sort((a, b) => freeAt(a.name) - freeAt(b.name))[0]
  const logins = new Map(accs.map((a) => [a.name, login(a.dir)]))
  return accs.map((a) => {
    const u = usage(a.name)
    const lim = freeAt(a.name)
    const me = logins.get(a.name)!
    const twin = me.email ? accs.find((b) => b.name !== a.name && logins.get(b.name)!.email === me.email)?.name : undefined
    return {
      name: a.name, dir: a.dir, ...me, ...(twin ? { sameLoginAs: twin } : {}), limitedUntil: lim > now ? new Date(lim).toISOString() : "", next: a.name === usable?.name,
      sampledAt: String(u.ts ?? ""), fiveHour: windowOf(u.windows?.five_hour, now), week: windowOf(u.windows?.seven_day, now),
      runs: liveRuns.filter((r) => r.account === a.name).map((r) => r.key),
    }
  })
}
