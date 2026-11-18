// The dashboard's 30 s rescans (runs.ts buildRuns, stubs.ts buildStubs) read hundreds of files synchronously: every
// transcript's stat and head, every dossier, every runner file. On a saturated disk that held the event loop for tens of
// seconds and lagwatch killed the server. The server turns on a worker thread for them (enableScanWorker), so a slow
// rescan only delays the next view, never a request. Without it (CLI, tests) they run in-thread as before.
import type { Question } from "./store.ts"

export type ScanKind = "runs" | "stubs"
type Pending = { resolve: (v: any) => void; reject: (e: Error) => void }

let enabled = false
let worker: Worker | null = null
let seq = 0
const pending = new Map<number, Pending>()

/** Run the rescans on a worker thread from now on (server only). */
export function enableScanWorker(): void { enabled = true }

function failAll(e: Error): void {
  for (const p of pending.values()) p.reject(e)
  pending.clear()
  worker?.terminate()
  worker = null   // the next scan starts a fresh one
}

function getWorker(): Worker {
  if (worker) return worker
  const w = new Worker(new URL("./scanworker.ts", import.meta.url).href)
  w.onmessage = (ev: MessageEvent) => {
    const { id, ok, value, error } = ev.data ?? {}
    const p = pending.get(id)
    if (!p) return
    pending.delete(id)
    if (ok) p.resolve(value); else p.reject(new Error(String(error)))
  }
  w.addEventListener("error", (ev: any) => failAll(new Error(`scan worker: ${ev?.message ?? "error"}`)))
  w.unref()
  worker = w
  return w
}

/** `kind`'s rescan over `questions`: on the worker when enabled, else `inThread()`. */
export function offThread<T>(kind: ScanKind, questions: Question[], inThread: () => Promise<T>): Promise<T> {
  if (!enabled) return inThread()
  return new Promise<T>((resolve, reject) => {
    const id = ++seq
    pending.set(id, { resolve, reject })
    // A worker sees the environment the process started with, not later changes: send the ASD_* settings along.
    const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => k.startsWith("ASD_")))
    try { getWorker().postMessage({ id, kind, questions, env }) } catch (e) { pending.delete(id); reject(e as Error) }
  })
}
