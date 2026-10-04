import type { Plugin } from "@opencode-ai/plugin"
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs"
import { homedir, tmpdir } from "node:os"
import { dirname, isAbsolute, join, resolve } from "node:path"

// The file is symlinked into ~/.config/opencode/plugins and runs from its real path in
// this repo, which has no node_modules: take the plugin SDK (and its zod) from
// opencode's config directory, where opencode installs it.
const configDir = process.env.OPENCODE_CONFIG_DIR ?? join(homedir(), ".config", "opencode")
const { tool } = (await import(
  Bun.resolveSync("@opencode-ai/plugin", configDir)
)) as typeof import("@opencode-ai/plugin")

// Code navigation — returns the part of a file asked for, not the file.
//
// Every model step re-bills the whole context, so a 60 KB Rust file paged in with
// `sed -n` windows, whole-file reads and chains of greps is paid for on every later
// step. These tools wrap scripts/code_nav.py, which parses Rust (tokenizer-exact brace
// matching), Python (ast) and Markdown (headings, fences respected) and returns one
// item or one section, bounded in size. The plugin holds no state and persists nothing.

// The script lives in this repo, two directories above the plugin's real path.
const SCRIPT = join(dirname(realpathSync(import.meta.path)), "..", "..", "scripts", "code_nav.py")

function run(args: string[], cwd: string): string {
  const proc = Bun.spawnSync(["python3", SCRIPT, ...args], { cwd, stdout: "pipe", stderr: "pipe" })
  const out = proc.stdout.toString()
  if (proc.exitCode === 0) return out
  const err = proc.stderr.toString().trim()
  return `code_nav failed (exit ${proc.exitCode}): ${err}${out ? `\n${out}` : ""}`
}

function base(ctx: { directory?: string }): string {
  return ctx.directory ?? process.cwd()
}

function abs(path: string, ctx: { directory?: string }): string {
  return isAbsolute(path) ? path : resolve(base(ctx), path)
}

export const CodeNavPlugin: Plugin = async () => ({
  tool: {
    code_outline: tool({
      description:
        "Use instead of reading a whole Rust (.rs), Python (.py) or Markdown (.md) file to learn what is in it: " +
        "returns every item (mod, fn, struct, enum, trait, impl, const, class, def; headings for markdown) with its " +
        "1-based line range, kind, qualified name and first doc line, nested items indented. Then fetch only what " +
        "you need with code_item or doc_section. Output is bounded, never the file's bytes.",
      args: {
        path: tool.schema.string().describe("file to outline; absolute or relative to the working directory"),
      },
      async execute({ path }, ctx) {
        return run(["outline", abs(path, ctx)], base(ctx))
      },
    }),

    code_item: tool({
      description:
        "Use instead of sed -n line windows, whole-file reads or grep chains to read ONE Rust or Python item. " +
        "SYMBOL is a qualified name (`Type::method`, `module::fn`, `Class.method`) or a bare name when unique " +
        "(an ambiguous one lists the candidates). mode: `contract` = docs + attributes + signature with bodies " +
        "elided (impl/trait/class: each member's docs + signature; struct/enum: the whole declaration) — the cheap " +
        "way to learn an API; `signature` = signature only; `doc` = doc comments only; `full` (default) = " +
        "attributes + docs + the whole item. Prints a path:start-end header.",
      args: {
        path: tool.schema.string().describe("a .rs or .py file"),
        symbol: tool.schema.string().describe("qualified or bare item name, e.g. `RunConfig::from_values`"),
        mode: tool.schema.enum(["full", "contract", "doc", "signature"]).optional().describe("default full"),
      },
      async execute({ path, symbol, mode }, ctx) {
        return run(["item", abs(path, ctx), symbol, "--mode", mode ?? "full"], base(ctx))
      },
    }),

    code_find: tool({
      description:
        "Use instead of grep/glob chains to find where a Rust or Python symbol is DEFINED across a tree: one row " +
        "per definition, `path:line  kind  qualified_name  — first doc line`, exact name matches first, then " +
        "suffix matches (`new` finds every `X::new`). Skips target/, .git/, node_modules/, .venv/. Not for " +
        "finding usages or text: use grep for that.",
      args: {
        symbol: tool.schema.string().describe("name to find, bare or qualified"),
        root: tool.schema.string().optional().describe("directory to search; default the working directory"),
        kind: tool.schema.string().optional().describe("restrict to a kind: fn, struct, enum, trait, impl, const, class, ..."),
      },
      async execute({ symbol, root, kind }, ctx) {
        const args = ["find", symbol, "--root", root ? abs(root, ctx) : base(ctx)]
        if (kind) args.push("--kind", kind)
        return run(args, base(ctx))
      },
    }),

    doc_section: tool({
      description:
        "Use instead of reading a whole markdown file (rule files such as learned-rules.md or the ADR log are " +
        "80-170 KB) to read ONE section: the heading through the line before the next heading of the same or " +
        "higher level. HEADING is the heading text (case-insensitive, backticks ignored) or an id that appears " +
        "in a heading: `LRN-0073`, `ADR-0150`, `W-084`, `§4`. Several matches are listed, not dumped. Pass " +
        "`--list` as the heading to get the headings only (with line ranges).",
      args: {
        path: tool.schema.string().describe("a .md file"),
        heading: tool.schema.string().describe("heading text or id, e.g. `LRN-0073`; or `--list` for the headings"),
      },
      async execute({ path, heading }, ctx) {
        const file = abs(path, ctx)
        return run(heading.trim() === "--list" ? ["section", file, "--list"] : ["section", file, heading], base(ctx))
      },
    }),

    code_replace: tool({
      description:
        "Use instead of an edit that needs the old text pasted or the whole file read: replace ONE Rust or Python " +
        "item, found by symbol, with `content`. By default the span runs from the item's first attribute (or " +
        "signature) through its end and the doc comment above it is kept; with_docs=true replaces the doc " +
        "comment too. Content without leading indentation is indented like the item. Refuses an ambiguous or " +
        "missing symbol and a result that no longer parses; the file is then untouched. Returns " +
        "`replaced path:start-end (N lines -> M lines)`.",
      args: {
        path: tool.schema.string().describe("a .rs or .py file"),
        symbol: tool.schema.string().describe("qualified or bare item name"),
        content: tool.schema.string().describe("the new text of the item"),
        with_docs: tool.schema.boolean().optional().describe("also replace the doc comment; default false"),
      },
      async execute({ path, symbol, content, with_docs }, ctx) {
        const dir = mkdtempSync(join(tmpdir(), "code-nav-"))
        try {
          const file = join(dir, "content.txt")
          writeFileSync(file, content)
          const args = ["replace", abs(path, ctx), symbol, "--from-file", file]
          if (with_docs) args.push("--with-docs")
          return run(args, base(ctx))
        } finally {
          rmSync(dir, { recursive: true, force: true })
        }
      },
    }),
  },
})
