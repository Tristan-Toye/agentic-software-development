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
    - `RUNNER: WAITING ci:<pr>[,ci:<pr>...]` — the CI breakpoint: the runner
      runs `wait_ci.py --pr <pr> --timeout <ASD_CI_TIMEOUT, 5400>` itself
      (with `--log <dossier>` when the run has a dossier file), so no tokens are
      spent while CI runs, and sends the verdict into the same live process:
      `CI RESULT <pr>: green|red|timeout|never-started <failing checks + log tail>`.
      Several PRs wait in parallel and come back together, one line each;
      `ci:` ids are never mixed with question ids. While it waits the run's state is
      `waiting` (event `waiting on CI #<pr>`, state field `ci`), so the slot
      is free and the keep-alive ping runs exactly as in an owner wait; the
      next step takes a slot again, ahead of runs not yet started. A `wait_ci`
      exit other than 0, 3, 6 and 7 (1 failed; 2, 4, 5 error, stale green, conflict)
      is `red` with the exit code and its output in the summary. Exit 6 is
      `never-started`: the PR head has no workflow run even after `wait_ci`
      re-triggered it once (`gh pr close` + `gh pr reopen`, once per head sha,
      never a force-push). It means CI did not run, not that it failed. The
      session is told so and to end with `RUNNER: FAILED CI never started on
      PR <n>: ...` (a blocker for the owner); a further `WAITING ci:<pr>` for
      that PR is refused by the runner without running `wait_ci` again.
      Exit 7 is also `never-started`, but the cause is known: the PR is
      stacked on a parent PR (the open PR whose head is its base) that is
      DIRTY/CONFLICTING, and GitHub starts no workflow for the child
      meanwhile. `wait_ci` checks this before any retrigger. The runner, not the
      session, gets the parent resolved: it starts a `resolve-merge` run for
      the parent PR (key `<repo>.<ID>.resolve-<pr>`, outside the merge queue,
      which would merge a clean dossier into its stub), polls until the parent
      is no longer DIRTY and runs `wait_ci` on the child again, with the session
      parked and told nothing. Only when the parent still conflicts after
      two such runs the session gets `never-started` and ends with
      `RUNNER: FAILED CI not started on PR <n>: its parent PR still
      conflicts ...`; the runner then hands the stack to the merge queue (the
      dossier is enqueued, the run ends `done` with the timeline line "a parent PR
      of the stack conflicts with its base; the merge queue resolves it", its lock
      is released and no run-failure question is asked: the queue's resolve-merge
      could not run while that question held the run, W-182); the session never resolves the parent itself (the
      dossier's own PRs are not its to edit) and never asks the owner how. The
      "no further wait after never-started" refusal is for exit 6 only. An owner
      message ends the wait early and is delivered; the agent waits again.
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
      When that re-plan ends `DONE` with the dossier `ready` (any `/plan` run
      of a dossier a `/work-on` run already started counts, also one the
      owner started), the runner requeues `/work-on` itself. In local mode,
      or once the plan has merged into this checkout, that is the plain
      `/work-on`. In committed mode with the plan PR still open, it is
      `/work-on <ID> plan-branch=plan/<KEY>` (`--plan-branch`): the newest
      `origin/plan/*` branch committed since the re-plan started whose copy
      reads `ready`. The build forks from the stub as always and
      cherry-picks the plan commits (`work-on/phase-2-conditional.md`). A plan that did
      not reach `ready` is logged ("/work-on not requeued") and left to the
      owner. `ASD_RUNNER_REQUEUE=0` turns this off.
      **Parking.** A `/work-on` started fresh (requeued, pressed, or started
      by automatic work) whose `blocked_by` names a dossier that is not
      `done` (the same rule as the dossier graph; a blocker with no dossier
      file counts as missing) never opens a session or takes a slot: the run
      ends `parked`, its record holding `parked.blockers` (`W-023
      (building)`), and the dashboard shows "parked · waits for W-023". The
      2-minute `revive` tick checks every parked run, with no age limit, and
      once every blocker is `done` it starts `/work-on` again by itself (a
      fresh session, the same `--plan-branch` and `--model`). A re-plan that
      adds a blocker is the usual way in. A resumed run (a live record with a
      session) is never parked. `ASD_RUNNER_PARK=0` turns it off.
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
  frees. So does a run the merge queue started (`resolve-merge`, `after-merge`):
  merges wait on it. A run waiting on the owner holds no slot. The dashboard
  shows "queued · waiting for a slot". So a usage-limit reset, or many Start
  presses, no longer sets ten sessions going in the same minute.
  **One reserved slot per active repo, the rest shared:** `~/.agentic/active-repos.json`
  (`ASD_ACTIVE_REPOS_FILE`) is `{"repos": {"<repo>": "<main checkout root>"}}`, keyed by the repo
  name the run keys use; `run_dossier.py active` shows it, `active --add [NAME] [--root R]` and
  `active --remove NAME` edit it. Without the file nothing is reserved and the dashboard shows
  every repo. An active repo holds a reservation while a dossier in
  `<root>/.discovery/dossiers/*.md` has a front-matter `status` other than `done` (draft, blocked and
  dropped count as buildable; read every ~60 s, `ASD_RESERVE_TTL`), also when none of its runs is queued;
  with no such dossier it gives the slot up. A run of a reserved repo that holds no slot is admitted
  when `held < cap`, skipping the shared queue, but only its repo's first queued run takes it. Any
  other run is admitted when `held + (other reserved repos holding no slot) < cap` and nobody is
  ahead of it: today's first-in-first-out with the reservations deducted. A cap not above the number
  of reservations still lets each reserved repo run one (the shared pool is empty). Cap 0 stays
  unlimited; the disk floor and `rehome` are unchanged. The `queued` event says "waiting for a shared
  slot (N reserved for a, b)" or "waiting for the reserved slot of X", and the start event "using
  the reserved slot of X". With the file the dashboard (runs, stubs and, through them, the auto-work
  facts) lists only the active repos: `shared/questions/activerepos.ts`, applied in `candidateRoots`.
  **`testing` releases the slot:** while the session is blocked on a long wait
  the runner records `testing` and `slot_census` does not count it, so queued
  runs may start (podcast W-007 once sat 35 min in the foreground on
  `run_tests.py` holding a slot the whole time). The session is blocked when its
  in-flight foreground Bash call runs `run_tests.py` or `run_gates.py` (`--wait`
  included), polls a detached run's state directory (`run_gates-detach/`,
  `run_tests-detach/`), or waits on CI (`wait_ci.py`, `gh pr checks --watch`,
  `gh run watch`), or has produced no output for more than
  `TESTING_QUIET_SECS` (300; `ASD_TESTING_QUIET_SECS` overrides). The CLI's own
  progress lines for the call in flight (`tool_progress`, the `task_started` and
  `task_progress` system lines) are not output here: they arrive every few
  seconds during any long Bash call, and counting them kept a 10-minute poll
  loop from ever looking quiet (podcast W-018, 2026-11-13). They still count
  against a stall. It is also blocked when its turn ended and the only work
  still in flight is background Bash commands (a CI wait launched with
  `run_in_background`; config-harness W-132 and W-156 each held a slot ~45 min
  that way): once the idle window passes with those in the transcript, the
  slot is released; a background agent or workflow keeps it, because a model
  works there. The session's next event ends that wait. When the call's result
  arrives the state
  goes back to `running` at once, without queueing. Trade-off, accepted: the
  live count may briefly exceed the cap until another run finishes or waits.
  `testing` counts as live everywhere else (stale-pid checks, dashboard,
  awake, merge guards); a `testing` run is never nudged or pinged. The
  dashboard shows "testing · slot released".
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
- **Refused sign-in:** a turn that fails because the API refused the sign-in
  for a reason outside the account (Cloudflare's "403 Attention Required!" page
  in front of the API, which the CLI shows as "Failed to authenticate"; a 401
  `authentication_error`; an expired OAuth token) is retried the same way and
  posts `paused: sign-in refused (…)`. A block of the network address clears by
  itself within minutes to an hour, so it is not the owner's call at first; but
  a sign-in that stays refused needs the owner, so it asks after
  `ASD_RUNNER_AUTH_RETRIES` retries in a row (default 16, about 3.5 hours at the
  backoff above): "sign-in refused on 16 retries in a row: …".
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
  One runner per run key: every runner takes an exclusive flock on `<key>.runner.lock` for its
  whole life; a second start for the key (revive, Resume, an answered failure question) logs one
  line and exits 0, and `revive` skips a key whose lock is held. The pid in the record is not the guard.
  A session is resumed (`claude --resume`) only on the account whose config dir holds its
  transcript (`projects/*/<session>.jsonl`): after a switch of account (a usage limit on
  every other one) the stage starts fresh from the checkpoints, with one event saying so,
  and a resume that comes back "No conversation found with session ID" (also as an error
  result) is the same fresh start, never a failure or an owner question.
  Auto-work treats a member whose facts read `stopped` but whose `runner.json` still holds
  a live state as restarting for `ASD_AUTO_WORK_GRACE` (the revive is on its way), and a
  paused cycle (member, relaunch) clears itself once that run is live again.
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
- **Commit hooks are skipped only under the Phase 2 policy:** while a /work-on fan-out is mid-migration the orchestrator may skip a workspace-wide hook on its own commits (`references/work-on/phase-2-conditional.md`, gates as cover). Every other commit runs the hooks (base or stub merges, resolve-merge, /fix-deferred, every commit after the Phase 5 merge), and the runner rules forbid `--no-verify`, `LEFTHOOK=0` and an owner question asking to allow it there. A failing hook is fixed in the file it names; only a failure in a file the branch did not change that also fails on the base may be asked about, with fixing it on the base as an option (W-182: a moved `#[cfg(target_os = "linux")]` hidden for five days by stretching the Phase 2 bypass to later commits).
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
  affected tests and gates, and pushes. It never merges into the stub. A run
  the merge queue starts has the key `<repo>.<ID>.resolve-<prs>`, so the dossier's
  own run (and its failure question) never blocks it. The queue waits on that run
  as long as it is alive: time queued for a slot or spent on an owner question is
  not counted toward `ASD_MERGE_RUN_TIMEOUT`, a run that failed but still holds its
  lock or has an open run-failure question is waited on (no new attempt), and a run
  still live when its working time is up is waited on, not failed. The queue passes
  nothing but the PR numbers; the command file makes the run re-run a clash check
  exactly as the queue does (bottom PR's base, top PR's head, bare names). W-195's
  "no migration clashes" came from `origin/`-prefixed names read as origin/origin/...,
  fixed by `bare_ref` and the exit 2 on unreadable refs.
- **The stub breakpoint, `RUNNER: WAITING stub`:** a build whose Phase 2
  finds the stub being integrated (`stub_target.py` exit 3) ends its turn
  with it. The runner records `waiting`, polls `stub_target.py` every
  `ASD_STUB_POLL` seconds (default 300) without spending tokens, and once the
  integrated stub has merged and the new stub is cut, takes a run slot and
  sends `STUB READY: <branch>` into the same session.
- **The merge breakpoint, `RUNNER: WAITING merged:<pr>`:** a `/plan` whose
  stubs or contract need code only an unmerged PR adds ends its turn with it
  instead of asking a hold question. The runner records `waiting` (slot free,
  `waiting_for: pr`, the event "waiting for PR n to merge"), polls
  `gh pr view` every `ASD_PR_POLL` seconds (default 120) with no tokens, and
  sends `PR RESULT <pr>: merged into <base>` or `PR RESULT <pr>: closed without
  merging` into the same session. Closed unmerged is the owner's decision, so
  the agent asks then. A blocker that only affects the build stays in
  `blocked_by`: no wait, no question.
- **The CI breakpoint, `RUNNER: WAITING ci:<pr>`:** a headless agent never
  calls `wait_ci.py` in a loop (each call is a full turn that re-reads the
  whole context: 88 `--timeout 580` calls in 7 days of `/fix-deferred`). It
  pushes, ends the turn with the line, and the runner waits (see the turn
  lines above). In an interactive session, call `wait_ci.py` directly.
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
- **`seed-<slug> --command plan --seed FILE`:** a free-form `/plan` (a
  description, no dossier and no deferred item yet). The id is a slug,
  `seed-<lowercase words>`, so the key is `<repo>.seed-<slug>`. The runner
  copies FILE to `<key>.seed.md` (at most 64 KiB) and starts
  `/plan the description in <that file>`. A restart (revive, Resume) finds
  the kept brief without `--seed`. No `/work-on` is requeued when it ends:
  the dossier it wrote goes the usual way. The dashboard's Running now lists
  it as "Planning: <the brief's first line>".
  While it runs, the ids it claimed are its own (`scripts/planning_runs.py`,
  ported in `shared/questions/planning.ts`): a stage event naming the id as
  claimed or minted, the id two stage events open with, its questions, and an
  empty `<ID>-<slug>.md` created after it started, matched by its seed slug
  (or the only live planning run). No second `/plan` or `/work-on` of such an
  id starts: the launch refuses it, and a run that queued before the claim
  checks again once it has a slot and ends done, "not started", with no session.
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
  window lives in `<usage dir>/burn.json` as the pooled rate, and each account
  has its own EWMA and sample count (`{"rates": {...}, "by_account": {name:
  {window: {"rate", "n"}}}, "last": {...}}`; a file without `by_account` still
  loads). Accounts are different plans, so an account's own rate is used once
  it has `ASD_BURN_MIN_SAMPLES` (3) samples in that window, else the pooled
  rate, else the prior; the `accounts` rows of `slots-cap.json` say which
  (`burn`, `burn_from`). The prior, until something is learned:
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
- **Account at slot grant.** A run that starts a stage with no session to
  resume (no `--resume`: a fresh session) is placed, when it is granted its
  slot, on the account with the most paced headroom: the largest `runs_a` minus
  the runs already working on that account. Limited accounts and accounts with
  no reading are not chosen; a tie keeps the account it had. A run that resumes
  a session keeps its account (the transcript lives in that account's config
  dir) unless it is limited, as before. The event is `slot free on account
  NAME (most headroom: ...)`. Not with a fixed cap.
- **Never preempts.** A lower cap only stops admissions; a run that works
  keeps working. The cap is cached for ~60 s (`ASD_PACE_TTL`) in
  `<runs dir>/slots-cap.json` (`{cap, reason, ts, accounts, rates, ...}`), so
  all runners agree. The dashboard's "Running now" header shows it ("4 working
  · 5 slots", the reason on hover), and the `queued` event carries it: `queued:
  3 of 3 runs are working (3 slots: main 5h 85% resets in 2.0h, ...)`.

Knobs: `ASD_RUNNER_ADAPTIVE` (`0` = fixed), `ASD_RUNNER_MAX_LIVE` (fixed cap),
`ASD_RUNNER_MIN_LIVE` (3), `ASD_RUNNER_CAP_MAX` (6), `ASD_PACE_TTL` (60 s),
`ASD_PACE_REFRESH_S` (900 s), `ASD_BURN_MIN_SAMPLES` (3), and for tests `ASD_RUNNER_NOW` (epoch seconds).

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
  `ASD_KEEPALIVE_MAX_H` hours (default 24), or with `ASD_KEEPALIVE=0`. A skipped
  ping that was due is logged once per reason: `keepalive skipped a ping for
  <run>: <reason>`.
- A run in state `running` or `testing` is pinged too when its turn has ended
  (the session waits on a background task, from the stage's read loop) and its
  last API call is old enough. A run with a turn open (the session is working or
  blocked in a tool call) cannot take a ping: that skip is logged.

Knobs: `ASD_KEEPALIVE` (`0` = off), `ASD_KEEPALIVE_AFTER` (3000 s),
`ASD_KEEPALIVE_MAX_H` (24), `ASD_KEEPALIVE_POLL` (check interval, 5 s) and
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

## Repo-declared CI recovery

A repository declares in `.agentic/ci-recover.json` the red checks only its own command
clears (`name`, `detect`, `ci_check`, `ci_pattern`, `fix`, `paths`, `message`). The
plugin knows no repository: `scripts/ci_recover.py` reads that file. `prevent` runs in
the build before the push; `recover` runs when every red check of a PR is a declared
`ci_check` whose failed log matches `ci_pattern`: it runs `fix` (for config-harness,
`seal_bootstrap_from_cloud.sh` through `deploy/host/host.sh exec`, in the VM), commits
`paths` through the repo's hooks, pushes the dossier branch and the runner waits for CI.
It never asks the owner; another red check alongside, or an unmatched log, is not
recovered. The fix's output is never read or logged.

## Runner-mode rules (the appended system prompt)

The prompt tells the orchestrator these rules:

- You run under the runner and nobody reads the terminal.
- Ask only through `ask_owner.py`, and never wait in the background:
  - keep working on whatever is not blocked;
  - when nothing can proceed, end the turn with
    `RUNNER: WAITING <id>[,<id>...]`.
- Run every test through `run_tests.py` and every gate list through
  `run_gates.py` (`--path-prepend`/`--env-script` for a toolchain off the
  shell PATH: both apply to a local run and in the VM, and a missing env
  script fails the run, `--detach` then a blocking `--wait ID` for a long run (a call blocks at most 270 s, `--wait-cap` / `ASD_WAIT_CAP`
  change that, so a sub-agent's 5-minute prompt cache stays warm): never a
  `sleep`/`pgrep`/`until` poll loop or repeated status turns; only an exit 6 (still running) earns another
  `--wait`; `--compile-lint-only` at the contract commit, the full list at the first push and the final head, `--affected-since REV` for the fix rounds between), never an ad-hoc
  script, so the timeline gets the suite and gate lines; and only the tests the step names (under SUITE-MODE `stub`, the
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

## Automatic work (`scripts/auto_work.py`)

Off unless the owner turns it on for a repository (the dashboard toggle, or
`auto_work.py --enable --root R`, which also turns the scripted stub merge on).
It starts nothing itself: every member of a cycle goes through
`run_dossier.py <ID> --command work-on|plan|fix-deferred`, so the slot cap, the
`queued` state, the account routing and every owner question work as for a
click. It follows the runner's file per dossier (`<repo>.<ID>.runner.json`):
a state in `running`, `testing`, `waiting`, `queued` or `paused` is waited for;
`failed` or `stopped` pauses the cycle. The file lags a start by a few seconds,
so a started run is matched by its command and its `pid`/`started` pair before its
end is believed, and a run that shows nothing within `ASD_AUTO_WORK_GRACE`
seconds (default 600) is started once more (recorded on the member), and a retry that
also shows nothing pauses the cycle; a run in `queued` counts as shown. A /work-on that ends done with the dossier
not at `pr`/`done` first gets one `run_dossier.py chat` message to its own session (reply
`INTENDED:` + reason if it is meant); the cycle pauses only on a still-wrong status after the
reply, no reply within `ASD_AUTO_WORK_ASK_WAIT` seconds (default 1800), or a failed chat. See `references/owner-questions.md`
(Dashboard, Automatic work) for the cycle.

A run that failed and asked the owner itself is not a failure for the cycle: `run_dossier.py`
`ask_failure` records the state `failed`, saves its `run-failure` question in `runner.json` (`questions`) and keeps its
process alive waiting on the answer. While that process is alive and the question unanswered, auto work marks the member
"waiting on <question id>", does not pause, raises no question of its own and never starts a second run; Retry brings the
runner back to running (the member is normal again), Stop ends it `failed` with no open question (the usual pause and
`auto-work-pause` question follow). A cycle already paused for a member whose run is live again, or waits on its own
question, clears itself on the next tick (no relaunch) and closes its open `auto-work-pause` question with an agent answer.

Every pause also raises one owner question (product kind, topic `auto-work-pause`, on the member's dossier or `auto-work` for a repo-level stop; the id is kept in `paused.question`, so never twice for one pause): A resumes (for an `accept` pause A accepts the still-open items, B relaunches the run), the last option leaves it paused. Answering applies the action as `--resume` does; `--resume` or the Resume button closes the open question.

Eager merge is a second, separate switch (`auto_work.py --eager-merge on|off --root R`,
on by default, works with automatic work off): each dossier that is finished (build
done, fix-deferred run over, no open deferred item) and has an open pull request into
an open stub is put on the merge queue once, so Integrate has little left to merge.
A failed merge is retried by itself when retrying is safe: the failure was transient
(CI still running or timed out, a gh or network error), at most twice per head of the
stack, or a later run of the dossier started after the record and has finished, or a
queue-started `resolve-merge` run of the dossier ended `done` after the record (the
queue gave up while that run was still waiting or asking, e.g. W-197; also capped at
twice per head). "A resolve-merge run of W-161 is still live" is a wait, never a human
failure. One timeline line says so. A cancelled merge, and a failure that needs a human
(a conflict, red checks, spent resolve-merge attempts), stay the owner's to retry.
A finished `/fix-deferred` that leaves deferred items open never pauses the cycle (the owner's ruling, 2026-11-25:
auto work runs autonomously). Every open item, whether one the run was launched with or one its own review added, gets a
follow-up `/fix-deferred`, at most two per member; after that the member is finished and the next cycle adopts the
dossier. The `accept` pause below remains only for pauses raised before this rule.
Accepting a pause ("Accept the open items", or `--resume accept`) rules every open
deferred item leave-open (`<repo>.<ID>.deferred-owner.json`, as the dashboard's Leave
open does), so the eager-merge hold lifts and the dossier joins the queue (W-183).

## Standing repair of the VM and the stack (`scripts/stack_repair.py`)

Off unless `ASD_STACK_REPAIR=1`: a repair restarts resources other runs share,
so the owner enables it explicitly. A /stub run is the exception: it owns the
shared stack for its full run, so repair is on for it unless
`ASD_STACK_REPAIR=0`, and its agent repairs the rest of the test
infrastructure itself (`primary-agents/stub.md`, "The environment is yours to
repair"). Its `stack_up` must reach the repo's secrets: config-harness runs
`up.sh` through `deploy/host/host.sh exec`, which forwards the sops key into
the VM (a plain `limactl shell` cannot decrypt, and `up.sh` then fails without
touching `.runtime.env`). When on, the runner checks before every
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
- A dossier run is never answered with an owner question about the shared stack (owner ruling 2026-11-19, "if it is
  safe it should run it without asking me"). When the repair is off, bounded or fails, the session is told to skip its
  live stack tests, defer those criteria to the /stub full suite and head the PR note with `STACK-ENV: skipped — ...`.
  A /stub run owns the stack and keeps its behaviour; a disk and a stopped VM keep theirs.

**Stack settings behind a branch** (`scripts/stack_env.py`, called by `run_tests.py --vm`; on by default,
`ASD_STACK_ENV_CHECK=0` turns the check off, `ASD_STACK_ENV_SYNC=0` keeps the check but never restarts).
A branch can add a setting the shared stack needs (a new name in the repo's secrets catalog). The stack's env file
predates it, and the repo's env script then refuses every test. Before a VM run that sources that env script, the
setting NAMES the tree's catalog lists are compared with the NAMES in the env file the env script reads. No value
is read into output. On drift:
- re-run `stack_up` (the repo's `up.sh`) from the persistent checkout, the only checkout allowed to own the shared
  stack (a worktree's `up.sh` refuses): the main checkout, or for a run in the /stub stack checkout
  (`scripts/stack_checkout.py`, `<main>-stack`, a clone at the stub head) that clone itself, when its own catalog already has the missing names and no run holds the
  stack lease (`lease_probe`); under the repair lock, at most 4 times per hour per VM;
- otherwise (the persistent checkout is on an older branch, the lease is held, or `up.sh` did not close the drift)
  the run goes ahead without the env script and with the repo's `skip_env` switches, and its digest heads with
  `STACK-ENV: skipped — ...`. The stack tests did not run: that goes in the PR note and the build log, and the
  stub's full suite runs them. It is never an owner question, and the persistent checkout is never switched,
  pulled or written to.
- the full stack suite (`suite_markers`, for /stub) gets the sync but never the skip.
Config keys, beside `stack_up` in `config/stack-repair.json`: `env_script`, `env_catalog`, `env_file`,
`lease_probe`, `skip_env`, `suite_markers`, `fixture_stamp`, `fixture_probe`, `fixture_rebuild`, `leftover_projects`.

**A leftover compose project** (`stack_env.leftover()`, on a session's `RUNNER: ENV stack foreign_project_state ...`;
`ASD_STACK_LEFTOVER_REPAIR=0` turns the repair off). A run's op-mode bring-up refuses with `foreign_project_state
owner=<checkout>` when an earlier suite left the compose project up from another checkout. The session never tears it
down; it reports the refusal line and the runner decides. Config: `leftover_projects`, a list per repo, each
`{name, owner_probe, window_probe, pid_probe, down}` (config-harness: `nightwatch-w108`; the window file is
`${TMPDIR:-/tmp}/nightwatch-locks/stack-suite.window`, line 1 the holder's checkout, line 2 its pid). Safe means the
window's holder pid is no longer alive in the VM (or there is no window) AND the stack lease (`lease_probe`) is free. Then:
- `down` (config-harness: `cd {root} && bash deploy/host/opmode-stack.sh down`, which takes the suite-window guard and the
  stack lease itself and is not guarded by the foreign-state refusal) runs from the PERSISTENT checkout under the repair
  lock, at most 3 per hour per VM; the project must be gone afterwards. The session is answered `RUNNER ENV stack:
  repaired ...` and brings its own stack up again. This does not depend on `ASD_STACK_REPAIR`;
- otherwise (a live holder, an unreadable pid or window, the lease held, `down` failed or left it up, the hourly cap, the
  switch off) the session is answered to skip its live stack tests, defer those criteria to the stub's full suite and head
  the PR note with `STACK-ENV: skipped — leftover project ...`. Never an owner question. A `suite_window_active` or
  `stack_lease_refused` refusal (another run's suite is live) gets the same skip without any repair.
**A stale shared fixture** (`stack_env.fixture()`, same caller, after the settings check; `ASD_STACK_FIXTURE_REBUILD=0`
keeps the check but never rebuilds). The repo's env script refuses every DB-backed test when the shared fake-llm
fixture serves another source stamp than the tree's, and the stamp includes the root `Cargo.toml`/`Cargo.lock`, so a
dossier that changes a dependency or workspace lint always differs from development. The runner compares the tree's
stamp (`fixture_stamp`, run in the tree) with the running fixture's (`fixture_probe`; not answering is not stale). When stale:
- rebuild (`fixture_rebuild`) from the PERSISTENT checkout when its stamp equals the tree's and no run holds the stack
  lease; under the repair lock, at most 4 per hour per VM, holding the stack lease for the build itself (the config's
  command takes `flock` and exits 75 when it is taken). Never from a worktree; the persistent checkout is never
  switched, pulled or written to;
- otherwise (the stamps differ, the lease is held, the rebuild failed or left it stale) the run goes ahead without the env
  script and with `skip_env`, digest headed `STACK-ENV: skipped — fake-llm fixture ...`. It is never an owner
  question, and a session that reports the fixture as `RUNNER: ENV stack ...` is told to use `run_tests.py --vm` and
  never to rebuild it. This does not depend on `ASD_STACK_REPAIR`;
- the full stack suite (`suite_markers`, /stub) gets the rebuild, never the skip: when its tree's stamp differs from the
  persistent checkout's it rebuilds from the tree being integrated, as it owns the stack for the whole run; a failed
  rebuild is reported and the suite runs as asked.
