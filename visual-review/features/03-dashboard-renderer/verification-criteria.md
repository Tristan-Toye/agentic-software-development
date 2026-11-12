# Verification criteria

| ID | Criterion | How to check | Fail when |
|---|---|---|---|
| VC-01 | Opening a run page with a diagram makes zero requests to any origin other than the dashboard. | Read the browser network log. | Any other origin appears. |
| VC-02 | No diagram text reaches `innerHTML`. | Static scan of the diagram code path, plus the S3 test. | A match in the scan, or the S3 script runs. |
| VC-03 | Switching mode changes no node coordinate. | Read coordinates before and after the switch. | Any coordinate differs. |
| VC-04 | Every status has a non-colour mark in the DOM. | Query the DOM for the status attribute and mark class. | A status element has colour only. |
| VC-05 | Each `inferred` edge has a dashed stroke style that differs from `static` edges. | Compare stroke attributes. | Same style. |
| VC-06 | The server lays out a 100-node graph within a time limit fixed by the D0 benchmark. | Time it. | Time exceeds the limit. (No measurement exists; do not ship without it.) |
| VC-07 | The inspector shows each evidence path of the node and the tier. | Click 5 sample nodes. | One path or the tier is missing. |
| VC-08 | A run with no diagram folder shows no error and no empty panel. | Open such a run. | An error shows, or an empty panel shows. |
| VC-09 | The page size grows by no more than a limit set in the dossier. | Measure the page string before and after. | Growth exceeds the limit. (No limit is in the corpus.) |
| VC-10 | The mermaid export of one graph passes the Mermaid parser. | Parse the text. | A parse error. |
