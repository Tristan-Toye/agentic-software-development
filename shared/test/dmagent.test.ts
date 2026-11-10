// The owner's DM agent (shared/questions/dmagent.ts): session per thread, resume on a follow-up, argv, stream parsing,
// progress updates, the timeout and the cwd. (The owner gate is tested in questions-server.test.ts.)
import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const dir = mkdtempSync(join(tmpdir(), "dmagent-"))
process.env.ASD_DM_AGENT_FILE = join(dir, "dm.json")
const { Progress, askAgent, claudeArgs, clipForSlack, parseResult, pickCwd, pluginRoot, promptFile, readStore } = await import("../questions/dmagent.ts")

let failed = 0
const check = (name: string, ok: boolean) => { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) failed++ }

const a1 = claudeArgs({ sessionId: "S1", resume: false, model: "sonnet", addDirs: ["/r"] })
check("argv: a new session pins its id, the prompt file and auto mode", a1.includes("--session-id") && a1[a1.indexOf("--session-id") + 1] === "S1" && !a1.includes("--resume")
  && a1[a1.indexOf("--append-system-prompt-file") + 1] === promptFile() && a1[a1.indexOf("--permission-mode") + 1] === "auto" && a1.includes("/r"))
const a2 = claudeArgs({ sessionId: "S1", resume: true, model: "sonnet", addDirs: [] })
check("argv: a follow-up resumes the session", a2[a2.indexOf("--resume") + 1] === "S1" && !a2.includes("--session-id"))
check("argv: stream-json with verbose, no allowlist, no dontAsk, no bypass", a1[a1.indexOf("--output-format") + 1] === "stream-json" && a1.includes("--verbose")
  && !a1.includes("--allowedTools") && !a1.includes("dontAsk") && !a1.includes("--dangerously-skip-permissions") && !a1.includes("bypassPermissions"))
process.env.ASD_DM_AGENT_PERMISSION_MODE = "acceptEdits"
check("argv: ASD_DM_AGENT_PERMISSION_MODE overrides the mode", claudeArgs({ sessionId: "S", resume: false, model: "m", addDirs: [] }).includes("acceptEdits"))
delete process.env.ASD_DM_AGENT_PERMISSION_MODE
check("cwd: a repo named in the message is the cwd, the longest name wins", pickCwd("fix the bug in my-repo please", ["/x/other", "/x/my-repo"]) === "/x/my-repo"
  && pickCwd("in repo-ui and repo", ["/x/repo", "/x/repo-ui"]) === "/x/repo-ui")
check("cwd: no repo named (or only part of a name) keeps the plugin root", pickCwd("status?", ["/x/my-repo"]) === pluginRoot() && pickCwd("my-repository", ["/x/my-repo"]) === pluginRoot())
check("result: json result is the answer", JSON.stringify(parseResult('{"type":"result","result":"All good","is_error":false}', "", 0)) === JSON.stringify({ ok: true, text: "All good" }))
check("result: an error says why", parseResult('{"type":"result","subtype":"error_max_turns","is_error":true}', "", 1).ok === false && parseResult("", "boom", 1).text === "boom")
const ASSIST = (tool?: string) => JSON.stringify({ type: "assistant", message: { content: tool ? [{ type: "tool_use", name: tool, input: {} }] : [{ type: "text", text: "x" }] } })
const RESULT = (r: string) => JSON.stringify({ type: "result", subtype: "success", is_error: false, result: r })
check("result: the result event of a stream is the answer", parseResult([JSON.stringify({ type: "system" }), ASSIST("Bash"), RESULT("Done")].join("\n") + "\n", "", 0).text === "Done")
{
  const p = new Progress(0)
  p.feed("not json"); p.feed(JSON.stringify({ type: "system", subtype: "init" })); p.feed(ASSIST("Read")); p.feed(ASSIST("mcp__plugin_agentic-software-development_runs__runs_overview")); p.feed(ASSIST())
  check("stream: counts assistant turns and keeps the last tool", p.turns === 3 && p.lastTool === "runs:runs_overview")
  check("stream: the status line has elapsed time, turn and tool", /Working… 1 min 5 s, turn 3, last tool: `runs:runs_overview`/.test(p.status(65_000)))
  p.feed(RESULT("ok"))
  check("stream: the result event is kept", p.result?.result === "ok")
}
check("slack: long answers are clipped", clipForSlack("x".repeat(50_000)).length < 40_000)

const posts: string[] = [], updates: string[] = [], calls: string[][] = []
const deps = {
  post: async (_c: string, t: string, text: string) => { posts.push(`${t}:${text}`); return `P${posts.length}` },
  update: async (_c: string, ts: string, text: string) => { updates.push(`${ts}:${text}`) },
  configDir: () => "/cfg", addDirs: async () => [], log: () => {},
  spawn: async (argv: string[], env: Record<string, string>, stdin: string) => { calls.push([...argv, `ENV=${env.CLAUDE_CONFIG_DIR}`, `IN=${stdin}`]); return { stdout: JSON.stringify({ result: `answer ${calls.length}` }), stderr: "", code: 0 } },
}
await askAgent({ channel: "D1", ts: "10.1", text: "status?" }, deps)
const sid = readStore()["D1:10.1"]?.sessionId
check("turn: a top-level DM starts a session, stored by its thread", !!sid && calls[0].includes("--session-id") && calls[0].includes(sid!) && calls[0].includes("ENV=/cfg") && calls[0].includes("IN=status?"))
check("turn: the answer replaces the placeholder in the thread", posts[0].startsWith("10.1:") && updates[0] === "P1:answer 1")
await askAgent({ channel: "D1", ts: "10.5", threadTs: "10.1", text: "and W-1?" }, deps)
check("turn: a thread reply resumes the same session", calls[1].includes("--resume") && calls[1].includes(sid!) && readStore()["D1:10.1"].turns === 2)
await askAgent({ channel: "D1", ts: "11.1", text: "new question" }, deps)
check("turn: a new top-level DM is a new session", readStore()["D1:11.1"]?.sessionId !== sid && calls[2].includes("--session-id"))
// Two messages in one thread run one after the other.
let live = 0, maxLive = 0
const slow = { ...deps, spawn: async () => { live++; maxLive = Math.max(maxLive, live); await Bun.sleep(20); live--; return { stdout: '{"result":"ok"}', stderr: "", code: 0 } } }
await Promise.all([askAgent({ channel: "D1", ts: "12.1", text: "a" }, slow), askAgent({ channel: "D1", ts: "12.2", threadTs: "12.1", text: "b" }, slow)])
check("turn: one thread runs one turn at a time", maxLive === 1)

// Progress: the placeholder is updated at most every 20 s of the injected clock, then the answer replaces it.
{
  let t = 1_000_000
  const ups: string[] = [], cwds: string[] = [], argvs: string[][] = []
  const d = {
    ...deps, now: () => t, addDirs: async () => ["/r/my-repo"],
    update: async (_c: string, ts: string, text: string) => { ups.push(`${ts}:${text}`) },
    spawn: async (argv: string[], _e: Record<string, string>, _i: string, cwd: string, _to: number, onLine?: (l: string) => void) => {
      argvs.push(argv); cwds.push(cwd)
      onLine?.(ASSIST("Edit")); t += 5_000; onLine?.(ASSIST("Bash"))   // 5 s: no update yet
      t += 20_000; onLine?.(ASSIST("Bash(git)"))                       // 25 s: one update
      t += 10_000; onLine?.(ASSIST("Read"))                            // 10 s since: none
      t += 15_000; onLine?.(ASSIST("Grep"))                            // 25 s since: second update
      return { stdout: RESULT("all done") + "\n", stderr: "", code: 0 }
    },
  }
  await askAgent({ channel: "D2", ts: "20.1", text: "in my-repo, fix the typo" }, d)
  check("progress: updates come at most every 20 s with elapsed, turns and tool", ups.length === 3 && /Working… 25 s, turn 3, last tool: `Bash\(git\)`/.test(ups[0]) && /Working… 50 s, turn 5, last tool: `Grep`/.test(ups[1]))
  check("progress: the final answer replaces the placeholder", ups[2] === "P1:all done" || ups[2].endsWith(":all done"))
  check("turn: the session runs in the repo the message names and keeps it", cwds[0] === "/r/my-repo" && readStore()["D2:20.1"].cwd === "/r/my-repo")
  await askAgent({ channel: "D2", ts: "20.2", threadTs: "20.1", text: "thanks, no repo here" }, { ...d, addDirs: async () => [] })
  check("turn: a thread reply resumes from the same cwd", cwds[1] === "/r/my-repo" && argvs[1].includes("--resume"))
}
// Timeout: the turn says so and the session stays, so a reply resumes it.
{
  const ups: string[] = []
  const to: number[] = []
  const d = { ...deps, update: async (_c: string, _t: string, text: string) => { ups.push(text) },
    spawn: async (_a: string[], _e: Record<string, string>, _i: string, _c: string, ms: number, onLine?: (l: string) => void) => { to.push(ms); onLine?.(ASSIST("Bash")); return { stdout: "", stderr: "", code: 143, timedOut: true } } }
  await askAgent({ channel: "D3", ts: "30.1", text: "long job" }, d)
  check("timeout: says so, names the resume, keeps the session", /timed out after 60 min/.test(ups.at(-1)!) && /resume/.test(ups.at(-1)!) && !!readStore()["D3:30.1"])
  check("timeout: the default is 60 min", to[0] === 3_600_000)
  process.env.ASD_DM_AGENT_TIMEOUT_MS = "1234"
  await askAgent({ channel: "D3", ts: "30.2", threadTs: "30.1", text: "continue" }, d)
  delete process.env.ASD_DM_AGENT_TIMEOUT_MS
  check("timeout: ASD_DM_AGENT_TIMEOUT_MS overrides; the resumed turn counts", to[1] === 1234 && readStore()["D3:30.1"].turns === 2)
}
// Different threads run concurrently.
{
  let live = 0, maxLive = 0
  const slow = { ...deps, spawn: async () => { live++; maxLive = Math.max(maxLive, live); await Bun.sleep(20); live--; return { stdout: '{"result":"ok"}', stderr: "", code: 0 } } }
  await Promise.all([askAgent({ channel: "D4", ts: "40.1", text: "a" }, slow), askAgent({ channel: "D4", ts: "41.1", text: "b" }, slow)])
  check("turn: different threads run concurrently", maxLive === 2)
}

rmSync(dir, { recursive: true, force: true })
if (failed) { console.log(`${failed} failed`); process.exit(1) }
console.log("all passed")
