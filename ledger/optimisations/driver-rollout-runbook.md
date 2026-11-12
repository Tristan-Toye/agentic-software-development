# Driver rollout runbook (shadow, then A/B), for the next weekly stub

Plan and rules: `mechanical-phases-as-workflow.md` (sections 4 and 5). Everything below needs no further
building: the switch is `scripts/driver_modes.py`, the evidence reader is `scripts/driver_shadow_report.py`,
the A/B reader is `scripts/driver_ab_report.py`. All five drivers are `off` until step (c); nothing runs
unless the owner agrees to enable it. Ask before step (c).

Run every command from the main checkout of the repo being measured (`<repo>` below), with the plugin's
`scripts/` path (`S=<plugin scripts dir>`). Evidence lives in `<repo>/.git/asd-drivers/` (never tracked).

## (a) Pre-flight

1. The deployed plugin contains the rollout kit. The installed commit must have this PR's merge commit as an
   ancestor:
   `python3 -c "import json,os;[print(e.get('gitCommitSha')) for e in json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))['plugins']['agentic-software-development@agentic-software-development']]"`
   then `git -C <plugin repo> merge-base --is-ancestor <PR merge sha> <that sha> && echo deployed`.
   Not deployed: stop; sessions started now would not know the shadow paragraph of the phase docs.
2. `python3 $S/driver_modes.py show` prints five drivers, all `off`, and `drivers file: ... (absent: every driver off)`.
   Anything else was set by someone: find out who before going on.
3. No dossier you want in the baseline is mid-run under a changed mode. A runner reads the drivers file when it
   starts (and again after a stage-boundary re-exec or a revive), so change the file only at a quiet moment.
   Runs started before the change keep the modes in their runner metadata until their runner restarts.
4. `python3 $S/driver_shadow_report.py` prints NO-DATA for all five (a clean slate), or you know why not.

## (b) Which drivers go to shadow first

All five can go to shadow at once: shadow never decides, it runs the driver after the orchestrator's manual
steps and the orchestrator ignores its output. Cost: one extra script call per phase. Two exceptions to think about:

- `test_round` in shadow re-runs the whole suite (gates plus suite) after the orchestrator's own round. That costs
  wall time and, when the suite uses the VM, holds the VM stack lease a second time, so it contends with other runs
  waiting on the lease. Shadow it only while the suite is cheap or the stub is quiet; otherwise shadow it last,
  after the other four have their three dossiers.
- `finalize_pr` and `merge_packages` work in throwaway worktrees (disk): fine with the disk guard's usual floor.

Recommended: first batch `finalize_pr, merge_packages, pre_fanout_check, strength_gate_prep`; `test_round` joins
when a stack lease is free to spare, or from the second stub week.

## (c) Enable shadow

    python3 $S/driver_modes.py set finalize_pr=shadow merge_packages=shadow pre_fanout_check=shadow strength_gate_prep=shadow
    python3 $S/driver_modes.py set test_round=shadow        # when (b) allows
    python3 $S/driver_modes.py show

This writes `~/.agentic/drivers.json` atomically. No LaunchAgent edit, no restart: `run_dossier.py` exports
`ASD_DRIVER_<NAME>` into the claude child of every run it starts afterwards, records the modes in the run's
`<key>.runner.json` (`drivers`), and warns in the runner log about an unknown name or mode. An explicit
`ASD_DRIVER_*` in the runner's own environment would win over the file.

## (d) Pick the three shadow dossiers

Any three dossiers that start after (c) and reach the phases the driver covers: the first three to finish Phase 9
give `finalize_pr` evidence; `merge_packages`, `strength_gate_prep` and `test_round` need dossiers with at least
two packages and a failing suite round to say anything about failures (a green single-package dossier is valid
evidence but a weak one). Prefer different repos over three of one. Do not hand-pick easy ones and do not skip a
dossier because it looks messy: the report counts what ran. Check each run did its shadow call:
`ls <repo>/.git/asd-drivers/driver-shadow/<ID>/`. A dossier whose orchestrator never ran the driver shows no
file: that is a phase-doc compliance gap, noted in the decision log, not a pass.

## (e) Read the shadow report

    python3 $S/driver_shadow_report.py                 # every dossier with shadow evidence
    python3 $S/driver_shadow_report.py W-201 W-202 --driver test_round --json

Per driver and dossier it prints the shadow runs and exit codes (2 is a surprise: the driver met something it could
not classify), the driver's findings, the orchestrator's build-log lines for that phase, and an agreement line.
Agreement is only computed where it is well defined (finalize_pr: conflict markers / duplicate ADR vs a logged fix;
merge_packages: TOUCHED_BEYOND vs a scope/drift line; test_round: failed count vs the `SUITE:` lines); everything
else says `needs human read`, and you read the findings next to the build log yourself.

Verdict per driver:

- `READY-FOR-ON`: at least 3 dossiers with shadow runs, no exit 2, no disagreement. `needs human read` pairs do not
  block it but are read before the switch.
- `NOT-YET (reason)`: fewer than 3 dossiers, a surprise exit, or a disagreement. A disagreement is read, not
  averaged: either the driver is wrong (fix it, restart the count) or the orchestrator skipped a check by hand
  (a finding for the phase docs, count continues).
- `NO-DATA`: no shadow files.

## (f) Switch a driver to on

Only a driver with `READY-FOR-ON`, and only with the owner's yes:

    python3 $S/driver_modes.py set merge_packages=on

`on` means the orchestrator acts on the driver's output and the driver writes the one-line `DRIVER-...` build-log
summary; evidence moves to `driver-evidence/`. Enable the drivers READY at the same time in one command; if the A/B
(g) turns bad with several on, go back to shadow for all of them and bisect, one at a time.

## (g) The A/B

Arm B: the next 6 dossiers that start after (f). Arm A (baseline): 6 dossiers from history, built since
2026-11-09 (before any driver ran), matched to arm B by repo and package count (same repo, package count within
one). List candidates with their package counts:

    python3 - <<'EOF'
    import glob, os, sys
    sys.path.insert(0, os.environ["S"]); import merge_packages as mp
    for f in sorted(glob.glob(".discovery/dossiers/*.md")):
        try: print(os.path.basename(f)[:-3], len(mp.parse_package_table(open(f).read())))
        except Exception as e: print(os.path.basename(f)[:-3], "?", e)
    EOF

Cost and requests need the budget file; the windows below cover both arms:

    python3 $S/budget_report.py --since 2026-11-09 --json /tmp/budget-ab.json
    python3 $S/driver_ab_report.py \
        --baseline W-A1,W-A2,W-A3,W-A4,W-A5,W-A6 \
        --driver merge_packages=W-B1,W-B2,W-B3,W-B4,W-B5,W-B6 \
        --driver finalize_pr=W-B1,W-B2,W-B3,W-B4,W-B5,W-B6 \
        --budget /tmp/budget-ab.json --json /tmp/driver-ab.json

(one `--driver NAME=IDS` per driver that was `on`; IDS in build order; `--questions-dir` defaults to the owner
question store.) Which arm a dossier ran in is in its runner metadata:
`jq .drivers ~/.agentic/runs/<repo>.<ID>.runner.json`. A dossier whose `drivers` differs from the arm it is
listed in does not count: replace it.

Verdict per driver, from the script: a dossier is worse when any measured quality counter (defects, gaps,
contract changes, red CI, owner questions) is above the baseline median.

- `KEEP`: no two worse dossiers in a row. Keep it on. Requests and cost are read next to the baseline median; the
  plan's bar (cache-read in the driver's phases down 20%) needs
  `ledger/optimisations/scripts/measure_orchestrator_phases.py --since <date>` per phase, run once at the end.
- `BACK-TO-SHADOW`: two worse dossiers in a row. `driver_modes.py set NAME=shadow`, note it in the log, find the
  mechanism before the next try. The count of shadow dossiers restarts only if the driver changed.

Cheap stop rule while the A/B runs: look at `driver_ab_report.py` after every dossier of arm B; do not wait for six
when two in a row are already worse.

## (h) Rollback

    python3 $S/driver_modes.py off            # every driver off
    python3 $S/driver_modes.py set NAME=off   # one driver

Takes effect for the next run (and the next stage of a run whose runner restarts). The phase docs revert to the
manual steps by themselves because the paragraphs apply only for `on` or `shadow`. Evidence files stay.

## (i) Decision log

One row per decision (shadow start, READY-FOR-ON, switch to on, KEEP, BACK-TO-SHADOW, rollback). Fill in, commit.

| Date | Driver | Mode | Dossiers | Verdict |
|------|--------|------|----------|---------|
|      |        |      |          |         |
