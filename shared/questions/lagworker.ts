// Worker side of lagwatch.ts: independent clock, kills the process when the main thread stops beating.
import { lagVerdict } from "./lagwatch.ts"

declare const self: Worker
let last = Date.now()
let limit = 300_000
let grace = 0
let startedAt = Date.now()
let pid = 0
self.onmessage = (e: MessageEvent) => {
  const d = e.data ?? {}
  if (d.init) { limit = d.limitMs; grace = d.graceMs ?? 0; startedAt = d.startedAt ?? Date.now(); pid = d.pid; last = Date.now() }
  else last = Date.now()
}
setInterval(() => {
  if (!pid || lagVerdict(last, Date.now(), limit, startedAt, grace) === "ok") return
  console.error(`${new Date().toISOString()} [lagwatch] main thread silent for ${Math.round((Date.now() - last) / 1000)} s (limit ${Math.round(limit / 1000)} s); killing pid ${pid} so launchd restarts it`)
  try { process.kill(pid, "SIGKILL") } catch { process.exit(1) }
}, 1_000)
