# Verification criteria

| ID | Criterion | How to check | Fail when |
|---|---|---|---|
| VC-01 | `validate_pipeline.py --pre-fanout` exits non-zero when the `VISUAL-REVIEW:` line is missing. | Run on a fixture log. | It exits zero. |
| VC-02 | It exits zero when the line is present and well formed. | Run on a good log. | It exits non-zero. |
| VC-03 | The `VISUAL-REVIEW:` counts equal the counts in `diff.json`. | Compare. | Any count differs. |
| VC-04 | The validator rejects each of 6 single-defect graphs (no evidence, bad path, duplicate ID, over budget, missing legend, missing title). | Run on 6 fixtures. | Any one passes. |
| VC-05 | A graph that fails the validator creates no owner question. | Run the flow with a bad graph. | A question appears. |
| VC-06 | The agent definition lists no write tool for product paths. | Read the file. | A write tool is listed. |
| VC-07 | The contract-review payload contains no diagram file. | List the payload. | One is present. |
| VC-08 | The run folder holds the four named outputs. | List it. | One is missing. |
| VC-09 | The run-detail JSON has `diagrams`. | Call the API. | Field absent. |
| VC-10 | After 2 failed repair attempts the step stops and the build log says why. | Force failure. | A third attempt starts or the log is silent. |
