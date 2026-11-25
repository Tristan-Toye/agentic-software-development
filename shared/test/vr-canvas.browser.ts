// FU-04: browser check of the rendered visual-review canvas. Opens the dashboard page on the W-145 fixtures at 390 and 1280 px
// in headless Chrome and reads the DOM (no screenshots, nothing is sent to a model). It fails on a label rendered under 12 px,
// two overlapping label boxes, a label wider than its node box, a horizontal page scroll, or a console/page error.
// A seeded bad fixture (an unbreakable label that overflows its node) must FAIL; the real fixtures must PASS.
//
// Run: bun shared/test/vr-canvas.browser.ts            (from the repo root; `bun run vr-canvas` inside shared/)
// Needs puppeteer-core (`cd shared && bun install`) and Google Chrome; without either it prints one SKIP line and exits 0.
// Nothing outside a temp dir is touched: the dashboard server runs on an ephemeral port with every state dir under a temp HOME.
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync, cpSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const skip = (why: string): never => { console.log(`SKIP vr-canvas browser check: ${why}`); process.exit(0) }

const CHROME = process.env.ASD_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if (!existsSync(CHROME)) skip(`Chrome not found at ${CHROME} (set ASD_CHROME)`)
let puppeteer: any
try { puppeteer = (await import("puppeteer-core")).default } catch { skip("puppeteer-core is not installed (run `bun install` in shared/)") }

const FIX = join(import.meta.dir, "..", "..", "references", "visual-review", "fixtures")
const SERVER = join(import.meta.dir, "..", "questions", "server.ts")
const WIDTHS = [390, 1280]
const MIN_FONT_PX = 12
const EXAMPLES = Number(process.env.ASD_VR_EXAMPLES) || 3
const EPS = 0.6 // sub-pixel rounding of getBoundingClientRect, in px

type Fix = { id: string; files: Record<string, string>; expect: "pass" | "fail" }
const real: Fix[] = [
  { id: "W-145", expect: "pass", files: { l0: "w145-l0.json", l1: "w145-l1.json", l2: "w145-l2.json" } },
  { id: "W-146", expect: "pass", files: { l0: "demo-w145/l0.json", l1: "demo-w145/l1.json", l2: "demo-w145/l2.json" } },
]
// The seeded bad fixture: the real L2 with one node whose label is a single unbreakable word far wider than any box.
const BAD = { id: "W-147", expect: "fail" as const }
// The seeded overlap fixture: a three-line label with a stereotype and a member count, so the lines run into the "N members" text.
const OVERLAP = { id: "W-148", expect: "fail" as const }

const work = mkdtempSync(join(tmpdir(), "vr-canvas-"))
const repo = join(work, "repoV")
mkdirSync(join(repo, ".discovery", "dossiers"), { recursive: true })
function seed(f: Fix, mutate?: (name: string, g: any) => void): void {
  writeFileSync(join(repo, ".discovery", "dossiers", f.id + ".md"), `---\nid: ${f.id}\ntitle: T ${f.id}\nstatus: ready\n---\nbody\n`)
  const dir = join(repo, ".discovery", "visual-review", f.id)
  mkdirSync(dir, { recursive: true })
  for (const [v, file] of Object.entries(f.files)) {
    const g = JSON.parse(readFileSync(join(FIX, file), "utf8"))
    mutate?.(v, g)
    writeFileSync(join(dir, v + ".json"), JSON.stringify(g))
  }
}
for (const f of real) seed(f)
const badFiles = real[0].files
seed({ ...BAD, files: badFiles }, (v, g) => {
  if (v !== "l2") return
  g.nodes[0].label = "Unbreakable" + "Overflowing".repeat(6) + "Label"
})

seed({ ...OVERLAP, files: badFiles }, (v, g) => {
  if (v !== "l2") return
  g.nodes[0].label = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega"
  g.nodes[0].stereotype = "service"
  g.nodes[0].members = ["m1", "m2", "m3"].map((m) => ({ id: m, label: m, kind: "function", status: "changed", evidence: [] }))
})

// The shapes fixture: the real L2 with its first nodes drawn as each shape; every shape must keep its label inside its box and not overlap.
const SHAPED: Fix = { id: "W-149", expect: "pass", files: real[0].files }
seed(SHAPED, (v, g) => {
  if (v !== "l2") return
  ;["diamond", "terminal", "io", "circle", "cylinder", "round", "subroutine", "asymmetric", "junction"].forEach((sh, i) => { if (g.nodes[i]) g.nodes[i].shape = sh })
})
real.push(SHAPED)

// The decision flow fixture: the W-145 views plus decision.json (a merged before/after decision graph: start/end ovals, decision diamonds, an io parallelogram,
// YES/NO labels on the branches, an added retry loop and a removed edge). Its tab must pass the same label rules, and its Mermaid export must use the flow shapes.
const DECISION: Fix = { id: "W-150", expect: "pass", files: { ...real[0].files, decision: "decision-retry-diff.json" } }
seed(DECISION)
real.push(DECISION)

let proc: ReturnType<typeof Bun.spawn> | undefined
let browser: any
const cleanup = async (): Promise<void> => { try { await browser?.close() } catch {} ; proc?.kill(); rmSync(work, { recursive: true, force: true }) }

async function startServer(): Promise<string> {
  const home = join(work, "home"), c = join(work, "state")
  mkdirSync(home, { recursive: true })
  proc = Bun.spawn([process.execPath, "--no-install", SERVER], {
    env: {
      ...process.env, HOME: home, ASD_QUESTIONS_DIR: join(c, "q"), ASD_QUESTIONS_PORT: "0", ASD_QUESTIONS_NO_NOTIFY: "1", ASD_DISK_WATCH: "0", ASD_LAG_WATCH: "0",
      ASD_QUESTIONS_NO_SLACK: "1", ASD_SLACK_USER: "", ASD_RUNS_ROOTS: repo, ASD_OPEN_WORK_DIR: join(c, "ow"), ASD_RUNS_DIR: join(c, "runs"),
      ASD_CLAUDE_PROJECTS: join(c, "projects"), ASD_RUNS_NO_GH: "1", ASD_BLOCK_LOG: join(c, "blocks.jsonl"), ASD_CHAT_DIR: join(c, "chats"), ASD_DM_AGENT_FILE: join(c, "dm.json"),
    },
    stdout: "pipe", stderr: "ignore",
  })
  const reader = (proc.stdout as ReadableStream).getReader()
  let out = ""
  const t0 = Date.now()
  while (!/listening http:\/\/127\.0\.0\.1:(\d+)/.test(out) && Date.now() - t0 < 30_000) {
    const { value, done } = await reader.read()
    if (done) break
    out += new TextDecoder().decode(value)
  }
  return out.match(/listening (http:\/\/127\.0\.0\.1:\d+)/)?.[1] ?? ""
}

/** Runs inside the page: measures the visible canvas of the open tab. Returns problem strings. */
function measure(minFont: number, eps: number, checkFont: boolean): { problems: string[]; labels: number; nodes: number } {
  const problems: string[] = []
  const svg = Array.from(document.querySelectorAll<SVGSVGElement>(".vr svg")).find((s) => (s as any).getBoundingClientRect().width > 0)
  if (!svg) return { problems: ["no visible canvas svg"], labels: 0, nodes: 0 }
  const name = (el: Element): string => {
    const g = el.closest("g[data-id],g[data-frame]")
    return g ? (g.getAttribute("data-id") || g.getAttribute("data-frame") || "?") : "?"
  }
  type Lab = { el: Element; id: string; r: DOMRect; txt: string }
  const labs: Lab[] = []
  svg.querySelectorAll("text").forEach((t) => {
    if (t.closest(".vr-mark")) return // the one-glyph status mark, not a label
    const r = t.getBoundingClientRect()
    if (!r.width || !(t.textContent || "").trim()) return
    labs.push({ el: t, id: name(t), r, txt: (t.textContent || "").trim() })
    const m = (t as unknown as SVGGraphicsElement).getScreenCTM()
    const px = parseFloat(getComputedStyle(t).fontSize) * (m ? Math.hypot(m.a, m.b) : 1)
    if (checkFont && px < minFont - 0.05) problems.push(`label under ${minFont}px: ${px.toFixed(1)}px on ${name(t)} "${(t.textContent || "").trim().slice(0, 40)}"`)
  })
  // a label wider than its node box
  let nodes = 0
  svg.querySelectorAll("g.vrn").forEach((g) => {
    nodes++
    const rect = g.querySelector("rect:not(.vrhd)") as SVGRectElement | null
    if (!rect) return
    const b = rect.getBoundingClientRect()
    g.querySelectorAll("text").forEach((t) => {
      if (t.closest(".vr-mark")) return
      const r = t.getBoundingClientRect()
      if (!r.width) return
      if (r.width > b.width + eps) problems.push(`label wider than its node: ${r.width.toFixed(0)}px > ${b.width.toFixed(0)}px on ${g.getAttribute("data-id")} "${(t.textContent || "").trim().slice(0, 40)}"`)
      else if (r.left < b.left - eps || r.right > b.right + eps || r.top < b.top - eps || r.bottom > b.bottom + eps) problems.push(`label outside its node box on ${g.getAttribute("data-id")} "${(t.textContent || "").trim().slice(0, 40)}"`)
    })
  })
  // two overlapping label boxes (lines of one node are one box column apart, so only different texts are compared)
  for (let i = 0; i < labs.length; i++) for (let j = i + 1; j < labs.length; j++) {
    const a = labs[i].r, b = labs[j].r
    const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)
    if (w > eps && h > eps) problems.push(`labels overlap: ${labs[i].id} "${labs[i].txt.slice(0, 30)}" and ${labs[j].id} "${labs[j].txt.slice(0, 30)}" (${w.toFixed(0)}x${h.toFixed(0)}px)`)
  }
  const de = document.documentElement
  if (de.scrollWidth > de.clientWidth) problems.push(`horizontal page scroll: scrollWidth ${de.scrollWidth} > clientWidth ${de.clientWidth}`)
  return { problems, labels: labs.length, nodes }
}

/** Decisions tab only: the shapes are drawn, every branch label is there, the summary line names the counts, and the Mermaid export uses the flow shapes. */
async function decisionExtras(page: any): Promise<string[]> {
  const probs: string[] = []
  const info = await page.evaluate(() => {
    const svg = Array.from(document.querySelectorAll<SVGSVGElement>(".vr svg")).find((s) => (s as any).getBoundingClientRect().width > 0)!
    const texts = Array.from(svg.querySelectorAll("text")).map((t) => (t.textContent || "").trim())
    return { polygons: svg.querySelectorAll("g.vrn polygon").length, labels: texts, summary: (document.querySelector(".vr .vrdc") as HTMLElement | null)?.textContent || "", aria: svg.getAttribute("aria-label") || "" }
  })
  if (info.polygons < 3) probs.push(`decision tab draws ${info.polygons} polygon shapes, expected diamonds and a parallelogram`)
  for (const l of ["YES", "NO", "RETRY"]) if (!info.labels.includes(l)) probs.push(`decision tab has no ${l} branch label`)
  if (!/decisions 1 → 2 · paths 2 → 2 · new error ends 1/.test(info.summary)) probs.push(`decision summary line is "${info.summary}"`)
  if (!/^Decision flow: /.test(info.aria)) probs.push(`decision canvas aria-label is "${info.aria}"`)
  await page.evaluate(() => { (Array.from(document.querySelectorAll(".vr button.btn.sm")).find((b) => /^Mermaid$/.test(b.textContent || "")) as HTMLElement)?.click() })
  const mm: string = await page.evaluate(() => (document.querySelector(".vr textarea.vrta") as HTMLTextAreaElement | null)?.value || "")
  if (!/^flowchart TD/.test(mm)) probs.push("decision Mermaid export is not a top-down flowchart")
  if (!/\(\[".*"\]\)/.test(mm)) probs.push("decision Mermaid export has no stadium (start/end)")
  if (!/\{".*"\}/.test(mm)) probs.push("decision Mermaid export has no diamond (decision)")
  if (!/\[\/".*"\/\]/.test(mm)) probs.push("decision Mermaid export has no parallelogram (io)")
  if (!/-- "NO" -->/.test(mm)) probs.push("decision Mermaid export has no labelled NO branch")
  await page.evaluate(() => { (Array.from(document.querySelectorAll(".vr button.btn.sm")).find((b) => /^Mermaid$/.test(b.textContent || "")) as HTMLElement)?.click() })
  return probs
}

// A comment being typed in an element's verdict box survives the periodic reload (vrLoad repaints the inspector from
// review.json): the text, the focus and the cursor come back, and nothing is saved by it.
async function draftSurvivesReload(page: any, id: string): Promise<string[]> {
  const probs: string[] = []
  const ok = await page.evaluate(() => { const n = document.querySelector(".vr svg .vrn[data-id]") as SVGElement | null; if (!n) return false; n.dispatchEvent(new MouseEvent("click", { bubbles: true })); return true })
  if (!ok) return ["draft check: no clickable element"]
  await new Promise((r) => setTimeout(r, 300))
  if (!(await page.$(".vr .vrvd textarea"))) return ["draft check: clicking an element opened no verdict box"]
  const text = "a long unsaved comment ".repeat(12).trim()
  await page.focus(".vr .vrvd textarea")
  await page.keyboard.type(text, { delay: 0 })
  await page.evaluate((d: string) => (window as any).vrLoad("repoV", d), id)
  await new Promise((r) => setTimeout(r, 400))
  const after = await page.evaluate(() => { const t = document.querySelector(".vr .vrvd textarea") as HTMLTextAreaElement | null; return t ? { v: t.value, f: document.activeElement === t, c: t.selectionStart } : null })
  if (!after) probs.push("draft check: the verdict box is gone after a reload")
  else {
    if (after.v !== text) probs.push(`draft check: the unsaved comment was lost on reload (${after.v.length} of ${text.length} chars kept)`)
    if (!after.f) probs.push("draft check: the comment box lost focus on reload")
    if (after.c !== text.length) probs.push(`draft check: the cursor moved on reload (${after.c}, expected ${text.length})`)
  }
  return probs
}

type Result = { fix: Fix; width: number; tab: string; problems: string[]; labels: number; nodes: number }
async function check(base: string, f: Fix, width: number): Promise<Result[]> {
  const page = await browser.newPage()
  const errors: string[] = []
  page.on("console", (m: any) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push("console error: " + m.text()) })
  // The browser's own "Failed to load resource" line names no URL; the response event does. A missing /favicon.ico is not the canvas,
  // nor is the 404 of /api/review-packet for a fixture with no packet (the page shows no packet pane then, by design).
  page.on("response", (r: any) => { if (r.status() >= 400 && !/\/favicon\.ico$/.test(r.url()) && !(r.status() === 404 && /\/api\/review-packet\?/.test(r.url()))) errors.push(`console error: HTTP ${r.status()} for ${r.url()}`) })
  page.on("pageerror", (e: any) => errors.push("page error: " + (e?.message ?? e)))
  await page.setViewport({ width, height: 900, deviceScaleFactor: 1 })
  await page.goto(`${base}/repo/repoV/${f.id}`, { waitUntil: "load", timeout: 30_000 })
  await page.waitForSelector(".vr svg .vrn", { timeout: 20_000 })
  const tabs: string[] = await page.$$eval(".vr button.btn.sm", (bs: Element[]) => bs.map((b) => b.textContent || "").filter((t) => /^(L[012] |Decisions$)/.test(t)))
  const out: Result[] = []
  const click = async (text: RegExp): Promise<void> => {
    await page.evaluate((src: string) => { (Array.from(document.querySelectorAll(".vr button.btn.sm")).find((b) => new RegExp(src).test(b.textContent || "")) as HTMLElement)?.click() }, text.source)
    await new Promise((r) => setTimeout(r, 400))
  }
  // Readable opening state (12 px rule on), then "Fit all" (12 px rule off), then back to "Readable size" (12 px rule on again).
  for (const [phase, font] of [["open", true], ["fit-all", false], ["readable", true]] as [string, boolean][]) {
    if (phase === "fit-all") await click(/^Fit all$/)
    if (phase === "readable") await click(/^Readable size$/)
    for (const tab of tabs.length ? tabs : [""]) {
      if (tab) {
        await click(new RegExp("^" + tab.replace(/[^\w ]/g, "\\$&") + "$"))
        await page.waitForSelector(".vr svg .vrn", { timeout: 20_000 })
      }
      const m = await page.evaluate(measure, MIN_FONT_PX, EPS, font)
      if (tab === "Decisions" && phase === "open") m.problems.push(...(await decisionExtras(page)))
      out.push({ fix: f, width, tab: `${phase} ${tab}`.trim(), ...m })
    }
  }
  if (f.expect === "pass" && width === WIDTHS[WIDTHS.length - 1]) out[0].problems.push(...(await draftSurvivesReload(page, f.id)))
  if (errors.length) out[0].problems.push(...errors)
  await page.close()
  return out
}

let failed = 0
try {
  const base = await startServer()
  if (!base) throw new Error("the dashboard server did not start")
  try { browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--no-first-run", "--no-default-browser-check"] }) } catch (e) { await cleanup(); skip(`Chrome could not be launched (${(e as Error).message.split("\n")[0]})`) }
  for (const f of [...real, { ...BAD, files: badFiles } as Fix, { ...OVERLAP, files: badFiles } as Fix]) {
    for (const w of WIDTHS) {
      const rs = await check(base, f, w)
      const problems = rs.flatMap((r) => r.problems.map((p) => `[${r.tab || "canvas"}] ${p}`))
      const got = problems.length ? "fail" : "pass"
      const ok = got === f.expect
      if (!ok) failed++
      const nl = rs.reduce((s, r) => s + r.labels, 0), nn = rs.reduce((s, r) => s + r.nodes, 0)
      console.log(`${ok ? "OK  " : "BAD "} ${f.id} @${w}px: check ${got.toUpperCase()} (expected ${f.expect}); ${rs.length} views, ${nn} nodes, ${nl} labels`)
      // Group by rule so a whole view failing one rule is one block: the count, then the first examples.
      const rules = new Map<string, string[]>()
      for (const p of problems) { const k = p.replace(/^\[[^\]]*\] /, "").split(/[:(]/)[0].replace(/\d+(\.\d+)?/g, "N"); rules.set(k, [...(rules.get(k) ?? []), p]) }
      for (const [k, ps] of rules) { console.log(`       - ${ps.length} x ${k}`); for (const p of ps.slice(0, EXAMPLES)) console.log(`           ${p}`) }
    }
  }
} finally { await cleanup() }
console.log(failed ? `FAIL vr-canvas browser check: ${failed} case(s) differ from the expectation` : "PASS vr-canvas browser check: real fixtures pass, seeded overflow and overlap fixtures fail, at 390 and 1280 px")
process.exit(failed ? 1 : 0)
