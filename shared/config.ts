import { readFileSync, statSync } from "node:fs"

// The context-guard settings, shared by the opencode plugin and the Claude Code hooks.
// One YAML (shared/context-guard.yaml) is the single config; this module owns its shape,
// its defaults and the mtime-cached loading. Needs bun (Bun.YAML).

export type Config = {
  trigger_tokens: number
  keep_tool_steps: number
  keep_reasoning_steps: number
  protect_tools: string[]
  protect_paths: string[]
  mask_input_tools: string[]
  keep_input_chars: number
  regrow_tokens: number
  min_gain_tokens: number
  mask_agents: string[]
  mask_reasoning_agents: string[]
  guard_agents: string[]
  max_read_bytes: number
  nav_redirect: boolean
  nav_small_bytes: number
  nav_grep_context: number
}

export const DEFAULTS: Config = {
  trigger_tokens: 70000,
  keep_tool_steps: 10,
  keep_reasoning_steps: 20,
  protect_tools: ["phase_checkpoint", "task", "question", "todowrite", "skill", "edit", "write"],
  protect_paths: ["references/work-on/phase-\\d+\\.md", "primary-agents/work-on\\.md", "primary-agents/stub\\.md"],
  mask_input_tools: ["bash", "task", "write", "edit"],
  keep_input_chars: 300,
  regrow_tokens: 10000,
  min_gain_tokens: 4000,
  mask_agents: ["work-on", "stub", "dossier", "implementer", "integration-test-author", "reviewer"],
  mask_reasoning_agents: ["work-on", "stub", "dossier"],
  guard_agents: ["work-on", "stub"],
  max_read_bytes: 20000,
  nav_redirect: true,
  nav_small_bytes: 4000,
  nav_grep_context: 5,
}

const num = (v: unknown, fallback: number, min = 0) => {
  const n = Number(v)
  return Number.isFinite(n) && n >= min ? Math.floor(n) : fallback
}
const bool = (v: unknown, fallback: boolean) => (typeof v === "boolean" ? v : fallback)
const strings = (v: unknown, fallback: string[]) =>
  Array.isArray(v) ? v.filter((x) => typeof x === "string") : fallback

// The regexes of `protect_paths`; a bad pattern is skipped, not fatal. Cached per config object.
const compiled = new WeakMap<Config, RegExp[]>()
export function protectRegexes(cfg: Config): RegExp[] {
  let res = compiled.get(cfg)
  if (!res) {
    res = []
    for (const p of cfg.protect_paths) {
      try {
        res.push(new RegExp(p))
      } catch {
        // skipped
      }
    }
    compiled.set(cfg, res)
  }
  return res
}

// A loader for one YAML path: re-reads it when its mtime moves. A broken edit keeps the
// last good config; a missing key falls back to its default.
export function configLoader(path: string): () => Config {
  let config: Config = DEFAULTS
  let mtimeSeen = -1
  return () => {
    try {
      const mtime = statSync(path).mtimeMs
      if (mtime !== mtimeSeen) {
        const raw = ((Bun.YAML.parse(readFileSync(path, "utf8")) as Record<string, unknown>) ?? {}) as Record<string, unknown>
        config = {
          trigger_tokens: num(raw.trigger_tokens, DEFAULTS.trigger_tokens, 1),
          keep_tool_steps: num(raw.keep_tool_steps, DEFAULTS.keep_tool_steps),
          keep_reasoning_steps: num(raw.keep_reasoning_steps, DEFAULTS.keep_reasoning_steps),
          protect_tools: strings(raw.protect_tools, DEFAULTS.protect_tools),
          protect_paths: strings(raw.protect_paths, DEFAULTS.protect_paths),
          mask_input_tools: strings(raw.mask_input_tools, DEFAULTS.mask_input_tools),
          keep_input_chars: num(raw.keep_input_chars, DEFAULTS.keep_input_chars, 1),
          regrow_tokens: num(raw.regrow_tokens, DEFAULTS.regrow_tokens),
          min_gain_tokens: num(raw.min_gain_tokens, DEFAULTS.min_gain_tokens),
          mask_agents: strings(raw.mask_agents, DEFAULTS.mask_agents),
          mask_reasoning_agents: strings(raw.mask_reasoning_agents, DEFAULTS.mask_reasoning_agents),
          guard_agents: strings(raw.guard_agents, DEFAULTS.guard_agents),
          max_read_bytes: num(raw.max_read_bytes, DEFAULTS.max_read_bytes, 1),
          nav_redirect: bool(raw.nav_redirect, DEFAULTS.nav_redirect),
          nav_small_bytes: num(raw.nav_small_bytes, DEFAULTS.nav_small_bytes),
          nav_grep_context: num(raw.nav_grep_context, DEFAULTS.nav_grep_context, 1),
        }
        mtimeSeen = mtime
      }
    } catch {
      // keep the last good config (the defaults when none was ever read)
    }
    return config
  }
}
