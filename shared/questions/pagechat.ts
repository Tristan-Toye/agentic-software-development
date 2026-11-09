// The dashboard's chat with the owner's agent: the same agent as the Slack DM (dmagent.ts: references/dm-agent.md,
// the runs MCP server, the same allowed tools), with the dashboard as its channel instead of Slack. A new conversation
// is a new session, like a new top-level DM; a message in a conversation resumes that session, like a thread reply.
// Each conversation is one file, `~/.agentic/chat/<id>.json` (`ASD_CHAT_DIR`); its session sits in the DM agent's
// store under `page:<id>`. Contract: references/owner-questions.md ("The dashboard chat").
import { mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { askAgent, type DmDeps } from "./dmagent.ts"

export type ChatMsg = { id: string; from: "owner" | "agent"; text: string; ts: string; pending?: boolean }
export type Chat = { id: string; title: string; started: string; last: string; messages: ChatMsg[] }

export const CHANNEL = "page"
export const chatDir = (): string => process.env.ASD_CHAT_DIR || join(process.env.HOME || homedir(), ".agentic", "chat")
const ID = /^[a-z0-9]{6,40}$/

let seq = 0
/** A sortable id: time first, then a counter, so two ids minted in one millisecond differ. */
export const newId = (): string => Date.now().toString(36).padStart(9, "0") + (seq++ % 1296).toString(36).padStart(2, "0")

function file(id: string): string {
  if (!ID.test(id)) throw new Error("bad conversation id")
  return join(chatDir(), `${id}.json`)
}
export function readChat(id: string): Chat | null {
  try { return JSON.parse(readFileSync(file(id), "utf8")) } catch { return null }
}
function writeChat(c: Chat): void {
  mkdirSync(chatDir(), { recursive: true })
  const f = file(c.id)
  writeFileSync(f + ".tmp", JSON.stringify(c, null, 1))
  renameSync(f + ".tmp", f)
}
/** Every conversation, newest activity first, without the messages. */
export function listChats(): { id: string; title: string; started: string; last: string; turns: number; pending: boolean }[] {
  let names: string[] = []
  try { names = readdirSync(chatDir()).filter((n) => n.endsWith(".json")) } catch { return [] }
  const out = []
  for (const n of names) {
    const c = readChat(n.slice(0, -5))
    if (!c) continue
    out.push({ id: c.id, title: c.title, started: c.started, last: c.last, turns: c.messages.filter((m) => m.from === "owner").length, pending: c.messages.some((m) => m.pending) })
  }
  return out.sort((a, b) => (a.last < b.last ? 1 : -1))
}

// One writer per file: a turn's post and update and an owner's message never interleave a read-modify-write.
const locks = new Map<string, Promise<unknown>>()
function edit<T>(id: string, fn: (c: Chat | null) => { c: Chat; out: T }): Promise<T> {
  const prev = locks.get(id) ?? Promise.resolve()
  const next = prev.then(() => { const { c, out } = fn(readChat(id)); writeChat(c); return out })
  locks.set(id, next.catch(() => {}))
  return next
}

export type ChatBase = Omit<DmDeps, "post" | "update">

function writeAgent(root: string, mid: string, text: string, pending: boolean): Promise<null> {
  return edit(root, (c) => {
    if (!c) throw new Error(`no conversation ${root}`)
    const ts = new Date().toISOString()
    const m = c.messages.find((x) => x.id === mid)
    if (m) { m.text = text; m.ts = ts; if (pending) m.pending = true; else delete m.pending }
    else c.messages.push({ id: mid, from: "agent", text, ts, ...(pending ? { pending: true } : {}) })
    c.last = ts
    return { c, out: null }
  })
}

/**
 * The owner says `text`: in conversation `id`, or (no id) in a new one. Resolves to the conversation id once the
 * owner's message is stored; the agent's answer lands in the file when its turn ends (`done`, for tests). Turns in
 * one conversation run one at a time (dmagent.ts). `changed` announces every write, for the event stream.
 */
export async function say(text: string, id: string | undefined, base: ChatBase, changed: () => void): Promise<{ id: string; done: Promise<void> }> {
  const fresh = !id
  const root = id || newId()
  const now = new Date().toISOString()
  const msgId = newId()
  await edit(root, (c) => {
    if (!c && !fresh) throw new Error("no such conversation")
    const chat: Chat = c ?? { id: root, title: text.replace(/\s+/g, " ").trim().slice(0, 80), started: now, last: now, messages: [] }
    chat.messages.push({ id: msgId, from: "owner", text, ts: now })
    chat.last = now
    return { c: chat, out: null }
  })
  changed()
  // The Slack shape: post() is the "Looking into it…" placeholder in the thread, update() replaces it with the answer.
  const deps: DmDeps = {
    ...base,
    post: async (_ch, _root, t) => { const mid = newId(); await writeAgent(root, mid, t, true); changed(); return mid },
    update: async (_ch, mid, t) => { await writeAgent(root, mid, t, false); changed() },
  }
  const done = askAgent({ channel: CHANNEL, ts: fresh ? root : msgId, threadTs: fresh ? undefined : root, text }, deps)
  return { id: root, done }
}
