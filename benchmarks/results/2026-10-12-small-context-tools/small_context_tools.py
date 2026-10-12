#!/usr/bin/env python3
"""Agent-style tool use against a DGPP server with a small context window.

Run against a server started with a small KV pool (e.g. --kv-capacity 8192).
Each case prints PASS/FAIL/INFO with what the server did; INFO cases record
behavior (errors, limits) rather than assert a particular choice. Stdlib only.

  small_context_tools.py [BASE_URL] [--json-out FILE]
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("base", nargs="?", default="http://127.0.0.1:18080")
ap.add_argument("--json-out")
ap.add_argument("--pad", type=int, default=300, help="filler words per tool result in the overflow case")
args = ap.parse_args()
BASE = args.base
report = []


def call(body, timeout=900):
    """POST a chat completion; returns (status, json-or-text)."""
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, text


def record(case, status, detail):
    print(f"{status:4} {case}: {detail}")
    report.append({"case": case, "status": status, "detail": detail})


with urllib.request.urlopen(BASE + "/v1/models", timeout=30) as r:
    models = json.loads(r.read())
MODEL = models["data"][0]["id"]
record("models", "INFO", json.dumps({k: v for k, v in models["data"][0].items()
                                     if k in ("id", "max_model_len", "context_length", "max_context")}))

# A tiny repository the agent explores through tools.
FILES = {
    "README.md": "Project zephyr. Settings live in config/. See config/limits.toml for quotas.",
    "config/app.toml": "[app]\nname = \"zephyr\"\nworkers = 6\n",
    "config/limits.toml": "[limits]\nmax_upload_mb = 37\nrate_per_minute = 120\n",
    "src/main.py": "from config import load\nprint(load())\n",
}
TOOLS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "List files under a directory ('' for the root).",
        "parameters": {"type": "object", "properties": {"dir": {"type": "string"}}, "required": ["dir"]}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a file's full contents.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
]


def run_tool(name, arguments, pad=0):
    a = json.loads(arguments)
    if name == "list_files":
        d = a.get("dir", "").strip("/")
        out = sorted(p for p in FILES if p.startswith(d + "/") or not d)
    elif name == "read_file":
        out = FILES.get(a.get("path", "").strip("/"), "error: no such file")
    else:
        out = f"error: unknown tool {name}"
    text = json.dumps(out)
    return text + ("\n# padding: " + "lorem ipsum " * pad if pad else "")


def agent(task, max_steps=8, pad=0, max_tokens=512):
    """Run a tool loop; returns (final_text, steps, error)."""
    messages = [{"role": "system", "content": "You are a coding agent. Use the tools to answer; be brief."},
                {"role": "user", "content": task}]
    for step in range(1, max_steps + 1):
        status, r = call({"model": MODEL, "messages": messages, "tools": TOOLS, "max_tokens": max_tokens,
                          "temperature": 0, "reasoning_effort": "none"})
        if status != 200:
            return None, step, (status, r, sum(len(m.get("content") or "") for m in messages))
        msg = r["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content"), step, None
        messages.append(msg)
        for c in calls:
            messages.append({"role": "tool", "tool_call_id": c["id"],
                             "content": run_tool(c["function"]["name"], c["function"]["arguments"], pad)})
    return None, max_steps, "no final answer within the step limit"


# 1. Multi-step tool loop that has to explore before it can answer.
text, steps, err = agent("What is the maximum upload size in MB for this project? Find it in the files.")
if err:
    record("multi-step tool loop", "FAIL", f"step {steps}: {err}")
else:
    record("multi-step tool loop", "PASS" if "37" in (text or "") else "FAIL",
           f"{steps} model calls; answer: {(text or '').strip()[:120]!r}")

# 2. tool_choice forcing a specific function.
status, r = call({"model": MODEL, "max_tokens": 200, "temperature": 0, "reasoning_effort": "none",
                  "tools": TOOLS, "tool_choice": {"type": "function", "function": {"name": "read_file"}},
                  "messages": [{"role": "user", "content": "Hello there!"}]})
calls = (r["choices"][0]["message"].get("tool_calls") or []) if status == 200 else []
record("tool_choice named function", "PASS" if calls and calls[0]["function"]["name"] == "read_file" else "FAIL",
       f"HTTP {status}; {json.dumps(calls)[:160] if status == 200 else str(r)[:200]}")

# 3. tool_choice "required".
status, r = call({"model": MODEL, "max_tokens": 200, "temperature": 0, "reasoning_effort": "none",
                  "tools": TOOLS, "tool_choice": "required",
                  "messages": [{"role": "user", "content": "Hello there!"}]})
calls = (r["choices"][0]["message"].get("tool_calls") or []) if status == 200 else []
record("tool_choice required", "PASS" if calls else "FAIL",
       f"HTTP {status}; {json.dumps(calls)[:160] if status == 200 else str(r)[:200]}")

# 4. Several independent lookups in one turn (parallel tool calls).
status, r = call({"model": MODEL, "max_tokens": 300, "temperature": 0, "reasoning_effort": "none",
                  "tools": TOOLS, "parallel_tool_calls": True,
                  "messages": [{"role": "user", "content":
                                "Read config/app.toml and config/limits.toml. Call read_file for both now."}]})
calls = (r["choices"][0]["message"].get("tool_calls") or []) if status == 200 else []
record("parallel tool calls", "INFO", f"HTTP {status}; {len(calls)} call(s) in one turn: "
       + ", ".join(json.loads(c["function"]["arguments"]).get("path", "?") for c in calls))

# 5. Streamed tool call: arguments arrive as deltas and reassemble to valid JSON.
req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps({
    "model": MODEL, "max_tokens": 200, "temperature": 0, "reasoning_effort": "none", "stream": True,
    "tools": TOOLS, "messages": [{"role": "user", "content": "Read README.md using the tool."}]}).encode(),
    headers={"Content-Type": "application/json"})
name, argtext, chunks, finish = "", "", 0, None
with urllib.request.urlopen(req, timeout=900) as resp:
    for raw in resp:
        line = raw.decode().strip()
        if not line.startswith("data:") or line[5:].strip() == "[DONE]":
            continue
        ch = json.loads(line[5:]).get("choices") or []
        if not ch:
            continue
        finish = ch[0].get("finish_reason") or finish
        for tc in ch[0].get("delta", {}).get("tool_calls") or []:
            chunks += 1
            name += tc.get("function", {}).get("name") or ""
            argtext += tc.get("function", {}).get("arguments") or ""
try:
    ok = name == "read_file" and json.loads(argtext).get("path", "").strip("/") == "README.md"
except ValueError:
    ok = False
record("streamed tool call", "PASS" if ok else "FAIL",
       f"{chunks} tool-call delta(s), name={name!r}, arguments={argtext!r}, finish_reason={finish}")

# 6. History growing past the window: every tool result is padded (~1.5K
#    tokens), so the conversation outgrows a small pool within a few steps.
text, steps, err = agent("Read every file in this project one at a time, then summarize them.",
                         max_steps=12, pad=args.pad, max_tokens=512)
if err and isinstance(err, tuple):
    status, body, chars = err
    record("history outgrows the window", "INFO",
           f"stopped at model call {steps}: HTTP {status}, history {chars} chars; error: {json.dumps(body)[:300]}")
else:
    record("history outgrows the window", "INFO", f"finished in {steps} calls without hitting the limit: "
           f"{(text or str(err))[:120]!r}")

# 7. A single prompt larger than the window.
status, r = call({"model": MODEL, "max_tokens": 64, "temperature": 0, "reasoning_effort": "none",
                  "messages": [{"role": "user", "content": "lorem ipsum " * 12000 + "\nSay hi."}]})
record("prompt larger than the window", "INFO", f"HTTP {status}; {json.dumps(r)[:300]}")

# 8. A short prompt with a max_tokens budget larger than the window.
status, r = call({"model": MODEL, "max_tokens": 20000, "temperature": 0, "reasoning_effort": "none",
                  "messages": [{"role": "user", "content": "Say hi."}]})
record("max_tokens larger than the window", "INFO",
       f"HTTP {status}; {json.dumps(r)[:300] if status != 200 else 'accepted: ' + repr(r['choices'][0]['message'].get('content'))[:80]}")

# 9. No max_tokens at all (the deployment's default_max_tokens applies).
status, r = call({"model": MODEL, "temperature": 0, "reasoning_effort": "none",
                  "messages": [{"role": "user", "content": "Say hi."}]})
record("no max_tokens", "INFO",
       f"HTTP {status}; {json.dumps(r)[:300] if status != 200 else 'accepted, usage ' + json.dumps(r.get('usage'))}")

if args.json_out:
    open(args.json_out, "w").write(json.dumps(report, indent=1))
sys.exit(0 if all(x["status"] != "FAIL" for x in report) else 1)
