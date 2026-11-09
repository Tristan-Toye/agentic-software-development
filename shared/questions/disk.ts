// The dashboard's disk watch: every few minutes it asks scripts/disk_guard.py how much is free, starts a reclaim of
// build output nobody uses when free space drops below the reclaim target, and tells the owner once when it drops
// below the floor, where the runner stops starting stages. Hourly it also sweeps: each repo's own reclaim hook
// (a VM's build directories and disk trim) runs whatever the free space. The page shows the reading (`disk` in /api/runs) as a
// banner while it is low. Contract: references/runner.md ("Disk space").
import { join } from "node:path"

export type DiskReading = {
  freeGiB: number; floorGiB: number; targetGiB: number; low: boolean; checked: string
  reclaimable: number; reclaiming: boolean; lastReclaim?: string; lastSweep?: string
}

export const diskScript = (): string => process.env.ASD_DISK_GUARD_BIN || join(import.meta.dir, "..", "..", "scripts", "disk_guard.py")

export type DiskDeps = {
  status: () => Promise<any | null>            // disk_guard.py status --json, parsed; null when it failed
  reclaim: () => Promise<void>                 // disk_guard.py reclaim, resolved when it ends
  sweep: () => Promise<void>                   // disk_guard.py sweep: the repos' own reclaim hooks, on a timer
  notify: (title: string, body: string) => void
  changed: () => void
}

let reading: DiskReading | null = null
let reclaiming = false
let lastReclaim: string | undefined
let sweeping = false
let lastSweep: string | undefined

export const diskReading = (): DiskReading | null => reading

/** One check: read, reclaim when below target, notify on crossing the floor. Resolves once the reading is stored. */
export async function checkDisk(d: DiskDeps): Promise<DiskReading | null> {
  const st = await d.status()
  if (!st || typeof st.freeGiB !== "number") return reading
  const was = reading?.low ?? false
  const n = Array.isArray(st.reclaimable) ? st.reclaimable.length : 0
  reading = { freeGiB: st.freeGiB, floorGiB: st.floorGiB, targetGiB: st.targetGiB, low: !!st.low, checked: st.checked, reclaimable: n, reclaiming, lastReclaim, lastSweep }
  if (reading.low && !was)
    d.notify("Disk almost full", `${st.freeGiB} GiB free, below the ${st.floorGiB} GiB floor: runs queue until there is room. Reclaiming unused build output.`)
  if (st.freeGiB < st.targetGiB && n && !reclaiming) {
    reclaiming = true; reading.reclaiming = true
    void d.reclaim().finally(() => {
      reclaiming = false; lastReclaim = new Date().toISOString()
      if (reading) { reading.reclaiming = false; reading.lastReclaim = lastReclaim }
      void checkDisk(d)
    })
  }
  d.changed()
  return reading
}

/** The timed sweep: each repository's `.agentic/reclaim.sh` with its finished runs' idle worktrees, whatever the free
 *  space, because build output a repository keeps elsewhere (a VM's disk) fills while the host still has room.
 *  One at a time; resolves when it ends. */
export async function sweepDisk(d: DiskDeps): Promise<boolean> {
  if (sweeping || reclaiming) return false
  sweeping = true
  try { await d.sweep() } finally {
    sweeping = false; lastSweep = new Date().toISOString()
    if (reading) reading.lastSweep = lastSweep
    d.changed()
  }
  return true
}

/** The real dependencies: disk_guard.py through python3. */
export function diskDeps(notify: DiskDeps["notify"], changed: () => void): DiskDeps {
  return {
    status: async () => {
      try {
        const p = Bun.spawn(["python3", diskScript(), "status", "--json"], { stdout: "pipe", stderr: "ignore", stdin: "ignore" })
        const t = await new Response(p.stdout).text()
        return JSON.parse(t)
      } catch { return null }
    },
    reclaim: async () => {
      try { await Bun.spawn(["python3", diskScript(), "reclaim"], { stdout: "ignore", stderr: "ignore", stdin: "ignore" }).exited } catch { /* next check retries */ }
    },
    sweep: async () => {
      try { await Bun.spawn(["python3", diskScript(), "sweep"], { stdout: "ignore", stderr: "ignore", stdin: "ignore" }).exited } catch { /* next sweep retries */ }
    },
    notify, changed,
  }
}

/** Test hook: forget the stored reading. */
export function resetDisk(): void { reading = null; reclaiming = false; lastReclaim = undefined; sweeping = false; lastSweep = undefined }
