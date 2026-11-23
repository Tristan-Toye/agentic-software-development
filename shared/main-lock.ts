// The main-checkout lock, as a rule over one tool call. In a repo that commits `.discovery/` (committed mode) the main
// checkout is read-only: it is only pulled, fetched and used to add worktrees. Dossier writes go to the dossier's
// worktree or its notes file (scripts/dossier_edit.py); everything else is built in a worktree. A tracked file edited
// in the main checkout blocks `git pull` there and is invisible to every branch, so the hook refuses the edit up front.
//
// Refused, when the target is in a committed-mode main checkout:
//   - Edit, Write, MultiEdit, NotebookEdit of a path git does not ignore;
//   - a Bash command run there (its cwd, a leading `cd <dir>`, or `git -C <dir>`) that is a mutating git subcommand,
//     an in-place edit (`sed -i`, `perl -i`), or a redirect/`tee` into a path git does not ignore.
// Allowed: everything in a worktree, in uncommitted mode, outside git; git pull/fetch/worktree/status/log/show/diff
// and the other read-only subcommands; ignored paths (the notes store, .agent-staging/). `ASD_MAIN_LOCK=0` as a
// command prefix, or in the session's environment, switches it off for that call.
import { spawnSync } from "node:child_process"
import { existsSync, statSync } from "node:fs"
import { dirname, isAbsolute, resolve } from "node:path"

export type Git = (dir: string, args: string[]) => { code: number; stdout: string }

export const realGit: Git = (dir, args) => {
  const r = spawnSync("git", ["-C", dir, ...args], { encoding: "utf8", timeout: 3000 })
  return { code: r.status ?? 1, stdout: r.stdout ?? "" }
}

/** The main checkout root when `path` sits in a committed-mode main checkout, else undefined. */
export function lockedRoot(path: string, git: Git = realGit): string | undefined {
  let dir = path
  while (dir && !existsSync(dir)) { const up = dirname(dir); if (up === dir) return undefined; dir = up }
  try { if (!statSync(dir).isDirectory()) dir = dirname(dir) } catch { return undefined }
  const r = git(dir, ["rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"])
  if (r.code !== 0) return undefined
  const [top, common] = r.stdout.trim().split("\n")
  if (!top || !common || resolve(common) !== resolve(top, ".git")) return undefined   // a linked worktree
  const tracked = git(top, ["ls-files", "--", ".discovery/dossiers"])
  return tracked.code === 0 && tracked.stdout.trim() ? top : undefined
}

const ignored = (root: string, path: string, git: Git) => git(root, ["check-ignore", "-q", "--", path]).code === 0

const READ_ONLY_GIT = new Set(["pull", "fetch", "worktree", "status", "log", "show", "diff", "branch", "rev-parse", "ls-files",
  "ls-remote", "check-ignore", "config", "remote", "blame", "grep", "describe", "shortlog", "cat-file", "rev-list",
  "merge-base", "for-each-ref", "symbolic-ref", "name-rev", "reflog", "help", "version", "show-ref", "var"])

const reason = (root: string, what: string) =>
  `main-checkout lock: ${what} in ${root}, the main checkout of a repo that commits .discovery/. It is read-only (pull, ` +
  `fetch, worktree add only). Edit a dossier with scripts/dossier_edit.py (it writes the dossier's worktree or its notes ` +
  `file), and build in a worktree: git -C ${root} worktree add ../<name> <branch>. Prefix ASD_MAIN_LOCK=0 only when the ` +
  `owner asked for this exact change.`

const FILE_TOOLS: Record<string, string> = { Edit: "file_path", Write: "file_path", MultiEdit: "file_path", NotebookEdit: "notebook_path" }

/** The refusal reason for one tool call, or undefined to allow it. */
export function mainLock(tool: string, input: Record<string, any>, cwd: string, git: Git = realGit, env: Record<string, string | undefined> = {}): string | undefined {
  if (env.ASD_MAIN_LOCK === "0") return undefined
  const key = FILE_TOOLS[tool]
  if (key) {
    const p = input[key]
    if (typeof p !== "string" || !p) return undefined
    const abs = isAbsolute(p) ? p : resolve(cwd, p)
    const root = lockedRoot(abs, git)
    if (!root || ignored(root, abs, git)) return undefined
    return reason(root, `${tool} of ${abs}`)
  }
  if (tool !== "Bash" || typeof input.command !== "string") return undefined
  return bashLock(input.command, cwd, git)
}

/** Splits a command line into simple commands (on ; && || | and newlines), keeping quoted text intact. */
export function segments(command: string): string[] {
  const out: string[] = []
  let cur = "", q = ""
  for (let i = 0; i < command.length; i++) {
    const c = command[i]
    if (q) { cur += c; if (c === q) q = ""; else if (c === "\\" && q === '"' && i + 1 < command.length) cur += command[++i]; continue }
    if (c === "'" || c === '"') { q = c; cur += c; continue }
    if (c === ";" || c === "\n" || c === "|" || (c === "&" && command[i + 1] === "&")) {
      if (cur.trim()) out.push(cur.trim())
      cur = ""
      if ((c === "|" || c === "&") && command[i + 1] === c) i++
      continue
    }
    cur += c
  }
  if (cur.trim()) out.push(cur.trim())
  return out
}

/** Shell words of one simple command, quotes removed (no expansion). */
export function words(seg: string): string[] {
  const out: string[] = []
  let cur = "", q = "", any = false
  for (let i = 0; i < seg.length; i++) {
    const c = seg[i]
    if (q) { if (c === q) q = ""; else if (c === "\\" && q === '"' && i + 1 < seg.length) cur += seg[++i]; else cur += c; continue }
    if (c === "'" || c === '"') { q = c; any = true; continue }
    if (/\s/.test(c)) { if (cur || any) out.push(cur); cur = ""; any = false; continue }
    if (c === ">" ) {   // a redirect is its own word: `>`, `>>`, `2>`
      if (cur && !/^\d$/.test(cur)) out.push(cur)
      cur = ""; any = false
      if (seg[i + 1] === ">") i++
      if (seg[i + 1] === "&") { i++; while (/[\d-]/.test(seg[i + 1] ?? "")) i++; continue }   // 2>&1, >&2, >&-
      out.push(">")
      continue
    }
    cur += c
  }
  if (cur || any) out.push(cur)
  return out
}

function bashLock(command: string, cwd: string, git: Git): string | undefined {
  let dir = cwd
  for (const seg of segments(command)) {
    let w = words(seg)
    while (w.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(w[0])) {
      if (w[0] === "ASD_MAIN_LOCK=0") return undefined
      w = w.slice(1)
    }
    if (!w.length) continue
    if (w[0] === "cd" || w[0] === "pushd") { if (w[1] && !w[1].startsWith("-")) dir = isAbsolute(w[1]) ? w[1] : resolve(dir, w[1].replace(/^~(?=\/|$)/, process.env.HOME ?? "~")); continue }
    let here = dir
    const cmd = w[0].split("/").pop()
    // redirects and tee: the target path decides
    const targets: string[] = []
    w.forEach((x, i) => { if (x === ">" && w[i + 1]) targets.push(w[i + 1]) })
    if (cmd === "tee") targets.push(...w.slice(1).filter((x) => !x.startsWith("-") && x !== ">"))
    for (const t of targets) {
      if (t === "/dev/null" || t.startsWith("/dev/")) continue
      const abs = isAbsolute(t) ? t : resolve(here, t)
      const root = lockedRoot(abs, git)
      if (root && !ignored(root, abs, git)) return reason(root, `writing ${abs}`)
    }
    if ((cmd === "sed" || cmd === "perl") && w.some((x) => /^-[a-zA-Z]*i/.test(x) || x === "--in-place" || x.startsWith("--in-place="))) {
      for (const f of w.slice(1).filter((x) => !x.startsWith("-") && x !== ">")) {
        const abs = isAbsolute(f) ? f : resolve(here, f)
        if (!existsSync(abs)) continue
        const root = lockedRoot(abs, git)
        if (root && !ignored(root, abs, git)) return reason(root, `${cmd} -i on ${abs}`)
      }
      continue
    }
    if (cmd !== "git") continue
    let i = 1
    while (i < w.length && w[i].startsWith("-")) {
      if (w[i] === "-C" && w[i + 1]) { here = isAbsolute(w[i + 1]) ? w[i + 1] : resolve(here, w[i + 1]); i += 2; continue }
      if (w[i] === "-c" || w[i] === "--git-dir" || w[i] === "--work-tree") { i += 2; continue }
      i++
    }
    const sub = w[i]
    if (!sub || READ_ONLY_GIT.has(sub)) {
      if (sub === "branch" && w.slice(i + 1).some((x) => /^-(d|D|m|M|c|C|f)$|^--(delete|move|copy|force)$/.test(x) || !x.startsWith("-"))) {
        // creating, renaming or deleting a branch does not touch the work tree: allowed
      }
      continue
    }
    const root = lockedRoot(here, git)
    if (root) return reason(root, `git ${sub}`)
  }
  return undefined
}
