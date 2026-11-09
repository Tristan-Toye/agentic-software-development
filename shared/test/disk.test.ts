// The dashboard's disk watch (shared/questions/disk.ts), with fake readings: no real disk is read or cleaned.
import { checkDisk, resetDisk, type DiskDeps } from "../questions/disk.ts"

const fails: string[] = []
const check = (name: string, ok: boolean, extra = "") => { console.log((ok ? "PASS " : "FAIL ") + "disk: " + name); if (!ok) fails.push(name + (extra ? " — " + extra : "")) }

function deps(readings: any[]) {
  const log = { reclaims: 0, notes: [] as string[], changes: 0 }
  let i = 0
  let release: () => void = () => {}
  const d: DiskDeps = {
    status: async () => readings[Math.min(i++, readings.length - 1)],
    reclaim: () => { log.reclaims++; return new Promise<void>((r) => { release = r }) },
    notify: (t) => { log.notes.push(t) },
    changed: () => { log.changes++ },
  }
  return { d, log, release: () => release() }
}
const st = (free: number, n = 2) => ({ freeGiB: free, floorGiB: 30, targetGiB: 80, low: free < 30, checked: "t", reclaimable: Array(n).fill({}) })

resetDisk()
{
  const { d, log } = deps([st(200)])
  const r = await checkDisk(d)
  check("plenty free: no reclaim, no notice, the reading is stored", r?.freeGiB === 200 && !r.low && log.reclaims === 0 && log.notes.length === 0 && log.changes === 1)
}
resetDisk()
{
  const { d, log, release } = deps([st(50), st(50), st(120)])
  const r = await checkDisk(d)
  check("below the target: one reclaim starts, no notice yet", log.reclaims === 1 && r?.reclaiming === true && log.notes.length === 0)
  await checkDisk(d)
  check("a second check while one reclaim runs starts no other", log.reclaims === 1)
  release(); await new Promise((r) => setTimeout(r, 20))
  const after = await checkDisk(d)
  check("after the reclaim the next reading is stored, with its time", after?.freeGiB === 120 && after.reclaiming === false && !!after.lastReclaim)
}
resetDisk()
{
  const { d, log } = deps([st(10), st(10)])
  await checkDisk(d); await checkDisk(d)
  check("below the floor: the owner is told once, not every check", log.notes.length === 1 && /Disk almost full/.test(log.notes[0]))
}
resetDisk()
{
  const { d, log } = deps([st(10, 0)])
  const r = await checkDisk(d)
  check("nothing reclaimable: no reclaim is started, the reading says low", log.reclaims === 0 && r?.low === true && r.reclaimable === 0)
}
resetDisk()
{
  const { d } = deps([null])
  check("a failed status keeps the last reading (none yet)", (await checkDisk(d)) === null)
}

if (fails.length) { console.log("FAIL\n  " + fails.join("\n  ")); process.exit(1) }
console.log("PASS (all cases)")
