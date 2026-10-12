#!/usr/bin/env python3
"""Summarize bench.py results: wall tok/s range across prompt classes per
concurrency (the serving table's metric), plus the ingest test."""
import json
import sys
from collections import defaultdict
from pathlib import Path

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "results"
names = sys.argv[2:] or sorted(p.name[:-len(".timed_load.json")] for p in out.glob("*.timed_load.json"))
concs = set()
rows = []
for name in names:
    tl = out / f"{name}.timed_load.json"
    ig = out / f"{name}.ingest.json"
    if not tl.exists():
        continue
    by_c = defaultdict(list)
    for phase in json.loads(tl.read_text())["phases"]:
        by_c[phase["concurrency"]].append(phase["metrics"]["wall_tokens_per_s"])
    concs |= set(by_c)
    ingest = json.loads(ig.read_text())["ingest"] if ig.exists() else None
    rows.append((name, by_c, ingest))

concs = sorted(concs)
print("| variant | " + " | ".join(f"C{c} wall tok/s" for c in concs)
      + " | ingest docs/h | ingest out tok/s | ingest wall s |")
print("|---|" + "---|" * (len(concs) + 3))
for name, by_c, ingest in rows:
    cells = [f"{min(by_c[c]):.1f}–{max(by_c[c]):.1f}" if by_c.get(c) else "" for c in concs]
    ig = [str(ingest["docs_per_hour"]), str(ingest["output_tok_s"]), str(ingest["wall_s"])] if ingest else ["", "", ""]
    print(f"| {name} | " + " | ".join(cells) + " | " + " | ".join(ig) + " |")
