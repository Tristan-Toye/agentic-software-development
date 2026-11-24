// Dossier page panes (panes.ts) and how page.ts wires them. Run: bun test shared/test/panes.test.ts
import { describe, expect, test } from "bun:test"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { paneDefault, paneRead, paneSlug, paneWrite, splitSections } from "../questions/panes.ts"

const page = readFileSync(join(import.meta.dir, "..", "questions", "page.ts"), "utf8")
const fake = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m } }
const bad = { getItem() { throw new Error("blocked") }, setItem() { throw new Error("blocked") } }

describe("pane state", () => {
  test("writes then reads back, per item and key", () => {
    const s = fake()
    expect(paneWrite(s, "W-1", "history", true)).toBe(true)
    expect(paneRead(s, "W-1", "history", false)).toBe(true)
    expect(paneRead(s, "W-2", "history", false)).toBe(false)
    paneWrite(s, "W-1", "history", false)
    expect(paneRead(s, "W-1", "history", true)).toBe(false)
    expect(s.m.get("asd.pane.W-1.history")).toBe("0")
  })
  test("falls back to the default with no storage, throwing storage or a foreign value", () => {
    expect(paneRead(null, "a", "b", true)).toBe(true)
    expect(paneRead(bad, "a", "b", true)).toBe(true)
    expect(paneWrite(bad, "a", "b", true)).toBe(false)
    expect(paneWrite(undefined, "a", "b", true)).toBe(false)
    const s = fake(); s.setItem("asd.pane.a.b", "maybe")
    expect(paneRead(s, "a", "b", false)).toBe(false)
  })
  test("defaults: summary and decisions open, history, answered and timeline closed", () => {
    for (const k of ["summary", "dossier", "blocked"]) expect(paneDefault(k, {})).toBe(true)
    for (const k of ["answered", "log", "result", "history"]) expect(paneDefault(k, {})).toBe(false)
    expect(paneDefault("timeline", { mode: "dossier" })).toBe(false)
    expect(paneDefault("timeline", { mode: "run", needs: true })).toBe(false)
    expect(paneDefault("timeline", { mode: "run", needs: false })).toBe(true)
    expect(paneDefault("diagram", { reviewing: true })).toBe(true)
    expect(paneDefault("diagram", undefined)).toBe(false)
  })
})

describe("splitSections", () => {
  test("one section per H2 with the intro kept apart", () => {
    const r = splitSections("<h1>T</h1><p>lead</p><h2>Problem</h2><p>a</p><h2>Approach <code>x</code></h2><p>b</p><h3>sub</h3>")
    expect(r.intro).toBe("<h1>T</h1><p>lead</p>")
    expect(r.sections.map((s) => s.title)).toEqual(["Problem", "Approach x"])
    expect(r.sections[0].html).toBe("<p>a</p>")
    expect(r.sections[1].html).toBe("<p>b</p><h3>sub</h3>")
  })
  test("repeated titles get unique slugs; entities are decoded in titles", () => {
    const r = splitSections("<h2>Risks</h2>x<h2>Risks</h2>y<h2>A &amp; B</h2>z")
    expect(r.sections.map((s) => s.slug)).toEqual(["risks", "risks-2", "a-b"])
    expect(r.sections[2].title).toBe("A & B")
  })
  test("no H2 means everything is the intro; empty input is safe", () => {
    expect(splitSections("<p>x</p>")).toEqual({ intro: "<p>x</p>", sections: [] })
    expect(splitSections("")).toEqual({ intro: "", sections: [] })
    expect(paneSlug("!!!")).toBe("section")
    expect(paneSlug("x".repeat(100)).length).toBe(60)
  })
})

describe("page wiring", () => {
  const body = (name: string): string => {
    const i = page.indexOf("function " + name + "("); const j = page.indexOf("\nfunction ", i + 1)
    return page.slice(i, j)
  }
  test("the pane helpers are shipped to the page and the pane registry exists", () => {
    for (const f of ["paneRead", "paneWrite", "paneDefault", "paneSlug", "splitSections"]) expect(page).toContain("${" + f + ".toString()}")
    expect(page).toContain("const PANES = []")
    expect(page).toContain("h('details', { class: 'pane'")
    expect(page).toContain("data-act': 'pane-go'")
  })
  test("standing rulings render on the repo page, not on the dossier page; the dossier keeps a link", () => {
    expect(body("repoView")).toContain("rulingsView(repo)")
    expect(body("itemView")).not.toContain("rulingsView(")
    expect(body("itemView")).toContain("manage them on the repo page")
  })
  test("the dossier markdown is a heading accordion and the diagram toolbar has an overflow menu", () => {
    expect(page).toContain("splitSections(")
    expect(page).toContain("'sec:'")
    expect(page).toContain("'vrmore'")
    expect(page).toContain("Needs you now")
  })
  test("storage is only touched through the guarded helpers", () => {
    expect(page).not.toMatch(/localStorage\.(get|set)Item\(['"]asd\.pane/)
  })
})
