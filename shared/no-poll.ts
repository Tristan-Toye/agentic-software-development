import { simpleCommands } from "./read-guard"

// The no-polling rule of the building sub-agents (implementer, stub-materialiser): a build, lint or own-package
// test run is one foreground call, and a long run is waited on with the blocking `--wait` of run_tests.py /
// run_gates.py, never by polling. Polling turns a long wait into many turns, each paying a full context read.
// Rules only; the Claude Code hook is shared/claude/no-poll.ts, the opencode one is in context-guard.ts.
// Which agents it applies to: `no_poll_agents` in shared/context-guard.yaml.

export const PLUGIN_PREFIX = "agentic-software-development:"

// `agentic-software-development:implementer` and `implementer` are the same agent.
export const shortAgent = (agent: string | undefined) => (agent ?? "").replace(PLUGIN_PREFIX, "")

export const noPollOn = (env: Record<string, string | undefined> = process.env) => env.ASD_NO_POLL !== "0"

const INSTEAD =
  "Run it in the foreground in one call (compile, lint, your own package's tests); for a long run use `run_tests.py ... --detach` then the blocking `run_tests.py --wait <log>` (or run_gates.py), and call `--wait` again when it exits 6."

// Words that make the polling intent of a loop condition: a check of a process, a file or a log.
const POLL_CHECK = /\b(grep|egrep|pgrep|pidof|ps|cat|tail|head|stat|test|ls|curl|wc|jq|kill)\b|\[/

// Quoted text out of the way, so `git commit -m "sleep in a loop"` is not a loop.
const unquoted = (s: string) => s.replace(/'[^']*'|"(?:[^"\\]|\\.)*"/g, "''")

// Seconds of a `sleep` argument: 5, 0.5, 2m, 1h, 1d; a sum for several arguments; undefined when not a number.
export function sleepSeconds(args: string[]): number | undefined {
  let total = 0
  let any = false
  for (const a of args) {
    const m = /^(\d+(?:\.\d+)?)([smhd]?)$/.exec(a)
    if (!m) continue
    any = true
    total += Number(m[1]) * ({ "": 1, s: 1, m: 60, h: 3600, d: 86400 } as Record<string, number>)[m[2]]
  }
  return any ? total : undefined
}

// The real command of a simple command: leading VAR=x assignments and the wrappers `timeout [opts] N`, `time`,
// `nice [-n N]`, `command`, `nohup`, `env`, `stdbuf` stepped over. `timeout 600 cargo test` runs `cargo`.
export function realCommand(words: string[]): string[] {
  let w = words
  for (let guard = 0; guard < 6 && w.length; guard++) {
    const head = w[0]
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(head)) w = w.slice(1)
    else if (head === "timeout" || head === "gtimeout") {
      let i = 1
      while (i < w.length && w[i].startsWith("-")) i += /^(-k|--kill-after|-s|--signal)$/.test(w[i]) ? 2 : 1
      w = w.slice(i + 1) // the duration
    } else if (head === "nice") {
      let i = 1
      if (w[i] === "-n") i += 2
      else if (/^-\d+$/.test(w[i] ?? "")) i++
      w = w.slice(i)
    } else if (["time", "command", "nohup", "env", "stdbuf", "exec"].includes(head)) {
      let i = 1
      while (i < w.length && (w[i].startsWith("-") || /^[A-Za-z_][A-Za-z0-9_]*=/.test(w[i]))) i++
      w = w.slice(i)
    } else break
  }
  return w
}

const isFollow = (arg: string) => arg === "--follow" || arg.startsWith("--follow=") || /^-[A-Za-z]*[fF][A-Za-z0-9]*$/.test(arg)

// One Bash call of a guarded agent: a reason to refuse it, or undefined.
export function noPoll(toolName: string, toolInput: Record<string, any>, sleepMax = 5): string | undefined {
  if (toolName !== "Bash") return undefined
  if (toolInput.run_in_background === true)
    return `run_in_background polls: you would check the run again and again. ${INSTEAD}`
  const command = String(toolInput.command ?? "")
  if (!command.trim()) return undefined
  const bare = unquoted(command)

  // A loop that waits: `while|until|for ... sleep`, or a `while|until` whose condition checks a process, file or log.
  const loopStart = /(?:^|[\s;&|(){}])(while|until|for)\s/.exec(bare)
  if (loopStart) {
    if (/\bsleep\b/.test(bare)) return `a sleep in a ${loopStart[1]} loop is polling. ${INSTEAD}`
    if (loopStart[1] !== "for") {
      const cond = bare.slice(loopStart.index + loopStart[0].length).split(/;\s*do\b|\bdo\b/)[0]
      if (!/\bread\b/.test(cond) && (loopStart[1] === "until" || POLL_CHECK.test(cond)))
        return `a ${loopStart[1]} loop that re-checks a process, file or log is polling. ${INSTEAD}`
    }
  }

  for (const raw of simpleCommands(command)) {
    const words = realCommand(raw)
    const name = (words[0] ?? "").replace(/^.*\//, "")
    if (name === "sleep") {
      const s = sleepSeconds(words.slice(1))
      if (s !== undefined && s > sleepMax) return `sleep ${words.slice(1).join(" ")} waits longer than ${sleepMax}s: that is polling. ${INSTEAD}`
    } else if (name === "watch") return `watch re-runs a command on a timer: that is polling. ${INSTEAD}`
    else if (name === "tail" && words.slice(1).some(isFollow)) return `tail -f never returns: that is polling. ${INSTEAD}`
  }
  // A trailing `&` backgrounds the command, the same as run_in_background.
  if (/(?:^|[^&>|])&\s*$/.test(bare.trim())) return `a trailing & backgrounds the run, like run_in_background. ${INSTEAD}`
  return undefined
}
