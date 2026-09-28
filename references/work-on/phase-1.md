<!-- /work-on, Phase 1. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 1 — Jira: one ticket, lightly

Use the Atlassian Rovo tools. Get the `cloudId` once with
`getAccessibleAtlassianResources`.

- `jira` set in the front matter → `getJiraIssue` to check it, and show its
  status.
- Not set → `searchJiraIssuesUsingJql` for the title, and link a match on the
  user's confirmation. No match → propose one issue (type, summary, description
  from `## Problem`) and create it **only on an explicit yes**. Write the key to
  the front matter.

One ticket. No subtask, no per-phase transition, no time comment — Tempo holds
the time. You comment on the ticket exactly one time, when the PR opens.
