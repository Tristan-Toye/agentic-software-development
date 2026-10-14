import { describeTool, type ToolDef } from "./tools"

// opencode's side of shared/tools.ts: `tool({...})` built from a host-neutral definition.
// `tool` is the SDK's helper, passed in by the plugin (the SDK resolves from opencode's
// config directory, not from this repo). Kept out of opencode/plugins/ on purpose:
// opencode may load every file there as a plugin.
export function opencodeTool(tool: any, def: ToolDef, execute: (args: any, ctx: any) => any) {
  const args: Record<string, unknown> = {}
  for (const p of def.params) {
    let schema = p.enum ? tool.schema.enum(p.enum) : tool.schema[p.type]()
    if (p.optional) schema = schema.optional()
    args[p.name] = schema.describe(p.description)
  }
  return tool({ description: describeTool(def, "opencode"), args, execute })
}
