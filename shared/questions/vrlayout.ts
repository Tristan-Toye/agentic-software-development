// Visual review (VR-03): the server-side layout of a change graph (references/visual-review/graph.schema.json 1.0.0).
// ELK runs in a Bun Worker from the vendored elk-api.js + elk-worker.min.js. NEVER import elk.bundled.js: under Bun it
// starts a Worker it cannot terminate and the process never exits (research/d0/REPORT.md, 1.2).
// The page never lays out: it gets boxes and edge points from here and only draws them.
import { createHash } from "node:crypto"
import { existsSync, readFileSync, statSync } from "node:fs"
import { join } from "node:path"
// @ts-ignore: vendored CommonJS build, no types
import ElkModule from "./vendor/elk/elk-api.js"

const ElkClass: any = (ElkModule as any)?.default ?? ElkModule
const WORKER_FILE = new URL("./vendor/elk/elk-worker.min.js", import.meta.url)

export type Box = { x: number; y: number; w: number; h: number }
export type Layout = {
  view: string
  /** The ELK settings used: `thorough` up to FAST_ABOVE nodes, `fast` (cheaper) above. */
  mode: "thorough" | "fast"
  direction: "RIGHT" | "DOWN"
  width: number
  height: number
  boxes: Record<string, Box>
  edges: Record<string, [number, number][]>
}
export type Issue = { code: string; where: string; message: string }
/** One view as the page gets it. `layout` is null when the graph could not be laid out (see `issues`). */
export type ViewResult = { file: string; view?: string; title?: string; graph: any | null; layout: Layout | null; issues: Issue[] }
export type VisualReview = { dossier: string; dir: string; rev: string; views: Record<"L0" | "L1" | "L2", ViewResult | null>; issues: Issue[]; hasReview: boolean }

/** Above this many nodes the layout uses ELK's cheaper settings (scenario S9). */
export const FAST_ABOVE = 150
/** A layout that takes longer than this is abandoned and the worker is replaced. */
export const LAYOUT_TIMEOUT_MS = 20_000
const IDLE_MS = 15_000

/** Drop every `$`-prefixed key at any depth ($schema, $comment, …): they carry no layout and would break determinism. */
export function stripDollar<T>(v: T): T {
  if (Array.isArray(v)) return v.map(stripDollar) as any
  if (v && typeof v === "object") {
    const o: any = {}
    for (const k of Object.keys(v as any)) if (!k.startsWith("$")) o[k] = stripDollar((v as any)[k])
    return o
  }
  return v
}
function canon(v: any): any {
  if (Array.isArray(v)) return v.map(canon)
  if (v && typeof v === "object") { const o: any = {}; for (const k of Object.keys(v).sort()) o[k] = canon(v[k]); return o }
  return v
}
/** sha256 over the canonical JSON of the stripped graph: the cache key of its layout. */
export const graphHash = (g: unknown): string => createHash("sha256").update(JSON.stringify(canon(stripDollar(g)))).digest("hex")

const STATUS = new Set(["added", "changed", "removed", "context"])
const VIEWS = new Set(["L0", "L1", "L2", "full"])
/** Structural checks the page and the layout rely on. The full rules (budgets, evidence paths) stay in scripts/vr_graph.py. */
export function checkGraph(g: any, expectView?: string): Issue[] {
  const out: Issue[] = []
  const bad = (code: string, where: string, message: string) => out.push({ code, where, message })
  if (!g || typeof g !== "object" || Array.isArray(g)) { bad("not_object", "", "the file is not a JSON object"); return out }
  if (typeof g.schema_version !== "string" || !/^\d+\.\d+\.\d+$/.test(g.schema_version)) bad("schema_version", "schema_version", "missing or malformed schema_version")
  else if (g.schema_version.split(".")[0] !== "1") bad("schema_version", "schema_version", "unsupported major version " + g.schema_version + " (this reader handles 1.x)")
  if (!VIEWS.has(g.view)) bad("view", "view", "view must be L0, L1, L2 or full")
  else if (expectView && g.view !== expectView) bad("view_mismatch", "view", "the file holds view " + g.view + ", expected " + expectView)
  for (const k of ["nodes", "edges"]) if (!Array.isArray(g[k])) bad("missing_field", k, k + " must be an array")
  if (out.length) return out
  const ids = new Set<string>()
  const take = (kind: string, x: any, i: number) => {
    const w = kind + "[" + i + "]"
    if (!x || typeof x !== "object") return bad("not_object", w, kind + " entry is not an object")
    if (typeof x.id !== "string" || !x.id) return bad("missing_field", w + ".id", "id is required")
    if (ids.has(x.id)) bad("duplicate_id", w, "duplicate id " + x.id)
    ids.add(x.id)
    if (typeof x.label !== "string" && kind !== "edges") bad("missing_field", w + ".label", "label is required")
    if (!STATUS.has(x.status)) bad("status", w + ".status", "status must be added, changed, removed or context")
  }
  ;(g.groups ?? []).forEach((x: any, i: number) => take("groups", x, i))
  ;(g.lanes ?? []).forEach((x: any, i: number) => { if (!x || typeof x.id !== "string") bad("missing_field", "lanes[" + i + "]", "lane needs an id"); else ids.add(x.id) })
  g.nodes.forEach((x: any, i: number) => take("nodes", x, i))
  g.edges.forEach((x: any, i: number) => {
    take("edges", x, i)
    if (x && typeof x === "object") for (const end of ["source", "target"]) if (!ids.has(x[end])) bad("dangling_edge", "edges[" + i + "]." + end, "edge " + x.id + " points at unknown " + x[end])
  })
  return out
}

// ---- the worker ----
let elk: any = null
let idleTimer: ReturnType<typeof setTimeout> | null = null
/** Layouts running in the worker now: the idle timer only closes a worker nobody is waiting on. */
let inflight = 0
function client(): any {
  if (idleTimer) { clearTimeout(idleTimer); idleTimer = null }
  if (!elk) {
    elk = new ElkClass({ workerFactory: () => { const w: any = new Worker(WORKER_FILE.href); w.unref?.(); return w } })
  }
  return elk
}
function armIdle(): void {
  if (idleTimer) clearTimeout(idleTimer)
  idleTimer = null
  if (inflight > 0 || !elk) return
  idleTimer = setTimeout(closeLayout, IDLE_MS)
  idleTimer.unref?.()
}
/** Terminate the worker. The process can exit afterwards; the next layout starts a new one. */
export function closeLayout(): void {
  if (idleTimer) { clearTimeout(idleTimer); idleTimer = null }
  const e = elk
  elk = null
  try { e?.terminateWorker() } catch { /* already gone */ }
}

const size = (g: any, label: string): { width: number; height: number } => {
  const t = String(label ?? "").length
  return { width: Math.max(130, Math.min(250, Math.round(t * 6.6 + 28))), height: g.view === "L1" ? 62 : 50 }
}

/** The ELK input for a graph: groups (or L1 lanes) as compound nodes, model order on. Pure. */
export function elkInput(g: any, mode: "thorough" | "fast"): { root: any; direction: "RIGHT" | "DOWN" } {
  const direction = g.view === "L1" ? "DOWN" : "RIGHT"
  const opts: Record<string, string> = {
    "elk.algorithm": "layered", "elk.direction": direction, "elk.hierarchyHandling": "INCLUDE_CHILDREN",
    "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES", "elk.spacing.nodeNode": "28",
    "elk.layered.spacing.nodeNodeBetweenLayers": "56", "elk.edgeRouting": "ORTHOGONAL", "elk.json.shapeCoords": "ROOT", "elk.json.edgeCoords": "ROOT",
  }
  if (mode === "fast") Object.assign(opts, { "elk.layered.thoroughness": "1", "elk.layered.nodePlacement.strategy": "SIMPLE", "elk.edgeRouting": "POLYLINE" })
  const lanes: any[] = g.view === "L1" ? g.lanes ?? [] : []
  const containers: any[] = [...(g.groups ?? []), ...lanes]
  const known = new Set<string>(containers.map((c) => c.id))
  const nodeIds = new Set<string>(g.nodes.map((n: any) => n.id))
  const all = new Set<string>([...known, ...nodeIds])
  const parentOf = (n: any, isGroup: boolean): string | undefined => {
    const p = isGroup ? n.parent : (g.view === "L1" && lanes.length ? n.lane : n.group)
    return p && known.has(p) && p !== n.id ? p : undefined
  }
  const mk = new Map<string, any>()
  for (const c of containers) mk.set(c.id, { id: c.id, layoutOptions: { "elk.padding": "[top=30,left=14,bottom=14,right=14]" }, children: [] })
  for (const n of g.nodes) mk.set(n.id, { id: n.id, ...size(g, n.label) })
  const root: any = { id: "root", layoutOptions: opts, children: [], edges: [] }
  // A parent cycle (a -> b -> a) would drop both groups out of the tree: break it by hanging the group on the root.
  const cyc = (id: string): boolean => { const seen = new Set<string>([id]); let p = parentOf(containers.find((c) => c.id === id) ?? {}, true); while (p) { if (seen.has(p)) return true; seen.add(p); p = parentOf(containers.find((c) => c.id === p) ?? {}, true) } return false }
  for (const c of containers) { const p = g.view === "L1" && lanes.some((l) => l.id === c.id) ? undefined : parentOf(c, true); (p && !cyc(c.id) ? mk.get(p).children : root.children).push(mk.get(c.id)) }
  for (const n of g.nodes) { const p = parentOf(n, false); (p ? mk.get(p).children : root.children).push(mk.get(n.id)) }
  for (const e of g.edges) if (all.has(e.source) && all.has(e.target)) root.edges.push({ id: e.id, sources: [e.source], targets: [e.target] })
  return { root, direction }
}

const cache = new Map<string, Layout>()
/** Cached layouts held at most. */
const CACHE_MAX = 64
export const layoutCacheSize = (): number => cache.size

/**
 * Lay out one valid graph through ELK in the worker. Deterministic: the same graph gives the same boxes and points.
 * Cached on graphHash. Rejects after `timeoutMs` (the worker is then replaced).
 */
/** Layouts in progress by graphHash: a second request for the same graph waits on the first instead of running it again. */
const pending = new Map<string, Promise<Layout>>()
export async function layoutGraph(graph: any, timeoutMs = LAYOUT_TIMEOUT_MS): Promise<Layout> {
  const g = stripDollar(graph)
  const key = graphHash(g)
  const hit = cache.get(key)
  if (hit) return hit
  const running = pending.get(key)
  if (running) return running
  const p = runLayout(g, key, timeoutMs).finally(() => pending.delete(key))
  pending.set(key, p)
  return p
}
async function runLayout(g: any, key: string, timeoutMs: number): Promise<Layout> {
  const mode: "thorough" | "fast" = g.nodes.length > FAST_ABOVE ? "fast" : "thorough"
  const { root, direction } = elkInput(g, mode)
  let timer: ReturnType<typeof setTimeout> | undefined
  const worker = client()
  inflight++
  // A timeout replaces the worker only if it is still the one this layout ran in (a hung worker is hung for every layout in it).
  const out: any = await Promise.race([
    worker.layout(root),
    new Promise((_, rej) => { timer = setTimeout(() => { if (elk === worker) closeLayout(); rej(new Error("layout timed out after " + timeoutMs + " ms")) }, timeoutMs) }),
  ]).finally(() => { clearTimeout(timer); inflight--; armIdle() })
  const L: Layout = { view: g.view, mode, direction, width: Math.ceil(out.width), height: Math.ceil(out.height), boxes: {}, edges: {} }
  const walk = (n: any) => { for (const c of n.children ?? []) { L.boxes[c.id] = { x: Math.round(c.x), y: Math.round(c.y), w: Math.round(c.width), h: Math.round(c.height) }; walk(c) } }
  walk(out)
  for (const e of out.edges ?? []) {
    const s = (e.sections ?? [])[0]
    if (s) L.edges[e.id] = [s.startPoint, ...(s.bendPoints ?? []), s.endPoint].map((p: any) => [Math.round(p.x), Math.round(p.y)] as [number, number])
  }
  if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value as string)
  cache.set(key, L)
  return L
}

// ---- the three files of a dossier ----
export const VR_VIEWS = ["L0", "L1", "L2"] as const
export const vrDir = (root: string, dossier: string): string => join(root, ".discovery", "visual-review", dossier)

/** Whether `root` holds a visual-review folder for the dossier with at least one of l0/l1/l2.json. */
export const hasVisualReview = (root: string, dossier: string): boolean => VR_VIEWS.some((v) => existsSync(join(vrDir(root, dossier), v.toLowerCase() + ".json")))

/**
 * Read, validate and lay out l0/l1/l2.json of one dossier directory. A missing file is a null view; a broken one carries
 * its issues and no layout. `review.json` (VR-04) is noted and otherwise ignored.
 */
export async function visualReview(dir: string, dossier: string): Promise<VisualReview> {
  const res: VisualReview = { dossier, dir, rev: "", views: { L0: null, L1: null, L2: null }, issues: [], hasReview: existsSync(join(dir, "review.json")) }
  const h = createHash("sha256")
  for (const v of VR_VIEWS) {
    const file = join(dir, v.toLowerCase() + ".json")
    if (!existsSync(file)) continue
    const out: ViewResult = { file: v.toLowerCase() + ".json", graph: null, layout: null, issues: [] }
    res.views[v] = out
    let g: any
    try {
      if (!statSync(file).isFile()) throw new Error("not a file")
      const text = readFileSync(file, "utf8")
      h.update(v + ":" + text)
      g = JSON.parse(text)
    } catch (e) { out.issues.push({ code: "unreadable", where: "", message: "cannot read " + out.file + ": " + (e as Error).message }); continue }
    out.issues.push(...checkGraph(g, v))
    // A dangling edge is dropped by the layout and reported; every other issue means no layout.
    const fatal = out.issues.some((i) => i.code !== "dangling_edge")
    if (typeof g?.title === "string") out.title = g.title
    if (fatal) continue
    const clean = stripDollar(g)
    clean.edges = clean.edges.filter((e: any) => !out.issues.some((i) => i.code === "dangling_edge" && i.message.startsWith("edge " + e.id + " ")))
    out.view = clean.view
    out.graph = clean
    try { out.layout = await layoutGraph(clean) } catch (e) { out.issues.push({ code: "layout_failed", where: "", message: (e as Error).message }) }
  }
  res.rev = h.digest("hex").slice(0, 16)
  res.issues = VR_VIEWS.flatMap((v) => (res.views[v]?.issues ?? []).map((i) => ({ ...i, where: v + (i.where ? "." + i.where : "") })))
  return res
}
