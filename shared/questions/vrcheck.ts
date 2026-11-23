// Visual review: a geometry check over a computed layout (the JSON boxes, labels and edge points of vrlayout.ts, not the SVG).
// The idea comes from diagram-design's verify_geometry.py (cathrynlavery/diagram-design, which checks the SVG); no code is taken from it.
// Pure: a layout and its graph in, a list of typed issues out. Used by the server (a warning beside the view) and by the tests, which
// assert that the fixtures lay out clean.
import type { Box, Issue, Layout } from "./vrlayout.ts"
import { vrFitLabel } from "./vrtext.ts"

/** Overlaps smaller than this many pixels in either direction are not reported (ELK pads boxes by whole pixels). */
const TOL = 2

const inter = (a: Box, b: Box): boolean => a.x + TOL < b.x + b.w && b.x + TOL < a.x + a.w && a.y + TOL < b.y + b.h && b.y + TOL < a.y + a.h

/** Whether the segment p-q (horizontal or vertical, or any line) passes through the inside of the box (shrunk by TOL on every side). */
export function segmentHitsBox(p: [number, number], q: [number, number], b: Box): boolean {
  const x0 = b.x + TOL, x1 = b.x + b.w - TOL, y0 = b.y + TOL, y1 = b.y + b.h - TOL
  if (x1 <= x0 || y1 <= y0) return false
  // Liang-Barsky clipping of the segment against the shrunk box.
  let t0 = 0, t1 = 1
  const dx = q[0] - p[0], dy = q[1] - p[1]
  for (const [pp, qq] of [[-dx, p[0] - x0], [dx, x1 - p[0]], [-dy, p[1] - y0], [dy, y1 - p[1]]] as [number, number][]) {
    if (pp === 0) { if (qq < 0) return false; continue }
    const r = qq / pp
    if (pp < 0) { if (r > t1) return false; if (r > t0) t0 = r } else { if (r < t0) return false; if (r < t1) t1 = r }
  }
  return t0 < t1
}

/**
 * The problems of a layout:
 * - label_clipped: a node label needs more lines than its box draws (see vrFitLabel); `where` is the node id.
 * - node_overlap: two node boxes overlap (`where` is "a,b").
 * - label_overlap: an edge label's box overlaps a node box or another edge label (`where` is "edge,node" or "edge,edge").
 * - edge_through_node: an edge segment passes through the box of a node that is not its source or target, nor inside one of them
 *   when the end is a group (`where` is "edge,node"). Not checked for a sequence diagram, whose arrows are straight lines by design.
 * Never throws on a partial layout: an element with no box is skipped.
 */
export function checkLayout(graph: any, layout: Layout | null | undefined): Issue[] {
  const out: Issue[] = []
  if (!graph || !layout) return out
  const boxes = layout.boxes ?? {}
  const nodes: any[] = (graph.nodes ?? []).filter((n: any) => boxes[n.id])
  // A sequence diagram's messages and notes are arrow and note text (seqLayout), not wrapped into a node box.
  for (const n of nodes) {
    const f = vrFitLabel(String(n.label ?? ""), boxes[n.id].w, !!n.stereotype, graph.view)
    if (f.clipped) out.push({ code: "label_clipped", where: n.id, message: `the label of ${n.id} needs ${f.needed} lines or a wider box; the box draws ${f.lines.length}` })
  }
  for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
    if (inter(boxes[nodes[i].id], boxes[nodes[j].id])) out.push({ code: "node_overlap", where: nodes[i].id + "," + nodes[j].id, message: `the boxes of ${nodes[i].id} and ${nodes[j].id} overlap` })
  }
  const labels = Object.entries(layout.labels ?? {})
  for (const [eid, lb] of labels) {
    for (const n of nodes) if (inter(lb, boxes[n.id])) out.push({ code: "label_overlap", where: eid + "," + n.id, message: `the label of edge ${eid} overlaps the box of ${n.id}` })
  }
  for (let i = 0; i < labels.length; i++) for (let j = i + 1; j < labels.length; j++) {
    if (inter(labels[i][1], labels[j][1])) out.push({ code: "label_overlap", where: labels[i][0] + "," + labels[j][0], message: `the labels of edges ${labels[i][0]} and ${labels[j][0]} overlap` })
  }
  if (graph.view !== "seq") {
    // Parent chain, so an edge that ends at a group may run inside that group.
    const parent: Record<string, string | undefined> = {}
    for (const g of graph.groups ?? []) parent[g.id] = g.parent
    for (const n of graph.nodes ?? []) parent[n.id] = n.group ?? n.lane
    const inside = (id: string, anc: string): boolean => { for (let p: string | undefined = parent[id], k = 0; p && k < 50; p = parent[p], k++) if (p === anc) return true; return false }
    for (const e of graph.edges ?? []) {
      const pts = layout.edges?.[e.id]
      if (!pts || pts.length < 2) continue
      for (const n of nodes) {
        if (n.id === e.source || n.id === e.target || inside(n.id, e.source) || inside(n.id, e.target)) continue
        const b = boxes[n.id]
        if (pts.some((q, i) => i > 0 && segmentHitsBox(pts[i - 1], q, b))) out.push({ code: "edge_through_node", where: e.id + "," + n.id, message: `edge ${e.id} passes through the box of ${n.id}` })
      }
    }
  }
  return out
}
