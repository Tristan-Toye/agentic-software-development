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
  After `ASD_RUNNER_IDLE` seconds of silence (default 1200) it sends a nudge
  into the same session, at most twice, and only then records `failed`.
- **Out of tokens:** a turn that fails on a usage or rate limit (or a
  process that exits with one on stderr) does not fail the run. The runner
  records `paused` with `resumes_at`, posts `paused: usage limit reached;
  resumes at <time>` (also to the Slack run thread), sleeps until the reset
  time the CLI printed (an epoch after `|` or an ISO time; else it retries
  every 30 minutes, `ASD_RUNNER_LIMIT_WAIT`), then runs the same stage again
  from its checkpoints. `stop` ends a paused run.
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
- **`--command fix-deferred`:** runs `/fix-deferred <ID>`, which fixes the
  ledger's direct items in one stacked pull request and seeds a dossier for
  each of the rest (`commands/fix-deferred.md`). The dashboard's "Fix all
  deferred" button starts it.
- **Limits:** `--max-stages` caps the number of stages a run starts
  (default 20). A crash of the process counts as `FAILED`.

## Runner-mode rules (the appended system prompt)

The prompt tells the orchestrator these rules:

- You run under the runner and nobody reads the terminal.
- Ask only through `ask_owner.py`, and never wait in the background:
  - keep working on whatever is not blocked;
  - when nothing can proceed, end the turn with
    `RUNNER: WAITING <id>[,<id>...]`.
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
