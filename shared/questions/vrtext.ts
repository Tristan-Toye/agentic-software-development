// Visual review: the one text-fit rule of a node box. The layout sizes a box from it (vrlayout.ts size()), the page wraps and draws
// the label from it (page.ts interpolates the two functions below), and scripts/vr_graph.py ports the same numbers and algorithm
// (text_fit in that file); references/visual-review/text-fit.fixtures.json pins the three together (shared/test/vrtext.test.ts and
// vr_graph.py --selftest). Change a number here and in vr_graph.py, then check the fixture expectations.
//
// The two functions are shipped verbatim to the page via `.toString()`: keep them self-contained (only VR_TEXT and each other).

/** Pixels per character of the 12 px label font (a deliberate over-estimate), the inset the text keeps from the box sides, the box limits. */
export const VR_TEXT = { charPx: 6.6, pad: 28, inset: 24, minW: 130, maxW: 250, foldedExtra: 36, maxLines: 3 }

/** The width of a node box for a label: grows with the label up to the maximum (a folded box has room for its fold button too). */
export function vrNodeWidth(label: string, folded?: boolean): number {
  const extra = folded ? VR_TEXT.foldedExtra : 0
  const t = String(label == null ? "" : label).length
  return Math.max(VR_TEXT.minW, Math.min(VR_TEXT.maxW + extra, Math.round(t * VR_TEXT.charPx + VR_TEXT.pad + extra)))
}

/**
 * The lines a label is drawn in inside a box `boxW` wide: wrapped on spaces at floor((boxW - inset) / charPx) characters. A stereotype
 * takes one of the maxLines. `clipped` is true when the label needs more lines than are left, or a single word is wider than a line:
 * then `lines` holds what the page draws (the last line ends with an ellipsis); `needed` is the number of lines the label needs.
 */
export function vrFitLabel(label: string, boxW: number, stereotype?: boolean): { lines: string[]; clipped: boolean; needed: number } {
  const w = Math.max(1, Math.floor((boxW - VR_TEXT.inset) / VR_TEXT.charPx))
  const out: string[] = []
  let l = ""
  String(label == null ? "" : label).split(" ").forEach((t) => {
    if ((l + " " + t).trim().length > w && l) { out.push(l); l = t } else l = (l + " " + t).trim()
  })
  if (l) out.push(l)
  const room = VR_TEXT.maxLines - (stereotype ? 1 : 0)
  const wide = out.some((x) => x.length > w)
  const clipped = out.length > room || wide
  const lines = out.slice(0, room)
  if (out.length > room && lines.length) lines[lines.length - 1] = lines[lines.length - 1].replace(/.?$/, "…")
  return { lines, clipped, needed: out.length + (stereotype ? 1 : 0) }
}
