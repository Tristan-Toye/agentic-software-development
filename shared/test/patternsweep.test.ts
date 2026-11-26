// The pattern-sweep indicator's read side (shared/questions/patternsweep.ts).
import { mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { sweepOf } from "../questions/patternsweep.ts"

const fails: string[] = []
const check = (name: string, ok: boolean) => { console.log((ok ? "PASS " : "FAIL ") + "patternsweep: " + name); if (!ok) fails.push(name) }

const dir = mkdtempSync(join(tmpdir(), "patternsweep-"))
const now = Date.parse("2026-11-26T12:00:00Z")
check("never run: no file", sweepOf("a", now, dir).lastRun === null && sweepOf("a", now, dir).stale)
writeFileSync(join(dir, "a.json"), JSON.stringify({ last_run: "2026-11-21T12:00:00Z", items: 4, summary: "four" }))
const a = sweepOf("a", now, dir)
check("fresh run: 5 days, 4 items, not stale", a.days === 5 && a.items === 4 && !a.stale && a.summary === "four")
writeFileSync(join(dir, "b.json"), JSON.stringify({ last_run: "2026-10-01T00:00:00Z", items: 0 }))
check("older than 30 days is stale", sweepOf("b", now, dir).stale && sweepOf("b", now, dir).days === 56)
writeFileSync(join(dir, "c.json"), "{nope")
check("unreadable file reads as never run", sweepOf("c", now, dir).lastRun === null)
check("a path-like repo name is refused", sweepOf("../a", now, dir).lastRun === null)
if (fails.length) { console.log(`${fails.length} failed`); process.exit(1) }
