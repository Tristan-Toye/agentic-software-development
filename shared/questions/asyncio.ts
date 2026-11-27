// Async file helpers for the dashboard's hot paths. A slow disk makes every open slow; the sync calls
// (readFileSync/statSync) then stall the whole event loop, so the timers and handlers use these instead.
import { readFile, stat } from "node:fs/promises"

/** Files opened at once; bounded so 700 files never mean 700 pending opens. */
export const IO_CONCURRENCY = 16

/** `fn` over `items`, at most `limit` running at once; results keep the order of `items`. */
export async function mapLimit<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const out = new Array<R>(items.length)
  let next = 0
  const worker = async () => {
    while (next < items.length) {
      const i = next++
      out[i] = await fn(items[i])
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker))
  return out
}

/** Change signature of a file (size + mtime), or null when it cannot be statted. */
export async function fileSig(path: string): Promise<string | null> {
  try {
    const s = await stat(path)
    return `${s.size}:${s.mtimeMs}`
  } catch { return null }
}

const jsonCache = new Map<string, { sig: string; value: any }>()
/**
 * The file's parsed JSON, or null when it is missing or unreadable (never a throw), read off the main thread and kept
 * until the file's size or mtime changes. The async twin of the small sync `readJson`s on the request paths.
 */
export async function readJsonAsync(path: string): Promise<any> {
  const sig = await fileSig(path)
  if (sig === null) { jsonCache.delete(path); return null }
  const hit = jsonCache.get(path)
  if (hit && hit.sig === sig) return hit.value
  const text = await readText(path)
  let value: any = null
  try { value = text === null ? null : JSON.parse(text) } catch { value = null }
  if (text !== null) jsonCache.set(path, { sig, value })
  return value
}

/** The file's text, or null when it cannot be read. */
export async function readText(path: string): Promise<string | null> {
  try { return await readFile(path, "utf8") } catch { return null }
}
