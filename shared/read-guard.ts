import { readFileSync, statSync } from "node:fs"
import { extname, isAbsolute, resolve } from "node:path"
import { protectRegexes, type Config } from "./config"

// The read guard as a pure function: the refusal message for a read or a bash call that
// would put a whole dossier or a large file into an orchestrator's context, or undefined
// to allow. Both hosts normalise their tool input into a ReadGuardCall and act on the
// message (opencode throws, Claude Code denies in a PreToolUse hook).

export type ReadGuardCall =
  | { kind: "read"; path: string; offset?: number; limit?: number }
  | { kind: "bash"; command: string }

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

// `cwd` resolves relative paths; `pipelineRoot` is the pipeline repo the refusal's script
// paths point into.
export function readGuard(call: ReadGuardCall, cwd: string, cfg: Config, pipelineRoot: string): string | undefined {
  if (call.kind === "read") {
    const file = call.path
    if (!file || call.limit !== undefined) return undefined
    if (protectRegexes(cfg).some((re) => re.test(file))) return undefined
    if (DOSSIER_RE.test(file))
      return `read a dossier by section: python3 ${pipelineRoot}/scripts/dossier_edit.py section <dossier> '<heading>' — or read with offset/limit`
    const size = sizeOf(file, cwd)
    if (size && size.bytes > cfg.max_read_bytes) return bigFileReason(file, size)
    return undefined
  }
  const command = call.command
  if (LIMACTL_RUN_RE.test(command) && /cargo\s+(test|nextest)\b/.test(command) && !command.includes("run_tests.py"))
    return `use python3 ${pipelineRoot}/scripts/run_tests.py --vm nightwatch --workdir <X> --env-script deploy/host/test-env.sh -- <command>`
  const cat = CAT_RE.exec(command)
  if (cat) {
    const file = cat[1].replace(/^["']|["']$/g, "")
    const size = sizeOf(file, cwd)
    if (size && size.bytes > cfg.max_read_bytes) return bigFileReason(file, size)
  }
  return undefined
}
