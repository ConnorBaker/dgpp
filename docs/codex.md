# Codex with a DGPP model

Codex speaks only the OpenAI Responses API (`wire_api = "responses"`;
`"chat"` is rejected). DGPP serves Chat Completions, so a LiteLLM proxy
translates between them. Checked with Codex 0.162.1, LiteLLM 1.102.1 from
Nixpkgs (in the binary cache) and Qwen3.8-Flash-Next NVFP4 on one Spark.

## Run

The dev shell (`nix develop`) provides both `codex` and `litellm` from
Nixpkgs.

1. Serve the model (here on `127.0.0.1:18080`).
2. Start the proxy:

   ```bash
   litellm --config deploy/litellm-codex.example.yaml --host 127.0.0.1 --port 4000
   ```

3. Copy [deploy/codex-dgpp.example.toml](../deploy/codex-dgpp.example.toml)
   to `~/.codex/dgpp.config.toml` and run `codex --profile dgpp` (or
   `codex exec --profile dgpp "..."`). The profile layers over the base
   config.

## Why each setting

| Setting | Reason |
| --- | --- |
| `openai/chat_completions/` model prefix (proxy) | With plain `openai/`, LiteLLM forwards `/v1/responses` unchanged and DGPP answers 404. |
| `web_search = "disabled"` | Codex sends its hosted search tool by default; LiteLLM turns it into `web_search_options`, which DGPP refuses, and a local model cannot run it. |
| `features.apps = false` | When signed in to ChatGPT, Codex adds its apps/connectors as MCP tools (about 130), past the 128-tool limit. |
| `model_reasoning_summary = "none"` | With a summary requested, LiteLLM forwards the whole `reasoning` object as `reasoning_effort`, which must be a string. |
| `sandbox_mode = "danger-full-access"`, `approval_policy = "never"` | Commands run unsandboxed and unprompted. On Ubuntu with `kernel.apparmor_restrict_unprivileged_userns = 1` and no AppArmor profile for `bwrap`, Codex's sandbox cannot start (`bwrap: loopback: Failed RTM_NEWADDR`). |

DGPP itself handles the rest of Codex's request shape: several leading
developer messages and one mid-conversation are folded for templates that
accept only one leading system message, and `prompt_cache_key` is accepted
(see [OpenAI compatibility](openai-compatibility.md)).

## Limits

- Codex has no catalog entry for the model and uses fallback metadata
  (warning at start): no `apply_patch` tool, so edits go through shell
  commands.
- Code mode (`features.code_mode`) works but less reliably: LiteLLM sends
  its `exec` tool as a function, and the model writes the JavaScript. On a
  small find-and-write task, 3 of 5 runs passed with code mode on (one
  ended the turn early, one never closed its reasoning) and 2 of 2 with it
  off (2026-10-12).
- Multi-agent threads each send their own requests; they queue behind the
  deployment's `max_concurrency`.
- Every turn resends the whole history; the prefix cache serves most of it
  (see [prefix cache](prefix-cache.md)).
