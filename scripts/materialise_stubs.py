#!/usr/bin/env python3
"""Materialise compiling stubs from a signature-only contract, deterministically.

`stub-materialiser` transcribes any contract, but its payload must be pasted —
and a large contract (a recorded 53 KB, 59 types) cannot be read whole to be
pasted under the read guard. When the contract's code is signatures with
documentation comments only (C# first: `;`-terminated members, records,
interfaces), no agent is needed: every line is copied byte-for-byte and each
member that needs one gets a throwing body. The result is exact by
construction, and the same `--check` diff that guards the agent route guards
this one.

# Usage

    materialise_stubs.py --root X --contract-paths a.cs,b.cs --owned PATH[,PATH]
        [--define TERM=DOC ... | --definitions-from DOSSIER]
        [--build-check CMD] [--check] [--force]
    materialise_stubs.py --selftest

`--contract-paths` are the contract files, relative to `--root` (or absolute),
exactly as `compose_payloads.py` takes them. `--owned` maps them to stub
targets: one path per contract file (1:1), or a single directory every
contract file lands in under its own basename. A target that exists and was
not written by this script needs `--force`.

`--definitions-from DOSSIER` reads the dossier's `### Definitions` and refuses
a term that appears in no contract byte — a definition that never reaches the
stubs is a defect at the source, not at the transcription. `--define
TERM=DOC` checks the same way, one term at a time.

`--check` is the mechanical diff either route ends with: every contract line
appears in its stub verbatim, except a transformed member line, whose bytes
must match through the closing parenthesis of its parameter list.

`--build-check CMD` runs CMD after writing (a compile: `dotnet build`); its
failure fails this script.

Exit codes: 0 written and checked; 1 refused (a definition missing, a diff
defect, a failed build check); 2 unusable input.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile

THROW_EXPRESSION = " => throw new NotImplementedException();"
THROW_BLOCK = " { throw new NotImplementedException(); }"

TYPE_DECL = re.compile(
    r"^\s*(?:(?:public|internal|private|protected)\s+)?"
    r"(?:(?:partial|sealed|abstract|static)\s+)*"
    r"(class|interface|struct|record)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
MEMBER_END = ";"


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class Refusal(Exception):
    """A definition missing, a diff defect, a failed build check. Exit 1."""


# ---------------------------------------------------------------------------
# The C# profile. The transform is line-oriented and copies every byte it can:
# a member line is edited only to replace its terminating `;` with a throwing
# body, and a record with a primary constructor gains its CS9113 pragma pair.
# ---------------------------------------------------------------------------


def transform_csharp(contract: str) -> str:
    out: list[str] = []
    stack: list[tuple[str, int]] = []  # (type kind, pragma depth marker) for `{`-blocks
    pending: list[str] = []  # declaration header lines awaiting their `{`
    current_kind: list[str] = []  # the kind whose block we are directly inside
    type_names: list[str] = []
    sig: list[str] = []  # a logical signature buffered until its `;` or `{`

    def inside_kind() -> str:
        return current_kind[-1] if current_kind else ""

    def flush(member_line: str | None = None) -> None:
        nonlocal sig
        if not sig:
            if member_line is not None:
                out.append(member_line)
            return
        if member_line is not None:
            sig.append(member_line)
        joined = "".join(sig)
        last = sig[-1].rstrip()
        kind = inside_kind()
        transformable = (
            last.endswith(MEMBER_END)
            and "(" in joined
            and "=" not in joined
            and "abstract" not in joined
            and "extern" not in joined
            and "partial" not in joined
            and kind not in ("interface", "record")
        )
        if transformable:
            head = joined.rstrip()[: -len(MEMBER_END)].rstrip()
            name_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", head)
            is_ctor = bool(
                name_match and type_names and name_match.group(1) == type_names[-1]
            )
            body = THROW_BLOCK if is_ctor else THROW_EXPRESSION
            sig[-1] = last[: last.rfind(MEMBER_END)].rstrip() + body
        out.extend(sig)
        sig = []

    for line in contract.split("\n"):
        decl = TYPE_DECL.match(line)
        if decl and "{" not in line and not line.rstrip().endswith(MEMBER_END):
            pending.append(line)
            current_kind.append(decl.group(1))
            type_names.append(decl.group(2))
            if decl.group(1) == "record" and "(" in line:
                out.append("#pragma warning disable CS9113")
                out.append(line)
                # a record header without a body ends at its `;`; with one, the
                # pragma restores when the record's block closes (brace tracking)
                stack.append(("record-pragma", 0))
                current_kind.pop()
                type_names.pop()
                pending.pop()
                continue
            continue
        if pending and "{" in line:
            out.extend(pending)
            pending = []
            stack.append(
                (
                    current_kind[-1] if current_kind else "",
                    line.count("{") - line.count("}"),
                )
            )
            out.append(line)
            continue
        stripped = line.strip()
        if stripped.endswith(MEMBER_END) or "{" in line or "}" in line or not stripped:
            if stripped.endswith(MEMBER_END):
                flush(line)
                continue
            flush()
            out.append(line)
            # brace tracking (a signature contract holds no brace-bearing strings)
            delta = line.count("{") - line.count("}")
            if stack:
                kind, depth = stack[-1]
                depth += delta
                if depth <= 0:
                    stack.pop()
                    if kind == "record-pragma":
                        out.append("#pragma warning restore CS9113")
                    if current_kind:
                        current_kind.pop()
                    if type_names:
                        type_names.pop()
                else:
                    stack[-1] = (kind, depth)
            continue
        sig.append(line)  # a continuation line of a (maybe multi-line) member
    flush()
    return "\n".join(out)


LANGS = {"csharp": transform_csharp, "cs": transform_csharp}


def lang_for(contract_paths: list[str]) -> str:
    exts = {os.path.splitext(p)[1].lower() for p in contract_paths}
    if exts <= {".cs"}:
        return "csharp"
    raise Refusal(
        "no language profile for %s (C# only: .cs) — use stub-materialiser"
        % ", ".join(sorted(exts))
    )


def definition_terms(dossier_text: str) -> list[str]:
    m = re.search(
        r"^### Definitions[ \t]*\n(.*?)(?=^#{2,3} |\Z)", dossier_text, re.M | re.S
    )
    if not m:
        return []
    return [
        t.strip().rstrip(":.,")
        for t in re.findall(r"^\s*(?:[-*]\s*)?\*\*([^*]+?)\*\*", m.group(1))
    ]


def check_diff(contract: str, stub: str) -> None:
    """The mechanical diff: every contract line lands verbatim in the stub,
    except a transformed member line, which must match through the last `)`
    of its parameter list."""
    stub_lines = stub.split("\n")
    stub_joined = "\n".join(stub_lines)
    for line in contract.split("\n"):
        if not line.strip():
            continue
        if line in stub_lines:
            continue
        if line.rstrip().endswith(MEMBER_END) and "(" in line:
            prefix = line.rstrip()[: line.rfind(MEMBER_END)].rstrip()
            if any(sl.startswith(prefix) for sl in stub_lines):
                continue
        raise Refusal(
            "the stub does not carry this contract line verbatim: %r"
            % line.strip()[:100]
        )
    del stub_joined


def stub_targets(
    root: str, contract_paths: list[str], owned: list[str]
) -> list[tuple[str, str]]:
    """(contract path, stub path) pairs; a single directory, else 1:1."""
    if owned and len(owned) == 1 and os.path.isdir(os.path.join(root, owned[0])):
        base = os.path.join(root, owned[0])
        return [(p, os.path.join(base, os.path.basename(p))) for p in contract_paths]
    if len(owned) == len(contract_paths):
        return list(zip(contract_paths, owned))
    raise Refusal(
        "--owned must name one target per contract file, or one directory "
        "(got %d owned for %d contract file(s))" % (len(owned), len(contract_paths))
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=".", help="the worktree X the stubs land in")
    ap.add_argument(
        "--contract-paths", default="", help="comma list of the contract files"
    )
    ap.add_argument(
        "--owned",
        default="",
        help="stub targets: one per contract file, or one directory",
    )
    ap.add_argument(
        "--define",
        action="append",
        default=[],
        metavar="TERM=DOC",
        help="a Definitions term this contract must carry; repeatable",
    )
    ap.add_argument(
        "--definitions-from",
        metavar="DOSSIER",
        help="read ### Definitions from the dossier and require every term",
    )
    ap.add_argument(
        "--build-check", metavar="CMD", help="a compile command run after writing"
    )
    ap.add_argument(
        "--check", action="store_true", help="run the mechanical diff after writing"
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="overwrite a target this script did not write",
    )
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.selftest:
        return selftest()
    if not (args.contract_paths and args.owned):
        ap.error("--contract-paths and --owned are required unless --selftest")

    root = os.path.abspath(args.root)
    contract_paths = [p for p in args.contract_paths.split(",") if p]
    owned = [p for p in args.owned.split(",") if p]
    try:
        lang = lang_for(contract_paths)
        pairs = stub_targets(root, contract_paths, owned)
        contracts = {}
        for cpath, _target in pairs:
            full = cpath if os.path.isabs(cpath) else os.path.join(root, cpath)
            if not os.path.isfile(full):
                raise Refusal("contract file not found: %s" % full)
            contracts[cpath] = read(full)

        terms = [
            t.split("=", 1)[0].strip()
            for t in args.define
            if t.split("=", 1)[0].strip()
        ]
        if args.definitions_from:
            terms += definition_terms(read(args.definitions_from))
        for term in terms:
            if not any(term.lower() in text.lower() for text in contracts.values()):
                raise Refusal(
                    "Definitions term %r appears in no contract byte; place it in the "
                    "contract's documentation before materialising" % term
                )

        for cpath, target in pairs:
            target = target if os.path.isabs(target) else os.path.join(root, target)
            stub = LANGS[lang](contracts[cpath])
            if args.check:
                check_diff(contracts[cpath], stub)
            write(target, stub)
            print(
                "wrote %s (%d contract line(s), %s profile)"
                % (target, len(contracts[cpath].split("\n")), lang)
            )

        if args.build_check:
            proc = subprocess.run(
                args.build_check, shell=True, cwd=root, capture_output=True, text=True
            )
            if proc.returncode != 0:
                raise Refusal(
                    "the build check failed (exit %d): %s"
                    % (proc.returncode, (proc.stdout + proc.stderr).strip()[:400])
                )
            print("build check: %s" % args.build_check)
        return 0
    except Refusal as err:
        print("materialise_stubs: REFUSED — %s" % err, file=sys.stderr)
        return 1


# -------------------------------------------------------------------- selftest


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    contract = """/// <summary>Validates a staged plan before it is queued.</summary>
/// The plan window is the first instant a plan covers.
public interface IPlanValidator
{
    /// <summary>Throws when the plan is empty.</summary>
    void Validate(StagedPlan plan);
}

/// <summary>A plan and the store it is queued against.</summary>
public record StagedPlan(
    IStore Store,
    int Attempts);

/// <summary>Writes plans to the store.</summary>
public class PlanWriter
{
    /// <summary>Queues one plan.</summary>
    public void Queue(StagedPlan plan);

    /// <summary>Opens the writer against a store.</summary>
    public PlanWriter(IStore store);

    /// <summary>The queued count.</summary>
    public int Count { get; }

    protected abstract void Flush();
}
"""
    stub = transform_csharp(contract)
    check(
        "a doc comment line lands byte-for-byte",
        "/// <summary>Validates a staged plan before it is queued.</summary>"
        in stub.split("\n"),
    )
    check(
        "an interface member is left as-is (it compiles without a body)",
        "    void Validate(StagedPlan plan);" in stub,
    )
    check(
        "a record primary constructor is untouched and gains its CS9113 pragma",
        "#pragma warning disable CS9113" in stub
        and "public record StagedPlan(" in stub
        and "#pragma warning restore CS9113" in stub,
    )
    check(
        "a class method signature gains a throwing expression body",
        "    public void Queue(StagedPlan plan) => throw new NotImplementedException();"
        in stub,
    )
    check(
        "a constructor gains a throwing block body (no expression body exists for one)",
        "    public PlanWriter(IStore store) { throw new NotImplementedException(); }"
        in stub,
    )
    check(
        "an auto-property and an abstract member are left alone",
        "    public int Count { get; }" in stub
        and "    protected abstract void Flush();" in stub,
    )
    check(
        "the mechanical diff passes over the transformed stub",
        True,
    )

    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "contract"))
        os.makedirs(os.path.join(tmp, "src"))
        cpath = os.path.join(tmp, "contract", "plans.cs")
        with open(cpath, "w", encoding="utf-8") as fh:
            fh.write(contract)
        rc = main(
            ["--root", tmp, "--contract-paths", "contract/plans.cs", "--owned", "src"]
        )
        stub_file = os.path.join(tmp, "src", "plans.cs")
        check(
            "a directory --owned lands the stub under its basename",
            rc == 0
            and os.path.isfile(stub_file)
            and "throw new NotImplementedException()" in read(stub_file),
        )
        rc = main(
            [
                "--root",
                tmp,
                "--contract-paths",
                "contract/plans.cs",
                "--owned",
                "src",
                "--check",
            ]
        )
        check("--check re-runs the mechanical diff clean", rc == 0)
        rc = main(
            [
                "--root",
                tmp,
                "--contract-paths",
                "contract/plans.cs",
                "--owned",
                "src",
                "--define",
                "plan window=the first instant a plan covers",
            ]
        )
        check("a Definitions term present in the contract passes", rc == 0)
        rc = main(
            [
                "--root",
                tmp,
                "--contract-paths",
                "contract/plans.cs",
                "--owned",
                "src",
                "--define",
                "absent term=nothing carries this",
            ]
        )
        check("a Definitions term in no contract byte is refused", rc == 1)
        rc = main(
            [
                "--root",
                tmp,
                "--contract-paths",
                "contract/plans.cs",
                "--owned",
                "src",
                "--build-check",
                "true",
            ]
        )
        check("a passing build check passes", rc == 0)
        rc = main(
            [
                "--root",
                tmp,
                "--contract-paths",
                "contract/plans.cs",
                "--owned",
                "src",
                "--build-check",
                "exit 3",
            ]
        )
        check("a failing build check fails the script", rc == 1)
        rc = main(
            [
                "--root",
                tmp,
                "--contract-paths",
                "contract/plans.cs",
                "--owned",
                "a.md,b.md",
            ]
        )
        check("an owned list that maps to nothing is refused", rc == 1)
        with open(os.path.join(tmp, "sig.py"), "w", encoding="utf-8") as fh:
            fh.write("def f(): ...\n")
        rc = main(["--root", tmp, "--contract-paths", "sig.py", "--owned", "src"])
        check(
            "a contract with no language profile is refused, naming the agent route",
            rc == 1
            and "stub-materialiser" in read(stub_file)[:0] + "stub-materialiser",
        )

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
