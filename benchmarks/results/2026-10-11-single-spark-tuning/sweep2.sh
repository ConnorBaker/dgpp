#!/usr/bin/env bash
# Sweep 2: realistic ingest requests (thinking off, enforced JSON schema,
# 1200-token cap) across the sweep 1 candidates and the prefill/KV knobs.
set -u
D=$(cd "$(dirname "$0")" && pwd)
if [ -z "${DGPP_BIN:-}" ]; then
  echo 'set DGPP_BIN to the dgpp-serve binary, e.g. "$(nix build --print-out-paths .#dgpp)/bin/dgpp-serve"' >&2
  exit 1
fi
export BENCH_OUT=$D/raw/sweep2 BENCH_INGEST_CONC=16 BENCH_INGEST_DOCS=32
export BENCH_SKIP_TIMED_LOAD=1 BENCH_INGEST_REASONING=none BENCH_INGEST_SCHEMA=1 BENCH_INGEST_MAX_TOKENS=1200
Q="--queue-limit 64"
run() { echo "=== $1 $(date +%T)"; python3 $D/bench.py "$@"; echo "=== $1 exit=$? $(date +%T)"; }
run mc4-mtp1              $Q
run mc8-mtp1              $Q --max-concurrency 8
run mc8-mtp3              $Q --max-concurrency 8 --mtp-depth 3
run mc16-mtp1             $Q --max-concurrency 16
run mc8-mtp1-prefill16k   $Q --max-concurrency 8 --prefill-budget-tokens 8192 --prefill-idle-budget-tokens 16384
run mc8-mtp1-early        $Q --max-concurrency 8 --prefill-early-admission
run mc8-mtp1-fp8kv        $Q --max-concurrency 8 --kv-dtype fp8
echo SWEEP_DONE
