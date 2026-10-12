#!/usr/bin/env python3
"""Prefix-cache probe: one long document asked two different questions.

The second request shares the document as a prefix, so it should attach a
cached entry (usage.prompt_tokens_details.cached_tokens > 0, and a much
shorter time to first token). Prints both requests' TTFT and token counts
and the prefix_cache metric deltas. Stdlib only.

  prefix_probe.py [BASE_URL] [--records N]
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from qwen_yarn_release_check import filler  # noqa: E402  (the NVMe check's own document generator)

ap = argparse.ArgumentParser()
ap.add_argument("base", nargs="?", default="http://127.0.0.1:18080")
ap.add_argument("--records", type=int, default=700, help="filler records (~45 tokens each)")
args = ap.parse_args()


def get(path):
    with urllib.request.urlopen(args.base + path, timeout=30) as r:
        return json.loads(r.read())


def ask(model, doc, question):
    body = {"model": model, "max_tokens": 32, "temperature": 0, "stream": True,
            "stream_options": {"include_usage": True}, "reasoning_effort": "none",
            "messages": [{"role": "user", "content": f"{doc}\n\nQuestion: {question}"}]}
    req = urllib.request.Request(args.base + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0, ttft, usage = time.time(), None, None
    with urllib.request.urlopen(req, timeout=900) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                continue
            chunk = json.loads(line[5:])
            if ttft is None and chunk.get("choices") and chunk["choices"][0].get("delta", {}).get("content"):
                ttft = time.time() - t0
            usage = chunk.get("usage") or usage
    cached = ((usage or {}).get("prompt_tokens_details") or {}).get("cached_tokens")
    return {"ttft_ms": round((ttft or 0) * 1000), "prompt": (usage or {}).get("prompt_tokens"), "cached": cached}


def prefix_metrics():
    m = get("/v1/metrics")
    return m.get("prefix_cache", {})


model = get("/v1/models")["data"][0]["id"]
doc = "".join(filler(20260925, args.records))
before = prefix_metrics()
a = ask(model, doc, "What is the first record's identifier?")
time.sleep(1)
b = ask(model, doc, "How many records mention the word 'alpha'?")
after = prefix_metrics()
deltas = {k: after[k] - before.get(k, 0) for k in after if isinstance(after[k], (int, float))}
print(json.dumps({"first": a, "second": b, "prefix_cache_deltas": deltas}, indent=1))
sys.exit(0 if (b["cached"] or 0) > 0 else 1)
