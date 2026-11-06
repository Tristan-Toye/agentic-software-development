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
    - `RUNNER: DONE` — the dossier is finished: the runner records `done`
      and exits 0.
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
  work at once. A run takes a slot before each stage and after an owner's
  answer; only `running` runs hold one. Without a free slot it records
  `queued` (`queued_since`) and waits, longest-queued first; the dashboard
  shows "queued · waiting for a slot". So a usage-limit reset, or many Start
  presses, no longer sets ten sessions going in the same minute.
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
  network error (…); retry n/8 of stage N at <time>`, waits (30 s, 1, 2, 5, 10,
  then 15 min; `ASD_RUNNER_NET_WAIT` overrides), and runs the same stage again
  from its checkpoints. After `ASD_RUNNER_NET_RETRIES` (default 8, about an
  hour) in a row it becomes a failure the owner rules on ("network errors on 8
  retries in a row: …"). A stage that ends well resets the count.
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
  `reason`, `tool_use_id`). The runner-mode rules tell the agent never to work
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
  logs `stage end held (n/3): …`, tells the agent to wait for the
  notification and end the stage again, and keeps the session open. After
  three holds it lets the stage end and logs what it cut off. The rules tell
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
- End with `RUNNER: DONE` when `/work-on` is finished, and with
  `RUNNER: FAILED <why>` when you cannot go on.
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
  - **The suite line:** a `run_tests.py` call writes `suite: passed N failed M`
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
