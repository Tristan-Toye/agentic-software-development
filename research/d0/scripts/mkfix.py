import json
OUT="research/d0/fixtures/"  # run from the repo root
def src(path,line=None,end=None,note=None):
    e={"tier":"source","path":path}
    if line: e["line"]=line
    if end: e["end_line"]=end
    if note: e["note"]=note
    return e
def inf(note): return {"tier":"inferred","note":note}
H="crates/harness/src/tools/step.rs"
meta=lambda view,title:{"schema_version":"0.1.0","view":view,"title":title,
 "meta":{"change":"W-145","repo":"config-harness","commit":"56034559","branch":"development","generated_by":"hand",
 "tools":[],"note":"Hand-built D0 fixture. Source anchors resolve at the commit above (after-state, W-145 merged)."}}
def N(id,label,status,ev,kind="component",**k):
    d={"id":id,"label":label,"kind":kind,"status":status,"evidence":ev}; d.update(k); return d
def E(id,s,t,status,ev,kind="uses",resolution="static",**k):
    d={"id":id,"source":s,"target":t,"kind":kind,"status":status,"resolution":resolution,"evidence":ev}; d.update(k); return d
# ---------- L0
l0=meta("L0","W-145 L0: features")
l0["nodes"]=[
 N("H-01","H-01 Tool layer core","changed",[src(H,1732,None,"run_calls groups consecutive read-only calls"),inf("feature id from FEATURE-BOOK.md:58; feature-to-code map does not exist, so the link is by hand")],"feature"),
 N("H-05","H-05 Gated git tools","changed",[src("crates/harness/src/tools/git_tools.rs",273,None,"GIT_OPTIONAL_LOCKS=0 for Reading git tools"),inf("feature id from FEATURE-BOOK.md:59")],"feature"),
 N("H-04","H-04 Risk gate and IAM","context",[inf("Pipeline::decide still runs per call, in call order")],"feature"),
 N("H-09","H-09 Inbox and relay","context",[inf("relay and ask_human calls end the loop and are never grouped")],"feature"),
 N("H-06","H-06 Checkpoint schema","context",[inf("journal Result entry written per settled call; a dropped group re-runs unsettled calls")],"feature"),
 N("PF-01","PF-01 Telemetry","context",[inf("tool.call span now also covers the wait for other runs of the group")],"feature"),
 N("SET","Runtime settings","added",[src("crates/protocol/src/schema/settings.rs",222,None,"new field tool_read_concurrency"),inf("no feature id owns runtime settings in FEATURE-BOOK; placeholder name")],"feature"),
]
l0["edges"]=[
 E("e1","H-01","H-04","context",[inf("decide phase calls Pipeline::decide")],"depends"),
 E("e2","H-01","H-09","context",[inf("relay ends the loop")],"depends"),
 E("e3","H-01","H-06","context",[inf("journal write in the settle phase")],"depends"),
 E("e4","H-01","PF-01","context",[inf("tool.call span")],"depends"),
 E("e5","H-01","H-05","changed",[inf("git read tools run inside a group")],"depends"),
 E("e6","H-01","SET","added",[src(H,81,None,"StepPolicy::from_settings reads tool_read_concurrency")],"depends"),
]
# ---------- L1
l1=meta("L1","W-145 L1: one response, before and after (merged flow)")
lanes=[{"id":"lane-step","label":"Step (harness)"},{"id":"lane-git","label":"Git tools (harness)"},{"id":"lane-set","label":"Settings (protocol)"}]
l1["lanes"]=lanes
L1N=[
 N("s1","Loop over the calls of one response","context",[src(H,1732,None,"run_calls")],"step",lane="lane-step",order=1,detail={"before":"for loop, awaits each run_one_call","after":"while loop over call_index, splits runs of groupable calls"}),
 N("s2","Mark groupable calls from the registry","added",[src(H,1803,None,"is_groupable"),src("crates/harness/src/tools/pipeline.rs",638,None,"Pipeline::reads_only")],"step",lane="lane-step",order=2,detail={"after":"a group is 2 or more consecutive read-only calls; none when read_concurrency is 1"}),
 N("s3","Call outside a group: run_one_call","context",[src(H,2067,None,"run_one_call, same code path as before")],"step",lane="lane-step",order=3,detail={"before":"decide, run, settle back to back","after":"unchanged"}),
 N("s4","run_group: decide each call, in call order","added",[src(H,1816,None,"run_group"),src(H,2112,None,"decide_call")],"step",lane="lane-step",order=4,detail={"after":"rules, call cap, Pipeline::decide per call; sequential"}),
 N("s5","Run phases: Tool::run","changed",[src(H,1864,None,"run_slots_concurrently")],"step",lane="lane-step",order=5,detail={"before":"one at a time","after":"up to read_concurrency at once on the step task, no tokio::spawn"}),
 N("s6","Settle phase, in call order","changed",[src(H,1899,None,"settle_slots"),src(H,2352,None,"settle_call")],"step",lane="lane-step",order=6,detail={"before":"per call, right after its run","after":"after the whole group ran, call order; duration_ms = ended - started"}),
 N("s7","Boundary reminder from the kept tally value","changed",[src(H,2918,None,"count_call"),src(H,2941,None,"append_boundary_reminder")],"step",lane="lane-step",order=7,detail={"after":"decide phase keeps calls_since_boundary; settle phase gives it to the reminder"}),
 N("s8","Git read tools set GIT_OPTIONAL_LOCKS=0","added",[src("crates/harness/src/tools/git_tools.rs",273,None,"OPTIONAL_LOCKS_KEY"),src("crates/harness/src/tools/git_tools.rs",983,None,"added to env")],"step",lane="lane-git",order=8,detail={"after":"no optional index lock for concurrent git reads"}),
 N("s9","Setting tool_read_concurrency (1 to 64, default 4)","added",[src("crates/protocol/src/schema/settings.rs",222,None,"field"),src("crates/protocol/migrations/0063_tool_read_concurrency.sql",5,6,"column and CHECK")],"step",lane="lane-set",order=9),
 N("s10","StepPolicy.read_concurrency from the setting","added",[src(H,72,None,"field"),src(H,93,None,"from_settings, floor 1")],"step",lane="lane-set",order=10),
]
l1["nodes"]=L1N
fl=lambda i,a,b,st,ev,**k:E(i,a,b,st,ev,"flow",**k)
l1["edges"]=[
 fl("f1","s1","s2","added",[src(H,1745,None,"groupable computed before any decide phase")]),
 fl("f2","s2","s3","context",[src(H,1745)],label="single call"),
 fl("f3","s2","s4","added",[src(H,1756,None,"run_group called for run_len >= 2")],label="group"),
 fl("f4","s4","s5","added",[inf("run phase follows the last decide of the group")]),
 fl("f5","s5","s6","changed",[src(H,1816,None,"run_group runs run_slots_concurrently then settle_slots")]),
 fl("f6","s6","s7","changed",[src(H,2941)]),
 fl("f7","s3","s7","context",[src(H,2067)]),
 fl("f8","s10","s2","added",[src(H,81)],label="policy"),
 fl("f9","s9","s10","added",[src("crates/protocol/src/schema/settings.rs",1496,None,"in the settings select list")]),
 fl("f10","s8","s5","added",[inf("env applies to every git read run inside the group")]),
]
# ---------- L2
l2=meta("L2","W-145 L2: crates and components")
l2["groups"]=[
 {"id":"g-harness","label":"harness","status":"changed","evidence":[src("crates/harness/Cargo.toml",1)]},
 {"id":"g-protocol","label":"protocol","status":"changed","evidence":[src("crates/protocol/Cargo.toml",1)]},
 {"id":"g-testsupport","label":"test-support","status":"changed","evidence":[src("crates/test-support/src/fixture_values.rs",263,None,"full_row gets tool_read_concurrency: 4")]},
 {"id":"g-platform","label":"platform","status":"context","evidence":[src("crates/platform/tests/tool_read_concurrency_settings_test.rs",1,None,"test only")]},
 {"id":"g-croot","label":"composition-root","status":"context","evidence":[src("crates/composition-root/src/run.rs",2700,None,"calls StepPolicy::from_settings, no edit")]},
]
l2["nodes"]=[
 N("c-step","tools::step (run_group)","changed",[src(H,1816)],group="g-harness",kind="module"),
 N("c-pipe","tools::pipeline (reads_only)","changed",[src("crates/harness/src/tools/pipeline.rs",638)],group="g-harness",kind="module"),
 N("c-git","tools::git_tools","changed",[src("crates/harness/src/tools/git_tools.rs",273)],group="g-harness",kind="module"),
 N("c-set","schema::settings","added",[src("crates/protocol/src/schema/settings.rs",222)],group="g-protocol",kind="module"),
 N("c-mig","migration 0063","added",[src("crates/protocol/migrations/0063_tool_read_concurrency.sql",5)],group="g-protocol",kind="module"),
 N("c-fix","fixture_values::full_row","changed",[src("crates/test-support/src/fixture_values.rs",263)],group="g-testsupport",kind="module"),
 N("c-plat","platform (settings IT)","context",[src("crates/platform/tests/tool_read_concurrency_settings_test.rs",1)],group="g-platform",kind="crate"),
 N("c-run","run.rs","context",[src("crates/composition-root/src/run.rs",2700)],group="g-croot",kind="module"),
 N("c-fault","fault","context",[src("crates/fault/Cargo.toml",1)],kind="crate"),
 N("c-timing","step-timing","context",[src("crates/step-timing/Cargo.toml",1)],kind="crate"),
 N("c-sbx","sandbox-tool","context",[src("crates/sandbox-tool/Cargo.toml",1)],kind="crate"),
 N("c-graph","graph-runtime","context",[src("crates/graph-runtime/Cargo.toml",1)],kind="crate"),
]
cm="cargo metadata: workspace dependency in "
l2["edges"]=[
 E("d1","c-step","c-pipe","changed",[src(H,1803,None,"is_groupable calls reads_only")],"calls"),
 E("d2","c-step","c-set","added",[src(H,93,None,"from_settings reads the row field")],"uses"),
 E("d3","c-set","c-mig","added",[src("crates/protocol/src/schema/settings.rs",220,None,"doc comment names the column")],"documents"),
 E("d4","c-git","c-step","context",[inf("git read tool run is polled by run_slots_concurrently")],"called_by",resolution="dispatch"),
 E("d5","c-run","c-step","context",[src("crates/composition-root/src/run.rs",2700)],"uses"),
 E("d6","c-fix","c-set","changed",[src("crates/test-support/src/fixture_values.rs",263)],"uses"),
 E("d7","g-harness","c-fault","context",[src("crates/harness/Cargo.toml",83,None,cm+"harness -> fault")],"depends"),
 E("d8","g-harness","c-timing","context",[src("crates/harness/Cargo.toml",86,None,cm+"harness -> step-timing")],"depends"),
 E("d9","g-harness","c-sbx","context",[src("crates/harness/Cargo.toml",87,None,cm+"harness -> sandbox-tool")],"depends"),
 E("d10","g-harness","g-protocol","context",[src("crates/harness/Cargo.toml",82,None,cm+"harness -> protocol")],"depends"),
 E("d11","g-croot","c-graph","context",[src("crates/composition-root/Cargo.toml",45,None,cm+"composition-root -> graph-runtime")],"depends"),
 E("d12","g-platform","g-protocol","context",[src("crates/platform/Cargo.toml",27,None,cm+"platform -> protocol")],"depends"),
 E("d13","g-croot","g-harness","context",[src("crates/composition-root/Cargo.toml",39,None,cm+"composition-root -> harness")],"depends"),
]
for name,d in [("l0",l0),("l1",l1),("l2",l2)]:
    d.setdefault("groups",[]); 
    json.dump(d,open(OUT+f"w145-{name}.json","w"),indent=1,ensure_ascii=False)
print("ok")
