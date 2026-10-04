// Run: bun --no-install opencode/plugins/test/code-nav.test.ts
// Drives every tool's execute with a fake context on temp fixtures. --no-install proves
// the SDK resolves the way opencode loads it.
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const { CodeNavPlugin } = await import(join(import.meta.dir, "..", "code-nav.ts"))
const hooks: any = await CodeNavPlugin({} as never)
const t = hooks.tool

const dir = mkdtempSync(join(tmpdir(), "cn-"))
mkdirSync(join(dir, "src"))
writeFileSync(
  join(dir, "src", "lib.rs"),
  [
    "/// A counter.",
    "pub struct Counter { n: u32 }",
    "",
    "impl Counter {",
    "    /// Adds one; the brace in the string must not confuse anything: \"}\".",
    "    pub fn bump(&mut self) -> u32 {",
    '        let _s = "}";',
    "        self.n += 1;",
    "        self.n",
    "    }",
    "}",
    "",
  ].join("\n"),
)
writeFileSync(join(dir, "notes.md"), "# Notes\n\n## LRN-0001: first\n\nbody one\n\n```\n## fake\n```\n\n## LRN-0002: second\n\nbody two\n")
writeFileSync(join(dir, "x.txt"), "plain")

const fails: string[] = []
const check = (name: string, ok: boolean) => ok || fails.push(name)
const ctx = { sessionID: "s", messageID: "m", agent: "t", directory: dir } as any

// relative paths resolve against ctx.directory
const outline = String(await t.code_outline.execute({ path: "src/lib.rs" }, ctx))
check("outline: struct", outline.includes("struct Counter  — A counter."))
check("outline: method", outline.includes("fn Counter::bump"))

const full = String(await t.code_item.execute({ path: "src/lib.rs", symbol: "Counter::bump" }, ctx))
check("item full", full.includes("self.n += 1;") && full.includes("src/lib.rs:5-10"))
const contract = String(await t.code_item.execute({ path: join(dir, "src", "lib.rs"), symbol: "bump", mode: "contract" }, ctx))
check("item contract (absolute path, bare name)", contract.includes("pub fn bump(&mut self) -> u32 { … }") && !contract.includes("self.n += 1"))
check("item missing: failure carries exit code", String(await t.code_item.execute({ path: "src/lib.rs", symbol: "nope" }, ctx)).includes("code_nav failed (exit 1)"))
check("unknown extension: exit 2", String(await t.code_outline.execute({ path: "x.txt" }, ctx)).includes("(exit 2)"))

const found = String(await t.code_find.execute({ symbol: "Counter", kind: "struct" }, ctx))
check("find (root defaults to directory)", found.includes("lib.rs:1  struct  Counter  — A counter."))
check("find with root", String(await t.code_find.execute({ symbol: "bump", root: "src" }, ctx)).includes("Counter::bump"))

const sec = String(await t.doc_section.execute({ path: "notes.md", heading: "LRN-0001" }, ctx))
check("section by id", sec.includes("body one") && sec.includes("## fake") && !sec.includes("body two"))
check("section list", String(await t.doc_section.execute({ path: "notes.md", heading: "--list" }, ctx)).includes("## LRN-0002: second"))

const rep = String(await t.code_replace.execute({
  path: "src/lib.rs",
  symbol: "Counter::bump",
  content: "pub fn bump(&mut self) -> u32 {\n    self.n += 2;\n    self.n\n}",
}, ctx))
check("replace report", /^replaced .*lib\.rs:6-10 \(5 lines -> 4 lines\)/.test(rep))
const after = readFileSync(join(dir, "src", "lib.rs"), "utf8")
check("replace keeps doc, indents, swaps body", after.includes("/// Adds one;") && after.includes("    pub fn bump") && after.includes("        self.n += 2;") && !after.includes("+= 1"))
const bad = String(await t.code_replace.execute({ path: "src/lib.rs", symbol: "Counter::bump", content: "pub fn bump() {" }, ctx))
check("replace refuses unparsable result", bad.includes("(exit 2)") && readFileSync(join(dir, "src", "lib.rs"), "utf8") === after)
check("replace with_docs", String(await t.code_replace.execute({ path: "src/lib.rs", symbol: "Counter::bump", content: "fn bump(&mut self) {}", with_docs: true }, ctx)).startsWith("replaced") && !readFileSync(join(dir, "src", "lib.rs"), "utf8").includes("Adds one"))

if (fails.length) {
  console.error("FAILED:\n" + fails.map((f) => "  - " + f).join("\n"))
  process.exit(1)
}
console.log("code-nav tests ok")
