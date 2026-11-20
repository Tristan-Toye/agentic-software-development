// The active repos: ~/.agentic/active-repos.json (ASD_ACTIVE_REPOS_FILE), `{"repos": {"<repo>": "<main checkout root>"}}`,
// the same file scripts/run_dossier.py reads for its reserved run slots (`run_dossier.py active` edits it). Without the file
// every repo is shown; with it only these are. The one helper the dashboard filters through: candidateRoots (runs.ts) feeds
// the runs view, the stubs view and, through them, the auto-work facts.
import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

export const activeReposFile = (): string => process.env.ASD_ACTIVE_REPOS_FILE || join(homedir(), ".agentic", "active-repos.json")

/** The active repo names, or null when the file is missing or unreadable (then nothing is filtered). */
export function activeRepos(path = activeReposFile()): Set<string> | null {
  try {
    const j = JSON.parse(readFileSync(path, "utf8"))
    if (!j || typeof j.repos !== "object" || j.repos === null || Array.isArray(j.repos)) return null
    return new Set(Object.keys(j.repos))
  } catch { return null }
}

/** Whether the dashboard shows `repo`: every repo without the file, only the listed ones with it. */
export const repoShown = (repo: string, active: Set<string> | null = activeRepos()): boolean => active === null || active.has(repo)
