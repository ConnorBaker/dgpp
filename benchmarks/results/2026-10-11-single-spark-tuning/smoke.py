#!/usr/bin/env python3
"""Smoke test for a running DGPP OpenAI-compatible server (stdlib only).

Checks model listing, plain chat, streaming, a two-turn tool-call round trip,
then measures aggregate decode throughput at concurrency 1 and 8.
"""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18080"


def post(path, body, timeout=900):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
    return ok


results = []
with urllib.request.urlopen(BASE + "/v1/models", timeout=30) as r:
    model = json.loads(r.read())["data"][0]["id"]
results.append(check("models", bool(model), f"id={model}"))

chat = post("/v1/chat/completions", {
    "model": model, "max_tokens": 160, "reasoning_effort": "low",
    "messages": [{"role": "user", "content": "Name three primary colors."}]})
text = chat["choices"][0]["message"].get("content") or ""
results.append(check("plain chat", "red" in text.lower() or "blue" in text.lower(),
                     text[:120].replace("\n", " ")))

req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps({
    "model": model, "stream": True, "max_tokens": 64, "reasoning_effort": "low",
    "messages": [{"role": "user", "content": "Count from 1 to 5."}]}).encode(),
    headers={"Content-Type": "application/json"})
chunks, done = 0, False
with urllib.request.urlopen(req, timeout=900) as r:
    for raw in r:
        line = raw.decode().strip()
        if line.startswith("data:"):
            if line[5:].strip() == "[DONE]":
                done = True
            else:
                chunks += 1
results.append(check("streaming", done and chunks > 1, f"chunks={chunks} done={done}"))

tools = [{"type": "function", "function": {
    "name": "get_weather", "description": "Get the current weather for a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                   "required": ["city"]}}}]
messages = [{"role": "user", "content": "What is the weather in Paris right now? Use the tool."}]
first = post("/v1/chat/completions", {"model": model, "max_tokens": 400, "reasoning_effort": "low",
                                      "tools": tools, "messages": messages})
msg = first["choices"][0]["message"]
calls = msg.get("tool_calls") or []
ok_call = bool(calls) and calls[0]["function"]["name"] == "get_weather"
args = json.loads(calls[0]["function"]["arguments"]) if ok_call else {}
results.append(check("tool call emitted", ok_call and "city" in args, json.dumps(calls)[:160]))
if ok_call:
    messages += [msg, {"role": "tool", "tool_call_id": calls[0]["id"], "content": json.dumps(
        {"city": args.get("city", "Paris"), "temp_c": 18, "conditions": "light rain"})}]
    second = post("/v1/chat/completions", {"model": model, "max_tokens": 300, "reasoning_effort": "low",
                                           "tools": tools, "messages": messages})
    final = second["choices"][0]["message"].get("content") or ""
    results.append(check("tool result used in answer", "18" in final, final[:160].replace("\n", " ")))


def one(i):
    t = time.time()
    r = post("/v1/chat/completions", {
        "model": model, "max_tokens": 256, "reasoning_effort": "low",
        "messages": [{"role": "user", "content": f"Write a short paragraph about topic number {i}: the history of bridges."}]})
    return r["usage"]["completion_tokens"], time.time() - t


for c in (1, 8):
    t0 = time.time()
    with ThreadPoolExecutor(c) as ex:
        res = list(ex.map(one, range(c)))
    wall = time.time() - t0
    toks = sum(x[0] for x in res)
    print(f"C{c}: wall {wall:.1f}s, tokens {toks}, aggregate {toks / wall:.1f} tok/s, "
          f"per-request {[round(x[1], 1) for x in res]}")

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
