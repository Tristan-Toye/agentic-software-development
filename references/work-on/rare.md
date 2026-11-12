<!-- /work-on, rare cases. Read one section with ref_section when the case arises; the
     rules that hold in every stage stay in primary-agents/work-on.md. -->

# /work-on — rare cases

### Asking the owner — mechanics

- **A question only the owner can settle goes through `ask_owner.py`.** Where
  this file says to ask the user or stop for a ruling, first apply the ask
  policy (`references/owner-questions.md`, "The ask policy"); when it is a
  question, run `scripts/ask_owner.py ask --kind design|product|uncertainty --build-log <live dossier>`, as `references/owner-questions.md`
  ("Asking, in a run") says: every option with its consequence, your
  recommendation, and what it blocks. Keep working on what is not blocked.
  When nothing else can proceed, run `ask_owner.py wait <id>` as a background
  command under Claude Code and end the turn (`wait <id> --build-log <live dossier>`).
  `ask --build-log` logs `QUESTION:` and `wait --build-log` logs `OWNER-RULING (<date>):` in `## Build log`; a follow-up you accept as the ruling
  goes in with `accept --build-log`. Never pick an option of a question you asked.

### Resuming an agent from its own transcript

- **An agent resumes from its own transcript, never from the file on disk.**
  If you edited a file an agent owns, never describe its current content back
  to it; state the delta — "the file on disk now differs from what you wrote;
  here is what changed and why". A false statement about an agent's own
  output is the one instruction a good agent must refuse and a weak one will
  satisfy by fabricating. When you may still resume the owner, prefer sending
  the change as an instruction over editing its file yourself.
