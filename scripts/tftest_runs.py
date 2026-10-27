#!/usr/bin/env python3
"""Judge each terraform test run block on its own.

`terraform test` stops judging after a failed `expect_failures` run: every run
after it reports skipped, so one file's red-run cannot be read from a failure
count. The recorded stub red-run reported "5 failed and 6 skipped" against 11
runs; splitting the file by hand — one run per test file in scratch copies of
the module — showed 10 red and 1 legitimately green. This script is that
split, without the hand work: for every `run` block in a `.tftest.hcl` file it
copies the module to a scratch dir, writes a test file that carries the file's
other top-level blocks (`mock_provider`, `variables`, ...) and that one run
block only, runs `terraform init -backend=false` and `terraform test` there,
and prints one verdict line per run.

# Usage

    tftest_runs.py FILE [--root DIR] [--scratch DIR] [--list] [--selftest]

`FILE` is the `.tftest.hcl` file; `--root` is the module directory it belongs
to (default: FILE's directory). `--list` prints the run blocks and the shared
blocks without running anything. Every verdict line:

    PASS <run name>
    FAIL <run name> — <first failing line of terraform test's output>

Exit codes: 0 every run judged and printed (PASS or FAIL — the verdict is the
line's, not the exit's); 1 nothing could be judged (no run blocks, a scratch
copy failed, terraform missing with runs to judge); 2 unusable input.

No third-party imports: this runs wherever python3 (and, to judge, terraform)
do.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

SKIP_COPY = (".git", ".terraform", ".agent-staging", ".discovery")
BLOCK_HEADER = re.compile(r'^\s*([A-Za-z_][\w-]*)\s*(?:"([^"]*)")?\s*\{')


def split_blocks(text: str) -> tuple[list[tuple[str, str, str, int]], str]:
    """Top-level HCL blocks as (kind, name, body-with-braces, header-line-no),
    plus the prose between them (comments) passed through."""
    blocks: list[tuple[str, str, str, int]] = []
    kept: list[str] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        header = BLOCK_HEADER.match(lines[i])
        if not header:
            kept.append(lines[i])
            i += 1
            continue
        depth = 0
        body: list[str] = []
        j = i
        while j < len(lines):
            body.append(lines[j])
            depth += lines[j].count("{") - lines[j].count("}")
            if depth == 0:
                break
            j += 1
        blocks.append((header.group(1), header.group(2) or "", "\n".join(body), i + 1))
        i = j + 1
    return blocks, "\n".join(kept)


def run_blocks(blocks) -> list[tuple[str, str]]:
    return [(name, body) for kind, name, body, _ in blocks if kind == "run" and name]


def shared_blocks(blocks) -> list[str]:
    return [body for kind, name, body, _ in blocks if kind != "run"]


def copy_module(root: str, scratch: str) -> str:
    dst = tempfile.mkdtemp(prefix="tftest-", dir=scratch)
    for entry in sorted(os.listdir(root)):
        if entry in SKIP_COPY:
            continue
        src = os.path.join(root, entry)
        target = os.path.join(dst, entry)
        if os.path.isdir(src):
            shutil.copytree(src, target)
        else:
            shutil.copy2(src, target)
    return dst


def terraform_available() -> bool:
    return shutil.which("terraform") is not None


def judge(
    name: str, run_body: str, shared: list[str], test_name: str, root: str, scratch: str
) -> tuple[bool, str]:
    module = copy_module(root, scratch)
    with open(os.path.join(module, test_name), "w", encoding="utf-8") as fh:
        fh.write("\n\n".join([*shared, run_body]) + "\n")
    init = subprocess.run(
        ["terraform", "init", "-backend=false"],
        cwd=module,
        capture_output=True,
        text=True,
    )
    if init.returncode != 0:
        return False, "terraform init failed: " + (
            init.stderr.strip().splitlines() or ["?"]
        )[-1][:120]
    test = subprocess.run(
        ["terraform", "test"], cwd=module, capture_output=True, text=True
    )
    out = (test.stdout + test.stderr).splitlines()
    first = next((l for l in out if "fail" in l.lower() or "error" in l.lower()), "")
    return test.returncode == 0, first[:120]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file", nargs="?", help="the .tftest.hcl file")
    ap.add_argument("--root", help="the module directory (default: the file's)")
    ap.add_argument("--scratch", help="where scratch copies go (default: a temp dir)")
    ap.add_argument(
        "--list", action="store_true", help="print the blocks, judge nothing"
    )
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.selftest:
        return selftest()
    if not args.file:
        ap.error("FILE is required unless --selftest")
    if not os.path.isfile(args.file):
        print(f"tftest_runs: no such file: {args.file}", file=sys.stderr)
        return 2
    root = os.path.abspath(args.root or os.path.dirname(args.file))
    with open(args.file, encoding="utf-8") as fh:
        blocks, _kept = split_blocks(fh.read())
    runs = run_blocks(blocks)
    shared = shared_blocks(blocks)
    if not runs:
        print("tftest_runs: no run blocks in %s" % args.file, file=sys.stderr)
        return 1
    if args.list:
        for body in shared:
            print("shared: %s" % BLOCK_HEADER.match(body.split("\n")[0]).group(1))
        for name, _body in runs:
            print("run: %s" % name)
        return 0
    if not terraform_available():
        print(
            "tftest_runs: terraform is not on PATH; nothing was judged", file=sys.stderr
        )
        return 1
    scratch = args.scratch or tempfile.mkdtemp(prefix="tftest-runs-")
    os.makedirs(scratch, exist_ok=True)
    test_name = os.path.basename(args.file)
    judged = 0
    for name, body in runs:
        ok, why = judge(name, body, shared, test_name, root, scratch)
        judged += 1
        if ok:
            print("PASS %s" % name)
        else:
            print("FAIL %s — %s" % (name, why))
    return 0 if judged == len(runs) else 1


# -------------------------------------------------------------------- selftest


def selftest() -> int:
    import tempfile

    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    sample = """// module-level comment
mock_provider "aws" {
  mock_data "plan" {
    data = { kind = "podcast" }
  }
}

variables {
  plan_count = 2
}

run "queues one plan" {
  command = plan
  assert {
    condition     = awscc_plan.this.count == 1
    error_message = "one plan"
  }
}

run "rejects an empty plan" {
  command = apply
  expect_failures = [awscc_plan.this]
}
"""
    blocks, kept = split_blocks(sample)
    runs = run_blocks(blocks)
    shared = shared_blocks(blocks)
    check("four top-level blocks are split", len(blocks) == 4)
    check(
        "two run blocks are found by name",
        [n for n, _ in runs] == ["queues one plan", "rejects an empty plan"],
    )
    check(
        "the mock_provider and variables blocks are shared, not run",
        len(shared) == 2 and all("run " not in b.split("\n")[0] for b in shared),
    )
    check(
        "a block body keeps its braces and nested assert block",
        "expect_failures = [awscc_plan.this]" in runs[1][1]
        and runs[1][1].startswith('run "rejects an empty plan" {')
        and runs[1][1].rstrip().endswith("}"),
    )
    check(
        "the module-level comment passes through",
        kept.strip().startswith("// module-level comment"),
    )
    composed = "\n\n".join([*shared, runs[0][1]]) + "\n"
    check(
        "a composed scratch file carries every shared block and one run",
        composed.count("mock_provider") == 1
        and composed.count("variables") == 1
        and composed.count('run "') == 1,
    )

    with tempfile.TemporaryDirectory() as tmp:
        module = os.path.join(tmp, "module")
        os.makedirs(module)
        with open(os.path.join(module, "main.tf"), "w", encoding="utf-8") as fh:
            fh.write('variable "plan_count" {\n  type = number\n}\n')
        test_file = os.path.join(module, "plans.tftest.hcl")
        with open(test_file, "w", encoding="utf-8") as fh:
            fh.write(sample)
        import contextlib, io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main([test_file, "--list"])
        listed = buf.getvalue()
        check(
            "--list names the blocks without judging anything",
            rc == 0
            and "run: queues one plan" in listed
            and "run: rejects an empty plan" in listed
            and "shared: mock_provider" in listed,
        )
        rc = main(
            [test_file, "--root", module, "--scratch", os.path.join(tmp, "scratch")]
        )
        if terraform_available():
            check("with terraform, every run prints one verdict line", rc == 0)
        else:
            check(
                "without terraform, nothing is judged and the script says so", rc == 1
            )
        empty = os.path.join(module, "none.tftest.hcl")
        with open(empty, "w", encoding="utf-8") as fh:
            fh.write("variables {\n  x = 1\n}\n")
        rc = main([empty])
        check("a file with no run blocks is refused", rc == 1)

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
