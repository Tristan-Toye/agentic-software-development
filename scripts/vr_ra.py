#!/usr/bin/env python3
"""Visual review layer 4: a rust-analyzer call hierarchy over LSP stdio.

Adapted from the D0 spike (research/d0/scripts/ra_calls.py), now with a hard
deadline: a reader thread feeds a queue, every wait is bounded by `timeout`
seconds for the WHOLE session, and on expiry the server is killed and RaError
is raised. Stdlib only. For whole-repo builds `outgoing_for_file(rel)` batches one file (documentSymbol, then the
outgoing calls of each fn), `set_deadline(s)` resets the clock per file, requests retry on "content modified"
(-32801/-32802), and `options=FAST_OPTIONS` (build scripts, proc macros, check-on-save and cache priming off, verified in the
rust-analyzer manual) keeps indexing to seconds; calls visible only in macro-expanded code are then missed. D0 measured 16 s to the first answer and 3 GB RSS on
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
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import quote, unquote, urlparse


# Initialization options for a whole-repo batch run (vr_refresh.py). Names are the rust-analyzer settings without
# the `rust-analyzer.` prefix (documented: cargo.buildScripts.enable, procMacro.enable, checkOnSave, cachePriming.enable,
# diagnostics.enable). Build scripts and proc macros off means no `cargo check` of the dependency tree and no
# proc-macro server; calls made only inside macro-expanded code are not resolved. `cargo.noDeps` was tried and rejected:
# it drops the dependency edges between workspace crates, so no cross-crate call resolves (measured on config-harness).
# Instead the server runs with a private CARGO_HOME (see `_private_cargo_home`).
FAST_OPTIONS = {"cargo": {"buildScripts": {"enable": False}}, "procMacro": {"enable": False}, "checkOnSave": False,
                "cachePriming": {"enable": False}, "diagnostics": {"enable": False}}


def _private_cargo_home():
    """(env, tmpdir): an environment whose CARGO_HOME is a scratch directory that links the real registry and git caches.

    Every `cargo metadata` call of rust-analyzer takes cargo's package-cache lock in CARGO_HOME. Another cargo build on
    the machine holds that lock for tens of seconds ("Blocking waiting for file lock on package cache": measured 36 s per
    call on config-harness under load), which is the usual reason a whole session ran out of its clock. The lock file of the
    private directory is ours alone; the caches are read through links."""
    import tempfile
    real = os.environ.get("CARGO_HOME") or os.path.join(os.path.expanduser("~"), ".cargo")
    tmp = tempfile.mkdtemp(prefix="asd-cargo-home-")
    for name in ("registry", "git", "config.toml", "config"):
        src = os.path.join(real, name)
        if os.path.exists(src):
            os.symlink(src, os.path.join(tmp, name))
    env = dict(os.environ, CARGO_HOME=tmp)
    env.setdefault("RUSTUP_HOME", os.path.join(os.path.expanduser("~"), ".rustup"))
    return env, tmp


class RaError(Exception):
    """The language server died, timed out or answered with an error."""


def _uri(path: str) -> str:
    return "file://" + quote(path)


def _path(uri: str) -> str:
    return unquote(urlparse(uri).path)


class RaClient:
    def __init__(self, root: str, cmd=("rust-analyzer",), timeout: float = 120.0, options: dict | None = None):
        self.options = options
        self.quiescent = False
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
        stdin = self._proc.stdin if self._proc else None
        if stdin is None:
            raise RaError("language server is not running")
        try:
            stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
            stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RaError("language server closed its input: %s" % exc)

    def _reader(self) -> None:
        out = self._proc.stdout if self._proc else None
        if out is None:
            self._q.put(None)
            return
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
        if msg.get("method") == "experimental/serverStatus" and "id" not in msg:
            self.quiescent = bool((msg.get("params") or {}).get("quiescent"))
        return msg

    def _request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        retries = 0
        while True:
            m = self._recv()
            if "method" in m and "id" in m:  # server -> client request: acknowledge
                self._send({"jsonrpc": "2.0", "id": m["id"], "result": None})
                continue
            if m.get("id") == rid:
                if "error" in m and m["error"].get("code") in (-32801, -32802) and retries < 20:
                    # ContentModified / ServerCancelled: the server reloaded the workspace under the request. Ask again.
                    retries += 1
                    time.sleep(1)
                    self._next_id += 1
                    rid = self._next_id
                    self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
                    continue
                if "error" in m:
                    raise RaError("%s: %s" % (method, m["error"].get("message", m["error"])))
                return m

    # -- lifecycle
    def start(self) -> "RaClient":
        self._deadline = time.time() + self.timeout
        env = None
        if self.options and self.cmd and self.cmd[0] == "rust-analyzer":
            env, self._home = _private_cargo_home()
        try:
            self._proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, cwd=self.root, env=env)
        except OSError as exc:
            raise RaError("cannot start %s: %s" % (self.cmd[0], exc))
        threading.Thread(target=self._reader, daemon=True).start()
        uri = _uri(self.root)
        r = self._request("initialize", {
            "processId": os.getpid(), "rootUri": uri, "initializationOptions": self.options or {},
            "capabilities": {"experimental": {"serverStatusNotification": True},
                             "window": {"workDoneProgress": True},
                             "textDocument": {"callHierarchy": {},
                                              "documentSymbol": {"hierarchicalDocumentSymbolSupport": True}}},
            "workspaceFolders": [{"uri": uri, "name": "ws"}]})
        self.version = (r.get("result", {}).get("serverInfo") or {}).get("version")
        self._send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        return self

    def kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()

    def close(self) -> None:
        try:
            self._close()
        finally:
            home = getattr(self, "_home", None)
            if home:
                shutil.rmtree(home, ignore_errors=True)
                self._home = None

    def _close(self) -> None:
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


    def set_deadline(self, seconds: float) -> None:
        """Restart the clock: the next waits are bounded by `seconds` from now (a batch run gives each file its own)."""
        self.timeout = seconds
        self._deadline = time.time() + seconds

    def outgoing_for_file(self, rel_path: str, tries: int = 30) -> list:
        """Every function of one file with its outgoing calls: [{"name","line","col","out":[{"name","path","line","ranges"}]}].

        One documentSymbol request lists the file's functions (kinds Function and Method, nested too);
        each gets a prepareCallHierarchy and an outgoingCalls request. Incoming calls are not asked: they
        are the inverse of the outgoing calls of every other file. A function whose item stays empty after
        `tries` polls (a macro-generated or cfg'd-out symbol) is listed with `"out": None`. Bounded by the
        deadline of `set_deadline`.
        """
        full = os.path.join(self.root, rel_path)
        with open(full, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        uri = _uri(full)
        patient = tries
        if getattr(self, "_proven", False):
            tries = min(tries, 2)   # the server has answered before: an empty answer is a real one (no 15-30 s stall)
        self._send({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {
            "textDocument": {"uri": uri, "languageId": "rust", "version": 1, "text": text}}})
        try:
            syms = None
            for _ in range(tries):
                try:
                    syms = self._request("textDocument/documentSymbol", {"textDocument": {"uri": uri}}).get("result")
                except RaError as exc:
                    if "timed out" in str(exc) or "exited" in str(exc):
                        raise
                if syms:
                    self._proven = True
                    break
                time.sleep(1 if tries == patient else 0.3)
            fns = []

            def walk(items):
                for it in items or []:
                    if it.get("kind") in (6, 12) and it.get("selectionRange"):
                        fns.append(it)
                    walk(it.get("children"))
            walk(syms if syms and "selectionRange" in (syms[0] or {}) else [])
            res = []
            for it in sorted(fns, key=lambda i: (i["selectionRange"]["start"]["line"], i["selectionRange"]["start"]["character"])):
                st = it["selectionRange"]["start"]
                rec = {"name": it["name"], "line": st["line"] + 1, "col": st["character"] + 1, "out": None}
                pos = {"textDocument": {"uri": uri}, "position": st}
                found = None
                for _ in range(tries):
                    found = self._request("textDocument/prepareCallHierarchy", pos).get("result") or []
                    if found:
                        break
                    time.sleep(0.5)
                if found:
                    out = self._request("callHierarchy/outgoingCalls", {"item": found[0]}).get("result") or []
                    rec["out"] = []
                    for c in out:
                        t = self._item(c["to"])
                        rec["out"].append({"name": t["name"], "path": t["path"], "line": t["line"],
                                           "ranges": [(r["start"]["line"] + 1) for r in c.get("fromRanges", [])]})
                res.append(rec)
            return res
        finally:
            try:
                self._send({"jsonrpc": "2.0", "method": "textDocument/didClose", "params": {"textDocument": {"uri": uri}}})
            except RaError:
                pass


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
        elif meth == "textDocument/didOpen":
            state["text"] = m["params"]["textDocument"]["text"]
        elif meth == "textDocument/documentSymbol":
            syms = []
            for i, ln in enumerate(state.get("text", "").split("\n")):
                mm = re.search(r"fn (\w+)", ln)
                if mm:
                    r = {"start": {"line": i, "character": mm.start(1)}, "end": {"line": i, "character": mm.end(1)}}
                    syms.append({"name": mm.group(1), "kind": 12, "range": r, "selectionRange": r})
            send({"jsonrpc": "2.0", "id": rid, "result": [{"name": "impl S", "kind": 19, "range": {"start": {"line": 0, "character": 0}, "end": {"line": 99, "character": 0}},
                                                          "selectionRange": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}}, "children": syms}]})
        elif meth == "callHierarchy/outgoingCalls":
            if mode == "modified" and not state.get("modified"):
                state["modified"] = True
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32801, "message": "content modified"}})
                continue
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
        with open(os.path.join(d, "crates/a/src/lib.rs"), "w") as fh:
            fh.write("pub fn one() {}\nimpl S {\n    fn two(&self) {}\n}\n")
        with RaClient(d, cmd=[sys.executable, me, "--fake-server", "modified"], timeout=30) as ra:
            ra.set_deadline(20)
            fl = ra.outgoing_for_file("crates/a/src/lib.rs")
        if [(f["name"], f["line"], f["col"]) for f in fl] != [("one", 1, 8), ("two", 3, 8)]:
            fails.append("file functions: %r" % [(f["name"], f["line"], f["col"]) for f in fl])
        elif sorted(o["name"] for o in fl[0]["out"]) != ["area", "inner", "push"]:
            fails.append("file outgoing: %r" % fl[0]["out"])
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
        fake_server("hang" if "hang" in sys.argv else "modified" if "modified" in sys.argv else "")
    elif "--selftest" in sys.argv:
        sys.exit(_selftest())
    else:
        print(__doc__)
        sys.exit(2)
