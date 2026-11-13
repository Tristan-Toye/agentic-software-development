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
const VIEWS = new Set(["L0", "L1", "L2", "map", "full"])
/** Structural checks the page and the layout rely on. The full rules (budgets, evidence paths) stay in scripts/vr_graph.py. */
export function checkGraph(g: any, expectView?: string): Issue[] {
  const out: Issue[] = []
  const bad = (code: string, where: string, message: string) => out.push({ code, where, message })
  if (!g || typeof g !== "object" || Array.isArray(g)) { bad("not_object", "", "the file is not a JSON object"); return out }
  if (typeof g.schema_version !== "string" || !/^\d+\.\d+\.\d+$/.test(g.schema_version)) bad("schema_version", "schema_version", "missing or malformed schema_version")
  else if (g.schema_version.split(".")[0] !== "1") bad("schema_version", "schema_version", "unsupported major version " + g.schema_version + " (this reader handles 1.x)")
  if (!VIEWS.has(g.view)) bad("view", "view", "view must be L0, L1, L2, map or full")
  else if (expectView && g.view !== expectView) bad("view_mismatch", "view", "the file holds view " + g.view + ", expected " + expectView)
  if (typeof g.title !== "string") bad("missing_field", "title", "title is required")
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

// ---- collapsed groups (VR-03) ----
/** Most collapsed ids one request may carry. */
export const COLLAPSE_MAX = 200
/**
 * The graph with the given groups drawn as one box each. Pure; unknown ids are ignored; an empty set returns `graph` itself.
 * A collapsed group becomes a node under its own id (`collapsed: true`, `members` = member nodes at any depth, label
 * "Name (n)", status = the group's, or `changed` for a context group that holds changes). Its members and subgroups go away.
 * Every edge that touched a hidden element is re-routed to the outermost collapsed box and aggregated per (source, target):
 * one edge keeps its id and data (its own, even an edge already between those two ends), several become `agg:source>target` labelled by aggLabel ("builds on ×n", "N links") (status = the common one, else
 * `changed`; `inferred` only when all are). An edge that ends inside one box on both sides is dropped.
 */
export function collapseGraph(graph: any, ids: string[]): any {
  const groups: any[] = graph.groups ?? []
  const gmap = new Map<string, any>(groups.map((x) => [x.id, x]))
  const want = new Set<string>(ids.filter((i) => gmap.has(i)))
  if (!want.size) return graph
  const top = (start: string | undefined): string | undefined => {
    let t: string | undefined
    let p = start
    for (let n = 0; p && n < 100; n++) { if (want.has(p)) t = p; p = gmap.get(p)?.parent }
    return t
  }
  const repOf = (id: string, isNode: boolean, g?: string): string => (isNode ? top(g) : top(id)) ?? id
  const nodeRep = new Map<string, string>()
  for (const n of graph.nodes) nodeRep.set(n.id, repOf(n.id, true, n.group))
  const rep = (id: string): string => nodeRep.get(id) ?? (gmap.has(id) ? repOf(id, false) : id)
  const stats = new Map<string, { n: number; added: number; changed: number; removed: number }>()
  for (const w of want) stats.set(w, { n: 0, added: 0, changed: 0, removed: 0 })
  for (const n of graph.nodes) {
    const r = nodeRep.get(n.id)!
    const s = r !== n.id ? stats.get(r) : undefined
    if (s) { s.n++; if (n.status in s) (s as any)[n.status]++ }
  }
  const boxNode = (w: string): any => {
    const g = gmap.get(w), s = stats.get(w)!
    const status = g.status === "context" && s.added + s.changed + s.removed > 0 ? "changed" : g.status
    const parts = (["added", "changed", "removed"] as const).filter((k) => s[k]).map((k) => s[k] + " " + k)
    const out: any = { id: w, label: g.label + " (" + s.n + ")", kind: "group", status, evidence: g.evidence ?? [], collapsed: true, members: s.n,
      detail: { after: "Collapsed group of " + s.n + " element" + (s.n === 1 ? "" : "s") + (parts.length ? ": " + parts.join(", ") : "") + ". Click to open it." } }
    if (g.parent && gmap.has(g.parent)) out.group = g.parent
    if (g.label_source) out.label_source = g.label_source
    return out
  }
  const nodes: any[] = []
  const placed = new Set<string>()
  for (const n of graph.nodes) {
    const r = nodeRep.get(n.id)!
    if (r === n.id) nodes.push(n)
    else if (!placed.has(r)) { placed.add(r); nodes.push(boxNode(r)) }
  }
  for (const w of want) if (!placed.has(w) && rep(w) === w) nodes.push(boxNode(w))
  const keptGroups = groups.filter((x) => !want.has(x.id) && rep(x.id) === x.id)
  const bucket = new Map<string, any[]>()
  for (const e of graph.edges) {
    const s = rep(e.source), t = rep(e.target)
    if (s === t && (s !== e.source || t !== e.target)) continue
    const k = s + "\u0000" + t
    if (!bucket.has(k)) bucket.set(k, [])
    bucket.get(k)!.push(e)
  }
  const out: any[] = [...bucket.entries()].map(([k, list]) => {
    const [s, t] = k.split("\u0000")
    if (list.length === 1) return list[0].source === s && list[0].target === t ? list[0] : { ...list[0], source: s, target: t }
    const sts = new Set(list.map((x) => x.status))
    return { id: "agg:" + s + ">" + t, source: s, target: t, kind: "aggregate", label: aggLabel(list), status: sts.size === 1 ? [...sts][0] : "changed",
      resolution: list.every((x) => (x.resolution ?? "static") === "inferred") ? "inferred" : "static", count: list.length,
      evidence: list.flatMap((x) => x.evidence ?? []).slice(0, 50) }
  })
  return { ...graph, groups: keptGroups, nodes, edges: out.length > MERGE_ABOVE ? mergeDirections(out, nodes) : out }
}

/** Longest edge label the page draws (graph.schema.json). */
export const EDGE_LABEL_MAX = 40
/** A collapsed graph with more edges than this folds both directions of a pair into one edge (a dense folded map, scenario VR-10). */
export const MERGE_ABOVE = 8
const KIND_VERB: Record<string, string> = { depends: "builds on", calls: "calls", contains: "contains" }
const clip = (s: string): string => (s.length > EDGE_LABEL_MAX ? s.slice(0, EDGE_LABEL_MAX - 1) + "…" : s)
/** What an aggregate says: the label all merged edges share ("builds on ×3"), else their shared kind's verb, else "N links". */
export function aggLabel(list: any[]): string {
  const n = list.length
  const labels = new Set(list.map((x) => (typeof x.label === "string" ? x.label.trim() : "")))
  const one = labels.size === 1 ? [...labels][0] : ""
  if (one && !/^\d+$/.test(one)) return clip(one + " ×" + n)
  const kinds = new Set(list.map((x) => x.kind))
  const verb = kinds.size === 1 ? KIND_VERB[[...kinds][0]] : ""
  return verb ? clip(verb + " ×" + n) : n + " links"
}

/** The words of an edge without its count: "builds on" for "builds on ×3"; "" for "N links" or a bare number. */
const verbOf = (e: any): string => {
  const l = typeof e.label === "string" ? e.label.replace(/ ×\d+$/, "").trim() : ""
  return l && !/^\d+( links)?$/.test(l) && !l.startsWith("×") ? l : KIND_VERB[e.kind] ?? ""
}

/**
 * Fold the two directions of one pair of boxes into a single edge `agg:a<>b` (source = the end listed first in the graph), drawn with an arrow at
 * both ends. `counts` = [a to b, b to a] keeps both numbers, the label shows both ("builds on ×3 | ×2"), the evidence of both sides stays.
 */
function mergeDirections(edges: any[], nodes: any[]): any[] {
  const order = new Map<string, number>(nodes.map((n, i) => [n.id, i]))
  const by = new Map<string, any>(edges.map((e) => [e.source + "\u0000" + e.target, e]))
  const done = new Set<any>()
  const res: any[] = []
  for (const e of edges) {
    if (done.has(e)) continue
    const back = by.get(e.target + "\u0000" + e.source)
    if (!back || back === e) { res.push(e); continue }
    done.add(e); done.add(back)
    const [f, b] = (order.get(e.source) ?? 0) <= (order.get(e.target) ?? 0) ? [e, back] : [back, e]
    const cf = f.count ?? 1, cb = b.count ?? 1
    const sts = new Set([f.status, b.status])
    const vf = verbOf(f), verb = vf && vf === verbOf(b) ? vf : ""
    res.push({ id: "agg:" + f.source + "<>" + f.target, source: f.source, target: f.target, kind: "aggregate", bidirectional: true, counts: [cf, cb],
      label: clip((verb ? verb + " " : "") + "×" + cf + " | ×" + cb), status: sts.size === 1 ? [...sts][0] : "changed",
      resolution: f.resolution === "inferred" && b.resolution === "inferred" ? "inferred" : "static", count: cf + cb,
      evidence: [...(f.evidence ?? []), ...(b.evidence ?? [])].slice(0, 50) })
  }
  return res
}

/** A parsed graph file with `collapsed` groups drawn as boxes, and its layout. Shared by the dossier views and the committed maps. */
export async function collapseLoaded(g: any, view: string, collapsed: string[]): Promise<{ graph: any; layout: Layout; collapsed: string[] }> {
  if (checkGraph(g, view).some((i) => i.code !== "dangling_edge")) throw new Error(view + " is not a valid graph")
  const ids = [...new Set(collapsed)].slice(0, COLLAPSE_MAX)
  const clean = stripDollar(g)
  const known = new Set<string>([...(clean.groups ?? []).map((x: any) => x.id), ...clean.nodes.map((x: any) => x.id), ...(clean.lanes ?? []).map((x: any) => x.id)])
  clean.edges = clean.edges.filter((e: any) => known.has(e.source) && known.has(e.target))
  const lanes = view === "L1" && (clean.lanes ?? []).length > 0
  const use = lanes ? [] : ids.filter((i) => (clean.groups ?? []).some((x: any) => x.id === i))
  const graph = collapseGraph(clean, use)
  return { graph, layout: await layoutGraph(graph), collapsed: use }
}

/** One view of a dossier directory with `collapsed` groups drawn as boxes, and its layout (cached on the collapsed graph's hash). */
export async function collapsedView(dir: string, view: string, collapsed: string[]): Promise<{ graph: any; layout: Layout; collapsed: string[] }> {
  if (!(VR_VIEWS as readonly string[]).includes(view)) throw new Error("view must be L0, L1 or L2")
  const file = join(dir, view.toLowerCase() + ".json")
  if (!existsSync(file) || !statSync(file).isFile()) throw new Error("no " + view.toLowerCase() + ".json")
  return collapseLoaded(JSON.parse(readFileSync(file, "utf8")), view, collapsed)
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
    // The dangling edges are found from the id sets, not by parsing the issue messages (an id may hold spaces).
    const known = new Set<string>([...(clean.groups ?? []), ...(clean.lanes ?? []), ...clean.nodes].map((x: any) => x.id))
    clean.edges = clean.edges.filter((e: any) => known.has(e.source) && known.has(e.target))
    out.view = clean.view
    out.graph = clean
    try { out.layout = await layoutGraph(clean) } catch (e) { out.issues.push({ code: "layout_failed", where: "", message: (e as Error).message }) }
  }
  res.rev = h.digest("hex").slice(0, 16)
  res.issues = VR_VIEWS.flatMap((v) => (res.views[v]?.issues ?? []).map((i) => ({ ...i, where: v + (i.where ? "." + i.where : "") })))
  return res
}
