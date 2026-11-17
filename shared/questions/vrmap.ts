// Visual review (VR-06 wiring): the dashboard side of the living product map. It runs scripts/vr_map.py (check, list)
// on a repo root, with a timeout, and caches the staleness result on the map file's mtime.
import { existsSync, readdirSync, readFileSync, realpathSync, statSync } from "node:fs"
import { dirname, join, resolve, sep } from "node:path"
import { checkGraph, collapseLoaded, layoutGraph, stripDollar, type ViewResult } from "./vrlayout.ts"

export const MAP_REL = "docs/architecture/product-map.json"
export const MAP_TIMEOUT_MS = 20_000
/** A cached check is reused while the map's mtime is the same and it is younger than this (the code under the map can move on). */
export const MAP_TTL_MS = 60_000
export const vrMapScript = (): string => process.env.ASD_VR_MAP_BIN || join(import.meta.dir, "..", "..", "scripts", "vr_map.py")

export type Diagram = { path: string; kind: string; title: string; view?: string }
export type MapStatus = {
  repo: string
  hasMap: boolean
  /** The count and the ids of stale elements; 0 and [] with no map or no check result. */
  stale: number
  ids: string[]
  banner: string
  /** Why there is no banner when it is not because the map is fresh: no map, check timed out, check failed. */
  message: string
  diagrams: Diagram[]
  checkedAt: string
}

async function py(args: string[], timeoutMs: number): Promise<{ code: number; out: string; err: string; timedOut: boolean }> {
  let p: ReturnType<typeof Bun.spawn>
  // Bun.spawn throws at once when python3 is missing: report it like a failed run instead of a 500.
  try { p = Bun.spawn(["python3", vrMapScript(), ...args], { stdout: "pipe", stderr: "pipe", stdin: "ignore" }) } catch (e) { return { code: 127, out: "", err: (e as Error).message, timedOut: false } }
  let timedOut = false
  const t = setTimeout(() => { timedOut = true; p.kill() }, timeoutMs)
  try {
    const [out, err] = await Promise.all([new Response(p.stdout as ReadableStream).text(), new Response(p.stderr as ReadableStream).text()])
    const code = await p.exited
    return { code, out, err, timedOut }
  } finally { clearTimeout(t) }
}

const cache = new Map<string, { mtime: number; at: number; status: MapStatus }>()
export const clearMapCache = (): void => cache.clear()

/** The committed diagrams of the repo (`vr_map.py list --json`) and, when the map exists, its staleness (`check --json`). */
const inflight = new Map<string, Promise<MapStatus>>()
export async function mapStatus(repo: string, root: string, timeoutMs = MAP_TIMEOUT_MS): Promise<MapStatus> {
  // Concurrent calls for one repo share one check instead of each spawning vr_map.py.
  const running = inflight.get(root)
  if (running) return running
  const p = checkMap(repo, root, timeoutMs).finally(() => inflight.delete(root))
  inflight.set(root, p)
  return p
}
async function checkMap(repo: string, root: string, timeoutMs: number): Promise<MapStatus> {
  const map = join(root, MAP_REL)
  const has = existsSync(map)
  // The key also holds the mtimes of docs/architecture and the manifest, so a new diagram file is seen at once.
  const mt = (f: string): number => { try { return statSync(f).mtimeMs } catch { return 0 } }
  const mtime = (has ? mt(map) : 0) + mt(dirname(map)) * 1e-3 + mt(join(dirname(map), "manifest.json")) * 1e-6
  const key = root
  const hit = cache.get(key)
  if (hit && hit.mtime === mtime && Date.now() - hit.at < MAP_TTL_MS) return hit.status
  const st: MapStatus = { repo, hasMap: has, stale: 0, ids: [], banner: "", message: "", diagrams: [], checkedAt: new Date().toISOString() }
  const ls = await py(["list", root, "--json"], timeoutMs)
  if (ls.code === 0) {
    try {
      const j = JSON.parse(ls.out)
      st.diagrams = Array.isArray(j.diagrams) ? j.diagrams.filter((d: any) => d && typeof d.path === "string").map((d: any) => ({ path: d.path, kind: String(d.kind ?? "diagram"), title: String(d.title ?? d.path), view: typeof d.view === "string" ? d.view : undefined })) : []
      if (!j.has_map && typeof j.message === "string") st.message = j.message
    } catch { st.message = "vr_map list printed no JSON" }
  } else st.message = ls.timedOut ? "vr_map list timed out" : "vr_map list failed: " + ls.err.trim().split("\n").pop()
  if (!has) st.message = (st.message ? st.message + "; " : "") + "no committed product map yet (" + MAP_REL + ")"
  else {
    const ck = await py(["check", root, map, "--json", "--no-metrics"], timeoutMs)
    // exit 0 fresh, 1 stale (the JSON is on stdout either way), 2 unreadable
    try {
      if (ck.timedOut || ck.code > 1) throw new Error(ck.timedOut ? "timed out after " + timeoutMs / 1000 + " s" : ck.err.trim().split("\n").pop() || "exit " + ck.code)
      const j = JSON.parse(ck.out)
      const stale: any[] = Array.isArray(j.stale) ? j.stale : []
      st.stale = stale.length
      st.ids = stale.map((s) => String(s.id))
      st.banner = typeof j.banner === "string" ? j.banner : ""
    } catch (e) { st.message = "the map could not be checked: " + (e as Error).message }
  }
  cache.set(key, { mtime, at: Date.now(), status: st })
  return st
}

/** The text of a listed diagram: only a path `list` named, inside the repo (symlinks compared by real path). */
function readListed(root: string, rel: string, listed: Diagram[]): { d: Diagram; text: string } {
  const d = listed.find((x) => x.path === rel)
  if (!d) throw new DiagramError(404, "not a listed diagram")
  return { d, text: readInRepo(root, rel) }
}
/** The text of a file inside the repo (symlinks compared by real path). */
function readInRepo(root: string, rel: string): string {
  const abs = resolve(root, rel)
  if (!abs.startsWith(resolve(root) + sep)) throw new DiagramError(404, "path leaves the repo")
  // The lexical check passes a symlink inside the repo that points outside it: compare the real paths.
  let real: string
  try { real = realpathSync(abs) } catch { throw new DiagramError(404, "diagram not found") }
  if (!real.startsWith(realpathSync(root) + sep)) throw new DiagramError(404, "path leaves the repo")
  return readFileSync(real, "utf8")
}
/**
 * The change-graph JSON beside a Markdown diagram (scripts/vr_mermaid.py writes `<stem>.json`, or `<stem>.<n>.json` for a file with
 * several blocks): its path, or undefined. With several the first block's. The .md stays the listed diagram, so it appears once.
 */
export function siblingJson(root: string, rel: string): string | undefined {
  if (!rel.endsWith(".md")) return undefined
  const stem = rel.slice(0, -3), dir = dirname(resolve(root, rel)), base = stem.slice(stem.lastIndexOf("/") + 1)
  let names: string[]
  try { names = readdirSync(dir) } catch { return undefined }
  const mine = (f: string): number => f === base + ".json" ? 0 : (f.startsWith(base + ".") && /^\d+\.json$/.test(f.slice(base.length + 1))) ? Number(f.slice(base.length + 1, -5)) : -1
  const hit = names.filter((f) => mine(f) >= 0).sort((a, b) => mine(a) - mine(b))[0]
  return hit ? stem.slice(0, stem.length - base.length) + hit : undefined
}
/** The slot of the canvas a view goes in: `map` is L0, the flow and sequence views L1, everything else (full, c1-c4) L2. */
const slotOf = (view: string): string => view === "map" ? "L0" : view === "L0" || view === "L1" || view === "L2" ? view : view === "seq" ? "L1" : "L2"

/** The ids of the top-level groups of a graph (the groups that sit in no other group). */
export const topGroups = (g: any): string[] => {
  const ids = new Set<string>((g.groups ?? []).map((x: any) => x.id))
  return (g.groups ?? []).filter((x: any) => !x.parent || !ids.has(x.parent)).map((x: any) => x.id)
}

/** The canvas opens a view with more visible nodes than this with groups folded (about 15 to 20 read at once). */
export const OPEN_BUDGET = 18
/** The layout size the canvas opens a committed diagram with: 12 px text stays at or above ~11 px on screen at a 980 px canvas (zoom >= 0.92). */
export const FIT_WIDTH = 1020
export const FIT_HEIGHT = 760
/** Layouts tried at most while folding more to fit. */
const FIT_TRIES = 14
/** The ids of the groups with nodes in them, biggest (by nodes at any depth) first, that are not inside a folded group nor folded themselves. Pure. */
export function nextFold(g: any, folded: string[]): string | undefined {
  const gmap = new Map<string, any>((g.groups ?? []).map((x: any) => [x.id, x]))
  const chain = (id: string | undefined): string[] => { const c: string[] = []; for (let n = 0; id && gmap.has(id) && n < 100; n++) { c.push(id); id = gmap.get(id).parent } return c }
  const f = new Set(folded)
  const size = new Map<string, number>()
  for (const n of g.nodes ?? []) for (const id of chain(n.group)) size.set(id, (size.get(id) ?? 0) + 1)
  const open = [...size.entries()].filter(([id]) => !f.has(id) && !chain(gmap.get(id)?.parent).some((a) => f.has(a)) && size.get(id)! > 1)
  open.sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
  return open[0]?.[0]
}
/** What the view-kind tab says, from the kind `vr_map list` gives. */
const KIND_TAB: Record<string, string> = { "product-map": "Product map", "crate-map": "Crate map", "crate-calls": "Crate calls", modules: "Modules", calls: "Calls", c1: "Context", c2: "Containers", c3: "Components", c4: "Classes", seq: "Sequence" }
export const kindTab = (d: Diagram, g: any): string => KIND_TAB[d.kind] ?? (g?.view === "map" ? "Product map" : d.kind.replace(/-/g, " ").replace(/^./, (c) => c.toUpperCase()))

/**
 * The top-level groups to fold on open. A `map` folds every top-level group (the areas). Any other view with more than `budget` nodes
 * folds its biggest top-level groups first until the visible nodes (loose nodes plus one box per folded group) fit, or none is left.
 */
export function openFolds(g: any, budget = OPEN_BUDGET): string[] {
  const tops = topGroups(g)
  if (g.view === "map") return tops
  if (!tops.length || (g.nodes ?? []).length <= budget) return []
  const gmap = new Map<string, any>((g.groups ?? []).map((x: any) => [x.id, x]))
  const topOf = (id: string | undefined): string | undefined => { let t: string | undefined; for (let n = 0; id && gmap.has(id) && n < 100; n++) { t = id; id = gmap.get(id).parent } return t }
  const size = new Map<string, number>(tops.map((t) => [t, 0]))
  for (const n of g.nodes) { const t = topOf(n.group); if (t && size.has(t)) size.set(t, size.get(t)! + 1);  }
  const order = [...size.entries()].filter(([, c]) => c > 0).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
  let visible = g.nodes.length
  const fold: string[] = []
  for (const [id, c] of order) { if (visible <= budget) break; fold.push(id); visible -= c - 1 }
  return fold.sort()
}

/**
 * One committed diagram as the canvas takes it: a product-map or other graph file, laid out like a dossier view. The canvas has three
 * slots: a `map` (the whole-repo product map) goes in the L0 slot, `full` views (crate and module maps, call graphs) in the L2 slot, which
 * the structure layout draws with their groups. `tab` names the view kind for the tab. A view with more nodes than OPEN_BUDGET comes with
 * `collapsed` = the groups to fold on open; the canvas asks `collapsedDiagram` for that layout.
 */
/** A failure of openDiagram with the HTTP status the route sends: 404 for a diagram that is not listed or not there, 400 for a file that is not a usable graph. */
export class DiagramError extends Error {
  constructor(public status: number, message: string) { super(message) }
}
export async function openDiagram(root: string, rel: string, listed: Diagram[]): Promise<{ kind: "graph"; title: string; views: Record<string, ViewResult & { collapsed?: string[]; tab?: string }> } | { kind: "mermaid"; title: string; text: string }> {
  const { d, text: raw } = readListed(root, rel, listed)
  let text = raw
  let file = rel
  const sib = siblingJson(root, rel)
  if (sib) { try { text = readInRepo(root, sib); file = sib } catch { /* fall back to the Mermaid text */ } }
  if (!file.endsWith(".json")) {
    const m = text.match(/```mermaid\n([\s\S]*?)```/)
    return { kind: "mermaid", title: d.title, text: (m ? m[1] : text).trim() }
  }
  let g: any
  try { g = JSON.parse(text) } catch (e) { throw new DiagramError(400, "the diagram is not valid JSON: " + (e as Error).message) }
  const out: ViewResult & { collapsed?: string[]; tab?: string } = { file, graph: null, layout: null, issues: checkGraph(g) }
  if (typeof g?.title === "string") out.title = g.title
  out.tab = kindTab(d, g)
  if (!out.issues.length) {
    out.graph = stripDollar(g)
    out.view = g.view
    let fold = openFolds(out.graph)
    // A view that opens folded may be too big to lay out unfolded: then the failure is not reported (layout stays null) and the canvas asks for the folded layout.
    try { out.layout = await layoutGraph(out.graph) } catch (e) { if (!fold.length) out.issues.push({ code: "layout_failed", where: "", message: (e as Error).message }) }
    // Fold more (the biggest open group, nested ones too) until the opening layout fits the readable size, instead of letting Fit shrink the text.
    if (g.view !== "map" && (out.graph.groups ?? []).length) {
      try {
        for (let i = 0; i < FIT_TRIES; i++) {
          const L = fold.length ? (await collapseLoaded(out.graph, g.view, fold)).layout : out.layout
          if (L && L.width <= FIT_WIDTH && L.height <= FIT_HEIGHT) break
          const next = nextFold(out.graph, fold)
          if (!next) break
          fold = [...fold, next].sort()
        }
      } catch { /* keep the folds found so far */ }
    }
    if (fold.length) out.collapsed = fold
  }
  return { kind: "graph", title: d.title, views: { [slotOf(g.view)]: out } }
}

/** A committed diagram with some groups folded to one box each (edges aggregated with counts), and its layout. */
export async function collapsedDiagram(root: string, rel: string, listed: Diagram[], collapsed: string[]): Promise<{ graph: any; layout: any; collapsed: string[] }> {
  const sib = siblingJson(root, rel)
  if (!rel.endsWith(".json") && !sib) throw new DiagramError(400, "not a graph diagram")
  let g: any
  try { const l = readListed(root, rel, listed); g = JSON.parse(sib ? readInRepo(root, sib) : l.text) } catch (e) { if (e instanceof DiagramError) throw e; throw new DiagramError(400, "the diagram is not valid JSON: " + (e as Error).message) }
  return collapseLoaded(g, g.view, collapsed)
}
