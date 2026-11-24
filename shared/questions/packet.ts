// The review packet on the dossier page: scripts/review_packet.py writes five JSON files under
// `<root>/.discovery/review-packet/<ID>/`; this reads them (read-only, cached by size and mtime) and turns each into a compact
// pane model (title, badge counts, tables). `packetModel` and `packetBody` are pure and shipped verbatim to the page
// (page.ts interpolates `<fn>.toString()`): each uses only its arguments, with no import, no outside name, no backtick and no dollar-brace.
// Rules from the review research: a generated sentence with no mechanical anchor is labelled `speculative`; no forced checklist
// (the panes are all collapsed, none is a step the reviewer must tick).
import { readFileSync, readdirSync, statSync } from "node:fs"
import { join } from "node:path"
import { makeVrCache } from "./vrcache.ts"

/** The packet files in page order: file stem, pane key. */
export const PACKET_FILES = ["contract-diff", "criteria-evidence", "reading-order", "invariants", "coupling-delta"] as const
export type PacketName = (typeof PACKET_FILES)[number]

/** The packet directory of a dossier under a repo root. The id is checked by the caller (a plain name), never built from anything else. */
export const packetDir = (root: string, dossier: string): string => join(root, ".discovery", "review-packet", dossier)

/** Whether `<root>` holds at least one packet file for the dossier. */
export function hasPacket(root: string, dossier: string): boolean {
  try { const names = new Set(readdirSync(packetDir(root, dossier))); return PACKET_FILES.some((n) => names.has(n + ".json")) } catch { return false }
}

/** Name, size and mtime of every packet file: the cache key's signature (an edit or a new file is a miss). */
export function packetSignature(dir: string): string {
  const parts: string[] = []
  for (const n of PACKET_FILES) { try { const s = statSync(join(dir, n + ".json")); if (s.isFile()) parts.push(n + ":" + s.size + ":" + Math.round(s.mtimeMs)) } catch { /* absent: no pane */ } }
  return parts.join("|")
}

export interface PacketData { dossier: string; root: string; files: Partial<Record<PacketName, unknown>>; unreadable: string[] }

/** Every packet file that exists and parses; a missing file is absent (no pane), an unparsable one is listed in `unreadable`. Never throws. */
export function readPacket(root: string, dossier: string): PacketData {
  const dir = packetDir(root, dossier); const files: PacketData["files"] = {}; const unreadable: string[] = []
  for (const n of PACKET_FILES) {
    let text: string
    try { text = readFileSync(join(dir, n + ".json"), "utf8") } catch { continue }
    try { const v = JSON.parse(text); if (v && typeof v === "object" && !Array.isArray(v)) files[n] = v; else unreadable.push(n) } catch { unreadable.push(n) }
  }
  return { dossier, root, files, unreadable }
}

/** The cache the route keeps in front of `readPacket` (vrcache.ts: the signature is the sizes and mtimes of the five files). */
export const packetCache = makeVrCache<PacketData>()

/**
 * One packet file as a pane model: {key, title, badge: {text, tone}, notes: [string], sections: [{head, cols, rows}]}. A cell is a
 * string, or {t: text, a: "path:line"} (an anchor, linked by packetBody), or {t: text, spec: true} (a sentence with no mechanical anchor,
 * shown with the word speculative). Returns null when the file carries nothing to show. A skipped producer is one pane with the
 * reason and no table.
 */
export function packetModel(name: string, d: any): any {
  const arr = (x: any): any[] => (Array.isArray(x) ? x.filter((i: any) => i !== null && i !== undefined) : [])
  const str = (x: any): string => (x === null || x === undefined ? "" : String(x))
  const num = (x: any): number => (typeof x === "number" && isFinite(x) ? x : 0)
  const anc = (t: any, a: any): any => (a ? { t: str(t) || str(a), a: str(a) } : str(t))
  const titles: any = { "contract-diff": "Contract diff", "criteria-evidence": "Criteria vs evidence", "reading-order": "Reading order", invariants: "Invariants and pre-mortem", "coupling-delta": "Coupling delta" }
  if (!d || typeof d !== "object" || !titles[name]) return null
  const m: any = { key: "packet:" + name, title: titles[name], badge: null, notes: [], sections: [] }
  if (d.skipped) { m.badge = { text: "skipped", tone: "neutral" }; m.notes.push("skipped: " + str(d.skipped)); return m }
  const s = d.summary || {}
  if (name === "contract-diff") {
    const es = arr(d.entries), cc = arr(d.cannot_confirm)
    const err = num(s.ERR), warn = num(s.WARN)
    m.title = "Contract diff"
    m.badge = { text: es.length + (err ? " · " + err + " ERR" : "") + (warn ? " · " + warn + " WARN" : "") + (cc.length ? " · " + cc.length + " unconfirmed" : ""), tone: err ? "warn" : "neutral" }
    if (es.length) m.sections.push({ head: "Public signature changes", cols: ["severity", "change", "rule", "reason", "at"],
      rows: es.map((e: any) => [str(e.severity) + (e.tier === "cannot-confirm" ? " (cannot confirm)" : ""), str(e.status) + " " + str(e.name || e.id) + (e.before || e.after ? ": " + str(e.before) + " -> " + str(e.after) : ""), str(e.rule), str(e.reason), anc("", e.anchor)]) })
    if (cc.length) m.sections.push({ head: "Cannot confirm", cols: ["id", "reason", "at"], rows: cc.map((c: any) => [str(c.id), str(c.reason), anc("", c.anchor)]) })
    if (arr(d.not_caught).length) m.sections.push({ head: "Not caught by this check", cols: ["limit"], rows: arr(d.not_caught).map((x: any) => [str(x)]) })
    if (!es.length && !cc.length) m.notes.push("No public signature change.")
  } else if (name === "criteria-evidence") {
    const rows = arr(d.rows), no = num(s["no-evidence"]), part = num(s.partial)
    m.badge = { text: num(s.covered) + "/" + (num(s.criteria) || rows.length) + " covered" + (part ? " · " + part + " partial" : "") + (no ? " · " + no + " no evidence" : ""), tone: no || part ? "warn" : "ok" }
    m.sections.push({ head: "", cols: ["#", "status", "criterion", "tests", "evidence", "at"], rows: rows.map((r: any) => {
      const tests = arr(r.tests), failed = tests.filter((t: any) => t.result === "fail").length
      const links = arr(r.links).map((l: any) => str(l.how) + (l.detail ? " " + str(l.detail) : "")).join("; ")
      return [str(r.criterion), str(r.status), str(r.text), tests.length ? tests.length + (failed ? " (" + failed + " failing)" : "") : "0", arr(r.status_reasons).join("; ") || links, anc("", r.anchor)]
    }) })
    const withTests = rows.filter((r: any) => arr(r.tests).length)
    if (withTests.length) m.sections.push({ head: "Tests per criterion", cols: ["#", "test", "result", "found by"], rows: withTests.flatMap((r: any) => arr(r.tests).map((t: any) => [str(r.criterion), anc(str(t.file) + (t.name ? " :: " + str(t.name) : ""), t.file && t.line ? str(t.file) + ":" + t.line : null), str(t.result), str(t.found_by)])) })
  } else if (name === "reading-order") {
    const order = arr(d.order), nl = d.not_listed || {}
    m.badge = { text: num(d.listed_files) + " of " + num(d.total_changed_files) + " files" + (num(nl.count) ? " · " + num(nl.count) + " not listed" : ""), tone: "neutral" }
    order.forEach((g: any) => m.sections.push({ head: str(g.rank) + ". " + str(g.group) + (g.reason ? ": " + str(g.reason) : ""), cols: ["file", "+/-", "signals", "callers", "why"],
      rows: arr(g.files).map((f: any) => [anc(str(f.path), f.path), "+" + num(f.added) + " -" + num(f.deleted), str(num(f.signals)), str(num(f.callers)), str(f.why)]) }))
    if (num(nl.count)) m.notes.push(num(nl.count) + " changed files not listed (" + num(nl.over_cap) + " over the cap, " + num(nl.noise) + " noise" + (nl.noise_rule ? ": " + str(nl.noise_rule) : "") + ").")
    if (d.caution) m.notes.push(str(d.caution))
  } else if (name === "invariants") {
    const items = arr(d.items), spec = items.filter((i: any) => i.speculative || !i.anchor).length
    m.badge = { text: items.length + (spec ? " · " + spec + " speculative" : ""), tone: "neutral" }
    if (d.rule) m.notes.push(str(d.rule))
    m.sections.push({ head: "", cols: ["kind", "statement", "anchor", "check"], rows: items.map((i: any) => [str(i.kind).replace("_", " "), str(i.text),
      i.anchor && !i.speculative ? anc("", i.anchor) : { t: "speculative" + (i.anchor_problem ? " (" + str(i.anchor_problem) + ")" : ""), spec: true }, str(i.check)]) })
  } else {
    const ad = arr(d.added), rm = arr(d.removed), cy = arr(d.new_cycles), lb = arr(d.layer_breaks), fc = arr(d.fan_changes)
    const parts = [ad.length ? "+" + ad.length : "", rm.length ? "-" + rm.length : "", cy.length ? cy.length + " cycle" + (cy.length === 1 ? "" : "s") : "", lb.length ? lb.length + " layer break" + (lb.length === 1 ? "" : "s") : ""].filter(Boolean)
    m.badge = { text: parts.length ? parts.join(" · ") : "no change", tone: cy.length || lb.length ? "warn" : "neutral" }
    if (ad.length) m.sections.push({ head: "New dependencies", cols: ["from", "to", "at"], rows: ad.map((e: any) => [str(e.from), str(e.to), anc("", e.anchor)]) })
    if (rm.length) m.sections.push({ head: "Removed dependencies", cols: ["from", "to", "at"], rows: rm.map((e: any) => [str(e.from), str(e.to), anc("", e.anchor)]) })
    if (cy.length) m.sections.push({ head: "New cycles", cols: ["cycle"], rows: cy.map((c: any) => [arr(c.nodes || c).map(str).join(" -> ")]) })
    if (lb.length) m.sections.push({ head: "Layer breaks", cols: ["from", "to", "layers", "new", "at"], rows: lb.map((e: any) => [str(e.from), str(e.to), str(e.from_layer) + " -> " + str(e.to_layer), e.new ? "new" : "existing", anc("", e.anchor)]) })
    if (fc.length) m.sections.push({ head: "Fan-in and fan-out changes", cols: ["change"], rows: fc.map((x: any) => [typeof x === "string" ? x : Object.keys(x || {}).map((k) => k + "=" + str(x[k])).join(" ")]) })
    if (d.layers && d.layers.status === "skipped") m.notes.push("Layer check skipped.")
    if (d.coverage) m.notes.push(str(d.coverage))
  }
  return m
}

/**
 * The body of one packet pane as DOM: notes, then each section as a small table. `h` is the page's element builder, `root` the repo
 * root. An anchor `path:line` becomes a link that opens the editor at that line (vscode://file/<root>/<path>:<line>); anything else
 * (a test id) is plain code. A `spec` cell shows the word speculative in a chip. Text is set as text content, never parsed.
 */
export function packetBody(h: any, m: any, root: string): any[] {
  const out: any[] = []
  const cell = (c: any): any => {
    if (c && typeof c === "object") {
      if (c.spec) return h("span", { class: "chip warn", title: "no mechanical anchor: a guess, not a finding" }, [c.t])
      const mt = /^([^\s:]+?)(?::(\d+))?$/.exec(c.a)
      const label = c.t || c.a
      const ok = mt && root && (mt[2] || /[\/.]/.test(mt[1]))
      return ok ? h("a", { class: "pk-a", href: "vscode://file/" + root.replace(/\/+$/, "") + "/" + mt[1] + (mt[2] ? ":" + mt[2] : ""), title: "open " + c.a + " in the editor" }, [label]) : h("code", { class: "pk-a" }, [label])
    }
    return c
  }
  ;(m.notes || []).forEach((n: string) => out.push(h("p", { class: "small mut" }, [n])))
  ;(m.sections || []).forEach((s: any) => {
    if (s.head) out.push(h("div", { class: "pk-h" }, [s.head]))
    out.push(h("div", { class: "pk-wrap" }, [h("table", { class: "pk" }, [
      h("thead", {}, [h("tr", {}, s.cols.map((c: string) => h("th", {}, [c])))]),
      h("tbody", {}, s.rows.map((r: any[]) => h("tr", {}, r.map((c: any) => h("td", {}, [cell(c)])))))])]))
  })
  return out
}
