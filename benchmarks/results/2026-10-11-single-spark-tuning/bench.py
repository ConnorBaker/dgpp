#!/usr/bin/env python3
"""Run one DGPP tuning variant: start, measure, stop.

  bench.py NAME [KNOBS...]

Starts the single-Spark deployment with the given dgpp-serve flags appended,
runs scripts/timed_load.py (the repo's serving-table method) and an
ingest-style test, writes results to OUT/NAME.*, then stops the server.
Run inside `nix develop` from the repo root.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("BENCH_OUT", Path(__file__).resolve().parent / "raw" / "adhoc"))
CONFIG = Path(os.environ.get("BENCH_CONFIG", REPO / "deploy/cluster_qwen-3.8-flash-next_nvfp4_w1.json"))
BASE = "http://127.0.0.1:18080"
BIN = os.environ["DGPP_BIN"]
CONCURRENCY = os.environ.get("BENCH_CONCURRENCY", "1,4,8,16")
INGEST_DOCS = int(os.environ.get("BENCH_INGEST_DOCS", "32"))
INGEST_CONC = int(os.environ.get("BENCH_INGEST_CONC", "8"))
# "low" keeps Qwen's thinking on; "none" maps to enable_thinking: false.
INGEST_REASONING = os.environ.get("BENCH_INGEST_REASONING", "low")
SKIP_TIMED_LOAD = os.environ.get("BENCH_SKIP_TIMED_LOAD") == "1"
INGEST_MAX_TOKENS = int(os.environ.get("BENCH_INGEST_MAX_TOKENS", "600"))
# Enforce the summary shape with response_format (bounded lists keep it short).
INGEST_SCHEMA = os.environ.get("BENCH_INGEST_SCHEMA") == "1"
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 5},
        "entities": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
    },
    "required": ["title", "summary", "key_points", "entities", "tags"],
    "additionalProperties": False,
}


def cluster(*args, log):
    env = dict(os.environ)
    cmd = ["python3", str(REPO / "scripts/dgpp-cluster"), *args, "--config", str(CONFIG)]
    with open(log, "a") as f:
        f.write(f"$ {' '.join(cmd)}\n")
        f.flush()
        return subprocess.run(cmd, cwd=REPO, env=env, stdout=f, stderr=subprocess.STDOUT).returncode


def post(body, timeout=1800):
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def documents(n, chars=32000):
    """Distinct ~8K-token documents cut from the repo's own prose."""
    text = "\n".join(p.read_text() for p in sorted((REPO / "docs").glob("*.md")))
    step = max(1, (len(text) - chars) // n)
    return [text[i * step:i * step + chars] for i in range(n)]


def ingest(model, name):
    docs = documents(INGEST_DOCS)
    answers = {}
    instruction = ("Summarize the document below for a knowledge base. Reply with JSON: "
                   '{"title": str, "summary": str (3 sentences), "key_points": [str] (5 items), '
                   '"entities": [str], "tags": [str]}.')

    def one(i):
        t = time.time()
        body = {"model": model, "max_tokens": INGEST_MAX_TOKENS, "reasoning_effort": INGEST_REASONING,
                "temperature": 0,
                "messages": [{"role": "system", "content": instruction},
                             {"role": "user", "content": f"Document {i}:\n\n{docs[i]}"}]}
        if INGEST_SCHEMA:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "kb_summary", "schema": SCHEMA, "strict": True}}
        r = post(body)
        u = r["usage"]
        choice = r["choices"][0]
        answers[i] = {"finish_reason": choice.get("finish_reason"), "content": choice["message"].get("content"),
                      "reasoning_chars": len(choice["message"].get("reasoning_content") or "")}
        return u["prompt_tokens"], u["completion_tokens"], time.time() - t

    t0 = time.time()
    with ThreadPoolExecutor(INGEST_CONC) as ex:
        rows = list(ex.map(one, range(len(docs))))
    wall = time.time() - t0
    (OUT / f"{name}.ingest_answers.json").write_text(json.dumps(answers, indent=1, sort_keys=True))

    def valid(a):
        text = (a["content"] or "").strip().removeprefix("```json").removesuffix("```").strip()
        try:
            return isinstance(json.loads(text), dict)
        except ValueError:
            return False

    return {"docs": len(rows), "concurrency": INGEST_CONC, "reasoning": INGEST_REASONING,
            "valid_json": sum(valid(a) for a in answers.values()),
            "finished_stop": sum(a["finish_reason"] == "stop" for a in answers.values()),
            "wall_s": round(wall, 1),
            "prompt_tokens": sum(r[0] for r in rows), "completion_tokens": sum(r[1] for r in rows),
            "docs_per_hour": round(len(rows) / wall * 3600),
            "output_tok_s": round(sum(r[1] for r in rows) / wall, 1),
            "request_s_min_max": [round(min(r[2] for r in rows), 1), round(max(r[2] for r in rows), 1)]}


def main():
    name, knobs = sys.argv[1], sys.argv[2:]
    OUT.mkdir(parents=True, exist_ok=True)
    log = OUT / f"{name}.log"
    log.write_text("")
    cluster("down", log=log)
    rc = cluster("up", "--bin", BIN, "--head-wrap", "nixglhost", "--knobs", " ".join(knobs), log=log)
    if rc:
        print(f"{name}: up failed (exit {rc}); see {log}")
        return 1
    try:
        with urllib.request.urlopen(BASE + "/v1/models", timeout=30) as r:
            model = json.loads(r.read())["data"][0]["id"]
        if not SKIP_TIMED_LOAD:
            with open(OUT / f"{name}.timed_load.txt", "w") as f:
                subprocess.run(["python3", str(REPO / "scripts/timed_load.py"), "127.0.0.1", "18080",
                                "--concurrency", CONCURRENCY, "--classes", "all", "--repeat", "1",
                                "--max-tokens", "256", "--json-out", str(OUT / f"{name}.timed_load.json")],
                               cwd=REPO / "scripts", stdout=f, stderr=subprocess.STDOUT, check=True)
        result = {"name": name, "knobs": knobs, "ingest": ingest(model, name)}
        (OUT / f"{name}.ingest.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        cluster("down", log=log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
