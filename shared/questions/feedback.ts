// Unified feedback control of the dossier page: the local draft (viewed marks and notes kept in the viewer's browser until
// the one "Send review" submit), the "N of M viewed" count and the payload of the submit. Pure, so it is tested in bun and also
// shipped verbatim to the page (page.ts interpolates `<fn>.toString()`): each function uses only the others here (all are
// shipped together), with no import, no outside name, no backtick and no dollar-brace.
//
// Shapes. saved: what review.json holds, {notes: [{anchor, verdict, comment, blocking}], viewed: [anchor]} (either may be
// missing). draft (st): {drafts: {anchor: {verdict, comment, blocking}}, viewed: {anchor: true|false}}. A draft verdict of ""
// with no comment means "no feedback": it clears a saved note. A viewed entry is the viewer's intent, so false un-views.

/** An empty draft. */
export function fbEmpty(): any { return { drafts: {}, viewed: {} } }

/** The draft kept for one dossier in `storage`, or an empty one when none is stored, it is not ours, or the storage is missing or throws. Never throws. */
export function fbRead(storage: any, item: string): any {
  try {
    if (!storage) return fbEmpty()
    const o = JSON.parse(storage.getItem("asd.fb." + item) || "null")
    if (!o || typeof o !== "object" || !o.drafts || typeof o.drafts !== "object" || !o.viewed || typeof o.viewed !== "object") return fbEmpty()
    return { drafts: o.drafts, viewed: o.viewed }
  } catch (e) { return fbEmpty() }
}

/** Keep the draft (removing the stored item when it is empty). Returns whether it was stored; false (and no throw) without a working storage. */
export function fbWrite(storage: any, item: string, st: any): boolean {
  try {
    if (!storage) return false
    if (!Object.keys(st.drafts).length && !Object.keys(st.viewed).length) storage.removeItem("asd.fb." + item)
    else storage.setItem("asd.fb." + item, JSON.stringify(st))
    return true
  } catch (e) { return false }
}

/** Set (or, with verdict "" and no comment, drop) the draft note of one anchor. patch: {verdict, comment, blocking}; missing fields keep their draft value. */
export function fbSetNote(st: any, anchor: string, patch: any): any {
  const old = st.drafts[anchor] || { verdict: "", comment: "", blocking: false }
  const n = { verdict: patch.verdict !== undefined ? String(patch.verdict) : old.verdict, comment: patch.comment !== undefined ? String(patch.comment) : old.comment, blocking: patch.blocking !== undefined ? !!patch.blocking : old.blocking }
  st.drafts[anchor] = n
  return st
}

/** Mark one anchor viewed (true) or not viewed (false) in the draft. */
export function fbSetViewed(st: any, anchor: string, on: boolean): any {
  st.viewed[anchor] = !!on
  return st
}

/** The note the viewer sees on an anchor: the draft when there is one, else the saved one, else an empty note. `draft` says which. */
export function fbNote(saved: any, st: any, anchor: string): any {
  const d = st.drafts[anchor]
  if (d) return { verdict: d.verdict, comment: d.comment, blocking: !!d.blocking, draft: true }
  const notes = (saved && saved.notes) || []
  for (let i = 0; i < notes.length; i++) if (notes[i].anchor === anchor) return { verdict: notes[i].verdict, comment: notes[i].comment || "", blocking: !!notes[i].blocking, draft: false }
  return { verdict: "", comment: "", blocking: false, draft: false }
}

/** Whether an anchor counts as viewed: the draft's choice when there is one, else review.json's. */
export function fbIsViewed(saved: any, st: any, anchor: string): boolean {
  if (st.viewed[anchor] !== undefined) return !!st.viewed[anchor]
  const v = (saved && saved.viewed) || []
  return v.indexOf(anchor) >= 0
}

/** "N of M viewed" over the anchors the page offers feedback on. No forced checklist: the count is information only. */
export function fbCounts(anchors: string[], saved: any, st: any): { viewed: number; total: number; text: string } {
  let n = 0
  for (let i = 0; i < anchors.length; i++) if (fbIsViewed(saved, st, anchors[i])) n++
  return { viewed: n, total: anchors.length, text: n + " of " + anchors.length + " viewed" }
}

/** What "Send review" would send, or null when the draft holds nothing that differs from review.json. */
export function fbPayload(saved: any, st: any): any {
  const notes: any[] = [], add: string[] = [], rem: string[] = []
  const sv = (saved && saved.viewed) || []
  for (const a in st.drafts) {
    const d = st.drafts[a], was = fbNote(saved, { drafts: {}, viewed: {} }, a)
    if (!d.verdict && !String(d.comment || "").trim()) { if (was.verdict) notes.push({ anchor: a, verdict: "clear" }); continue }
    const verdict = d.verdict || "comment"
    if (was.verdict === verdict && was.comment === String(d.comment || "").trim() && was.blocking === !!d.blocking) continue
    notes.push({ anchor: a, verdict: verdict, comment: String(d.comment || "").trim(), blocking: !!d.blocking })
  }
  for (const a in st.viewed) {
    const had = sv.indexOf(a) >= 0
    if (st.viewed[a] && !had) add.push(a)
    else if (!st.viewed[a] && had) rem.push(a)
  }
  return notes.length || add.length || rem.length ? { notes: notes, viewed_add: add, viewed_remove: rem } : null
}

/** How many changes "Send review" would send (notes plus viewed marks). */
export function fbPending(saved: any, st: any): number {
  const p = fbPayload(saved, st)
  return p ? p.notes.length + p.viewed_add.length + p.viewed_remove.length : 0
}

/** After a successful submit: the draft entries the server now holds are dropped; anything the viewer typed since stays. `sent` is the payload that was sent. */
export function fbSettle(st: any, sent: any): any {
  for (let i = 0; i < sent.notes.length; i++) delete st.drafts[sent.notes[i].anchor]
  for (let i = 0; i < sent.viewed_add.length; i++) delete st.viewed[sent.viewed_add[i]]
  for (let i = 0; i < sent.viewed_remove.length; i++) delete st.viewed[sent.viewed_remove[i]]
  return st
}
