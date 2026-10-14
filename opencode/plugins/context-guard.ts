import type { Plugin } from "@opencode-ai/plugin"
import { appendFileSync, mkdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, extname, isAbsolute, join, resolve } from "node:path"

// Context guard — keeps an orchestrator's carried context small, and stops the reads
// that bloat it. Two parts, both configured by context-guard.yaml beside the real file
// (the plugin is symlinked into ~/.config/opencode/plugins, so the path is resolved
// through the link; re-read whenever its mtime moves; OPENCODE_CONTEXT_GUARD_CONFIG
// points at a different file).
//
// A. Batched observation masking (experimental.chat.messages.transform). In a long run
//    most carried context is the model's own old reasoning and old tool output. Once the
//    estimated request size reaches `trigger_tokens`, a watermark moves forward so that
//    only the newest `keep_tool_steps` assistant steps keep their tool output and only
//    the newest `keep_reasoning_steps` keep their reasoning. Between triggers the same
//    watermark is applied to every request, so the conversation only appends and the
//    provider's prompt cache keeps hitting. Only the outgoing request changes: masked
//    parts are copies, the stored messages are never touched. The watermark persists per
//    session under $XDG_DATA_HOME/opencode/context-guard/<session>.json, so a restart
//    keeps the same masked set.
//
//    Past the watermark the long inputs of `mask_input_tools` are shortened too (a bash
//    heredoc, a task prompt, a write's content): masking only outputs left a floor of
//    old inputs that the recorded runs could not get under the trigger.
//
//    Every move breaks the prompt cache from the watermark on, so moves are batched by
//    two rules. A move must free at least `min_gain_tokens`; and after a move (or a
//    check that found nothing worth freeing) the next one waits until the request has
//    regrown `regrow_tokens` past the size it left. Without them a context whose
//    unmaskable part sits above the trigger moved the watermark on every step: 154 moves
//    and ~35 cache misses in one recorded run. When a check finds nothing worth freeing
//    it logs a `floor` line: only a phase_checkpoint can shrink that context.
//
// B. A read guard (tool.execute.before). For the agents in `guard_agents` it refuses
//    whole-file reads of dossiers and large files, `cargo test` through limactl without
//    run_tests.py, and `cat` of a large file. A refusal is a thrown Error: the call is
//    blocked and the model reads the message. A guard that fails internally allows.
//    The files in `protect_paths` (the pipeline's own phase files) are never refused: the
//    orchestrator is told to read them whole. `limactl` counts only where it is the
//    command run, never as text inside another command's argument.
//
// Every mask move and every refusal is one timestamped line in context-guard.log beside
// the watermarks. OPENCODE_CONTEXT_GUARD_LOG: unset, `true`, `1`, `on` or `yes` log to
// that file; `false`, `0`, `off` or `no` stop logging; any other value is the log path.
// Nothing but the plugin is exported: opencode may call every export as a plugin.

type Config = {
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
}

const DEFAULTS: Config = {
  trigger_tokens: 70000,
  keep_tool_steps: 10,
  keep_reasoning_steps: 20,
  protect_tools: ["phase_checkpoint", "task", "question", "todowrite", "skill", "edit", "write"],
  protect_paths: ["references/work-on/phase-\\d+\\.md", "primary-agents/work-on\\.md"],
  mask_input_tools: ["bash", "task", "write", "edit"],
  keep_input_chars: 300,
  regrow_tokens: 10000,
  min_gain_tokens: 4000,
  mask_agents: ["work-on", "dossier", "implementer", "integration-test-author", "reviewer"],
  mask_reasoning_agents: ["work-on", "dossier"],
  guard_agents: ["work-on"],
  max_read_bytes: 20000,
}

const configPath =
  process.env.OPENCODE_CONTEXT_GUARD_CONFIG ?? join(dirname(realpathSync(import.meta.path)), "context-guard.yaml")
// The pipeline repo the plugin lives in (opencode/plugins/ under it), for the script paths a refusal names.
const pluginRepo = resolve(dirname(realpathSync(import.meta.path)), "..", "..")
let config: Config = DEFAULTS
let configMtime = -1
let protectRes: RegExp[] = DEFAULTS.protect_paths.map((p) => new RegExp(p))

const num = (v: unknown, fallback: number, min = 0) => {
  const n = Number(v)
  return Number.isFinite(n) && n >= min ? Math.floor(n) : fallback
}
const strings = (v: unknown, fallback: string[]) =>
  Array.isArray(v) ? v.filter((x) => typeof x === "string") : fallback

// Re-reads the YAML when its mtime moves. A broken edit keeps the last good config;
// a missing key falls back to its default.
function currentConfig(): Config {
  try {
    const mtime = statSync(configPath).mtimeMs
    if (mtime !== configMtime) {
      const raw = ((Bun.YAML.parse(readFileSync(configPath, "utf8")) as Record<string, unknown>) ?? {}) as Record<string, unknown>
      const next: Config = {
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
      }
      const res: RegExp[] = []
      for (const p of next.protect_paths) {
        try {
          res.push(new RegExp(p))
        } catch {
          // a bad pattern is skipped, not fatal
        }
      }
      config = next
      protectRes = res
      configMtime = mtime
    }
  } catch {
    // keep the last good config (the defaults when none was ever read)
  }
  return config
}

const dir = join(process.env.XDG_DATA_HOME ?? join(homedir(), ".local", "share"), "opencode", "context-guard")

// A boolean-looking value is a switch, never a path: `true` must not create ./true.
function logPath(setting: string | undefined): string | undefined {
  const value = (setting ?? "").trim()
  if (/^(false|0|off|no)$/i.test(value)) return undefined
  if (value === "" || /^(true|1|on|yes)$/i.test(value)) return join(dir, "context-guard.log")
  return value
}
const logFile = logPath(process.env.OPENCODE_CONTEXT_GUARD_LOG)

// One line per event. A log that cannot be written never breaks a request.
function log(event: string, sessionID: string, detail: string) {
  if (!logFile) return
  try {
    mkdirSync(dirname(logFile), { recursive: true })
    appendFileSync(logFile, `${new Date().toISOString()} ${event} ${sessionID} ${detail}\n`)
  } catch {
    // nothing to do: masking and guarding do not depend on the log
  }
}

// ---- watermark state -------------------------------------------------------------

// tool: newest message whose older tool outputs are masked; reasoning: the same for
// reasoning (its window is wider, so its watermark trails the tool one). floor: the
// estimated request size the last move (or no-gain check) left, valid while floorAt —
// the newest message at that time — is still in the request (a phase_checkpoint cut
// removes it, and the floor with it).
type State = { tool?: string; reasoning?: string; floor?: number; floorAt?: string }
const states = new Map<string, State>()

function load(sessionID: string): State {
  let state = states.get(sessionID)
  if (state) return state
  try {
    state = JSON.parse(readFileSync(join(dir, `${sessionID}.json`), "utf8")) as State
  } catch {
    state = {}
  }
  states.set(sessionID, state)
  return state
}

function save(sessionID: string, state: State) {
  states.set(sessionID, state)
  try {
    mkdirSync(dir, { recursive: true })
    writeFileSync(join(dir, `${sessionID}.json`), JSON.stringify(state, null, 2))
  } catch (err) {
    log("persist-error", sessionID, String(err))
  }
}

// ---- masking ---------------------------------------------------------------------

type Entry = { info: any; parts: any[] }

const REASONING_MASK = "[earlier reasoning omitted]"
const toolMask = (tool: string, chars: number) =>
  `[context-guard: ${tool} output of ${chars} chars masked; re-run the call if you still need it, ` +
  "or fetch only the part you need (code_item, doc_section, dossier_edit section)]"

// A long string field of an old call's input, shortened: the head says what the call
// was; the rest (a heredoc, a payload, a file's content) is on disk or in the dossier.
function shortenInput(input: any, keep: number): { input: any; chars: number } {
  if (!input || typeof input !== "object") return { input, chars: 0 }
  let chars = 0
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(input)) {
    if (typeof v === "string" && v.length > keep + 80) {
      const note = ` … [context-guard: ${v.length - keep} chars of this old input elided]`
      out[k] = v.slice(0, keep) + note
      chars += v.length - keep - note.length
    } else out[k] = v
  }
  return chars > 0 ? { input: out, chars } : { input, chars: 0 }
}

const outputOf = (p: any): string => (typeof p?.state?.output === "string" ? p.state.output : "")

function partChars(p: any): number {
  if (!p) return 0
  if (p.type === "text" || p.type === "reasoning") return String(p.text ?? "").length
  if (p.type === "tool") return JSON.stringify(p.state?.input ?? "").length + outputOf(p).length
  return 0
}

// The estimated size of the request: what the provider reported for the latest assistant
// message (fresh input plus cache reads) and ~chars/4 for everything after it.
function estimate(messages: Entry[]): number {
  let at = -1
  for (let i = messages.length - 1; i >= 0; i--) {
    const t = messages[i].info?.tokens
    if (messages[i].info?.role === "assistant" && t && Number(t.input ?? 0) + Number(t.cache?.read ?? 0) > 0) {
      at = i
      break
    }
  }
  const base = at === -1 ? 0 : Number(messages[at].info.tokens.input ?? 0) + Number(messages[at].info.tokens.cache?.read ?? 0)
  let chars = 0
  for (let i = at + 1; i < messages.length; i++) for (const p of messages[i].parts) chars += partChars(p)
  return base + Math.round(chars / 4)
}

function isProtected(p: any, cfg: Config): boolean {
  if (cfg.protect_tools.includes(p.tool)) return true
  const input = p.state?.input ?? {}
  const target = String(input.filePath ?? input.path ?? input.command ?? "")
  return target !== "" && protectRes.some((re) => re.test(target))
}

type Masked = { outputs: number; inputs: number; reasoning: number; chars: number }

// Masks the older parts of the message list in place (replacing entries by copies).
function applyMask(messages: Entry[], state: State, cfg: Config, reasoningToo: boolean): Masked {
  const result: Masked = { outputs: 0, inputs: 0, reasoning: 0, chars: 0 }
  const toolAt = state.tool ? messages.findIndex((m) => m.info?.id === state.tool) : -1
  const reasonAt = reasoningToo && state.reasoning ? messages.findIndex((m) => m.info?.id === state.reasoning) : -1
  for (let i = 0; i <= Math.max(toolAt, reasonAt); i++) {
    const m = messages[i]
    if (m.info?.role !== "assistant") continue
    let changed = false
    const parts = m.parts.map((p) => {
      if (p?.type === "tool" && i <= toolAt && p.state?.status === "completed") {
        let state = p.state
        if (!isProtected(p, cfg)) {
          const out = outputOf(p)
          const mask = toolMask(String(p.tool), out.length)
          if (out.length > mask.length) {
            result.outputs++
            result.chars += out.length - mask.length
            state = { ...state, output: mask }
          }
        }
        if (cfg.mask_input_tools.includes(p.tool)) {
          const short = shortenInput(state.input, cfg.keep_input_chars)
          if (short.chars > 0) {
            result.inputs++
            result.chars += short.chars
            state = { ...state, input: short.input }
          }
        }
        if (state !== p.state) {
          changed = true
          return { ...p, state }
        }
      }
      if (p?.type === "reasoning" && i <= reasonAt) {
        const text = String(p.text ?? "")
        if (text.length > REASONING_MASK.length) {
          changed = true
          result.reasoning++
          result.chars += text.length - REASONING_MASK.length
          return { ...p, text: REASONING_MASK }
        }
      }
      return p
    })
    if (changed) messages[i] = { info: m.info, parts }
  }
  return result
}

// The message id `keep` assistant steps before the end, or undefined when fewer exist.
function watermarkFor(messages: Entry[], keep: number): string | undefined {
  const ids = messages.filter((m) => m.info?.role === "assistant").map((m) => m.info.id as string)
  const at = ids.length - keep - 1
  return at >= 0 ? ids[at] : undefined
}

function indexOfId(messages: Entry[], id: string | undefined): number {
  return id ? messages.findIndex((m) => m.info?.id === id) : -1
}

// ---- read guard ------------------------------------------------------------------

const sessionAgents = new Map<string, string>()

const DOSSIER_RE = /\/\.discovery\/dossiers\/[^/]+\.md$/
const SOURCE_EXT = new Set([
  ".rs", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".py", ".go", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".swift", ".sh",
])
// limactl as the command run: at the start, or after a separator, `then`, `do`, `exec`,
// `time` or `rtk` — not inside a quoted argument of another command (compose_payloads.py
// carries a TEST_COMMAND that names limactl; refusing it cost the recorded runs a step).
const LIMACTL_RUN_RE = /(?:^|[;&|(]\s*|\b(?:then|do|exec|time)\s+)(?:rtk\s+)?limactl\s+shell\b/m
const CAT_RE = /^\s*cat\s+(?:--\s+)?("[^"]+"|'[^']+'|[^\s|;&<>$`()*?]+)\s*$/

function sizeOf(file: string, cwd: string): { bytes: number; lines: number } | undefined {
  try {
    const abs = isAbsolute(file) ? file : resolve(cwd, file)
    const st = statSync(abs)
    if (!st.isFile()) return undefined
    const text = readFileSync(abs, "utf8")
    return { bytes: st.size, lines: text === "" ? 0 : text.split("\n").length - (text.endsWith("\n") ? 1 : 0) }
  } catch {
    return undefined
  }
}

function bigFileReason(file: string, size: { bytes: number; lines: number }): string {
  const ext = extname(file).toLowerCase()
  const how =
    ext === ".md"
      ? "use doc_section for the heading you need"
      : SOURCE_EXT.has(ext)
        ? "use code_outline for the shape of the file and code_item for one item"
        : "use code_outline / code_item (source) or doc_section (markdown)"
  return `${file} is ${size.bytes} bytes, ${size.lines} lines; reading it whole costs the context of every later step: ${how}, or read with offset and limit.`
}

// The reason to refuse a call, or undefined to allow it.
function refusal(tool: string, args: any, cfg: Config): string | undefined {
  if (tool === "read") {
    const file = String(args?.filePath ?? "")
    if (!file || args?.limit !== undefined) return undefined
    if (protectRes.some((re) => re.test(file))) return undefined
    if (DOSSIER_RE.test(file))
      return `read a dossier by section: python3 ${pluginRepo}/scripts/dossier_edit.py section <dossier> '<heading>' — or read with offset/limit`
    const size = sizeOf(file, process.cwd())
    if (size && size.bytes > cfg.max_read_bytes) return bigFileReason(file, size)
    return undefined
  }
  if (tool === "bash") {
    const command = String(args?.command ?? "")
    if (LIMACTL_RUN_RE.test(command) && /cargo\s+(test|nextest)\b/.test(command) && !command.includes("run_tests.py"))
      return `use python3 ${pluginRepo}/scripts/run_tests.py --vm nightwatch --workdir <X> --env-script deploy/host/test-env.sh -- <command>`
    const cat = CAT_RE.exec(command)
    if (cat) {
      const file = cat[1].replace(/^["']|["']$/g, "")
      const size = sizeOf(file, String(args?.workdir ?? process.cwd()))
      if (size && size.bytes > cfg.max_read_bytes) return bigFileReason(file, size)
    }
  }
  return undefined
}

// ---- plugin ----------------------------------------------------------------------

export const ContextGuardPlugin: Plugin = async () => {
  currentConfig()
  return {
    "chat.params": async (input) => {
      try {
        if (input.sessionID && input.agent) sessionAgents.set(input.sessionID, input.agent)
      } catch {
        // the guard falls back to "unknown agent = not guarded"
      }
    },

    "tool.execute.before": async (input, output) => {
      let reason: string | undefined
      let agent = ""
      try {
        const cfg = currentConfig()
        agent = sessionAgents.get(input.sessionID) ?? ""
        if (!agent || !cfg.guard_agents.includes(agent)) return
        reason = refusal(input.tool, output.args, cfg)
      } catch {
        return // a guard error never breaks a call
      }
      if (!reason) return
      log("refused", input.sessionID, `${agent} ${input.tool}: ${reason}`)
      throw new Error(`context-guard: ${reason}`)
    },

    "experimental.chat.messages.transform": async (_input, output) => {
      try {
        const messages = output.messages as unknown as Entry[]
        const first = messages[0]
        const sessionID = first?.info?.sessionID
        if (!sessionID || first.info.role !== "user") return
        const cfg = currentConfig()
        const agent = String(first.info.agent ?? "")
        if (!cfg.mask_agents.includes(agent)) return
        const reasoningToo = cfg.keep_reasoning_steps > 0 && cfg.mask_reasoning_agents.includes(agent)
        const state = load(sessionID)

        const before = estimate(messages)
        const floorValid = state.floor !== undefined && indexOfId(messages, state.floorAt) !== -1
        const due = Math.max(cfg.trigger_tokens, floorValid ? (state.floor as number) + cfg.regrow_tokens : 0)
        if (before >= due) {
          const tool = watermarkFor(messages, cfg.keep_tool_steps)
          const reasoning = reasoningToo ? watermarkFor(messages, cfg.keep_reasoning_steps) : undefined
          const next: State = { tool: state.tool, reasoning: state.reasoning }
          if (indexOfId(messages, tool) > indexOfId(messages, state.tool)) next.tool = tool
          if (indexOfId(messages, reasoning) > indexOfId(messages, state.reasoning)) next.reasoning = reasoning
          // What this move frees beyond what the current watermark already masks; measured
          // on copies of the list (applyMask replaces entries, never edits them).
          const moved = next.tool !== state.tool || next.reasoning !== state.reasoning
          const now = applyMask(messages.slice(), state, cfg, reasoningToo)
          const masked = moved ? applyMask(messages.slice(), next, cfg, reasoningToo) : now
          const gain = Math.round((masked.chars - now.chars) / 4)
          const newest = messages[messages.length - 1]?.info?.id
          if (moved && gain >= cfg.min_gain_tokens) {
            const after = Math.max(0, before - gain)
            save(sessionID, { ...next, floor: after, floorAt: newest })
            applyMask(messages, next, cfg, reasoningToo)
            log(
              "mask",
              sessionID,
              `watermark ${next.tool ?? next.reasoning}: ~${Math.round(before / 1000)}k -> ~${Math.round(after / 1000)}k tokens, ` +
                `${masked.outputs - now.outputs} outputs, ${masked.inputs - now.inputs} inputs, ` +
                `${masked.reasoning - now.reasoning} reasoning`,
            )
            return
          }
          // Not worth a cache break: hold the watermark and wait for the request to regrow.
          save(sessionID, { ...state, floor: before, floorAt: newest })
          log(
            "floor",
            sessionID,
            `~${Math.round(before / 1000)}k tokens, a move would free ~${Math.round(Math.max(0, gain) / 1000)}k: ` +
              "the rest is unmaskable; only a phase_checkpoint shrinks it",
          )
        }
        if (state.tool || state.reasoning) applyMask(messages, state, cfg, reasoningToo)
      } catch (err) {
        log("error", "-", String(err))
      }
    },
  }
}
