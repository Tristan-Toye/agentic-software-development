// Bun test preload (shared/bunfig.toml): point every per-owner state file at an empty temp dir, so no test (nor the
// server it spawns, which inherits process.env) reads or writes the real ~/.agentic. Without this, a host
// ~/.agentic/active-repos.json that does not list a test's fixture repo hides it ("no such dossier").
// A test that needs a specific file still sets its own variable after this runs.
import { mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const dir = mkdtempSync(join(tmpdir(), "asd-hermetic-"))
const set = (k: string, v: string): void => { if (!process.env[k]) process.env[k] = join(dir, v) }
set("ASD_ACTIVE_REPOS_FILE", "active-repos.json")
set("ASD_DELIVERY_FLOWS", "delivery-flows.json")
set("ASD_DRIVERS_FILE", "drivers.json")
set("ASD_ACCOUNTS", "accounts.json")
set("ASD_ACCOUNT_STATE", "accounts-state.json")
set("ASD_USAGE_DIR", "usage")
set("ASD_STUB_AUTOMERGE_FILE", "stub-auto-merge.json")
set("ASD_RULINGS_DIR", "rulings")
set("ASD_CHAT_DIR", "chat")
set("ASD_DM_AGENT_FILE", "dm-agent.json")
set("ASD_RUNS_DIR", "runs")
set("ASD_QUESTIONS_DIR", "questions")
set("ASD_OPEN_WORK_DIR", "open-work")
