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
  /** Where ELK put each labelled edge's label (the box it kept free for it). */
  labels?: Record<string, Box>
  /** seq: the box around each frame's first..last steps (see frameBoxes), by frame id. */
  frames?: Record<string, Box>
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
const VIEWS = new Set(["L0", "L1", "L2", "map", "full", "c1", "c2", "c3", "c4", "seq"])
/** Views drawn as lanes top to bottom (steps in order) instead of groups left to right: the L1 flow and a sequence diagram. */
export const isLaneView = (view: unknown): boolean => view === "L1" || view === "seq"
/** The views of the architecture diagrams kept as JSON beside their Markdown (scripts/vr_mermaid.py). */
export const isDiagramView = (view: unknown): boolean => typeof view === "string" && /^(c[1-4]|seq)$/.test(view)
/** Structural checks the page and the layout rely on. The full rules (budgets, evidence paths) stay in scripts/vr_graph.py. */
export function checkGraph(g: any, expectView?: string): Issue[] {
  const out: Issue[] = []
  const bad = (code: string, where: string, message: string) => out.push({ code, where, message })
  if (!g || typeof g !== "object" || Array.isArray(g)) { bad("not_object", "", "the file is not a JSON object"); return out }
  if (typeof g.schema_version !== "string" || !/^\d+\.\d+\.\d+$/.test(g.schema_version)) bad("schema_version", "schema_version", "missing or malformed schema_version")
  else if (g.schema_version.split(".")[0] !== "1") bad("schema_version", "schema_version", "unsupported major version " + g.schema_version + " (this reader handles 1.x)")
  if (!VIEWS.has(g.view)) bad("view", "view", "view must be L0, L1, L2, map, full, c1, c2, c3, c4 or seq")
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

/** A folded box draws a ▸ before its label and a fold button after it: room for both. */
const FOLDED_EXTRA = 36
const size = (g: any, label: string, folded = false): { width: number; height: number } => {
  const t = String(label ?? "").length
  return { width: Math.max(130, Math.min(250 + (folded ? FOLDED_EXTRA : 0), Math.round(t * 6.6 + 28 + (folded ? FOLDED_EXTRA : 0)))), height: isLaneView(g.view) ? 62 : 50 }
}

/** The size of an edge label as the page draws it (11 px text on a backing rect). */
export const edgeLabelSize = (label: string): { width: number; height: number } => ({ width: Math.ceil(label.length * 6.1) + 8, height: 15 })

/** The width-to-height ratio a wrapped layout aims for: about the canvas's shape. */
export const WRAP_ASPECT = 1.8
/** A left-to-right layout wider than this and more than WRAP_FLAT times as wide as tall is laid out again, wrapped onto more rows. */
export const WRAP_WIDTH = 1020
export const WRAP_FLAT = 3

/** The ELK input for a graph: groups (or L1 lanes) as compound nodes, model order on. Pure. */
export function elkInput(g: any, mode: "thorough" | "fast", wrap = false): { root: any; direction: "RIGHT" | "DOWN" } {
  const direction = isLaneView(g.view) ? "DOWN" : "RIGHT"
  const opts: Record<string, string> = {
    "elk.algorithm": "layered", "elk.direction": direction, "elk.hierarchyHandling": "INCLUDE_CHILDREN",
    "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES", "elk.spacing.nodeNode": "28",
    "elk.layered.spacing.nodeNodeBetweenLayers": "28", "elk.edgeRouting": "ORTHOGONAL", "elk.json.shapeCoords": "ROOT", "elk.json.edgeCoords": "ROOT",
  }
  // A long left-to-right chain wraps onto more rows near the canvas shape, instead of one strip the canvas must shrink to fit.
  if (direction === "RIGHT" && wrap) Object.assign(opts, { "elk.layered.wrapping.strategy": "MULTI_EDGE", "elk.aspectRatio": String(WRAP_ASPECT) })
  if (mode === "fast") Object.assign(opts, { "elk.layered.thoroughness": "1", "elk.layered.nodePlacement.strategy": "SIMPLE", "elk.edgeRouting": "POLYLINE" })
  const lanes: any[] = isLaneView(g.view) ? g.lanes ?? [] : []
  const containers: any[] = [...(g.groups ?? []), ...lanes]
  const known = new Set<string>(containers.map((c) => c.id))
  const nodeIds = new Set<string>(g.nodes.map((n: any) => n.id))
  const all = new Set<string>([...known, ...nodeIds])
  const parentOf = (n: any, isGroup: boolean): string | undefined => {
    const p = isGroup ? n.parent : (isLaneView(g.view) && lanes.length ? n.lane : n.group)
    return p && known.has(p) && p !== n.id ? p : undefined
  }
  const mk = new Map<string, any>()
  for (const c of containers) mk.set(c.id, { id: c.id, layoutOptions: { "elk.padding": "[top=30,left=14,bottom=14,right=14]" }, children: [] })
  for (const n of g.nodes) mk.set(n.id, { id: n.id, ...size(g, n.label, n.collapsed === true) })
  const root: any = { id: "root", layoutOptions: opts, children: [], edges: [] }
  // A parent cycle (a -> b -> a) would drop both groups out of the tree: break it by hanging the group on the root.
  const cyc = (id: string): boolean => { const seen = new Set<string>([id]); let p = parentOf(containers.find((c) => c.id === id) ?? {}, true); while (p) { if (seen.has(p)) return true; seen.add(p); p = parentOf(containers.find((c) => c.id === p) ?? {}, true) } return false }
  for (const c of containers) { const p = isLaneView(g.view) && lanes.some((l) => l.id === c.id) ? undefined : parentOf(c, true); (p && !cyc(c.id) ? mk.get(p).children : root.children).push(mk.get(c.id)) }
  for (const n of g.nodes) { const p = parentOf(n, false); (p ? mk.get(p).children : root.children).push(mk.get(n.id)) }
  // A labelled edge carries its label at the size the page draws it, so ELK keeps room for it instead of the page squeezing it in later.
  for (const e of g.edges) if (all.has(e.source) && all.has(e.target)) {
    const ed: any = { id: e.id, sources: [e.source], targets: [e.target] }
    if (typeof e.label === "string" && e.label) ed.labels = [{ id: e.id + "#label", text: e.label, ...edgeLabelSize(e.label), layoutOptions: { "elk.edgeLabels.inline": "true" } }]
    root.edges.push(ed)
  }
  return { root, direction }
}

/**
 * seq: the box of each frame, from the laid-out boxes of the steps it spans (flows[0].after from `first` to `last`; a step the
 * layout has no box for is skipped). A frame that holds other frames is padded wider, so nested boxes do not share an edge; the
 * top keeps room for the label. Pure. Undefined when the graph has no frame with a box.
 */
export function frameBoxes(g: any, boxes: Record<string, Box>): Record<string, Box> | undefined {
  if (!Array.isArray(g.frames) || !g.frames.length) return undefined
  const order: string[] = Array.isArray(g.flows?.[0]?.after) ? g.flows[0].after : g.nodes.filter((n: any) => n.order !== undefined).sort((a: any, b: any) => a.order - b.order).map((n: any) => n.id)
  const at = new Map<string, number>(order.map((id, i) => [id, i]))
  const rng = new Map<string, [number, number]>()
  for (const f of g.frames) { const a = at.get(f.first), b = at.get(f.last); if (a !== undefined && b !== undefined && a <= b) rng.set(f.id, [a, b]) }
  // frame o sits inside frame f: its range is within f's, and when the ranges are equal it comes later in the array (outer before inner)
  const idx = new Map<string, number>(g.frames.map((f: any, i: number) => [f.id, i]))
  const inside = (o: any, f: any): boolean => { const q = rng.get(o.id), r = rng.get(f.id); return !!q && !!r && o.id !== f.id && q[0] >= r[0] && q[1] <= r[1] && (q[0] !== r[0] || q[1] !== r[1] || idx.get(o.id)! > idx.get(f.id)!) }
  const memo = new Map<string, number>()
  const depth = (f: any): number => { let d = memo.get(f.id); if (d === undefined) { d = 0; for (const o of g.frames) if (inside(o, f)) d = Math.max(d, 1 + depth(o)); memo.set(f.id, d) } return d }
  const res: Record<string, Box> = {}
  for (const f of g.frames) {
    const r = rng.get(f.id)
    if (!r) continue
    const below = depth(f)
    const bs = order.slice(r[0], r[1] + 1).map((id) => boxes[id]).filter(Boolean)
    if (!bs.length) continue
    const pad = 8 + 5 * Math.min(below, 4)
    const x0 = Math.min(...bs.map((b) => b.x)) - pad, y0 = Math.min(...bs.map((b) => b.y)) - pad - 14
    const x1 = Math.max(...bs.map((b) => b.x + b.w)) + pad, y1 = Math.max(...bs.map((b) => b.y + b.h)) + pad
    const x = Math.max(0, Math.round(x0)), y = Math.max(0, Math.round(y0))
    res[f.id] = { x, y, w: Math.round(x1) - x, h: Math.round(y1) - y }
  }
  return Object.keys(res).length ? res : undefined
}

/**
 * seq: the layout of a sequence diagram, computed here and not by ELK: ELK lays the lane boxes out one by one and loses the order of
 * steps that sit in different lanes. Lanes are columns in file order, each step is a box centred in its lane, one row per step in
 * flows[0].after order, joined to the next by the edge of `flow` kind (down, across, down). A frame that opens or closes at a step
 * adds room above or below it, so the frame boxes (frameBoxes) do not touch the neighbouring steps. Pure and deterministic.
 */
export function seqLayout(g: any): Layout {
  const lanes: any[] = g.lanes ?? []
  const ids = new Set<string>(g.nodes.map((n: any) => n.id))
  const order: string[] = [...(Array.isArray(g.flows?.[0]?.after) ? g.flows[0].after.filter((id: string) => ids.has(id)) : [])]
  const seen = new Set<string>(order)
  for (const n of [...g.nodes].sort((a: any, b: any) => (a.order ?? 1e9) - (b.order ?? 1e9))) if (!seen.has(n.id)) { seen.add(n.id); order.push(n.id) }
  const node = new Map<string, any>(g.nodes.map((n: any) => [n.id, n]))
  const laneOf = (n: any): number => Math.max(0, lanes.findIndex((l) => l.id === n.lane))
  const sz = new Map<string, { width: number; height: number }>(order.map((id) => [id, size(g, node.get(id).label)]))
  const colW = lanes.map((_, i) => Math.max(160, ...order.filter((id) => laneOf(node.get(id)) === i).map((id) => sz.get(id)!.width)) + 2 * 20)
  const colX: number[] = []
  colW.reduce((x, w, i) => (colX[i] = x, x + w + 14), 0)
  const at = new Map<string, number>(order.map((id, i) => [id, i]))
  const starts = order.map(() => 0), ends = order.map(() => 0)
  for (const f of g.frames ?? []) { const a = at.get(f.first), b = at.get(f.last); if (a !== undefined && b !== undefined && a <= b) { starts[a]++; ends[b]++ } }
  const boxes: Record<string, Box> = {}
  let y = 44
  order.forEach((id, i) => {
    if (i) y += 28 + 18 * Math.min(starts[i], 4) + 8 * Math.min(ends[i - 1], 4)
    const n = node.get(id), l = laneOf(n), s = sz.get(id)!
    boxes[id] = { x: Math.round(colX[l] + (colW[l] - s.width) / 2), y, w: s.width, h: s.height }
    y += s.height
  })
  const bottom = y + 20
  const last = lanes.length - 1
  const L: Layout = { view: g.view, mode: "thorough", direction: "DOWN", width: last < 0 ? 0 : colX[last] + colW[last], height: bottom, boxes, edges: {} }
  lanes.forEach((l, i) => { L.boxes[l.id] = { x: colX[i], y: 0, w: colW[i], h: bottom } })
  for (const e of g.edges ?? []) {
    const a = boxes[e.source], b = boxes[e.target]
    if (!a || !b) continue
    const ax = a.x + a.w / 2, bx = b.x + b.w / 2, ya = a.y + a.h, yb = b.y
    const mid = Math.round((ya + yb) / 2)
    L.edges[e.id] = (ax === bx || yb <= ya ? [[Math.round(ax), ya], [Math.round(bx), yb]] : [[Math.round(ax), ya], [Math.round(ax), mid], [Math.round(bx), mid], [Math.round(bx), yb]]) as [number, number][]
  }
  const fr = frameBoxes(g, boxes)
  if (fr) { L.frames = fr; for (const b of Object.values(fr)) { L.width = Math.max(L.width, b.x + b.w); L.height = Math.max(L.height, b.y + b.h) } }
  return L
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
  // A long flat strip (one chain of layers) is laid out again wrapped onto rows near the canvas shape. A small graph is never wrapped: its
  // edges would loop around the rows. Wrapping is an ELK extra that can throw on some graphs: then the unwrapped layout stays.
  if (g.view === "seq" && (g.lanes ?? []).length) { const L = seqLayout(g); cache.set(key, L); return L }
  const flat = await runLayoutOnce(g, key, timeoutMs, false)
  if (flat.direction !== "RIGHT" || flat.mode !== "thorough" || flat.width <= WRAP_WIDTH || flat.width / Math.max(flat.height, 1) <= WRAP_FLAT) return flat
  let wrapped: Layout
  try { wrapped = await runLayoutOnce(g, key, timeoutMs, true) } catch (e) { if (/timed out/.test((e as Error).message)) throw e; return flat }
  // ELK does not route a wrapped edge that crosses into a group: its pieces then join with a diagonal. Keep the flat layout then.
  return hasDiagonal(wrapped) ? flat : wrapped
}
/** Whether any edge of an orthogonal layout has a segment that is neither horizontal nor vertical. */
export function hasDiagonal(L: Layout): boolean {
  return Object.values(L.edges).some((p) => p.some((q, i) => i > 0 && q[0] !== p[i - 1][0] && q[1] !== p[i - 1][1]))
}
/** The sections of one edge from source to target: the one with no incoming section first, then each `outgoingSections` in turn. */
export function chainSections(secs: any[]): any[] {
  if (secs.length < 2) return secs
  const byId = new Map<string, any>(secs.map((x) => [x.id, x]))
  let cur = secs.find((x) => !(x.incomingSections ?? []).length) ?? secs[0]
  const out: any[] = [], seen = new Set<string>()
  while (cur && !seen.has(cur.id)) { out.push(cur); seen.add(cur.id); cur = byId.get((cur.outgoingSections ?? [])[0]) }
  return out.length === secs.length ? out : secs
}
async function runLayoutOnce(g: any, key: string, timeoutMs: number, wrap: boolean): Promise<Layout> {
  const mode: "thorough" | "fast" = g.nodes.length > FAST_ABOVE ? "fast" : "thorough"
  const { root, direction } = elkInput(g, mode, wrap)
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
  const fr = frameBoxes(g, L.boxes)
  if (fr) { L.frames = fr; for (const b of Object.values(fr)) { L.width = Math.max(L.width, b.x + b.w); L.height = Math.max(L.height, b.y + b.h) } }
  for (const e of out.edges ?? []) {
    // A wrapped edge comes in several sections, one per row it crosses: the drawn line is all of them in order.
    const pts = chainSections(e.sections ?? []).flatMap((s: any) => [s.startPoint, ...(s.bendPoints ?? []), s.endPoint])
    if (pts.length) L.edges[e.id] = pts.map((p: any) => [Math.round(p.x), Math.round(p.y)] as [number, number])
    const lb = (e.labels ?? [])[0]
    if (lb && typeof lb.x === "number") (L.labels ??= {})[e.id] = { x: Math.round(lb.x), y: Math.round(lb.y), w: Math.round(lb.width), h: Math.round(lb.height) }
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
  const stats = new Map<string, { n: number; added: number; changed: number; removed: number; kinds: string[] }>()
  for (const w of want) stats.set(w, { n: 0, added: 0, changed: 0, removed: 0, kinds: [] })
  for (const n of graph.nodes) {
    const r = nodeRep.get(n.id)!
    const s = r !== n.id ? stats.get(r) : undefined
    if (s) { s.n++; s.kinds.push(String(n.kind ?? "item")); if (n.status in s) (s as any)[n.status]++ }
  }
  const boxNode = (w: string): any => {
    const g = gmap.get(w), s = stats.get(w)!
    const status = g.status === "context" && s.added + s.changed + s.removed > 0 ? "changed" : g.status
    const parts = (["added", "changed", "removed"] as const).filter((k) => s[k]).map((k) => s[k] + " " + k)
    const out: any = { id: w, label: g.label + " (" + s.n + ")", kind: "group", status, evidence: g.evidence ?? [], collapsed: true, members: s.n, member_kinds: s.kinds,
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
/**
 * What an aggregate says, as one phrase: the base label once and the total count ("builds on ×5"). Merged edges that carry different labels
 * read "<most common label> +N more" (N = the merged edges with another label). With no usable label, the shared kind's verb, else "N links".
 * Edges that are aggregates already count for their `count`.
 */
export function aggLabel(list: any[]): string {
  const total = list.reduce((a, x) => a + (x.count ?? 1), 0)
  const tally = new Map<string, number>()
  for (const x of list) { const v = verbOf(x, false); if (v) tally.set(v, (tally.get(v) ?? 0) + (x.count ?? 1)) }
  if (tally.size) {
    const [top, n] = [...tally.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))[0]
    return clip(tally.size === 1 && n === total ? top + " ×" + total : top + " +" + (total - n) + " more")
  }
  const kinds = new Set(list.map((x) => x.kind))
  const verb = kinds.size === 1 ? KIND_VERB[[...kinds][0]] : ""
  return verb ? clip(verb + " ×" + total) : total + " links"
}

/** The words of an edge without its count: "builds on" for "builds on ×3"; "" for "N links" or a bare number (or, with `byKind`, the kind's verb). */
const verbOf = (e: any, byKind = true): string => {
  const l = typeof e.label === "string" ? e.label.replace(/ ×\d+$/, "").trim() : ""
  return l && !/^\d+( links)?$/.test(l) && !l.startsWith("×") && !/ \+\d+ more$/.test(l) ? l : byKind ? KIND_VERB[e.kind] ?? "" : ""
}

/**
 * Fold the two directions of one pair of boxes into a single edge `agg:a<>b` (source = the end listed first in the graph), drawn with an arrow at
 * both ends. `counts` = [a to b, b to a] keeps both numbers, the label shows the total ("builds on ×5"), the evidence of both sides stays.
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
    res.push({ id: "agg:" + f.source + "<>" + f.target, source: f.source, target: f.target, kind: "aggregate", bidirectional: true, counts: [cf, cb],
      label: aggLabel([f, b]), status: sts.size === 1 ? [...sts][0] : "changed",
      resolution: f.resolution === "inferred" && b.resolution === "inferred" ? "inferred" : "static", count: cf + cb,
      evidence: [...(f.evidence ?? []), ...(b.evidence ?? [])].slice(0, 50) })
  }
  return res
}

/**
 * L1 and seq give the order of its steps in `flows` (before and after lists), not as edges. Each step after the next in a flow gets
 * an edge, so the layout runs the flow top to bottom and the page draws the arrows: `context` when the pair is in both lists,
 * `added` when only in the after list, `removed` when only in the before list. A pair an edge of the file already joins is left alone.
 */
export function withFlowEdges(g: any): any {
  if (!isLaneView(g.view) || !Array.isArray(g.flows) || !g.flows.length) return g
  const nodes = new Set<string>(g.nodes.map((n: any) => n.id))
  const have = new Set<string>(g.edges.map((e: any) => e.source + "\u0000" + e.target))
  const added: any[] = []
  for (const f of g.flows) {
    const pairs = (l: unknown) => { const a = Array.isArray(l) ? l.filter((x) => nodes.has(x)) : []; return a.slice(1).map((t, i) => a[i] + "\u0000" + t) }
    const before = new Set(pairs(f.before)), after = pairs(f.after)
    const seq = [...after, ...[...before].filter((k) => !after.includes(k))]
    for (const k of seq) {
      if (have.has(k)) continue
      have.add(k)
      const [source, target] = k.split("\u0000")
      added.push({ id: "flow:" + (f.id ?? "") + ":" + source + "->" + target, source, target, kind: "flow", status: !after.includes(k) ? "removed" : before.has(k) ? "context" : "added", evidence: Array.isArray(f.evidence) ? f.evidence : [], derived: true })
    }
  }
  return added.length ? { ...g, edges: [...g.edges, ...added] } : g
}

/** A parsed graph file with `collapsed` groups drawn as boxes, and its layout. Shared by the dossier views and the committed maps. */
export async function collapseLoaded(g: any, view: string, collapsed: string[]): Promise<{ graph: any; layout: Layout; collapsed: string[] }> {
  if (checkGraph(g, view).some((i) => i.code !== "dangling_edge")) throw new Error(view + " is not a valid graph")
  const ids = [...new Set(collapsed)].slice(0, COLLAPSE_MAX)
  const clean = stripDollar(g)
  const known = new Set<string>([...(clean.groups ?? []).map((x: any) => x.id), ...clean.nodes.map((x: any) => x.id), ...(clean.lanes ?? []).map((x: any) => x.id)])
  clean.edges = clean.edges.filter((e: any) => known.has(e.source) && known.has(e.target))
  const flowed = withFlowEdges(clean)
  const lanes = isLaneView(view) && (clean.lanes ?? []).length > 0
  const use = lanes ? [] : ids.filter((i) => (clean.groups ?? []).some((x: any) => x.id === i))
  const graph = collapseGraph(flowed, use)
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
    out.graph = withFlowEdges(clean)
    try { out.layout = await layoutGraph(out.graph) } catch (e) { out.issues.push({ code: "layout_failed", where: "", message: (e as Error).message }) }
  }
  res.rev = h.digest("hex").slice(0, 16)
  res.issues = VR_VIEWS.flatMap((v) => (res.views[v]?.issues ?? []).map((i) => ({ ...i, where: v + (i.where ? "." + i.where : "") })))
  return res
}
