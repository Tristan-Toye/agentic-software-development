// The dashboard's keep-awake (shared/questions/awake.ts), with a fake spawn: no real caffeinate runs.
import { mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { anyLiveRun, awakeHeld, checkAwake, CAFFEINATE_BIN, releaseAwake, resetAwake, type AwakeDeps } from "../questions/awake.ts"

const fails: string[] = []
const check = (name: string, ok: boolean, extra = "") => { console.log((ok ? "PASS " : "FAIL ") + "awake: " + name); if (!ok) fails.push(name + (extra ? " — " + extra : "")) }

function deps(o: Partial<AwakeDeps> & { liveNow?: { v: boolean } } = {}) {
  const log = { spawned: [] as string[][], killed: 0 }
  const live = o.liveNow ?? { v: true }
  const d: AwakeDeps = {
    platform: "darwin", exists: () => true, live: () => live.v, enabled: () => true,
    spawn: (cmd) => { log.spawned.push(cmd); return { kill: () => { log.killed++ } } },
    ...o,
  }
  return { d, log, live }
}

resetAwake()
{
  const { d, log, live } = deps()
  check("a live run starts one caffeinate -s -i", checkAwake(d) && log.spawned.length === 1 && log.spawned[0].join(" ") === `${CAFFEINATE_BIN} -s -i`)
  checkAwake(d)
  check("a second check does not start another", log.spawned.length === 1)
  live.v = false
  check("no live run releases it (child killed)", !checkAwake(d) && log.killed === 1 && !awakeHeld())
  checkAwake(d)
  check("no live run again does nothing", log.killed === 1 && log.spawned.length === 1)
  live.v = true; checkAwake(d)
  check("a new live run holds it again", log.spawned.length === 2 && awakeHeld())
  releaseAwake()
  check("release on exit kills the child", log.killed === 2 && !awakeHeld())
}
resetAwake()
{
  const a = deps({ platform: "linux" }); checkAwake(a.d)
  check("not darwin: never spawns", a.log.spawned.length === 0)
  const b = deps({ exists: () => false }); checkAwake(b.d)
  check("no /usr/bin/caffeinate: never spawns", b.log.spawned.length === 0)
  const c = deps({ enabled: () => false }); checkAwake(c.d)
  check("ASD_CAFFEINATE=0: never spawns", c.log.spawned.length === 0)
}
resetAwake()
{
  let boom = true
  const { d, log } = deps({ spawn: (cmd) => { if (boom) throw new Error("nope"); log2.push(cmd); return { kill() {} } } })
  var log2: string[][] = []
  check("a failing spawn is not held and does not throw", !checkAwake(d))
  boom = false
  check("the next check retries", checkAwake(d) && log2.length === 1 && log.spawned.length === 0)
  releaseAwake()
}
resetAwake()
{
  let die: () => void = () => {}
  let n = 0
  const { d } = deps({ spawn: () => { n++; return { kill() {}, exited: new Promise<void>((r) => { die = r }) } } })
  checkAwake(d); die(); await Promise.resolve(); await Promise.resolve()
  check("a child that died on its own is dropped and restarted on the next check", !awakeHeld() && checkAwake(d) && n === 2)
  releaseAwake()
}
{
  const dir = mkdtempSync(join(tmpdir(), "awake-"))
  const w = (k: string, state: string, pid: number) => writeFileSync(join(dir, `${k}.runner.json`), JSON.stringify({ state, pid }))
  w("r.A", "done", process.pid); w("r.B", "stopped", process.pid); w("r.C", "running", 2 ** 22 + 99)
  check("finished, stopped and dead-pid runs are not live", !anyLiveRun(dir))
  for (const s of ["running", "waiting", "queued", "paused"]) {
    w("r.D", s, process.pid)
    check(`a ${s} run with a live pid is live`, anyLiveRun(dir))
  }
  check("a missing runs dir is not live", !anyLiveRun(join(dir, "nope")))
}

if (fails.length) { console.log("FAIL\n  " + fails.join("\n  ")); process.exit(1) }
console.log("PASS (all cases)")
