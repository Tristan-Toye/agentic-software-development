// Run: bun --no-install opencode/plugins/test/provider-fifo.test.ts
// Peak in-flight per expected lane: pools share one queue, a `models` entry beats a pool,
// unlisted models fall back to the host lane.
import { join } from "node:path"

process.env.OPENCODE_FIFO_CONFIG = join(import.meta.dir, "provider-fifo.test.yaml")
const lane: Record<string, string> = {
  "glm-5.3": "frontier",
  "minimax-m3": "frontier",
  "ds-flash": "core",
  "mimo-flash": "core",
  solo: "solo",
  other: "host",
}
const inflight: Record<string, number> = {}
const peak: Record<string, number> = {}
globalThis.fetch = (async (_: unknown, init: RequestInit) => {
  const g = lane[JSON.parse(init.body as string).model]
  inflight[g] = (inflight[g] ?? 0) + 1
  peak[g] = Math.max(peak[g] ?? 0, inflight[g])
  await Bun.sleep(30)
  inflight[g]--
  return new Response("ok")
}) as typeof fetch

const { ProviderFifoPlugin } = await import(join(import.meta.dir, "..", "provider-fifo.ts"))
await ProviderFifoPlugin({} as never)

const models = Object.keys(lane)
await Promise.all(
  Array.from({ length: 36 }, (_, i) =>
    fetch("https://api.cheapestinference.com/v1/chat", {
      method: "POST",
      body: JSON.stringify({ model: models[i % models.length] }),
    }).then((r) => r.text()),
  ),
)
console.log(JSON.stringify(peak))
const want: Record<string, number> = { frontier: 1, core: 2, solo: 4, host: 2 }
const bad = Object.entries(want).filter(([k, v]) => peak[k] !== v)
console.log(bad.length ? `FAIL ${JSON.stringify(bad)}` : "PASS")
process.exit(bad.length ? 1 : 0)
