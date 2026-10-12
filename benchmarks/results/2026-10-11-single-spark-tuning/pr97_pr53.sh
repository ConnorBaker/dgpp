#!/usr/bin/env bash
# PR #97 (parallel eager layer load) and PR #53 (NVMe cold tier for the
# prefix cache) on the tuned single-Spark deployment. Run from the repo root
# inside `nix develop`, with DGPP_BIN set to the merged build's dgpp-serve.
#
# #97: time to READY with DGPP_PRELOAD_THREADS unset vs 4, warm (resident
#      image present) and cold (DGPP_RESIDENT_CACHE=off; page cache dropped).
# #53: the PR's own check (evict a long document from a small arena, restore
#      it from disk, compare TTFT and transcripts), then the ingest benchmark
#      with the tier on beside the merged build's baseline.
set -u
D=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$D/../../.." && pwd)
if [ -z "${DGPP_BIN:-}" ]; then
  echo 'set DGPP_BIN to the dgpp-serve binary, e.g. "$(nix build --print-out-paths .#dgpp)/bin/dgpp-serve"' >&2
  exit 1
fi
OUT=$D/raw/pr97_pr53
mkdir -p "$OUT"
CONFIG=$REPO/deploy/cluster_qwen-3.8-flash-next_nvfp4_w1.json
NVME_DIR=$HOME/dgpp/nvme-cache

up() {  # name, extra env..., -- knobs...
  local name=$1; shift
  local envs=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do envs+=("$1"); shift; done
  [ $# -gt 0 ] && shift
  python3 "$REPO/scripts/dgpp-cluster" down --config "$CONFIG" >> "$OUT/$name.log" 2>&1
  env "${envs[@]}" python3 "$REPO/scripts/dgpp-cluster" up --bin "$DGPP_BIN" --head-wrap nixglhost \
    --config "$CONFIG" --knobs "$*" >> "$OUT/$name.log" 2>&1
  local rc=$?
  echo "$name: up exit=$rc; $(grep -o 'rank 0 serving: ok ([0-9]*s)' "$OUT/$name.log" | tail -1)"
  return $rc
}
down() { python3 "$REPO/scripts/dgpp-cluster" down --config "$CONFIG" >> "$OUT/$1.log" 2>&1; }
drop_caches() { sync; echo 3 | sudo -n tee /proc/sys/vm/drop_caches > /dev/null; }

echo "=== #97 boot times $(date +%T)"
up boot-first -- ; down boot-first                       # captures the image if this build needs one
up boot-warm-serial -- ; down boot-warm-serial
up boot-warm-par4 DGPP_PRELOAD_THREADS=4 -- ; down boot-warm-par4
drop_caches; up boot-cold-serial DGPP_RESIDENT_CACHE=off -- ; down boot-cold-serial
drop_caches; up boot-cold-par4 DGPP_RESIDENT_CACHE=off DGPP_PRELOAD_THREADS=4 -- ; down boot-cold-par4

echo "=== #53 NVMe tier check $(date +%T)"
mkdir -p "$NVME_DIR"
if up nvme-check -- --nvme-cache-path "$NVME_DIR" --nvme-cache-gib 32 --prefix-cache-gib 0.15; then
  python3 "$REPO/scripts/serve_nvme_cache_check.py" --host 127.0.0.1 --port 18080 \
    --out "$OUT/nvme-check.json" > "$OUT/nvme-check.txt" 2>&1
  echo "nvme-check: exit=$?"
fi
down nvme-check

echo "=== #53 ingest with and without the tier $(date +%T)"
export BENCH_OUT=$OUT BENCH_INGEST_CONC=16 BENCH_INGEST_DOCS=32
export BENCH_SKIP_TIMED_LOAD=1 BENCH_INGEST_REASONING=none BENCH_INGEST_SCHEMA=1 BENCH_INGEST_MAX_TOKENS=1200
python3 "$D/bench.py" ingest-merged-baseline
python3 "$D/bench.py" ingest-nvme-tier --nvme-cache-path "$NVME_DIR" --nvme-cache-gib 64
echo DONE
