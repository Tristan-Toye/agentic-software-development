// VR-04: verdicts per element (review.json), the batch answer, the run-detail `diagrams` field, and the VR-06 banner and
// repo view on the dashboard. Run: bun test shared/test/vrreview.test.ts
import { afterAll, describe, expect, test } from "bun:test"
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { buildAnswer, diagramsOf, MAX_ANSWER, MAX_COMMENT, readReview, reviewOptions, reviewState, setVerdict, ReviewError } from "../questions/vrreview.ts"
import { questionBlocks } from "../questions/slack.ts"
import { parseQuestion } from "../questions/store.ts"
import { PAGE } from "../questions/page.ts"

const FIX = join(import.meta.dir, "..", "..", "references", "visual-review", "fixtures")
const load = (n: string) => JSON.parse(readFileSync(join(FIX, n), "utf8"))
const dirWith = (): string => {
  const d = mkdtempSync(join(tmpdir(), "vrr-"))
  for (const v of ["l0", "l1", "l2"]) cpSync(join(FIX, `w145-${v}.json`), join(d, `${v}.json`))
  return d
}
const nodes = (v: string): string[] => load(`w145-${v}.json`).nodes.map((n: any) => n.id)
const fails = (f: () => unknown): ReviewError => { try { f() } catch (e) { return e as ReviewError } throw new Error("did not throw") }

describe("setVerdict and review.json", () => {
  test("a valid verdict is written with its anchor {diagram, element, graph_hash} and counted", () => {
    const d = dirWith(), id = nodes("l1")[0]
    const r = setVerdict(d, "W-145", { diagram: "L1", element: id, verdict: "ok" })
    const file = JSON.parse(readFileSync(join(d, "review.json"), "utf8"))
    expect(file.schema_version).toBe("1.0.0")
    expect(file.dossier).toBe("W-145")
    expect(file.lines).toHaveLength(1)
    expect(file.lines[0].anchor).toEqual({ diagram: "L1", element: id, graph_hash: reviewState(d, "W-145").hashes.L1 })
    expect(file.lines[0].anchor.graph_hash).toMatch(/^[0-9a-f]{64}$/)
    expect(r.followup).toBeNull()
    expect(r.state.summary.checked).toBe(1)
    expect(r.state.summary.total).toBe(nodes("l0").length + nodes("l1").length + nodes("l2").length)
    expect(r.state.summary.text).toBe(`1 of ${r.state.summary.total} elements checked`)
  })
  test("a new verdict on the same element replaces the line; unset clears it", () => {
    const d = dirWith(), id = nodes("l2")[0]
    setVerdict(d, "W-145", { diagram: "L2", element: id, verdict: "ok" })
    setVerdict(d, "W-145", { diagram: "L2", element: id, verdict: "change", comment: "split it" })
    const rv = readReview(d, "W-145")!
    expect(rv.lines).toHaveLength(1)
    expect(rv.lines[0].verdict).toBe("change")
    expect(setVerdict(d, "W-145", { diagram: "L2", element: id, verdict: "unset" }).state.summary.checked).toBe(0)
  })
  test("an unknown element, diagram, verdict or a non-text comment is refused", () => {
    const d = dirWith()
    expect(fails(() => setVerdict(d, "W-145", { diagram: "L1", element: "nope", verdict: "ok" })).status).toBe(400)
    expect(fails(() => setVerdict(d, "W-145", { diagram: "L1", element: nodes("l2")[0], verdict: "ok" })).message).toMatch(/unknown element/)
    expect(fails(() => setVerdict(d, "W-145", { diagram: "L9", element: "x", verdict: "ok" })).status).toBe(400)
    expect(fails(() => setVerdict(d, "W-145", { diagram: "L1", element: nodes("l1")[0], verdict: "approve" })).status).toBe(400)
    expect(fails(() => setVerdict(d, "W-145", { diagram: "L1", element: nodes("l1")[0], verdict: "ok", comment: 5 })).status).toBe(400)
    expect(existsSync(join(d, "review.json"))).toBe(false)
  })
  test("an oversize comment or element id is refused (413 / 400)", () => {
    const d = dirWith()
    expect(fails(() => setVerdict(d, "W-145", { diagram: "L1", element: nodes("l1")[0], verdict: "change", comment: "x".repeat(MAX_COMMENT + 1) })).status).toBe(413)
    expect(fails(() => setVerdict(d, "W-145", { diagram: "L1", element: "e".repeat(201), verdict: "ok" })).status).toBe(400)
  })
  test("a Question with a comment asks for a follow-up with the anchor, once", () => {
    const d = dirWith(), id = nodes("l1")[1]
    const a = setVerdict(d, "W-145", { diagram: "L1", element: id, verdict: "question", comment: "why here?" })
    expect(a.followup?.anchor.element).toBe(id)
    expect(a.followup?.text).toBe("why here?")
    expect(setVerdict(d, "W-145", { diagram: "L1", element: id, verdict: "question", comment: "why here?" }).followup).toBeNull()
    expect(setVerdict(d, "W-145", { diagram: "L1", element: nodes("l1")[2], verdict: "question" }).followup).toBeNull()
  })
  test("a review.json of another shape is ignored, not fatal", () => {
    const d = dirWith()
    writeFileSync(join(d, "review.json"), JSON.stringify({ anything: ["goes"] }))
    expect(reviewState(d, "W-145").summary.checked).toBe(0)
    expect(setVerdict(d, "W-145", { diagram: "L0", element: nodes("l0")[0], verdict: "ok" }).state.summary.checked).toBe(1)
  })
})

describe("outdated and gone anchors", () => {
  test("a verdict on an older graph shows as outdated and is not counted as checked", () => {
    const d = dirWith(), id = nodes("l1")[0]
    setVerdict(d, "W-145", { diagram: "L1", element: id, verdict: "ok" })
    expect(reviewState(d, "W-145").lines[0].state).toBe("current")
    const g = load("w145-l1.json"); g.title = "regenerated"
    writeFileSync(join(d, "l1.json"), JSON.stringify(g))
    const st = reviewState(d, "W-145")
    expect(st.lines[0].state).toBe("outdated")
    expect(st.summary.checked).toBe(0)
    expect(st.summary.outdated).toBe(1)
    expect(st.summary.text).toContain("1 outdated")
    // saving it again confirms it on the new graph
    expect(setVerdict(d, "W-145", { diagram: "L1", element: id, verdict: "ok" }).state.lines[0].state).toBe("current")
  })
  test("a comment on an element the new graph no longer has stays in the file and shows as gone (S7, VC-08)", () => {
    const d = dirWith(), id = nodes("l1")[1]
    setVerdict(d, "W-145", { diagram: "L1", element: id, verdict: "change", comment: "keep this note" })
    const g = load("w145-l1.json"); g.nodes = g.nodes.filter((n: any) => n.id !== id); g.edges = g.edges.filter((e: any) => e.source !== id && e.target !== id)
    writeFileSync(join(d, "l1.json"), JSON.stringify(g))
    const st = reviewState(d, "W-145")
    expect(st.lines[0].state).toBe("gone")
    expect(st.lines[0].comment).toBe("keep this note")
    expect(st.summary.gone).toBe(1)
    expect(readReview(d, "W-145")!.lines).toHaveLength(1)
  })
  test("a gone line can be cleared with unset; a new verdict on a gone element is still refused", () => {
    const d = dirWith(), id = nodes("l1")[1]
    setVerdict(d, "W-145", { diagram: "L1", element: id, verdict: "change", comment: "old" })
    const g = load("w145-l1.json"); g.nodes = g.nodes.filter((n: any) => n.id !== id); g.edges = g.edges.filter((e: any) => e.source !== id && e.target !== id)
    writeFileSync(join(d, "l1.json"), JSON.stringify(g))
    expect(fails(() => setVerdict(d, "W-145", { diagram: "L1", element: id, verdict: "ok" })).status).toBe(400)
    const r = setVerdict(d, "W-145", { diagram: "L1", element: id, verdict: "unset" })
    expect(r.state.lines).toHaveLength(0)
    expect(r.state.summary.gone).toBe(0)
  })
  test("element ids stay the same across a regeneration, so the comment stays attached (S6, VC-05)", () => {
    const d = dirWith(), id = nodes("l2")[0]
    setVerdict(d, "W-145", { diagram: "L2", element: id, verdict: "change", comment: "c" })
    const g = load("w145-l2.json"); g.nodes.find((n: any) => n.id === id).label += " (renamed)"
    writeFileSync(join(d, "l2.json"), JSON.stringify(g))
    const l = reviewState(d, "W-145").lines[0]
    expect(l.anchor.element).toBe(id)
    expect(l.state).toBe("outdated")
  })
})

describe("answer text", () => {
  const okAll = (d: string) => { for (const v of ["L0", "L1", "L2"]) for (const id of nodes(v.toLowerCase())) setVerdict(d, "W-145", { diagram: v, element: id, verdict: "ok" }) }
  test("every element OK gives A, approved", () => {
    const d = dirWith(); okAll(d)
    const a = buildAnswer(reviewState(d, "W-145"), "W-145")
    expect(a.option).toBe("A")
    expect(a.text).toMatch(/approved/)
    expect(a.text).not.toMatch(/changes requested/)
  })
  test("all OK but one diagram file unreadable (mid-write) is B, never approved, and names the file", () => {
    const d = dirWith(); okAll(d)
    writeFileSync(join(d, "l2.json"), "{\"nodes\": [")
    const st = reviewState(d, "W-145")
    expect(st.broken).toEqual(["L2"])
    const a = buildAnswer(st, "W-145")
    expect(a.option).toBe("B")
    expect(a.text).toMatch(/unreadable diagram file \(not reviewed\): L2/)
  })
  test("anything else gives B, changes requested, with element ids, comments, and what is unchecked", () => {
    const d = dirWith(); okAll(d)
    const c = nodes("l1")[0], r = nodes("l2")[0], q = nodes("l0")[0]
    setVerdict(d, "W-145", { diagram: "L1", element: c, verdict: "change", comment: "use a queue" })
    setVerdict(d, "W-145", { diagram: "L2", element: r, verdict: "remove" })
    setVerdict(d, "W-145", { diagram: "L0", element: q, verdict: "question", comment: "is this in scope?" })
    setVerdict(d, "W-145", { diagram: "L2", element: nodes("l2")[1], verdict: "unset" })
    const a = buildAnswer(reviewState(d, "W-145"), "W-145")
    expect(a.option).toBe("B")
    expect(a.text).toMatch(/^W-145 visual review: changes requested\./)
    expect(a.text).toContain(`- change L1 ${c}: use a queue`)
    expect(a.text).toContain(`- remove from scope L2 ${r}`)
    expect(a.text).toContain(`- question L0 ${q}: is this in scope?`)
    expect(a.text).toContain(`not yet looked at: L2 ${nodes("l2")[1]}`)
    expect(a.text).toContain("review.json")
    expect(a.text.length).toBeLessThanOrEqual(MAX_ANSWER)
  })
  test("no verdict at all is B, never approved; a long list is cut to fit the store line", () => {
    const d = dirWith()
    expect(buildAnswer(reviewState(d, "W-145"), "W-145").option).toBe("B")
    for (const v of ["L0", "L1", "L2"]) for (const id of nodes(v.toLowerCase())) setVerdict(d, "W-145", { diagram: v, element: id, verdict: "change", comment: "x".repeat(900) })
    const a = buildAnswer(reviewState(d, "W-145"), "W-145")
    expect(a.text.length).toBeLessThanOrEqual(MAX_ANSWER)
    expect(a.text).toContain("cut")
  })
  test("the option keys come from the labels the Phase 3 step sets, and the question has 2 options without the word approve as an action (VC-07)", () => {
    const opts = [{ key: "A", label: "Approved" }, { key: "B", label: "Changes requested" }]
    expect(reviewOptions(opts)).toEqual({ A: "A", B: "B" })
    expect(reviewOptions([{ key: "A", label: "Yes" }, { key: "B", label: "No" }])).toBeNull()
    const ph = readFileSync(join(import.meta.dir, "..", "..", "references", "work-on", "phase-3.md"), "utf8")
    expect(ph).toContain("`A|Approved|fan-out starts`")
    expect(ph).toContain("`B|Changes requested|back to the contract`")
    expect(ph).toContain("review.json")
  })
})

describe("diagramsOf (run detail)", () => {
  test("paths of l0/l1/l2 and the review counts; null without a visual review", () => {
    const root = mkdtempSync(join(tmpdir(), "vrr-root-"))
    expect(diagramsOf(root, "W-145")).toBeNull()
    const dir = join(root, ".discovery", "visual-review", "W-145")
    mkdirSync(dir, { recursive: true })
    cpSync(join(FIX, "w145-l0.json"), join(dir, "l0.json"))
    setVerdict(dir, "W-145", { diagram: "L0", element: nodes("l0")[0], verdict: "ok" })
    const d = diagramsOf(root, "W-145")!
    expect(d.l0).toBe(".discovery/visual-review/W-145/l0.json")
    expect(d.l1).toBeNull()
    expect(d.review.checked).toBe(1)
    expect(d.review.exists).toBe(true)
  })
})

describe("Slack gets the question text only", () => {
  const q = (answer: any) => parseQuestion("r.W-145.Q1", [
    JSON.stringify({ ts: "1", from: "agent", kind: "question", text: "Does this picture match what the feature should do?", qkind: "product", repo: "r", dossier: "W-145", options: [{ key: "A", label: "Approved", consequence: "fan-out starts" }, { key: "B", label: "Changes requested", consequence: "back to the contract" }] }),
    JSON.stringify({ ts: "2", from: "owner", kind: "followup", text: "Element s1 (L1): why?", anchor: { diagram: "L1", element: "s1" } }),
    JSON.stringify(answer)].join("\n"))!
  test("the question blocks have no verdicts, and an answered review shows the option only", () => {
    const open = parseQuestion("r.W-145.Q1", JSON.stringify({ ts: "1", from: "agent", kind: "question", text: "Does this picture match what the feature should do?", qkind: "product", repo: "r", dossier: "W-145", options: [{ key: "A", label: "Approved" }, { key: "B", label: "Changes requested" }] }))!
    expect(JSON.stringify(questionBlocks(open, 7317))).toContain("Does this picture match")
    const ans = q({ ts: "3", from: "owner", kind: "answer", option: "B", text: "changes requested\n- change L1 s2: secret comment", review: true })
    expect(ans.state).toBe("answered")
    const blocks = JSON.stringify(questionBlocks(ans, 7317, true))
    expect(blocks).toContain("Answered (B)")
    expect(blocks).not.toContain("secret comment")
    // an ordinary answer is still echoed, as before
    expect(JSON.stringify(questionBlocks(q({ ts: "3", from: "owner", kind: "answer", option: "A", text: "plain" }), 7317, true))).toContain("plain")
  })
})

// ---------------------------------------------------------------------------------------------------- the server
describe("the dashboard routes", () => {
  const root = mkdtempSync(join(tmpdir(), "vrr-srv-"))
  const repo = join(root, "repoV"), bare = join(root, "repoN")
  for (const r of [repo, bare]) mkdirSync(join(r, ".discovery", "dossiers"), { recursive: true })
  for (const id of ["W-145", "W-146"]) writeFileSync(join(repo, ".discovery", "dossiers", id + ".md"), `---\nid: ${id}\ntitle: T ${id}\nstatus: ready\n---\nbody\n`)
  writeFileSync(join(bare, ".discovery", "dossiers", "N-1.md"), "---\nid: N-1\ntitle: T N-1\nstatus: ready\n---\nbody\n")
  const vdir = join(repo, ".discovery", "visual-review", "W-145")
  mkdirSync(vdir, { recursive: true })
  for (const v of ["l0", "l1", "l2"]) cpSync(join(FIX, `w145-${v}.json`), join(vdir, `${v}.json`))
  // the committed map and diagrams of repoV; the fake vr_map reports two stale elements and logs each call
  mkdirSync(join(repo, "docs", "architecture"), { recursive: true })
  const mapFile = join(repo, "docs", "architecture", "product-map.json")
  cpSync(join(FIX, "w145-l0.json"), mapFile)
  writeFileSync(join(repo, "docs", "architecture", "c4-context.md"), "# Context\n\n```mermaid\nflowchart LR\n  A --> B\n```\n")
  const cdir = mkdtempSync(join(tmpdir(), "vrr-x-"))
  const log = join(cdir, "fake-vr-map.log")
  const fake = join(cdir, "fake_vr_map.py")
  writeFileSync(fake, `import json, os, sys
open(${JSON.stringify(log)}, "a").write(sys.argv[1] + " " + sys.argv[2] + "\\n")
root = sys.argv[2]
if sys.argv[1] == "list":
    d = os.path.join(root, "docs", "architecture")
    out = []
    if os.path.exists(os.path.join(d, "product-map.json")):
        out.append({"path": "docs/architecture/product-map.json", "kind": "product-map", "view": "L0", "title": "Product map"})
    if os.path.exists(os.path.join(d, "c4-context.md")):
        out.append({"path": "docs/architecture/c4-context.md", "kind": "c4", "title": "Context"})
    has = any(e["kind"] == "product-map" for e in out)
    print(json.dumps({"dir": "docs/architecture", "diagrams": out, "has_map": has, "message": "" if has else "no committed product map yet in docs/architecture"}))
else:
    s = [{"id": "feature:H-01", "reasons": []}, {"id": "feature:H-04", "reasons": []}]
    print(json.dumps({"elements": 5, "stale": s, "cosmetic": [], "moved": [], "fresh": 3, "count": 2, "banner": "2 stale elements: feature:H-01, feature:H-04"}))
    sys.exit(1)
`)
  let proc: ReturnType<typeof Bun.spawn> | undefined
  let base = ""
  const qdir = join(cdir, "q")
  async function start(): Promise<void> {
    if (base) return
    mkdirSync(qdir, { recursive: true })
    const J = (o: any) => JSON.stringify(o)
    writeFileSync(join(qdir, "repoV.W-145.Q1.jsonl"), J({ ts: "2026-11-12T10:00:00Z", from: "agent", kind: "question", repo: "repoV", dossier: "W-145", root: repo, qkind: "product", text: "Does this picture match what the feature should do?", recommendation: "A", blocks: "Phase 4 fan-out",
      options: [{ key: "A", label: "Approved", consequence: "fan-out starts" }, { key: "B", label: "Changes requested", consequence: "back to the contract" }] }) + "\n")
    writeFileSync(join(qdir, "repoV.W-145.Q2.jsonl"), J({ ts: "2026-11-12T10:01:00Z", from: "agent", kind: "question", repo: "repoV", dossier: "W-145", root: repo, qkind: "product", text: "Which name?", recommendation: "A", blocks: "naming",
      options: [{ key: "A", label: "Keep", consequence: "none" }, { key: "B", label: "Rename", consequence: "rename" }] }) + "\n")
    proc = Bun.spawn([process.execPath, "--no-install", join(import.meta.dir, "..", "questions", "server.ts")], {
      env: { ...process.env, ASD_QUESTIONS_DIR: qdir, ASD_QUESTIONS_PORT: "0", ASD_QUESTIONS_NO_NOTIFY: "1", ASD_DISK_WATCH: "0", ASD_LAG_WATCH: "0", ASD_QUESTIONS_NO_SLACK: "1", ASD_SLACK_USER: "", ASD_RUNS_ROOTS: repo + ":" + bare, ASD_OPEN_WORK_DIR: join(cdir, "ow"), ASD_RUNS_DIR: join(cdir, "runs"), ASD_CLAUDE_PROJECTS: join(cdir, "projects"), ASD_RUNS_NO_GH: "1", ASD_BLOCK_LOG: join(cdir, "blocks.jsonl"), ASD_CHAT_DIR: join(cdir, "chats"), ASD_DM_AGENT_FILE: join(cdir, "dm.json"), ASD_VR_MAP_BIN: fake },
      stdout: "pipe", stderr: "ignore",
    })
    const reader = (proc.stdout as ReadableStream).getReader()
    let out = ""
    while (!/listening http:\/\/127\.0\.0\.1:(\d+)/.test(out)) {
      const { value, done } = await reader.read()
      if (done) break
      out += new TextDecoder().decode(value)
    }
    base = out.match(/listening (http:\/\/127\.0\.0\.1:\d+)/)?.[1] ?? ""
  }
  afterAll(() => { proc?.kill() })
  const post = (body: unknown, headers: Record<string, string> = {}) => fetch(`${base}/api/visual-review/review`, { method: "POST", headers: { "content-type": "application/json", ...headers }, body: typeof body === "string" ? body : JSON.stringify(body) })
  const ok = { dossier: "W-145", repo: "repoV", diagram: "L1", element: nodes("l1")[0], verdict: "ok" }
  const question = async () => (await (await fetch(`${base}/api/questions/repoV.W-145.Q1`)).json()) as any

  test("GET layout: the collapsed graph and its layout; bad names and views are refused", async () => {
    await start()
    const r = await fetch(`${base}/api/visual-review/layout?dossier=W-145&repo=repoV&view=L2&collapsed=g-protocol,nope`)
    expect(r.status).toBe(200)
    const j: any = await r.json()
    expect(j.collapsed).toEqual(["g-protocol"])
    expect(j.graph.nodes.find((n: any) => n.id === "g-protocol")).toMatchObject({ collapsed: true, members: 2 })
    expect(j.layout.boxes["g-protocol"]).toBeDefined()
    expect(j.layout.boxes["c-set"]).toBeUndefined()
    expect((await fetch(`${base}/api/visual-review/layout?dossier=W-145&repo=repoV&view=L9`)).status).toBe(400)
    expect((await fetch(`${base}/api/visual-review/layout?dossier=..%2Fx&view=L2`)).status).toBe(400)
    expect((await fetch(`${base}/api/visual-review/layout?dossier=NOPE&view=L2`)).status).toBe(404)
  })
  test("POST review: a valid verdict is saved under the run's repo root and the response counts it", async () => {
    await start()
    const r = await post(ok)
    expect(r.status).toBe(200)
    const j: any = await r.json()
    expect(j.review.summary.checked).toBe(1)
    const f = JSON.parse(readFileSync(join(vdir, "review.json"), "utf8"))
    expect(f.lines[0].anchor.diagram).toBe("L1")
    expect(f.lines[0].anchor.element).toBe(ok.element)
    const g: any = await (await fetch(`${base}/api/visual-review?dossier=W-145`)).json()
    expect(g.review.summary.checked).toBe(1)
    expect(g.hasReview).toBe(true)
  })
  test("POST review: unknown element 400, oversize 413, bad JSON 400, bad origin 403, GET 405", async () => {
    await start()
    expect((await post({ ...ok, element: "ghost" })).status).toBe(400)
    expect((await post({ ...ok, comment: "x".repeat(2000) })).status).toBe(413)
    expect((await post("y".repeat(9000))).status).toBe(413)
    expect((await post("{ nope")).status).toBe(400)
    expect((await post(ok, { origin: "http://evil.example" })).status).toBe(403)
    expect((await fetch(`${base}/api/visual-review/review`)).status).toBe(405)
  })
  test("POST review: no path traversal from the dossier id or the repo", async () => {
    await start()
    for (const dossier of ["../W-145", "..", ".", "W-145/../W-146", "..%2f..", "a b", ""]) expect((await post({ ...ok, dossier })).status, dossier).toBe(400)
    expect((await post({ ...ok, repo: "../repoV" })).status).toBe(400)
    expect((await post({ ...ok, dossier: "W-999" })).status).toBe(404)
    expect((await post({ ...ok, dossier: "W-146" })).status).toBe(404)
    expect(existsSync(join(repo, ".discovery", "visual-review", "W-146"))).toBe(false)
  })
  test("a Question verdict stores a follow-up with the anchor and the question stays open (S2, VC-01, VC-04)", async () => {
    await start()
    const el = nodes("l1")[1]
    const r = await post({ ...ok, element: el, verdict: "question", comment: "why a queue here?" })
    expect(((await r.json()) as any).followup).toBe(true)
    const q = await question()
    expect(q.state).toBe("open")
    const l = q.lines.find((x: any) => x.kind === "followup")
    expect(l.anchor).toEqual({ diagram: "L1", element: el, graph_hash: expect.stringMatching(/^[0-9a-f]{64}$/) })
    expect(l.text).toContain("why a queue here?")
  })
  test("the messages route takes an anchor on a follow-up only, and checks its shape (VC-01)", async () => {
    await start()
    const m = (b: unknown) => fetch(`${base}/api/questions/repoV.W-145.Q1/messages`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b) })
    expect((await m({ kind: "followup", text: "t", anchor: { diagram: "L2", element: "e" } })).status).toBe(201)
    expect((await m({ kind: "followup", text: "t", anchor: { diagram: "L7", element: "e" } })).status).toBe(400)
    expect((await m({ kind: "followup", text: "t", anchor: "x" })).status).toBe(400)
    expect((await m({ kind: "answer", option: "A", text: "t", anchor: { diagram: "L2", element: "e" } })).status).toBe(400)
    expect((await question()).state).toBe("open")
  })
  test("answer preview: B with the flagged ids and comments; canSend with an open question", async () => {
    await start()
    await post({ ...ok, diagram: "L2", element: nodes("l2")[0], verdict: "change", comment: "split this" })
    const a: any = await (await fetch(`${base}/api/visual-review/answer?dossier=W-145&repo=repoV`)).json()
    expect(a.question).toBe("repoV.W-145.Q1")
    expect(a.choice).toBe("B")
    expect(a.option).toBe("B")
    expect(a.canSend).toBe(true)
    expect(a.text).toContain(`- change L2 ${nodes("l2")[0]}: split this`)
    expect((await fetch(`${base}/api/visual-review/answer?dossier=..&repo=repoV`)).status).toBe(400)
  })
  test("run detail carries `diagrams`: the three paths and the review counts (VR-05 VC-09)", async () => {
    await start()
    const d: any = await (await fetch(`${base}/api/timeline/repoV/W-145`)).json()
    expect(d.diagrams.l0).toBe(".discovery/visual-review/W-145/l0.json")
    expect(d.diagrams.l1).toBe(".discovery/visual-review/W-145/l1.json")
    expect(d.diagrams.l2).toBe(".discovery/visual-review/W-145/l2.json")
    expect(d.diagrams.review.checked).toBe(3)
    expect(d.diagrams.review.question).toBe(1)
    expect(d.diagrams.review.change).toBe(1)
    const none: any = await (await fetch(`${base}/api/timeline/repoV/W-146`)).json()
    expect(none.diagrams).toBeNull()
  })
  test("review: true on a question that is not the visual-review one is refused", async () => {
    await start()
    const r = await fetch(`${base}/api/questions/repoV.W-145.Q1/messages`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ kind: "followup", text: "x", review: true }) })
    expect(r.status).toBe(201) // a follow-up ignores the flag
    const r2 = await fetch(`${base}/api/questions/repoV.W-145.Q2/messages`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ kind: "answer", option: "A", text: "y", review: true }) })
    expect(r2.status).toBe(400)
  })
  test("the batch answer goes through the question card's path; the first answer is final (S4, S5, VC-03)", async () => {
    await start()
    const a: any = await (await fetch(`${base}/api/visual-review/answer?dossier=W-145&repo=repoV`)).json()
    const send = () => fetch(`${base}/api/questions/${a.question}/messages`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ kind: "answer", option: "A", text: "forged: approved", review: true }) })
    expect((await send()).status).toBe(201)
    const q = await question()
    // the server rebuilds option and text from review.json: a forged "approved" is stored as the verdicts' answer
    expect(q.answer.text).not.toContain("forged")
    expect(q.state).toBe("answered")
    expect(q.answer.option).toBe("B")
    expect(q.answer.review).toBe(true)
    expect(q.answer.text).toContain("changes requested")
    expect((await send()).status).toBe(409)
    expect(q.lines.filter((l: any) => l.kind === "answer")).toHaveLength(1)
    const after: any = await (await fetch(`${base}/api/visual-review/answer?dossier=W-145&repo=repoV`)).json()
    expect(after.canSend).toBe(false)
  })
  test("map: stale banner data (count and ids) from vr_map check, cached on the map's mtime", async () => {
    await start()
    const get = async () => (await (await fetch(`${base}/api/visual-review/map?repo=repoV`)).json()) as any
    const m = await get()
    expect(m.hasMap).toBe(true)
    expect(m.stale).toBe(2)
    expect(m.ids).toEqual(["feature:H-01", "feature:H-04"])
    expect(m.banner).toContain("2 stale elements")
    const calls = () => readFileSync(log, "utf8").split("\n").filter((l) => l.startsWith("check")).length
    const n = calls()
    await get(); await get()
    expect(calls()).toBe(n)
    const t = new Date(Date.now() + 5000)
    require("node:fs").utimesSync(mapFile, t, t)
    await get()
    expect(calls()).toBe(n + 1)
  })
  test("repo view: the committed diagrams are listed and each opens (S9: a repo with no map says so, no banner)", async () => {
    await start()
    const m: any = await (await fetch(`${base}/api/visual-review/map?repo=repoV`)).json()
    expect(m.diagrams.map((d: any) => d.path)).toEqual(["docs/architecture/product-map.json", "docs/architecture/c4-context.md"])
    const g: any = await (await fetch(`${base}/api/visual-review/diagram?repo=repoV&path=docs/architecture/product-map.json`)).json()
    expect(g.kind).toBe("graph")
    expect(g.views.L0.layout.boxes).toBeDefined()
    const t: any = await (await fetch(`${base}/api/visual-review/diagram?repo=repoV&path=docs/architecture/c4-context.md`)).json()
    expect(t.kind).toBe("mermaid")
    expect(t.text).toContain("A --> B")
    expect((await fetch(`${base}/api/visual-review/diagram?repo=repoV&path=${encodeURIComponent("../../etc/passwd")}`)).status).toBe(404)
    expect((await fetch(`${base}/api/visual-review/diagram?repo=repoV&path=${encodeURIComponent(".discovery/dossiers/W-145.md")}`)).status).toBe(404)
    const none: any = await (await fetch(`${base}/api/visual-review/map?repo=repoN`)).json()
    expect(none.hasMap).toBe(false)
    expect(none.stale).toBe(0)
    expect(none.banner).toBe("")
    expect(none.message).toMatch(/no committed product map/)
    expect((await fetch(`${base}/api/visual-review/map?repo=nope`)).status).toBe(404)
    expect((await fetch(`${base}/api/visual-review/map?repo=..`)).status).toBe(404)
  })
  test("a vr_map that fails or hangs gives a message and no banner, not an error", async () => {
    const { mapStatus, clearMapCache } = await import("../questions/vrmap.ts")
    clearMapCache()
    const prev = process.env.ASD_VR_MAP_BIN
    const slow = join(cdir, "slow.py")
    writeFileSync(slow, "import time\ntime.sleep(30)\n")
    process.env.ASD_VR_MAP_BIN = slow
    const m = await mapStatus("repoV", repo, 600)
    process.env.ASD_VR_MAP_BIN = prev
    clearMapCache()
    expect(m.stale).toBe(0)
    expect(m.banner).toBe("")
    expect(m.message).toMatch(/timed out|could not be checked/)
  })
  test("the page code: verdict controls, Send review, banner and repo view exist and keep the no-markup rule", () => {
    for (const w of ["Send review", "Save verdict", "Remove from scope", "Not yet looked at", "/api/visual-review/review", "/api/visual-review/answer", "Product map and diagrams", "vr-open", "review: true"]) expect(PAGE, w).toContain(w)
  })
})
