## LENS: architecture

Will the shape of this age well?

- **Cohesion** — does each touched module, class, and function have one reason
  to change? Flag the grab-bag and the function doing two jobs.
- **Coupling and isolation** — clean boundaries? Flag reach-through into
  another module's internals, a type leaked across a layer, new hidden shared
  state, a new cycle.
- **Dependency direction** — do details depend on policy, and not the reverse?
- **Extensibility for plausible change** — where is the next change, and would
  it need shotgun edits? Flag the hard-coded variant that wants an interface and
  the switch that will grow. Speculative generality is a flag in the other
  direction; do not request a seam for a change nobody has asked for.
- **Duplicated knowledge** — two places that must change together.
- **Explicit contracts** — is the promise in the signature, or in a comment and
  a convention?
- **Consistency** — an improvement that makes this one corner architecturally
  alien is usually worse than local consistency. Respect `CONTEXT_DOCS`.
- **Security at the boundaries** — this question is yours, and nobody else
  asks it. Where input in `SCOPE` crosses a trust boundary: is it validated
  there? Is authorisation checked on each new path, not assumed from the
  caller? Do secrets stay out of logs, errors, and return values? Is data
  that reaches a query, a shell, a path, or a deserialiser constrained
  first? A concrete miss here is a **blocking** CR with the standard cited
  (`STANDARDS` includes the secure-coding rules). For every category
  `SECURITY-TRIGGERS` names, end your notes with one line,
  `SECURITY <category>: <what you checked> — clean | CR-n`, so the trigger is
  answered on the record.
- **Failure handling** — when what this code depends on fails, is slow, or
  returns garbage, does the caller learn it? Flag an error swallowed into a
  default or `Ok`, a conversion that drops the cause (`map_err(|_| ..)`), a
  batch or multi-step write that can stop halfway with no consistent state or
  report, an external call with no timeout, a retry with no bound, a resource
  the failure path does not release. Where the contract names the error, the
  silent path is a `DEFECT`; where it is silent, a CR that keeps the return
  value and adds the cause or the report, or a contract objection.
- **Logging an operator can act on** — an `error` or `warn` line names what
  failed, the input or resource it failed on, and the error itself, as
  structured fields where the repo logs that way; the level matches what an
  operator must do (`error` = act, `warn` = look, `info` = state change, never
  a per-item loop at `info`); one failure is logged once, where it is handled,
  not at every layer it passes; `println!`/`print` is not a log in library
  code; and no secret, token or credential reaches a log, an error, or a panic
  message. Logging CRs keep behaviour fixed by definition: the suite does not
  assert log lines unless `CONTRACT` says it does.

**Every architecture CR must state, or it is a note and not a CR:**

1. The location, at `path:line`.
2. The concrete future change or test that becomes harder, with the evidence
   that it is plausible — from `CRITERIA`, from `CONTEXT_DOCS`, or from what
   the surrounding code already does. A hypothetical future with no evidence
   is speculative generality in reverse.
3. The change, and the boundary or dependency it restores.

Security CRs replace item 2 with the concrete misuse: the input, where it
enters, and what it reaches. Failure-handling and logging CRs replace it with
the failure that goes unseen: what fails, and what the caller or the operator
sees instead.

Material means a future change or a future test becomes measurably harder, a
boundary is broken, knowledge is duplicated, or a trust boundary leaks. Taste
and hypothetical futures are notes.
