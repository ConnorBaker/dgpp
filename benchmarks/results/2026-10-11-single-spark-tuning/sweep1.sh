#!/usr/bin/env bash
# Sweep 1: slot count and MTP depth on the merged build. Every variant gets
# --queue-limit 64 so a 16-wide client queues instead of being rejected.
set -u
D=$(cd "$(dirname "$0")" && pwd)
if [ -z "${DGPP_BIN:-}" ]; then
  echo 'set DGPP_BIN to the dgpp-serve binary, e.g. "$(nix build --print-out-paths .#dgpp)/bin/dgpp-serve"' >&2
  exit 1
fi
export BENCH_OUT=$D/raw/sweep1 BENCH_INGEST_CONC=16 BENCH_INGEST_DOCS=32
Q="--queue-limit 64"
run() { echo "=== $1 $(date +%T)"; python3 $D/bench.py "$@"; echo "=== $1 exit=$? $(date +%T)"; }
run base-mc4-mtp1        $Q
run mc8-mtp1             $Q --max-concurrency 8
run mc8-mtp3             $Q --max-concurrency 8 --mtp-depth 3
run mc8-nomtp            $Q --max-concurrency 8 --no-mtp
run mc16-mtp1            $Q --max-concurrency 16
run mc16-mtp3            $Q --max-concurrency 16 --mtp-depth 3
echo SWEEP_DONE
