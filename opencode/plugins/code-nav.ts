import type { Plugin } from "@opencode-ai/plugin"
import { homedir } from "node:os"
import { join } from "node:path"
import { opencodeTool } from "../../shared/opencode"
import { TOOLS } from "../../shared/tools"

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
// step. The tools (code_outline, code_item, code_find, doc_section, code_replace) are
// defined once in shared/tools.ts, which also serves Claude Code's MCP server; this file
// is the opencode adapter. The plugin holds no state and persists nothing.

const NAV = ["code_outline", "code_item", "code_find", "doc_section", "code_replace"]

export const CodeNavPlugin: Plugin = async () => ({
  tool: Object.fromEntries(
    TOOLS.filter((d) => NAV.includes(d.name)).map((def) => [
      def.name,
      opencodeTool(tool, def, (args: any, ctx: any) => def.execute!(args, { directory: ctx.directory ?? process.cwd() })),
    ]),
  ),
})
