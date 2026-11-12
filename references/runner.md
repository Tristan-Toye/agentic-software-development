# The runner

`scripts/run_dossier.py` runs one dossier through `/work-on` without a
terminal session: one headless Claude Code process per stage, so every stage
starts from a clean context, and owner questions asked and answered through
`references/owner-questions.md`. One runner owns one dossier; it never picks
up another one while it waits.

    run_dossier.py [ID] [--root R] [--foreground] [--command work-on|stub]
                   [--model M] [--permission-mode auto] [--max-stages N]
    run_dossier.py status [ID] [--root R]
    run_dossier.py stop ID [--root R]
    run_dossier.py --selftest
    setup_claude_account.sh NAME [DIR]    # add a second login to switch to

The repository defaults to the one the command runs in (its git toplevel).
It starts in the background by default (`--detach`), with its output in
`<key>.runner.log`, which is also how the dashboard's Start and Resume
buttons start it; `--foreground` keeps it in the terminal. Running it again after `stopped` or `failed` resumes from
the recorded checkpoints.

## How a stage runs

- **The process:** each stage is one
  `claude -p --input-format stream-json --output-format stream-json --verbose`
  process.
  - It runs in R, with `--permission-mode` (default `auto`) and the
    environment variable `ASD_RUN_KEY=<repo>.<ID>`.
  - It gets `--append-system-prompt` with the runner-mode rules below.
- **The first message** is the command: `/agentic-software-development:work-on <ID>`. After it comes the
  hand-off of every earlier stage: the task, then each checkpoint summary in
  order, ending with the last `NEXT:` line.
- **Watching the output:** the runner reads the stream-json output.
  - For every `phase_checkpoint` tool call it records `{name, summary}` in
    `<key>.checkpoints.json` and appends a `stage` update.
  - The `result` event ends a turn. The runner then reads the turn's last
    line:
    - `RUNNER: STAGE-DONE <name>` — stdin closes, the process exits, and the
      next stage starts as a new process.
    - `RUNNER: WAITING <question id>[,<id>...]` — the runner runs
      `ask_owner.py wait <id>` itself, so no tokens are spent while it waits.
      It sends the owner's message into the same live process as a new user
      message: `OWNER ANSWER <id>: …` or `OWNER FOLLOWUP <id>: …`. On a
      follow-up the agent answers with `ask_owner.py reply` and ends the turn
      with `RUNNER: WAITING <id>` again.
    - `RUNNER: ENV <vm|stack|disk> <detail>` — a broken environment (the
      Lima VM down, a compose service unhealthy, the disk low), reported
      instead of asked about. The session stays open; the runner answers with
      `RUNNER ENV <kind>: repaired ...` (carry on) or `RUNNER ENV <kind>: not
      repaired ...` (ask the owner as usual). See "Standing repair" below.
    - `RUNNER: DONE` — the dossier is finished: the runner records `done`
      and exits 0.
    - `RUNNER: REPLAN <why>` — the owner ruled the dossier back to `/plan`
      (or the build showed it must be re-planned and the owner agreed). Not
      a failure and not a question: the runner sets the dossier's `status:`
      to `planned`, records the run `done` with the reason, and starts a
      `/plan` run of the dossier, which queues for a slot like any run and
      starts with empty checkpoints. A `FAILED` whose reason says "sent back
      to /plan" is read as `REPLAN`. `ASD_RUNNER_REPLAN=0` keeps the status
      change and skips the automatic `/plan`.
    - `RUNNER: FAILED <why>`, or a turn with no `RUNNER:` line — the runner
      records `failed` with the reason, notifies the owner (a question
      through `ask_owner.py`: retry the stage, or stop), and acts on the
      answer.
- **A turn without a `RUNNER:` line** is not a failure at once. A turn
  that ends while a background command or workflow runs is re-invoked by
  Claude Code when that work finishes, so the runner keeps the session open.
  A `RUNNER: WAITING` line that names no question id (`RUNNER: WAITING
  gates,background`) is read the same way: a wait on background work, not
  on the owner. After `ASD_RUNNER_IDLE` seconds of silence (default 1200) it sends a nudge
  into the same session, at most twice, and only then records `failed`.
- **Out of tokens:** a turn that fails on a usage or rate limit (or a
  process that exits with one on stderr) does not fail the run. The runner
  records `paused` with `resumes_at`, posts `paused: usage limit reached;
  resumes at <time>` (also to the Slack run thread), sleeps until the reset
  time the CLI printed (an epoch after `|` or an ISO time; else it retries
  every 30 minutes, `ASD_RUNNER_LIMIT_WAIT`), then runs the same stage again
  from its checkpoints. `stop` ends a paused run.
- **The model:** the orchestrator runs on `claude-opus-5-5`, passed as an exact
  id (`ASD_RUNNER_MODEL` overrides it), never on an alias: `opus` resolves per
  login, and on one it resolved to `claude-fable-5-1` for a day. `--model` with
  Fable is refused before anything starts, and the model the session reports
  (its init line, each turn) is checked: any other model stops the stage and
  the owner is asked. The spawn guard refuses any sub-agent or workflow agent
  named for Fable; sub-agents run on their agent files' Sonnet or Haiku.
- **Gates settled once:** every stage is a new session running the command
  again, so the rules tell it the gates are settled: the worktree gate is
  yes, and time logging is **off** (no Tempo, no question, no read of
  `time-logging.md`) unless `ASD_RUNNER_TIME_LOGGING=on`, then it runs in the
  first stage only. The rules also ask for the plugin's references by section
  (the nav `ref_section` tool: a short name such as `phase-4` or
  `formats/dossier` and a heading): 15 runs read them in full ~1,300 times.
  A runner stage counts as a pipeline orchestrator for the plugin's hooks
  (`ASD_RUN_KEY`), so the read guard and the checkpoint reminders apply in it;
  before, they only recognised a command typed in a terminal.
- **Accounts bound to an email:** an entry in `accounts.json` may carry
  `email`. An account whose config dir is logged in as another email, or as
  the same email as an account listed before it, is skipped while a good one
  exists; the dashboard marks it "logged in as the wrong account · not used".
  Keep runner logins in config dirs of their own (`setup_claude_account.sh`),
  never in the `~/.claude` you log in to interactively.
- **Run slots:** at most `ASD_RUNNER_MAX_LIVE` runs (default 4; 0 = no limit)
  work at once (unless the cap is paced, see "Slots that follow the budget"). A run takes a slot before each stage and after an owner's
  answer; only `running` runs hold one. Without a free slot it records
  `queued` (`queued_since`) and waits, longest-queued first. A run the owner
  just answered queues first (`queued_first`): it already waited on the owner,
  so it goes ahead of every run not answered and takes the next slot that
  frees. A run waiting on the owner holds no slot. The dashboard
  shows "queued · waiting for a slot". So a usage-limit reset, or many Start
  presses, no longer sets ten sessions going in the same minute.
- **Replies on open questions, mid-run:** an agent may ask and keep working
  on what is not blocked. Each question records the run that asked it
  (`run`, the run key). While the run works, the runner checks that run's
  open questions every 20 s (`ASD_RUNNER_QFWD_EVERY`) and sends each new owner
  line on once, as `OWNER FOLLOWUP <id>: …` or `OWNER ANSWER <id> …`, logging
  `sent on n owner replies on open questions`. A question the run is waiting
  on is left to the wait. Before, a follow-up typed while the agent worked was
  passed on only when the agent next waited on that question.
- **Dropped completion notices:** a background command or workflow that
  finishes while no turn runs is announced by a `<task-notification>`, and
  headless sessions have been seen to `remove` it from the queue instead of
  delivering it (W-131 waited 80 minutes for news it was sent). While a session
  is idle the runner reads its transcript every 30 s (`ASD_RUNNER_FWD_EVERY`)
  and sends each dropped notice on once, as a message, logging `sent on n
  dropped background notice(s)`. The idle nudge counts only the session's own
  turns as activity, so queue events no longer keep it from firing.
- **Network errors:** a turn (or a process exit) that fails on a transient
  network or API-server error (`ENOTFOUND`, a reset, refused or timed-out
  connection, "Can't reach the API server", a 5xx) does not fail the run
  either. The runner records `paused` with `resumes_at`, posts `paused:
  network error (…); retry n of stage N at <time>`, waits (30 s, 1, 2, 5, 10,
  then 15 min; `ASD_RUNNER_NET_WAIT` overrides), and runs the same stage again
  from its checkpoints, for as long as the outage lasts. Setting
  `ASD_RUNNER_NET_RETRIES` makes it a failure the owner rules on after that many
  in a row ("network errors on 8 retries in a row: …"); the default, 0, never
  does. A stage that ends well resets the count.
- **A claude process killed from outside:** when the stage's claude process
  exits on a signal the runner did not send (exit 143, 137 or 129, or a
  negative return code: a memory or disk squeeze, a host app crash, a
  logout) and the owner did not ask to stop, the runner posts `the claude
  process was killed from outside the runner (exit N; <free> GiB free, claude
  <version>, host woke <time>); stage N again in S s`, the clues to the sender,
  waits (1 then 5 min; `ASD_RUNNER_KILL_WAIT` overrides) and runs the stage
  again from its checkpoints through the slot queue, so the disk floor still
  applies. It asks no question. Only after `ASD_RUNNER_KILL_RETRIES` (2) such
  kills in a row does it become a failure the owner rules on. A stage that
  ends well resets the count.
- **Deployed fixes reach live runners:** a runner keeps the code it started
  with in memory. At every stage boundary it compares the script a fresh start
  would use (the same path, or under a plugin cache the highest installed
  version) with the one it loaded. When that has changed it posts `runner code
  changed (…); restarting the runner on it, same run` and execs it with the
  same arguments. The pid stays, and the checkpoints carry the work on.
  `ASD_RUNNER_REEXEC=0` turns it off.
- **A run killed rather than stopped:** a SIGTERM nobody asked for (a logout, a
  shutdown) leaves its state as it is, and so does a SIGKILL or a crash; the Stop
  button writes `<key>.stop-requested` first, so only that reads as stopped. The
  dashboard runs `run_dossier.py revive` at startup and every 2 minutes: a run
  whose record says working and whose process is gone goes on from its
  checkpoints. A run dead for over `ASD_RUNNER_REVIVE_HOURS` (default 48) stays
  down, and one that dies 3 times within an hour is left stopped.
- **Several accounts:** `scripts/setup_claude_account.sh NAME` adds a login
  in its own config dir (`CLAUDE_CONFIG_DIR`, sharing settings, plugins and
  hooks with `~/.claude` by symlink) and registers it in
  `~/.agentic/accounts.json`; log in once with `CLAUDE_CONFIG_DIR=<dir> claude`.
  The runner runs each stage on one account (recorded as `account` in
  `<key>.runner.json`). On a usage limit it records the account's reset time
  in `~/.agentic/accounts-state.json`, switches at once to an account that is
  not limited (`switched to account …`), and when every account is limited
  waits for the one that resets first, then continues on it. Reset times are
  read from the CLI's epoch, an ISO time, or text like "resets Nov 6 at 12pm
  (Europe/Brussels)". The dashboard reads session history from every
  registered account.
- **Blocked calls:** the plugin's PermissionDenied hook
  (`shared/claude/permission-denied.ts`) records every call auto mode refuses
  as a `blocked` update (`tool`, `target` — the exact command or path,
  `reason`, `tool_use_id`), and logs it again, run or no run, to
  `~/.agentic/blocks.jsonl` (`ASD_BLOCK_LOG`), where the dashboard also logs
  each ruling (`block-ruling`, with its scope and rule).
  `scripts/block_report.py [--since D] [--runs]` groups that log by command
  shape, most frequent first, with how each was ruled: the list to read
  before widening an allow rule. The runner-mode rules tell the agent never to work
  around a block and to ask the owner, quoting the exact command. The
  dashboard's Blocked card rules on it (owner-questions.md). How a background
  run is allowed what auto mode blocks, since the permissions dialog needs a terminal:
  an approval in the owner's answer that names the exact action and what makes
  it risky (the classifier reads the owner's messages, one action unless said
  to be standing); a narrow `permissions.allow` rule in the repo's
  `.claude/settings.local.json`, applied before the classifier from the next
  stage; or `autoMode.environment` / `autoMode.allow` in `~/.claude/settings.json`
  for trusted infrastructure. A `deny` rule always wins.
- **Usage per account:** every `rate_limit_event` a stage emits is recorded
  in `~/.agentic/usage/<account>.json` (`ASD_USAGE_DIR`): the 5-hour and
  weekly windows (`used_percentage`, `resets_at`, `status`, from
  `unifiedWindows`). `run_dossier.py usage [--account NAME]` reads them for an
  account nothing runs on, with one tiny haiku call each; the dashboard's
  Accounts panel shows them. Interactive sessions can keep the same file
  current from the status line, whose JSON input carries
  `rate_limits.five_hour` and `rate_limits.seven_day` (`used_percentage`,
  `resets_at`): a status-line command that maps its `CLAUDE_CONFIG_DIR` to the
  account name in `~/.agentic/accounts.json` and writes that shape (the
  owner's `~/.claude/hooks/claude-usage-sampler.py` does, with `via:
  "statusline"`).
- **Background work across a stage end (#239):** ending a stage ends its
  process, and every background command, workflow or agent it started dies
  with it, without a notification. So on `RUNNER: STAGE-DONE` or `RUNNER:
  DONE` the runner reads the session transcript for background launches with
  no `<task-notification>` yet. While any is in flight it holds the end: it
  logs `stage end held (1/1): …`, tells the agent to wait for the
  notification and end the stage again (and never to ask the owner), and
  keeps the session open. A task the agent stopped (a `TaskStop` or kill-shell
  call, whatever its result says, or a result saying no such task) counts as
  finished, and so does a command whose output file no process holds open
  (`lsof`). After one hold it ends the stage itself and logs what it cut off:
  a stale background task is never a question for the owner
  (`ASD_ASK_LINT` also refuses "end the stage anyway"). The rules tell
  the agent the same, and a checkpoint that still names work in flight says
  how the next stage tells it finished or died.
- **A stage with no checkpoint:** if a stage ends with `RUNNER: STAGE-DONE`
  but no `phase_checkpoint` call was seen (the tool was missing or skipped),
  the runner writes the `stage` line itself, so the dashboard still draws the
  phase divider. It also hands the next stage a note to resume from the
  dossier: `/work-on` resumes from its status and `## Build log` in any case.
- **The deferred report:** after a `/work-on` run ends `RUNNER: DONE`, one
  more short process runs `/deferred <ID>` and stores its report at
  `<key>.deferred.md`. The runner logs `deferred report: <n> issue(s)`, which
  the Slack run thread shows when it is not zero. `ASD_RUNNER_DEFERRED=0`
  skips it.
- **`--command fix-deferred [--items D-1,D-3]`:** runs `/fix-deferred <ID>
  [items]`, which fixes the ledger's direct items (all open ones, or only the
  listed ones) in one stacked pull request without asking, and asks the owner
  only about an item it would rather not fix there: one that deserves its own
  dossier, no longer applies, or is not worth it (`commands/fix-deferred.md`).
  The dashboard's "Fix all deferred" and "Fix selected" buttons start it.
- **`--command after-merge --items 429,430`:** runs `/after-merge <ID> <PRs>`
  (`commands/after-merge.md`): the cleanup after the owner merged the dossier's
  pull request (or its stacked deferred-fix one) into the stub. It settles the
  record, cleans local branches and worktrees, retargets a stacked pull request,
  and asks before refreshing one that now conflicts; it never merges. The
  dashboard's merge detector starts it, on sonnet.
- **`--command resolve-merge --items 427,429`:** runs `/resolve-merge <ID>
  <PRs>` (`commands/resolve-merge.md`): the merge queue found the dossier's
  PRs conflicting with their base, and the dossier's agent merges the base in,
  resolves keeping both sides, checks no hunk was lost, proves it with the
  affected tests and gates, and pushes. It never merges into the stub.
- **The stub breakpoint, `RUNNER: WAITING stub`:** a build whose Phase 2
  finds the stub being integrated (`stub_target.py` exit 3) ends its turn
  with it. The runner records `waiting`, polls `stub_target.py` every
  `ASD_STUB_POLL` seconds (default 300) without spending tokens, and once the
  integrated stub has merged and the new stub is cut, takes a run slot and
  sends `STUB READY: <branch>` into the same session.
- **`--command stub --items stub/<date>`:** runs `/stub <branch>` under the
  key `<repo>.stub-<date>` (the branch with `/` as `-`), so the stub page and
  the hooks' stub records share one timeline. The dashboard's Integrate starts
  it after the merge queue has merged the stub's finished dossiers.
- **`--command plan`:** runs `/plan <ID>` for a planned dossier, under its own
  key; the dashboard's Plan button starts it.
- **`--command plan --items D-n`:** runs `/plan <ID> D-n` for one deferred
  item routed to its own dossier, under the key `<repo>.<ID>.plan-D-n`, so
  several items plan at once beside any run of the source. `/fix-deferred`
  Step 7 starts one per routed item; the dashboard's Plan it starts one.
- **The product summary:** after the deferred report, one more short
  process runs `/product-summary <ID>` (`commands/product-summary.md`) on
  `ASD_RUNNER_SUMMARY_MODEL` (default `sonnet`) and stores the page at
  `<key>.summary.md`: what the dossier adds or changes, product first, then
  the technical shape, in about 250 words. The dashboard shows it above the
  dossier. `ASD_RUNNER_SUMMARY=0` skips it; `run_dossier.py summary ID`
  writes it for a build that is already done (the dashboard's "Write product
  summary" button), leaving the run's record as it is.
- **The last reply:** on `RUNNER: DONE` the runner writes the turn's text,
  without its `RUNNER:` line, as a `result` line (`{ts, kind: "result", text,
  session, command}`) and records `result_session` in `<key>.runner.json`.
- **`run_dossier.py chat ID --text T`:** the owner's message to a finished
  run. It writes a `chat` line (`from: owner`) at once, then a background
  process resumes the session (`<key>.chat.json` `session`, else
  `result_session`, else the last `stage N end` line's) with `claude -p
  --resume`, on the run's account, and writes the reply as a `chat` line
  (`from: agent`, or `from: runner` with why there is none). Refused while
  the last message is still being answered. A new run's reply starts a new
  chat.
- **A message to a live run** (the same `chat` command, the dossier page's
  "Message the running agent" box): appended to `<key>.inbox.jsonl` and to the
  updates as an `owner-message` line. The runner sends it into the live
  session as `OWNER MESSAGE: …`, mid-turn, between turns, or by waking a wait
  on a question (which stays open; the agent ends that turn waiting again). A
  paused run gets it when it resumes. `inbox_seen` in `<key>.runner.json`
  counts what was delivered, so a resumed run never sends one twice. The
  message outranks an earlier answer: the agent acts on it and confirms in a
  note.
- **Limits:** `--max-stages` caps the number of stages a run starts
  (default 20). A crash of the process counts as `FAILED`.

## Slots that follow the budget

Unless `ASD_RUNNER_ADAPTIVE=0` or `ASD_RUNNER_MAX_LIVE` is set (either keeps the
fixed cap above, exactly), the number of runs that may work at once is paced by
what the accounts have left.

- **Burn rate.** `r5` is the percentage points of an account's `five_hour`
  window that one running run uses per hour; `r7` is the same for `seven_day`.
  Each usage reading (`rate_limit_event`) is compared with the account's
  previous one: delta used% divided by (runs in state `running` on that account
  x delta hours). An interval that crosses a reset, runs over 2 h, has no run
  working, or shows the percentage falling is skipped. An EWMA (alpha 0.2) per
  window, not per account, lives in `<usage dir>/burn.json`
  (`{"rates": {...}, "last": {...}}`). The prior, until something is learned:
  `five_hour` 6.0 and `seven_day` 0.5 points per run-hour. They come from this
  machine's history: the runs' `.jsonl` `state` events give how many runs were
  `running` over time (41 runs, 435 run-hours), `~/.claude/usage-log.jsonl`
  gives the percentages (613 pairs in one window, at most 2 h apart: +1377
  points over 208 run-hours = 6.6 for the 5-hour window, 0.58 for the weekly
  one; per-window highs agree, as lower bounds because most hit 100).
- **Per usable account** (configured in `accounts.json`, not limit-marked,
  weekly window under 98%): with `h5`/`h7` the hours to the resets and margins
  `m5 = 10`, `m7 = 5`,
  `runs5 = (100 - m5 - used5) / (r5 x max(h5, 0.25))`,
  `runs7 = (100 - m7 - used7) / (r7 x max(h7, 1))`,
  `runs_a = max(0, min(runs5, runs7))`. A window that reset since the reading
  counts as empty.
- **Cap** = `clamp(floor(sum runs_a), ASD_RUNNER_MIN_LIVE (3), ASD_RUNNER_CAP_MAX (6))`.
  With every account limit-marked the existing pause logic applies and the cap
  is the minimum.
- **Stale readings.** A reading older than 30 min, with no running run on the
  account refreshing it, is refreshed by whichever runner is deciding, through
  the tiny usage call (`run_dossier.py usage`), at most once per 15 min per
  account (`<usage dir>/<account>.refresh`, with a lock file so several runners
  do not all refresh). An account with no reading counts `4 / number of
  accounts`; with no reading anywhere the cap is 4.
- **Never preempts.** A lower cap only stops admissions; a run that works
  keeps working. The cap is cached for ~60 s (`ASD_PACE_TTL`) in
  `<runs dir>/slots-cap.json` (`{cap, reason, ts, accounts, rates, ...}`), so
  all runners agree. The dashboard's "Running now" header shows it ("4 working
  · 5 slots", the reason on hover), and the `queued` event carries it: `queued:
  3 of 3 runs are working (3 slots: main 5h 85% resets in 2.0h, ...)`.

Knobs: `ASD_RUNNER_ADAPTIVE` (`0` = fixed), `ASD_RUNNER_MAX_LIVE` (fixed cap),
`ASD_RUNNER_MIN_LIVE` (3), `ASD_RUNNER_CAP_MAX` (6), `ASD_PACE_TTL` (60 s),
`ASD_PACE_REFRESH_S` (900 s), and for tests `ASD_RUNNER_NOW` (epoch seconds).

## Keep-alive pings

The prompt cache of a runner session lives 1 h. A run that waits on the owner,
or in the queue for a slot, loses it if the wait is longer, and the next turn
then pays for the whole context again. So, for a run in state `waiting` or
`queued` whose session's last API call is `ASD_KEEPALIVE_AFTER` seconds old
(default 3000), the runner sends a ping:

- the message is `RUNNER KEEPALIVE: reply with exactly OK and do nothing else.`
  If the claude process is alive, it is written to its stream-json stdin and
  its result is consumed there, not taken for stage output; the state, the
  question and the stage are not touched. Otherwise one
  `claude -p --resume <session>` turn runs with the same argv builder as the
  stage (flags, model, appended rules, permission mode), the run's account
  (`CLAUDE_CONFIG_DIR`) and cwd, so the cached prefix is byte-identical.
- It is sent from the thread that waits for the owner's answer (and from the
  slot queue loop), so a ping and an answer never overlap on the session: an
  answer arriving during a ping is delivered right after it.
- It takes no run slot.
- The event `keepalive ping: cache read N, written M tokens` is logged from the
  result's usage. A ping that writes more than it reads found the cache already
  cold: pinging that session stops (`keepalive stopped: ...`).
- No ping when the run is paused for usage (its account is limit-marked), when
  the account's five-hour window is at 80% or more, after the wait has lasted
  `ASD_KEEPALIVE_MAX_H` hours (default 12), or with `ASD_KEEPALIVE=0`.

Knobs: `ASD_KEEPALIVE` (`0` = off), `ASD_KEEPALIVE_AFTER` (3000 s),
`ASD_KEEPALIVE_MAX_H` (12), `ASD_KEEPALIVE_POLL` (check interval, 5 s) and
`ASD_KEEPALIVE_TIMEOUT` (180 s). The agent's side: see `RUNNER KEEPALIVE` in the
runner-mode rules below.

## Keeping the context small

Over 14 days of runner sessions on config-harness the CLI auto-compacted 83 times in runner sessions (213 in all), each at
about 168k (the 200k window minus 32k). The call after a compaction is about 80k, 30k of which is the harness
re-injecting its fixed prefix, each compaction pauses about 56 s, and rewriting the cache cost about $126. Three
settings keep that down. All three are built in `claude_cmd()` or the stage loop, so only runner-launched sessions see them.

| Variable | Default | Effect |
|---|---|---|
| `ASD_RUNNER_LEAN` | on | `0` gives the argv of 1.52.35 and before. On adds `--settings` and `--disallowedTools` (below) and the four opt-outs next. |
| `ASD_RUNNER_LEAN_CLAUDEMD` | on | `0` loads the target repo's own `CLAUDE.md`. On: `claudeMdExcludes` in `--settings` skips `<root>/CLAUDE.md` and `<root>-*/CLAUDE.md` (its worktrees) in the session and in every sub-agent, and the runner rules carry a pointer instead (below). |
| `ASD_RUNNER_LEAN_GIT` | on | `0` keeps Claude Code's built-in git instructions. On: `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1` in the child's environment (also drops the git status snapshot). |
| `ASD_RUNNER_LEAN_MEMORY` | on | `0` keeps auto-memory. On: `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. |
| `ASD_RUNNER_LEAN_CONTEXT7` | on | `0` keeps the context7 plugin. On: `context7@claude-plugins-official` is false in `enabledPlugins`. |
| `ASD_RUNNER_SPLIT_AT` | 135000 | context at a `phase_checkpoint` above which the stage ends and the next one starts fresh. `0` = off. |
| `ASD_RUNNER_COMPACT_WINDOW` | 250000 | `--autocompact N`: the auto-compact window. `0` = pass none. |

**A smaller fixed prefix.** Measured with one real haiku call (it resolves to claude-sonnet-5-5) per argv, built by
`claude_cmd()`, in config-harness on the main account: the first call's input is 31.9k tokens (cache write plus read)
with `ASD_RUNNER_LEAN=0` and 26.3k with it on, 5.6k (17%) fewer, and 35 tools become 21. What goes:

- tools the commands, skills, agents, references and hooks never name: CronCreate, CronDelete, CronList, DesignSync,
  ListAgents, PublishPlugin, PushNotification, RemoteTrigger, ReportFindings, ScheduleWakeup, ShareOnboardingGuide,
  NotebookEdit, EnterWorktree, ExitWorktree (`--disallowedTools`, comma separated). About 3.5k of the 5.6k.
- Claude Code's bundled skills (deep-research, design, verify, debug, code-review, simplify, loop, ...) listed by name
  only: `skillOverrides` `name-only` in `--settings`. About 2k. Not `off` and not `disableBundledSkills`: with fewer
  skills listed the budget widens and the plugin's own skill descriptions print in full, which cost 4k more, measured.
- the context-mode plugin (`enabledPlugins` false in `--settings`; the owner already turned it off for config-harness,
  so it measures 0 there and counts where it is still on).

What stays: the nav and runs MCP servers, the plugin's agents, skills, hooks and commands, Workflow, Monitor,
SendMessage, TaskStop, ToolSearch, LSP, WebFetch, WebSearch, Skill, Task, and the context7 and LSP plugins.
`--strict-mcp-config` is not used: it drops the plugin's nav and runs servers too (measured, `mcp_servers` 3 to 0).
`--settings` merges over the project's and the user's settings; the keep-alive resume ping is `claude_cmd()` plus
`--resume`, so it carries the identical flags and the cache prefix still matches. The remaining 26k is the CLI's own
system prompt and tool schemas, the account's CLAUDE.md files, the plugin's skill listing and the appended runner rules.

**Where the rest of the prefix goes** (1.52.37; one haiku call per row, config-harness, main account, lean flags on,
Claude Code 2.1.322; the first-call total is 25.25k without the runner rules, 27.28k with them):

| Component | Tokens | Cut per session? |
|---|---|---|
| the plugin's agent, command and skill descriptions | 3.1k | shortened in 1.52.37 to 1.9k (-1.24k); `skillOverrides` does not apply to plugin skills (docs, and measured 0) |
| the plugin's nav tool schemas and the rest of the plugin | 1.0k | no: the pipeline calls them |
| config-harness `CLAUDE.md` (`AGENTS.md` is not loaded) | 5.2k | cut in 1.52.42: `claudeMdExcludes` (-5.17k measured; sub-agents too, so each spawn saves it as well); the rules carry a pointer |
| `~/.claude/CLAUDE.md` + `RTK.md` | 0.7k | `claudeMdExcludes`; its subagent-model rule matters to runner sessions: keep |
| auto-memory index (`MEMORY.md`) | 0.84k | cut in 1.52.42: `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` (-0.84k measured) |
| context7 plugin's MCP block | 0.2k | cut in 1.52.42: `enabledPlugins` false (-0.21k measured) |
| built-in git instructions | 0.9k | cut in 1.52.42: `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1` (-0.89k measured) |
| org-synced and builtin plugins (ask-product-catalog, cc-plugin-*, design skills) | 1.1k | no: `enabledPlugins` and `skillOverrides` have no effect on them; only `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` drops them (it also stops telemetry), not used |
| the appended runner rules (`runner_rules()`) | 2.0k | no: they are the contract with the runner |
| SessionStart hooks | 0 | the plugin's hook injects only after a compaction |
| everything else: CLI system prompt, built-in tool schemas, deferred-tool names, MCP instructions | about 14k | the floor |

**The four session-start opt-outs** (1.52.42; one haiku call each on config-harness, main account, lean flags on, no runner
rules: 24.02k first-call total before): CLAUDE.md excluded 18.85k (-5.17k), git instructions off 23.13k (-0.89k),
auto-memory off 23.18k (-0.84k), context7 off 23.81k (-0.21k), all four 16.91k (-7.11k, 30%). A sub-agent spawned
under `claudeMdExcludes` does not see the excluded file either (a haiku sub-agent answered NO with it, YES without),
so the saving repeats for every sub-agent. The runner rules gain one pointer line (about 0.1k) instead of the 5.2k file:
the target repo's `CLAUDE.md` routes to its binding rules (config-harness: `docs/learned-rules.md`, read by scope in
Phase 2 and 8b; the plugin's prompts name `docs/adr/RULES.md` or a section of `CLAUDE.md` as the rules file, so
they no longer find it alone) and holds its environment notes (config-harness: `cargo` is not on a non-interactive
PATH, database tests need `DATABASE_URL` and run in the VM through `deploy/host/`). The rest of that file the plugin
already carries: the ADR index (`adr` scripts), branch and PR targets (`stub_target.py`, the work-on flow), signed
commits (`gate_commit.py`), VM runs (`run_tests.py --vm`). Org-managed plugins and telemetry are left alone.

**A fresh stage instead of a compaction.** When a stage's `phase_checkpoint` is taken with the session's context
(the last assistant usage, `input + cache_read + cache_creation`) at or above `ASD_RUNNER_SPLIT_AT`, the runner waits
for that checkpoint's tool result, kills the session and ends the stage as if the agent had written
`RUNNER: STAGE-DONE <phase>`. The runner records `split at checkpoint <phase>: context <N>k`. The next stage is a new session
that starts with the usual hand-off (`RUNNER HAND-OFF`, with this checkpoint's summary and its `NEXT:` line). Notes:

- It only happens at a checkpoint, never mid-work; a stage with no checkpoint is compacted by the CLI as before.
- A split stage counts as a stage for `--max-stages`.
- It is held (logged, tried again at the next checkpoint) while a background command or workflow is in flight, because
  those die with the process. It cannot happen while the run waits on the owner: no checkpoint is taken then, and a
  checkpoint's turn that ended first clears it.
- A keep-alive ping needs a waiting or queued run; the session it pings is the newest one (`state.session`).

**The auto-compact window.** `--autocompact 250000` (Claude Code 2.1.221 or later; 100k to 1M) applies to this
session only and, unlike `autoCompactWindow` in a settings file, outranks the project's `.claude/settings.json`
(200000 in config-harness) and a window saved with the CLI's autocompact command; the runner also drops
`CLAUDE_CODE_AUTO_COMPACT_WINDOW` from the child's environment because that variable would outrank the flag. The CLI
caps the value at the model's window. `claude-opus-5-5` and `claude-sonnet-5-5` both report a 1M window (`modelUsage.contextWindow`),
so 250k takes effect (compaction near 218k); for a model with a 200k window (haiku-3/4, opus-4, sonnet-4) none is passed.
With the split at 135k, the compaction at about 218k is the backstop for a stage that never checkpoints.

## Keeping the Mac awake

While at least one run is `running`, `waiting`, `queued` or `paused` (its runner
process alive), the dashboard server (`shared/questions/awake.ts`) holds one
`/usr/bin/caffeinate -s -i` child, and kills it when no run is live. It
re-checks on the server's 10 s rescan and kills the child when the server
exits. Darwin only, and only when `/usr/bin/caffeinate` exists;
`ASD_CAFFEINATE=0` turns it off.

Limit: `-s` only prevents system sleep while the Mac is on AC power. On battery
with the lid closed, macOS still sleeps and caffeinate does nothing. That needs
`sudo pmset disablesleep 1` (on the corporate Mac, through Admin By Request).

## Disk space

Every worktree a run makes has its own build output (`target/` for Rust,
6–21 GB each), and a fan-out makes several (`<repo>-<ID>-P<n>`, `-TE-*`,
`-MUT`). Concurrent runs fill the disk fast, and a full disk breaks every run
at once, the VM included. Two guards keep that from happening:

- **The runner's floor.** Before it starts a stage, `take_slot` checks the free
  space on the runs directory's disk. Below `ASD_DISK_FLOOR_GIB` (default 30)
  the run goes `queued` with the event "queued: the disk has X GiB free …",
  starts `scripts/disk_guard.py reclaim` (at most every 5 minutes;
  `ASD_DISK_NO_RECLAIM` turns that off), and starts once there is room.
- **The dashboard's watch.** Every `ASD_DISK_EVERY_MS` (default 5 minutes;
  `ASD_DISK_WATCH=0` turns it off) the server reads
  `disk_guard.py status --json`. Below `ASD_DISK_RECLAIM_GIB` (default 80) it
  starts a reclaim; below the floor it notifies the owner once and the home
  page shows a banner. The reading is `disk` in `GET /api/runs`.

`disk_guard.py reclaim` removes build directories (`ASD_DISK_BUILD_DIRS`,
default `target`) in this order until free space reaches the reclaim target:
worktrees with no live run, then a live run's sub-worktrees, then the own
worktree of a parked run (queued for a slot, waiting on the owner, or paused:
no build runs there now, and its next one starts cold), then the main
checkouts. It never touches a running run's own worktree, nor anything written in
the last `ASD_DISK_IDLE_MIN` minutes (default 60). One reclaim runs at a time
(a lock in the runs directory). After that it runs
`<main>/.agentic/reclaim.sh <checkouts…>` when the repo has one, so a repo can
clean what only it knows, such as build output inside a VM.
`disk_guard.py status` lists what a reclaim would remove.

**The sweep.** A repository's own build output can fill while the host still
has room: a VM's disk holds a build directory per worktree and, as a sparse
image, gives nothing back to the host until it is trimmed. So the dashboard
also runs `disk_guard.py sweep` every `ASD_DISK_SWEEP_EVERY_MS` (default one
hour, first a minute after start; `ASD_DISK_SWEEP=0` turns it off), whatever
the free space. It runs each repository's `.agentic/reclaim.sh` with the
dossier worktrees whose run is no longer live and whose host build has been
idle for `ASD_DISK_IDLE_MIN`. It never names a live run's worktrees, a
worktree that is no dossier's, or the main checkout. With no such worktree the
hook still runs, with no arguments, so the repository can clean its orphans
and trim. A sweep and a reclaim share the lock, and the reading's `lastSweep`
says when the last sweep ended.

## Runner-mode rules (the appended system prompt)

The prompt tells the orchestrator these rules:

- You run under the runner and nobody reads the terminal.
- Ask only through `ask_owner.py`, and never wait in the background:
  - keep working on whatever is not blocked;
  - when nothing can proceed, end the turn with
    `RUNNER: WAITING <id>[,<id>...]`.
- Run every test through `run_tests.py` and every gate list through
  `run_gates.py`, never an ad-hoc script, so the timeline gets the suite and
  gate lines; and only the tests the step names (under SUITE-MODE `stub`, the
  new and affected tests plus CI: the full suite belongs to `/stub`).
- Never end a stage while background work it started is still running;
  wait for its notification first (the runner holds an early end).
- End each stage with its `phase_checkpoint` call and then the line
  `RUNNER: STAGE-DONE <stage name>`, and stop. The next stage starts in a
  new process with your checkpoints as its hand-off.
- `RUNNER KEEPALIVE: ...` is the runner keeping the prompt cache warm while
  you wait: reply with exactly `OK` and do nothing else (no tool call, no
  `RUNNER:` line, no change to your plan). The runner asks nothing else of you
  with it.
- End with `RUNNER: DONE` when `/work-on` is finished, and with
  `RUNNER: FAILED <why>` when you cannot go on. When the owner rules the
  dossier back to `/plan`, record the ruling and the re-plan's inputs in the
  dossier, leave nothing half-built behind, and end with
  `RUNNER: REPLAN <why>`, never `FAILED`: the runner starts `/plan` itself.
- Post an update within a long stage, in one line, when something worth
  knowing happens:
  `ask_owner.py note --root R --dossier ID --text "..."`.
  Examples: a wave returned, a suite went green or red, an arbitration was
  ruled.

## The updates store

- **Location:** `$ASD_RUNS_DIR`, default `~/.agentic/runs`. Each run has
  `<key>.jsonl`, append-only, with lines of the same single-write form as
  the questions store. The key is `<repo>.<ID>`.
- **Line fields:** `ts`, `kind` (`runner`, `stage` or `update`), `text`, and
  optionally `stage`, `state` and `session`.
- **Who writes it:**
  - **The runner:** `runner` lines for start, stage start and end,
    waiting, answered, done, failed and stopped.
  - **The plugin's PostToolUse hook:** a `stage` line for every
    `phase_checkpoint` call when `ASD_RUN_KEY` is set. It writes the stage
    name and the first line of the summary.
  - **The suite line:** a `run_tests.py` call (one the command starts, not one
    it only names, such as a `ps | grep`) writes `suite: passed N failed M`
    from its digest; a `--detach` or background call writes `suite started`,
    and its `--wait` call writes the result.
  - **The same hook, for progress:** an `update` line with `auto: true` for
    each `dossier_edit.py log` line, each `run_tests.py` and `run_gates.py`
    result, each workflow started and agent spawned, and each PR opened or
    merged. This happens in every /work-on, /stub and /plan session, with or
    without a runner. Without a runner, the run key is learnt from the first
    command that names a dossier, or, in a /stub run, from the stub's record
    file (`<repo>.stub-<date>` or `<repo>.<branch with / as ->`). `ASD_RUN_UPDATES=0` turns it off.
  - **Line types:** every automatic line has an `event` field: `log`, `suite`,
    `gates`, `pr`, `workflow`, `other`, `spawn` or `spawn-done`.
    - A `spawn` line is one line, the agent type and the description, never
      the prompt. It has a `status` (`running` for a background launch, `done`
      for a foreground one) and its `agent_id`.
    - The SubagentStop hook (`shared/claude/subagent-stop.ts`) closes a
      `spawn` with a `spawn-done` line for the same `agent_id`. Its `status`
      is `done`, `gap` or `contract-change`.
  - **`ask_owner.py note`:** an `update` line, for what no command shows.
- **The runner's own state:** `<key>.runner.json` holds
  `{pid, started, state, stage, session, questions}`, rewritten on every
  change. `state` is one of `running`, `waiting`, `done`, `failed` or
  `stopped`. A pid that is no longer alive reads as `stopped`.

The dashboard shows these lines on each running dossier, newest first.

## Standing repair of the VM and the stack (`scripts/stack_repair.py`)

Off unless `ASD_STACK_REPAIR=1`: a repair restarts resources other runs share,
so the owner enables it explicitly. When on, the runner checks before every
stage, and again on a `RUNNER: ENV vm|stack ...` line:

- Detect, read-only: `limactl list --json` (the VM must be Running) and
  `docker compose ps --format json` over `limactl shell` (every service running,
  none unhealthy). The disk is detected (the existing floor) but never repaired
  automatically: no deletion, the owner is asked as before.
- Repair, with the repo's own commands only, named in the repo's
  `.agentic/stack-repair.json` (or `ASD_STACK_REPAIR_CONFIG`, or the plugin's
  `config/stack-repair.json` under `repos.<directory name>`): `vm_start` (VM
  stopped) and `stack_up` (stack unhealthy; the repo's `up.sh`, which takes the
  repo's stack lease itself). Placeholders `{vm} {root} {compose_dir}`.
- One repairer at a time: an flock on `~/.agentic/stack-repair.lock`
  (`ASD_STACK_REPAIR_DIR` moves it); other runners wait, then look again before
  they touch anything.
- At most 2 attempts per hour per resource (`stack-repair-state.json`); after
  that the session is told `not repaired` and asks the owner.
- Every action goes to the runner log and the run's events.
