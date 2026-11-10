# Owner questions

How a run asks the owner a question it cannot settle itself, keeps working,
and waits only when it is blocked. The owner answers from a local page or from
a thread in the repo's Slack channel, and can ask follow-ups first.

Only the orchestrator asks. A sub-agent returns `GAP:` or `CONTRACT-CHANGE:`,
and the orchestrator decides whether that needs the owner. A run never picks
an option itself, however long it waits.

## Parts

- `scripts/ask_owner.py`: the CLI the orchestrator runs: `ask`, `reply`,
  `wait`, `list`, `show`. It reads and writes the store directly, so it works
  with the server down; the server then only adds the notifications.
- `shared/questions/server.ts`: one Bun process, no dependencies. It watches
  the store, notifies (macOS notification, the repo's Slack channel), serves the page on
  `http://127.0.0.1:7317`, and appends the owner's messages from the page and
  from Slack.
- `scripts/install_questions_agent.sh`: installs the LaunchAgent
  `dev.tristan.asd-questions`, which starts the server at login and keeps it
  running.

## The store

One directory, `$ASD_QUESTIONS_DIR` (default `~/.agentic/questions`). One file
per question, `<id>.jsonl`, one JSON object per line, append-only. Every
writer appends one whole line with a single `O_APPEND` write of under 4 KB
(longer text is cut at 3,500 characters with ` [cut]`), so concurrent writers
never interleave.

The id is `<repo>.<dossier>.Q<n>`: `<repo>` is the basename of the repository
root, `<dossier>` the dossier id, `<n>` one more than the highest existing `n`
for that repo and dossier. Example: `config-harness.W-123.Q2`.

Line fields:

| field | on | meaning |
|---|---|---|
| `ts` | every line | ISO 8601 UTC |
| `from` | every line | `agent` or `owner` |
| `kind` | every line | `question` (first line only), `reply` (agent), `followup` or `answer` (owner), or `answer` with `accepted: true` (agent, below) |
| `text` | every line | the message |
| `dossier`, `repo`, `root` | `question` | dossier id, repo basename, repository root path |
| `options` | `question` | list of `{"key": "A", "label": "...", "consequence": "..."}`, 2 to 4 |
| `recommendation` | `question` | an option key, or empty |
| `blocks` | `question` | what cannot proceed until it is answered |
| `option` | `answer` | the chosen key, or empty for a free-text answer |
| `via` | owner lines | `page`, `slack` or `cli` |

A question is **open** until it has an `answer` line, then **answered**. The
first `answer` line is final; later lines are ignored by every reader.

Sidecar `<id>.slack.json` (server only): `{"channel": "...", "ts": "..."}` of
the question's Slack message, the thread every later message goes to.

## The CLI

    ask_owner.py ask --root R --dossier ID --question TEXT
                     --option "A|label|consequence" [--option ...]
                     [--recommend A] --blocks TEXT        # prints the id
    ask_owner.py reply ID --text TEXT
    ask_owner.py wait ID [--timeout SECONDS]               # default: no timeout
    ask_owner.py list [--open] [--dossier ID]
    ask_owner.py show ID
    ask_owner.py --selftest

`wait` returns on the first owner line after the last agent line, polling the
file every 2 s:

- `ANSWER ID [option A]: <text>`, exit 0;
- `FOLLOWUP ID: <text>`, exit 10: answer it with `reply`, then `wait` again;
- `TIMEOUT ID`, exit 3.

It prints at most 40 lines.

## Asking, in a run

### What is worth asking

The owner's attention is the scarcest thing in the pipeline: a question
stops the run and frees its slot until it is answered. Ask only what needs
the owner's judgement:

- **Technical, architectural or product design:** two defensible designs, a
  trade-off the dossier does not settle, a scope or behaviour change, a
  contract ambiguity the plan leaves open (`AMBIGUITY:` with both readings).
- **Issues:** a blocker you cannot fix inside the dossier's scope, a failure
  you cannot attribute, evidence that the dossier is wrong or obsolete, a
  conflict between review lenses.
- **Actions outside the run's standing authority** (below): merging, a push
  to a protected branch, a force-push or history rewrite, a Jira create or
  transition, deleting anything not the run's own, finalizing time logs,
  anything touching a repository the owner does not own.

Never ask for what the run already has authority for, or what the plan
already settled. Do it, record it in `## Build log` with the usual line, and
name it in the run's summary, where the owner sees it without being stopped.
**A run started by the runner (dashboard Start, `run_dossier.py`) carries
standing authority for:**

- the worktrees the command creates, and removing them when done;
- pushing the dossier's own branch, and opening or updating its draft PR
  into the base or the accepting stub (`SUITE-RUNNER: … push and draft PR:
  yes`, with the source `runner start`);
- the readings, defaults and choices the dossier already records (its
  contract, its criteria, its rulings): carry them out, never re-confirm
  them. Only a reading the dossier leaves open is a question;
- a graduation issue in the plugin repository (`agentic-software-development`,
  the owner's): open it and record its number;
- a repository rule written from this build (Phase 8b): commit it on the
  dossier's branch; the owner ratifies it by reviewing and merging the PR.

A batch question ("approve these 6 readings", "may I push and open the
draft PR", "may I open the issue") is a sign the answer is already given:
check the dossier and this list before asking. Under a terminal run without
the runner, the gates of the command still ask, once, at its start.

### Steps

1. Ask as soon as the question is known, with every option's consequence and
   your recommendation, then go on with whatever does not depend on it.
2. Record `QUESTION: <id> — <question>` in the dossier's build log.
3. When nothing else can proceed, wait. Under Claude Code run
   `ask_owner.py wait <id>` as a background command and end the turn: the
   harness wakes you when it exits, and no request is made while you wait.
   Under opencode run it in the foreground with `--timeout 3000` and repeat
   on `TIMEOUT`.
4. On `FOLLOWUP`, answer with `reply` (facts, not a new recommendation unless
   asked), then wait again.
5. On `ANSWER`, record `OWNER-RULING (<date>): <id> — <ruling>` in the build
   log, then continue.

## The server

- **Watch:** it reads the store at start and on every change (`fs.watch`, plus
  a 10 s rescan).
- **New question:**
  - a macOS notification (`osascript`), titled with the repo and dossier;
  - a Slack message in the repo's channel: the question, `blocks`, each option as a button and as a
    line with its consequence, the recommendation, and a link to the page.
- **New agent reply:** posted into the question's Slack thread.
- **Reminder:** an open question whose last line is an agent line is notified
  again 30 min after that line, then 1 h later, then every 2 h. A new agent
  line restarts the schedule; a server restart sends no backlog.
- **Page** `GET /`: a dashboard (see Dashboard): the repo cards, then the queue of
  open questions, then running dossiers, this week's stub, stubs and open work.
  - **Accounts** (home): one card per registered login (`~/.agentic/accounts.json`)
    with its email and plan, "next runs use this" on the account the next
    stage starts on, a "blocked until …" chip while the runner has it marked limited, the
    5-hour and weekly meters with their reset times (`~/.agentic/usage/`), and
    how many live runs use it. **↻ Check usage** POSTs `/api/accounts/refresh`,
    which runs `run_dossier.py usage` in the background.
  - **Accounts:** a limit the runner recorded no longer shows once a later
    reading says the account's windows are open (`limitSuperseded`; the
    runner's `pick_account` uses the same rule). Two accounts logged in with
    the same email are flagged "same login as …": they share one set of limits.
  - **Running now** (home): every live run across repos (running, waiting,
    paused) with its step, account, sub-agents at work this stage, context and
    last activity; the header counts runs, working runs and sub-agents. A
    `/plan` run counts like a build: a dossier being planned is live (Running
    now, the stub card's In progress, its timeline), including one `/plan` is
    writing from a deferred item (its run `<repo>.<src>.plan-<D-n>`, found
    through the dossier's `DEFERRED-SOURCE` line, `runnerOf`). Before that
    dossier exists, the run shows as "Planning: <item>" linked to the source.
  - **Start runs the next flow:** **Plan** on a planned dossier (`/plan <ID>`,
    `run_dossier.py <ID> --command plan`), **Start** on a ready one
    (`/work-on`); **Resume** reruns the flow that stopped, with its items.
  - Each open question is a **chat until a button closes it**: the question,
  then the owner's typed messages and the agent's replies in order ("Waiting
  for the agent's reply…" while the owner spoke last), then the buttons, then a
  text box with one **Send** button. Typed text is always a follow-up and the
  question stays open. The agent answers it in the chat, and when it reads as
  an answer or an instruction it adds that reading as a button
  (`ask_owner.py reply <id> --text … --option "D|Confirm: <reading>|<what
  happens>"`). **Only a button closes a question**, so every question ends with
  one press; the agent never closes one itself. When its recommendation
  changes, the agent moves it (`ask_owner.py reply <id> --text … --recommend
  <key>`), and the page highlights the new key. (`ask_owner.py accept`, and an
  `answer` with `accepted: true` from older runs, still count as an answer.)
  Slack thread replies follow the same rule (`replyKind`).
  - It refreshes every 5 s, patching the DOM so a half-written text box and
    the scroll position survive.
- **The runs MCP server** (`shared/claude/runs-mcp.ts`, registered in the
  plugin's `.mcp.json` as `runs`): the dashboard for agents. It is a thin
  client of this HTTP API (port `ASD_QUESTIONS_PORT`, default 7317), with no
  state or rules of its own, so the page, Slack and agents always agree. Read:
  `runs_overview`, `run_detail`, `deferred`, `stubs`, `stub_detail`, `questions`, `question`,
  `runner_log`. Act, exactly as the page's buttons: `answer_question`,
  `reply_question`, `message_run`, `run_control` (start, resume, stop,
  fix-deferred), `plan_item`, `merge_preview`, `merge`, `block_ruling`,
  `write_summary`, `refresh_usage`. A dashboard that is down is reported as
  such. **Only sessions in this plugin's own repository** (any worktree of
  it; `ASD_RUNS_MCP_REPOS` widens the list) get the tools: elsewhere,
  runner stages and implementers included, `tools/list` is empty and a call
  is refused. Test: `shared/test/runs-mcp.test.ts`.
- **API:**
  - `GET /api/questions?state=open|all`
  - `GET /api/questions/<id>`
  - `GET /api/runs`: all dossiers with stage, session, PR, updates, runner
    state and answered Q&A as JSON, plus `disk` (the last disk reading, see
    `references/runner.md`, "Disk space"); `GET /api/stubs`: stubs per repo and the
    open-work repos; `GET /open-work/<repo>`; `GET /dossier/<repo>/<ID>`
  - `POST /api/questions/<id>/messages` with `{"kind": "followup"|"answer", "text": "...", "option": "A"}`.

  The server writes the owner line with `via: page` and mirrors it into the
  Slack thread.
- **Binding:** it binds 127.0.0.1 only.
- **Slack:** Socket Mode, over the server's own outgoing WebSocket.
  - **Credentials:**
    - the bot token comes from the keychain item `asd-slack-bot`;
    - the app token comes from `asd-slack-app`;
    - the owner's member ID is `$ASD_SLACK_USER`.
  - **Channels:** one private channel per repo, named after the repo
    (lower case, other characters as `-`; `<name>-pipeline` when the name is
    someone else's channel). The server creates it on first use, invites the
    owner, and posts that repo's questions and run threads there. The app
    needs the bot scopes `groups:write`, `groups:read`, `groups:history`,
    `channels:read` and the event `message.groups` for this. Without them it
    falls back to the DM and logs why once per repo.

    Without the tokens or the member ID the server runs without Slack and logs
    that once.
  - **What counts as what:**
    - a button press is an `answer` with that option;
    - every thread reply is a `followup` (the agent answers it and, when it
      reads as an answer, adds a button for it, as on the page);
    - a DM outside a question's thread goes to the **DM agent** (below);
      other messages in a repo channel are left alone;
    - `status`, `update` or `?` alone (or `status W-128`) in a repo channel or
      a run thread asks for a run status (below). With the DM agent off
      (`ASD_DM_AGENT=0`) it works in the DM too, and other DMs get a pointer.
  - **After an answer:** the question message is updated to show the answer,
    and gets a ✅ reaction.
  - **Connection:** the server reconnects on a `disconnect` envelope, a
    closed or errored socket, or, when `ASD_SLACK_IDLE_MS` is set, a socket
    silent that long (off by default: Slack sends no app-level heartbeat),
    with backoff. Exactly one socket is live: the old one is
    closed before a new one opens. Each envelope is acked first thing.
  - **Duplicates:** Slack redelivers on reconnects. A message
    (`channel:ts`) or button press (`message_ts:action_id:trigger_id`) seen in
    the last 10 minutes (2000 kept) is acked and dropped, logged as
    `slack duplicate`.
  - **Log:** every dashboard log line starts with an ISO UTC time.

## Dashboard

`shared/questions/runs.ts`, `stubs.ts`, `runstore.ts`, `timeline.ts`,
`controls.ts` and `events.ts`, rescanned every 30 s and cached; the page is
one card-based single-page app with deep links. The last runs and stubs views
are also kept in `~/.agentic/view-cache/` (`ASD_VIEW_CACHE_DIR`), so a
restarted server answers at once from them while its first scan runs.

- **Sources:**
  - candidate roots: the `cwd` of the newest transcript in each
    `~/.claude/projects/*/`, every `root` of a question, and `roots` in an
    optional `~/.agentic/repos.json`; each checkout's worktrees; only dirs
    with `.discovery/dossiers/*.md` count; per dossier id the copy with the
    longest `## Build log` wins;
  - transcripts whose `cwd` is the worktree or below (last 256 KB): session
    state, context size, request count;
  - `gh pr view` per running dossier (2 min cache, 10 s timeout);
  - phase names: the first heading of `references/work-on/phase-<n>.md`;
  - run updates `$ASD_RUNS_DIR/<repo>.<ID>.jsonl` (lines may carry
    `auto: true`) and runner state `<repo>.<ID>.runner.json`
    (`references/runner.md`; a dead pid reads as stopped), re-read on every
    request;
  - stubs: per repo main checkout `git ls-remote --heads origin 'stub/*'
    'release/*'`; per branch `gh pr list --head <b> --base development`.
    **A stub shows only the dossiers implemented on it**: the first-parent
    merges `origin/development..origin/<b>` of the local refs (the dashboard
    fetches the `stub/*`, `release/*` and `development` remote-tracking refs
    of each main checkout itself, every `ASD_STUB_FETCH_SECS`, default 180,
    0 = off; nothing else moves), mapped by PR
    number to the dossiers' `pr:` field (or by a fix-branch name). An open PR
    into the stub does not count. State: `accepting`, `integrating` (stage =
    last `## Step N`/`Round` heading of `.discovery/analysis/stub-*.md`),
    `merged`. Cached 2 min, failures omitted;
  - open work: `scripts/generate_open_work.py` (see the code), served at
    `/open-work/<repo>`;
  - **the Merge button** (stub card rows in "PR open · waiting for your merge",
    and the dossier header): `GET /api/runs/<repo>/<ID>/merge` is the dry run
    the confirm shows ("would merge #427, #429 into stub/… as one stack");
    `POST` puts the dossier in its repository's **merge queue**
    (`merge_dossier.py --enqueue`, `<repo>.merge-queue.json`, first in, first
    out; `{cancel: true}` leaves it). One worker per repository works the queue
    to the end, one dossier at a time: it waits for the dossier's own run, asks
    GitHub whether its PRs conflict, and if they do starts the dossier's agent
    on `/resolve-merge` (merge the stub into each branch, keep both sides,
    check no hunk was lost, prove it, push; never rebase or force-push) and
    waits for it; then merges by script, then runs `/after-merge` to its end,
    then takes the next. A dossier that still conflicts, or whose run fails,
    is marked failed with why and the queue goes on. Chips: in the merge queue
    · #n, merge waits for its run, resolving merge conflicts, merging…, merged
    · cleaning up. No agent merges: the script finds every open PR whose head
    names the id, refuses unless they are one chain whose bottom goes into an
    open stub, none is a draft and no run of the dossier is live, links them
    (`gh stack link`), merges the stack (`gh stack merge <top> --yes --merge`,
    all or nothing; one PR: `gh pr merge --merge`), checks each is MERGED,
    marks them started for the detector and starts `/after-merge`. State:
    `<key>.merge.json` (merging, merged, failed and why);
  - **Integrate a stub** (the open stub's card and its page): **Integrate**
    runs `merge_dossier.py --integrate <stub>` in the background. First it
    opens the stub's PR into development, which **closes the stub at once**:
    the next build cuts or takes the next stub. Part 1: every
    finished dossier on the stub at that moment (a ready PR, no live run; the
    merge queue lets only these into a closed stub) goes through the
    merge queue, first in first out, conflicts to its own agent, cleanup after
    each. Part 2, once all merged: `/stub` on the runner (key
    `<repo>.<stub slug>`: the full stack run, the audit, the stub's PR into
    development). A dossier still in flight (a draft PR or a live run) is left
    out; once the stub is in development, the merge queue moves its PR to the
    accepting stub (one that already holds the integrated batch; it cuts
    `stub/<today>` from development when none exists) and merges it there.
    Before that it waits, so its PR never carries the old batch. The stub page
    shows the run like a dossier's: state, Stop, Resume /stub, the Now panel,
    and its timeline with a "Merge the finished dossiers" section, then the
    /stub steps. The whole stub card opens that page. An integration that
    failed offers **Integrate** again on the card and the page; nothing offers
    it while the merges or the /stub run are going.
  - **One merge queue for both kinds of PR** (`merge_dossier.py`): a
    dossier's stack into its stub, and a plan or docs PR into development
    (`--enqueue-pr`). FIFO per repository, one worker. Each item goes through
    the same steps, cheapest first: GitHub's mergeability asked again while it
    says UNKNOWN; a dossier id or ADR number the base already holds renumbered
    on the branch by script (`--finalize-ids` for ADRs), pushed; red checks
    re-run once; then the dossier's agent (`/resolve-merge`, a fresh run under
    the dossier's key) for a content conflict or a check still red; then the
    script merges. The agent's push can turn up the next failure, so one attempt
    gets up to three agent rounds. A migration whose number the base or the stack
    already has goes to the agent too, before any check is waited for: a fresh
    database could not migrate, and CI without a database cannot see it. A stack
    also runs `/after-merge`; a non-code PR does not, and
    nothing in it touches a stub. An item that still cannot merge is marked
    failed with why, and the queue goes on.
  - **Non-code PRs into development** (repo page, under the stubs): every
    open `plan/*` or `docs/*` PR into `development`, the PRs that may skip the
    stub, each with why it can or cannot merge now (`scripts/merge_docs_prs.py
    list`): code files in it, a draft, a conflict, red or running checks, or a
    dossier id that another open PR or `development` already has (two
    concurrent plans can mint one number; renumber the later one with
    `validate_pipeline.py --finalize-ids`). **Merge all** puts every open
    non-draft one in the merge queue above, oldest first; each row shows its
    queue step (`GET`/`POST /api/repo/<repo>/doc-prs[/merge]`). Non-code PRs
    waiting in a row go through as one batch: one `plan/queue-batch-*` PR, one
    CI run, one merge. A member that conflicts or a batch that stays red goes
    one PR at a time (`ASD_MERGE_BATCH=0` always does). An outage or a GitHub
    5xx is waited out for as long as it lasts, and a queue whose worker died (a
    logout, a reboot) restarts within two minutes of the dashboard running.
  - **the merge detector** (`merges.ts`): every 2 min, `gh pr list --base <stub>
    --state merged` per open stub. A PR merged since the last look, mapped to
    its dossier by `pr:` number or the id in its head branch, starts
    `run_dossier.py <ID> --command after-merge --items <PRs>` once (sonnet);
    a dossier with a live runner waits for the next poll. The first look only
    records a baseline in `<repo>.merges.json`. Only a main checkout counts
    (a worktree is skipped). `ASD_MERGE_DETECTOR=0` turns it off.
- **Pages (all deep links):**
  - `/` Home: one card per repo (the badge: dossiers in progress or on the
    oldest open stub, each once; open questions; that stub's state), and directly under them the **queue of
    open questions** across all repos, oldest waiting first. Each is a compact
    card: repo and dossier chips (the dossier chip links to the dossier), the
    question, how long it has waited, what it blocks, and the answer controls
    inline (options with consequences, the recommendation highlighted, the
    follow-up box). With none it shows one muted line "No open questions."
    There is no Open questions card. Header with Slack status and the last
    update;
  - **Repo label:** a root that is not a git checkout (it only holds
    `.discovery/dossiers`, like `~/Documents/Twipe/repos`) is labelled by its
    path relative to the home directory ("Twipe/repos") wherever a repo name
    is shown. The basename stays the identity in routes and keys;
  - `GET /questions`: the same queue at full width, every open question with options and consequences, the
    recommendation highlighted, thread, follow-up and answer, each linking to
    its dossier (`GET /questions#q=<id>` scrolls to one; old `/#<id>` links
    redirect);
  - `/repo/<repo>`: under the repo's **open questions** (inline answer
    controls, shown only when any are open), two full-width sections, one
    under the other: first the **stubs**, then **Open work**, the
    complete open-work report in a same-origin iframe sized to its content (no
    inner scrollbar; re-sized on load, on content change and on report
    refresh). It is generated with `--embed --link-base /repo/<repo>
    --simplify-default`. Embedded, the report drops the suggested next
    action, the count tiles and the pipeline bar, and draws the dependency
    graph left to right as one layered drawing: each independent flow laid
    out alone and stacked, a dossier with no dependency link at all as a chip
    in an "Independent" row above it, no Start boxes, a root placed next to
    its first dependent, edges orthogonal with rounded corners in per-source
    lanes between the columns (edges from one dossier share a trunk), and on
    hover the whole chain before and after a dossier lit while the rest
    dims. Only the done frontier is drawn: no edge between two finished
    dossiers, a finished prerequisite dropped when a newer finished one
    already implies it, so done work is one thin layer in front of the open
    graph; the Simplify toggle is hidden. The dashboard is always dark. A
    drawing wider than the column scrolls inside its panel. A ready dossier
    whose blockers are all done carries a ▶ (graph box, Independent chip,
    flight-plan row): two clicks POST `/api/runs/<repo>/<ID>/start`, which
    re-checks eligibility and starts `run_dossier.py` in the background. The
    server passes each dossier's live control (`--run-controls`), so a dossier
    whose run stopped or failed shows ↻ (resume) instead, and the report is
    regenerated when a runner starts or stops. The
    stubs section also lists, under "Deferred on merged work", the dossiers with
    open deferred issues that no open stub card shows (their stub merged); a
    dossier on an open card shows its deferred count on its row instead. A dossier's
    page shows its **Deferred issues**, parsed from the ledger in its build log
    (`- D-n <kind> <title> — <path:line> — <risk> — <why> — contract: <c> — effort: <e>`;
    the report's summary table supersedes the line's contract and effort, and
    lines from before the two fields existed show them only from the report). Each item reads
    "fixed in stacked PR" (`DEFERRED-FIXED:`), "→ dossier W-…"
    (`DEFERRED-DOSSIER: D-n → <ID>`, written by `/plan`), "own dossier · not
    planned yet" with **Plan it** (starts `run_dossier.py <ID> --command plan
    --items D-n`: a `/plan <ID> D-n` session of its own, `<repo>.<ID>.plan-D-n`)
    and a Copy button for the command (`DEFERRED-TRIAGE: D-n → dossier`: routed,
    no dossier written yet), "being planned" while that run is live, then "→
    dossier W-…" from the new dossier's `DEFERRED-SOURCE: <ID> D-n` line; an item
    that is the same issue as another dossier's (its seed, or `DEFERRED-SAME`)
    follows that one, "deprecated" (`DEFERRED-DROPPED:`) or
    "not tackled". The timeline is one tab per run: **Implement dossier** (the `/work-on`
    phases) and one per later run (**Fix deferred issues**, **After the
    merge**), grouped by that command's own steps (`Step <n>` in the
    checkpoint name, else its words); the Now panel follows the newest. Every dossier page has the tabs Dossier (finished or not
    started), Timeline, Build log and Deferred, each shown when it has content. Each
    item has a Plan button that copies `/plan <ID> D-n` (ledgers of dossiers last
    updated before 2026-11-03 and never run by the runner are ignored,
    `ASD_DEFERRED_SINCE`), the runner's full
    report is linked at `/deferred/<repo>/<ID>`, and the header has **Fix all
    deferred** on a `pr` or `done` dossier with open items: two clicks start
    `run_dossier.py <ID> --command fix-deferred` in the background. The
    Deferred tab lists one card per item (ID, kind, state, title, location,
    risk, contract and effort from the report's summary table, the reason it
    was deferred); a card still to tackle is selected by clicking it, and a
    sticky bar above the cards has Select all and the selection count. **Fix selected (n)**, two
    clicks, POSTs `{items: [...]}` to `/api/runs/<repo>/<ID>/fix-deferred`,
    which starts `--command fix-deferred --items D-1,D-3`: only those items,
    one stacked PR through the implementer and review flow; an item that looks
    too big for it is put to the owner as a question (own dossier, fix here
    anyway, or leave open). **A later run on a finished dossier** (`/fix-deferred`) opens its own
    timeline section, "Fix deferred issues", at the runner's `start` line,
    and its steps stay inside it; while it runs the header reads "Fixing
    deferred issues", the page opens on the timeline, and the dossier's row on
    its stub card carries a live "fixing deferred" chip. **Deferred fix progress** (dossier page, under the last reply): once
    `/fix-deferred` has run or runs, a bar and one row per item: fixed (with
    the stacked PR), being fixed (`DEFERRED-TRIAGE: D-n → direct` without its
    `DEFERRED-FIXED:`), own dossier, dropped, left open; the stacked PR or the
    fix branch (`DEFERRED-FIX:`), the questions it waits on, and its latest
    note while live. The Deferred tab's cards carry the same states.
    **Blocked by the safety check** (top of the dossier page): every call auto
    mode refused in the current stage process that is not ruled on yet
    (`readBlocks`), with the exact command and the reason. A refusal from an
    earlier process (before the latest `stage N start`, restart or resume) is
    not open: nobody waits on it any more, and a stage that still needs the
    call hits it again. **Allow once** and **Deny** answer the agent's newest open
    question with a ruling that names the exact command; **Always allow in this
    repo** saves the rule in the box (default `Bash(<command>)`, widen it to a
    prefix; `checkRule` refuses `Bash(*)` and whole interpreters) to the repo's
    `.claude/settings.local.json` `permissions.allow`, then answers the same
    way. While the run is live it also leaves `<key>.restart`: when the ruling
    reaches the runner, it restarts the stage (a new process loads the rule)
    and hands the answer over with the checkpoints, instead of sending it into
    the old process. A stage that ends first clears the request. Each
    ruling is a `block-ruling` update. POST
    `/api/runs/<repo>/<ID>/blocks/<tool_use_id>` `{action: once|always|deny, rule}`.
    **The product summary** (`<key>.summary.md`, written by the runner after
    Phase 9) sits above the dossier on its Dossier tab; a built dossier without
    one offers **Write product summary**, which POSTs
    `/api/runs/<repo>/<ID>/summary` and runs `run_dossier.py summary <ID>` in
    the background. **A finished run's last reply** is the first thing
    on the dossier page (the runner's `result` line; for older runs the agent's
    last note before its final stage ended), with the owner's chat under it: a
    message POSTs to `/api/runs/<repo>/<ID>/chat`, which runs
    `run_dossier.py chat <ID> --text …`, resuming that session on its account;
    the reply appears as it lands. **A finished build's dossier** is read from
    its build branch (the id in the branch name, any case; local or `origin/`)
    when that copy's build log is longer, since `/work-on` removes the worktree
    and the checked-out branch has the pre-build copy until the PR merges; the
    open-work report overlays the same copy. A done dossier stays under its
    stub while its PR into the stub is open. A click on a dossier
    navigates the top window to `/repo/<repo>/<ID>`. **The stubs section**
    (above Open work): every open (not merged) stub, newest first, as a card:
    branch, state chip, step, PR + CI dot; under it a tree of its dossiers,
    as grouped rows (In progress, PR open · waiting for your merge, Merged into the stub; the merged group folds when it has more than six), each row the id, title and where it stands: first the in-progress ones (phase n · name, a live dot while a runner or
    session is active, an open-question badge, a "no PR yet" chip where it
    applies), then the done ones (merged on the stub). A sub-item opens that
    dossier's view, the card opens the stub view. Running dossiers that belong
    to no stub form a small "In progress" list above the stubs; a collapsed
    "Merged stubs" list closes the section. `/repo/<repo>/graph` redirects here.
    **Which in-progress dossiers sit under a stub** (`stubMembers`): one whose
    open PR targets that stub branch (`gh pr list --base <branch> --state open
    --json number,headRefName`, cached 2 min, 10 s timeout; matched by the
    dossier's `pr:` number or by its id in the head branch, repo-scoped;
    `plan/...` heads never count); plus a dossier in `building`/`review` (or
    with a live runner) that has no PR yet, under the **accepting** stub (the
    newest open stub with no PR into development, else the newest open stub).
    Ready and planned dossiers never appear under a stub. The data is
    `members` on each stub in `/api/stubs`;
  - `/repo/<repo>/<ID>`: not started or done shows the full dossier with its
    answered questions; in progress, or with any run updates (not done), shows
    the **run view**. Both headers carry **Start** (ready, every `blocked_by`
    done; else "blocked by ..."), **Resume** (runner stopped or failed, not
    done) and **Stop** (running or waiting) with the two-step confirm.
    `/repo/<repo>/<ID>/dossier` forces the dossier. The run view has three
    views of one run (tabs; **Timeline** opens by default):
    - **Timeline** (default): a **Now** panel (**Waiting on you** first, with
      the answer controls, then the phase, time in it, the newest note and the
      runner state), then the run grouped by phase, newest phase first. It
      shows only what a person wants to read: the agent's own notes (markdown,
      rendered: bullets and tables), its questions with their threads and
      answer controls, one line per spawned agent with its status chip and
      duration, a pull request opened or merged, and a stage that failed or a
      run that stopped or finished. No commands, gates or suite lines, build-
      log copies, checkpoint text, or runner bookkeeping such as "waiting on
      Q-1" (the question itself is shown). With nothing to show it says "No
      updates yet";
    - **Build log**: the dossier's `## Build log` lines. A `planned` dossier
      whose updates file has lines labels the timeline "Planning";
  - `/repo/<repo>/stub/<branch>`: header (cut, state, step, PR + CI,
    implemented dossiers as cards) with the same split: **Timeline**
    (Now panel and the filtered updates and questions grouped by the `## Step
    N` headings of the record file `stub-*.md`, under key `<repo>.<branch with
    / as ->`) and **Record** (each `##`/`###` heading of the record file as an entry,
    dated by `(YYYY-MM-DD)` in it else by position).
  - An id is unique only within a repo: dossiers are picked per main checkout,
    and every lookup (runs, questions, stubs) is scoped by repo.
- **Run controls** (POST, same Host/Origin checks; the button turns into
  "Confirm ..." first, never a browser dialog). The command's one-line output
  or error is shown on the card, with a link to the runner log:
  - `POST /api/runs/<repo>/<ID>/start`: status `ready` and every id in
    `blocked_by` is `done` (else "blocked by W-..."); runs
    `python3 <plugin root>/scripts/run_dossier.py <ID> --root <main checkout>`;
  - `POST .../resume`: not done, runner `stopped` or `failed`; same command
    with `--root` = the `root` recorded in `<key>.runner.json`;
  - `POST .../stop`: runner running or waiting; `run_dossier.py stop <ID>
    --root <root>`;
  - `GET /api/runs/<repo>/<ID>/log`: the last 200 lines of `<key>.runner.log`.
- **Live updates:** `GET /api/events` is a Server-Sent Events stream that
  watches the questions dir and `$ASD_RUNS_DIR` and sends
  `event: changed` with `{"what": [...]}` (`questions`, `runs`, `stubs`,
  `scan`) on a change and after each background rescan. The page re-fetches
  only what changed and patches the DOM, so scroll, open expanders and typed
  text stay; a 60 s poll runs only while the stream is down.
- **Slack progress thread** (server side, from the updates store; off with
  `ASD_RUN_SLACK=0`): the first `runner` start line of a run opens one message in the repo's channel
  message "▶ <repo> · <ID> — <title>"; each stage-done, waiting, answered,
  failed and done line becomes a reply in that thread with a link to the run
  view; `auto` lines never post. The thread ts and the lines already seen are
  kept in `$ASD_RUNS_DIR/<key>.slack.json`; a restart posts no backlog.
  The thread opener carries a **Status** button for that run.
- **Status on demand** (`runstatus.ts`): `status` in a repo channel lists
  that repo's running, waiting and paused dossiers; in a run thread, that
  run; in the DM, every running dossier; `status W-128` names one. The reply
  goes in a thread under the asking message. Per run: state, phase, account,
  age; sub-agents of the current stage (running, finished, finished with a
  gap or contract change); a wait for agent slots; last activity (⚠ quiet
  after 20 min) and context size; the agent's last human note; open
  questions. A **↻ Refresh** button rewrites the message in place with the
  same scope. No Slack app change is needed: the message and interactivity
  events are the ones questions already use.
- **The DM agent** (`dmagent.ts`, on unless `ASD_DM_AGENT=0`): a top-level
  DM starts a `claude -p` session (`ASD_DM_AGENT_MODEL`, default `sonnet`) on
  the account the next stage would use, with `references/dm-agent.md` as its
  appended prompt, the plugin root as cwd, the repos' main checkouts and
  `~/.agentic` as added dirs, and only read tools plus the runs MCP server
  allowed (`--permission-mode dontAsk`). A reply in that message's thread
  resumes the same session (`--resume`); turns in one thread run one at a
  time. A "_Looking into it…_" reply in the thread is replaced by the answer.
  Sessions per thread: `~/.agentic/dm-agent.json` (`ASD_DM_AGENT_FILE`);
  timeout `ASD_DM_AGENT_TIMEOUT_MS` (10 min). The prompt references the docs
  rather than restating them; a stub's progress comes from the MCP tool
  `stub_detail`.
- **The dashboard chat** (`pagechat.ts`, the page `GET /chat`, the header's "Ask the
  agent"): the same agent as the DM, with the dashboard as its channel. A new
  conversation is a new session, like a top-level DM; a message in a
  conversation resumes its session, like a thread reply, and its turns run one
  at a time. The pending "The agent is answering…" line is replaced by the
  rendered answer; the page follows over the event stream (`chat`).
  Cmd/Ctrl+Enter sends. One file per conversation in `~/.agentic/chat/<id>.json`
  (`ASD_CHAT_DIR`); its session sits in the DM agent's store under
  `page:<id>`. API: `GET /api/chat` (`{enabled, chats}`), `GET /api/chat/<id>`
  (messages with rendered `html`), `POST /api/chat {text, id?}` → `{ok, id}`
  (503 when `ASD_DM_AGENT=0`, 404 for an unknown conversation).
- **Other API:** `GET /api/runs`, `/api/stubs`, `/api/questions`,
  `/api/timeline/<repo>/<ID>` (entries, `groups`, `now`, `log`), `/api/dossier/<repo>/<ID>`,
  `/api/stub?repo=&branch=`, and `GET /dossier/<repo>/<ID>` (read-only,
  escaped HTML).
- **Env overrides:** `ASD_RUNS_ROOTS`, `ASD_CLAUDE_PROJECTS`,
  `ASD_RUNS_NO_GH=1`, `ASD_STUBS_NO_GIT=1`, `ASD_RUNS_DIR`,
  `ASD_OPEN_WORK_DIR`, `ASD_RUNNER_BIN` (replaces the runner script path).
