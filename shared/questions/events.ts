// Change notifications for the dashboard's Server-Sent Events stream. The store files stay the source of truth.
export type Change = "questions" | "runs" | "stubs" | "scan"
type Sub = (what: Change[]) => void
const subs = new Set<Sub>()
let pending = new Set<Change>()
let timer: ReturnType<typeof setTimeout> | undefined

export function subscribe(fn: Sub): () => void {
  subs.add(fn)
  return () => void subs.delete(fn)
}
/** Announce a change; bursts within 100 ms are sent as one event. */
export function emitChange(what: Change): void {
  pending.add(what)
  if (timer) return
  timer = setTimeout(() => {
    const out = [...pending]
    pending = new Set()
    timer = undefined
    for (const s of subs) { try { s(out) } catch { /* a dead stream */ } }
  }, 100)
}
