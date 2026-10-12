#!/usr/bin/env bash
# Isolate the prefix-cache misses seen in the NVMe tier check: the probe on
# the merged build with the tuned config, the merged build with the template
# config, and the pre-#97/#53 build with the tuned config. Needs DGPP_BIN
# (merged) and DGPP_BIN_PREMERGE; run from the repo root in `nix develop`.
set -u
D=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$D/../../.." && pwd)
for v in DGPP_BIN DGPP_BIN_PREMERGE; do
  if [ -z "${!v:-}" ]; then echo "set $v" >&2; exit 1; fi
done
OUT=$D/raw/prefix_matrix
mkdir -p "$OUT"
CONFIG=$REPO/deploy/cluster_qwen-3.8-flash-next_nvfp4_w1.json
run() {  # name, binary, knobs...
  local name=$1 bin=$2; shift 2
  python3 "$REPO/scripts/dgpp-cluster" down --config "$CONFIG" >> "$OUT/$name.log" 2>&1
  if python3 "$REPO/scripts/dgpp-cluster" up --bin "$bin" --head-wrap nixglhost --config "$CONFIG" \
       --knobs "$*" >> "$OUT/$name.log" 2>&1; then
    python3 "$D/prefix_probe.py" > "$OUT/$name.json" 2>&1
    echo "$name: probe exit=$? $(tr -d '\n ' < "$OUT/$name.json" | cut -c1-220)"
  else
    echo "$name: up failed"
  fi
  python3 "$REPO/scripts/dgpp-cluster" down --config "$CONFIG" >> "$OUT/$name.log" 2>&1
}
run merged-tuned "$DGPP_BIN"
run merged-template "$DGPP_BIN" --max-concurrency 4 --prefill-budget-tokens 4096 --prefill-idle-budget-tokens 4096
run premerge-tuned "$DGPP_BIN_PREMERGE"
echo DONE
