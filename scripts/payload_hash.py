#!/usr/bin/env python3
"""The one CONTRACT_HASH recipe, shared by compose_payloads.py and
verify_return.py so the two cannot drift again.

Before this module the composer stamped sha256 of the raw contract text while
the verifier re-derived sha256 of the contract files re-read from the agent's
worktree — bytes that differ whenever the worktree's copy is not byte-equal to
the composer's (a join, a trailing blank line, a formatter), so a return whose
contract provably did not change raised a HASH-FLAG that had to be ruled
mechanical by hand.

The canonical form is the CONTRACT block as the payload carries it: the
field's lines de-indented (the two spaces render_field writes), trailing
blank lines dropped, one final newline. The composer hashes the value it
renders; the verifier extracts the block out of the payload file and hashes
that. Both hold the same bytes, so the hash stamps exactly what the agent was
stamped with, whatever the worktree's copy of the contract files looks like.

# Usage

    from payload_hash import contract_hash, extract_contract_block, raw_sha256

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import hashlib
import os


def block_canonical(value: str) -> str:
    """The canonical block text: trailing blank lines dropped, final newline."""
    lines = value.split("\n")
    while len(lines) > 1 and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


def contract_hash(value: str) -> str:
    """sha256 of the canonical CONTRACT block form of `value`."""
    return hashlib.sha256(block_canonical(value).encode("utf-8")).hexdigest()


def raw_sha256(text: str) -> str:
    """sha256 of the exact bytes given — PAYLOAD_SHA256's recipe, which must
    never canonicalise: it proves the payload file itself is unchanged."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_contract_block(payload_text: str) -> str | None:
    """The CONTRACT field's value out of a payload file, de-indented; None
    when the payload carries no CONTRACT field. A block's lines are blank or
    start with two spaces; the first non-blank column-0 line ends the block —
    the same rule check_payload.py reads blocks by."""
    lines = payload_text.split("\n")
    start = None
    single = None
    for i, line in enumerate(lines):
        if line == "CONTRACT: |":
            start = i + 1
            break
        if line.startswith("CONTRACT:") and line.rstrip() != "CONTRACT: |":
            single = line[len("CONTRACT:") :].strip()
            break
    if single is not None:
        return single
    if start is None:
        return None
    out: list[str] = []
    for line in lines[start:]:
        if line.strip() and not line.startswith("  "):
            break
        out.append(line[2:] if line.startswith("  ") else "")
    return "\n".join(out) + "\n"


def resolve_contract_block(payload_text: str) -> str | None:
    """The CONTRACT field's content: the block itself, or — the staged form
    (issue #146, compose_payloads.py stage_oversized) — the content of the
    absolute .agent-staging/ path a single-line CONTRACT field names. The
    path may carry a trailing "(staged — read it whole ...)" note; the path is
    the token before the note's open parenthesis."""
    value = extract_contract_block(payload_text)
    if value is None:
        return None
    stripped = value.strip()
    if "\n" not in stripped and "/.agent-staging/" in stripped:
        candidate = stripped.split(" (")[0].strip()
        if os.path.isfile(candidate):
            with open(candidate, encoding="utf-8") as fh:
                return fh.read()
    return value
