// The dashboard's disk watch: every few minutes it asks scripts/disk_guard.py how much is free, starts a reclaim of
// build output nobody uses when free space drops below the reclaim target, and tells the owner once when it drops
// below the floor, where the runner stops starting stages. Hourly it also sweeps: each repo's own reclaim hook
// (a VM's build directories and disk trim) runs whatever the free space. The reading also carries each Docker VM engine
// (Colima profile, Lima instance): a low one starts a reclaim and, if still low after it, one owner alert. The page shows the reading (`disk` in /api/runs) as a
// banner while it is low. Contract: references/runner.md ("Disk space").
import { join } from "node:path"

export type DiskReading = {
  freeGiB: number; floorGiB: number; targetGiB: number; low: boolean; checked: string
  reclaimable: number; reclaiming: boolean; lastReclaim?: string; lastSweep?: string
  engines?: DockerEngine[]                     // each Docker VM engine (Colima profile, Lima instance) and its free space
}

export type DockerEngine = {
  kind: string; name: string; context: string; reachable: boolean; low: boolean
  freeGiB?: number; totalGiB?: number; freeFrac?: number; floorPct?: number; floorGiB?: number; repos?: string[]
}

const ENGINE_RETRY_MS = 30 * 60_000
const engineTried = new Map<string, number>()   // engine name -> when a reclaim last ran while it was low
const engineAlerted = new Set<string>()         // engines the owner was told about, until they are back above the floor

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
  const engines: DockerEngine[] = Array.isArray(st.engines) ? st.engines : []
  reading = { engines, freeGiB: st.freeGiB, floorGiB: st.floorGiB, targetGiB: st.targetGiB, low: !!st.low, checked: st.checked, reclaimable: n, reclaiming, lastReclaim, lastSweep }
  if (reading.low && !was)
    d.notify("Disk almost full", `${st.freeGiB} GiB free, below the ${st.floorGiB} GiB floor: runs queue until there is room. Reclaiming unused build output.`)
  // A low VM engine: one reclaim (dangling images, then build cache), and one owner alert if it is still low afterwards.
  const lowNow = new Set<string>()
  let engineReclaim = false
  for (const e of engines) {
    if (!e.low) continue
    lowNow.add(e.name)
    const tried = engineTried.get(e.name)
    if (!reclaiming && (tried === undefined || Date.now() - tried > ENGINE_RETRY_MS)) engineReclaim = true
    else if (!reclaiming && tried !== undefined && !engineAlerted.has(e.name)) {
      engineAlerted.add(e.name)
      const frac = typeof e.freeFrac === "number" ? ` (${Math.round(e.freeFrac * 100)}%)` : ""
      d.notify(`Docker engine ${e.name} almost full`,
        `${e.kind} ${e.name} (context ${e.context}) has ${e.freeGiB} GiB free${frac} of ${e.totalGiB} GiB after pruning dangling images and build cache. ` +
        `Runs that need it queue until there is room. Options: resize the VM disk, or prune tagged images by hand ` +
        `(docker --context ${e.context} image ls, then image rm).`)
    }
  }
  for (const k of [...engineTried.keys()]) if (!lowNow.has(k)) { engineTried.delete(k); engineAlerted.delete(k) }
  if (engineReclaim && !reclaiming) {
    for (const k of lowNow) if (!engineTried.has(k) || Date.now() - (engineTried.get(k) ?? 0) > ENGINE_RETRY_MS) engineTried.set(k, Date.now())
  }
  if ((engineReclaim || (st.freeGiB < st.targetGiB && n)) && !reclaiming) {
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
export function resetDisk(): void { engineTried.clear(); engineAlerted.clear(); reading = null; reclaiming = false; lastReclaim = undefined; sweeping = false; lastSweep = undefined }
