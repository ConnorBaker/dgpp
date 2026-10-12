# Single-Spark tuning: Qwen3.8-Flash-Next NVFP4 for an agent swarm

Date: 2026-10-11. One DGX Spark (GB10, driver 615.71.09, kernel
7.0.0-1019-nvidia), `nvidia/Qwen3.8-Flash-Next-NVFP4` (revision
`fc694b54`), the `cluster_qwen-3.8-flash-next_nvfp4_w1` template as the
starting point. `dgpp-serve` built with the repository flake (Nixpkgs
master, CUDA 13.4.2, `cudaCapabilities = [ "12.1a" ]`) and run through the
host's `nixglhost`.

The target workload is an agent swarm whose main job is reading documents
(bookmarked web pages) into a knowledge base: long prompts, short
structured answers, many requests in flight.

## Method

Each variant starts the deployment with extra `dgpp-serve` flags
(`dgpp-cluster up --knobs ...`), measures it, and stops it
([bench.py](bench.py)).

- **Decode**: [scripts/timed_load.py](../../../scripts/timed_load.py), the
  serving table's method: five prompt classes, 256 greedy tokens, thinking
  off, concurrency 1/4/8/16, one repeat. Cells are wall tok/s, min–max
  across the classes.
- **Ingest**: 32 distinct documents of about 10K tokens (cut from the
  repository's `docs/*.md`), 16 requests in flight, each asking for a JSON
  summary (title, summary, key points, entities, tags). Reported as
  documents per hour over the whole batch.
  - Sweep 1 used `reasoning_effort: "low"` and a 600-token cap. Every answer
    hit the cap and none was valid JSON, so sweep 1's ingest column compares
    engine throughput only.
  - Sweeps 2–4 use the realistic request: thinking off
    (`reasoning_effort: "none"`), the schema enforced with `response_format`
    (bounded lists), a 1200-token cap. Every answer is valid JSON and
    finishes with `stop`.

Every variant passes `--queue-limit 64`: the template's limit of 8 would
reject, not queue, part of a 16-wide client.

## Results

### Sweep 1: slots and MTP depth ([sweep1.sh](sweep1.sh))

| variant | C1 wall tok/s | C4 wall tok/s | C8 wall tok/s | C16 wall tok/s | ingest docs/h | ingest out tok/s | ingest wall s |
|---|---|---|---|---|---|---|---|
| base-mc4-mtp1 (template) | 45.1–51.1 | 93.7–107.9 | 92.8–106.2 | 91.6–105.1 | 284 | 47.3 | 406.0 |
| mc8-mtp1 | 44.4–51.3 | 93.0–108.6 | 126.4–152.4 | 132.9–149.2 | 283 | 47.2 | 406.4 |
| mc8-mtp3 | 26.5–65.8 | 54.7–145.0 | 63.5–160.5 | 103.4–154.0 | 296 | 49.4 | 388.9 |
| mc8-nomtp | 33.6–34.3 | 82.5–85.3 | 110.5–112.3 | 108.7–111.2 | 267 | 44.5 | 431.0 |
| mc16-mtp1 | 39.6–51.4 | 82.5–106.2 | 134.4–149.5 | 148.7–181.4 | 246 | 41.0 | 467.9 |
| mc16-mtp3 | 35.4–65.6 | 80.9–147.5 | 104.2–151.8 | 124.9–212.4 | 253 | 42.2 | 455.2 |
| mc8-mtp1-prefill8k | 42.6–50.4 | 86.4–106.9 | 133.4–147.7 | 128.6–152.2 | 320 | 53.3 | 360.2 |

Four slots cap concurrent decode at about 100 tok/s. MTP depth 1 beats
plain decode everywhere; depth 3 raises the best class and badly lowers the
worst (prose). Sixteen slots win at C16 but lose ingest, as more long
prompts prefill at once.

### Sweeps 2–4: realistic ingest ([sweep2.sh](sweep2.sh), [sweep3.sh](sweep3.sh), [sweep4.sh](sweep4.sh))

| variant | docs/h (each run) |
|---|---|
| 4 slots, MTP 1 (template) | 307 |
| 8 slots, MTP 1 | 320, 313, 316, 315, 320 |
| 8 slots, MTP 3 | 322, 311 |
| 16 slots, MTP 1 | 275 |
| 8 slots, `--prefill-early-admission` (#107) | 321 |
| 8 slots, `--kv-dtype fp8` (#101) | 247 |
| 8 slots, prefill budget 2048 busy / 4096 idle | 279 |
| 8 slots, prefill budget 8192 / 8192 | 291, 340, 344, 341, 345 |
| 8 slots, prefill budget 8192 idle 16384 | refused at startup: 110.06 GiB + 4 GiB headroom against 110.09 GiB free |

Per-run detail (wall time, token counts, per-request range) is in
`raw/sweep*/<variant>.ingest.json`; the answers themselves in
`<variant>.ingest_answers.json`.

The 8K budget's first run (291) is an outlier against its other four; the
4K budget stays at 313–320 over five runs.

### PR #97: parallel eager layer load ([pr97_pr53.sh](pr97_pr53.sh))

Time to READY with the tuned deployment:

| boot | `DGPP_PRELOAD_THREADS` unset | `DGPP_PRELOAD_THREADS=4` |
|---|---|---|
| warm (resident image present) | 24 s | 28 s |
| cold (`DGPP_RESIDENT_CACHE=off`, page cache dropped) | 142 s | 66 s |

### PR #53: NVMe cold tier for the prefix cache ([pr97_pr53.sh](pr97_pr53.sh))

The PR's check ([scripts/serve_nvme_cache_check.py](../../../scripts/serve_nvme_cache_check.py)),
31.5K-token documents, a 32 GiB slab, a 0.7 GiB arena (six 110.3 MiB slots):

| step | TTFT | cached tokens |
|---|---|---|
| document A, cold | 17115 ms | 0 |
| document A, new question (memory hit) | 4052 ms | 24576 |
| three other documents (evict A to disk) | 17551–18124 ms | 0 |
| document A, restored from disk | 1271 ms | 31488 |
| document A, memory hit after the restore | 390 ms | 31488 |

Transcripts after the disk restore are identical to the in-memory hit's; no
spill or restore failures. The PR's docstring example arena (0.15 GiB, the
GLM recipe) holds one Qwen slot and never keeps an entry long enough to hit;
that run is kept as `raw/pr97_pr53/nvme-check.*`.

Ingest with the tier on (64 GiB slab) against the same build without it:
347 vs 342 docs/h.

[prefix_probe.py](prefix_probe.py) / [prefix_matrix.sh](prefix_matrix.sh)
confirm the prefix cache attaches identically on the merged build (tuned and
template configurations) and on the build before #97 and #53: 24576 cached
tokens, TTFT 17.8 s → 4.1 s.

## Decisions

The deployment now runs:

- `max_concurrency: 8`, `mtp_depth: 1` (unchanged), `kv_dtype: bf16`
  (unchanged);
- `prefill_budget_tokens: 8192`, `prefill_idle_budget_tokens: 8192`;
- `queue_limit: 64`;
- `nvme_cache: {enabled: true, path: ~/dgpp/nvme-cache, capacity_gib: 64}`.

Not adopted:

- FP8 KV (#101): −23 % ingest, and the BF16 pool already holds the working
  set.
- Early admission (#107): neutral for this batch workload.
- `DGPP_PRELOAD_THREADS` (#97): slower on the normal (warm) boot; set it
  only when the resident image must be rebuilt.
- Sixteen slots: better for chat-heavy C16 decode, worse for ingest.

Clients should send `reasoning_effort: "none"` and a `response_format`
schema for extraction.

## Reproduce

From the repository root:

```bash
export DGPP_BIN="$(nix build --print-out-paths .#dgpp)/bin/dgpp-serve"
nix develop -c bash benchmarks/results/2026-10-11-single-spark-tuning/sweep1.sh
nix develop -c python3 benchmarks/results/2026-10-11-single-spark-tuning/summarize.py \
  benchmarks/results/2026-10-11-single-spark-tuning/raw/sweep1
```

`sweep2.sh`–`sweep4.sh` and `pr97_pr53.sh` run the same way;
`summarize_ingest.py raw/<sweep>` tabulates ingest-only sweeps.
`pr97_pr53.sh` drops the page cache with `sudo` before cold boots.
[smoke.py](smoke.py) is the functional check (models, chat, streaming, a
tool-call round trip, C1/C8 throughput) used after each deployment change.
