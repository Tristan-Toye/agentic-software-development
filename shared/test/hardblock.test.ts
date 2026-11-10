// Run: bun --no-install shared/test/hardblock.test.ts
// The hard block of the DM agent and the dashboard chat: the rules (shared/hard-block.ts), the hook as Claude Code runs
// it (a process fed hook JSON on stdin, marker on or off), and the argv/env askAgent gives both surfaces.
import { mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const tmp = mkdtempSync(join(tmpdir(), "hardblock-"))
process.env.ASD_DM_AGENT_FILE = join(tmp, "dm.json")
const root = join(import.meta.dir, "..", "..")
const { hardBlock, surfaceOf } = await import("../hard-block.ts")
const { askAgent, claudeArgs, hardBlockSettings } = await import("../questions/dmagent.ts")
const { say } = await import("../questions/pagechat.ts")

const fails: string[] = []
let n = 0
const check = (name: string, ok: boolean) => { n++; ok || fails.push(name) }

const HOME = "/home/o"
let branch = "feat/x"
const ctx = { home: HOME, cwd: "/work/repo/deploy/compose", branch: () => branch }
const bash = (command: string) => hardBlock("Bash", { command }, ctx)
const refused = (c: string) => check(`refuse: ${c}`, !!bash(c))
const allowed = (c: string) => check(`allow: ${c}`, bash(c) === undefined)

// ---- merging ------------------------------------------------------------------------------------------------------
for (const c of ["gh pr merge 12", "gh pr merge 12 --squash --auto", "gh -R o/r pr merge 3", "gh pr -R o/r merge 3", "cd x && gh pr merge",
  "gh api -X PUT repos/o/r/pulls/12/merge", "gh api repos/o/r/pulls/12/merge -f merge_method=squash",
  "gh api graphql -f query='mutation{mergePullRequest(input:{pullRequestId:\"x\"}){clientMutationId}}'",
  "gh api graphql -f query='mutation{enablePullRequestAutoMerge(input:{pullRequestId:\"x\"}){clientMutationId}}'",
  "bash -c 'gh pr merge 1'", "echo $(gh pr merge 1)", "/usr/local/bin/gh pr merge 1", "rtk gh pr merge 1"]) refused(c)
for (const c of ["gh pr create --title t", "gh pr view 12 --json mergeable", "gh pr list", "gh pr checks 12", "gh api repos/o/r/pulls/12", "gh api repos/o/r/issues"]) allowed(c)

for (const c of ["git push origin production", "git push origin development", "git push origin main", "git push origin master", "git push -u origin HEAD:production",
  "git push origin feat/x:main", "git push origin refs/heads/main", "git -C /x push origin main", "git push --all origin"]) refused(c)
branch = "production"
for (const c of ["git push", "git push origin", "git push origin HEAD"]) refused(c)
branch = "feat/x"
for (const c of ["git push", "git push origin", "git push -u origin feat/x", "git push origin HEAD", "git push origin feat/production", "git push origin HEAD:feat/y",
  "git push --tags", "git merge production", "git commit -m 'merge main'", "git pull origin production", "git fetch origin production", "git push origin v1.0"]) allowed(c)

// ---- force pushes -------------------------------------------------------------------------------------------------
for (const c of ["git push -f", "git push --force origin feat/x", "git push origin feat/x --force-with-lease", "git push --force-with-lease=feat/x origin feat/x",
  "git push --force-if-includes origin feat/x", "git push origin +feat/x", "git push origin +HEAD:feat/x", "git push -fu origin feat/x", "git push --mirror",
  "git push --delete origin production", "git push origin :production", "git push origin --delete main", "git push origin :refs/heads/development"]) refused(c)
for (const c of ["git push --delete origin feat/old", "git push origin :feat/old", "git push --follow-tags origin feat/x", "git push --no-verify origin feat/x", "git push -o ci.skip origin feat/x"]) allowed(c)

// ---- secret paths: tools ------------------------------------------------------------------------------------------
const tool = (t: string, i: Record<string, any>) => hardBlock(t, i, ctx)
for (const [t, i] of [
  ["Read", { file_path: "/x/y/.runtime.env" }], ["Read", { file_path: "../../.runtime.env" }], ["Edit", { file_path: "/r/deploy/secrets/db.yaml" }],
  ["Write", { file_path: "/r/app/.env" }], ["Read", { file_path: "/r/keys/server.pem" }], ["Read", { file_path: "/r/id_rsa" }], ["Read", { file_path: "/r/id_rsa.pub" }],
  ["Read", { file_path: `${HOME}/.ssh/config` }], ["Read", { file_path: "~/.aws/credentials" }], ["Read", { file_path: `${HOME}/.config/gh/hosts.yml` }],
  ["Read", { file_path: `${HOME}/.claude/.credentials.json` }], ["Read", { file_path: `${HOME}/.claude-work/.credentials.json` }],
  ["NotebookEdit", { notebook_path: "/r/deploy/secrets/n.ipynb" }], ["Grep", { pattern: "x", path: `${HOME}/.ssh` }], ["Grep", { pattern: "x", path: "/r", glob: "**/.env" }],
  ["Glob", { pattern: "**/.runtime.env" }], ["Glob", { pattern: "*.pem", path: "/r" }], ["Glob", { pattern: "**", path: `${HOME}/.aws` }],
] as [string, Record<string, any>][]) check(`tool refuses ${t} ${JSON.stringify(i)}`, !!tool(t, i))
for (const [t, i] of [
  ["Read", { file_path: "/r/README.md" }], ["Read", { file_path: "/r/.env.example" }], ["Read", { file_path: `${HOME}/.claude/settings.json` }], ["Edit", { file_path: "/r/deploy/compose.yml" }],
  ["Write", { file_path: "/r/src/environment.ts" }], ["Grep", { pattern: "\\.env", path: "/r/src" }], ["Grep", { pattern: "x", glob: "*.ts" }], ["Glob", { pattern: "**/*.ts" }],
  ["WebFetch", { url: "https://x" }],
] as [string, Record<string, any>][]) check(`tool allows ${t} ${JSON.stringify(i)}`, tool(t, i) === undefined)

// ---- secret paths: bash -------------------------------------------------------------------------------------------
for (const c of ["cat .runtime.env", "cat ../../.runtime.env", "less deploy/secrets/a.yaml", "head -5 /r/.env", "tail -f ~/.ssh/id_rsa", "grep TOKEN .runtime.env", "grep -rn TOKEN ~/.aws",
  "sed -n 1,5p .runtime.env", "awk '{print}' /r/.env", "cp .runtime.env /tmp/x", "mv ~/.ssh/id_rsa /tmp", "cat $HOME/.config/gh/hosts.yml", "cat ${HOME}/.aws/credentials",
  "sops -d deploy/secrets/a.enc.yaml", "sops --decrypt x.yaml", "sops decrypt x.yaml", "source .runtime.env", ". ./.runtime.env", "set -a; source ../../.runtime.env; set +a",
  "security find-generic-password -s x -w", "security find-internet-password -a me", "cat < .env", "echo $(cat .runtime.env)", "bash -c 'cat .runtime.env'", "sudo cat ~/.ssh/id_rsa",
  "FOO=1 cat .env", "export X=`cat .env`", "cat a.txt | head ~/.claude/.credentials.json", "cat server.pem", "grep -f .env file", "cat --show-all .env"]) refused(c)
for (const c of ["docker compose --env-file ../../.runtime.env up -d", "docker compose --env-file ../../.runtime.env config --quiet", "docker compose -f compose.yml --env-file=../../.runtime.env ps",
  "cat README.md", "grep -rn '.env' src", "grep .env README.md", "ls -la .runtime.env", "test -f .runtime.env && echo yes", "git check-ignore .runtime.env", "sops exec-env deploy/secrets/a.yaml 'make x'",
  "security list-keychains", "cat .env.example", "ssh-keygen -l -f key.pub", "echo '.runtime.env is not read'", "python3 app.py --config .runtime.env"]) allowed(c)

// ---- the marker ---------------------------------------------------------------------------------------------------
check("marker: dm and chat switch it on", surfaceOf({ ASD_AGENT_SURFACE: "dm" }) === "dm" && surfaceOf({ ASD_AGENT_SURFACE: "chat" }) === "chat")
check("marker: ASD_HARD_BLOCK=1 switches it on for any session", surfaceOf({ ASD_HARD_BLOCK: "1" }) === "any")
check("marker: unset, empty, other values and ASD_HARD_BLOCK=0 are off", surfaceOf({}) === undefined && surfaceOf({ ASD_AGENT_SURFACE: "" }) === undefined
  && surfaceOf({ ASD_AGENT_SURFACE: "runner" }) === undefined && surfaceOf({ ASD_HARD_BLOCK: "0" }) === undefined)

// ---- the hook, as Claude Code runs it -----------------------------------------------------------------------------
const hookEnv = (over: Record<string, string>) => {
  const e: Record<string, string> = { ...(process.env as Record<string, string>), XDG_DATA_HOME: tmp, HOME: tmp, ...over }
  delete e.ASD_AGENT_SURFACE; delete e.ASD_HARD_BLOCK
  return { ...e, ...over }
}
const run = (stdin: string, over: Record<string, string>) => {
  const p = Bun.spawnSync(["bun", "--no-install", join(root, "shared", "claude", "hard-block.ts")], { stdin: new TextEncoder().encode(stdin), env: hookEnv(over), stdout: "pipe", stderr: "pipe" })
  return { out: p.stdout.toString().trim(), code: p.exitCode }
}
const call = (tool_name: string, tool_input: object, extra: object = {}) => JSON.stringify({ hook_event_name: "PreToolUse", session_id: "s1", cwd: tmp, tool_name, tool_input, ...extra })
const merge = call("Bash", { command: "gh pr merge 5 --squash" })
let r = run(merge, { ASD_AGENT_SURFACE: "dm" })
let o = r.out ? JSON.parse(r.out) : undefined
check("hook dm: merge denied with the PreToolUse JSON", r.code === 0 && o?.hookSpecificOutput?.hookEventName === "PreToolUse" && o.hookSpecificOutput.permissionDecision === "deny")
check("hook dm: the reason says what is refused and that the owner does it", /gh pr merge refused/.test(o?.hookSpecificOutput?.permissionDecisionReason) && /owner does this step themselves.*Merge button/.test(o.hookSpecificOutput.permissionDecisionReason))
check("hook chat: merge denied", run(merge, { ASD_AGENT_SURFACE: "chat" }).out.includes('"deny"'))
check("hook: marker off allows the same call", run(merge, {}).out === "")
check("hook: marker for another surface allows it", run(merge, { ASD_AGENT_SURFACE: "runner" }).out === "")
check("hook: ASD_HARD_BLOCK=1 denies without a surface", run(merge, { ASD_HARD_BLOCK: "1" }).out.includes('"deny"'))
check("hook: a subagent of the session is blocked too", run(call("Bash", { command: "gh pr merge 5" }, { agent_id: "a1", agent_type: "implementer" }), { ASD_AGENT_SURFACE: "dm" }).out.includes('"deny"'))
check("hook: Read of a secret denied", run(call("Read", { file_path: join(tmp, ".ssh", "id_ed25519") }), { ASD_AGENT_SURFACE: "chat" }).out.includes('"deny"'))
check("hook: Read of an ordinary file allowed", run(call("Read", { file_path: "/r/README.md" }), { ASD_AGENT_SURFACE: "chat" }).out === "")
check("hook: docker compose --env-file allowed", run(call("Bash", { command: "docker compose --env-file ../../.runtime.env up -d" }), { ASD_AGENT_SURFACE: "dm" }).out === "")
check("hook: force push denied", run(call("Bash", { command: "git push --force origin feat/x" }), { ASD_AGENT_SURFACE: "dm" }).out.includes("force push refused"))
r = run("not json", { ASD_AGENT_SURFACE: "dm" })
check("hook: unparseable input fails open (no output, exit 0)", r.out === "" && r.code === 0)
r = run("", { ASD_AGENT_SURFACE: "dm" })
check("hook: empty input fails open", r.out === "" && r.code === 0)
const logText = (() => { try { return require("node:fs").readFileSync(join(tmp, "agentic-software-development", "claude", "claude-hooks.log"), "utf8") } catch { return "" } })()
check("hook: a refusal and unparseable input are logged", /refused s1 hard block \(dm\) Bash/.test(logText) && /hard-block-unparseable/.test(logText))
check("hook: the marker off logs nothing for unparseable input", (() => { const before = logText.split("hard-block-unparseable").length; run("not json", {}); return before === (() => { try { return require("node:fs").readFileSync(join(tmp, "agentic-software-development", "claude", "claude-hooks.log"), "utf8") } catch { return "" } })().split("hard-block-unparseable").length })())

// ---- hooks.json and the dmagent argv/env --------------------------------------------------------------------------
const hooks = JSON.parse(await Bun.file(join(root, "hooks", "hooks.json")).text())
const pre = hooks.hooks.PreToolUse.find((e: any) => e.hooks.some((h: any) => h.command.includes("shared/claude/hard-block.ts")))
check("hooks.json: the hard block is a PreToolUse hook on file tools and Bash", !!pre && ["Read", "Edit", "Write", "NotebookEdit", "Grep", "Glob", "Bash"].every((t) => pre.matcher.split("|").includes(t)))
const st = JSON.parse(hardBlockSettings())
const entry = st.hooks.PreToolUse[0]
check("settings: a PreToolUse command hook running hard-block.ts", entry.hooks[0].type === "command" && entry.hooks[0].command.includes(join(root, "shared", "claude", "hard-block.ts")) && ["Bash", "Read", "Write", "Edit", "Grep", "Glob", "NotebookEdit"].every((t) => entry.matcher.split("|").includes(t)))
const argv = claudeArgs({ sessionId: "S", resume: false, model: "m", addDirs: [] })
check("argv: --settings carries the hook JSON", argv[argv.indexOf("--settings") + 1] === hardBlockSettings())

const seen: { argv: string[]; env: Record<string, string> }[] = []
const base = {
  configDir: () => "/cfg", addDirs: async () => [], log: () => {},
  spawn: async (a: string[], env: Record<string, string>) => { seen.push({ argv: a, env }); return { stdout: JSON.stringify({ result: "ok" }), stderr: "", code: 0 } },
}
await askAgent({ channel: "D1", ts: "1.1", text: "hi" }, { ...base, post: async () => "P", update: async () => {} })
check("askAgent: a Slack DM turn has ASD_AGENT_SURFACE=dm and the --settings hook", seen[0].env.ASD_AGENT_SURFACE === "dm" && seen[0].argv.includes("--settings"))
process.env.ASD_CHAT_DIR = join(tmp, "chat")
process.env.ASD_AGENT_SURFACE = "leaked"
const chat = await say("hello", undefined, base, () => {})
await chat.done
check("pagechat: a dashboard chat turn has ASD_AGENT_SURFACE=chat (overriding the parent's) and the --settings hook", seen[1]?.env.ASD_AGENT_SURFACE === "chat" && seen[1].argv.includes("--settings"))
delete process.env.ASD_AGENT_SURFACE
check("askAgent: the parent process env is not changed", process.env.ASD_AGENT_SURFACE === undefined)

console.log(fails.length ? `FAIL ${fails.length}/${n}: ` + fails.join(" | ") : `PASS (${n} cases)`)
process.exit(fails.length ? 1 : 0)
