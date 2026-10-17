import { mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, isAbsolute, join, resolve } from "node:path"
import { MAX_SUMMARY_CHARS, TOOL as CHECKPOINT } from "./checkpoint"

// Host-neutral definitions of the six tools. opencode's plugins build `tool({...})` from
// them with tool.schema; the Claude Code MCP server (shared/claude/mcp.ts) builds JSON
// Schema from the same list. Description, parameters and (for the code_* tools) execute
// live here once. phase_checkpoint has no execute: what it does differs per host.
//
// The code_* tools wrap scripts/code_nav.py, which parses Rust (tokenizer-exact brace
// matching), Python (ast) and Markdown (headings, fences respected) and returns one item
// or one section, bounded in size. They hold no state and persist nothing.

export type Host = "opencode" | "claude"

export type Param = {
  name: string
  type: "string" | "number" | "boolean"
  optional?: boolean
  description: string
  // Only for a string parameter with a closed set of values.
  enum?: string[]
}

export type ToolContext = { directory: string }

export type ToolDef = {
  name: string
  description: string
  // One sentence per host, appended to the description, where the host behaviour differs.
  hostNote?: Partial<Record<Host, string>>
  params: Param[]
  execute?: (args: Record<string, any>, ctx: ToolContext) => string | Promise<string>
}

// The script lives in this repo, one directory above shared/ (resolved through symlinks).
const SCRIPT = join(dirname(realpathSync(import.meta.path)), "..", "scripts", "code_nav.py")

function run(args: string[], cwd: string): string {
  const proc = Bun.spawnSync(["python3", SCRIPT, ...args], { cwd, stdout: "pipe", stderr: "pipe" })
  const out = proc.stdout.toString()
  if (proc.exitCode === 0) return out
  const err = proc.stderr.toString().trim()
  return `code_nav failed (exit ${proc.exitCode}): ${err}${out ? `\n${out}` : ""}`
}

const abs = (path: string, ctx: ToolContext) => (isAbsolute(path) ? path : resolve(ctx.directory, path))

export const TOOLS: ToolDef[] = [
  {
    name: "code_outline",
    description:
      "Use instead of reading a whole Rust (.rs), Python (.py) or Markdown (.md) file to learn what is in it: " +
      "returns every item (mod, fn, struct, enum, trait, impl, const, class, def; headings for markdown) with its " +
      "1-based line range, kind, qualified name and first doc line, nested items indented. Then fetch only what " +
      "you need with code_item or doc_section. Output is bounded, never the file's bytes.",
    params: [{ name: "path", type: "string", description: "file to outline; absolute or relative to the working directory" }],
    execute: ({ path }, ctx) => run(["outline", abs(path, ctx)], ctx.directory),
  },
  {
    name: "code_item",
    description:
      "Use instead of sed -n line windows, whole-file reads or grep chains to read ONE Rust or Python item. " +
      "SYMBOL is a qualified name (`Type::method`, `module::fn`, `Class.method`) or a bare name when unique " +
      "(an ambiguous one lists the candidates). mode: `contract` = docs + attributes + signature with bodies " +
      "elided (impl/trait/class: each member's docs + signature; struct/enum: the whole declaration) — the cheap " +
      "way to learn an API; `members` = a struct/enum/trait plus every impl block for it in the file " +
      "(inherent and trait impls) in contract form, the whole API of a type in one call; `signature` = " +
      "signature only; `doc` = doc comments only; `full` (default) = attributes + docs + the whole item. " +
      "Several items in one call: separate the symbols with ` | ` (`Config::load | Config::save`). Prints a " +
      "path:start-end header per item.",
    params: [
      { name: "path", type: "string", description: "a .rs or .py file" },
      { name: "symbol", type: "string", description: "qualified or bare item name, e.g. `RunConfig::from_values`; several as `A | B`" },
      { name: "mode", type: "string", optional: true, enum: ["full", "contract", "members", "doc", "signature"], description: "default full" },
    ],
    execute: ({ path, symbol, mode }, ctx) => run(["item", abs(path, ctx), symbol, "--mode", mode ?? "full"], ctx.directory),
  },
  {
    name: "code_find",
    description:
      "Use instead of grep/glob chains to find where a Rust or Python symbol is DEFINED across a tree: one row " +
      "per definition, `path:line  kind  qualified_name  — first doc line`, exact name matches first, then " +
      "suffix matches (`new` finds every `X::new`). Skips target/, .git/, node_modules/, .venv/. Not for " +
      "finding usages or text: use grep for that.",
    params: [
      { name: "symbol", type: "string", description: "name to find, bare or qualified" },
      { name: "root", type: "string", optional: true, description: "directory to search; default the working directory" },
      { name: "kind", type: "string", optional: true, description: "restrict to a kind: fn, struct, enum, trait, impl, const, class, ..." },
    ],
    execute: ({ symbol, root, kind }, ctx) => {
      const args = ["find", symbol, "--root", root ? abs(root, ctx) : ctx.directory]
      if (kind) args.push("--kind", kind)
      return run(args, ctx.directory)
    },
  },
  {
    name: "doc_section",
    description:
      "Use instead of reading a whole markdown file (rule files such as learned-rules.md or the ADR log are " +
      "80-170 KB) to read ONE section: the heading through the line before the next heading of the same or " +
      "higher level. HEADING is the heading text (case-insensitive, backticks ignored) or an id that appears " +
      "in a heading: `LRN-0073`, `ADR-0150`, `W-084`, `§4`. Several matches are listed, not dumped. Pass " +
      "`--list` as the heading to get the headings only (with line ranges). Several sections in one call: " +
      "separate the headings with ` | ` (`LRN-0073 | LRN-0081`).",
    params: [
      { name: "path", type: "string", description: "a .md file" },
      { name: "heading", type: "string", description: "heading text or id, e.g. `LRN-0073`; several as `a | b`; or `--list` for the headings" },
    ],
    execute: ({ path, heading }, ctx) => {
      const file = abs(path, ctx)
      return run(String(heading).trim() === "--list" ? ["section", file, "--list"] : ["section", file, heading], ctx.directory)
    },
  },
  {
    name: "code_replace",
    description:
      "Use instead of an edit that needs the old text pasted or the whole file read: replace ONE Rust or Python " +
      "item, found by symbol, with `content`. By default the span runs from the item's first attribute (or " +
      "signature) through its end and the doc comment above it is kept; with_docs=true replaces the doc " +
      "comment too. Content without leading indentation is indented like the item. Refuses an ambiguous or " +
      "missing symbol and a result that no longer parses; the file is then untouched. Returns " +
      "`replaced path:start-end (N lines -> M lines)`.",
    params: [
      { name: "path", type: "string", description: "a .rs or .py file" },
      { name: "symbol", type: "string", description: "qualified or bare item name" },
      { name: "content", type: "string", description: "the new text of the item" },
      { name: "with_docs", type: "boolean", optional: true, description: "also replace the doc comment; default false" },
    ],
    execute: ({ path, symbol, content, with_docs }, ctx) => {
      const dir = mkdtempSync(join(tmpdir(), "code-nav-"))
      try {
        const file = join(dir, "content.txt")
        writeFileSync(file, content)
        const args = ["replace", abs(path, ctx), symbol, "--from-file", file]
        if (with_docs) args.push("--with-docs")
        return run(args, ctx.directory)
      } finally {
        rmSync(dir, { recursive: true, force: true })
      }
    },
  },
  {
    name: CHECKPOINT,
    description:
      "Close a /work-on phase or a stage inside one. Call it at a phase boundary, or when a stage inside a phase " +
      "is done — a wave returned, a test or review round ruled, an investigation answered — and only after that " +
      "outcome is written to the dossier's ## Build log. Otherwise never in the middle of a stage you are still " +
      "working from. The summary is what you will know about all earlier work: the dossier path and worktree, what just " +
      "finished and what comes next, commits and SHAs, rulings, findings and open items, and anything later steps " +
      `must not rediscover. At most ${MAX_SUMMARY_CHARS} characters; detail belongs in the build log. End it with ` +
      "a `NEXT:` line — the exact next action with the paths, branches and IDs it needs — which a restore starts " +
      "from. Also call it, mid-stage, when you are told the context is near compaction: name the stage you are " +
      "in (\"Phase 4 — wave 2, before compaction\").",
    hostNote: {
      opencode:
        "It drops every earlier step of this run from your context from the next request on, keeping the original " +
        "task and the summaries of completed phases and stages.",
      claude:
        "It records the checkpoint and does not cut: Claude Code compacts on its own, and after each compaction the " +
        "original task and the summaries of every checkpoint are restored into your context.",
    },
    params: [
      {
        name: "phase",
        type: "string",
        description: 'what just finished, starting "Phase <N>": "Phase 3 — Mechanical check", or a stage: "Phase 6 — round 2", "Phase 4 — wave 1"',
      },
      { name: "summary", type: "string", description: "the hand-off to the rest of the run, self-contained" },
    ],
  },
]

export const toolByName = (name: string) => TOOLS.find((t) => t.name === name)

// The description a host shows the model: the shared text plus that host's sentence.
export function describeTool(def: ToolDef, host: Host): string {
  const note = def.hostNote?.[host]
  if (!note) return def.description
  // The note follows the first sentence: it says what the call does on this host.
  const cut = def.description.indexOf(". ")
  return cut === -1 ? `${def.description} ${note}` : `${def.description.slice(0, cut + 1)} ${note} ${def.description.slice(cut + 2)}`
}

// JSON Schema (draft 2020-12 subset) of a tool's parameters, for MCP.
export function inputSchema(def: ToolDef): Record<string, unknown> {
  const properties: Record<string, unknown> = {}
  for (const p of def.params) {
    properties[p.name] = { type: p.type, description: p.description, ...(p.enum ? { enum: p.enum } : {}) }
  }
  return {
    type: "object",
    properties,
    required: def.params.filter((p) => !p.optional).map((p) => p.name),
    additionalProperties: false,
  }
}
