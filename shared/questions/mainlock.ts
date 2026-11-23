// The main-checkout lock, as the dashboard sees it. In a repo that commits `.discovery/` (committed mode) the main
// checkout is never edited by hand or by a run: dossier writes go to the dossier's worktree, or to its notes file
// (scripts/dossier_edit.py). A tracked file changed in the main checkout anyway blocks `git pull` there and hides the
// change from every branch, so the page warns about it until it is committed on a branch or discarded.
import { readFileSync } from "node:fs"
import { activeReposFile } from "./activerepos.ts"
import { shRun } from "./runs.ts"

export type DirtyMain = { repo: string; root: string; files: string[] }
export type Git = (root: string, args: string[]) => Promise<{ code: number; stdout: string }>

const realGit: Git = async (root, args) => {
  const r = await shRun(["git", "-C", root, ...args], undefined, 30_000)
  return { code: r.code, stdout: r.stdout }
}

/** `{repo: main checkout root}` from the active-repos file; empty when it is missing or unreadable. */
export function activeRoots(path = activeReposFile()): Record<string, string> {
  try {
    const j = JSON.parse(readFileSync(path, "utf8"))
    if (!j || typeof j.repos !== "object" || j.repos === null || Array.isArray(j.repos)) return {}
    return Object.fromEntries(Object.entries(j.repos).filter(([, v]) => typeof v === "string")) as Record<string, string>
  } catch { return {} }
}

/** The tracked files changed in each committed-mode main checkout (uncommitted mode and clean checkouts are left out). */
export async function dirtyMains(roots: Record<string, string>, git: Git = realGit): Promise<DirtyMain[]> {
  const out: DirtyMain[] = []
  for (const [repo, root] of Object.entries(roots)) {
    const tracked = await git(root, ["ls-files", "--", ".discovery/dossiers"])
    if (tracked.code !== 0 || !tracked.stdout.trim()) continue   // .discovery/ not committed: the checkout is the store
    const st = await git(root, ["status", "--porcelain", "--untracked-files=no"])
    if (st.code !== 0) continue
    const files = st.stdout.split("\n").filter((l) => l.trim()).map((l) => l.slice(3).trim())
    if (files.length) out.push({ repo, root, files })
  }
  return out
}

let reading: DirtyMain[] = []
export const dirtyMainReading = (): DirtyMain[] => reading
export async function checkDirtyMains(changed: () => void, git: Git = realGit): Promise<DirtyMain[]> {
  const next = await dirtyMains(activeRoots(), git).catch(() => reading)
  if (JSON.stringify(next) !== JSON.stringify(reading)) { reading = next; changed() }
  return reading
}
