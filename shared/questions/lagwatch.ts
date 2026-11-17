// Event-loop watchdog for the dashboard server. A Worker thread keeps its own clock; the main thread sends it a beat
// every second. A main thread stuck in synchronous work for longer than the limit stops the beats, and the worker
// SIGKILLs the process (SIGTERM would need the blocked main thread to run its handler) so launchd's KeepAlive starts a
// fresh one. Shorter stalls are logged by the main thread itself once it runs again.
// Contract: references/owner-questions.md (Dashboard, "Event-loop watchdog").

export type LagVerdict = "ok" | "kill"

/** Pure decision: `kill` when the last beat is older than `limitMs`. */
export function lagVerdict(lastBeat: number, now: number, limitMs: number): LagVerdict {
  return now - lastBeat > limitMs ? "kill" : "ok"
}

/** Main-thread side: how late a `ms` interval tick ran, or 0 when it was within `warnMs` of on time. */
export function lagOf(expected: number, now: number, warnMs: number): number {
  const late = now - expected
  return late > warnMs ? late : 0
}

export const LAG_LIMIT_MS = Number(process.env.ASD_LAG_LIMIT_MS) || 60_000
const BEAT_MS = 1_000
const WARN_MS = 5_000

/** Start the watchdog: beats to a worker and logs main-thread stalls. `ASD_LAG_WATCH=0` turns it off. */
export function startLagWatch(log: (s: string) => void = console.log, limitMs = LAG_LIMIT_MS): void {
  if (process.env.ASD_LAG_WATCH === "0") return
  let worker: Worker | undefined
  try {
    worker = new Worker(new URL("./lagworker.ts", import.meta.url).href)
    worker.postMessage({ init: true, limitMs, pid: process.pid })
    worker.unref()
  } catch (e) { log(`lag watchdog: worker unavailable (${(e as Error).message}); logging only`) }
  let expected = Date.now() + BEAT_MS
  setInterval(() => {
    const now = Date.now()
    const late = lagOf(expected, now, WARN_MS)
    if (late) log(`event loop was blocked for ${Math.round(late / 1000)} s`)
    expected = now + BEAT_MS
    try { worker?.postMessage({ beat: now }) } catch { /* the worker is gone */ }
  }, BEAT_MS)
}
