// Dossier page panes: the open/closed memory of the collapsible panes, their default states, and the split of the dossier's
// rendered markdown into one pane per H2. Pure, so it is tested in bun and also shipped verbatim to the page (page.ts
// interpolates `<fn>.toString()`): each function uses only the others here (all are shipped together), with no import, no outside name, no backtick and no dollar-brace.

/**
 * The remembered open state of one pane for this viewer, or `def` when nothing is stored, the stored value is not ours, or
 * the storage is missing or throws (a private window, blocked site data). Never throws.
 */
export function paneRead(storage: any, item: string, key: string, def: boolean): boolean {
  try {
    if (!storage) return def
    const v = storage.getItem("asd.pane." + item + "." + key)
    return v === "1" ? true : v === "0" ? false : def
  } catch (e) { return def }
}

/** Remember the open state of one pane. Returns whether it was stored; false (and no throw) without a working storage. */
export function paneWrite(storage: any, item: string, key: string, open: boolean): boolean {
  try {
    if (!storage) return false
    storage.setItem("asd.pane." + item + "." + key, open ? "1" : "0")
    return true
  } catch (e) { return false }
}

/**
 * Whether a pane starts open when the viewer never chose. ctx: mode ("run" or "dossier"), needs (anything waits on the
 * owner), reviewing (an open visual-review question or unreviewed elements), fixLive (a fix-deferred run is working),
 * fixWaiting (it waits on the owner). The dossier summary and the decisions are open; history, answered questions, the
 * final reply, the log and the timeline are closed (the timeline of a live run with nothing waiting is the one exception).
 */
export function paneDefault(key: string, ctx: any): boolean {
  const c = ctx || {}
  if (key === "summary" || key === "dossier" || key === "blocked") return true
  if (key === "diagram") return !!c.reviewing
  if (key === "deferredprog") return !!c.fixLive
  if (key === "deferred") return !!c.fixWaiting
  if (key === "timeline") return c.mode === "run" && !c.needs
  return false
}

/** A pane key part from a heading's text: lower case, letters and digits joined by dashes, at most 60 characters. */
export function paneSlug(text: string): string {
  const s = String(text || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60)
  return s || "section"
}

/**
 * The dossier's rendered markdown (the server's escaped html) split at its H2 headings: the `intro` before the first H2
 * (the title, front matter, an opening paragraph) and one `{title, slug, html}` per H2 holding everything up to the next
 * H2. `title` is the plain heading text; `slug` is unique inside the document. With no H2 the whole html is the intro.
 */
export function splitSections(html: string): { intro: string; sections: { title: string; slug: string; html: string }[] } {
  const src = String(html || "")
  const re = /<h2>([\s\S]*?)<\/h2>/g
  const hits: { at: number; end: number; title: string }[] = []
  let m: RegExpExecArray | null
  while ((m = re.exec(src))) {
    const title = m[1].replace(/<[^>]*>/g, "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, "\"").replace(/&#39;/g, "'").replace(/&amp;/g, "&").trim()
    hits.push({ at: m.index, end: m.index + m[0].length, title })
  }
  if (!hits.length) return { intro: src, sections: [] }
  const used: Record<string, number> = {}
  const sections = hits.map((h, i) => {
    const base = paneSlug(h.title)
    used[base] = (used[base] || 0) + 1
    return { title: h.title || "Untitled", slug: used[base] > 1 ? base + "-" + used[base] : base, html: src.slice(h.end, i + 1 < hits.length ? hits[i + 1].at : src.length) }
  })
  return { intro: src.slice(0, hits[0].at), sections }
}
