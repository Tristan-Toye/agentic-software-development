# c3 — Components: `protocol`

Derived, not authored: the Mermaid block below is the byte-for-byte output of
`python3 scripts/module_graph.py mermaid --crate protocol`, and
`scripts/module_graph.py check --against docs/architecture/c3-components-protocol.md`
regenerates it from the tree and refuses this file the moment the two
disagree. The fan table is this crate's rows of
`python3 scripts/module_graph.py table`, in the same module order the table
command already sorts them in.

```mermaid
%% module_graph: protocol
graph LR
    protocol["protocol"]
    protocol__askpass["protocol::askpass"]
    protocol__build["protocol::build"]
    protocol__envelope["protocol::envelope"]
    protocol__git_upkeep["protocol::git_upkeep"]
    protocol__identity["protocol::identity"]
    protocol__ops["protocol::ops"]
    protocol__ports["protocol::ports"]
    protocol__ports__artifact_store["protocol::ports::artifact_store"]
    protocol__ports__chat["protocol::ports::chat"]
    protocol__ports__client_secrets["protocol::ports::client_secrets"]
    protocol__ports__cloud["protocol::ports::cloud"]
    protocol__ports__git_host["protocol::ports::git_host"]
    protocol__ports__llm["protocol::ports::llm"]
    protocol__ports__parameters["protocol::ports::parameters"]
    protocol__ports__search["protocol::ports::search"]
    protocol__ports__secrets["protocol::ports::secrets"]
    protocol__ports__ticketing["protocol::ports::ticketing"]
    protocol__ports__trigger_source["protocol::ports::trigger_source"]
    protocol__provision["protocol::provision"]
    protocol__repo_grant["protocol::repo_grant"]
    protocol__sandbox_tool["protocol::sandbox_tool"]
    protocol__schema["protocol::schema"]
    protocol__schema__annotation["protocol::schema::annotation"]
    protocol__schema__artifact["protocol::schema::artifact"]
    protocol__schema__effects["protocol::schema::effects"]
    protocol__schema__enums["protocol::schema::enums"]
    protocol__schema__fingerprint["protocol::schema::fingerprint"]
    protocol__schema__ids["protocol::schema::ids"]
    protocol__schema__insights["protocol::schema::insights"]
    protocol__schema__invariants["protocol::schema::invariants"]
    protocol__schema__ledger["protocol::schema::ledger"]
    protocol__schema__mirror_maintenance["protocol::schema::mirror_maintenance"]
    protocol__schema__node["protocol::schema::node"]
    protocol__schema__provenance["protocol::schema::provenance"]
    protocol__schema__secrets["protocol::schema::secrets"]
    protocol__schema__settings["protocol::schema::settings"]
    protocol__schema__tool_policy["protocol::schema::tool_policy"]
    protocol__schema__track["protocol::schema::track"]
    protocol__schema__trigger["protocol::schema::trigger"]
    protocol__subjects["protocol::subjects"]
    protocol__telemetry["protocol::telemetry"]
    protocol__topology["protocol::topology"]
    protocol__transport["protocol::transport"]
    protocol__envelope --> protocol__identity
    protocol__ops --> protocol__identity
    protocol__ops --> protocol__schema
    protocol__ops --> protocol__transport
    protocol__ports__artifact_store --> protocol__identity
    protocol__ports__client_secrets --> protocol__identity
    protocol__ports__client_secrets --> protocol__schema__enums
    protocol__ports__client_secrets --> protocol__schema__ids
    protocol__ports__cloud --> protocol__ports__parameters
    protocol__ports__cloud --> protocol__ports__secrets
    protocol__ports__llm --> protocol__identity
    protocol__ports__llm --> protocol__schema__node
    protocol__ports__llm --> protocol__schema__tool_policy
    protocol__provision --> protocol__identity
    protocol__provision --> protocol__schema
    protocol__provision --> protocol__schema__enums
    protocol__provision --> protocol__topology
    protocol__repo_grant --> protocol__ports__git_host
    protocol__repo_grant --> protocol__schema
    protocol__schema__annotation --> protocol__identity
    protocol__schema__artifact --> protocol__identity
    protocol__schema__artifact --> protocol__schema__enums
    protocol__schema__artifact --> protocol__schema__ids
    protocol__schema__ids --> protocol__identity
    protocol__schema__insights --> protocol__schema__enums
    protocol__schema__insights --> protocol__schema__ids
    protocol__schema__ledger --> protocol__identity
    protocol__schema__ledger --> protocol__ports__llm
    protocol__schema__ledger --> protocol__schema__enums
    protocol__schema__ledger --> protocol__schema__ids
    protocol__schema__mirror_maintenance --> protocol__schema__enums
    protocol__schema__node --> protocol__identity
    protocol__schema__node --> protocol__schema__enums
    protocol__schema__provenance --> protocol__identity
    protocol__schema__provenance --> protocol__schema__enums
    protocol__schema__provenance --> protocol__schema__ids
    protocol__schema__secrets --> protocol__identity
    protocol__schema__secrets --> protocol__ports__client_secrets
    protocol__schema__secrets --> protocol__schema__enums
    protocol__schema__secrets --> protocol__schema__node
    protocol__schema__settings --> protocol__schema__insights
    protocol__schema__settings --> protocol__schema__mirror_maintenance
    protocol__schema__tool_policy --> protocol__ports__git_host
    protocol__schema__tool_policy --> protocol__repo_grant
    protocol__schema__tool_policy --> protocol__schema__effects
    protocol__schema__tool_policy --> protocol__schema__ids
    protocol__schema__track --> protocol__identity
    protocol__schema__track --> protocol__schema__enums
    protocol__schema__track --> protocol__schema__ids
    protocol__schema__trigger --> protocol__envelope
    protocol__schema__trigger --> protocol__identity
    protocol__schema__trigger --> protocol__ports__secrets
    protocol__schema__trigger --> protocol__schema__enums
    protocol__subjects --> protocol__envelope
    protocol__subjects --> protocol__identity
    protocol__subjects --> protocol__ops
    protocol__subjects --> protocol__transport
    protocol__telemetry --> protocol__identity
    protocol__topology --> protocol__identity
    protocol__topology --> protocol__schema
    protocol__transport --> protocol__envelope
    protocol__transport --> protocol__identity
```

## Fan table

| module | fan_out | fan_in | instability |
|---|---|---|---|
| protocol | 0 | 0 | 0.000 |
| protocol::askpass | 0 | 2 | 0.000 |
| protocol::build | 0 | 1 | 0.000 |
| protocol::envelope | 1 | 10 | 0.091 |
| protocol::git_upkeep | 0 | 2 | 0.000 |
| protocol::identity | 0 | 64 | 0.000 |
| protocol::ops | 3 | 8 | 0.273 |
| protocol::ports | 0 | 0 | 0.000 |
| protocol::ports::artifact_store | 1 | 12 | 0.077 |
| protocol::ports::chat | 0 | 5 | 0.000 |
| protocol::ports::client_secrets | 3 | 5 | 0.375 |
| protocol::ports::cloud | 2 | 8 | 0.200 |
| protocol::ports::git_host | 0 | 11 | 0.000 |
| protocol::ports::llm | 3 | 21 | 0.125 |
| protocol::ports::parameters | 0 | 12 | 0.000 |
| protocol::ports::search | 0 | 1 | 0.000 |
| protocol::ports::secrets | 0 | 18 | 0.000 |
| protocol::ports::ticketing | 0 | 1 | 0.000 |
| protocol::ports::trigger_source | 0 | 1 | 0.000 |
| protocol::provision | 3 | 4 | 0.429 |
| protocol::repo_grant | 2 | 3 | 0.400 |
| protocol::sandbox_tool | 0 | 9 | 0.000 |
| protocol::schema | 0 | 66 | 0.000 |
| protocol::schema::annotation | 1 | 0 | 1.000 |
| protocol::schema::artifact | 3 | 0 | 1.000 |
| protocol::schema::effects | 0 | 1 | 0.000 |
| protocol::schema::enums | 0 | 12 | 0.000 |
| protocol::schema::fingerprint | 0 | 3 | 0.000 |
| protocol::schema::ids | 1 | 9 | 0.100 |
| protocol::schema::insights | 2 | 3 | 0.400 |
| protocol::schema::invariants | 0 | 1 | 0.000 |
| protocol::schema::ledger | 4 | 0 | 1.000 |
| protocol::schema::node | 2 | 6 | 0.250 |
| protocol::schema::provenance | 3 | 0 | 1.000 |
| protocol::schema::secrets | 4 | 0 | 1.000 |
| protocol::schema::settings | 1 | 2 | 0.333 |
| protocol::schema::tool_policy | 4 | 6 | 0.400 |
| protocol::schema::track | 3 | 0 | 1.000 |
| protocol::subjects | 3 | 2 | 0.600 |
| protocol::telemetry | 1 | 3 | 0.250 |
| protocol::topology | 2 | 2 | 0.500 |
| protocol::transport | 2 | 15 | 0.118 |

