// Run: bun --no-install shared/test/rulings.test.ts
// The owner's standing "Always allow" rulings: stored per repo under ~/.agentic/rulings (ASD_RULINGS_DIR), never for a refused category.
import { mkdtempSync, readFileSync, readdirSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const tmp = mkdtempSync(join(tmpdir(), "rulings-"))
process.env.ASD_RULINGS_DIR = join(tmp, "rulings")
const { addRuling, readRulings, removeRuling, rulingRefused, hasRulings } = await import("../questions/rulings.ts")
const { checkRule } = await import("../questions/runstore.ts")
const root = join(import.meta.dir, "..", "..")

const fails: string[] = []
let n = 0
const check = (name: string, ok: boolean) => { n++; ok || fails.push(name) }

const rule = "Bash(limactl shell vm -- docker compose build *)"
check("empty at first", readRulings("repo-a").length === 0 && !hasRulings("repo-a"))
check("add: stored", addRuling("repo-a", { rule, by: "owner (dashboard)", question: "repo-a.W-1.Q3", run: "repo-a.W-1", tool: "Bash", target: "limactl shell vm -- docker compose build x" }) === "")
const r0 = readRulings("repo-a")[0]
check("the file carries the exact rule, who, when and which question", r0.rule === rule && r0.by === "owner (dashboard)" && r0.question === "repo-a.W-1.Q3" && /^\d{4}-\d\d-\d\dT/.test(r0.ts) && r0.kind === "allow")
addRuling("repo-a", { rule, by: "x", question: "q", run: "r", tool: "Bash", target: "t" })
check("the same rule is stored once", readRulings("repo-a").length === 1)
check("keyed per repo", readRulings("repo-b").length === 0 && readdirSync(process.env.ASD_RULINGS_DIR!).join() === "repo-a.json")
for (const bad of ["Bash(gh pr *)", "Bash(gh pr merge 12)", "Bash(git push --force *)", "Bash(git push -f *)", "Bash(git push origin production)", "Bash(git push *)", "Bash(gh api *)",
  "Read(~/.ssh/id_rsa)", "Bash(cat ~/.aws/credentials)", "Bash(*)", "Bash(python3 *)"]) {
  check("refused, never stored: " + bad, (rulingRefused(bad) !== "" || checkRule(bad) !== "") && addRuling("repo-a", { rule: bad, by: "o", question: "", run: "", tool: "Bash", target: "" }) !== "")
}
check("a safe prefix rule passes checkRule", checkRule("Bash(ssh vm-test *)") === "" && checkRule("Bash(python3 scripts/x.py --y)") === "")
check("nothing refused was stored", readRulings("repo-a").length === 1)
check("remove: gone, and a second remove says so", removeRuling("repo-a", rule) && !removeRuling("repo-a", rule) && readRulings("repo-a").length === 0)
const server = readFileSync(join(root, "shared/questions/server.ts"), "utf8"), page = readFileSync(join(root, "shared/questions/page.ts"), "utf8")
check("server: Always allow stores the ruling; GET and DELETE /api/rulings/<repo> exist; the dossier payload lists them",
  server.includes("addRuling(run.repo") && /\/api\\\/rulings\\\/\(\[\\w\.-\]\+\)/.test(server) && server.includes('req.method === "DELETE"') && server.includes("rulings: readRulings(run.repo)"))
check("page: the rulings are listed with a Remove button", page.includes("function rulingsView") && page.includes("'data-act': 'rmrule'") && page.includes("act === 'rmrule'"))
console.log(fails.length ? "FAIL: " + fails.join("; ") : `PASS (${n} cases)`)
process.exit(fails.length ? 1 : 0)
