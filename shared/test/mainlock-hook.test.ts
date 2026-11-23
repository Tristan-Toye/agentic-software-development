// Run: bun --no-install shared/test/mainlock-hook.test.ts
// The main-checkout lock hook: the rules (shared/main-lock.ts) against real throwaway repos (a committed-mode main
// checkout, its linked worktree, an uncommitted-mode repo), and the hook as Claude Code runs it.
import { mkdirSync, mkdtempSync, realpathSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { spawnSync } from "node:child_process"

const { mainLock, segments, words, lockedRoot } = await import("../main-lock.ts")
const tmp = realpathSync(mkdtempSync(join(tmpdir(), "mainlock-")))
const sh = (cwd: string, ...args: string[]) => { const r = spawnSync("git", args, { cwd, encoding: "utf8" }); if (r.status !== 0) throw new Error(r.stderr) }

const main = join(tmp, "repo"), wt = join(tmp, "repo-wt"), plain = join(tmp, "plain")
for (const d of [main, plain]) {
  mkdirSync(join(d, ".discovery", "dossiers"), { recursive: true })
  sh(d, "init", "-q", "-b", "main"); sh(d, "config", "user.email", "t@e"); sh(d, "config", "user.name", "t")
  writeFileSync(join(d, ".discovery", "dossiers", "W-1.md"), "---\nid: W-1\n---\n")
  writeFileSync(join(d, "src.rs"), "fn main() {}\n")
  writeFileSync(join(d, ".gitignore"), d === plain ? ".discovery/\n.agent-staging/\n" : ".agent-staging/\n")
  sh(d, "add", "-A"); sh(d, "commit", "-qm", "init")
}
writeFileSync(join(main, ".git", "info", "exclude"), ".discovery/notes/\n")
sh(main, "worktree", "add", "-q", "-b", "feat/x", wt)

const fails: string[] = []
let n = 0
const check = (name: string, ok: boolean) => { n++; ok || fails.push(name) }
const deny = (name: string, tool: string, input: object, cwd = main) => check(`deny: ${name}`, !!mainLock(tool, input as any, cwd))
const allow = (name: string, tool: string, input: object, cwd = main) => check(`allow: ${name}`, mainLock(tool, input as any, cwd) === undefined)

// ---- which checkouts are locked ---------------------------------------------------------------------------------
check("locked: the committed-mode main checkout", lockedRoot(join(main, "src.rs")) === main)
check("locked: a new file's path resolves through its existing parent", lockedRoot(join(main, "new", "deep", "f.md")) === main)
check("not locked: the linked worktree", lockedRoot(join(wt, "src.rs")) === undefined)
check("not locked: an uncommitted-mode repo", lockedRoot(join(plain, "src.rs")) === undefined)
check("not locked: outside git", lockedRoot(join(tmp, "nowhere.txt")) === undefined)

// ---- file tools -------------------------------------------------------------------------------------------------
deny("Edit of a dossier", "Edit", { file_path: join(main, ".discovery", "dossiers", "W-1.md") })
deny("Write of source", "Write", { file_path: join(main, "src.rs") })
deny("Write of a new tracked-to-be file", "Write", { file_path: join(main, "docs", "x.md") })
deny("MultiEdit, relative path", "MultiEdit", { file_path: "src.rs" })
deny("NotebookEdit", "NotebookEdit", { notebook_path: join(main, "n.ipynb") })
allow("Write of the notes store (excluded)", "Write", { file_path: join(main, ".discovery", "notes", "W-1.md") })
allow("Write under .agent-staging (ignored)", "Write", { file_path: join(main, ".agent-staging", "p.md") })
allow("Edit in the worktree", "Edit", { file_path: join(wt, ".discovery", "dossiers", "W-1.md") }, wt)
allow("Edit in an uncommitted-mode repo", "Edit", { file_path: join(plain, "src.rs") }, plain)
allow("Read is never refused", "Read", { file_path: join(main, "src.rs") })
check("env ASD_MAIN_LOCK=0 switches it off", mainLock("Edit", { file_path: join(main, "src.rs") }, main, undefined, { ASD_MAIN_LOCK: "0" }) === undefined)
check("the reason names the way out", /dossier_edit\.py/.test(mainLock("Edit", { file_path: join(main, "src.rs") }, main) ?? "") && /worktree add/.test(mainLock("Edit", { file_path: join(main, "src.rs") }, main) ?? ""))

// ---- Bash -------------------------------------------------------------------------------------------------------
const b = (command: string) => ({ command })
for (const c of ["git pull", "git pull --ff-only", "git fetch -q origin", "git worktree add ../w feat/y", "git worktree remove ../w", "git status --porcelain",
  "git log --oneline -3", "git diff HEAD~1", "git show HEAD", "git branch -D old", "git -C . rev-parse HEAD", "cat src.rs", "grep -rn x .",
  "ls > /dev/null", "echo hi 2>&1", "python3 scripts/dossier_edit.py set W-1 status done", "cargo test", "git status && git pull",
  "echo x > .agent-staging/a.md", "echo x > .discovery/notes/W-1.md", "ASD_MAIN_LOCK=0 git checkout -- .discovery/dossiers",
  `echo "a > b; git commit" `, "git log --format='%h > %s'"]) allow(`bash ${c}`, "Bash", b(c))
for (const c of ["git commit -m x", "git add -A", "git checkout -- src.rs", "git switch -c y", "git merge origin/x", "git rebase main", "git reset --hard",
  "git stash", "git restore src.rs", "git cherry-pick abc", "git apply p.patch", "git push origin HEAD", "git -c a=b commit -m x",
  "echo x > src.rs", "echo x >> .discovery/dossiers/W-1.md", "printf x | tee src.rs", "sed -i '' s/a/b/ src.rs", "perl -pi -e s/a/b/ src.rs",
  "git status; git commit -am x", "FOO=1 git commit -m x", "cd sub && cd .. && git add ."]) deny(`bash ${c}`, "Bash", b(c))
deny("bash cd into the main checkout from elsewhere", "Bash", b(`cd ${main} && git commit -m x`), tmp)
deny("bash git -C main checkout from elsewhere", "Bash", b(`git -C ${main} commit -m x`), tmp)
allow("bash cd into the worktree from the main checkout", "Bash", b(`cd ${wt} && git commit -m x && echo y > src.rs`))
allow("bash git -C worktree", "Bash", b(`git -C ${wt} add -A`))
allow("bash anything in an uncommitted-mode repo", "Bash", b("git commit -am x && echo y > src.rs"), plain)
check("segments: quotes keep separators", JSON.stringify(segments(`echo "a; b" && ls | wc`)) === JSON.stringify(['echo "a; b"', "ls", "wc"]))
check("words: redirects split out", JSON.stringify(words("echo x>out 2>&1")) === JSON.stringify(["echo", "x", ">", "out"]))

// ---- the hook, as Claude Code runs it ---------------------------------------------------------------------------
const root = join(import.meta.dir, "..", "..")
const run = (stdin: string, env: Record<string, string> = {}) => {
  const e: Record<string, string> = { ...(process.env as Record<string, string>), XDG_DATA_HOME: tmp, ...env }
  if (!("ASD_MAIN_LOCK" in env)) delete e.ASD_MAIN_LOCK
  const p = Bun.spawnSync(["bun", "--no-install", join(root, "shared", "claude", "main-lock.ts")], { stdin: new TextEncoder().encode(stdin), env: e, stdout: "pipe", stderr: "pipe" })
  return { out: p.stdout.toString().trim(), code: p.exitCode }
}
const call = (tool_name: string, tool_input: object, cwd = main) => JSON.stringify({ hook_event_name: "PreToolUse", session_id: "s1", cwd, tool_name, tool_input })
let r = run(call("Edit", { file_path: join(main, "src.rs") }))
const o = r.out ? JSON.parse(r.out) : undefined
check("hook: an edit in the main checkout is denied with the PreToolUse JSON", r.code === 0 && o?.hookSpecificOutput?.permissionDecision === "deny" && /main-checkout lock/.test(o.hookSpecificOutput.permissionDecisionReason))
check("hook: the same edit in the worktree is allowed", run(call("Edit", { file_path: join(wt, "src.rs") }, wt)).out === "")
check("hook: ASD_MAIN_LOCK=0 in the session allows it", run(call("Edit", { file_path: join(main, "src.rs") }), { ASD_MAIN_LOCK: "0" }).out === "")
r = run("not json")
check("hook: unparseable input fails open", r.code === 0 && r.out === "")

if (fails.length) { for (const f of fails) console.log(`FAIL ${f}`); console.log(`${fails.length}/${n} failed`); process.exit(1) }
console.log(`PASS ${n} checks`)
