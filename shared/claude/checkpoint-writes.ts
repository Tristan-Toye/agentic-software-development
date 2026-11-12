// What a Claude Code phase_checkpoint does besides recording: append its `log` lines to the
// dossier's ## Build log and post its `note` to the owner. Both go through the scripts that
// already own those writes, so there is one lock and one format:
//   `dossier_edit.py log <dossier> <line>...`  (flock, skips a line already present)
//   `ask_owner.py note --root R --dossier ID --stage S --text T`  (the dashboard's and Slack's source)
// opencode does not call this: the fields are Claude-only (shared/tools.ts `hosts`).
import { existsSync, readFileSync, realpathSync } from "node:fs"
import { spawnSync } from "node:child_process"
import { basename, dirname, isAbsolute, join, resolve } from "node:path"

const PLUGIN_ROOT = join(dirname(realpathSync(import.meta.path)), "..", "..")

export type CheckpointWrites = { dossier?: string; log?: string[]; note?: string | boolean }

// The dossier's id (its front matter `id:`, else the file name) and the repo root it sits in
// (the directory above `.discovery/dossiers/`, which is the worktree for a worktree's copy).
export function dossierIdentity(dossier: string, projectDir: string): { id: string; root: string } {
  let id = basename(dossier).replace(/\.md$/, "")
  try {
    const m = /^id:\s*(\S+)\s*$/m.exec(readFileSync(dossier, "utf8").split("\n---")[0])
    if (m) id = m[1]
  } catch {}
  const at = dossier.lastIndexOf("/.discovery/dossiers/")
  return { id, root: at > 0 ? dossier.slice(0, at) : projectDir }
}

function py(script: string, args: string[], cwd: string): { ok: boolean; out: string } {
  const r = spawnSync("python3", [join(PLUGIN_ROOT, "scripts", script), ...args], { cwd, encoding: "utf8", timeout: 30000 })
  const out = `${r.stdout ?? ""}${r.stderr ?? ""}`.trim().split("\n").slice(-2).join(" ")
  return { ok: r.status === 0, out }
}

// The lines to add to the checkpoint's result ("" when there is nothing to write).
export function applyCheckpointWrites(w: CheckpointWrites, phase: string, summary: string, projectDir: string): string {
  const lines = (w.log ?? []).filter((l) => typeof l === "string" && l.trim())
  const note = w.note === true ? summary : typeof w.note === "string" && w.note.trim() ? w.note : ""
  if (!lines.length && !note) return ""
  if (!w.dossier) return "\n\nbuild log and note NOT written: pass `dossier` (the live dossier's path) with `log` or `note`."
  const dossier = isAbsolute(w.dossier) ? w.dossier : resolve(projectDir, w.dossier)
  if (!existsSync(dossier)) return `\n\nbuild log and note NOT written: no dossier at ${dossier}.`
  const out: string[] = []
  if (lines.length) {
    const r = py("dossier_edit.py", ["log", dossier, ...lines], projectDir)
    out.push(r.ok ? `build log: ${r.out.replace(/^dossier_edit:\s*/, "")}` : `build log NOT written: ${r.out}`)
  }
  if (note) {
    const { id, root } = dossierIdentity(dossier, projectDir)
    const r = py("ask_owner.py", ["note", "--root", root, "--dossier", id, "--stage", phase, "--text", note], projectDir)
    out.push(r.ok ? "owner note: posted" : `owner note NOT posted: ${r.out}`)
  }
  return "\n\n" + out.join("\n")
}
