// The runner's slot cap and why, for the dashboard's "Running now" header: scripts/run_dossier.py writes
// `slots-cap.json` into the runs dir whenever it paces the cap (references/runner.md, "Slots that follow the budget").
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { runsDir } from "./runstore.ts"

export type SlotsReading = { cap: number; reason: string; ts: number }

/** The cap record, or null when there is none, it is unreadable, or no runner has paced the cap for `maxAgeS` seconds. */
export function slotsReading(dir = runsDir(), now = Date.now() / 1000, maxAgeS = 900): SlotsReading | null {
  try {
    const j = JSON.parse(readFileSync(join(dir, "slots-cap.json"), "utf8"))
    if (typeof j?.cap !== "number" || typeof j?.ts !== "number" || now - j.ts > maxAgeS) return null
    return { cap: j.cap, reason: String(j.reason ?? ""), ts: j.ts }
  } catch { return null }
}
