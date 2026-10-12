#!/usr/bin/env bash
# Sweep 4: alternate the template's 4K prefill budget with 8K, three times
# each, to separate the effect from run-to-run noise.
set -u
D=$(cd "$(dirname "$0")" && pwd)
if [ -z "${DGPP_BIN:-}" ]; then
  echo 'set DGPP_BIN to the dgpp-serve binary, e.g. "$(nix build --print-out-paths .#dgpp)/bin/dgpp-serve"' >&2
  exit 1
fi
export BENCH_OUT=$D/raw/sweep4 BENCH_INGEST_CONC=16 BENCH_INGEST_DOCS=32
export BENCH_SKIP_TIMED_LOAD=1 BENCH_INGEST_REASONING=none BENCH_INGEST_SCHEMA=1 BENCH_INGEST_MAX_TOKENS=1200
Q="--queue-limit 64 --max-concurrency 8"
run() { echo "=== $1 $(date +%T)"; python3 $D/bench.py "$@"; echo "=== $1 exit=$? $(date +%T)"; }
for r in 3 4 5; do
  run mc8-mtp1-r$r           $Q
  run mc8-mtp1-prefill8k-r$r $Q --prefill-budget-tokens 8192 --prefill-idle-budget-tokens 8192
done
echo SWEEP_DONE
