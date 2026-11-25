// The main-checkout lock on the dashboard: the notes overlay readers apply, and the dirty-main warning (fake git).
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { dirtyMains, type Git } from "../questions/mainlock.ts"
import { dossiersIn, isStackCheckout, notesFor, overlayNotes, readDossier } from "../questions/runs.ts"

const fails: string[] = []
const check = (name: string, ok: boolean, extra = "") => { console.log((ok ? "PASS " : "FAIL ") + "mainlock: " + name); if (!ok) fails.push(name + (extra ? " — " + extra : "")) }

const dossier = "---\nid: W-77\nstatus: ready\ntitle: x\n---\n\n## Problem\n\np\n\n## Build log\n\n- a\n\n## After\n\nz\n"
const notes = "---\nid: W-77\nstatus: done\npr: 12\n---\n\n## Build log\n\n- a\n- b\n"
const o = overlayNotes(dossier, notes)
check("overlay: a notes key replaces the dossier's, a new key is added, id is kept",
  o.includes("status: done") && !o.includes("status: ready") && o.includes("pr: 12") && o.startsWith("---\nid: W-77\n"), o)
check("overlay: a new Build log line lands in the Build log, a line already there is not repeated",
  o.split("\n- a\n").length === 2 && o.indexOf("- b") > o.indexOf("## Build log") && o.indexOf("- b") < o.indexOf("## After"), o)
check("overlay: no front matter in the notes leaves the text as it is", overlayNotes(dossier, "junk") === dossier)

const root = mkdtempSync(join(tmpdir(), "mainlock-"))
mkdirSync(join(root, ".discovery", "dossiers"), { recursive: true })
mkdirSync(join(root, ".discovery", "notes"), { recursive: true })
writeFileSync(join(root, ".discovery", "dossiers", "W-77-x.md"), dossier)
writeFileSync(join(root, ".discovery", "dossiers", "W-78-y.md"), dossier.replace("W-77", "W-78"))
writeFileSync(join(root, ".discovery", "notes", "W-77-x.md"), notes)
const ds = dossiersIn(root)
check("readers: dossiersIn lays the notes over the dossier", ds.find((d) => d.front.id === "W-77")?.front.status === "done" && ds.find((d) => d.front.id === "W-78")?.front.status === "ready")
check("readers: readDossier too", readDossier(join(root, ".discovery", "dossiers", "W-77-x.md")).includes("status: done"))
check("notesFor: only for a file under dossiers/ with a notes file", notesFor(join(root, ".discovery", "dossiers", "W-78-y.md")) === null && !!notesFor(join(root, ".discovery", "dossiers", "W-77-x.md")))

const fake = (m: Record<string, { files: string; status: string }>): Git => async (r, args) =>
  args[0] === "ls-files" ? { code: 0, stdout: m[r].files } : { code: 0, stdout: m[r].status }
const got = await dirtyMains({ a: "/a", b: "/b", c: "/c" }, fake({
  "/a": { files: ".discovery/dossiers/W-1-x.md\n", status: " M .discovery/dossiers/W-1-x.md\n M src/x.rs\n" },
  "/b": { files: "", status: " M .discovery/dossiers/W-1-x.md\n" },
  "/c": { files: ".discovery/dossiers/W-1-x.md\n", status: "" },
}))
check("dirty main: a committed-mode checkout with tracked changes is listed with its files; uncommitted mode and clean ones are not",
  got.length === 1 && got[0].repo === "a" && got[0].files.join(",") === ".discovery/dossiers/W-1-x.md,src/x.rs", JSON.stringify(got))

if (fails.length) { console.log(fails.join("\n")); process.exit(1) }

check("runs: the stack checkout <main>-stack (a clone beside its main checkout) is never a dossier root; other siblings are", (() => {
  const have = new Set(["/r/config-harness/.git", "/r/config-harness-stack/.git", "/r/config-harness-W-1/.git", "/r/lone-stack/.git"])
  const ex = (p: string) => have.has(p)
  return isStackCheckout("/r/config-harness-stack", ex) && isStackCheckout("/r/config-harness-stack/", ex)
    && !isStackCheckout("/r/config-harness-W-1", ex) && !isStackCheckout("/r/config-harness", ex)
    && !isStackCheckout("/r/lone-stack", ex)
})())
