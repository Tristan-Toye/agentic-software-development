#!/usr/bin/env python3
"""Inline the three fixtures and their ELK layouts into demo.template.html -> demo.html. Stdlib only."""
import json, pathlib
d = pathlib.Path(__file__).resolve().parent.parent
data = {v.upper(): {"fx": json.load(open(d / f"fixtures/w145-{v}.json")), "ly": json.load(open(d / f"fixtures/w145-{v}.layout.json"))} for v in ("l0", "l1", "l2")}
blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
html = (d / "demo.template.html").read_text().replace("/*DATA*/null", blob)
(d / "demo.html").write_text(html)
js = html.split("/*CANVAS-START*/")[1].split("/*CANVAS-END*/")[0]
print("demo.html bytes", len(html.encode()), "| canvas js lines", len(js.strip().splitlines()), "bytes", len(js.strip().encode()))
