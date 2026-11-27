// Owner-questions store: one append-only <id>.jsonl per question (see references/owner-questions.md).
import { closeSync, existsSync, mkdirSync, openSync, readdirSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync, writeSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { readdir, rename, unlink, writeFile } from "node:fs/promises"
import { fileSig, IO_CONCURRENCY, mapLimit, readText } from "./asyncio.ts"

export type Line = {
  ts: string
  from: "agent" | "owner" | "runner"
  kind: "question" | "reply" | "followup" | "answer"
  text: string
  via?: string
  [k: string]: unknown
}
export type Option = { key: string; label: string; consequence?: string }
export type Question = {
  id: string
  state: "open" | "answered"
  repo: string
  dossier: string
  root: string
  text: string
  options: Option[]
  recommendation: string
  blocks: string
  // What the owner is asked to decide, from `ask_owner.py ask --kind`: design, product or uncertainty; "" when not given.
  qkind: string
  // Set by `ask_owner.py ask --topic visual-review`: the one question the dashboard review canvas answers; "" otherwise.
  topic: string
  ts: string
  answer: Line | null
  lines: Line[]
}

export const MAX_TEXT = 3500

export function storeDir(): string {
  const dir = process.env.ASD_QUESTIONS_DIR || join(homedir(), ".agentic", "questions")
  mkdirSync(dir, { recursive: true })
  return dir
}

/** Parse the text of one question file. The id comes from the file name, never from the content. */
export function parseQuestion(id: string, content: string): Question | null {
  const lines: Line[] = []
  for (const raw of content.split("\n")) {
    if (!raw.trim()) continue
    try {
      const o = JSON.parse(raw)
      if (o && typeof o === "object" && typeof o.kind === "string") lines.push(o as Line)
    } catch {
      // a torn or foreign line is skipped
    }
  }
  const q = lines.find((l) => l.kind === "question")
  if (!q) return null
  // An option button is the owner's answer; a typed message stays a follow-up until the agent accepts it as one.
  const answer = lines.find((l) => l.kind === "answer" && (l.from === "owner" || l.accepted === true)) ?? null
  const str = (v: unknown) => (typeof v === "string" ? v : "")
  return {
    id,
    state: answer ? "answered" : "open",
    repo: str(q.repo),
    dossier: str(q.dossier),
    root: str(q.root),
    text: str(q.text),
    // The question's buttons, then any the agent added in its replies (`ask_owner.py reply --option`), new keys only.
    options: (() => {
      const out: Option[] = Array.isArray(q.options) ? [...(q.options as Option[])] : []
      for (const l of lines) if (l.kind === "reply" && Array.isArray((l as any).options)) for (const o of (l as any).options as Option[]) if (o && o.key && !out.some((x) => x.key === o.key)) out.push(o)
      return out
    })(),
    // The latest reply that names a recommendation moves it (ask_owner.py reply --recommend).
    recommendation: str([...lines].reverse().find((l) => l.kind === "reply" && typeof (l as any).recommend === "string")?.["recommend" as keyof Line] ?? q.recommendation),
    blocks: str(q.blocks),
    qkind: str(q.qkind),
    topic: str(q.topic),
    ts: str(q.ts),
    answer,
    lines,
  }
}

export function readQuestion(dir: string, id: string): Question | null {
  if (!/^[\w.-]+$/.test(id)) return null
  try {
    return parseQuestion(id, readFileSync(join(dir, `${id}.jsonl`), "utf8"))
  } catch {
    return null
  }
}

export function readAll(dir: string): Question[] {
  const out: Question[] = []
  for (const f of readdirSync(dir)) {
    if (!f.endsWith(".jsonl")) continue
    const q = readQuestion(dir, f.slice(0, -".jsonl".length))
    if (q) out.push(q)
  }
  return out.sort((a, b) => a.ts.localeCompare(b.ts) || a.id.localeCompare(b.id))
}

/** Parsed questions by path with the file signature they were parsed at: an unchanged file is not read again. */
const qcache = new Map<string, { sig: string; q: Question | null }>()

export async function readQuestionAsync(dir: string, id: string): Promise<Question | null> {
  if (!/^[\w.-]+$/.test(id)) return null
  const path = join(dir, `${id}.jsonl`)
  const sig = await fileSig(path)
  if (sig === null) { qcache.delete(path); return null }
  const hit = qcache.get(path)
  if (hit && hit.sig === sig) return hit.q
  const text = await readText(path)
  const q = text === null ? null : (() => { try { return parseQuestion(id, text) } catch { return null } })()
  if (text !== null) qcache.set(path, { sig, q })
  return q
}

/**
 * The open-question index: `<dir>/open-index.json` = `{ v: 1, open: [id...], closed: [id...] }`, the ids by state, so a
 * list call reads the few open questions and not every file in the folder. Writers (appendOwner here, ask_owner.py and
 * auto_work.py in other processes) update it under `<dir>/open-index.lock` (create-exclusive, stale after 10 s), writing
 * a temp file and renaming it. The index is a hint and never the truth: the folder listing is. An id in the folder that
 * the index does not list (a writer that bypassed it, a file written by hand) is read once and classified; a missing or
 * corrupt index is rebuilt from a full read; a background sweep every 60 s re-reads every question, so a wrong entry
 * lives at most that long. A question is hidden only when the index calls it closed while its file says open, and the
 * sweep fixes that. A closed (answered) question is final (AnsweredError), so it is kept in memory without a stat.
 */
export const INDEX_FILE = "open-index.json"
export const LOCK_FILE = "open-index.lock"
export const SWEEP_MS = 60_000
const LOCK_STALE_MS = 10_000
type Index = { v: 1; open: string[]; closed: string[] }

function parseIndex(text: string | null): Index | null {
  if (text === null) return null
  try {
    const o = JSON.parse(text)
    const ok = (a: unknown) => Array.isArray(a) && a.every((x) => typeof x === "string")
    return o && o.v === 1 && ok(o.open) && ok(o.closed) ? { v: 1, open: o.open, closed: o.closed } : null
  } catch { return null }
}

/** `idx` with `id` moved to the list of `state`; every other id untouched. */
function moved(idx: Index, id: string, state: "open" | "closed"): Index {
  const open = idx.open.filter((x) => x !== id), closed = idx.closed.filter((x) => x !== id)
  ;(state === "open" ? open : closed).push(id)
  return { v: 1, open, closed }
}

function tryLock(dir: string): boolean {
  const lp = join(dir, LOCK_FILE)
  try { closeSync(openSync(lp, "wx")); return true } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "EEXIST") {
      try { if (Date.now() - statSync(lp).mtimeMs > LOCK_STALE_MS) unlinkSync(lp) } catch { /* another writer took it */ }
    }
    return false
  }
}
const unlock = (dir: string) => { try { unlinkSync(join(dir, LOCK_FILE)) } catch { /* gone */ } }

/**
 * Record that question `id` is `state` in the index, for the writers that run in their own process (sync, short).
 * Best effort: a missing or unreadable index is left for the server to rebuild, and a lock not won in 2 s is skipped;
 * both are caught by the folder check, so a missed note never hides a question.
 */
export function noteQuestion(dir: string, id: string, state: "open" | "closed"): void {
  const path = join(dir, INDEX_FILE)
  if (!existsSync(path)) return
  const until = Date.now() + 2000
  const nap = new Int32Array(new SharedArrayBuffer(4))
  while (!tryLock(dir)) {
    if (Date.now() > until) return
    Atomics.wait(nap, 0, 0, 5)
  }
  try {
    const idx = parseIndex((() => { try { return readFileSync(path, "utf8") } catch { return null } })())
    if (!idx) return
    const tmp = `${path}.${process.pid}.tmp`
    writeFileSync(tmp, JSON.stringify(moved(idx, id, state)))
    renameSync(tmp, path)
  } catch { /* the rebuild covers it */ } finally { unlock(dir) }
}

/** Apply `change` to the index file under the lock (async, for the server); `null` from `change` leaves it alone. */
async function updateIndex(dir: string, change: (cur: Index | null) => Index | null): Promise<void> {
  const path = join(dir, INDEX_FILE)
  const until = Date.now() + 2000
  while (!tryLock(dir)) {
    if (Date.now() > until) return
    await new Promise((r) => setTimeout(r, 5))
  }
  try {
    const next = change(parseIndex(await readText(path)))
    if (!next) return
    const tmp = `${path}.${process.pid}.tmp`
    await writeFile(tmp, JSON.stringify(next))
    await rename(tmp, path)
  } catch { /* the next call retries */ } finally { unlock(dir) }
}

type DirState = { open: Set<string>; closed: Set<string>; retired: Map<string, Question>; sig: string | null | undefined; sweptAt: number; sweeping: boolean }
const dirStates = new Map<string, DirState>()
const stateOf = (dir: string): DirState => {
  let s = dirStates.get(dir)
  if (!s) dirStates.set(dir, (s = { open: new Set(), closed: new Set(), retired: new Map(), sig: undefined, sweptAt: Date.now(), sweeping: false }))
  return s
}

/**
 * Bring the in-memory view of `dir` up to date and return the open questions (and the answered ones when `withClosed`).
 * Per call: one folder listing, one stat of the index, and a stat plus read of the open questions only.
 */
async function syncDir(dir: string, withClosed: boolean): Promise<{ open: Question[]; closed: Question[] }> {
  const st = stateOf(dir)
  const ids = new Set((await readdir(dir)).filter((f) => f.endsWith(".jsonl")).map((f) => f.slice(0, -".jsonl".length)))
  const indexPath = join(dir, INDEX_FILE)
  const sig = await fileSig(indexPath)
  let rebuild = false
  if (sig !== st.sig) {
    const idx = parseIndex(await readText(indexPath))
    if (idx) { st.open = new Set(idx.open); st.closed = new Set(idx.closed) } else { st.open.clear(); st.closed.clear(); rebuild = true }
    st.sig = sig
  }
  const toOpen = new Set<string>(), toClosed = new Set<string>(), gone = new Set<string>()
  for (const id of [...st.open, ...st.closed]) if (!ids.has(id)) { gone.add(id); st.open.delete(id); st.closed.delete(id); st.retired.delete(id) }
  const cand = [...ids].filter((id) => !st.closed.has(id))
  const qs = await mapLimit(cand, IO_CONCURRENCY, (id) => readQuestionAsync(dir, id))
  const open: Question[] = []
  cand.forEach((id, i) => {
    const q = qs[i]
    if (!q) return
    if (q.state === "answered") {
      if (!st.closed.has(id)) toClosed.add(id)
      st.open.delete(id); st.closed.add(id); st.retired.set(id, q)
    } else {
      if (!st.open.has(id)) toOpen.add(id)
      st.open.add(id); open.push(q)
    }
  })
  const closed: Question[] = []
  if (withClosed) {
    const missing = [...st.closed].filter((id) => !st.retired.has(id))
    const rs = await mapLimit(missing, IO_CONCURRENCY, (id) => readQuestionAsync(dir, id))
    missing.forEach((id, i) => { const q = rs[i]; if (q) st.retired.set(id, q) })
    for (const id of st.closed) { const q = st.retired.get(id); if (q) closed.push(q) }
  }
  if (rebuild || toOpen.size || toClosed.size || gone.size) {
    await updateIndex(dir, (cur) => {
      if (!cur || rebuild) return { v: 1, open: [...st.open], closed: [...st.closed] }
      let next = cur
      for (const id of gone) next = { v: 1, open: next.open.filter((x) => x !== id), closed: next.closed.filter((x) => x !== id) }
      for (const id of toOpen) next = moved(next, id, "open")
      for (const id of toClosed) next = moved(next, id, "closed")
      return next
    })
    st.sig = await fileSig(indexPath)
  }
  if (Date.now() - st.sweptAt > SWEEP_MS && !st.sweeping) void sweep(dir, st)
  return { open, closed }
}

/** Background check of every question against its file: catches a wrong index entry and a line added to an answered question. */
async function sweep(dir: string, st: DirState): Promise<void> {
  st.sweeping = true
  try {
    const ids = [...st.closed]
    const qs = await mapLimit(ids, IO_CONCURRENCY, (id) => readQuestionAsync(dir, id))
    const reopened: string[] = []
    ids.forEach((id, i) => {
      const q = qs[i]
      if (!q) return
      if (q.state === "open") { st.closed.delete(id); st.retired.delete(id); st.open.add(id); reopened.push(id) } else st.retired.set(id, q)
    })
    if (reopened.length) {
      await updateIndex(dir, (cur) => { let next = cur ?? { v: 1 as const, open: [], closed: [] }; for (const id of reopened) next = moved(next, id, "open"); return next })
      st.sig = await fileSig(join(dir, INDEX_FILE))
    }
  } catch { /* the next sweep retries */ } finally { st.sweptAt = Date.now(); st.sweeping = false }
}

/**
 * Every question, off the main thread. One page load asks for the list several times at once; callers that arrive
 * while a read of the same folder is in flight share it. `appendOwner` drops the shared read: a caller after an owner
 * line never gets a list that began before it. Answered questions come from memory (see the index note above).
 */
const listing = new Map<string, Promise<Question[]>>()
const openListing = new Map<string, Promise<Question[]>>()
const byTime = (a: Question, b: Question) => a.ts.localeCompare(b.ts) || a.id.localeCompare(b.id)

function shared(map: Map<string, Promise<Question[]>>, dir: string, read: () => Promise<Question[]>): Promise<Question[]> {
  let p = map.get(dir)
  if (!p) {
    p = read().finally(() => { if (map.get(dir) === p) map.delete(dir) })
    map.set(dir, p)
  }
  return p
}

export async function readAllAsync(dir: string): Promise<Question[]> {
  return [...(await shared(listing, dir, async () => { const r = await syncDir(dir, true); return [...r.open, ...r.closed].sort(byTime) }))]
}

/** The open questions only: the hot path of the dashboard's polls reads no answered question. */
export async function readOpenAsync(dir: string): Promise<Question[]> {
  return [...(await shared(openListing, dir, async () => (await syncDir(dir, false)).open.sort(byTime)))]
}

export async function readSidecarAsync(dir: string, id: string): Promise<Sidecar | null> {
  const text = await readText(join(dir, `${id}.slack.json`))
  if (text === null) return null
  try {
    const o = JSON.parse(text)
    return o && typeof o.channel === "string" && typeof o.ts === "string" ? o : null
  } catch { return null }
}

export function cut(text: string): string {
  return text.length > MAX_TEXT ? text.slice(0, MAX_TEXT) + " [cut]" : text
}

export class AnsweredError extends Error {}

/**
 * Append one owner line with a single O_APPEND write. The first answer is final:
 * a later answer (or follow-up) on an answered question throws AnsweredError.
 */
export function appendOwner(
  dir: string,
  id: string,
  m: { kind: "followup" | "answer"; text: string; option?: string; via: "page" | "slack" | "cli"; anchor?: { diagram: string; element: string; graph_hash?: string }; review?: boolean },
): Question {
  const q = readQuestion(dir, id)
  if (!q) throw new Error(`no such question: ${id}`)
  if (q.state === "answered") throw new AnsweredError(`${id} is already answered`)
  const line: Line = { ts: new Date().toISOString(), from: "owner", kind: m.kind, text: cut(m.text), via: m.via }
  if (m.kind === "answer") line.option = m.option ?? ""
  // VR-04: a line about one diagram element carries its anchor; the batch answer of a visual review is marked so Slack does not repeat it.
  if (m.anchor) line.anchor = m.anchor
  if (m.review) line.review = true
  listing.delete(dir); openListing.delete(dir)
  const fd = openSync(join(dir, `${id}.jsonl`), "a")
  try {
    writeSync(fd, JSON.stringify(line) + "\n")
  } finally {
    closeSync(fd)
  }
  noteQuestion(dir, id, m.kind === "answer" ? "closed" : "open")
  return readQuestion(dir, id)!
}

export type Sidecar = { channel: string; ts: string }

export function readSidecar(dir: string, id: string): Sidecar | null {
  try {
    const o = JSON.parse(readFileSync(join(dir, `${id}.slack.json`), "utf8"))
    return o && typeof o.channel === "string" && typeof o.ts === "string" ? o : null
  } catch {
    return null
  }
}

export function writeSidecar(dir: string, id: string, s: Sidecar): void {
  const fd = openSync(join(dir, `${id}.slack.json`), "w")
  try {
    writeSync(fd, JSON.stringify(s))
  } finally {
    closeSync(fd)
  }
}

/** Find the question whose Slack message ts (the thread root) is `threadTs`. */
export function idForThread(dir: string, channel: string, threadTs: string): string | null {
  if (!existsSync(dir) || !statSync(dir).isDirectory()) return null
  for (const f of readdirSync(dir)) {
    if (!f.endsWith(".slack.json")) continue
    const id = f.slice(0, -".slack.json".length)
    const s = readSidecar(dir, id)
    if (s && s.ts === threadTs && s.channel === channel) return id
  }
  return null
}
