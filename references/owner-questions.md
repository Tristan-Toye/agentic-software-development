# Owner questions

How a run asks the owner a question it cannot settle itself, keeps working,
and waits only when it is blocked. The owner answers from a local page or from
a Slack DM thread, and can ask follow-ups first.

Only the orchestrator asks. A sub-agent returns `GAP:` or `CONTRACT-CHANGE:`,
and the orchestrator decides whether that needs the owner. A run never picks
an option itself, however long it waits.

## Parts

- `scripts/ask_owner.py`: the CLI the orchestrator runs: `ask`, `reply`,
  `wait`, `list`, `show`. It reads and writes the store directly, so it works
  with the server down; the server then only adds the notifications.
- `shared/questions/server.ts`: one Bun process, no dependencies. It watches
  the store, notifies (macOS notification, Slack DM), serves the page on
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
| `kind` | every line | `question` (first line only), `reply` (agent), `followup` or `answer` (owner) |
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
  - a Slack DM: the question, `blocks`, each option as a button and as a
    line with its consequence, the recommendation, and a link to the page.
- **New agent reply:** posted into the question's Slack thread.
- **Reminder:** an open question whose last line is an agent line older than
  2 h is notified again, at most once per 2 h.
- **Page** `GET /`: the open questions by dossier, then the answered ones.
  - Each question shows its thread, the option buttons (an answer), a text
    box with **Ask follow-up** and **Answer**.
  - It refreshes every 3 s.
- **API:**
  - `GET /api/questions?state=open|all`
  - `GET /api/questions/<id>`
  - `POST /api/questions/<id>/messages` with `{"kind": "followup"|"answer", "text": "...", "option": "A"}`.

  The server writes the owner line with `via: page` and mirrors it into the
  Slack thread.
- **Binding:** it binds 127.0.0.1 only.
- **Slack:** Socket Mode, over the server's own outgoing WebSocket.
  - **Credentials:**
    - the bot token comes from the keychain item `asd-slack-bot`;
    - the app token comes from `asd-slack-app`;
    - the member ID to DM is `$ASD_SLACK_USER`.

    Without the tokens or the member ID the server runs without Slack and logs
    that once.
  - **What counts as what:**
    - a button press is an `answer` with that option;
    - a thread reply starting with `answer:` is a free-text `answer`;
    - any other thread reply is a `followup`;
    - a DM outside a thread gets a pointer to reply in a question's thread.
  - **After an answer:** the question message is updated to show the answer,
    and gets a ✅ reaction.
  - **Connection:** the server reconnects on a `disconnect` envelope or a
    closed socket, with backoff.
