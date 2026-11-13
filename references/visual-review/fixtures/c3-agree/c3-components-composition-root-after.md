# c3 — Components: `composition_root`

Derived, not authored: the Mermaid block below is the byte-for-byte output of
`python3 scripts/module_graph.py mermaid --crate composition-root`, and
`scripts/module_graph.py check --against docs/architecture/c3-components-composition-root.md`
regenerates it from the tree and refuses this file the moment the two
disagree. The fan table is this crate's rows of
`python3 scripts/module_graph.py table`, in the same module order the table
command already sorts them in.

```mermaid
%% module_graph: composition_root
graph LR
    composition_root["composition_root"]
    composition_root__account_directory["composition_root::account_directory"]
    composition_root__bin__composition_root["composition_root::bin::composition_root"]
    composition_root__bin__mesh_provisioner["composition_root::bin::mesh_provisioner"]
    composition_root__bin__node_supervisor["composition_root::bin::node_supervisor"]
    composition_root__bin__trigger_listener["composition_root::bin::trigger_listener"]
    composition_root__gate["composition_root::gate"]
    composition_root__insights_cli["composition_root::insights_cli"]
    composition_root__mesh_provisioner["composition_root::mesh_provisioner"]
    composition_root__run["composition_root::run"]
    composition_root__sandbox_mint["composition_root::sandbox_mint"]
    composition_root__startup["composition_root::startup"]
    composition_root__supervisor["composition_root::supervisor"]
    composition_root__trigger_publisher["composition_root::trigger_publisher"]
    composition_root__account_directory --> composition_root__mesh_provisioner
    composition_root__bin__composition_root --> composition_root__startup
    composition_root__bin__mesh_provisioner --> composition_root__account_directory
    composition_root__bin__mesh_provisioner --> composition_root__mesh_provisioner
    composition_root__bin__node_supervisor --> composition_root__supervisor
    composition_root__bin__trigger_listener --> composition_root__trigger_publisher
    composition_root__run --> composition_root__startup
    composition_root__startup --> composition_root__sandbox_mint
```

## Fan table

| module | fan_out | fan_in | instability |
|---|---|---|---|
| composition_root | 0 | 0 | 0.000 |
| composition_root::account_directory | 12 | 1 | 0.923 |
| composition_root::bin::composition_root | 3 | 0 | 1.000 |
| composition_root::bin::mesh_provisioner | 9 | 0 | 1.000 |
| composition_root::bin::node_supervisor | 2 | 0 | 1.000 |
| composition_root::bin::trigger_listener | 8 | 0 | 1.000 |
| composition_root::gate | 3 | 0 | 1.000 |
| composition_root::insights_cli | 3 | 0 | 1.000 |
| composition_root::mesh_provisioner | 11 | 2 | 0.846 |
| composition_root::run | 75 | 0 | 1.000 |
| composition_root::sandbox_mint | 5 | 1 | 0.833 |
| composition_root::startup | 9 | 2 | 0.818 |
| composition_root::supervisor | 1 | 1 | 0.500 |
| composition_root::trigger_publisher | 13 | 1 | 0.929 |
