import { existsSync, readdirSync, readFileSync } from "node:fs"
import { join } from "node:path"

// arch_find: the diagram content that bears on a symbol, a label or a source path, read from a target
// repo's docs/architecture/*.json (the graph sources, scripts/vr_mermaid.py's input) instead of whole
// .md/.json files. Plain JSON reads, no layout, no state. A JSON that is not a graph is skipped.

export const MAX_MATCHES = 40
const MAX_MEMBERS = 12
const MAX_ANCHORS = 6
const MAX_NEIGHBOURS = 8
const MAX_OUTLINE_LINES = 80
const LABEL_CHARS = 120

type Ev = { tier?: string; path?: string; line?: number; note?: string }
type Node = { id: string; label?: string; kind?: string; group?: string; lane?: string; seq?: { to?: string }; stereotype?: string; members?: unknown[]; evidence?: Ev[] }
type Edge = { source?: string; target?: string; label?: string; kind?: string }
type Graph = { stem: string; title: string; nodes: Node[]; edges: Edge[]; lanes: { id: string; label?: string }[] }

const clip = (s: unknown, n = LABEL_CHARS) => {
  const t = String(s ?? "").replace(/\s+/g, " ").trim()
  return t.length > n ? t.slice(0, n - 1) + "…" : t
}

/** The stem of a diagram file: `seq-conversation-compaction.2` for `seq-conversation-compaction.2.json`. */
const stemOf = (file: string) => file.replace(/\.json$/, "")

/** Every graph in dir/docs/architecture, in file-name order; unreadable and non-graph JSON files are skipped. */
export function loadGraphs(root: string, diagram?: string): { graphs: Graph[]; error?: string } {
  const dir = join(root, "docs", "architecture")
  if (!existsSync(dir)) return { graphs: [], error: `arch_find: no docs/architecture/ under ${root}` }
  const want = diagram?.trim().replace(/\.(json|md)$/, "")
  const graphs: Graph[] = []
  for (const file of readdirSync(dir).filter((f) => f.endsWith(".json")).sort()) {
    const stem = stemOf(file)
    // `seq-x` selects `seq-x` and its blocks `seq-x.1`, `seq-x.2`.
    if (want && stem !== want && !stem.startsWith(want + ".")) continue
    let g: any
    try {
      g = JSON.parse(readFileSync(join(dir, file), "utf8"))
    } catch {
      continue
    }
    if (!g || typeof g !== "object" || !Array.isArray(g.nodes)) continue
    graphs.push({
      stem,
      title: clip(g.title ?? stem),
      nodes: g.nodes.filter((n: any) => n && typeof n.id === "string"),
      edges: Array.isArray(g.edges) ? g.edges : [],
      lanes: Array.isArray(g.lanes) ? g.lanes : [],
    })
  }
  return { graphs }
}

/** Match strength, lower is better; undefined = no match. */
function rank(n: Node, q: string): number | undefined {
  const label = (n.label ?? "").toLowerCase()
  const id = n.id.toLowerCase()
  const paths = (n.evidence ?? []).map((e) => (e.path ?? "").toLowerCase()).filter(Boolean)
  if (label === q || id === q || id.endsWith(":" + q)) return 0
  if (q.includes("/") ? paths.some((p) => p === q || p.endsWith("/" + q)) : false) return 1
  if (label.includes(q) || id.includes(q)) return 2
  if (paths.some((p) => p.includes(q))) return 3
  if ((n.members ?? []).some((m) => String(m).toLowerCase().includes(q))) return 4
  return undefined
}

function describe(g: Graph, n: Node, adj: Map<string, string[]>): string[] {
  const out = [`${g.stem} · ${n.id} · ${n.kind ?? "?"} · ${clip(n.label)}`]
  if (n.kind === "message" && n.lane && n.seq?.to) {
    const nm = (id: string) => clip(g.lanes.find((l) => l.id === id)?.label ?? id, 40)
    out.push(`  lanes: ${nm(n.lane)} -> ${nm(n.seq.to)}`)
  }
  const src = (n.evidence ?? []).filter((e) => e.tier === "source" && e.path)
  if (src.length) {
    const shown = src.slice(0, MAX_ANCHORS).map((e) => (e.line ? `${e.path}:${e.line}` : e.path))
    out.push(`  src: ${shown.join(", ")}${src.length > MAX_ANCHORS ? ` (+${src.length - MAX_ANCHORS} more)` : ""}`)
  }
  if (n.stereotype) out.push(`  stereotype: ${clip(n.stereotype, 40)}`)
  const members = (n.members ?? []).map((m) => clip(m, 80))
  if (members.length) out.push(`  members: ${members.slice(0, MAX_MEMBERS).join("; ")}${members.length > MAX_MEMBERS ? ` (+${members.length - MAX_MEMBERS} more)` : ""}`)
  const nb = adj.get(n.id) ?? []
  if (nb.length) out.push(`  links: ${nb.slice(0, MAX_NEIGHBOURS).join(" | ")}${nb.length > MAX_NEIGHBOURS ? ` (+${nb.length - MAX_NEIGHBOURS} more)` : ""}`)
  return out
}

/** The compact text for `query` (symbol, label substring or repo path), optionally inside one diagram. */
export function archFind(query: string, diagram: string | undefined, root: string): string {
  const { graphs, error } = loadGraphs(root, diagram)
  if (error) return error
  const q = (query ?? "").trim().toLowerCase()
  if (!graphs.length) return diagram ? `arch_find: no diagram "${diagram}" in docs/architecture` : "arch_find: docs/architecture has no graph JSON"
  if (!q) return diagram ? outline(graphs) : index(graphs)

  const hits: { g: Graph; n: Node; r: number; at: number }[] = []
  for (const g of graphs) g.nodes.forEach((n, at) => { const r = rank(n, q); if (r !== undefined) hits.push({ g, n, r: r + (n.kind === "note" ? 0.5 : 0), at }) })
  if (!hits.length) return `arch_find: nothing in ${graphs.length} diagram(s) matches "${query}"`
  hits.sort((a, b) => a.r - b.r || (a.g.stem < b.g.stem ? -1 : a.g.stem > b.g.stem ? 1 : 0) || a.at - b.at)
  const shown = hits.slice(0, MAX_MATCHES)

  // Neighbours, only for the shown nodes: one pass over each graph's edges.
  const lines: string[] = []
  const perGraph = new Map<Graph, Set<string>>()
  for (const h of shown) (perGraph.get(h.g) ?? perGraph.set(h.g, new Set()).get(h.g)!).add(h.n.id)
  const adjOf = new Map<Graph, Map<string, string[]>>()
  for (const [g, ids] of perGraph) {
    const byId = new Map(g.nodes.map((n) => [n.id, n]))
    const adj = new Map<string, string[]>()
    const add = (id: string, s: string) => (adj.get(id) ?? adj.set(id, []).get(id)!).push(s)
    const lab = (id?: string) => clip(byId.get(id ?? "")?.label ?? id, 60)
    for (const e of g.edges) {
      const el = e.label ? `[${clip(e.label, 40)}] ` : ""
      if (e.source && ids.has(e.source)) add(e.source, `-> ${el}${lab(e.target)}`)
      if (e.target && ids.has(e.target)) add(e.target, `<- ${el}${lab(e.source)}`)
    }
    adjOf.set(g, adj)
  }
  for (const h of shown) lines.push(...describe(h.g, h.n, adjOf.get(h.g)!))
  if (hits.length > shown.length) lines.push(`(${hits.length - shown.length} more match(es) dropped; pass \`diagram\` or a longer query to narrow)`)
  lines.unshift(`arch_find "${query}": ${hits.length} match(es) in ${new Set(hits.map((h) => h.g.stem)).size} diagram(s)`)
  return lines.join("\n")
}

/** No query, no diagram: what exists. */
function index(graphs: Graph[]): string {
  const rows = graphs.map((g) => `${g.stem} · ${g.nodes.length} nodes · ${g.title}`)
  return [`arch_find: ${graphs.length} diagram(s); pass \`diagram\` for an outline or \`query\` to search`, ...rows.slice(0, 120), ...(rows.length > 120 ? [`(+${rows.length - 120} more)`] : [])].join("\n")
}

/** One screen: title, lanes or groups, node labels. */
function outline(graphs: Graph[]): string {
  const out: string[] = []
  for (const g of graphs) {
    out.push(`${g.stem} · ${g.title} · ${g.nodes.length} nodes, ${g.edges.length} edges`)
    if (g.lanes.length) out.push(`  lanes: ${g.lanes.map((l) => clip(l.label ?? l.id, 40)).join(" | ")}`)
    const isSeq = g.lanes.length > 0
    if (isSeq) {
      const msgs = g.nodes.filter((n) => n.kind === "message")
      const lane = (id?: string) => clip(g.lanes.find((l) => l.id === id)?.label ?? id ?? "?", 24)
      msgs.slice(0, 40).forEach((n, i) => out.push(`  ${i + 1}. ${lane(n.lane)} -> ${lane(n.seq?.to)}: ${clip(n.label, 90)}`))
      if (msgs.length > 40) out.push(`  (+${msgs.length - 40} more messages)`)
      continue
    }
    const groupLabel = (id: string) => clip(g.nodes.find((n) => n.id === id)?.label ?? id, 40)
    const buckets = new Map<string, string[]>()
    for (const n of g.nodes) {
      if (n.kind === "group") continue
      const k = n.group ? groupLabel(n.group) : ""
      ;(buckets.get(k) ?? buckets.set(k, []).get(k)!).push(clip(n.label ?? n.id, 40))
    }
    for (const [k, labels] of buckets) out.push(`  ${k ? k + ": " : ""}${labels.slice(0, 30).join(", ")}${labels.length > 30 ? ` (+${labels.length - 30} more)` : ""}`)
  }
  return out.slice(0, MAX_OUTLINE_LINES * Math.max(1, graphs.length)).join("\n")
}
