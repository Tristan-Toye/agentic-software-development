// Visual review: the one text-fit rule of a node box. The layout sizes a box from it (vrlayout.ts size()), the page wraps and draws
// the label from it (page.ts interpolates the two functions below), and scripts/vr_graph.py ports the same numbers and algorithm
// (text_fit in that file); references/visual-review/text-fit.fixtures.json pins the three together (shared/test/vrtext.test.ts and
// vr_graph.py --selftest). Change a number here and in vr_graph.py, then check the fixture expectations.
//
// The two functions are shipped verbatim to the page via `.toString()`: keep them self-contained (only VR_TEXT and each other).

/** Pixels per character of the 12 px label font (a deliberate over-estimate), the inset the text keeps from the box sides, the box limits. */
export const VR_TEXT = { charPx: 6.6, pad: 28, inset: 24, minW: 130, maxW: 250, foldedExtra: 36, maxLines: 3, seqMaxLines: 5,
  // Shape rooms: the text of a shape sits in [factor of the box width] minus [inset] px (a rectangle: 1 and `inset`). A diamond holds text in its
  // middle 60%, a circle in 70%, a stadium and a parallelogram lose their round or slanted ends. Shapes not listed use the rectangle's.
  shapeRoom: { diamond: [0.6, 0], circle: [0.7, 0], terminal: [1, 36], io: [1, 44] } as Record<string, number[]>,
  /** The slant of a parallelogram (io) node: how far its top edge is shifted right and its bottom edge left, in px. The layout and the page use this one number. */
  ioSkew: 12,
  /** The narrowest and widest a diamond (width) or circle (diameter) grows to find room for its label; the extra width of a stadium or parallelogram. */
  diamondW: [160, 300], circleD: [72, 150], terminalExtra: 24 }

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
 * maxLines; `shape` (optional) narrows the room, see shapeRoom (a sequence diagram's message or note box may grow to seqMaxLines, see vrBoxHeight). `clipped` is true only when the label needs more lines than are left: then `lines` holds what the page draws (the last
 * line ends with an ellipsis); `needed` is the number of lines the label needs, the stereotype's included.
 */
export function vrFitLabel(label: string, boxW: number, stereotype?: boolean, view?: string, shape?: string): { lines: string[]; clipped: boolean; needed: number } {
  if (shape === "junction") return { lines: [], clipped: false, needed: 0 } // a dot draws no text (its label is the title)
  const sr = (shape && VR_TEXT.shapeRoom[shape]) || [1, VR_TEXT.inset]
  const w = Math.max(1, Math.floor((boxW * sr[0] - sr[1]) / VR_TEXT.charPx))
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

/**
 * The width of a node box for a label drawn in `shape` (the layout sizes shaped nodes from it; the page gets the box). A diamond or circle grows
 * from its minimum until the label needs no more than maxLines lines, or to its maximum; a stadium and a parallelogram are a plain box plus room
 * for their ends; a junction is a dot. Any other shape is the plain box. Not shipped to the page.
 */
export function vrShapeWidth(label: string, shape?: string, folded?: boolean): number {
  const base = vrNodeWidth(label, folded)
  if (shape === "terminal" || shape === "io") return base + VR_TEXT.terminalExtra
  if (shape === "junction") return 14
  const [lo, hi] = shape === "diamond" ? VR_TEXT.diamondW : shape === "circle" ? VR_TEXT.circleD : [0, 0]
  if (!hi) return base
  for (let w = lo; w < hi; w += 4) if (vrFitLabel(label, w, false, undefined, shape).needed <= VR_TEXT.maxLines) return w
  return hi
}
