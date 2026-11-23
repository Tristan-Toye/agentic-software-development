// Visual review: the numbers of a decision flow (view `decision`), before and after a change. Pure, so it is tested in bun and
// also shipped verbatim to the page (page.ts interpolates `vrDecisionStats.toString()`): keep it self-contained, with no import
// and no outside name. The path count is the one of scripts/vr_graph.py (`_path_count`, cap 99): same walk, same cap.

/**
 * The decisions and the start-to-end paths of the graph's before side (everything not `added`) and after side (everything not
 * `removed`), and the error ends the change adds. A decision is a node shaped decision or diamond; an end is a terminal with
 * `terminal: "end"`, an error end one with `outcome: "error"`. A path never visits a node twice, so a retry loop is walked once.
 * Never throws on a partial graph (a graph without exactly one start has 0 paths). Pure.
 */
export function vrDecisionStats(graph: any): { decisions: [number, number]; paths: [number, number]; errorEnds: number } {
  const nodes: any[] = (graph && Array.isArray(graph.nodes) ? graph.nodes : []).filter((n: any) => n && typeof n.id === "string")
  const edges: any[] = graph && Array.isArray(graph.edges) ? graph.edges : []
  const shapeOf = (n: any) => String(n.shape || n.kind || "").toLowerCase()
  const isDecision = (n: any) => shapeOf(n) === "decision" || shapeOf(n) === "diamond"
  const side = (keep: (s: string) => boolean): { decisions: number; paths: number } => {
    const ns = nodes.filter((n: any) => keep(n.status))
    const ids: any = {}
    ns.forEach((n: any) => { ids[n.id] = n })
    const out: any = {}
    ns.forEach((n: any) => { out[n.id] = [] })
    edges.forEach((e: any) => { if (keep(e.status) && ids[e.source] && ids[e.target]) out[e.source].push(e.target) })
    const starts = ns.filter((n: any) => n.terminal === "start")
    let count = 0
    if (starts.length === 1) {
      const stack: any[] = [[starts[0].id, { [starts[0].id]: 1 }]]
      while (stack.length && count < 99) {
        const [cur, seen] = stack.pop()
        if (ids[cur].terminal === "end") { count++; continue }
        for (const t of out[cur]) if (!seen[t]) stack.push([t, Object.assign({ [t]: 1 }, seen)])
      }
    }
    return { decisions: ns.filter(isDecision).length, paths: count }
  }
  const b = side((s: string) => s !== "added"), a = side((s: string) => s !== "removed")
  return {
    decisions: [b.decisions, a.decisions], paths: [b.paths, a.paths],
    errorEnds: nodes.filter((n: any) => n.status === "added" && n.terminal === "end" && n.outcome === "error").length,
  }
}
