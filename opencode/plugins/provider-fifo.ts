import type { Plugin } from "@opencode-ai/plugin"
import { readFileSync, realpathSync, statSync } from "node:fs"
import { dirname, join } from "node:path"

// Provider FIFO — makes provider requests wait their turn in a first-in, first-out queue.
//
// Some providers allow only a few requests in flight, and opencode fires them in
// parallel as soon as subagents run, so the overlapping ones fail. This plugin wraps
// globalThis.fetch: opencode's provider fetch wrapper (packages/opencode/src/provider/
// provider.ts, getSDK) calls the global fetch at request time, so every model call to a
// configured host passes through here.
//
// Limits live in provider-fifo.yaml next to the real file (the plugin is symlinked into
// ~/.config/opencode/plugins, so the path is resolved through the link). The file is
// re-read whenever it changes; OPENCODE_FIFO_CONFIG points at a different file.
//
// A slot is held until the response body is fully read (streams included), not just
// until the headers arrive, because the provider counts the whole stream as one call.
//
// opencode's headerTimeout starts before this queue, so time spent waiting counts
// against it: raise the provider's `headerTimeout` option when requests queue for long.
//
// OPENCODE_FIFO_DEBUG=1 logs queue activity to stderr.

type HostConfig = { concurrency?: number; models?: Record<string, number>; max_hold_ms?: number }
type Config = { defaults?: { max_hold_ms?: number }; hosts?: Record<string, HostConfig> }

const DEFAULT_MAX_HOLD_MS = 900_000
const debug = process.env.OPENCODE_FIFO_DEBUG === "1"
const log = (...args: unknown[]) => debug && console.error("[provider-fifo]", ...args)

const configPath =
  process.env.OPENCODE_FIFO_CONFIG ?? join(dirname(realpathSync(import.meta.path)), "provider-fifo.yaml")
let config: Config = {}
let configMtime = -1

// Re-reads the YAML when its mtime moves. A broken edit keeps the last good config.
function currentConfig(): Config {
  try {
    const mtime = statSync(configPath).mtimeMs
    if (mtime !== configMtime) {
      config = (Bun.YAML.parse(readFileSync(configPath, "utf8")) as Config) ?? {}
      configMtime = mtime
      log(`loaded ${configPath}`)
    }
  } catch (err) {
    if (configMtime === -1) log(`no usable config at ${configPath}:`, err)
  }
  return config
}

function positive(n: unknown, fallback: number): number {
  const v = Number(n)
  return Number.isFinite(v) && v >= 1 ? Math.floor(v) : fallback
}

type Limit = { lane: string; concurrency: number; maxHoldMs: number }

// The queue a request belongs to, or undefined when its host is not configured.
function limitFor(host: string, model: string | undefined): Limit | undefined {
  const cfg = currentConfig()
  const entry = Object.entries(cfg.hosts ?? {}).find(([h]) => host === h || host.endsWith(`.${h}`))
  if (!entry) return undefined
  const [name, hc] = entry
  const maxHoldMs = positive(hc.max_hold_ms ?? cfg.defaults?.max_hold_ms, DEFAULT_MAX_HOLD_MS)
  if (model && hc.models && model in hc.models)
    return { lane: `${name}/${model}`, concurrency: positive(hc.models[model], 1), maxHoldMs }
  return { lane: name, concurrency: positive(hc.concurrency, 1), maxHoldMs }
}

type Waiter = { grant: () => void }
type Lane = { active: number; queue: Waiter[]; limit: number }
const lanes = new Map<string, Lane>()

function abortError(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException("The operation was aborted.", "AbortError")
}

function next(lane: Lane) {
  while (lane.active < lane.limit && lane.queue.length > 0) lane.queue.shift()!.grant()
}

// Resolves with a release function once a slot is free; rejects if the signal aborts first.
function acquire(limit: Limit, signal?: AbortSignal | null): Promise<() => void> {
  let lane = lanes.get(limit.lane)
  if (!lane) lanes.set(limit.lane, (lane = { active: 0, queue: [], limit: limit.concurrency }))
  lane.limit = limit.concurrency
  const l = lane
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(abortError(signal))
    const waiter: Waiter = {
      grant: () => {
        signal?.removeEventListener("abort", onAbort)
        l.active++
        let released = false
        const watchdog = setTimeout(() => {
          log(`${limit.lane}: slot held ${limit.maxHoldMs} ms, freeing it`)
          release()
        }, limit.maxHoldMs)
        const release = () => {
          if (released) return
          released = true
          clearTimeout(watchdog)
          l.active--
          log(`${limit.lane}: released (active=${l.active}, queued=${l.queue.length})`)
          next(l)
        }
        resolve(release)
      },
    }
    const onAbort = () => {
      const i = l.queue.indexOf(waiter)
      if (i !== -1) l.queue.splice(i, 1)
      reject(abortError(signal!))
    }
    signal?.addEventListener("abort", onAbort, { once: true })
    l.queue.push(waiter)
    log(`${limit.lane}: queued (active=${l.active}, queued=${l.queue.length}, limit=${l.limit})`)
    next(l)
  })
}

// Returns a Response whose body frees the slot when it ends, errors, or is cancelled.
function releaseOnBodyEnd(res: Response, release: () => void): Response {
  if (!res.body) {
    release()
    return res
  }
  const reader = res.body.getReader()
  const body = new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const { done, value } = await reader.read()
        if (done) {
          release()
          controller.close()
        } else controller.enqueue(value)
      } catch (err) {
        release()
        controller.error(err)
      }
    },
    async cancel(reason) {
      release()
      await reader.cancel(reason).catch(() => {})
    },
  })
  const wrapped = new Response(body, { status: res.status, statusText: res.statusText, headers: res.headers })
  Object.defineProperty(wrapped, "url", { value: res.url })
  return wrapped
}

function hostOf(input: RequestInfo | URL): string | undefined {
  try {
    return new URL(input instanceof Request ? input.url : String(input)).hostname.toLowerCase()
  } catch {
    return undefined
  }
}

// The "model" field of a JSON request body; undefined for any other body.
function modelOf(init?: RequestInit): string | undefined {
  if (typeof init?.body !== "string") return undefined
  try {
    const model = JSON.parse(init.body)?.model
    return typeof model === "string" ? model : undefined
  } catch {
    return undefined
  }
}

const PATCHED = Symbol.for("opencode.provider-fifo")

export const ProviderFifoPlugin: Plugin = async () => {
  const g = globalThis as typeof globalThis & { [PATCHED]?: boolean }
  if (g[PATCHED]) return {}
  g[PATCHED] = true

  const original = globalThis.fetch
  const queued = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const host = hostOf(input)
    const limit = host ? limitFor(host, modelOf(init)) : undefined
    if (!limit) return original(input, init)
    const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined)
    const release = await acquire(limit, signal)
    log(`${limit.lane}: sending`)
    try {
      return releaseOnBodyEnd(await original(input, init), release)
    } catch (err) {
      release()
      throw err
    }
  }
  globalThis.fetch = Object.assign(queued, original) as typeof fetch
  currentConfig()
  log(`active, config ${configPath}`)
  return {}
}
