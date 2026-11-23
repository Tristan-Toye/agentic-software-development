import { readdirSync, readFileSync } from "node:fs"
import { dirname, isAbsolute, join, resolve } from "node:path"

// The unit test author's read boundary under Claude Code. opencode scopes its `read`
// permission map to the staging area and the test families (sub-agents/unit-test-author.md);
// Claude Code cannot scope a tool in an agent file, so the plugin's PreToolUse hook applies
// this function to every Read the agent makes. shared/test/shared.test.ts fails when the
// directories here drift from that read map.

export const BLIND_AGENT = "agentic-software-development:unit-test-author"

// The directory names the read map admits, as a path segment anywhere in the path.
export const BLIND_READ_DIRS = [".agent-staging", "tests", "test", "__tests__", "spec", "test-support", "testing"]

const ADMITTED = new RegExp(`(?:^|/)(?:${BLIND_READ_DIRS.map((d) => d.replace(/[.]/g, "\\.")).join("|")})/`)

// A co-located test file: its basename says it is a test, wherever it sits (`deploy/scripts/foo_test.sh`
// beside `foo.sh`). Mirrored by `HOOK_READ_FILES` in scripts/check_payload.py and by the `*_test.sh`
// style lines of the unit test author's read and edit maps. The implementation beside it stays sealed.
export const BLIND_READ_FILES = ["*_test.sh", "*_test.py", "*_test.go", "*.test.ts", "*.test.tsx", "*.test.js", "*.spec.ts", "*.spec.js"]

const ADMITTED_FILE = new RegExp(
  `(?:^|/)[^/]+(?:${BLIND_READ_FILES.map((g) => g.slice(1).replace(/[.]/g, "\\.")).join("|")})$`,
)

// A test project of the target repository, whatever its directory is called: the nearest
// ancestor directory holding a csproj that references a test framework (a .NET `*.Tests`
// project or any other). Mirrored by `in_test_project` in scripts/check_payload.py.
const TEST_FRAMEWORK = /Microsoft\.NET\.Test\.Sdk|xunit|nunit|MSTest|TUnit/i

// #515: and any directory whose name ends in `.Tests`, `.UnitTests` or `.IntegrationTests`, so an
// existing file of such a project is admitted even when its csproj names no framework itself
// (central package versions, a props file). Mirrored by `TEST_PROJECT_DIR` in scripts/check_payload.py.
const TEST_PROJECT_DIR = /(?:^|\/)[^/]+\.(?:Unit|Integration)?Tests\//

function inTestProject(abs: string): boolean {
  if (TEST_PROJECT_DIR.test(abs)) return true
  for (let dir = dirname(abs); ; dir = dirname(dir)) {
    try {
      for (const f of readdirSync(dir)) {
        if (f.endsWith(".csproj") && TEST_FRAMEWORK.test(readFileSync(join(dir, f), "utf8"))) return true
      }
    } catch {}
    if (dirname(dir) === dir) return false
  }
}

// The refusal message for a read the blind agent may not make, or undefined to allow.
// `cwd` resolves a relative path; `..` segments are resolved first, so they cannot climb out.
export function blindRead(path: string, cwd: string): string | undefined {
  if (!path) return "a Read with no path"
  const abs = isAbsolute(path) ? resolve(path) : resolve(cwd, path)
  if (ADMITTED.test(abs) || ADMITTED_FILE.test(abs) || inTestProject(abs)) return undefined
  return (
    `${abs} is outside your read boundary: you read only your payload under .agent-staging/ ` +
    `and the test families and test projects (${BLIND_READ_DIRS.filter((d) => d !== ".agent-staging").join(", ")}, ${BLIND_READ_FILES.join(", ")}). ` +
    `Report what you are missing in one GAP: line instead.`
  )
}

// The integration test author's seal. It reads the dossier and any wiring, but never the body
// of a contract member: compose_payloads.py writes `.agent-staging/sealed-paths.txt` (one
// repo-relative path per line: every work package's implementation paths and the contract
// files) beside its payload, and the hook refuses Read, Edit, Write and content-mode Grep of
// those files at any time. The stub text it needs is already in its payload's CONTRACT field.
// No sealed-paths.txt on the way up from the target means nothing is sealed (fail open).

export const INTEGRATION_AGENT = "agentic-software-development:integration-test-author"

const SEALED_FILE = join(".agent-staging", "sealed-paths.txt")

// The root holding the nearest sealed-paths.txt above `abs` and its listed paths, if any.
function sealedSet(abs: string): { root: string; paths: Set<string> } | undefined {
  for (let dir = dirname(abs); ; dir = dirname(dir)) {
    try {
      const text = readFileSync(join(dir, SEALED_FILE), "utf8")
      const paths = new Set(
        text
          .split("\n")
          .map((l) => l.trim())
          .filter((l) => l && !l.startsWith("#"))
          .map((l) => resolve(dir, l)),
      )
      return { root: dir, paths }
    } catch {}
    if (dirname(dir) === dir) return undefined
  }
}

const SEALED_MESSAGE =
  "its body is sealed: the integration test author never reads or edits an implementation file. " +
  "The signatures are in the CONTRACT field of your payload. " +
  "If you need more than that, report it in one GAP: line."

// The refusal message for a Read, Edit or Write of a sealed file, or undefined to allow.
export function sealedRead(path: string, cwd: string): string | undefined {
  if (!path) return undefined
  const abs = isAbsolute(path) ? resolve(path) : resolve(cwd, path)
  const set = sealedSet(abs)
  if (!set || !set.paths.has(abs)) return undefined
  return `${abs}: ${SEALED_MESSAGE}`
}

// A content-mode Grep would print body lines. Refused when its path is a sealed file or a
// directory holding one; files_with_matches and count stay allowed.
export function sealedGrep(path: string | undefined, outputMode: string | undefined, cwd: string): string | undefined {
  if (outputMode !== "content") return undefined
  const abs = path ? (isAbsolute(path) ? resolve(path) : resolve(cwd, path)) : resolve(cwd)
  // sealedSet starts at the parent of its argument; a probe name makes `abs` itself the first directory
  const set = sealedSet(join(abs, "x"))
  if (!set) return undefined
  const dir = abs.endsWith("/") ? abs : abs + "/"
  for (const p of set.paths) {
    if (p === abs || p.startsWith(dir)) {
      return (
        `a content search of ${abs} would print the body of ${p}, which is sealed. ` +
        `Use output_mode files_with_matches or count, or a narrower path that holds no sealed file. ` +
        `The signatures are in the CONTRACT field of your payload; report anything more in one GAP: line.`
      )
    }
  }
  return undefined
}

// The test authors' nav tools: both may look up the contract of an EXISTING function they must
// use (what it promises and refuses: docs + signature, bodies elided) and never a body. They get
// `code_outline` (rows of names, line ranges and first doc lines; no body) and `code_item` in
// `mode: contract` only. Everything else of the nav server, and any other `code_item` mode
// (`full` is the default and prints the whole item), is refused here, on the call's own input.
// Claude Code names a plugin's MCP tool `mcp__plugin_<plugin>_<server>__<tool>`; the tool is the part
// after the last `__`.
export const NAV_CONTRACT_ONLY = "contract"

const NAV_TOOL = /(?:^|__)(code_outline|code_item|code_find|code_replace|doc_section|ref_section|arch_find|phase_checkpoint)$/

// True when `toolName` is a tool of the nav server (the hook's matcher admits it).
export const isNavTool = (toolName: string): boolean => NAV_TOOL.test(toolName)

// The refusal message for a nav call a test author may not make, or undefined to allow.
export function navBodyGuard(toolName: string, toolInput: Record<string, any> | undefined): string | undefined {
  const tool = NAV_TOOL.exec(toolName)?.[1]
  if (!tool || tool === "code_outline") return undefined
  if (tool === "code_item") {
    const mode = toolInput?.mode
    if (mode === NAV_CONTRACT_ONLY) return undefined
    return (
      `code_item with mode ${mode === undefined ? "unset (the default is full: the whole body)" : JSON.stringify(mode)} ` +
      `is refused: you look up an existing function's contract with mode "${NAV_CONTRACT_ONLY}" only ` +
      `(docs + signature, bodies elided). Repeat the call with mode: "${NAV_CONTRACT_ONLY}".`
    )
  }
  return (
    `${tool} is refused: your nav tools are code_outline and code_item with mode "${NAV_CONTRACT_ONLY}". ` +
    `Nothing else of the nav server is open to you, because it can return an implementation body or a dossier.`
  )
}
