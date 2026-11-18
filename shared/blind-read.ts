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

// A test project of the target repository, whatever its directory is called: the nearest
// ancestor directory holding a csproj that references a test framework (a .NET `*.Tests`
// project or any other). Mirrored by `in_test_project` in scripts/check_payload.py.
const TEST_FRAMEWORK = /Microsoft\.NET\.Test\.Sdk|xunit|nunit|MSTest|TUnit/i

function inTestProject(abs: string): boolean {
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
  if (ADMITTED.test(abs) || inTestProject(abs)) return undefined
  return (
    `${abs} is outside your read boundary: you read only your payload under .agent-staging/ ` +
    `and the test families and test projects (${BLIND_READ_DIRS.filter((d) => d !== ".agent-staging").join(", ")}). ` +
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
