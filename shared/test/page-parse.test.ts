// The dashboard page's inline scripts must parse: a syntax error there (a brace lost in a merge) leaves the server answering 200
// with a page that renders nothing. Run: bun test shared/test/page-parse.test.ts
import { expect, test } from "bun:test"
import { PAGE } from "../questions/page.ts"

test("every inline <script> in PAGE parses", () => {
  const scripts = [...PAGE.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1])
  expect(scripts.length).toBeGreaterThan(0)
  for (const src of scripts) expect(() => new Function(src)).not.toThrow()
})
