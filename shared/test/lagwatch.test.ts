import { describe, expect, test } from "bun:test"
import { join } from "node:path"
import { lagOf, lagVerdict } from "../questions/lagwatch.ts"
import { runsWatchIgnored, threadChanged } from "../questions/runslack.ts"

describe("dashboard hang guards", () => {
  test("the server's own sidecars never wake the runs watch (the rescan feedback loop)", () => {
    expect(runsWatchIgnored("repo.W-1.slack.json")).toBe(true)
    expect(runsWatchIgnored("repo.W-1.jsonl.tmp")).toBe(true)
    expect(runsWatchIgnored("repo.W-1.jsonl")).toBe(false)
    expect(runsWatchIgnored("repo.W-1.runner.json")).toBe(false)
  })
  test("a thread sidecar is rewritten only when it changed", () => {
    const t = { channel: "C1", ts: "1.1" }
    expect(threadChanged(null, t, 3)).toBe(true)
    expect(threadChanged({ ...t, seen: 3 }, t, 3)).toBe(false)
    expect(threadChanged({ ...t, seen: 2 }, t, 3)).toBe(true)
    expect(threadChanged({ channel: "C2", ts: "1.1", seen: 3 }, t, 3)).toBe(true)
  })
  test("lag verdicts", () => {
    expect(lagVerdict(1000, 1000 + 59_000, 60_000)).toBe("ok")
    expect(lagVerdict(1000, 1000 + 61_000, 60_000)).toBe("kill")
    // the startup grace: no kill before it ends, the normal rule after it
    expect(lagVerdict(0, 400_000, 300_000, 0, 300_000)).toBe("kill")
    expect(lagVerdict(0, 299_000, 60_000, 0, 300_000)).toBe("ok")
    expect(lagVerdict(0, 301_000, 300_000, 0, 300_000)).toBe("kill")
    expect(lagVerdict(0, 200_000, 60_000, 0, 300_000)).toBe("ok")
    // a slow main thread that still beats is never killed: the silence is measured from the last beat
    expect(lagVerdict(1_000_000, 1_000_000 + 299_000, 300_000, 0, 300_000)).toBe("ok")
    expect(lagVerdict(1_000_000, 1_000_000 + 301_000, 300_000, 0, 300_000)).toBe("kill")
    expect(lagOf(1000, 1500, 5000)).toBe(0)
    expect(lagOf(1000, 9000, 5000)).toBe(8000)
  })
  test("a main thread blocked past the limit is killed by the watchdog worker (non-zero exit)", async () => {
    const script = `import { startLagWatch } from ${JSON.stringify(join(import.meta.dir, "../questions/lagwatch.ts"))}
startLagWatch(() => {}, 1500, 0)
await Bun.sleep(1200)
const end = Date.now() + 20000
while (Date.now() < end) {}
console.log("survived")`
    const p = Bun.spawn(["bun", "-e", script], { stdout: "pipe", stderr: "pipe" })
    const code = await p.exited
    expect(await new Response(p.stdout).text()).not.toContain("survived")
    expect(code).not.toBe(0)
  }, 30_000)
  test("a block inside the startup grace is not killed", async () => {
    const script = `import { startLagWatch } from ${JSON.stringify(join(import.meta.dir, "../questions/lagwatch.ts"))}
startLagWatch(() => {}, 1000, 60000)
await Bun.sleep(300)
const end = Date.now() + 4000
while (Date.now() < end) {}
await Bun.sleep(1500)
console.log("survived")
process.exit(0)`
    const p = Bun.spawn(["bun", "-e", script], { stdout: "pipe", stderr: "pipe" })
    expect(await p.exited).toBe(0)
    expect(await new Response(p.stdout).text()).toContain("survived")
  }, 20_000)
  test("a healthy process is left alone", async () => {
    const script = `import { startLagWatch } from ${JSON.stringify(join(import.meta.dir, "../questions/lagwatch.ts"))}
startLagWatch(() => {}, 1500, 0)
await Bun.sleep(4000)
console.log("alive")
process.exit(0)`
    const p = Bun.spawn(["bun", "-e", script], { stdout: "pipe", stderr: "pipe" })
    expect(await p.exited).toBe(0)
    expect(await new Response(p.stdout).text()).toContain("alive")
  }, 15_000)
})
