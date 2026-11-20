// The active-repos filter (shared/questions/activerepos.ts): no file shows everything, a file shows only its repos.
import { mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { activeRepos, repoShown } from "../questions/activerepos.ts"

const fails: string[] = []
const check = (name: string, ok: boolean) => { console.log((ok ? "PASS " : "FAIL ") + "activerepos: " + name); if (!ok) fails.push(name) }

const dir = mkdtempSync(join(tmpdir(), "activerepos-"))
const f = join(dir, "active-repos.json")
check("no file: null and every repo shown", activeRepos(f) === null && repoShown("anything", activeRepos(f)))
writeFileSync(f, JSON.stringify({ repos: { "config-harness": "/x/ch", podcast: "/x/p" } }))
const a = activeRepos(f)
check("the file's repos are read", a?.size === 2 && a.has("config-harness"))
check("an active repo is shown", repoShown("podcast", a))
check("a repo not in the file is hidden", !repoShown("other", a))
writeFileSync(f, "{nope")
check("an unreadable file shows everything", activeRepos(f) === null)
writeFileSync(f, JSON.stringify({ repos: ["a"] }))
check("a wrong shape shows everything", activeRepos(f) === null)
if (fails.length) { console.log(`${fails.length} failed`); process.exit(1) }
