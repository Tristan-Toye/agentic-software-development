// A small cache for the computed visual-review of one dossier, kept in front of `visualReview` and `collapsedView` by server.ts
// (their own file, vrlayout.ts, stays as it is). The key is the dossier directory plus the size and mtime of every file that
// feeds the result, so an edit, a new draft or a removed one is a miss; the same request while one runs waits for it.
import { existsSync, readdirSync, statSync } from "node:fs"
import { join } from "node:path"

/** Results held at most (least recently used goes first). */
export const VR_CACHE_MAX = 24

/**
 * The signature of a dossier directory's graph files: name, size and mtime of l0/l1/l2/decision.json and every file in arch/
 * and seq-preview/, plus whether review.json exists (the result carries `hasReview`). The content of review.json is left out on
 * purpose: a verdict changes it, not the layouts (the callers read it fresh).
 */
export function vrSignature(dir: string): string {
  const parts: string[] = []
  const one = (rel: string): void => { try { const s = statSync(join(dir, rel)); if (s.isFile()) parts.push(rel + ":" + s.size + ":" + Math.round(s.mtimeMs)) } catch { /* absent */ } }
  for (const f of ["l0.json", "l1.json", "l2.json", "decision.json"]) one(f)
  for (const sub of ["arch", "seq-preview"]) {
    try { for (const f of readdirSync(join(dir, sub)).sort()) if (f.endsWith(".json")) one(sub + "/" + f) } catch { /* absent */ }
  }
  parts.push(existsSync(join(dir, "review.json")) ? "review" : "no-review")
  return parts.join("|")
}

export interface VrCache<T> {
  /** The cached value for `key` under `sig`, else the one computing it, else `compute()` (stored when it resolves). A rejection is never stored. */
  get(key: string, sig: string, compute: () => Promise<T>): Promise<T>
  stats(): { hits: number; misses: number; size: number }
  clear(): void
}

export function makeVrCache<T>(max = VR_CACHE_MAX): VrCache<T> {
  const done = new Map<string, { sig: string; value: T }>()
  const running = new Map<string, { sig: string; p: Promise<T> }>()
  let hits = 0, misses = 0
  return {
    get(key, sig, compute) {
      const hit = done.get(key)
      if (hit && hit.sig === sig) { done.delete(key); done.set(key, hit); hits++; return Promise.resolve(hit.value) }
      const run = running.get(key)
      if (run && run.sig === sig) { hits++; return run.p }
      misses++
      const p = compute().then((value) => {
        if (running.get(key)?.p === p) {
          done.delete(key); done.set(key, { sig, value })
          while (done.size > max) done.delete(done.keys().next().value as string)
        }
        return value
      }).finally(() => { if (running.get(key)?.p === p) running.delete(key) })
      running.set(key, { sig, p })
      return p
    },
    stats: () => ({ hits, misses, size: done.size }),
    clear() { done.clear(); running.clear(); hits = 0; misses = 0 },
  }
}
