#!/usr/bin/env python3
"""Ingest-only summary of bench.py results, in run order."""
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
print("| variant | docs/h | wall s | valid JSON | stop | prompt tok | output tok | request s (min–max) |")
print("|---|---|---|---|---|---|---|---|")
for p in sorted(out.glob("*.ingest.json"), key=lambda p: p.stat().st_mtime):
    r = json.loads(p.read_text())
    i = r["ingest"]
    lo, hi = i["request_s_min_max"]
    print(f"| {r['name']} | {i['docs_per_hour']} | {i['wall_s']} | {i.get('valid_json', '')}/{i['docs']} "
          f"| {i.get('finished_stop', '')} | {i['prompt_tokens']} | {i['completion_tokens']} | {lo}–{hi} |")
