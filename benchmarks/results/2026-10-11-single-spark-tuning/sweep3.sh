#!/usr/bin/env bash
# Sweep 3: 8K prefill budgets, and repeats of the sweep 2 leaders for noise.
set -u
D=$(cd "$(dirname "$0")" && pwd)
if [ -z "${DGPP_BIN:-}" ]; then
  echo 'set DGPP_BIN to the dgpp-serve binary, e.g. "$(nix build --print-out-paths .#dgpp)/bin/dgpp-serve"' >&2
  exit 1
fi
export BENCH_OUT=$D/raw/sweep3 BENCH_INGEST_CONC=16 BENCH_INGEST_DOCS=32
export BENCH_SKIP_TIMED_LOAD=1 BENCH_INGEST_REASONING=none BENCH_INGEST_SCHEMA=1 BENCH_INGEST_MAX_TOKENS=1200
Q="--queue-limit 64"
run() { echo "=== $1 $(date +%T)"; python3 $D/bench.py "$@"; echo "=== $1 exit=$? $(date +%T)"; }
run mc8-mtp1-prefill8k    $Q --max-concurrency 8 --prefill-budget-tokens 8192 --prefill-idle-budget-tokens 8192
run mc8-mtp1-busy2k       $Q --max-concurrency 8 --prefill-budget-tokens 2048 --prefill-idle-budget-tokens 4096
run mc8-mtp1-r2           $Q --max-concurrency 8
run mc8-mtp3-r2           $Q --max-concurrency 8 --mtp-depth 3
run mc8-mtp1-prefill8k-r2 $Q --max-concurrency 8 --prefill-budget-tokens 8192 --prefill-idle-budget-tokens 8192
echo SWEEP_DONE
