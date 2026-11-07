// Weekly stubs per repo and the per-repo open-work report. Contract: references/owner-questions.md (Dashboard).
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import type { Question } from "./store.ts"
import { readRunner, readViewCache, runnerActive, writeViewCache } from "./runstore.ts"
import { emitChange } from "./events.ts"
import { answeredBy, baseName, buildLog, getRuns, repoLabel, candidateRoots, listTranscripts, mainOf, parseFrontMatter, pickDossiers, runnerOf, withBranchCopies, questionInRepo, sh, worktreesOf, type Pr, type QA } from "./runs.ts"

export type StubDossier = { id: string; title: string; status: string; repo: string; via: "merged"; answered: QA[] }
/** An in-progress dossier shown under a stub; `noPr` = no PR yet, parked on the accepting stub. */
export type StubMember = { id: string; noPr: boolean }
export type Stub = {
  branch: string; cut: string; state: "accepting" | "integrating" | "merged"; stage: string
  refAge: string; pr: Pr | null; dossiers: StubDossier[]; members: StubMember[]
}
export type RepoStubs = { repo: string; label: string; main: string; stubs: Stub[]; open: string[] }
/** The stubs that are not merged (accepting or integrating), newest first. */
export function openStubs(stubs: Stub[]): Stub[] {
  return stubs.filter((s) => s.state !== "merged").sort((a, b) => b.cut.localeCompare(a.cut) || b.refAge.localeCompare(a.refAge) || b.branch.localeCompare(a.branch))
}
/** An open PR whose base is a stub branch. */
export type BasePr = { number: number; headRefName: string }
/**
 * Which in-progress dossiers belong under which open stub. A dossier belongs to a stub when an open PR into
 * that stub is its own (the `pr:` number, or its id in the head branch); `plan/...` heads never count. A dossier
 * in building/review (or with a live runner) that has no `pr:` and no PR into any stub goes under the accepting
 * stub (the newest open stub with no PR into development; else the newest open stub) with `noPr`. A done
 * dossier appears only through its open PR into the stub; ready, planned and dropped dossiers never appear. Returns branch -> members; stubs with none are omitted.
 */
export function stubMembers(
  stubs: { branch: string; cut: string; refAge?: string; state: Stub["state"]; pr: unknown | null }[],
  dossiers: { id: string; status: string; pr?: string; live?: boolean; halted?: boolean }[],
  prsByBase: Record<string, BasePr[]>,
): Record<string, StubMember[]> {
  const out: Record<string, StubMember[]> = {}
  const open = openStubs(stubs.map((s) => ({ ...s, refAge: s.refAge ?? "", stage: "", dossiers: [], members: [], pr: s.pr as Pr | null })))
  const add = (b: string, m: StubMember) => { (out[b] ??= []); if (!out[b].some((x) => x.id === m.id)) out[b].push(m) }
  const eligible = dossiers.filter((d) => ["building", "review", "pr"].includes(d.status) || d.live === true)
  // A finished build stays under its stub while its PR into the stub is open.
  const withPr = dossiers.filter((d) => ["building", "review", "pr", "done"].includes(d.status) || d.live === true)
  const placed = new Set<string>()
  for (const b of open.map((s) => s.branch)) {
    for (const p of prsByBase[b] ?? []) {
      if (/^plan\//.test(p.headRefName)) continue
      // Head branches name the id in any case: feat/w-130-… is W-130's.
      const hit = withPr.find((d) => prFieldNumber(d.pr) === p.number || new RegExp(`(^|[^A-Za-z0-9])${d.id.replace(/[^\w-]/g, "")}(?![\w])`, "i").test(p.headRefName))
      if (hit) { add(b, { id: hit.id, noPr: false }); placed.add(hit.id) }
    }
  }
  const accepting = open.find((s) => s.pr === null) ?? open[0]
  if (accepting) for (const d of eligible) {
    if (placed.has(d.id) || prFieldNumber(d.pr) !== null) continue
    // Without a PR a dossier belongs to the stub only while work on it is under way:
    // a runner that was stopped or failed takes it off until it resumes.
    if (d.live || (!d.halted && (d.status === "building" || d.status === "review"))) add(accepting.branch, { id: d.id, noPr: true })
  }
  return out
}
export type StubsView = { scannedAt: string; repos: RepoStubs[]; openWork: { repo: string; updated: string }[] }

// ---- pure parsers ----
/** The PR number a first-parent merge subject stands for, or null. */
export function prNumberOf(subject: string): number | null {
  const m = subject.match(/^Merge pull request #(\d+)\b/) ?? subject.match(/\(#(\d+)\)\s*$/) ?? subject.match(/^Merge .*#(\d+)\b/)
  return m ? Number(m[1]) : null
}
/** The number in a dossier's `pr:` field (`123`, `#123`, a PR URL), or null. */
export function prFieldNumber(v: string | undefined): number | null {
  const m = (v ?? "").match(/(\d+)\s*$/)
  return m ? Number(m[1]) : null
}
/** Dossier ids for merge subjects: each subject's PR number looked up in `prToDossier`. */
export function dossiersOnMerges(subjects: string[], prToDossier: Map<number, string>, known?: Set<string>): string[] {
  const ids: string[] = []
  for (const s of subjects) {
    // A plan merged into the stub is not an implementation on it.
    if (/\bfrom \S+?\/plan\//.test(s)) continue
    const n = prNumberOf(s)
    let id = n === null ? undefined : prToDossier.get(n)
    // A fix or plan branch names its dossier: "Merge pull request #404 from o/fix/W-122-stub…".
    if (!id && known) id = (s.match(/\bfrom \S+?\/(\S+)/)?.[1].match(/\b[A-Z][A-Z0-9]*-\d+\b/g) ?? []).find((k) => known.has(k))
    if (id && !ids.includes(id)) ids.push(id)
  }
  return ids
}
/** The last `## Step N — name` / `## Round …` / `### Round …` heading of a stub record, or "". */
export function stubStage(text: string): string {
  let last = ""
  for (const m of text.matchAll(/^#{2,3}\s+((?:Step\s+\d+|Round\b).*?)\s*$/gm)) last = m[1]
  return last
}
export function stubState(pr: { state: string } | null): Stub["state"] {
  if (!pr) return "accepting"
  const s = pr.state.toUpperCase()
  return s === "MERGED" ? "merged" : s === "OPEN" ? "integrating" : "accepting"
}
export const stubDate = (branch: string): string => branch.match(/\d{4}-\d{2}-\d{2}/)?.[0] ?? ""
export function summarizeChecks(rollup: any[]): { green: number; red: number; pending: number } {
  let green = 0, red = 0, pending = 0
  for (const k of rollup ?? []) {
    const v = String(k.conclusion || k.state || k.status || "").toUpperCase()
    if (["SUCCESS", "NEUTRAL", "SKIPPED"].includes(v)) green++
    else if (["FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"].includes(v)) red++
    else pending++
  }
  return { green, red, pending }
}

// ---- git / gh, cached 2 min ----
const TTL = 120_000
const memo = new Map<string, { at: number; v: any }>()
async function cached<T>(key: string, fn: () => Promise<T>): Promise<T> {
  const c = memo.get(key)
  if (c && Date.now() - c.at < TTL) return c.v
  const v = await fn()
  memo.set(key, { at: Date.now(), v })
  return v
}
const noGit = () => process.env.ASD_STUBS_NO_GIT === "1"
const noGh = () => process.env.ASD_RUNS_NO_GH === "1"

async function gh(main: string, args: string[]): Promise<any[] | null> {
  if (noGh()) return null
  try { const j = JSON.parse(await sh(["gh", ...args], main, 10_000)); return Array.isArray(j) ? j : null } catch { return null }
}
/**
 * Refresh the stub, release and development remote-tracking refs of a main checkout, at most every
 * `ASD_STUB_FETCH_SECS` (default 180; 0 turns it off), so a merge into a stub reaches the card without any other
 * fetch. Only `refs/remotes/origin/...` move: no branch, worktree or index is touched. Auto gc is off for the call.
 * A moved ref drops the cached merge lists, so the next scan reads them again.
 */
const lastFetch = new Map<string, number>()
export async function fetchStubRefs(main: string, now = Date.now()): Promise<boolean> {
  const every = Number(process.env.ASD_STUB_FETCH_SECS ?? 180) * 1000
  if (!every || noGit() || now - (lastFetch.get(main) ?? 0) < every) return false
  lastFetch.set(main, now)
  const before = await sh(["git", "-C", main, "for-each-ref", "--format=%(objectname)", "refs/remotes/origin"], undefined, 5000)
  await sh(["git", "-C", main, "-c", "gc.auto=0", "fetch", "--quiet", "--prune", "--no-tags", "origin",
    "+refs/heads/stub/*:refs/remotes/origin/stub/*", "+refs/heads/release/*:refs/remotes/origin/release/*",
    "+refs/heads/development:refs/remotes/origin/development"], undefined, 60_000)
  const after = await sh(["git", "-C", main, "for-each-ref", "--format=%(objectname)", "refs/remotes/origin"], undefined, 5000)
  if (before === after) return false
  for (const k of [...memo.keys()]) if (k.startsWith(`mg:${main}:`) || k.startsWith(`rd:${main}:`) || k.startsWith(`ls:${main}`)) memo.delete(k)
  return true
}
async function stubBranches(main: string): Promise<string[]> {
  return cached(`ls:${main}`, async () => {
    const out = await sh(["git", "-C", main, "ls-remote", "--heads", "origin", "stub/*", "release/*"], undefined, 10_000)
    return out.split("\n").map((l) => l.split("\t")[1]?.replace(/^refs\/heads\//, "")).filter((b): b is string => !!b)
  })
}
async function prInto(main: string, branch: string): Promise<Pr | null> {
  return cached(`pr:${main}:${branch}`, async () => {
    const l = await gh(main, ["pr", "list", "--head", branch, "--base", "development", "--state", "all", "--json", "number,state,url,statusCheckRollup"])
    const j = l?.sort((a, b) => b.number - a.number)[0]
    return j ? { number: j.number, url: j.url, state: j.state, ...summarizeChecks(j.statusCheckRollup) } : null
  })
}
async function prsBase(main: string, branch: string): Promise<BasePr[]> {
  return cached(`base:${main}:${branch}`, async () => {
    const l = await gh(main, ["pr", "list", "--base", branch, "--state", "open", "--json", "number,headRefName"])
    return (l ?? []).map((x) => ({ number: Number(x.number), headRefName: String(x.headRefName ?? "") }))
  })
}
async function mergeSubjects(main: string, branch: string): Promise<string[]> {
  return (await sh(["git", "-C", main, "log", "--merges", "--first-parent", "--format=%s", `origin/development..origin/${branch}`], undefined, 10_000)).split("\n").filter(Boolean)
}
async function refDate(main: string, branch: string): Promise<string> {
  return (await sh(["git", "-C", main, "log", "-1", "--format=%cI", `origin/${branch}`], undefined, 5000)).trim()
}
/** The stub's record file: `stub-<date>.md` for `stub/<date>`, `stub-<date>-<branch slug>.md` for any branch. */
export function recordFile(main: string, branch: string): string | null {
  const dir = join(main, ".discovery", "analysis")
  const date = stubDate(branch), slug = branch.replace(/\//g, "-")
  try {
    const f = readdirSync(dir).filter((n) => n.startsWith("stub-") && n.endsWith(".md") &&
      (n.endsWith(`-${slug}.md`) || (branch.startsWith("stub/") && date !== "" && n === `stub-${date}.md`))).sort().pop()
    return f ? join(dir, f) : null
  } catch { return null }
}
export function recordStage(main: string, branch: string): string {
  const f = recordFile(main, branch)
  try { return f ? stubStage(readFileSync(f, "utf8")) : "" } catch { return "" }
}
const recordDate = (f: string | null) => (f ? f.match(/stub-(\d{4}-\d{2}-\d{2})/)?.[1] ?? "" : "")

// ---- assemble ----
type Doc = ReturnType<typeof pickDossiers>[number]
export async function buildStubs(questions: Question[], now = Date.now()): Promise<StubsView> {
  const roots = await candidateRoots(questions, listTranscripts())
  const byMain = new Map<string, string[]>()
  for (const wt of roots) {
    const m = await mainOf(wt)
    byMain.set(m, [...(byMain.get(m) ?? []), wt])
  }
  const repos: RepoStubs[] = []
  for (const [main, wts] of byMain) {
    const ans = answeredBy(questions, (q) => questionInRepo(q, baseName(main), main))
    if (noGit() || !existsSync(join(main, ".git"))) continue
    await fetchStubRefs(main)
    const docs: Doc[] = await withBranchCopies(main, pickDossiers(wts))
    const byId = new Map(docs.map((d) => [d.front.id!, d]))
    const prToDossier = new Map<number, string>()
    for (const d of docs) { const n = prFieldNumber(d.front.pr); if (n !== null) prToDossier.set(n, d.front.id!) }
    const mk = (id: string, via: StubDossier["via"]): StubDossier => {
      const d = byId.get(id)
      return { id, title: d?.front.title ?? "", status: d?.front.status ?? "", repo: baseName(main), via, answered: ans.get(id) ?? [] }
    }
    const stubs: Stub[] = []
    // A stub is a `stub/*` branch, or another branch /stub integrated (it has a record file).
    for (const branch of (await stubBranches(main)).filter((b) => b.startsWith("stub/") || recordFile(main, b) !== null)) {
      const [pr, subjects, date] = await Promise.all([prInto(main, branch), cached(`mg:${main}:${branch}`, () => mergeSubjects(main, branch)), cached(`rd:${main}:${branch}`, () => refDate(main, branch))])
      const ids = dossiersOnMerges(subjects, prToDossier, new Set(byId.keys()))
      const dossiers = ids.map((id) => mk(id, "merged"))
      const state = stubState(pr)
      stubs.push({
        branch, cut: stubDate(branch) || recordDate(recordFile(main, branch)) || date.slice(0, 10), state, stage: state === "integrating" ? recordStage(main, branch) : "",
        refAge: date, pr, dossiers, members: [],
      })
    }
    if (stubs.length) {
      const prsByBase: Record<string, BasePr[]> = {}
      for (const s of openStubs(stubs)) prsByBase[s.branch] = await prsBase(main, s.branch)
      const name = baseName(main)
      const mem = stubMembers(stubs, docs.map((d) => ({ id: d.front.id!, status: d.front.status ?? "", pr: d.front.pr, live: runnerActive(runnerOf(name, d.front.id!, buildLog(d.text)).runner), halted: ["stopped", "failed"].includes(runnerOf(name, d.front.id!, buildLog(d.text)).runner?.state ?? "") })), prsByBase)
      for (const s of stubs) s.members = mem[s.branch] ?? []
    }
    if (stubs.length) { const sorted = stubs.sort((a, b) => b.cut.localeCompare(a.cut) || b.branch.localeCompare(a.branch)); repos.push({ repo: baseName(main), label: repoLabel(main), main, stubs: sorted, open: openStubs(sorted).map((s) => s.branch) }) }
  }
  repos.sort((a, b) => a.repo.localeCompare(b.repo))
  return { scannedAt: new Date(now).toISOString(), repos, openWork: [] }
}

// ---- open work ----
export const openWorkDir = (): string => process.env.ASD_OPEN_WORK_DIR || join(homedir(), ".agentic", "open-work")
const pluginRoot = join(import.meta.dir, "..", "..")
const GEN = join(pluginRoot, "scripts", "generate_open_work.py")
const lastRun = new Map<string, { at: number; sig: string; inflight: boolean }>()
export const safeRepo = (r: string): boolean => /^[\w.-]+$/.test(r)

/** Dossier set + mtimes of every worktree of a repo. */
export function dossierSignature(wts: string[]): string {
  const parts: string[] = []
  for (const wt of wts) {
    const dir = join(wt, ".discovery", "dossiers")
    try { for (const n of readdirSync(dir).sort()) parts.push(`${wt}/${n}:${statSync(join(dir, n)).mtimeMs}`) } catch { /* none */ }
  }
  return parts.join("|")
}
async function runGen(root: string, out: string, embed: boolean, linkBase = "", controlsFile = ""): Promise<boolean> {
  try {
    const p = Bun.spawn(["python3", GEN, "--root", root, "--worktrees", "--out", out, "--quiet", ...(embed ? ["--embed", "--simplify-default"] : []), ...(linkBase ? ["--link-base", linkBase] : []), ...(controlsFile ? ["--run-controls", controlsFile] : [])], { stdout: "ignore", stderr: "ignore", stdin: "ignore" })
    const t = setTimeout(() => p.kill(), 120_000)
    const code = await p.exited
    clearTimeout(t)
    return code === 0
  } catch { return false }
}
/** Regenerate `<out>/<repo>.html` when the dossiers changed, at most once per 30 s per repo. `--embed` is dropped when the script rejects it. */
export async function refreshOpenWork(repo: string, root: string, wts: string[], now = Date.now(), controls: Record<string, string> = {}, refsSig = ""): Promise<void> {
  const out = join(openWorkDir(), `${repo}.html`)
  // The ▶ in the report follows the runner state, so a run that stops or starts regenerates it.
  const sig = dossierSignature(wts) + "#" + JSON.stringify(controls) + "#" + refsSig
  const st = lastRun.get(repo)
  if (st && (st.inflight || (st.sig === sig && existsSync(out)) || now - st.at < 30_000)) return
  mkdirSync(openWorkDir(), { recursive: true })
  const rec = { at: now, sig, inflight: true }
  lastRun.set(repo, rec)
  const cf = join(openWorkDir(), `${repo}.controls.json`)
  writeFileSync(cf, JSON.stringify(controls))
  try { if (!(await runGen(root, out, true, `/repo/${repo}`, cf))) await runGen(root, out, false) } finally { rec.inflight = false; rec.at = Date.now() }
}

// A restart serves the last view from disk at once; the first scan replaces it (readViewCache).
let cachedView: StubsView = readViewCache<StubsView>("stubs") ?? { scannedAt: "", repos: [], openWork: [] }
let inflight: Promise<void> | null = null
let lastScan = 0
let lastSig = ""
async function rescan(questions: Question[]): Promise<void> {
  const view = await buildStubs(questions)
  const roots = await candidateRoots(questions, listTranscripts())
  const byMain = new Map<string, string[]>()
  for (const wt of roots) { const m = await mainOf(wt); byMain.set(m, [...(byMain.get(m) ?? []), wt]) }
  const openWork: StubsView["openWork"] = []
  for (const [main, wts] of byMain) {
    const repo = baseName(main)
    if (!safeRepo(repo)) continue
    const root = existsSync(join(main, ".discovery")) ? join(main, ".discovery") : join(wts[0], ".discovery")
    const runs = (await getRuns(() => questions)).runs.filter((r) => r.repo === repo)
    const controls: Record<string, string> = {}
    for (const r of runs) {
      if (r.controls.start.show && r.controls.start.ok) controls[r.id] = "start"
      else if (r.controls.resume.show && r.controls.resume.ok) controls[r.id] = "resume"
    }
    // A finished build's copy lives on its branch (the report overlays it), so a moved ref regenerates the report too.
    const refs = await sh(["git", "-C", main, "for-each-ref", "--format=%(objectname)", "refs/heads", "refs/remotes/origin"], undefined, 5000)
    await refreshOpenWork(repo, root, wts, Date.now(), controls, String(Bun.hash(refs)))
    try { openWork.push({ repo, updated: new Date(statSync(join(openWorkDir(), `${repo}.html`)).mtimeMs).toISOString() }) } catch { /* not generated */ }
  }
  const next = { ...view, openWork: openWork.sort((a, b) => a.repo.localeCompare(b.repo)) }
  const sig = JSON.stringify([next.repos.map((r) => [r.repo, r.stubs.map((s) => [s.branch, s.state, s.stage, s.pr, s.dossiers.map((d) => d.id), s.members])]), next.openWork])
  cachedView = next
  writeViewCache("stubs", next)
  if (sig !== lastSig) { lastSig = sig; emitChange("stubs") }
  emitChange("scan")
}
/** A run started or resumed from the dashboard: the next scan runs at once and regenerates the report, so a reload shows no stale ▶. */
export function refreshAfterControl(repo: string): void {
  lastRun.delete(repo)
  lastScan = 0
}
/** Cached view; a rescan starts in the background when older than 30 s. Answered Q&A is refreshed on every call. */
export async function getStubs(questions: () => Question[]): Promise<StubsView> {
  if (!inflight && Date.now() - lastScan > 30_000) {
    lastScan = Date.now()
    inflight = rescan(questions()).catch(() => {}).finally(() => { inflight = null })
  }
  if (!cachedView.scannedAt && inflight) await inflight
  const qs = questions()
  return { ...cachedView, repos: cachedView.repos.map((r) => { const ans = answeredBy(qs, (q) => questionInRepo(q, r.repo, r.main)); return { ...r, stubs: r.stubs.map((s) => ({ ...s, dossiers: s.dossiers.map((d) => ({ ...d, answered: ans.get(d.id) ?? [] })) })) } }) }
}
/** The generated report for a repo; generated on demand when missing. */
export async function openWorkHtml(repo: string, questions: Question[]): Promise<string | null> {
  if (!safeRepo(repo)) return null
  const f = join(openWorkDir(), `${repo}.html`)
  if (!existsSync(f)) await getStubs(() => questions)
  try { return readFileSync(f, "utf8") } catch { return null }
}
