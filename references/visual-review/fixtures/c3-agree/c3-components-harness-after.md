# c3 — Components: `harness`

Derived, not authored: the Mermaid block below is the byte-for-byte output of
`python3 scripts/module_graph.py mermaid --crate harness`, and
`scripts/module_graph.py check --against docs/architecture/c3-components-harness.md`
regenerates it from the tree and refuses this file the moment the two
disagree. The fan table is this crate's rows of
`python3 scripts/module_graph.py table`, in the same module order the table
command already sorts them in.

```mermaid
%% module_graph: harness
graph LR
    harness["harness"]
    harness__archive["harness::archive"]
    harness__boot["harness::boot"]
    harness__build["harness::build"]
    harness__cancel_sweep["harness::cancel_sweep"]
    harness__checkpoint_delta["harness::checkpoint_delta"]
    harness__compaction["harness::compaction"]
    harness__context_ledger["harness::context_ledger"]
    harness__human_input["harness::human_input"]
    harness__inbox["harness::inbox"]
    harness__insights["harness::insights"]
    harness__ledger["harness::ledger"]
    harness__llm_step["harness::llm_step"]
    harness__ops["harness::ops"]
    harness__provenance["harness::provenance"]
    harness__ready_signal["harness::ready_signal"]
    harness__runtime["harness::runtime"]
    harness__sandbox["harness::sandbox"]
    harness__sandbox__checkpoint["harness::sandbox::checkpoint"]
    harness__sandbox__checkpointer["harness::sandbox::checkpointer"]
    harness__sandbox__child["harness::sandbox::child"]
    harness__sandbox__common["harness::sandbox::common"]
    harness__sandbox__confined["harness::sandbox::confined"]
    harness__sandbox__exec["harness::sandbox::exec"]
    harness__sandbox__file_backend["harness::sandbox::file_backend"]
    harness__sandbox__frame["harness::sandbox::frame"]
    harness__sandbox__gvisor_backend["harness::sandbox::gvisor_backend"]
    harness__sandbox__rootfs["harness::sandbox::rootfs"]
    harness__sandbox__tool_sandbox["harness::sandbox::tool_sandbox"]
    harness__sandbox__workspace_events["harness::sandbox::workspace_events"]
    harness__sandbox__workspace_lock["harness::sandbox::workspace_lock"]
    harness__sandbox__workspace_seed["harness::sandbox::workspace_seed"]
    harness__sandbox__zfs_backend["harness::sandbox::zfs_backend"]
    harness__sandbox__zfs_common["harness::sandbox::zfs_common"]
    harness__summariser["harness::summariser"]
    harness__supervisor["harness::supervisor"]
    harness__tool_ledger["harness::tool_ledger"]
    harness__tools["harness::tools"]
    harness__tools__annotations["harness::tools::annotations"]
    harness__tools__ask_human["harness::tools::ask_human"]
    harness__tools__audit["harness::tools::audit"]
    harness__tools__blast_radius["harness::tools::blast_radius"]
    harness__tools__boundary["harness::tools::boundary"]
    harness__tools__catalogue["harness::tools::catalogue"]
    harness__tools__commands["harness::tools::commands"]
    harness__tools__effects["harness::tools::effects"]
    harness__tools__gate["harness::tools::gate"]
    harness__tools__git_mirror["harness::tools::git_mirror"]
    harness__tools__git_tools["harness::tools::git_tools"]
    harness__tools__grants["harness::tools::grants"]
    harness__tools__handle["harness::tools::handle"]
    harness__tools__journal["harness::tools::journal"]
    harness__tools__mirror_maintenance["harness::tools::mirror_maintenance"]
    harness__tools__names["harness::tools::names"]
    harness__tools__outcome["harness::tools::outcome"]
    harness__tools__pipeline["harness::tools::pipeline"]
    harness__tools__red_corpus["harness::tools::red_corpus"]
    harness__tools__red_oracle["harness::tools::red_oracle"]
    harness__tools__registry["harness::tools::registry"]
    harness__tools__relay["harness::tools::relay"]
    harness__tools__rules["harness::tools::rules"]
    harness__tools__spec["harness::tools::spec"]
    harness__tools__step["harness::tools::step"]
    harness__tools__stops["harness::tools::stops"]
    harness__tools__util["harness::tools::util"]
    harness__tools__workspace["harness::tools::workspace"]
    harness__tools__write_tools["harness::tools::write_tools"]
    harness__track["harness::track"]
    harness__usage_scope["harness::usage_scope"]
    harness__vocab["harness::vocab"]
    harness__watchdog["harness::watchdog"]
    harness__working_memory["harness::working_memory"]
    harness --> harness__archive
    harness --> harness__boot
    harness --> harness__checkpoint_delta
    harness --> harness__compaction
    harness --> harness__ledger
    harness --> harness__llm_step
    harness --> harness__provenance
    harness --> harness__ready_signal
    harness --> harness__runtime
    harness --> harness__sandbox__checkpoint
    harness --> harness__sandbox__checkpointer
    harness --> harness__sandbox__file_backend
    harness --> harness__sandbox__gvisor_backend
    harness --> harness__sandbox__rootfs
    harness --> harness__sandbox__workspace_events
    harness --> harness__sandbox__workspace_lock
    harness --> harness__sandbox__zfs_backend
    harness --> harness__track
    harness --> harness__working_memory
    harness__boot --> harness__ledger
    harness__cancel_sweep --> harness__track
    harness__compaction --> harness__llm_step
    harness__compaction --> harness__tools__step
    harness__compaction --> harness__working_memory
    harness__context_ledger --> harness__tool_ledger
    harness__human_input --> harness__ops
    harness__human_input --> harness__track
    harness__inbox --> harness__llm_step
    harness__inbox --> harness__ops
    harness__inbox --> harness__track
    harness__ledger -.-> harness__tools__names
    harness__ledger --> harness__track
    harness__ledger --> harness__usage_scope
    harness__llm_step --> harness__runtime
    harness__llm_step --> harness__track
    harness__ops --> harness__runtime
    harness__provenance --> harness__vocab
    harness__runtime --> harness__human_input
    harness__runtime --> harness__inbox
    harness__runtime --> harness__llm_step
    harness__runtime --> harness__ops
    harness__runtime --> harness__ready_signal
    harness__runtime --> harness__summariser
    harness__runtime --> harness__supervisor
    harness__runtime --> harness__track
    harness__runtime --> harness__usage_scope
    harness__runtime --> harness__working_memory
    harness__sandbox --> harness__sandbox__checkpoint
    harness__sandbox --> harness__sandbox__checkpointer
    harness__sandbox --> harness__sandbox__common
    harness__sandbox --> harness__sandbox__file_backend
    harness__sandbox --> harness__sandbox__gvisor_backend
    harness__sandbox --> harness__sandbox__rootfs
    harness__sandbox --> harness__sandbox__workspace_events
    harness__sandbox --> harness__sandbox__workspace_lock
    harness__sandbox --> harness__sandbox__workspace_seed
    harness__sandbox --> harness__sandbox__zfs_backend
    harness__sandbox__checkpointer --> harness__build
    harness__sandbox__checkpointer --> harness__sandbox
    harness__sandbox__checkpointer --> harness__sandbox__checkpoint
    harness__sandbox__checkpointer --> harness__sandbox__workspace_events
    harness__sandbox__checkpointer --> harness__sandbox__workspace_lock
    harness__sandbox__checkpointer --> harness__tools__journal
    harness__sandbox__common --> harness__sandbox__checkpoint
    harness__sandbox__exec --> harness__sandbox
    harness__sandbox__exec --> harness__sandbox__checkpoint
    harness__sandbox__exec --> harness__sandbox__child
    harness__sandbox__exec --> harness__sandbox__common
    harness__sandbox__file_backend --> harness__sandbox
    harness__sandbox__file_backend --> harness__sandbox__checkpoint
    harness__sandbox__file_backend --> harness__sandbox__common
    harness__sandbox__file_backend --> harness__sandbox__frame
    harness__sandbox__frame --> harness__sandbox__checkpoint
    harness__sandbox__frame --> harness__sandbox__common
    harness__sandbox__gvisor_backend --> harness__sandbox__checkpoint
    harness__sandbox__gvisor_backend --> harness__sandbox__common
    harness__sandbox__gvisor_backend --> harness__sandbox__exec
    harness__sandbox__gvisor_backend --> harness__sandbox__rootfs
    harness__sandbox__gvisor_backend --> harness__tools__commands
    harness__sandbox__gvisor_backend --> harness__tools__spec
    harness__sandbox__rootfs --> harness__sandbox
    harness__sandbox__rootfs --> harness__sandbox__checkpoint
    harness__sandbox__rootfs --> harness__sandbox__common
    harness__sandbox__rootfs --> harness__sandbox__exec
    harness__sandbox__rootfs --> harness__sandbox__zfs_common
    harness__sandbox__tool_sandbox --> harness__sandbox__checkpoint
    harness__sandbox__tool_sandbox --> harness__sandbox__child
    harness__sandbox__tool_sandbox --> harness__sandbox__common
    harness__sandbox__tool_sandbox --> harness__sandbox__exec
    harness__sandbox__workspace_seed --> harness__sandbox
    harness__sandbox__workspace_seed --> harness__sandbox__checkpoint
    harness__sandbox__workspace_seed --> harness__sandbox__common
    harness__sandbox__workspace_seed --> harness__sandbox__exec
    harness__sandbox__workspace_seed --> harness__sandbox__workspace_lock
    harness__sandbox__workspace_seed --> harness__sandbox__zfs_backend
    harness__sandbox__workspace_seed --> harness__sandbox__zfs_common
    harness__sandbox__zfs_backend --> harness__sandbox
    harness__sandbox__zfs_backend --> harness__sandbox__checkpoint
    harness__sandbox__zfs_backend --> harness__sandbox__common
    harness__sandbox__zfs_backend --> harness__sandbox__exec
    harness__sandbox__zfs_backend --> harness__sandbox__frame
    harness__sandbox__zfs_common --> harness__sandbox__checkpoint
    harness__sandbox__zfs_common --> harness__sandbox__exec
    harness__summariser --> harness__llm_step
    harness__summariser --> harness__track
    harness__summariser --> harness__working_memory
    harness__supervisor --> harness__inbox
    harness__supervisor --> harness__track
    harness__tool_ledger --> harness__tools__gate
    harness__tool_ledger --> harness__vocab
    harness__tools -.-> harness__sandbox__exec
    harness__tools --> harness__tools__annotations
    harness__tools --> harness__tools__audit
    harness__tools --> harness__tools__blast_radius
    harness__tools --> harness__tools__catalogue
    harness__tools --> harness__tools__commands
    harness__tools --> harness__tools__effects
    harness__tools --> harness__tools__gate
    harness__tools --> harness__tools__git_tools
    harness__tools --> harness__tools__grants
    harness__tools --> harness__tools__handle
    harness__tools --> harness__tools__journal
    harness__tools --> harness__tools__pipeline
    harness__tools --> harness__tools__red_corpus
    harness__tools --> harness__tools__registry
    harness__tools --> harness__tools__rules
    harness__tools --> harness__tools__spec
    harness__tools --> harness__tools__step
    harness__tools --> harness__tools__workspace
    harness__tools --> harness__tools__write_tools
    harness__tools --> harness__vocab
    harness__tools__ask_human --> harness__tools__registry
    harness__tools__ask_human --> harness__tools__spec
    harness__tools__audit --> harness__tools__pipeline
    harness__tools__blast_radius --> harness__tools__effects
    harness__tools__blast_radius --> harness__tools__gate
    harness__tools__blast_radius --> harness__tools__spec
    harness__tools__blast_radius --> harness__tools__util
    harness__tools__boundary --> harness__tools__registry
    harness__tools__boundary --> harness__tools__spec
    harness__tools__catalogue --> harness__tools__annotations
    harness__tools__catalogue --> harness__tools__handle
    harness__tools__catalogue --> harness__tools__registry
    harness__tools__catalogue --> harness__tools__spec
    harness__tools__catalogue --> harness__tools__util
    harness__tools__commands -.-> harness__sandbox__exec
    harness__tools__commands -.-> harness__tools__catalogue
    harness__tools__commands --> harness__tools__registry
    harness__tools__commands --> harness__tools__rules
    harness__tools__commands --> harness__tools__spec
    harness__tools__effects --> harness__tools__spec
    harness__tools__effects --> harness__tools__util
    harness__tools__gate --> harness__tools__effects
    harness__tools__gate --> harness__tools__spec
    harness__tools__git_mirror --> harness__sandbox__common
    harness__tools__git_mirror --> harness__sandbox__workspace_lock
    harness__tools__git_mirror --> harness__tools__workspace
    harness__tools__git_tools --> harness__tools__audit
    harness__tools__git_tools --> harness__tools__catalogue
    harness__tools__git_tools --> harness__tools__git_mirror
    harness__tools__git_tools --> harness__tools__handle
    harness__tools__git_tools --> harness__tools__registry
    harness__tools__git_tools --> harness__tools__spec
    harness__tools__git_tools --> harness__tools__util
    harness__tools__git_tools --> harness__tools__workspace
    harness__tools__grants --> harness__tools__effects
    harness__tools__handle --> harness__tools__rules
    harness__tools__journal --> harness__tools__gate
    harness__tools__journal --> harness__tools__pipeline
    harness__tools__journal --> harness__vocab
    harness__tools__mirror_maintenance --> harness__tools__git_mirror
    harness__tools__outcome --> harness__vocab
    harness__tools__pipeline --> harness__ops
    harness__tools__pipeline --> harness__tools__audit
    harness__tools__pipeline -.-> harness__tools__catalogue
    harness__tools__pipeline --> harness__tools__effects
    harness__tools__pipeline --> harness__tools__gate
    harness__tools__pipeline --> harness__tools__grants
    harness__tools__pipeline --> harness__tools__handle
    harness__tools__pipeline -.-> harness__tools__names
    harness__tools__pipeline --> harness__tools__registry
    harness__tools__pipeline --> harness__tools__rules
    harness__tools__pipeline --> harness__tools__spec
    harness__tools__pipeline --> harness__tools__util
    harness__tools__red_corpus --> harness__tools__effects
    harness__tools__red_corpus --> harness__tools__gate
    harness__tools__red_corpus --> harness__tools__spec
    harness__tools__red_oracle --> harness__tools__blast_radius
    harness__tools__red_oracle --> harness__tools__effects
    harness__tools__red_oracle --> harness__tools__gate
    harness__tools__red_oracle --> harness__tools__red_corpus
    harness__tools__red_oracle --> harness__tools__spec
    harness__tools__registry --> harness__tools__spec
    harness__tools__relay --> harness__tools__registry
    harness__tools__relay --> harness__tools__spec
    harness__tools__rules --> harness__tools__spec
    harness__tools__step --> harness__archive
    harness__tools__step --> harness__compaction
    harness__tools__step --> harness__context_ledger
    harness__tools__step --> harness__llm_step
    harness__tools__step --> harness__provenance
    harness__tools__step --> harness__runtime
    harness__tools__step --> harness__tool_ledger
    harness__tools__step -.-> harness__tools__ask_human
    harness__tools__step --> harness__tools__audit
    harness__tools__step -.-> harness__tools__boundary
    harness__tools__step --> harness__tools__gate
    harness__tools__step --> harness__tools__journal
    harness__tools__step -.-> harness__tools__names
    harness__tools__step --> harness__tools__pipeline
    harness__tools__step --> harness__tools__registry
    harness__tools__step -.-> harness__tools__relay
    harness__tools__step --> harness__tools__spec
    harness__tools__step --> harness__tools__stops
    harness__tools__step --> harness__track
    harness__tools__step --> harness__usage_scope
    harness__tools__step --> harness__vocab
    harness__tools__step --> harness__working_memory
    harness__tools__stops --> harness__runtime
    harness__tools__stops --> harness__track
    harness__tools__util --> harness__tools__registry
    harness__tools__workspace --> harness__sandbox__checkpoint
    harness__tools__workspace --> harness__sandbox__child
    harness__tools__workspace --> harness__sandbox__workspace_lock
    harness__tools__workspace --> harness__sandbox__workspace_seed
    harness__tools__workspace --> harness__sandbox__zfs_backend
    harness__tools__workspace --> harness__tools__audit
    harness__tools__workspace --> harness__tools__registry
    harness__tools__workspace --> harness__tools__step
    harness__tools__workspace --> harness__tools__util
    harness__tools__workspace -.-> harness__vocab
    harness__tools__write_tools --> harness__tools__audit
    harness__tools__write_tools --> harness__tools__catalogue
    harness__tools__write_tools --> harness__tools__handle
    harness__tools__write_tools --> harness__tools__registry
    harness__tools__write_tools --> harness__tools__spec
    harness__tools__write_tools --> harness__tools__util
    harness__tools__write_tools --> harness__tools__workspace
    harness__tools__write_tools -.-> harness__vocab
    harness__track --> harness__ready_signal
    harness__track --> harness__working_memory
    harness__usage_scope -.-> harness__tools__names
    harness__watchdog --> harness__inbox
    harness__watchdog --> harness__ops
    harness__watchdog --> harness__track
```

## Fan table

| module | fan_out | fan_in | instability |
|---|---|---|---|
| harness | 20 | 0 | 1.000 |
| harness::archive | 3 | 3 | 0.500 |
| harness::boot | 5 | 2 | 0.714 |
| harness::build | 1 | 1 | 0.500 |
| harness::cancel_sweep | 2 | 1 | 0.667 |
| harness::checkpoint_delta | 0 | 1 | 0.000 |
| harness::compaction | 5 | 3 | 0.625 |
| harness::context_ledger | 3 | 2 | 0.600 |
| harness::human_input | 8 | 2 | 0.800 |
| harness::inbox | 10 | 4 | 0.714 |
| harness::insights | 4 | 2 | 0.667 |
| harness::ledger | 8 | 2 | 0.800 |
| harness::llm_step | 5 | 6 | 0.455 |
| harness::ops | 5 | 6 | 0.455 |
| harness::provenance | 5 | 3 | 0.625 |
| harness::ready_signal | 0 | 4 | 0.000 |
| harness::runtime | 17 | 6 | 0.739 |
| harness::sandbox | 12 | 9 | 0.571 |
| harness::sandbox::checkpoint | 4 | 15 | 0.211 |
| harness::sandbox::checkpointer | 12 | 4 | 0.750 |
| harness::sandbox::child | 0 | 3 | 0.000 |
| harness::sandbox::common | 1 | 10 | 0.091 |
| harness::sandbox::confined | 1 | 0 | 1.000 |
| harness::sandbox::exec | 4 | 9 | 0.308 |
| harness::sandbox::file_backend | 8 | 3 | 0.727 |
| harness::sandbox::frame | 2 | 2 | 0.500 |
| harness::sandbox::gvisor_backend | 10 | 3 | 0.769 |
| harness::sandbox::rootfs | 6 | 4 | 0.600 |
| harness::sandbox::tool_sandbox | 8 | 1 | 0.889 |
| harness::sandbox::workspace_events | 0 | 3 | 0.000 |
| harness::sandbox::workspace_lock | 0 | 6 | 0.000 |
| harness::sandbox::workspace_seed | 11 | 3 | 0.786 |
| harness::sandbox::zfs_backend | 9 | 5 | 0.643 |
| harness::sandbox::zfs_common | 2 | 2 | 0.500 |
| harness::summariser | 4 | 2 | 0.667 |
| harness::supervisor | 5 | 2 | 0.714 |
| harness::tool_ledger | 5 | 3 | 0.625 |
| harness::tools | 21 | 0 | 1.000 |
| harness::tools::annotations | 2 | 3 | 0.400 |
| harness::tools::ask_human | 3 | 2 | 0.600 |
| harness::tools::audit | 5 | 7 | 0.417 |
| harness::tools::blast_radius | 7 | 3 | 0.700 |
| harness::tools::boundary | 2 | 2 | 0.500 |
| harness::tools::catalogue | 8 | 6 | 0.571 |
| harness::tools::commands | 7 | 3 | 0.700 |
| harness::tools::effects | 4 | 7 | 0.364 |
| harness::tools::gate | 3 | 8 | 0.273 |
| harness::tools::git_mirror | 9 | 3 | 0.750 |
| harness::tools::git_tools | 13 | 2 | 0.867 |
| harness::tools::grants | 3 | 2 | 0.600 |
| harness::tools::handle | 2 | 6 | 0.250 |
| harness::tools::journal | 6 | 4 | 0.600 |
| harness::tools::mirror_maintenance | 2 | 1 | 0.667 |
| harness::tools::names | 0 | 4 | 0.000 |
| harness::tools::outcome | 1 | 0 | 1.000 |
| harness::tools::pipeline | 16 | 5 | 0.762 |
| harness::tools::red_corpus | 5 | 2 | 0.714 |
| harness::tools::red_oracle | 6 | 0 | 1.000 |
| harness::tools::registry | 5 | 13 | 0.278 |
| harness::tools::relay | 2 | 2 | 0.500 |
| harness::tools::rules | 2 | 5 | 0.286 |
| harness::tools::spec | 0 | 18 | 0.000 |
| harness::tools::step | 30 | 4 | 0.882 |
| harness::tools::stops | 4 | 2 | 0.667 |
| harness::tools::util | 3 | 7 | 0.300 |
| harness::tools::workspace | 15 | 5 | 0.750 |
| harness::tools::write_tools | 11 | 2 | 0.846 |
| harness::track | 10 | 13 | 0.435 |
| harness::usage_scope | 3 | 3 | 0.500 |
| harness::vocab | 0 | 8 | 0.000 |
| harness::watchdog | 8 | 1 | 0.889 |
| harness::working_memory | 1 | 7 | 0.125 |
