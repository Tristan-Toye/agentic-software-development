// Run controls: eligibility (pure) and the runner command. Contract: references/owner-questions.md (Dashboard), references/runner.md.
import { join } from "node:path"
import type { RunnerState } from "./runstore.ts"

export type Ctl = { show: boolean; ok: boolean; reason: string; command?: string }
export type Controls = { start: Ctl; resume: Ctl; stop: Ctl; "fix-deferred": Ctl }

/** The dossier and the stop target of a run. A dossier a /plan run of a deferred item minted (W-155 from W-145 D-1) still
 *  runs under its source: Resume plans W-145 D-1 again, never "W-155 D-1", and Stop names the whole key. */
export function runTarget(repo: string, id: string, runKey?: string, action?: string): { dossier: string; stop: string } {
  if (action === "start") return { dossier: id, stop: id }   // Start builds this dossier; only Resume and Stop act on the run that minted it
  const k = runKey?.startsWith(repo + ".") ? runKey.slice(repo.length + 1) : ""
  const m = k.match(/^(.+)\.plan-D-\d+$/)
  return m ? { dossier: m[1], stop: k } : { dossier: id, stop: id }
}

/** Dossier ids named in a `blocked_by` front-matter value such as `[W-1, W-2]`. */
export function blockedByOf(v: string | undefined): string[] {
  return [...new Set((v ?? "").match(/\b[A-Z][A-Z0-9]*-\d+\b/g) ?? [])]
}
const active = (r: RunnerState | null): boolean => r?.state === "running" || r?.state === "waiting" || r?.state === "paused" || r?.state === "queued"

/** Which of Start, Resume and Stop a dossier offers. `statusOf` gives the status of a blocker id, if known. */
export function controlsFor(d: { status: string; blockedBy: string[]; runner: RunnerState | null; deferredOpen?: number }, statusOf: (id: string) => string | undefined): Controls {
  const open = d.blockedBy.filter((id) => statusOf(id) !== "done")
  const stopped = d.runner?.state === "stopped" || d.runner?.state === "failed"
  // Start runs the dossier's next flow: a planned dossier is planned (/plan), a ready one built (/work-on).
  const startable = d.status === "ready" || d.status === "planned"
  const start: Ctl = { show: startable && !active(d.runner) && !stopped, ok: d.status === "planned" || open.length === 0, reason: d.status === "ready" && open.length ? `blocked by ${open.join(", ")}` : "", command: d.status === "planned" ? "plan" : "work-on" }
  // Resume picks up the flow that stopped, whichever it was.
  const resume: Ctl = { show: d.status !== "done" && d.status !== "dropped" && stopped, ok: !!d.runner?.root, reason: d.runner?.root ? "" : "no root recorded in the runner state", command: d.runner?.command || "work-on" }
  const stop: Ctl = { show: active(d.runner), ok: true, reason: "" }
  /* Fix all deferred: a finished build (pr or done) with open ledger items and no live runner. */
  const fix: Ctl = { show: (d.status === "pr" || d.status === "done") && (d.deferredOpen ?? 0) > 0 && !active(d.runner), ok: true, reason: "" }
  return { start, resume, stop, "fix-deferred": fix }
}

/** The runner script: `ASD_RUNNER_BIN` overrides it (tests). The plugin root is two levels above this file. */
export const runnerScript = (): string => process.env.ASD_RUNNER_BIN || join(import.meta.dir, "..", "..", "scripts", "run_dossier.py")

/** Run `python3 <runner> <args>`; the one-line outcome is the first line of stdout, else the last line of stderr. */
export async function runRunner(args: string[], timeoutMs = 30_000): Promise<{ ok: boolean; output: string }> {
  try {
    const p = Bun.spawn(["python3", runnerScript(), ...args], { stdout: "pipe", stderr: "pipe", stdin: "ignore" })
    const t = setTimeout(() => p.kill(), timeoutMs)
    const [out, err, code] = await Promise.all([new Response(p.stdout).text(), new Response(p.stderr).text(), p.exited])
    clearTimeout(t)
    const lines = (s: string) => s.split("\n").map((l) => l.trim()).filter(Boolean)
    const line = (code === 0 ? lines(out)[0] ?? lines(err).pop() : lines(err).pop() ?? lines(out)[0]) ?? (code === 0 ? "ok" : `exit ${code}`)
    return { ok: code === 0, output: line.slice(0, 300) }
  } catch (e) {
    return { ok: false, output: (e as Error).message.slice(0, 300) }
  }
}
