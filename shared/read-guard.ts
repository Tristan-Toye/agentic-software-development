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

// ---- Bash reading windows on source and markdown ---------------------------------------

const NAV_EXT = new Set([".rs", ".py", ".md"])

// A command line cut into simple commands at unquoted `&&`, `||`, `;`, `|` and newlines,
// each cut into words with its quotes removed. Approximate shell, enough to see what runs.
export function simpleCommands(line: string): string[][] {
  const out: string[][] = []
  let words: string[] = []
  let word = ""
  let quote = ""
  let inWord = false
  const endWord = () => {
    if (inWord) words.push(word)
    word = ""
    inWord = false
  }
  const endCommand = () => {
    endWord()
    if (words.length) out.push(words)
    words = []
  }
  for (let i = 0; i < line.length; i++) {
    const c = line[i]
    if (quote) {
      if (c === quote) quote = ""
      else if (c === "\\" && quote === '"' && i + 1 < line.length) word += line[++i]
      else word += c
      continue
    }
    if (c === "'" || c === '"') {
      quote = c
      inWord = true
    } else if (c === "\\" && i + 1 < line.length) {
      word += line[++i]
      inWord = true
    } else if (c === ";" || c === "\n" || c === "|" || c === "&") {
      if ((c === "|" || c === "&") && line[i + 1] === c) i++
      else if (c === "&" && line[i + 1] === ">") {
        word += c
        inWord = true
        continue
      }
      endCommand()
    } else if (c === " " || c === "\t") endWord()
    else {
      word += c
      inWord = true
    }
  }
  endCommand()
  return out
}

// The command a simple command runs, past env assignments and wrappers (`rtk`, `time`, `command`).
function program(words: string[]): { name: string; args: string[] } {
  let i = 0
  while (i < words.length && (/^[A-Za-z_][A-Za-z0-9_]*=/.test(words[i]) || ["rtk", "time", "command", "exec"].includes(words[i]))) i++
  return { name: (words[i] ?? "").split("/").pop() ?? "", args: words.slice(i + 1) }
}

// The files a command reads: its arguments, less redirection targets (`> f`, `>>f`, `2> f`).
function navFiles(args: string[]): string[] {
  const out: string[] = []
  for (let i = 0; i < args.length; i++) {
    const a = args[i]
    if (/^\d*>>?$/.test(a) || a === "<") {
      i++
      continue
    }
    if (/^\d*>/.test(a) || a.startsWith("<")) continue
    if (!a.startsWith("-") && NAV_EXT.has(extname(a).toLowerCase())) out.push(a)
  }
  return out
}
const writesHeredoc = (args: string[]) => args.some((a) => a.startsWith("<<"))

function navReason(file: string, how: string, pipelineRoot: string): string {
  const ext = extname(file).toLowerCase()
  const use = DOSSIER_RE.test(file)
    ? `python3 ${pipelineRoot}/scripts/dossier_edit.py section <dossier> '<heading>'`
    : ext === ".md"
      ? "doc_section (several headings at once: `heading A | heading B`)"
      : "code_item (mode contract for the API, members for a type with all its impl blocks, doc for the doc comment, full for the body; several symbols at once: `A | B::c`), or code_outline for the file's map"
  return `${how} ${file} is a read through Bash, one turn per window: use ${use}. Read with offset and limit stays allowed when those cannot parse the file.`
}

// The refusal for a Bash command that reads a window of a source or markdown file, or undefined.
function navRedirect(command: string, cwd: string, cfg: Config, pipelineRoot: string): string | undefined {
  for (const words of simpleCommands(command)) {
    const { name, args } = program(words)
    const files = navFiles(args)
    if (files.length === 0) continue
    const file = files[0]
    if (protectRegexes(cfg).some((re) => re.test(file))) continue
    if (name === "sed" && args.includes("-n")) return navReason(file, "sed -n on", pipelineRoot)
    if (name === "awk" && args.some((a) => /\bNR\s*[<>=]/.test(a))) return navReason(file, "awk NR on", pipelineRoot)
    if (name === "cat" || name === "head" || name === "tail" || name === "less" || name === "bat") {
      if (writesHeredoc(args)) continue
      if (name === "tail" && args.some((a) => a === "-f" || a === "-F")) continue
      const size = sizeOf(file, cwd)
      if (size && size.bytes <= cfg.nav_small_bytes) continue
      return navReason(file, `${name} of`, pipelineRoot)
    }
    if (name === "grep" || name === "rg" || name === "ugrep") {
      const context = args
        .map((a) => /^-(?:[ABC]|-(?:after|before)?-?context=?)(\d*)$/.exec(a) ?? /^-[ABC](\d+)$/.exec(a))
        .map((m, i) => (m ? Number(m[1] || args[i + 1]) : 0))
      if (Math.max(0, ...context.filter(Number.isFinite)) >= cfg.nav_grep_context)
        return navReason(file, `${name} with a context window on`, pipelineRoot)
    }
  }
  return undefined
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
  if (cfg.nav_redirect) {
    const nav = navRedirect(command, cwd, cfg, pipelineRoot)
    if (nav) return nav
  }
  const cat = CAT_RE.exec(command)
  if (cat) {
    const file = cat[1].replace(/^["']|["']$/g, "")
    const size = sizeOf(file, cwd)
    if (size && size.bytes > cfg.max_read_bytes) return bigFileReason(file, size)
  }
  return undefined
}
