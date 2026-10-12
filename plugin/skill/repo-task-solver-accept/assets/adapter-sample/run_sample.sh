#!/usr/bin/env bash
set -euo pipefail
trap 'exit 2' ERR
if [ "$#" -lt 2 ]; then echo "Usage: bash run_sample.sh DUT_REPO NEW_OUTPUT [test options]" >&2; exit 2; fi
sample_root=$(cd -- "$(dirname -- "$0")" && pwd)
repo=$(cd -- "$1" && pwd)
out=$2
shift 2
mkdir -- "$out"
out=$(cd -- "$out" && pwd)
cmake -S "$sample_root/test/cmatinv_batched" -B "$out/build" -DOPS_SOLVER_REPO="$repo"
cmake --build "$out/build"
export LD_LIBRARY_PATH="$repo/build:${LD_LIBRARY_PATH:-}"
trap - ERR
"${PYTHON:-python3}" "$sample_root/test/cmatinv_batched/run_tests.py" --executor "$out/build/cmatinv_test" --out "$out/results" "$@"
