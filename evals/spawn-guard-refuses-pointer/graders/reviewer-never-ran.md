---
type: regex
pattern: "(LENS:|CHANGE-REQUEST|change request)"
match: not_contains
target: last_message
---

No reviewer output came back: a reviewer that ran would have answered with a lens or findings.
