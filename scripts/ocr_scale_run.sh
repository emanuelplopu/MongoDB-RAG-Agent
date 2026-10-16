#!/usr/bin/env bash
# Run scripts/ocr_scan_check.py over one sample set in N parallel throwaway containers.
#
#   scripts/ocr_scale_run.sh <label> <set> <shards> <cpus-per-shard> [extra harness args...]
#
# Environment:
#   BENCH   bench folder holding sample/<set> (default C:/RecallHub-Verify/bench/ocr-scale-2026-10-16)
#   REPO    checkout whose src/ and scripts/ are mounted into the image (default: this script's repo)
#   IMAGE   worker image (default recallhub-ingestion-worker:ocrscale)
#   OCR_*   any OCR_* variable is passed into the containers (OCR_SCAN_DPI, OCR_IMAGE_MAX_MPX, ...)
#
# Outputs land in $BENCH/runs/<label>/<set>/ (one folder per file + summary_K_of_N.json).
# Containers are named ocr-<label>-<set>-<k>; `docker logs -f` follows one, `docker ps` lists them.
# Nothing here touches the Compose stack: plain `docker run`, side-tagged image, bind mounts only.
set -euo pipefail
export MSYS_NO_PATHCONV=1

label="${1:?label}"; set_name="${2:?set}"; shards="${3:-1}"; cpus="${4:-3}"; shift 4 || true
BENCH="${BENCH:-C:/RecallHub-Verify/bench/ocr-scale-2026-10-16}"
REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd -W 2>/dev/null || cd "$(dirname "$0")/.." && pwd)}"
IMAGE="${IMAGE:-recallhub-ingestion-worker:ocrscale}"

in_dir="$BENCH/sample/$set_name"
out_dir="$BENCH/runs/$label/$set_name"
mkdir -p "$out_dir"

env_args=()
for var in $(env | grep -E '^(OCR|INGEST)_[A-Z_]+=' | cut -d= -f1); do
  env_args+=(-e "$var=${!var}")
done
# Alternative traineddata (e.g. tessdata_best) can be mounted for OCR_TESSDATA_PATH=/data/tessdata
tessdata_mount=()
if [ -d "$BENCH/tessdata_best" ]; then
  tessdata_mount=(-v "$BENCH/tessdata_best:/data/tessdata:ro")
fi

for ((k = 0; k < shards; k++)); do
  name="ocr-$label-$set_name-$k"
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d --name "$name" --cpus "$cpus" \
    -e OMP_NUM_THREADS="$cpus" -e PYTHONUNBUFFERED=1 "${env_args[@]}" \
    -v "$in_dir:/data/in:ro" -v "$out_dir:/data/out" "${tessdata_mount[@]}" \
    -v "$REPO/scripts/ocr_scan_check.py:/app/ocr_scan_check.py:ro" \
    -v "$REPO/src:/app/src:ro" \
    --entrypoint python "$IMAGE" /app/ocr_scan_check.py /data/in /data/out --shard "$k/$shards" "$@" >/dev/null
  echo "started $name ($in_dir -> $out_dir)"
done
