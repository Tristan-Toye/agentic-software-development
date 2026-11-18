// Worker side of scanpool.ts: runs one rescan per message and posts its result back. Its module caches (transcript
// cwds, git blobs, main checkouts) live here, across scans.
import { buildRuns } from "./runs.ts"
import { buildStubsWithRoots } from "./stubs.ts"

declare var self: Worker

self.onmessage = async (ev: MessageEvent) => {
  const { id, kind, questions, env } = ev.data
  for (const k of Object.keys(process.env)) if (k.startsWith("ASD_") && !(k in env)) delete process.env[k]
  Object.assign(process.env, env)
  try {
    const value = kind === "runs" ? await buildRuns(questions) : await buildStubsWithRoots(questions)
    self.postMessage({ id, ok: true, value })
  } catch (e) {
    self.postMessage({ id, ok: false, error: String((e as Error)?.stack ?? e) })
  }
}
