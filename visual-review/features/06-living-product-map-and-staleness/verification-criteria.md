# Verification criteria

| ID | Criterion | How to check | Fail when |
|---|---|---|---|
| VC-01 | On the 2026-11-12 config-harness files the members script names `fault` and `step-timing` as missing from c2. | Run on the fixture. | Either is not named. |
| VC-02 | After c2 is fixed the same script exits zero. | Run on the fixed file. | It exits non-zero. |
| VC-03 | A comment-only edit does not mark an element stale. | Fixture S2. | Marked stale. |
| VC-04 | A public signature change marks the element stale. | Fixture S3. | Not marked. |
| VC-05 | The plan check fails when a `removed` element is missing from the base. | Fixture S4. | It passes. |
| VC-06 | Every feature H-01 to H-13 has at least one link whose `path:line` anchor passes the anchor checker. | Run the checker on the table. | Any feature lacks a valid link. |
| VC-07 | The banner count equals the count the script prints. | Compare. | Counts differ. |
| VC-08 | The drift check changes no file. | Compare file hashes before and after. | Any hash differs. |
| VC-09 | The staleness record holds tool versions and the extraction commit. | Read the record. | One is missing. |
| VC-10 | The repo view lists each committed diagram once. | Count entries against files. | A file is missing or doubled. |
