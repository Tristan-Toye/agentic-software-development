// The hard block of the owner's agent sessions (the Slack DM agent and the dashboard chat): a mechanical refusal,
// not a prompt rule. Pure functions; shared/claude/hard-block.ts is the PreToolUse hook that calls them.
// Contract: references/dm-agent.md ("The hard block"). Refused:
//   1. merging a pull request: `gh pr merge`, `gh api` with `/merge`, `mergePullRequest` or `AutoMerge` in its args,
//      and a `git push` to production, development, main or master (also bare `git push` while on one of them);
//   2. force and destructive pushes: -f, --force, --force-with-lease, --force-if-includes, `+refspec`, --mirror,
//      --delete / `:ref` of a protected branch;
//   3. secret paths (Read, Edit, Write, NotebookEdit, Grep, Glob; Bash readers: cat less head tail grep sed awk cp mv
//      and the like, `sops -d`, `source` / `.`, `security find-*-password`). A program that merely takes a secret file
//      as an argument (`docker compose --env-file .runtime.env`) is allowed.
import { spawnSync } from "node:child_process"
import { basename, resolve } from "node:path"

export const PROTECTED = ["production", "development", "main", "master"]
export const OWNER_DOES = "the owner does this step themselves (merge from the dashboard Merge button / GitHub)"

export type HardCtx = { home: string; cwd: string; branch?: (dir: string) => string | undefined }

/** The surface the session runs on: `dm` or `chat` (set by askAgent), or `any` when ASD_HARD_BLOCK=1; else undefined. */
export function surfaceOf(env: Record<string, string | undefined>): string | undefined {
  const s = (env.ASD_AGENT_SURFACE || "").trim()
  if (s === "dm" || s === "chat") return s
  return env.ASD_HARD_BLOCK === "1" ? "any" : undefined
}

// ---- secret paths -------------------------------------------------------------------------------------------------

function expand(p: string, home: string): string {
  let s = p
  if (s === "~" || s.startsWith("~/")) s = home + s.slice(1)
  return s.replace(/^\$\{?HOME\}?(?=\/|$)/, home)
}

/** Whether `p` (as typed: `~`, `$HOME` and relative paths are resolved) is a secret path. Globs are tested as text. */
export function isSecretPath(p: string, home: string, cwd: string): boolean {
  if (!p) return false
  const abs = resolve(cwd, expand(p, home))
  const base = basename(abs)
  if (base === ".runtime.env" || base === ".env") return true
  if (base.endsWith(".pem") || base.startsWith("id_rsa")) return true
  if (/(^|\/)deploy\/secrets(\/|$)/.test(abs)) return true
  for (const d of [`${home}/.ssh`, `${home}/.aws`]) if (abs === d || abs.startsWith(d + "/")) return true
  if (abs === `${home}/.config/gh/hosts.yml`) return true
  if (base === ".credentials.json") {
    if (/\/\.claude[^/]*\//.test(abs)) return true
    const cfg = process.env.CLAUDE_CONFIG_DIR
    if (cfg && abs.startsWith(resolve(cfg) + "/")) return true
  }
  return false
}

// ---- bash parsing -------------------------------------------------------------------------------------------------

/** Splits one command line into simple commands (argv arrays), quotes honoured; `$(..)` and backticks are their own commands. */
export function simpleCommands(line: string, depth = 0): string[][] {
  const out: string[][] = []
  if (depth > 4) return out
  // Substitutions first (innermost out), each analysed as a command of its own; the outer text keeps a placeholder.
  let rest = line
  for (let guard = 0; guard < 20; guard++) {
    const m = /\$\(([^()]*)\)|`([^`]*)`/.exec(rest)
    if (!m) break
    out.push(...simpleCommands(m[1] ?? m[2] ?? "", depth + 1))
    rest = rest.slice(0, m.index) + "X" + rest.slice(m.index + m[0].length)
  }
  let cur: string[] = [], word = "", has = false, q = ""
  const endWord = () => { if (has) cur.push(word); word = ""; has = false }
  const endCmd = () => { endWord(); if (cur.length) out.push(cur); cur = [] }
  for (let i = 0; i < rest.length; i++) {
    const c = rest[i]
    if (q === "'") { if (c === "'") q = ""; else word += c; continue }
    if (q === '"') {
      if (c === '"') q = ""
      else if (c === "\\" && i + 1 < rest.length) word += rest[++i]
      else word += c
      continue
    }
    if (c === "'" || c === '"') { q = c; has = true; continue }
    if (c === "\\" && i + 1 < rest.length) { word += rest[++i]; has = true; continue }
    if (c === ";" || c === "\n" || c === "|" || c === "&" || c === "(" || c === ")") { endCmd(); continue }
    if (c === " " || c === "\t") { endWord(); continue }
    word += c; has = true
  }
  endCmd()
  // sh -c '<script>' and eval '<script>': the script is more commands.
  const extra: string[][] = []
  for (const w of out) {
    const n = unwrap(w)
    const name = basename(n[0] ?? "")
    if (["sh", "bash", "zsh", "dash", "ksh"].includes(name)) {
      const k = n.findIndex((a, j) => j > 0 && /^-[a-zA-Z]*c[a-zA-Z]*$/.test(a))
      if (k > 0 && n[k + 1]) extra.push(...simpleCommands(n[k + 1], depth + 1))
    } else if (name === "eval" && n.length > 1) extra.push(...simpleCommands(n.slice(1).join(" "), depth + 1))
  }
  return [...out, ...extra]
}

const WRAPPERS = new Set(["sudo", "env", "command", "builtin", "nohup", "time", "exec", "nice", "xargs", "timeout", "stdbuf", "rtk", "proxy"])

/** Drops leading VAR=value words and wrapper programs (sudo, env, command, rtk, ...) and their flags. */
export function unwrap(w: string[]): string[] {
  let a = [...w]
  for (let guard = 0; guard < 10 && a.length; guard++) {
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(a[0])) { a.shift(); continue }
    if (WRAPPERS.has(basename(a[0]))) {
      const wasTimeout = basename(a[0]) === "timeout" || basename(a[0]) === "nice"
      a.shift()
      while (a.length && (/^-/.test(a[0]) || /^[A-Za-z_][A-Za-z0-9_]*=/.test(a[0]))) a.shift()
      if (wasTimeout && a.length && /^[0-9.]+[smhd]?$/.test(a[0])) a.shift()
      continue
    }
    break
  }
  return a
}

// ---- rules --------------------------------------------------------------------------------------------------------

const PUSH_VALUE_OPTS = new Set(["-o", "--push-option", "--receive-pack", "--exec", "--repo"])
const GIT_VALUE_OPTS = new Set(["-C", "-c", "--git-dir", "--work-tree", "--namespace"])

const bare = (ref: string): string => ref.replace(/^\+/, "").replace(/^refs\/heads\//, "")

function gitRule(args: string[], ctx: HardCtx): string | undefined {
  let dir = ctx.cwd, i = 0
  while (i < args.length && args[i].startsWith("-")) {
    if (args[i] === "-C" && args[i + 1]) dir = resolve(dir, args[i + 1])
    i += GIT_VALUE_OPTS.has(args[i]) ? 2 : 1
  }
  if (args[i] !== "push") return undefined
  const rest = args.slice(i + 1)
  const pos: string[] = []
  let del = false, all = false
  for (let k = 0; k < rest.length; k++) {
    const a = rest[k]
    if (a === "--") { pos.push(...rest.slice(k + 1)); break }
    if (a === "--force" || a === "--force-with-lease" || a === "--force-if-includes" || /^--force(-with-lease|-if-includes)?=/.test(a))
      return `force push refused (${a}); ${OWNER_DOES}`
    if (/^-[a-zA-Z]+$/.test(a) && a.includes("f")) return `force push refused (${a}); ${OWNER_DOES}`
    if (a === "--mirror") return `git push --mirror refused; ${OWNER_DOES}`
    if (a === "--delete" || (/^-[a-zA-Z]+$/.test(a) && a.includes("d"))) { del = true; continue }
    if (a === "--all" || a === "--branches") { all = true; continue }
    if (PUSH_VALUE_OPTS.has(a)) { k++; continue }
    if (a.startsWith("-")) continue
    pos.push(a)
  }
  const refspecs = pos.slice(1)
  for (const r of refspecs) if (r.startsWith("+")) return `force push refused (+refspec ${r}); ${OWNER_DOES}`
  if (all) return `git push --all refused (it would push ${PROTECTED.join(", ")}); ${OWNER_DOES}`
  let head: string | undefined
  const current = () => (head ??= (ctx.branch ?? gitBranch)(dir) ?? "")
  const targets = refspecs.length ? refspecs : ["HEAD"]
  for (const r of targets) {
    const colon = r.indexOf(":")
    const src = bare(colon >= 0 ? r.slice(0, colon) : r)
    const dst = bare(colon >= 0 ? r.slice(colon + 1) : r)
    const name = colon >= 0 ? dst : src === "HEAD" ? current() : src
    const resolved = name === "HEAD" ? current() : name
    if (!PROTECTED.includes(resolved)) continue
    if (del || (colon >= 0 && src === "")) return `deleting the branch ${resolved} refused; ${OWNER_DOES}`
    return `git push to ${resolved} refused (a push of ${r === "HEAD" && !refspecs.length ? "the current branch" : r}); open a pull request instead, ${OWNER_DOES}`
  }
  return undefined
}

function gitBranch(dir: string): string | undefined {
  try {
    const p = spawnSync("git", ["-C", dir, "rev-parse", "--abbrev-ref", "HEAD"], { encoding: "utf8", timeout: 2000 })
    const b = (p.stdout || "").trim()
    return p.status === 0 && b ? b : undefined
  } catch { return undefined }
}

function ghRule(args: string[]): string | undefined {
  const pr = args.indexOf("pr")
  if (pr >= 0 && args.slice(pr + 1).includes("merge")) return `gh pr merge refused; ${OWNER_DOES}`
  if (args[0] === "api" || args.some((a, i) => a === "api" && i < 3)) {
    const hit = args.find((a) => /\/merge|mergePullRequest|AutoMerge/i.test(a) && !/^-/.test(a.split("=")[0]))
    if (hit) return `gh api call refused (${hit.slice(0, 60)} merges a pull request); ${OWNER_DOES}`
  }
  return undefined
}

// Programs that print, copy or decode the files they are given.
const READERS = new Set([
  "cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "rg", "ag", "sed", "awk", "gawk", "cp", "mv", "bat", "tac", "nl", "od", "xxd",
  "strings", "base64", "cut", "sort", "diff", "jq", "yq", "scp", "rsync", "paste", "tee", "vi", "vim", "nano",
])
const PATTERN_FIRST = new Set(["grep", "egrep", "fgrep", "rg", "ag", "sed", "awk", "gawk"])
const KEYCHAIN = /^(find-(generic|internet)-password|dump-keychain|export)$/

function secretRule(name: string, args: string[], ctx: HardCtx): string | undefined {
  const secret = (a: string) => {
    const v = a.includes("=") && a.startsWith("-") ? a.slice(a.indexOf("=") + 1) : a
    return isSecretPath(v, ctx.home, ctx.cwd)
  }
  const refuse = (why: string) => `${why}: a secret (token, key, .env, credential); ${OWNER_DOES}`
  if (name === "security" && args.some((a) => KEYCHAIN.test(a))) return refuse("reading the macOS keychain")
  if (name === "sops" && args.some((a) => a === "-d" || a === "--decrypt" || a === "decrypt")) return refuse("sops decrypt")
  if (name === "source" || name === ".") {
    const hit = args.find(secret)
    return hit ? refuse(`${name} ${hit}`) : undefined
  }
  if (!READERS.has(name)) return undefined
  let cand = args
  if (PATTERN_FIRST.has(name) && !args.some((a) => /^(-[a-zA-Z]*[ef]|--regexp|--file|--expression)/.test(a))) {
    const k = args.findIndex((a) => !a.startsWith("-"))
    if (k >= 0) cand = args.filter((_, j) => j !== k)
  }
  const hit = cand.find(secret)
  return hit ? refuse(`${name} of ${hit}`) : undefined
}

/** The reason a Bash command is refused, or undefined. */
export function bashRule(command: string, ctx: HardCtx): string | undefined {
  for (const w of simpleCommands(command)) {
    const a = unwrap(w)
    if (!a.length) continue
    const name = basename(a[0])
    const args = a.slice(1)
    const why = name === "git" ? gitRule(args, ctx) : name === "gh" ? ghRule(args) : secretRule(name, args, ctx)
    if (why) return why
  }
  return undefined
}

/** The reason a tool call is refused, or undefined. */
export function hardBlock(tool: string, input: Record<string, any>, ctx: HardCtx): string | undefined {
  const refuse = (p: string) => `${tool} of ${p} refused: a secret path (.runtime.env, .env, deploy/secrets, keys, ~/.ssh, ~/.aws, gh and Claude credentials); ${OWNER_DOES}`
  switch (tool) {
    case "Bash": return bashRule(String(input.command ?? ""), ctx)
    case "Read": case "Edit": case "Write": case "MultiEdit": {
      const p = String(input.file_path ?? "")
      return isSecretPath(p, ctx.home, ctx.cwd) ? refuse(p) : undefined
    }
    case "NotebookEdit": {
      const p = String(input.notebook_path ?? "")
      return isSecretPath(p, ctx.home, ctx.cwd) ? refuse(p) : undefined
    }
    case "Grep": case "Glob": {
      const dir = String(input.path ?? "")
      // Grep's `pattern` is a regex, not a path: only its `glob` is; Glob's `pattern` is the glob.
      const g = input[tool === "Glob" ? "pattern" : "glob"]
      const pats = typeof g === "string" && g ? [g] : []
      const cands = [dir, ...pats]
      if (dir) for (const p of pats) cands.push(`${dir}/${p}`)
      const hit = cands.find((c) => c && isSecretPath(c, ctx.home, ctx.cwd))
      return hit ? refuse(hit) : undefined
    }
    default: return undefined
  }
}
