<!-- context7 -->
Use Context7 MCP to fetch current documentation whenever the user asks about a library, framework, SDK, API, CLI tool, or cloud service — even well-known ones like React, Next.js, Prisma, Express, Tailwind, Django. This includes API syntax, configuration, version migration, library-specific debugging, setup instructions, and CLI tool usage. Use even when you think you know the answer — your training data may not reflect recent changes. Prefer this over web search for library docs.

Do not use for: refactoring, writing scripts from scratch, debugging business logic, code review, or general programming concepts.

## Steps

1. Always start with `resolve-library-id` using the library name and what to look up in the library's documentation, unless the user provides an exact library ID in `/org/project` format
2. Pick the best match (ID format: `/org/project`) by: exact name match, description relevance, code snippet count, source reputation (High/Medium preferred), and benchmark score (higher is better). If results don't look right, try alternate names or queries (e.g., "next.js" not "nextjs", or rephrase the question). Use version-specific IDs when the user mentions a version
3. `query-docs` with the selected library ID and what to look up in the library's documentation (not single words), scoped to a single concept. If the question spans multiple distinct concepts (e.g., routing, auth, and caching), make a separate `query-docs` call per concept with the same library ID, unless the question is about how the concepts interact — combined queries dilute ranking and return shallow results for each topic
4. Answer using the fetched docs
<!-- context7 -->

<!-- github-stacks -->
## GitHub stacks

Whenever work is split into stacked pull requests, always create the true GitHub Stack object with the `gh stack` CLI (extension `github/gh-stack`). Never stack by chaining PR base branches alone — without the Stack object, GitHub shows no stack UI, no per-layer diffs, and no whole-stack merge.

- New work: `gh stack init <branch>` from the trunk, grow it with `gh stack add <branch>`, open it with `gh stack submit` (one PR per branch, linked as one Stack).
- Existing PRs: link them bottom-to-top with `gh stack link --base <trunk> <b1> <b2> ... <bN>`; open PRs are reused and mismatched bases are corrected automatically.
- After rebases or review changes: `gh stack sync` (or `gh stack rebase --continue` after conflicts) keeps every layer's base correct.
- Merge bottom-up, and only when the user asks — `gh stack merge` exists, but merging stays the user's decision.
<!-- github-stacks -->
