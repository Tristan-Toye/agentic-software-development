// Visual review: the one text-fit rule of a node box. The layout sizes a box from it (vrlayout.ts size()), the page wraps and draws
// the label from it (page.ts interpolates the two functions below), and scripts/vr_graph.py ports the same numbers and algorithm
// (text_fit in that file); references/visual-review/text-fit.fixtures.json pins the three together (shared/test/vrtext.test.ts and
// vr_graph.py --selftest). Change a number here and in vr_graph.py, then check the fixture expectations.
//
// The two functions are shipped verbatim to the page via `.toString()`: keep them self-contained (only VR_TEXT and each other).

/** Pixels per character of the 12 px label font (a deliberate over-estimate), the inset the text keeps from the box sides, the box limits. */
export const VR_TEXT = { charPx: 6.6, pad: 28, inset: 24, minW: 130, maxW: 250, foldedExtra: 36, maxLines: 3, seqMaxLines: 5 }

/** The width of a node box for a label: grows with the label up to the maximum (a folded box has room for its fold button too). */
export function vrNodeWidth(label: string, folded?: boolean): number {
  const extra = folded ? VR_TEXT.foldedExtra : 0
  const t = String(label == null ? "" : label).length
  return Math.max(VR_TEXT.minW, Math.min(VR_TEXT.maxW + extra, Math.round(t * VR_TEXT.charPx + VR_TEXT.pad + extra)))
}

/**
 * The lines a label is drawn in inside a box `boxW` wide: wrapped on spaces (a newline counts as one) at floor((boxW - inset) / charPx)
 * characters. A word wider than a line is broken, never rejected: after the last of / : . _ - that leaves a piece of at least a third
 * of the line, else at the line width (a file:line anchor or a long identifier is a normal label). A stereotype takes one of the
 * maxLines (a sequence diagram's message or note box may grow to seqMaxLines, see vrBoxHeight). `clipped` is true only when the label needs more lines than are left: then `lines` holds what the page draws (the last
 * line ends with an ellipsis); `needed` is the number of lines the label needs, the stereotype's included.
 */
export function vrFitLabel(label: string, boxW: number, stereotype?: boolean, view?: string): { lines: string[]; clipped: boolean; needed: number } {
  const w = Math.max(1, Math.floor((boxW - VR_TEXT.inset) / VR_TEXT.charPx))
  const out: string[] = []
  let l = ""
  String(label == null ? "" : label).split(/[ \n]/).forEach((t) => {
    if ((l + " " + t).trim().length > w && l) { out.push(l); l = t } else l = (l + " " + t).trim()
    while (l.length > w) {
      let cut = w
      for (let i = w; i >= Math.ceil(w / 3); i--) if ("/:._-".indexOf(l.charAt(i - 1)) >= 0) { cut = i; break }
      out.push(l.slice(0, cut))
      l = l.slice(cut)
    }
  })
  if (l) out.push(l)
  const room = (view === "seq" ? VR_TEXT.seqMaxLines : VR_TEXT.maxLines) - (stereotype ? 1 : 0)
  const clipped = out.length > room
  const lines = out.slice(0, room)
  if (clipped && lines.length) lines[lines.length - 1] = lines[lines.length - 1].replace(/.?$/, "…")
  return { lines, clipped, needed: out.length + (stereotype ? 1 : 0) }
}

/** The height of a box that draws `lines` lines of label (15 px each from 19 px down): never below `base`, the box's usual height. */
export function vrBoxHeight(lines: number, base: number): number {
  return Math.max(base, 17 + 15 * lines)
}
