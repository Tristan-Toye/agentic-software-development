import { beforeEach, describe, expect, test } from "bun:test"
import { controlsFor } from "../questions/controls.ts"
import { applyPending, clearPending, failPending, judgePending, markPending, PENDING_TTL_MS, resetPending, stubResumePending } from "../questions/pending.ts"

const T0 = 1_000_000
const run = (status: string, state?: string, extra: Record<string, unknown> = {}) => {
  const runner: any = state ? { state } : null
  return { repo: "r", id: "W-1", runner, controls: controlsFor({ status, blockedBy: [], runner, deferredOpen: 2 }, () => "done"), ...extra } as any
}
const never = () => undefined

beforeEach(resetPending)

describe("pending run buttons", () => {
  test("pressed -> Start hidden before runner.json exists", () => {
    const r = run("ready")
    expect(r.controls.start.show).toBe(true)
    markPending("r", "W-1", "start", T0)
    const v = applyPending(r, T0 + 500, never)
    expect(v.controls.start.show).toBe(false)
    expect(v.pending).toEqual(["start"])
  })
  test("pressed -> still hidden while queued, and the marker is dropped (the normal controls hide it)", () => {
    markPending("r", "W-1", "start", T0)
    const v = applyPending(run("ready", "queued"), T0 + 2000, never)
    expect(v.controls.start.show).toBe(false)
    expect(v.pending).toEqual([])
    // and it stays hidden for every later state a live run has
    for (const s of ["running", "waiting", "paused"]) expect(applyPending(run("ready", s), T0 + 3000, never).controls.start.show).toBe(false)
  })
  test("Resume pressed on a failed run stays hidden until a newer runner.json shows up", () => {
    markPending("r", "W-1", "resume", T0)
    const old = applyPending(run("ready", "failed"), T0 + 1000, () => T0 - 5000)
    expect(old.controls.resume.show).toBe(false)
    expect(old.pending).toEqual(["resume"])
    const fresh = applyPending(run("ready", "queued"), T0 + 2000, () => T0 + 1500)
    expect(fresh.controls.resume.show).toBe(false)
  })
  test("run failed after the press and is not queued -> the button returns", () => {
    markPending("r", "W-1", "start", T0)
    const v = applyPending(run("ready", "failed"), T0 + 4000, () => T0 + 3000)
    expect(v.controls.resume.show).toBe(true)
    expect(v.pending).toEqual([])
  })
  test("the server returned an error -> shown again with the error as the note", () => {
    markPending("r", "W-1", "start", T0)
    failPending("r", "W-1", "start", "no root recorded")
    const v = applyPending(run("ready"), T0 + 100, never)
    expect(v.controls.start.show).toBe(true)
    expect(v.pendingNotes.start).toBe("no root recorded")
  })
  test("no runner.json within the timeout -> shown with a note, once", () => {
    markPending("r", "W-1", "fix-deferred", T0)
    const r = run("done")
    expect(applyPending(r, T0 + PENDING_TTL_MS - 1, never).controls["fix-deferred"].show).toBe(false)
    const v = applyPending(r, T0 + PENDING_TTL_MS, never)
    expect(v.controls["fix-deferred"].show).toBe(true)
    expect(v.pendingNotes["fix-deferred"]).toContain("did not start within 60 s")
    expect(applyPending(r, T0 + PENDING_TTL_MS + 5, never).controls["fix-deferred"].show).toBe(true)
  })
  test("markers are keyed by dossier plus action", () => {
    markPending("r", "W-1", "start", T0)
    expect(applyPending({ ...run("ready"), id: "W-2" }, T0 + 1, never).controls.start.show).toBe(true)
    expect(applyPending(run("done"), T0 + 1, never).controls["fix-deferred"].show).toBe(true)
  })
  test("a new press clears the old failure note", () => {
    failPending("r", "W-1", "start", "boom")
    markPending("r", "W-1", "start", T0)
    expect(applyPending(run("ready"), T0 + 1, never).pendingNotes).toEqual({})
  })
  test("plan:<item>: pending until the item's plan run is live; failure returns the button", () => {
    const base = (planning?: string) => run("done", undefined, { deferred: { items: [{ id: "D-1", planning }] } })
    markPending("r", "W-1", "plan:D-1", T0)
    expect(applyPending(base(), T0 + 10, never).pending).toEqual(["plan:D-1"])
    expect(applyPending(base("queued"), T0 + 20, never).pending).toEqual([])
    markPending("r", "W-1", "plan:D-1", T0)
    failPending("r", "W-1", "plan:D-1", "refused")
    expect(applyPending(base(), T0 + 30, never).pendingNotes["plan:D-1"]).toBe("refused")
  })
  test("judgePending order: live beats settled beats timeout", () => {
    const m = { at: T0 }
    expect(judgePending(m, { active: true, settledAt: T0 + 1 }, T0 + 99_999)).toBe("running")
    expect(judgePending(m, { active: false, settledAt: T0 + 1 }, T0 + 99_999)).toBe("settled")
    expect(judgePending(m, { active: false }, T0 + 99_999)).toBe("expired")
    expect(judgePending(m, { active: false }, T0 + 5)).toBe("hidden")
  })
  test("stub Resume: hidden after the press, note on failure", () => {
    markPending("r", "stub-x", "resume", T0)
    expect(stubResumePending("r", "stub-x", { state: "failed" }, T0 + 10).hidden).toBe(true)
    failPending("r", "stub-x", "resume", "nope")
    expect(stubResumePending("r", "stub-x", { state: "failed" }, T0 + 20)).toEqual({ hidden: false, note: "nope" })
    clearPending("r", "stub-x", "resume")
    expect(stubResumePending("r", "stub-x", { state: "failed" }, T0 + 30).note).toBe("")
  })
})
