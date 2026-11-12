// Keeps the Mac awake while runs are live: the dashboard server holds one `caffeinate -s -i` child while at least one
// run is running, waiting, queued or paused (its runner process alive), and kills it when none is. Re-checked on the
// server's existing rescan. Darwin only, only with /usr/bin/caffeinate; ASD_CAFFEINATE=0 turns it off.
// Contract: references/runner.md ("Keeping the Mac awake").
import { existsSync, readdirSync } from "node:fs"
import { readRunner, runsDir } from "./runstore.ts"

export const CAFFEINATE_BIN = "/usr/bin/caffeinate"
export const LIVE_STATES = ["running", "testing", "waiting", "queued", "paused"]

export type AwakeChild = { kill: () => void; exited?: Promise<unknown> }
export type AwakeDeps = {
  platform: string
  exists: (path: string) => boolean
  live: () => boolean                          // at least one run in a live state
  spawn: (cmd: string[]) => AwakeChild
  enabled: () => boolean
}

/** True when some `<key>.runner.json` reads as running, waiting, queued or paused (readRunner turns a dead pid into stopped). */
export function anyLiveRun(dir = runsDir()): boolean {
  try {
    for (const f of readdirSync(dir)) {
      if (!f.endsWith(".runner.json")) continue
      const s = readRunner(f.slice(0, -".runner.json".length), dir)
      if (s && LIVE_STATES.includes(s.state)) return true
    }
  } catch { /* no runs dir: nothing is live */ }
  return false
}

export function awakeDeps(): AwakeDeps {
  return {
    platform: process.platform,
    exists: existsSync,
    live: () => anyLiveRun(),
    spawn: (cmd) => Bun.spawn(cmd, { stdout: "ignore", stderr: "ignore", stdin: "ignore" }),
    enabled: () => process.env.ASD_CAFFEINATE !== "0",
  }
}

let child: AwakeChild | null = null

export const awakeHeld = (): boolean => child !== null

/** One check: hold caffeinate while a run is live, release it when none is. Returns whether it is held afterwards. */
export function checkAwake(d: AwakeDeps): boolean {
  const possible = d.enabled() && d.platform === "darwin" && d.exists(CAFFEINATE_BIN)
  const want = possible && d.live()
  if (want && !child) {
    try {
      const c = d.spawn([CAFFEINATE_BIN, "-s", "-i"])
      child = c
      void c.exited?.then(() => { if (child === c) child = null })   // died on its own: the next check starts it again
    } catch { child = null }
  } else if (!want && child) releaseAwake()
  return child !== null
}

/** Kill the child (also on server exit). */
export function releaseAwake(): void {
  const c = child
  child = null
  try { c?.kill() } catch { /* already gone */ }
}

export const resetAwake = (): void => { child = null }
