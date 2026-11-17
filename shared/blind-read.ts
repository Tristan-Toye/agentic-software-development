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
