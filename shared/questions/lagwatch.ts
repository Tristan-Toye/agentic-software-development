// Event-loop watchdog for the dashboard server. A Worker thread keeps its own clock; the main thread sends it a beat
// every second. A main thread that has stopped completely (no beat for the hard limit, 300 s) makes the worker SIGKILL
// the process (SIGTERM would need the blocked main thread to run its handler) so launchd's KeepAlive starts a fresh one.
// A slow main thread is not a dead one: stalls of tens of seconds on a saturated machine are only logged (by the main
// thread itself once it runs again), and nothing is killed during the startup grace (the first scan is the heaviest
// work and a kill there only restarted it: the 2026-11-26 crash loop). Only the 2026-11-12 case, a server that stays
// unresponsive, is killed.
// Contract: references/owner-questions.md (Dashboard, "Event-loop watchdog").

export type LagVerdict = "ok" | "kill"

/**
 * Pure decision: `kill` when the last beat is older than `limitMs`, unless the process started less than `graceMs`
 * ago (`startedAt`; the default 0 means no grace). Any beat resets the silence, so a slow but beating thread is `ok`.
 */
export function lagVerdict(lastBeat: number, now: number, limitMs: number, startedAt = 0, graceMs = 0): LagVerdict {
  if (graceMs > 0 && now - startedAt < graceMs) return "ok"
  return now - lastBeat > limitMs ? "kill" : "ok"
}

/** Main-thread side: how late a `ms` interval tick ran, or 0 when it was within `warnMs` of on time. */
export function lagOf(expected: number, now: number, warnMs: number): number {
  const late = now - expected
  return late > warnMs ? late : 0
}

/** Silence that means the main thread is dead, not slow (`ASD_LAG_LIMIT_MS`). */
export const LAG_LIMIT_MS = Number(process.env.ASD_LAG_LIMIT_MS) || 300_000
/** No kill this long after start (`ASD_LAG_GRACE_MS`; 0 turns the grace off). */
export const LAG_GRACE_MS = process.env.ASD_LAG_GRACE_MS !== undefined && process.env.ASD_LAG_GRACE_MS !== "" && Number.isFinite(Number(process.env.ASD_LAG_GRACE_MS)) ? Number(process.env.ASD_LAG_GRACE_MS) : 300_000
const BEAT_MS = 1_000
const WARN_MS = 5_000

/** Start the watchdog: beats to a worker and logs main-thread stalls. `ASD_LAG_WATCH=0` turns it off. */
export function startLagWatch(log: (s: string) => void = console.log, limitMs = LAG_LIMIT_MS, graceMs = LAG_GRACE_MS): void {
  if (process.env.ASD_LAG_WATCH === "0") return
  let worker: Worker | undefined
  try {
    worker = new Worker(new URL("./lagworker.ts", import.meta.url).href)
    worker.postMessage({ init: true, limitMs, graceMs, pid: process.pid, startedAt: Date.now() })
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
