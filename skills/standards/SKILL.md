---
name: engineering-standards
description: >-
  Organizational coding standards. Consult whenever writing, modifying,
  reviewing, or refactoring code in any language — before implementing a
  feature, fixing a bug, adding tests, or assessing code quality — in ANY
  session, whether or not a deep-discovery command is running. Also
  consult when the user asks what the standards are or whether code
  conforms.
---

# Engineering standards

[engineering-standards.md](engineering-standards.md) is the whole
cross-repo standard: priority → CR severity, how the standards apply, the
citable rule index, three hard-won rules, security, and C#/backend
conventions.

Apply them as constraints, not suggestions, and cite the ID (`TCG 1002`,
`TCG 3004`) whenever one drives a choice or a change request.

**A repo's own rules outrank this file.** `/work-on` Phase 8 extracts what
a build decided into the repo it ran in — ADRs in `docs/adr/`, and standing
rules in that repo's rules file. Read those too, and on divergence the
repo wins: it knows its own language, toolchain, and history. Nothing is
promoted back here without the user asking for it.
