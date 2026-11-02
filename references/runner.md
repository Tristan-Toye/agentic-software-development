# The runner

`scripts/run_dossier.py` runs one dossier through `/work-on` without a
terminal session: one headless Claude Code process per stage, so every stage
starts from a clean context, and owner questions asked and answered through
`references/owner-questions.md`. One runner owns one dossier; it never picks
up another one while it waits.

    run_dossier.py [ID] [--root R] [--detach] [--command work-on|stub]
                   [--model M] [--permission-mode auto] [--max-stages N]
    run_dossier.py status [ID] [--root R]
    run_dossier.py stop ID [--root R]
    run_dossier.py --selftest

The repository defaults to the one the command runs in (its git toplevel).
It runs in the foreground; `--detach` starts it in the background with its
output in `<key>.runner.log`, which is how the dashboard's Start and Resume
buttons start it. Running it again after `stopped` or `failed` resumes from
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
  - **`ask_owner.py note`:** an `update` line. An interactive run, with no
    runner, also posts `note` at each checkpoint, so its updates show too.
- **The runner's own state:** `<key>.runner.json` holds
  `{pid, started, state, stage, session, questions}`, rewritten on every
  change. `state` is one of `running`, `waiting`, `done`, `failed` or
  `stopped`. A pid that is no longer alive reads as `stopped`.

The dashboard shows these lines on each running dossier, newest first.
