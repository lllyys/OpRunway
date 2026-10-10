#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 4 ]; then echo "Usage: bash run_sample.sh DUT_REPO SOC DEVICE NEW_OUTPUT" >&2; exit 2; fi
: "${SOLVER_ACCEPT_DIR:?Set SOLVER_ACCEPT_DIR}"
: "${SOLVER_CASE_GEN_DIR:?Set SOLVER_CASE_GEN_DIR}"
accept_root=$(cd -- "$SOLVER_ACCEPT_DIR" && pwd)
gen_root=$(cd -- "$SOLVER_CASE_GEN_DIR" && pwd)
sample_root=$(cd -- "$(dirname -- "$0")" && pwd)
repo=$(cd -- "$1" && pwd)
python_bin=${PYTHON:-python3}
mkdir -- "$4"
out=$(cd -- "$4" && pwd)
"$python_bin" "$accept_root/scripts/harness/build_dut.py" --repo "$repo" --ops cmatinv_batched --soc "$2" --device "$3" --out "$out/build.json"
"$python_bin" "$accept_root/scripts/harness/run_harness.py" --repo "$repo" --adapter "$sample_root/test/cmatinv_batched/cmatinv_batched_adapter.cpp" --gen-dir "$gen_root/scripts" --probe-op cmatinv_batched --n 8 --batch 4 --seed 923000001 --rerun 5 --device "$3" --provenance "$out/build.json" --work-dir "$out/work" --evidence-dir "$out/evidence" --report "$out/report.json" --keep probe-cmatinv_batched-n8-b4-s923000001
