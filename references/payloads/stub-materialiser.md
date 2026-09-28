### stub-materialiser

```
WORKTREE_DIR: /abs/path/repo-W-014
CONTRACT: |
  <verbatim — byte-identical to every other payload this fan-out>
OWNED_PATHS: src/flush.py
STUB_STYLE: |
  unimplemented!()      # the repo's own placeholder, verified against the repo
BUILD_CHECK: cargo check
```

## Field rules that matter

- **`STUB_STYLE` and `BUILD_CHECK` are verified against the repo before the
  spawn.** The placeholder must be the repo's own, and the compile command
  must run in `WORKTREE_DIR`; a stub spawn with an invented style produces
  stubs the fan-out cannot build on.
