# Visual review — follow-ups

Work decided but not done yet. Each item says what to do, when, and how to know it is done.

## FU-01. Upgrade cargo-modules past the 0.27.0 pin

- **What:** `scripts/vr_extract.py` pins `CARGO_MODULES_PIN = "0.27.0"`. Any other version makes the extractor skip the
  module layer. 0.28.0 is out (checked 2026-11-13).
- **Owner decision (2026-11-13):** keep the pin for the first demo and record the upgrade as a separate step. The pin
  lives in the plugin, not in config-harness, so this is a plugin follow-up, not a config-harness dossier.
- **When:** after the first visual-review demo at the new stub.
- **How:**
  1. `cargo install cargo-modules --version 0.28.0 --locked` in a scratch environment.
  2. Run `vr_extract.py rust` on the config-harness scratch clone with both versions, at the same commit, and compare the
     module node and edge sets of every crate (stable ids).
  3. Run `vr_c3_agree.py` (VC-06) and `vr_extract.py --selftest` under the new version.
  4. If the sets match, or every difference is explained, move the pin and update the version mentions in
     `references/visual-review/README.md`.
- **Done when:** the pin names the new version, the selftests and VC-06 pass, and the comparison is recorded here.
