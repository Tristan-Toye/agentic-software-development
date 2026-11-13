#!/usr/bin/env python3
"""D0: drive rust-analyzer over LSP stdio; ask call hierarchy for one function. Stdlib only."""
import json, subprocess, sys, time, os, resource, threading
root, rel, line, col = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
t0 = time.time()
p = subprocess.Popen(["rust-analyzer"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
def send(m):
    b = json.dumps(m).encode(); p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(b) + b); p.stdin.flush()
def recv():
    h = {}
    while True:
        l = p.stdout.readline()
        if not l: return None
        if l == b"\r\n": break
        k, v = l.decode().split(":", 1); h[k.lower()] = v.strip()
    return json.loads(p.stdout.read(int(h["content-length"])))
nid = [0]
quiescent = {"t": None}
def req(method, params, timeout=300):
    nid[0] += 1; i = nid[0]; send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
    while True:
        m = recv()
        if m is None: raise SystemExit("server died")
        if "method" in m and "id" in m:  # server->client request
            send({"jsonrpc": "2.0", "id": m["id"], "result": None}); continue
        if m.get("method") == "experimental/serverStatus" and m["params"].get("quiescent"): quiescent["t"] = time.time()
        if m.get("id") == i: return m
uri = "file://" + root
r = req("initialize", {"processId": os.getpid(), "rootUri": uri, "capabilities": {"experimental": {"serverStatusNotification": True}, "window": {"workDoneProgress": True}, "textDocument": {"callHierarchy": {}}}, "workspaceFolders": [{"uri": uri, "name": "ch"}]})
send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
f = os.path.join(root, rel); txt = open(f).read()
send({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {"uri": "file://" + f, "languageId": "rust", "version": 1, "text": txt}}})
# poll prepareCallHierarchy until it answers non-null (index ready)
td = {"textDocument": {"uri": "file://" + f}, "position": {"line": line - 1, "character": col - 1}}
item = None; tries = 0
while item is None and time.time() - t0 < 280:
    tries += 1
    m = req("textDocument/prepareCallHierarchy", td)
    item = (m.get("result") or [None])[0]
    if item is None: time.sleep(2)
t_prep = time.time() - t0
out = req("callHierarchy/outgoingCalls", {"item": item}); t_out = time.time() - t0
inc = req("callHierarchy/incomingCalls", {"item": item}); t_inc = time.time() - t0
def short(c, k): it = c[k]; return "%s %s:%d" % (it["name"], it["uri"].split("/crates/")[-1], it["range"]["start"]["line"] + 1)
print("item:", item["name"], item["detail"] if "detail" in item else "")
print("outgoing:", [short(c, "to") for c in out.get("result") or []])
print("incoming:", [short(c, "from") for c in inc.get("result") or []])
print("seconds: prepare_ok=%.1f outgoing=%.1f incoming=%.1f tries=%d" % (t_prep, t_out, t_inc, tries))
ru = resource.getrusage(resource.RUSAGE_CHILDREN)
print("note: quiescent seen:", quiescent["t"] is not None)
send({"jsonrpc": "2.0", "id": 9999, "method": "shutdown"}); send({"jsonrpc": "2.0", "method": "exit"})
p.wait(timeout=20)
ru = resource.getrusage(resource.RUSAGE_CHILDREN)
print("peak_rss_mb=%d cpu_s=%.0f" % (ru.ru_maxrss / 1e6, ru.ru_utime + ru.ru_stime))
