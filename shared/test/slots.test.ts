// The dashboard's slot-cap reading (shared/questions/slots.ts): the record the runner writes, fresh or stale.
import { mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { slotsReading } from "../questions/slots.ts"

const fails: string[] = []
const check = (name: string, ok: boolean) => { console.log((ok ? "PASS " : "FAIL ") + "slots: " + name); if (!ok) fails.push(name) }

const dir = mkdtempSync(join(tmpdir(), "slots-"))
check("no file: null", slotsReading(dir, 1000) === null)
writeFileSync(join(dir, "slots-cap.json"), JSON.stringify({ cap: 5, reason: "5 slots: main 18%", ts: 900, mode: "adaptive" }))
const r = slotsReading(dir, 1000)
check("fresh record is read", r?.cap === 5 && r.reason.startsWith("5 slots"))
check("a record older than 15 min is dropped", slotsReading(dir, 900 + 901) === null)
writeFileSync(join(dir, "slots-cap.json"), "{nope")
check("unreadable: null", slotsReading(dir, 1000) === null)
if (fails.length) { console.log(`${fails.length} failed`); process.exit(1) }
