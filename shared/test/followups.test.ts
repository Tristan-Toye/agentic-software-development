// Follow-up dossiers: the marker, the legacy forms, the back-link, the alarm view and the facts for the auto work tick (no network).
// Run: bun --no-install shared/test/followups.test.ts
import { followUpItems, followUpNorm, followUpSlug, followUpSources, followUpViews, linkFollowUps, parseDeferred, parseFollowUps } from "../questions/runs.ts"
import { buildFacts } from "../questions/autowork.ts"

let failed = 0
function check(name: string, ok: boolean, detail = ""): void {
  if (!ok) failed++
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : ` ${detail}`}`)
}

const log = [
  "- FOLLOW-UP-DOSSIER: W-215 F-2 — graph observer per graph; seed: A per-graph agent turned on like telemetry; own /plan",
  "FOLLOW-UP-DOSSIER: W-213 Q4 — broker routing; seed: Route links between graphs through the broker",
  "- FOLLOW-UP-PLANNED: W-213 Q4 → W-230",
].join("\n")
const m = parseFollowUps(log)
check("marker: key, summary and seed are parsed", m.length === 2 && m[0].key === "W-215 F-2" && m[0].summary === "graph observer per graph" && m[0].seed.startsWith("A per-graph agent") && m[0].kind === "marker", JSON.stringify(m))
check("back-link: FOLLOW-UP-PLANNED closes the item with its dossier", m[1].dossier === "W-230" && m[1].how === "planned" && m[0].dossier === "", JSON.stringify(m))
check("back-link: FOLLOW-UP-COVERED closes it too, a key typed in other case or spacing matches",
  parseFollowUps("- FOLLOW-UP-DOSSIER: W-1 F-1 — a; seed: b\n- FOLLOW-UP-COVERED: w-1  f-1 → W-7")[0].how === "covered")
check("a structured marker needs a seed part: without it the line is legacy, never auto-planned",
  (() => { const x = parseFollowUps("- FOLLOW-UP-DOSSIER: broker routing for links — to plan after W-214"); return x.length === 1 && x[0].kind === "legacy" })())
check("slug: deterministic, a valid seed slug, the parent id is added to a key that lacks it",
  followUpSlug("W-215", "W-215 F-2") === "seed-fu-w-215-f-2" && followUpSlug("W-215", "F-2") === "seed-fu-w-215-f-2" && /^seed-[a-z0-9][a-z0-9-]{0,60}$/.test(followUpSlug("W-1", "W-1 " + "x".repeat(200))))
check("source: the new dossier's FOLLOW-UP-SOURCE line is read", JSON.stringify(followUpSources("- FOLLOW-UP-SOURCE: W-215 W-215 F-2")) === JSON.stringify([{ src: "W-215", item: "W-215 F-2" }]))

const leg = parseFollowUps([
  "- FOLLOW-UP-DOSSIER-1: scripted watchdog service with no model",
  "- FOLLOW-UP-DOSSIER-2: graph observer",
  "- FOLLOW-UP-IDEA: graph-level monitor service",
  "- DEFERRED-SCOPE: the launcher is out of scope",
  "- OWNER-RULING (2026-11-21) Q2: option B. The launcher is its own dossier; the switch waits.",
  "- OWNER-RULING (2026-11-22): Q4 option D — routing through the broker, own dossier: links go through a stable name.",
  "- FOLLOW-UP-DOSSIER: broker routing (owner ruling Q4 option D) — to plan after W-214.",
  "- OWNER-RULING (2026-11-23): D-3 option A — its own dossier.",
  "- OWNER-RULING (2026-11-23): Q1 = A, go.",
].join("\n"))
check("legacy: FOLLOW-UP-DOSSIER-n, FOLLOW-UP-IDEA, DEFERRED-SCOPE, an OWNER-RULING 'own dossier' on a question, a free-text FOLLOW-UP-DOSSIER are detected",
  leg.map((x) => x.key).join(",") === "F-1,F-2,IDEA-1,S-1,Q2,L-1" && leg.every((x) => x.kind === "legacy"), leg.map((x) => x.key).join(","))
check("legacy: a ruling on a D-item and a plain ruling are not follow-ups; a ruling the next line already records is not counted twice", !leg.some((x) => x.summary.includes("D-3") || x.summary.includes("go.")) && leg.filter((x) => x.key === "Q4").length === 0)
check("legacy forms off (an old dossier): nothing is read but the structured marker", parseFollowUps("- FOLLOW-UP-IDEA: x\n- FOLLOW-UP-DOSSIER: W-1 F-1 — a; seed: b", false).length === 1)

// the unified list: markers, a deferred item routed to its own dossier (not a covered one, not a linked one), and the legacy ones
const run = (over: any = {}) => ({ id: "W-9", repo: "r", updated: "2026-11-28", followUpRaw: parseFollowUps("- FOLLOW-UP-DOSSIER: W-9 F-1 — a; seed: b\n- FOLLOW-UP-DOSSIER: W-9 F-2 — c; seed: d\n- FOLLOW-UP-PLANNED: W-9 F-2 → W-12\n- FOLLOW-UP-IDEA: idea"),
  deferred: parseDeferred("- D-1 bug a — f:1 — low — x\n- D-2 bug b — f:2 — low — y\n- D-3 bug c — f:3 — low — z\n- D-4 bug d — f:4 — low — w\nDEFERRED-TRIAGE: D-1 → dossier — design; seed: /plan W-9 D-1\nDEFERRED-TRIAGE: D-2 → dossier — covered by W-5\nDEFERRED-DOSSIER: D-3 → W-20\nDEFERRED-TRIAGE: D-4 → direct — fix it"),
  ...over })
const items = followUpItems(run())
check("auto-launch input: only unlinked items (a planned marker, a covered or linked D-item and a direct fix are left out)",
  items.map((i) => i.key + ":" + i.kind).join(",") === "W-9 F-1:marker,W-9 IDEA-1:legacy,W-9 D-1:deferred".replace("W-9 IDEA-1", "W-9 IDEA-1"), items.map((i) => i.key + ":" + i.kind).join(","))
const di = items.find((i) => i.kind === "deferred")!
check("a deferred item keeps the run key of Plan it and /fix-deferred Step 7 (<src>.plan-D-n)", di.planKey === "W-9.plan-D-1" && di.src === "W-9" && di.item === "D-1")
check("a marker gets the deterministic seed slug as its plan key, a legacy one none", items[0].planKey === "seed-fu-w-9-f-1" && items[1].planKey === "")

const none = (_k: string) => null
const tries: Record<string, { tries?: number; alarm?: string }> = {}
const st = (runner: (k: string) => { state: string } | null, t = tries) => Object.fromEntries(followUpViews(items, "r", runner, t).map((v) => [v.key, v.state]))
check("view: not started, queued, planning, failed (retries left), alarm after the cap, finished without a link, legacy",
  JSON.stringify(st(none)) === JSON.stringify({ "W-9 F-1": "unplanned", "W-9 IDEA-1": "legacy", "W-9 D-1": "unplanned" })
  && st((k) => k === "r.seed-fu-w-9-f-1" ? { state: "queued" } : null)["W-9 F-1"] === "queued"
  && st((k) => k === "r.seed-fu-w-9-f-1" ? { state: "running" } : null)["W-9 F-1"] === "planning"
  && st((k) => k === "r.W-9.plan-D-1" ? { state: "failed" } : null, { "W-9.plan-D-1": { tries: 2 } })["W-9 D-1"] === "failed"
  && st((k) => k === "r.W-9.plan-D-1" ? { state: "failed" } : null, { "W-9.plan-D-1": { tries: 3 } })["W-9 D-1"] === "alarm"
  && st(none, { "seed-fu-w-9-f-1": { tries: 3, alarm: "x" } })["W-9 F-1"] === "alarm"
  && st((k) => k === "r.seed-fu-w-9-f-1" ? { state: "done" } : null)["W-9 F-1"] === "unlinked", JSON.stringify(st(none)))

// back-link through the new dossier's own FOLLOW-UP-SOURCE line closes the item
const r1: any = { ...run(), repo: "r" }
linkFollowUps([r1], new Map([["r/W-9/" + followUpNorm("W-9 F-1"), "W-31"]]))
check("back-link: a FOLLOW-UP-SOURCE link from the new dossier closes the item, so it is no longer unlinked", r1.followUpRaw[0].dossier === "W-31" && !followUpItems(r1).some((i) => i.key === "W-9 F-1"))

// facts for the auto work tick
const vs = followUpViews(items, "r", none, tries)
const facts = buildFacts([{ repo: "r", root: "/x", main: "/x", id: "W-9", status: "done", blockedBy: [], runner: null, deferred: { items: [] }, followUps: vs as any }], [], () => true)
check("facts: the follow-ups reach the script with their plan key, kind, updated date and link", facts.repos.r.followUps.length === 3 && facts.repos.r.followUps[0].planKey === "seed-fu-w-9-f-1" && facts.repos.r.followUps[0].updated === "2026-11-28" && facts.repos.r.followUps[2].src === "W-9")
check("facts: a repository whose switches are all off sends nothing", Object.keys(buildFacts([{ repo: "r", root: "/x", id: "W-9", status: "done", blockedBy: [], runner: null, deferred: { items: [] }, followUps: vs as any }], [], () => false).repos).length === 0)

if (failed) { console.log(`${failed} FAILED`); process.exit(1) }
console.log("all passed")
