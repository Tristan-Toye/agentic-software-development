// Visual review: the reading order of the walkthrough (VR-03). Pure, so it is tested in bun and also shipped verbatim to the
// page (page.ts interpolates `vrWalkOrder.toString()`): keep it self-contained, with no import and no outside name.

/**
 * The ids of the changed elements (status added, changed or removed: nodes, groups and edges) of one laid-out view, in
 * reading order: the layout layer first (a column when the direction is RIGHT, a row when it is DOWN), then the graph's
 * `order` field (nodes without one come last), then the position inside the layer (top to bottom for columns, left to
 * right for rows), then the other axis, then the id. An edge sorts right after its source node. Elements without a box
 * or a path in the layout are skipped. Exception: a graph whose nodes carry step numbers (the L1 flow) is read by `order` alone,
 * because its lanes stack the steps out of sequence on the page; position only breaks ties there. Never throws on a partial graph.
 */
export function vrWalkOrder(graph: any, layout: any): string[] {
  const boxes: any = (layout && layout.boxes) || {}
  const paths: any = (layout && layout.edges) || {}
  const down = !!layout && layout.direction === "DOWN"
  const ms = (b: any) => (down ? b.y : b.x)
  const me = (b: any) => (down ? b.y + b.h : b.x + b.w)
  const cs = (b: any) => (down ? b.x : b.y)
  const nodes: any[] = (graph && graph.nodes) || []
  // Layers: leaf boxes sorted along the main axis; a box joins the running layer while it starts before that layer's earliest end.
  const leaf = nodes.filter((n: any) => boxes[n.id]).map((n: any) => boxes[n.id]).sort((a: any, b: any) => ms(a) - ms(b))
  const from: number[] = []
  let end = Infinity
  for (const b of leaf) {
    if (!from.length || ms(b) >= end) { from.push(ms(b)); end = me(b) } else end = Math.min(end, me(b))
  }
  const stepped = nodes.some((n: any) => typeof n.order === "number")
  const layerOf = (v: number) => {
    if (stepped) return 0
    let l = 0
    for (let i = 0; i < from.length; i++) if (from[i] <= v + 20) l = i
    return l
  }
  const byId: any = {}
  nodes.forEach((n: any) => { byId[n.id] = n })
  const rows: any[] = []
  const place = (id: string, status: string, b: any, order: any, rank: number) => {
    if (status !== "added" && status !== "changed" && status !== "removed") return
    rows.push({ id, k: [layerOf(ms(b)), typeof order === "number" ? order : Infinity, cs(b), ms(b), rank] })
  }
  const lanes: any = {}
  ;((graph && graph.lanes) || []).forEach((l: any) => { lanes[l.id] = 1 })
  ;((graph && graph.groups) || []).forEach((x: any) => { if (boxes[x.id] && !lanes[x.id]) place(x.id, x.status, boxes[x.id], undefined, 0) })
  nodes.forEach((n: any) => { if (boxes[n.id]) place(n.id, n.status, boxes[n.id], n.order, 0) })
  ;((graph && graph.edges) || []).forEach((e: any) => {
    const p = paths[e.id]
    const s = boxes[e.source]
    if (s) return place(e.id, e.status, s, byId[e.source] && byId[e.source].order, 1)
    if (p && p.length) place(e.id, e.status, { x: p[0][0], y: p[0][1], w: 0, h: 0 }, undefined, 1)
  })
  rows.sort((a: any, b: any) => {
    for (let i = 0; i < a.k.length; i++) if (a.k[i] !== b.k[i]) return a.k[i] < b.k[i] ? -1 : 1
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
  })
  return rows.map((r: any) => r.id)
}
