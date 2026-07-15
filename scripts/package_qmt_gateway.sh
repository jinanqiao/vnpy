#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

STAMP="${1:-$(date +%Y%m%d_%H%M)}"
OUT_DIR="outputs/qmt_gateway"
OUT="${OUT_DIR}/qmt_gateway_${STAMP}.zip"

mkdir -p "$OUT_DIR"
rm -f "$OUT"

(
  cd infra
  zip -r "../$OUT" qmt_gateway \
    -x "qmt_gateway/config.json" \
    -x "qmt_gateway/.venv/*" \
    -x "qmt_gateway/__pycache__/*" \
    -x "qmt_gateway/**/*.pyc"
)

echo "$OUT"
