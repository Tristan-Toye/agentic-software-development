#!/usr/bin/env python3
"""Visual review layer 4: a rust-analyzer call hierarchy over LSP stdio.

Adapted from the D0 spike (research/d0/scripts/ra_calls.py), now with a hard
deadline: a reader thread feeds a queue, every wait is bounded by `timeout`
seconds for the WHOLE session, and on expiry the server is killed and RaError
is raised. Stdlib only. D0 measured 16 s to the first answer and 3 GB RSS on
config-harness, so this runs on request only.

    from vr_ra import RaClient
    with RaClient(root, timeout=120) as ra:
        r = ra.call_hierarchy("crates/harness/src/tools/step.rs", 1816, 14)
        # {"item": {...}, "incoming": [{"item": {...}, "ranges": [...]}], "outgoing": [...]}

Items are {"name","path","line","col","detail"}; `path` is absolute, `line` and
`col` are 1-based and come from the SELECTION range (D0: rust-analyzer's item
range starts at the doc comment, the selection range at the name).

    vr_ra.py --selftest        (starts a fake LSP server from this same file)
    vr_ra.py --fake-server [hang]   (used by the selftest)
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from urllib.parse import quote, unquote, urlparse


class RaError(Exception):
    """The language server died, timed out or answered with an error."""


def _uri(path: str) -> str:
    return "file://" + quote(path)


def _path(uri: str) -> str:
    return unquote(urlparse(uri).path)


class RaClient:
    def __init__(self, root: str, cmd=("rust-analyzer",), timeout: float = 120.0):
        self.root = os.path.abspath(root)
        self.cmd = list(cmd)
        self.timeout = timeout
        self._deadline = 0.0
        self._next_id = 0
        self._q: queue.Queue = queue.Queue()
        self._proc = None
        self.version = None

    # -- transport
    def _send(self, msg: dict) -> None:
        body = json.dumps(msg).encode()
        try:
            self._proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RaError("language server closed its input: %s" % exc)

    def _reader(self) -> None:
        out = self._proc.stdout
        while True:
            headers = {}
            while True:
                line = out.readline()
                if not line:
                    self._q.put(None)
                    return
                if line == b"\r\n":
                    break
                k, _, v = line.decode().partition(":")
                headers[k.lower()] = v.strip()
            try:
                self._q.put(json.loads(out.read(int(headers["content-length"]))))
            except (KeyError, ValueError):
                self._q.put(None)
                return

    def _recv(self):
        left = self._deadline - time.time()
        if left <= 0:
            self.kill()
            raise RaError("rust-analyzer timed out after %.0f s" % self.timeout)
        try:
            msg = self._q.get(timeout=left)
        except queue.Empty:
            self.kill()
            raise RaError("rust-analyzer timed out after %.0f s" % self.timeout)
        if msg is None:
            raise RaError("rust-analyzer exited")
        return msg

    def _request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            m = self._recv()
            if "method" in m and "id" in m:  # server -> client request: acknowledge
                self._send({"jsonrpc": "2.0", "id": m["id"], "result": None})
                continue
            if m.get("id") == rid:
                if "error" in m:
                    raise RaError("%s: %s" % (method, m["error"].get("message", m["error"])))
                return m

    # -- lifecycle
    def start(self) -> "RaClient":
        self._deadline = time.time() + self.timeout
        try:
            self._proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, cwd=self.root)
        except OSError as exc:
            raise RaError("cannot start %s: %s" % (self.cmd[0], exc))
        threading.Thread(target=self._reader, daemon=True).start()
        uri = _uri(self.root)
        r = self._request("initialize", {
            "processId": os.getpid(), "rootUri": uri,
            "capabilities": {"experimental": {"serverStatusNotification": True},
                             "window": {"workDoneProgress": True},
                             "textDocument": {"callHierarchy": {}}},
            "workspaceFolders": [{"uri": uri, "name": "ws"}]})
        self.version = (r.get("result", {}).get("serverInfo") or {}).get("version")
        self._send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        return self

    def kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()

    def close(self) -> None:
        if not self._proc or self._proc.poll() is not None:
            return
        try:
            self._deadline = time.time() + 10
            self._request("shutdown", {})
            self._send({"jsonrpc": "2.0", "method": "exit"})
            self._proc.wait(timeout=10)
        except (RaError, subprocess.TimeoutExpired):
            self.kill()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    # -- queries
    @staticmethod
    def _item(it: dict) -> dict:
        sel = it.get("selectionRange") or it.get("range") or {"start": {"line": 0, "character": 0}}
        return {"name": it["name"], "path": _path(it["uri"]), "line": sel["start"]["line"] + 1,
                "col": sel["start"]["character"] + 1, "detail": it.get("detail") or ""}

    def call_hierarchy(self, rel_path: str, line: int, col: int) -> dict:
        """Callers and callees of the function whose name starts at rel_path:line:col (1-based).

        Polls prepareCallHierarchy until the index answers (D0: ~16 s), bounded by the timeout.
        """
        full = os.path.join(self.root, rel_path)
        with open(full, encoding="utf-8") as fh:
            text = fh.read()
        self._send({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {
            "textDocument": {"uri": _uri(full), "languageId": "rust", "version": 1, "text": text}}})
        pos = {"textDocument": {"uri": _uri(full)}, "position": {"line": line - 1, "character": col - 1}}
        item = None
        while item is None:
            res = self._request("textDocument/prepareCallHierarchy", pos).get("result") or []
            if res:
                item = res[0]
                break
            if time.time() + 2 >= self._deadline:
                self.kill()
                raise RaError("no call hierarchy item at %s:%d before the %.0f s timeout" % (rel_path, line, self.timeout))
            time.sleep(2)
        out = self._request("callHierarchy/outgoingCalls", {"item": item}).get("result") or []
        inc = self._request("callHierarchy/incomingCalls", {"item": item}).get("result") or []

        def ranges(c):
            return [(r["start"]["line"] + 1) for r in c.get("fromRanges", [])]
        return {"item": self._item(item),
                "outgoing": [{"item": self._item(c["to"]), "ranges": ranges(c)} for c in out],
                "incoming": [{"item": self._item(c["from"]), "ranges": ranges(c)} for c in inc]}


# ------------------------------------------------------------ fake server

def fake_server(mode: str = "") -> None:
    """A scripted LSP server for the selftests. It answers prepareCallHierarchy
    with null once (the index is "not ready"), then with an item; mode "hang"
    never answers it. Callees: `inner` (a plain fn), `Shape::area` (declared in
    a trait) and one standard-library item. Caller: `run_loop`."""
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    state = {"root": "/", "prepared": 0}

    def read():
        h = {}
        while True:
            line = inp.readline()
            if not line:
                return None
            if line == b"\r\n":
                break
            k, _, v = line.decode().partition(":")
            h[k.lower()] = v.strip()
        return json.loads(inp.read(int(h["content-length"])))

    def send(m):
        b = json.dumps(m).encode()
        out.write(b"Content-Length: %d\r\n\r\n" % len(b) + b)
        out.flush()

    def item(name, path, line):
        pos = {"line": line - 1, "character": 7}
        return {"name": name, "kind": 12, "uri": "file://" + path, "detail": "fn",
                "range": {"start": {"line": max(line - 2, 0), "character": 0}, "end": pos},
                "selectionRange": {"start": pos, "end": pos}}

    while True:
        m = read()
        if m is None:
            return
        meth, rid = m.get("method"), m.get("id")
        if meth == "initialize":
            state["root"] = urlparse(m["params"]["rootUri"]).path
            send({"jsonrpc": "2.0", "id": rid, "result": {"capabilities": {}, "serverInfo": {"name": "fake-ra", "version": "0.0.fake"}}})
        elif meth == "textDocument/prepareCallHierarchy":
            if mode == "hang":
                continue
            state["prepared"] += 1
            if state["prepared"] == 1:
                send({"jsonrpc": "2.0", "id": rid, "result": None})
            else:
                p = urlparse(m["params"]["textDocument"]["uri"]).path
                send({"jsonrpc": "2.0", "id": rid, "result": [item("target_fn", p, m["params"]["position"]["line"] + 1)]})
        elif meth == "callHierarchy/outgoingCalls":
            root = state["root"]
            lib = root + "/crates/a/src/lib.rs"
            rng = [{"start": {"line": 4, "character": 4}, "end": {"line": 4, "character": 9}}]
            send({"jsonrpc": "2.0", "id": rid, "result": [
                {"to": item("inner", lib, 3), "fromRanges": rng},
                {"to": item("area", lib, 12), "fromRanges": rng},
                {"to": item("push", "/rustc/abc/library/alloc/src/vec/mod.rs", 99), "fromRanges": rng}]})
        elif meth == "callHierarchy/incomingCalls":
            lib = state["root"] + "/crates/a/src/lib.rs"
            send({"jsonrpc": "2.0", "id": rid, "result": [
                {"from": item("run_loop", lib, 16), "fromRanges": [{"start": {"line": 16, "character": 2}, "end": {"line": 16, "character": 9}}]}]})
        elif meth == "shutdown":
            send({"jsonrpc": "2.0", "id": rid, "result": None})
        elif meth == "exit":
            return
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid, "result": None})


def _selftest() -> int:
    import tempfile
    fails = []
    me = os.path.abspath(__file__)
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "crates/a/src"))
        with open(os.path.join(d, "crates/a/src/lib.rs"), "w") as fh:
            fh.write("\n" * 30)
        with RaClient(d, cmd=[sys.executable, me, "--fake-server"], timeout=30) as ra:
            r = ra.call_hierarchy("crates/a/src/lib.rs", 7, 8)
            if ra.version != "0.0.fake":
                fails.append("server version not read")
        if r["item"]["name"] != "target_fn" or r["item"]["line"] != 7:
            fails.append("item: %r" % r["item"])
        names = sorted(c["item"]["name"] for c in r["outgoing"])
        if names != ["area", "inner", "push"]:
            fails.append("outgoing: %r" % names)
        if [c["item"]["name"] for c in r["incoming"]] != ["run_loop"]:
            fails.append("incoming")
        if r["outgoing"][0]["ranges"] != [5]:
            fails.append("call-site range: %r" % r["outgoing"][0]["ranges"])
        t0 = time.time()
        try:
            with RaClient(d, cmd=[sys.executable, me, "--fake-server", "hang"], timeout=2) as ra:
                ra.call_hierarchy("crates/a/src/lib.rs", 7, 8)
            fails.append("hang did not time out")
        except RaError as exc:
            if time.time() - t0 > 8 or "timed out" not in str(exc) and "timeout" not in str(exc):
                fails.append("timeout error: %s after %.1fs" % (exc, time.time() - t0))
        try:
            RaClient(d, cmd=["/nonexistent-ra"], timeout=2).start()
            fails.append("missing binary accepted")
        except RaError:
            pass
    if fails:
        print("vr_ra selftest FAILED:\n  " + "\n  ".join(fails), file=sys.stderr)
        return 1
    print("vr_ra selftest ok")
    return 0


if __name__ == "__main__":
    if "--fake-server" in sys.argv:
        fake_server("hang" if "hang" in sys.argv else "")
    elif "--selftest" in sys.argv:
        sys.exit(_selftest())
    else:
        print(__doc__)
        sys.exit(2)
